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
    python dialects/plateau/merge_to_3rd_mesh.py --code 53394663 --dir path/to/udx/bldg
    # Or explicitly specify sub-files
    python dialects/plateau/merge_to_3rd_mesh.py A1.gml A2.gml A3.gml A4.gml --out 53394663_bldg_6697_op.gml
"""
from __future__ import annotations

import argparse
import glob
import sys
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_xml import (  # noqa: E402
    Q_APPEARANCE, Q_APPEARANCE_MEMBER, Q_IMAGE_URI, Q_MEMBER, Q_PARAMETERIZED_TEXTURE, Q_SURFACE_DATA_MEMBER,
    Q_TARGET, Q_THEME, bbox, rebuild_root, serialize, sub, union, write_gml,
)
from scripts.citygml_constants import GIT_HOST_FILE_LIMIT  # noqa: E402
from scripts.safe_xml import safe_parse  # noqa: E402


def _sdm_key(surfdata) -> str:
    """Identity key for surfaceData. ParameterizedTexture entries are grouped by imageURI."""
    if surfdata.tag == Q_PARAMETERIZED_TEXTURE:
        img = surfdata.find(Q_IMAGE_URI)
        if img is not None and img.text:
            return "tex:" + img.text.strip()
    # Everything else (X3DMaterial etc.) is keyed by the serialized header excluding target
    header = [c for c in surfdata if c.tag != Q_TARGET]
    return "mat:" + surfdata.tag + "|" + "".join(serialize(h) for h in header)


def merge(inputs: list[Path], out_path: Path) -> dict:
    if not inputs:
        raise SystemExit("No input sub-files provided")

    first_root = safe_parse(str(inputs[0]), huge_tree=True).getroot()
    members: list = []
    boxes: list = []
    # key (imageURI etc.) -> {"skeleton": surfdata (header only), "targets": [..]}
    tex_groups: dict[str, dict] = {}
    tex_order: list[str] = []
    theme_el = None

    n_targets = 0
    for path in inputs:
        root = safe_parse(str(path), huge_tree=True).getroot()

        # every appearance member of the sub-file (D-4a1: only the first was read, so the textures
        # of the second and later ones were lost on a split -> merge round trip)
        for am in root.findall(Q_APPEARANCE_MEMBER):
            appearance = am.find(Q_APPEARANCE)
            if appearance is None:
                continue
            if theme_el is None:
                th = appearance.find(Q_THEME)
                if th is not None:
                    theme_el = deepcopy(th)
            for sdm in appearance.findall(Q_SURFACE_DATA_MEMBER):
                surfdata = next((c for c in sdm if isinstance(c.tag, str)), None)
                if surfdata is None:
                    continue
                key = _sdm_key(surfdata)
                grp = tex_groups.get(key)
                if grp is None:
                    skeleton = surfdata.makeelement(surfdata.tag, dict(surfdata.attrib))
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
        for m in root.findall(Q_MEMBER):
            members.append(m)
            boxes.append(bbox(m))

    appearances = []
    if tex_order:
        am = first_root.makeelement(Q_APPEARANCE_MEMBER, {})
        ap = sub(am, Q_APPEARANCE)
        if theme_el is not None:
            ap.append(theme_el)
        for key in tex_order:
            grp = tex_groups[key]
            surf = grp["skeleton"]
            for t in grp["targets"]:
                surf.append(t)
            sub(ap, Q_SURFACE_DATA_MEMBER).append(surf)
        appearances.append(am)
    written = sum(len(g["targets"]) for g in tex_groups.values())
    if written != n_targets:   # every texture target read is written once
        raise SystemExit(f"texture targets: {n_targets} read, {written} written")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_gml(rebuild_root(first_root, members, union(boxes), appearances=appearances), out_path, like=inputs[0])
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
        # the four quadrants only (D-4a5: `?` took any ninth character, a re-split's name too)
        inputs = sorted(Path(p) for p in glob.glob(str(d / f"{args.code}[1-4]_bldg_*_op.gml")))
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
    flag = "  ⚠️>100MiB" if rep["out"].stat().st_size > GIT_HOST_FILE_LIMIT else ""
    print(f"Consolidate: {rep['inputs']} subs → {rep['out'].name}")
    print(f"  Buildings {rep['members']}  textures {rep['textures']}  targets {rep['targets']}  {size_mb:.1f} MB{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
