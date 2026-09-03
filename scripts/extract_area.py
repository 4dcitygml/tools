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

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.safe_xml import safe_parser  # noqa: E402

GML = "http://www.opengis.net/gml"


def member_bbox(member: etree._Element):
    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    for tag in (f"{{{GML}}}posList", f"{{{GML}}}pos"):
        for el in member.iter(tag):
            if not el.text:
                continue
            nums = el.text.split()
            for i in range(0, len(nums) - 2, 3):
                try:
                    x, y, z = float(nums[i]), float(nums[i + 1]), float(nums[i + 2])
                except ValueError:
                    continue
                xmin, xmax = min(xmin, x), max(xmax, x)
                ymin, ymax = min(ymin, y), max(ymax, y)
                zmin, zmax = min(zmin, z), max(zmax, z)
    if xmin == float("inf"):
        return None
    return xmin, ymin, zmin, xmax, ymax, zmax


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

    parser = safe_parser(huge_tree=True, remove_blank_text=False)
    root = etree.parse(str(args.input), parser).getroot()
    if any(etree.QName(c).localname == "appearanceMember" for c in root
           if isinstance(c.tag, str)):
        raise SystemExit("Files with global appearance not supported")

    members = [c for c in root if isinstance(c.tag, str)
               and etree.QName(c).localname == "cityObjectMember"]
    kept, bbox_all = [], None
    for m in members:
        bb = member_bbox(m)
        if bb is None:
            continue
        cx, cy = (bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            kept.append(m)
            if bbox_all is None:
                bbox_all = list(bb)
            else:
                for i in (0, 1, 2):
                    bbox_all[i] = min(bbox_all[i], bb[i])
                for i in (3, 4, 5):
                    bbox_all[i] = max(bbox_all[i], bb[i])

    env = root.find(f"{{{GML}}}boundedBy/{{{GML}}}Envelope")
    srs = dict(env.attrib) if env is not None else {}

    new_root = etree.Element(root.tag, nsmap=root.nsmap)
    for k, v in root.attrib.items():
        new_root.set(k, v)
    if bbox_all:
        bounded = etree.SubElement(new_root, f"{{{GML}}}boundedBy")
        e2 = etree.SubElement(bounded, f"{{{GML}}}Envelope")
        for k, v in srs.items():
            e2.set(k, v)
        etree.SubElement(e2, f"{{{GML}}}lowerCorner").text = \
            f"{bbox_all[0]} {bbox_all[1]} {bbox_all[2]}"
        etree.SubElement(e2, f"{{{GML}}}upperCorner").text = \
            f"{bbox_all[3]} {bbox_all[4]} {bbox_all[5]}"
    for m in kept:
        new_root.append(m)
    etree.ElementTree(new_root).write(str(args.output), encoding="UTF-8",
                                      xml_declaration=True)
    print(f"Extracted {len(kept)}/{len(members)} buildings → {args.output}"
          f" ({args.output.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
