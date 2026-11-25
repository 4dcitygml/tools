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
  python3 dialects/plateau/setup_city_data.py <ZIP_URL> --list

  # Retrieve only specified meshes and create initial structure (default: all bldg meshes)
  python3 dialects/plateau/setup_city_data.py <ZIP_URL> --outdir . --mesh 53394611

Dependencies: retrieval uses only the standard library. lxml is required only if 4th-level splitting occurs.
"""
import argparse
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import range_zip  # noqa: E402

MIB = 1024 * 1024


def dataset_prefix(url, names):
    """Return the root folder name common to all entries. For distribution ZIPs without one, derive it from the URL filename."""
    roots = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(roots) == 1:
        return ""  # the ZIP itself has a root folder
    base = os.path.basename(urllib.parse.urlparse(url).path)
    return re.sub(r"\.zip$", "", base) + "/"


def bldg_mesh_table(infos):
    """Group GML files under udx/bldg by mesh code."""
    table = {}
    for info in infos:
        m = re.search(r"udx/bldg/(\d{8,9})_bldg_[^/]*\.gml$", info.filename)
        if m:
            table.setdefault(m.group(1), []).append(info)
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

    archive = range_zip.open_zip(args.url)
    print(f"Distribution ZIP: {archive.size / 1e9:.2f} GB — retrieving central directory only")
    entries = archive.infolist()
    meshes = bldg_mesh_table(entries)

    if args.list:
        print(f"Number of bldg meshes: {len(meshes)}")
        for code in sorted(meshes):
            gml = sum(e.file_size for e in meshes[code])
            app = sum(
                e.file_size for e in entries if f"udx/bldg/{code}_bldg" in e.filename and "appearance" in e.filename
            )
            mark = " ★Requires 4th-level split" if gml >= args.max_mib * MIB else ""
            print(f"  {code}  GML {gml / MIB:6.1f} MiB / Textures {app / MIB:6.1f} MiB{mark}")
        return

    codes = args.mesh or sorted(meshes)
    unknown = [c for c in codes if c not in meshes]
    if unknown:
        raise SystemExit(f"Mesh(es) not found in ZIP: {unknown} (check with --list)")

    chosen = [e for e in entries if re.search(r"(^|/)(codelists|metadata|specification)/", e.filename)]
    for c in codes:
        for e in entries:
            if f"udx/bldg/{c}_bldg" in e.filename:
                if args.no_appearance and "appearance" in e.filename:
                    continue
                chosen.append(e)
    total = sum(e.file_size for e in chosen)
    print(f"Retrieval target: {len(chosen)} files / {total / MIB:.1f} MiB (meshes: {', '.join(codes)})")
    prefix = dataset_prefix(args.url, archive.namelist())
    if prefix:
        print(f"Distribution ZIP has no root folder, adding {prefix} prefix")
    range_zip.extract(archive, [e.filename for e in chosen], args.outdir, prefix)

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
                    print("  Deleted original file (keep with --keep-original)")
    print("Complete")


if __name__ == "__main__":
    main()
