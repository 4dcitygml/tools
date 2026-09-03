#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Split CityGML tiles in meter-based CRS (UTM, etc.) into smaller tiles.

Generic splitting for non-Japanese data where the regional mesh system (split_4th_mesh.py) is not applicable.
Verified with German AdV LoD2 (CityGML 1.0, ETRS89/UTM32, e.g., 2km tile `690_5334.gml` from Bavaria open data).

- Assign buildings (cityObjectMember) **whole** to the small tile containing their bbox centroid (no splitting)
- Output name is `<E>_<N>_<size_km>.gml` (e.g., 690_5335_1.gml = 1km tile at SW corner E690km/N5335km)
- Envelope recalculated from each small tile's actual data extent
- Global appearance not supported (warns and stops if present; AdV LoD2 has no textures)

Usage:
    python3 scripts/split_utm_tile.py 690_5334.gml --tile-km 2 --out-km 1 --outdir DIR
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.safe_xml import safe_parser  # noqa: E402

GML = "http://www.opengis.net/gml"


def tile_origin_from_name(name: str) -> tuple[int, int]:
    m = re.match(r"(\d{3,4})_(\d{4})", name)
    if not m:
        raise SystemExit(f"Cannot extract km-scale tile origin E_N from filename: {name}")
    return int(m.group(1)) * 1000, int(m.group(2)) * 1000


def member_bbox(member: etree._Element):
    """Return the (E, N) bbox over all posList/pos inside a cityObjectMember (assumes 3D coordinates)."""
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
    ap.add_argument("--tile-km", type=int, default=2, help="Input tile side length (km) (default: 2)")
    ap.add_argument("--out-km", type=int, default=1, help="Output tile side length (km) (default: 1)")
    ap.add_argument("--outdir", type=Path, default=None, help="Output directory (default: same as input)")
    args = ap.parse_args()

    if args.tile_km % args.out_km != 0:
        raise SystemExit("--tile-km must be a multiple of --out-km")
    n = args.tile_km // args.out_km
    origin_e, origin_n = tile_origin_from_name(args.input.name)
    outdir = args.outdir or args.input.parent

    parser = safe_parser(huge_tree=True, remove_blank_text=False)
    root = etree.parse(str(args.input), parser).getroot()

    if any(etree.QName(c).localname == "appearanceMember" for c in root
           if isinstance(c.tag, str)):
        raise SystemExit("Tiles with global appearance not supported (texture redistribution required)")

    members = [c for c in root if isinstance(c.tag, str)
               and etree.QName(c).localname == "cityObjectMember"]
    step = args.out_km * 1000
    cells: dict[tuple[int, int], list] = {}
    cell_bbox: dict[tuple[int, int], list] = {}
    no_coord = 0
    for m in members:
        bb = member_bbox(m)
        if bb is None:
            no_coord += 1
            key = (0, 0)
        else:
            cx, cy = (bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0
            key = (min(n - 1, max(0, int((cx - origin_e) // step))),
                   min(n - 1, max(0, int((cy - origin_n) // step))))
        cells.setdefault(key, []).append(m)
        if bb is not None:
            cur = cell_bbox.setdefault(key, list(bb))
            cur[0], cur[1], cur[2] = min(cur[0], bb[0]), min(cur[1], bb[1]), min(cur[2], bb[2])
            cur[3], cur[4], cur[5] = max(cur[3], bb[3]), max(cur[4], bb[4]), max(cur[5], bb[5])

    # Carry over the srs attributes of the original Envelope
    env = root.find(f"{{{GML}}}boundedBy/{{{GML}}}Envelope")
    srs = dict(env.attrib) if env is not None else {}

    suffix = re.sub(r"^\d{3,4}_\d{4}", "", args.input.stem)  # rest of the name (normally empty)
    total_out = 0
    for (ix, iy), ms in sorted(cells.items()):
        e_km = (origin_e + ix * step) // 1000
        n_km = (origin_n + iy * step) // 1000
        new_root = etree.Element(root.tag, nsmap=root.nsmap)
        for k, v in root.attrib.items():
            new_root.set(k, v)
        name_el = etree.SubElement(new_root, f"{{{GML}}}name")
        name_el.text = f"{e_km}_{n_km}_{args.out_km}km"
        bb = cell_bbox.get((ix, iy))
        if bb:
            bounded = etree.SubElement(new_root, f"{{{GML}}}boundedBy")
            e2 = etree.SubElement(bounded, f"{{{GML}}}Envelope")
            for k, v in srs.items():
                e2.set(k, v)
            etree.SubElement(e2, f"{{{GML}}}lowerCorner").text = f"{bb[0]} {bb[1]} {bb[2]}"
            etree.SubElement(e2, f"{{{GML}}}upperCorner").text = f"{bb[3]} {bb[4]} {bb[5]}"
        for m in ms:
            new_root.append(m)
        out = outdir / f"{e_km}_{n_km}_{args.out_km}{suffix}.gml"
        etree.ElementTree(new_root).write(str(out), encoding="UTF-8",
                                          xml_declaration=True)
        total_out += len(ms)
        print(f"  {out.name}  {len(ms)} buildings  {out.stat().st_size / 1e6:.1f} MB")

    match_status = "Match" if total_out == len(members) else "Mismatch!"
    print(f"Total (output) {total_out} / input {len(members)} → {match_status}  No coords: {no_coord}")
    if total_out != len(members):
        sys.exit(1)


if __name__ == "__main__":
    main()
