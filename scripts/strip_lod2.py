#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Generate LOD2-excluded version (lightweight mirror for attribute-centric use) and measure capacity breakdown (#64).

For attribute-centric use (use type, floor count, height, source, etc.), LOD2 detailed geometry + textures are often unnecessary.
This tool **removes LOD2+ detailed geometry and appearance (textures)** from each building GML and generates
a **lightweight derivative with only attributes + LOD0 (roof edge) + LOD1 (box model)**. Aims to lighten the clone
and lower participation barriers for attribute use cases (one-way derivative; regenerable from full version, so reversibility unnecessary).

Removed items (LOD2/3 detailed geometry, textures):
- `bldg:boundedBy` under building (thematic boundary surfaces like Wall/Roof/Ground = LOD2 surfaces)
- `bldg:lod2Solid` / `lod2MultiSurface` / `lod2Geometry` / `lod2TerrainIntersection`
- `bldg:lod3*` / `bldg:lod4*` (LOD3/4 geometry, rooms)
- `bldg:outerBuildingInstallation` / `interiorBuildingInstallation` / `interiorRoom`
- `app:appearanceMember` under CityModel (inline appearance) + `*_appearance/` image directory

Preserved (attributes + coarse positioning geometry):
- All attributes (use type, floor count, height, source `uro:*`, address, buildingID, etc.)
- `bldg:lod0RoofEdge` / `lod0FootPrint` (LOD0), `bldg:lod1Solid` / `lod1MultiSurface` (LOD1)

Usage:
    # measure capacity breakdown (do not write; output LOD/appearance breakdown in JSON/table)
    python scripts/strip_lod2.py --measure 13101_chiyoda-ku_pref_2023_citygml_1_op [DATASET2 ...]
    # generate lightweight derivative (attributes + LOD0/1 only; do not copy texture images)
    python scripts/strip_lod2.py 13101_chiyoda-ku_pref_2023_citygml_1_op --output out/lite
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.diff_citygml import APP_NS  # noqa: E402
from scripts.safe_xml import safe_parse  # noqa: E402

# localnames of elements to remove directly under a building (bldg namespace: LOD2+ detailed geometry, thematic surfaces, installations, rooms).
DROP_LOCALNAMES = frozenset({
    "boundedBy",
    "lod2Solid", "lod2MultiSurface", "lod2MultiCurve", "lod2Geometry", "lod2TerrainIntersection",
    "lod3Solid", "lod3MultiSurface", "lod3MultiCurve", "lod3Geometry", "lod3TerrainIntersection",
    "lod4Solid", "lod4MultiSurface", "lod4Geometry", "lod4TerrainIntersection",
    "outerBuildingInstallation", "interiorBuildingInstallation", "interiorRoom",
})

_BUILDING_LOCALNAMES = frozenset({"Building"})
_APPEARANCE_MEMBER = f"{{{APP_NS}}}appearanceMember"


def _localname(elem) -> str:
    if not isinstance(elem.tag, str):  # comments/PIs
        return ""
    return etree.QName(elem).localname


def _remove_appearance(root) -> int:
    """Remove appearanceMember elements directly under CityModel; return the number removed."""
    n = 0
    for am in root.findall(_APPEARANCE_MEMBER):
        root.remove(am)
        n += 1
    return n


def _remove_lod2plus(root) -> int:
    """Remove LOD2+ detailed geometry and installations from all buildings; return the number of buildings processed."""
    n_buildings = 0
    for el in root.iter():
        if _localname(el) in _BUILDING_LOCALNAMES:
            n_buildings += 1
            for child in list(el):
                if _localname(child) in DROP_LOCALNAMES:
                    el.remove(child)
    return n_buildings


def strip_tree(tree) -> dict:
    """Slim down the whole tree in place (remove appearance + LOD2+ geometry)."""
    root = tree.getroot()
    n_app = _remove_appearance(root)
    n_buildings = _remove_lod2plus(root)
    return {"buildings": n_buildings, "appearance_members": n_app}


def _serialized_len(tree) -> int:
    return len(etree.tostring(tree, xml_declaration=True, encoding="UTF-8"))


def measure_tree(tree) -> dict:
    """Measure the per-category byte breakdown of a tree by stripping it in stages and diffing.

    Serializing the whole tree at each stage and taking **the difference** avoids the
    overcounting that occurs when elements are serialized individually and namespace
    declarations get duplicated onto each element.
    """
    root = tree.getroot()
    full = _serialized_len(tree)
    n_app = _remove_appearance(root)
    after_app = _serialized_len(tree)
    n_buildings = _remove_lod2plus(root)
    stripped = _serialized_len(tree)
    return {
        "buildings": n_buildings,
        "appearance_members": n_app,
        "serialized_full_bytes": full,
        "appearance_inline_bytes": full - after_app,
        "lod2plus_geometry_bytes": after_app - stripped,
        "stripped_gml_bytes": stripped,
    }


