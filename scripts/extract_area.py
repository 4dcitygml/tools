#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Extract only buildings within a rectangular bounding box from CityGML (generic tool for demo area extraction etc.).

Coordinate-system-agnostic: --bbox is specified in the CRS values of the input data
(can be lat/lon, meters, feet, etc.; directly compares 1st and 2nd axes of posList).
Buildings with centroid within the bbox are adopted **whole** (not cut).
Does not support global appearance (stops if present).

Usage:
    python3 scripts/extract_area.py IN.gml --bbox XMIN YMIN XMAX YMAX --output OUT.gml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_xml import bbox as member_bbox, members, rebuild_root, refuse_global_appearance, union, write_gml  # noqa: E402
from scripts.safe_xml import safe_parse  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                    help="Rectangular bounding box in input CRS values")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    x0, y0, x1, y1 = args.bbox

    root = safe_parse(str(args.input), huge_tree=True, remove_blank_text=False).getroot()
    refuse_global_appearance(root, args.input.name)
    all_members = members(root)
    kept, boxes = [], []
    for m in all_members:
        bb = member_bbox(m)
        if bb is None:
            continue
        cx, cy = (bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            kept.append(m)
            boxes.append(bb)
    write_gml(rebuild_root(root, kept, union(boxes)), args.output, like=args.input)
    print(f"Extracted {len(kept)}/{len(all_members)} buildings → {args.output}"
          f" ({args.output.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
