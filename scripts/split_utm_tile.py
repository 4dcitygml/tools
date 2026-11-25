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

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_xml import bbox as member_bbox, members, rebuild_root, refuse_global_appearance, union, write_gml  # noqa: E402
from scripts.safe_xml import safe_parse  # noqa: E402

TILE_NAME = re.compile(r"(\d{3,4})_(\d{4})(?:_\d+)?")   # <E km>_<N km>[_<size km>]


def tile_origin_from_name(name: str) -> tuple[int, int]:
    m = TILE_NAME.match(name)
    if not m:
        raise SystemExit(f"Cannot extract km-scale tile origin E_N from filename: {name}")
    return int(m.group(1)) * 1000, int(m.group(2)) * 1000


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
    outdir.mkdir(parents=True, exist_ok=True)

    root = safe_parse(str(args.input), huge_tree=True, remove_blank_text=False).getroot()
    refuse_global_appearance(root, args.input.name)

    all_members = members(root)
    step = args.out_km * 1000
    cells: dict[tuple[int, int], list] = {}
    cell_boxes: dict[tuple[int, int], list] = {}
    no_coord = 0
    for m in all_members:
        bb = member_bbox(m)
        if bb is None:
            no_coord += 1
            key = (0, 0)
        else:
            cx, cy = (bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0
            key = (min(n - 1, max(0, int((cx - origin_e) // step))),
                   min(n - 1, max(0, int((cy - origin_n) // step))))
        cells.setdefault(key, []).append(m)
        cell_boxes.setdefault(key, []).append(bb)

    # the rest of the name after <E>_<N>[_<size>] (normally empty; D-4a5: a re-split kept the old size)
    suffix = TILE_NAME.sub("", args.input.stem, count=1)
    total_out = 0
    for (ix, iy), ms in sorted(cells.items()):
        e_km = (origin_e + ix * step) // 1000
        n_km = (origin_n + iy * step) // 1000
        new_root = rebuild_root(root, ms, union(cell_boxes[(ix, iy)]), name=f"{e_km}_{n_km}_{args.out_km}km")
        out = outdir / f"{e_km}_{n_km}_{args.out_km}{suffix}.gml"
        write_gml(new_root, out, like=args.input)
        total_out += len(ms)
        print(f"  {out.name}  {len(ms)} buildings  {out.stat().st_size / 1e6:.1f} MB")

    match_status = "Match" if total_out == len(all_members) else "Mismatch!"
    print(f"Total (output) {total_out} / input {len(all_members)} → {match_status}  No coords: {no_coord}")
    if total_out != len(all_members):
        sys.exit(1)


if __name__ == "__main__":
    main()