def _texture_dir_bytes(bldg_dir: Path) -> int:
    """Total bytes of *_appearance directories (texture images) under udx/bldg."""
    total = 0
    for app_dir in bldg_dir.glob("*_appearance"):
        for p in app_dir.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    return total


def _iter_gml(dataset: Path) -> list:
    bldg = dataset / "udx" / "bldg"
    if not bldg.is_dir():
        # Also accept the case where the path points directly at the bldg directory rather than the dataset root
        bldg = dataset
    return sorted(bldg.glob("*.gml"))


def measure_dataset(dataset: Path) -> dict:
    """Measure a dataset's size breakdown by LOD/appearance (no output files written)."""
    gml_files = _iter_gml(dataset)
    bldg_dir = (dataset / "udx" / "bldg") if (dataset / "udx" / "bldg").is_dir() else dataset
    orig_gml = 0
    stripped_gml = 0
    lod2_geom = 0
    app_inline = 0
    n_buildings = 0
    for f in gml_files:
        orig_gml += f.stat().st_size
        info = measure_tree(safe_parse(str(f)))
        lod2_geom += info["lod2plus_geometry_bytes"]
        app_inline += info["appearance_inline_bytes"]
        n_buildings += info["buildings"]
        stripped_gml += info["stripped_gml_bytes"]
    tex_bytes = _texture_dir_bytes(bldg_dir)
    return {
        "dataset": dataset.name,
        "buildings": n_buildings,
        "gml_files": len(gml_files),
        "original_gml_bytes": orig_gml,
        "stripped_gml_bytes": stripped_gml,
        "lod2plus_geometry_bytes": lod2_geom,
        "appearance_inline_bytes": app_inline,
        "texture_image_bytes": tex_bytes,
        # Size of the attribute-centric lite version (GML). Texture images not included.
        "lite_total_bytes": stripped_gml,
        "full_total_bytes": orig_gml + tex_bytes,
    }


def _mb(n: int) -> float:
    return round(n / 1048576, 1)


def render_measure_markdown(results: list) -> str:
    lines = ["## 📦 LOD2-Excluded Version Capacity Breakdown (#64)", "",
             "Capacity comparison: attribute-centric lite version (attributes + LOD0/1) vs. full version (+ LOD2/3 geometry + textures).", "",
             "| Dataset | Buildings | Full | Lite | Reduction | LOD2+ Geometry | Inline Appearance | Texture Images |",
             "|---|--:|--:|--:|--:|--:|--:|--:|"]
    for r in results:
        reduction = 1 - r["lite_total_bytes"] / r["full_total_bytes"] if r["full_total_bytes"] else 0
        lines.append(
            f"| {r['dataset']} | {r['buildings']:,} | {_mb(r['full_total_bytes']):,} MB | "
            f"{_mb(r['lite_total_bytes']):,} MB | **{reduction:.0%}** | "
            f"{_mb(r['lod2plus_geometry_bytes']):,} MB | {_mb(r['appearance_inline_bytes']):,} MB | "
            f"{_mb(r['texture_image_bytes']):,} MB |")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_lite(dataset: Path, out_dir: Path) -> dict:
    """Derive the lite version into out_dir/<dataset name>/udx/bldg/ (texture images are not generated)."""
    gml_files = _iter_gml(dataset)
    dest_bldg = out_dir / dataset.name / "udx" / "bldg"
    dest_bldg.mkdir(parents=True, exist_ok=True)
    n = 0
    stripped_bytes = 0
    for f in gml_files:
        tree = safe_parse(str(f))
        strip_tree(tree)
        out = dest_bldg / f.name
        tree.write(str(out), xml_declaration=True, encoding="UTF-8")
        stripped_bytes += out.stat().st_size
        n += 1
    # Copy codelists since they are needed to resolve attribute meanings (textures excluded).
    src_codelists = dataset / "codelists"
    if src_codelists.is_dir():
        shutil.copytree(src_codelists, out_dir / dataset.name / "codelists", dirs_exist_ok=True)
    return {"dataset": dataset.name, "gml_files": n, "stripped_gml_bytes": stripped_bytes}


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("datasets", nargs="+", type=Path, help="Dataset directory (contains */udx/bldg)")
    p.add_argument("--measure", action="store_true", help="Measure and display capacity breakdown (do not write)")
    p.add_argument("--output", type=Path, default=None, help="Output directory for lite version")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    args = p.parse_args(argv)

    if args.measure:
        results = [measure_dataset(d) for d in args.datasets]
        if args.json:
            json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_measure_markdown(results))
        return 0

    if not args.output:
        p.error("--output required to generate lite version (use --measure for measurement only).")
    results = [write_lite(d, args.output) for d in args.datasets]
    for r in results:
        print(f"Generated: {args.output / r['dataset']} ({r['gml_files']} files, "
              f"{_mb(r['stripped_gml_bytes'])} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
