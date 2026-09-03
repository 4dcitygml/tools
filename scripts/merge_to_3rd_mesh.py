#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Consolidate CityGML(bldg) from 4th-level meshes (1/2 regional meshes) into 3rd-level meshes.

Reverse operation of split_4th_mesh.py. Consolidate 4th-level sub-files
(9-digit codes ending in 1-4) that belong to a single 3rd-level mesh (8-digit code) into one 3rd-level mesh GML,
and restore conformance with the official PLATEAU 3rd-level mesh distribution format.

- Merge all cityObjectMember elements
- Recombine appearances split by quadrant during division at imageURI granularity
  (aggregate all targets under the same ParameterizedTexture)
- Envelope recalculated from full building extent
- Image folder (<mesh>_bldg_6697_appearance/) remains shared (no rename needed)

Usage:
    # Specify 3rd-level code and folder ({CODE}1..4 auto-collected)
    python scripts/merge_to_3rd_mesh.py --code 53394663 --dir path/to/udx/bldg
    # Or explicitly specify sub-files
    python scripts/merge_to_3rd_mesh.py A1.gml A2.gml A3.gml A4.gml --out 53394663_bldg_6697_op.gml
"""
from __future__ import annotations

import argparse
import glob
import sys
from copy import deepcopy
from pathlib import Path

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.safe_xml import safe_parse  # noqa: E402

CORE = "http://www.opengis.net/citygml/2.0"
GML = "http://www.opengis.net/gml"
APP = "http://www.opengis.net/citygml/appearance/2.0"

Q_CORE = f"{{{CORE}}}cityObjectMember"
Q_APPMEMBER = f"{{{APP}}}appearanceMember"
Q_APPEARANCE = f"{{{APP}}}Appearance"
Q_THEME = f"{{{APP}}}theme"
Q_SDM = f"{{{APP}}}surfaceDataMember"
Q_TARGET = f"{{{APP}}}target"
Q_PARAMTEX = f"{{{APP}}}ParameterizedTexture"
Q_IMAGEURI = f"{{{APP}}}imageURI"
Q_BOUNDEDBY = f"{{{GML}}}boundedBy"
Q_ENVELOPE = f"{{{GML}}}Envelope"
Q_LOWER = f"{{{GML}}}lowerCorner"
Q_UPPER = f"{{{GML}}}upperCorner"


def write_plateau_gml(root: etree._Element, out_path: Path) -> None:
    """Write in the official PLATEAU serialization format (UTF-8 BOM + CRLF + double-quoted declaration)."""
    body = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    body = b'<?xml version="1.0" encoding="UTF-8"?>\n' + body
    body = body.replace(b"\n", b"\r\n")
    body = b"\xef\xbb\xbf" + body
    out_path.write_bytes(body)


def iter_coords(el: etree._Element):
    for tag in (f"{{{GML}}}posList", f"{{{GML}}}pos"):
        for e in el.iter(tag):
            if not e.text:
                continue
            nums = e.text.split()
            for i in range(0, len(nums) - 2, 3):
                try:
                    yield float(nums[i]), float(nums[i + 1]), float(nums[i + 2])
                except ValueError:
                    continue


def _sdm_key(surfdata: etree._Element) -> str:
    """Identity key for surfaceData. ParameterizedTexture entries are grouped by imageURI."""
    if surfdata.tag == Q_PARAMTEX:
        img = surfdata.find(Q_IMAGEURI)
        if img is not None and img.text:
            return "tex:" + img.text.strip()
    # Everything else (X3DMaterial etc.) is keyed by the serialized header excluding target
    header = [c for c in surfdata if c.tag != Q_TARGET]
    sig = surfdata.tag + "|" + "".join(
        etree.tostring(h, encoding="unicode") for h in header
    )
    return "mat:" + sig


def merge(inputs: list[Path], out_path: Path) -> dict:
    if not inputs:
        raise SystemExit("No input sub-files provided")

    first_root = safe_parse(str(inputs[0]), huge_tree=True).getroot()
    nsmap = first_root.nsmap
    root_attrib = dict(first_root.attrib)
    src_env = first_root.find(f"{Q_BOUNDEDBY}/{Q_ENVELOPE}")
    srs_name = src_env.get("srsName") if src_env is not None else "http://www.opengis.net/def/crs/EPSG/0/6697"
    srs_dim = src_env.get("srsDimension") if src_env is not None else "3"

    members: list[etree._Element] = []
    # key (imageURI etc.) -> {"skeleton": surfdata (header only), "targets": [..]}
    tex_groups: dict[str, dict] = {}
    tex_order: list[str] = []
    theme_el = None

    latmin = lonmin = zmin = float("inf")
    latmax = lonmax = zmax = float("-inf")

    n_targets = 0
    for path in inputs:
        root = safe_parse(str(path), huge_tree=True).getroot()

        # appearance
        am = root.find(Q_APPMEMBER)
        if am is not None:
            appearance = am.find(Q_APPEARANCE)
            if theme_el is None:
                th = appearance.find(Q_THEME)
                if th is not None:
                    theme_el = deepcopy(th)
            for sdm in appearance.findall(Q_SDM):
                surfdata = next((c for c in sdm if isinstance(c.tag, str)), None)
                if surfdata is None:
                    continue
                key = _sdm_key(surfdata)
                grp = tex_groups.get(key)
                if grp is None:
                    skeleton = etree.Element(surfdata.tag)
                    for k, v in surfdata.attrib.items():
                        skeleton.set(k, v)
                    for hc in surfdata:
                        if hc.tag != Q_TARGET:
                            skeleton.append(deepcopy(hc))
                    grp = {"skeleton": skeleton, "targets": []}
                    tex_groups[key] = grp
                    tex_order.append(key)
                for t in surfdata.findall(Q_TARGET):
                    grp["targets"].append(t)  # move
                    n_targets += 1

        # cityObjectMembers (move) + Envelope aggregation
        for m in root.findall(Q_CORE):
            members.append(m)
            for la, lo, z in iter_coords(m):
                latmin = min(latmin, la); latmax = max(latmax, la)
                lonmin = min(lonmin, lo); lonmax = max(lonmax, lo)
                zmin = min(zmin, z); zmax = max(zmax, z)

    # Build the output tree
    new_root = etree.Element(first_root.tag, nsmap=nsmap)
    for k, v in root_attrib.items():
        new_root.set(k, v)

    if latmin != float("inf"):
        bounded = etree.SubElement(new_root, Q_BOUNDEDBY)
        env = etree.SubElement(bounded, Q_ENVELOPE)
        env.set("srsName", srs_name); env.set("srsDimension", srs_dim)
        etree.SubElement(env, Q_LOWER).text = f"{latmin} {lonmin} {zmin}"
        etree.SubElement(env, Q_UPPER).text = f"{latmax} {lonmax} {zmax}"

    if tex_order:
        am = etree.SubElement(new_root, Q_APPMEMBER)
        ap = etree.SubElement(am, Q_APPEARANCE)
        if theme_el is not None:
            ap.append(theme_el)
        for key in tex_order:
            grp = tex_groups[key]
            sdm = etree.SubElement(ap, Q_SDM)
            surf = grp["skeleton"]
            for t in grp["targets"]:
                surf.append(t)
            sdm.append(surf)

    for m in members:
        new_root.append(m)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_plateau_gml(new_root, out_path)
    return {
        "inputs": len(inputs),
        "members": len(members),
        "textures": len(tex_order),
        "targets": n_targets,
        "out": out_path,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", type=Path, help="Explicitly specify 4th-level sub-files (.gml)")
    ap.add_argument("--code", help="3rd-level mesh 8-digit code (combined with --dir, auto-collect {CODE}1..4)")
    ap.add_argument("--dir", type=Path, help="Folder containing sub-files")
    ap.add_argument("--out", type=Path, help="Output path (default: {CODE}_bldg_6697_op.gml in --dir)")
    args = ap.parse_args()

    if args.code:
        d = args.dir or Path(".")
        inputs = sorted(Path(p) for p in glob.glob(str(d / f"{args.code}?_bldg_6697_op.gml")))
        if not inputs:
            raise SystemExit(f"File {args.code}[1-4]_bldg_6697_op.gml not found in {d}")
        suffix = inputs[0].name[len(args.code) + 1:]  # _bldg_6697_op.gml
        out = args.out or (d / f"{args.code}{suffix}")
    else:
        inputs = args.inputs
        if not inputs:
            ap.error("Specify sub-files or provide --code/--dir")
        if not args.out:
            ap.error("--out is required (explicit specification mode)")
        out = args.out

    rep = merge(inputs, out)
    size_mb = rep["out"].stat().st_size / 1048576
    flag = "  ⚠️>100MiB" if rep["out"].stat().st_size > 104857600 else ""
    print(f"Consolidate: {rep['inputs']} subs → {rep['out'].name}")
    print(f"  Buildings {rep['members']}  textures {rep['textures']}  targets {rep['targets']}  {size_mb:.1f} MB{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
