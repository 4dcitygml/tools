#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Development CLI to create UV map JSON from CityGML ParameterizedTexture.

Scans the specified CityGML file, collects polygons under buildings (direct gml:Polygon
and lod2Solid surfaceMember references), and exports texture coordinates from
ParameterizedTexture as a table mapping "polygon ID → image filename and UV coordinate list".

Output format (uvmap.json):

    {"<polygonID>": {"img": "<image filename>", "uv": [[u, v], ...]}}

If an existing output file exists, reads it and merges the current results with priority
to the current run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts import citygml_faces  # noqa: E402
from scripts.safe_xml import safe_parse  # noqa: E402

DEFAULT_GML = (
    REPO_ROOT
    / "13101_chiyoda-ku_pref_2023_citygml_1_op/udx/bldg/53394611_bldg_6697_op.gml"
)
DEFAULT_OUTPUT = REPO_ROOT / "docs/appearance/uvmap.json"

NS_BLDG = "http://www.opengis.net/citygml/building/2.0"
NS_GML = "http://www.opengis.net/gml"
NS_APP = "http://www.opengis.net/citygml/appearance/2.0"
NS_XLINK = "http://www.w3.org/1999/xlink"


def collect_target_polygons(root, bldg_ids: list[str] | None) -> set[str]:
    """Return the set of polygon IDs belonging to the target buildings."""
    targets: set[str] = set()
    for building in root.iter(f"{{{NS_BLDG}}}Building"):
        bid = building.get(f"{{{NS_GML}}}id")
        if bldg_ids and bid not in bldg_ids:
            continue
        for poly in building.iter(f"{{{NS_GML}}}Polygon"):
            pid = poly.get(f"{{{NS_GML}}}id")
            if pid:
                targets.add(pid)
        for solid in building.iter(f"{{{NS_BLDG}}}lod2Solid"):
            for member in solid.iter(f"{{{NS_GML}}}surfaceMember"):
                href = member.get(f"{{{NS_XLINK}}}href")
                if href and href.startswith("#"):
                    targets.add(href[1:])
    return targets


def extract_uv_entries(root, targets: set[str]) -> dict[str, dict]:
    """polygon ID -> {img, uv}: the coordinates of the polygon's exterior ring, read as the
    editors read them (scripts/citygml_faces.py: by ring id, else the target's first entry)."""
    by_ring, by_poly = citygml_faces.texture_rings(root, {"gml": NS_GML, "app": NS_APP})
    entries: dict[str, dict] = {}
    for poly in root.iter(f"{{{NS_GML}}}Polygon"):
        pid = poly.get(f"{{{NS_GML}}}id")
        if pid not in targets:
            continue
        ext = poly.find(f"{{{NS_GML}}}exterior/{{{NS_GML}}}LinearRing")
        ring = (ext.get(f"{{{NS_GML}}}id") or "") if ext is not None else ""
        tex = citygml_faces.face_texture({"id": pid, "ring": ring}, by_ring, by_poly)
        if tex:
            entries[pid] = {"img": tex["img"].rsplit("/", 1)[-1], "uv": tex["uv"]}
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate uvmap.json from CityGML"
    )
    parser.add_argument("--gml", type=Path, default=DEFAULT_GML)
    parser.add_argument("--bldg-ids", nargs="*", help="Target building gml:ids (default: all buildings)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    print(f"parsing: {args.gml.name}")
    root = safe_parse(str(args.gml), huge_tree=True).getroot()

    targets = collect_target_polygons(root, args.bldg_ids)
    print(f"target polygons: {len(targets)}")

    entries = extract_uv_entries(root, targets)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        merged = json.loads(args.output.read_text(encoding="utf-8"))
        merged.update(entries)
    else:
        merged = entries
    args.output.write_text(
        json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {args.output} ({len(merged)} entries)")


if __name__ == "__main__":
    main()
