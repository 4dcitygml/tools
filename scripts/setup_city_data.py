#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Fetch urban data from the G-Spatial Information Center PLATEAU distribution ZIP and create the initial repository structure.

Instead of downloading the entire distribution ZIP (several GB), use HTTP Range requests to retrieve only necessary entries.
The distribution source must support Range requests (G-Spatial S3/CMS already support this).
After retrieval, GML files of 50MiB or larger are automatically split into 4th-level meshes using split_4th_mesh.py,
and organized into a state suitable for Git management (all files under 50MiB).

Usage:
  # Check the list and sizes of recorded meshes
  python3 scripts/setup_city_data.py <ZIP_URL> --list

  # Retrieve only specified meshes and create initial structure (default: all bldg meshes)
  python3 scripts/setup_city_data.py <ZIP_URL> --outdir . --mesh 53394611

Dependencies: retrieval uses only the standard library. lxml is required only if 4th-level splitting occurs.
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import urllib.parse
import urllib.request
import zlib

EOCD_SIG = b"PK\x05\x06"
Z64_LOC_SIG = b"PK\x06\x07"
Z64_EOCD_SIG = b"PK\x06\x06"
CEN_SIG = b"PK\x01\x02"
MIB = 1024 * 1024


def fetch(url, start, end):
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def total_size(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.headers.get("Accept-Ranges", "").lower() != "bytes" and not r.headers.get(
            "x-goog-stored-content-length"
        ):
            print("Warning: Distribution source does not explicitly support Range requests. Full download may be required if this fails.")
        return int(r.headers["Content-Length"])


def central_directory(url, size):
    tail_len = min(size, 66000)
    tail = fetch(url, size - tail_len, size - 1)
    i = tail.rfind(EOCD_SIG)
    if i < 0:
        raise SystemExit("ZIP EOCD not found (Range requests not supported or file corrupted)")
    cd_size, cd_off = struct.unpack("<II", tail[i + 12 : i + 20])
    if cd_size == 0xFFFFFFFF or cd_off == 0xFFFFFFFF:
        j = tail.rfind(Z64_LOC_SIG, 0, i)
        if j < 0:
            raise SystemExit("zip64 locator not found")
        z64_off = struct.unpack("<Q", tail[j + 8 : j + 16])[0]
        z64 = fetch(url, z64_off, z64_off + 55)
        if z64[:4] != Z64_EOCD_SIG:
            raise SystemExit("zip64 EOCD not found")
        cd_size, cd_off = struct.unpack("<QQ", z64[40:56])
    return fetch(url, cd_off, cd_off + cd_size - 1)


def parse_entries(cd):
    entries = []
    p = 0
    while p + 4 <= len(cd) and cd[p : p + 4] == CEN_SIG:
        method = struct.unpack("<H", cd[p + 10 : p + 12])[0]
        csize = struct.unpack("<I", cd[p + 20 : p + 24])[0]
        usize = struct.unpack("<I", cd[p + 24 : p + 28])[0]
        name_len, extra_len, comment_len = struct.unpack("<HHH", cd[p + 28 : p + 34])
        offset = struct.unpack("<I", cd[p + 42 : p + 46])[0]
        name = cd[p + 46 : p + 46 + name_len].decode("utf-8", "replace")
        extra = cd[p + 46 + name_len : p + 46 + name_len + extra_len]
        q = 0
        while q + 4 <= len(extra):  # zip64 extra: only fields set to 0xFFFFFFFF appear, in order
            eid, elen = struct.unpack("<HH", extra[q : q + 4])
            if eid == 0x0001:
                body = extra[q + 4 : q + 4 + elen]
                r = 0
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack("<Q", body[r : r + 8])[0]
                    r += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack("<Q", body[r : r + 8])[0]
                    r += 8
                if offset == 0xFFFFFFFF:
                    offset = struct.unpack("<Q", body[r : r + 8])[0]
                    r += 8
            q += 4 + elen
        if not name.endswith("/"):
            entries.append(
                {"name": name, "method": method, "csize": csize, "usize": usize, "offset": offset}
            )
        p += 46 + name_len + extra_len + comment_len
    return entries


def dataset_prefix(url, entries):
    """Return the root folder name common to all entries. For distribution ZIPs without one, derive it from the URL filename."""
    roots = {e["name"].split("/", 1)[0] for e in entries if "/" in e["name"]}
    if len(roots) == 1:
        return ""  # the ZIP itself has a root folder
    base = os.path.basename(urllib.parse.urlparse(url).path)
    return re.sub(r"\.zip$", "", base) + "/"


def extract(url, chosen, outdir, prefix=""):
    """Extract the selected entries to outdir, merging Range requests for nearby entries."""
    chosen = sorted(chosen, key=lambda e: e["offset"])
    GAP, MAXCHUNK = 64 * 1024, 64 * MIB
    groups, cur = [], []
    for e in chosen:
        e["_end"] = e["offset"] + 30 + len(e["name"]) + 200 + e["csize"]
        if cur and (
            e["offset"] - cur[-1]["_end"] > GAP or e["_end"] - cur[0]["offset"] > MAXCHUNK
        ):
            groups.append(cur)
            cur = []
        cur.append(e)
    if cur:
        groups.append(cur)

    done = 0
    for g in groups:
        start = g[0]["offset"]
        blob = fetch(url, start, g[-1]["_end"] - 1)
        for e in g:
            base = e["offset"] - start
            if blob[base : base + 4] != b"PK\x03\x04":
                raise SystemExit(f"Local header mismatch: {e['name']}")
            nlen, xlen = struct.unpack("<HH", blob[base + 26 : base + 30])
            dstart = base + 30 + nlen + xlen
            raw = blob[dstart : dstart + e["csize"]]
            if len(raw) < e["csize"]:
                raw += fetch(url, start + dstart + len(raw), start + dstart + e["csize"] - 1)
            data = zlib.decompress(raw, -15) if e["method"] == 8 else raw
            if len(data) != e["usize"]:
                raise SystemExit(f"Decompressed size mismatch: {e['name']}")
            dest = os.path.join(outdir, prefix + e["name"])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            done += 1
            if done % 200 == 0 or done == len(chosen):
                print(f"  Retrieved {done}/{len(chosen)}", flush=True)


def bldg_mesh_table(entries):
    """Group GML files under udx/bldg by mesh code."""
    table = {}
    for e in entries:
        m = re.search(r"udx/bldg/(\d{8,9})_bldg_[^/]*\.gml$", e["name"])
        if m:
            table.setdefault(m.group(1), []).append(e)
    return table


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="URL of the G-Spatial Information Center CityGML distribution ZIP")
    ap.add_argument("--outdir", default=".", help="Repository root (default: current directory)")
    ap.add_argument("--mesh", action="append", default=[], help="3rd-level mesh codes to retrieve (multiple allowed; default: all meshes)")
    ap.add_argument("--list", action="store_true", help="Display list and sizes of bldg meshes and exit")
    ap.add_argument("--no-appearance", action="store_true", help="Do not retrieve textures (appearance)")
    ap.add_argument("--keep-original", action="store_true", help="Keep original 3rd-level mesh GML after 4th-level splitting")
    ap.add_argument("--max-mib", type=int, default=50, help="Split GML files of this size or larger into 4th-level (default: 50)")
    args = ap.parse_args()

    size = total_size(args.url)
    print(f"Distribution ZIP: {size / 1e9:.2f} GB — retrieving central directory only")
    entries = parse_entries(central_directory(args.url, size))
    meshes = bldg_mesh_table(entries)

    if args.list:
        print(f"Number of bldg meshes: {len(meshes)}")
        for code in sorted(meshes):
            gml = sum(e["usize"] for e in meshes[code])
            app = sum(
                e["usize"] for e in entries if f"udx/bldg/{code}_bldg" in e["name"] and "appearance" in e["name"]
            )
            mark = " ★Requires 4th-level split" if gml >= args.max_mib * MIB else ""
            print(f"  {code}  GML {gml / MIB:6.1f} MiB / Textures {app / MIB:6.1f} MiB{mark}")
        return

    codes = args.mesh or sorted(meshes)
    unknown = [c for c in codes if c not in meshes]
    if unknown:
        raise SystemExit(f"Mesh(es) not found in ZIP: {unknown} (check with --list)")

    chosen = [e for e in entries if re.search(r"(^|/)(codelists|metadata|specification)/", e["name"])]
    for c in codes:
        for e in entries:
            if f"udx/bldg/{c}_bldg" in e["name"]:
                if args.no_appearance and "appearance" in e["name"]:
                    continue
                chosen.append(e)
    total = sum(e["usize"] for e in chosen)
    print(f"Retrieval target: {len(chosen)} files / {total / MIB:.1f} MiB (meshes: {', '.join(codes)})")
    prefix = dataset_prefix(args.url, entries)
    if prefix:
        print(f"Distribution ZIP has no root folder, adding {prefix} prefix")
    extract(args.url, chosen, args.outdir, prefix)

    # Split GML files of 50MiB or more into 4th-level meshes (to fit Git management)
    split_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "split_4th_mesh.py")
    for root, _dirs, files in os.walk(args.outdir):
        if not root.replace(os.sep, "/").endswith("udx/bldg"):
            continue
        for f in sorted(files):
            path = os.path.join(root, f)
            if re.fullmatch(r"\d{8}_bldg_[^/]*\.gml", f) and os.path.getsize(path) >= args.max_mib * MIB:
                print(f"4th-level split: {f} ({os.path.getsize(path) / MIB:.1f} MiB)")
                subprocess.run([sys.executable, split_script, path, "--outdir", root], check=True)
                if not args.keep_original:
                    os.remove(path)
                    print(f"  Deleted original file (keep with --keep-original)")
    print("Complete")


if __name__ == "__main__":
    main()
