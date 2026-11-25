#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Match buildings across mesh years and measure update volume for the same CityGML mesh (PLATEAU).

The PLATEAU pack's command line over scripts/building_compare.py (the generic matching: same
gml:id, LOD0 1 mm fingerprint, mutual IoU), with the counts per municipality (uro:city) and the
`--city-code` filter of a delivery mesh that covers several municipalities.

Dependencies: lxml==6.1.1, shapely==2.1.2
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dialects.plateau.pack import PACK  # noqa: E402
from scripts import building_compare as C  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_gml", type=Path)
    parser.add_argument("new_gml", type=Path)
    parser.add_argument("--old-label", required=True)
    parser.add_argument("--new-label", required=True)
    parser.add_argument("--minimum-iou", type=float, default=0.5)
    parser.add_argument("--search-padding-m", type=float, default=2.0)
    parser.add_argument("--city-code", help="compare buildings of this municipality code only")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    frame = C.frame_for(args.old_gml)   # one frame for both editions of the mesh
    old_all = C.load_buildings(args.old_gml, frame, PACK)
    new_all = C.load_buildings(args.new_gml, frame, PACK)
    old_city_counts = Counter(building.city_code or "unknown" for building in old_all.values())
    new_city_counts = Counter(building.city_code or "unknown" for building in new_all.values())
    if args.city_code:
        old = {key: value for key, value in old_all.items() if value.city_code == args.city_code}
        new = {key: value for key, value in new_all.items() if value.city_code == args.city_code}
    else:
        old = old_all
        new = new_all
    result = {
        "old": {"label": args.old_label, "path": str(args.old_gml)},
        "new": {"label": args.new_label, "path": str(args.new_gml)},
        "parameters": {
            "matching_order": ["same_id", "lod0_fingerprint_1mm", "mutual_best_iou"],
            "minimum_iou": args.minimum_iou,
            "search_padding_m": args.search_padding_m,
            "fingerprint_grid_m": 0.001,
            "reference_latitude": frame.reference_latitude,
            "city_code_filter": args.city_code,
        },
        "source_scope": {
            "old_all_buildings": len(old_all),
            "new_all_buildings": len(new_all),
            "old_city_counts": dict(sorted(old_city_counts.items())),
            "new_city_counts": dict(sorted(new_city_counts.items())),
        },
        **C.compare(old, new, args.minimum_iou, args.search_padding_m),
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
