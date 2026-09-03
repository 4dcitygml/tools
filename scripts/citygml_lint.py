#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""CityGML data quality lint (generic, data-agnostic) — check geometric structural integrity per building.

Beyond `validate_citygml` (well-formed + XSD), this checks only **geometric structural failures
that are universal to any CityGML data**. **Does not depend on specific data conventions like PLATEAU**
(convention-dependent checks such as sentinel unknowns, codelists, valid ranges are separated
into `plateau_lint.py`). This is the data equivalent of standard SE assertion/invariant/property-based
tests; this layer is **reusable for any CityGML**.

Check rules (error = structural failure that never appears in correct data, 0 cases in real data):
    - ring_not_closed    LinearRing start != end (not closed)
    - coord_count        posList coordinate count is not a multiple of dimension (3)
    - too_few_points     LinearRing has < 4 vertices (insufficient for closed polygon)
    - degenerate_face    fewer than 3 distinct vertices (degenerate face)
    - self_intersection  face (lod0FootPrint/lod0RoofEdge/GroundSurface) self-intersects
warning (structurally valid but suspicious, non-blocking):
    - duplicate_geometry different buildings in same file with identical geometry (possible duplicate/containment)

This module exposes a lint engine (`run_lint`/`collect_ci_files`/`render_markdown`) and geometry parsing helpers,
which `plateau_lint.py` reuses for convention-layer checks. Coordinate dimension assumes 3D (e.g., EPSG:6697).

Usage:
    python scripts/citygml_lint.py FILE.gml [FILE2.gml ...]        # check all buildings
    python scripts/citygml_lint.py FILE.gml --json
    python scripts/citygml_lint.py --file-list L --base-sha B --head-sha H --repo R  # CI: changed buildings only
exit code: 1 if any error (for CI blocking), otherwise 0.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Callable, Optional

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_constants import CITYGML_LINT_MARKER, COORD_DIM  # noqa: E402
from scripts.safe_xml import safe_iterparse  # noqa: E402
from scripts.diff_citygml import (  # noqa: E402
    _BUILDING_TAGS,
    _drop,
    _geometry_hash,
    _gml_id,
    _local,
    GML_NS,
    load_buildings,
)

Source = object  # Path | str | bytes
# Check-function type: (building element, geometry-hash index or None) -> {"errors":[...], "warnings":[...]}
CheckFn = Callable[[etree._Element, Optional[dict]], dict]


# --- Geometry parsing (public helpers also reused by plateau_lint) ----------
def ring_points(ring: etree._Element) -> dict:
    """Get (coordinate-value count, point list or None) from a LinearRing. Numerifies posList.

    If the coordinate count is not a multiple of the dimension: points=None,
    bad_count=True (coord_count error).
    """
    pos = None
    for c in ring.iter():
        cns, cname = _local(c)
        if cns == GML_NS and cname == "posList":
            pos = c
            break
    if pos is None:
        return {"n": 0, "points": None, "bad_count": False, "no_poslist": True}
    toks = (pos.text or "").split()
    n = len(toks)
    if n == 0 or n % COORD_DIM != 0:
        return {"n": n, "points": None, "bad_count": n % COORD_DIM != 0, "no_poslist": False}
    try:
        pts = [
            tuple(float(toks[i + k]) for k in range(COORD_DIM))
            for i in range(0, n, COORD_DIM)
        ]
    except ValueError:
        return {"n": n, "points": None, "bad_count": True, "no_poslist": False}
    return {"n": n, "points": pts, "bad_count": False, "no_poslist": False}


def iter_linear_rings(elem: etree._Element):
    for r in elem.iter():
        ns, name = _local(r)
        if ns == GML_NS and name == "LinearRing":
            yield r


def footprint_rings(building: etree._Element) -> list:
    """Ring point lists under the building's LOD0 outline and GroundSurface."""
    out = []
    for el in building.iter():
        _, name = _local(el)
        if name in ("lod0FootPrint", "lod0RoofEdge", "GroundSurface"):
            for ring in iter_linear_rings(el):
                out.append(ring_points(ring))
    return out


def number_by_localname(building: etree._Element, localname: str) -> Optional[float]:
    """Take exactly one numeric value from the text of the element with the given localname (None if absent). Also used by plateau_lint."""
    for el in building.iter():
        _, name = _local(el)
        if name == localname:
            text = (el.text or "").strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
    return None


# --- Self-intersection (2D, footprint) --------------------------------------
def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _proper_intersect(a, b, c, d) -> bool:
    d1, d2 = _orient(a, b, c), _orient(a, b, d)
    d3, d4 = _orient(c, d, a), _orient(c, d, b)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def self_intersects(points: list) -> bool:
    """Whether a closed ring (last = first) self-intersects (proper intersection in 2D on the first two coordinates = (lat, lon))."""
    ring = [(p[0], p[1]) for p in points[:-1]]
    n = len(ring)
    if n < 4:
        return False
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (i + 1) % n == j or (j + 1) % n == i:
                continue
            c, d = ring[j], ring[(j + 1) % n]
            if _proper_intersect(a, b, c, d):
                return True
    return False


# --- Generic CityGML building checks ----------------------------------------
def check_building(building: etree._Element, geom_index: Optional[dict] = None) -> dict:
    """Check the geometric structure of one building (generic CityGML, convention-independent)."""
    errors: list = []
    warnings: list = []
    seen: set = set()

    def err(code, detail):
        if code not in seen:
            errors.append({"code": code, "detail": detail})
            seen.add(code)

    for ring in iter_linear_rings(building):
        rp = ring_points(ring)
        if rp["no_poslist"]:
            continue
        if rp["bad_count"]:
            err("coord_count", f"posList has {rp['n']} coordinate values, not a multiple of dimension {COORD_DIM}")
            continue
        pts = rp["points"]
        if pts is None:
            continue
        if len(pts) < 4:
            err("too_few_points", f"LinearRing has {len(pts)} vertices (not enough for a closed polygon)")
        if pts and pts[0] != pts[-1]:
            err("ring_not_closed", "LinearRing start and end points differ (ring is not closed)")
        if len({tuple(p) for p in pts}) < 3:
            err("degenerate_face", "Fewer than 3 distinct vertices (degenerate face)")

    for fp in footprint_rings(building):
        if fp["points"] and len(fp["points"]) >= 4 and self_intersects(fp["points"]):
            err("self_intersection", "A surface (e.g. footprint) self-intersects")
            break

    if geom_index is not None:
        gh = _geometry_hash(building)
        if gh is not None:
            others = [x for x in geom_index.get(gh, []) if x != _gml_id(building)]
            if others:
                warnings.append({
                    "code": "duplicate_geometry",
                    "detail": f"Other building(s) with identical geometry: {', '.join(others[:3])}",
                })

    return {"errors": errors, "warnings": warnings}


# --- Generic lint engine (also imported and used by plateau_lint) ------------
def _open(source: Source):
    if isinstance(source, (bytes, bytearray)):
        return io.BytesIO(source)
    return str(source)


def geom_index_from_map(bmap: dict) -> dict:
    """Build a geometry-hash -> [id] index from load_buildings' {id:(attrs,geom)}."""
    index: dict = {}
    for bid, (_attrs, geom) in bmap.items():
        if geom is not None:
            index.setdefault(geom, []).append(bid)
    return index


def run_lint(
    source: Source,
    path: str,
    check_fn: CheckFn,
    only_ids: Optional[set] = None,
    geom_index: Optional[dict] = None,
) -> dict:
    """Generic engine that scans buildings, applies check_fn, and collects findings.

    With only_ids, check only those buildings (CI: changed buildings only).
    geom_index is passed through for duplicate detection etc.
    """
    buildings: list = []
    n_err = n_warn = 0
    context = safe_iterparse(_open(source), events=("end",), tag=_BUILDING_TAGS)
    for _event, b in context:
        bid = _gml_id(b)
        if bid is not None and (only_ids is None or bid in only_ids):
            res = check_fn(b, geom_index)
            if res["errors"] or res["warnings"]:
                buildings.append({"id": bid, **res})
                n_err += len(res["errors"])
                n_warn += len(res["warnings"])
        _drop(b)
    return {"file": path, "buildings": buildings, "n_errors": n_err, "n_warnings": n_warn}


def _changed_ids(old_map: dict, new_map: dict) -> set:
    """Building ids added/modified from base to head (changed ones only, so pre-existing defects do not fail unrelated PRs)."""
    from scripts.diff_citygml import attribute_diffs

    old_ids, new_ids = set(old_map), set(new_map)
    changed = set(new_ids - old_ids)
    for bid in old_ids & new_ids:
        o_attrs, o_geom = old_map[bid]
        n_attrs, n_geom = new_map[bid]
        if o_geom != n_geom or attribute_diffs(o_attrs, n_attrs):
            changed.add(bid)
    return changed


def collect_ci_files(
    repo: Path, base_sha: str, head_sha: str, gml_files: list, check_fn: CheckFn,
    check_fn_for: Optional[Callable] = None,
) -> list:
    """CI: for each changed .gml, check only the changed buildings in head (the geometry index is built over all of head).

    When check_fn_for(path) is given, use the check function it returns per file
    (for checks that depend on the file's location, such as codelist consistency).
    """
    from scripts.extract_building_preview import _get_file_at_sha

    results: list = []
    for rel in gml_files:
        old_map = load_buildings(_get_file_at_sha(repo, base_sha, rel))
        head_bytes = _get_file_at_sha(repo, head_sha, rel)
        if head_bytes is None:
            continue
        new_map = load_buildings(head_bytes)
        changed = _changed_ids(old_map, new_map)
        if not changed:
            continue
        fn = check_fn_for(Path(repo) / rel) if check_fn_for is not None else check_fn
        results.append(
            run_lint(head_bytes, rel, fn, only_ids=changed,
                     geom_index=geom_index_from_map(new_map))
        )
    return results


def lint_file(path: Path, check_fn: CheckFn = check_building) -> dict:
    """Single file: check all buildings (duplicate detection within the same file)."""
    raw = path.read_bytes()
    return run_lint(raw, str(path), check_fn, geom_index=geom_index_from_map(load_buildings(raw)))


# --- Output ------------------------------------------------------------------
_LABEL = {
    "ring_not_closed": "Unclosed ring",
    "coord_count": "Invalid coordinate count",
    "too_few_points": "Too few vertices",
    "degenerate_face": "Degenerate face",
    "self_intersection": "Surface self-intersection",
    "duplicate_geometry": "Suspected duplicate geometry",
}


def render_markdown(files: list, marker: str, title: str, label: dict = None) -> str:
    """Markdown for PR comments (with marker, errors/warnings separated)."""
    label = label or _LABEL
    n_err = sum(f["n_errors"] for f in files)
    n_warn = sum(f["n_warnings"] for f in files)
    lines = [marker, f"## {title}", ""]
    if n_err == 0 and n_warn == 0:
        lines += ["✅ No warnings.", ""]
        return "\n".join(lines).rstrip() + "\n"
    lines.append(f"**❌ {n_err} error(s) / ⚠️ {n_warn} warning(s)** (errors must be fixed and block merging).")
    lines.append("")
    for f in files:
        if not f["buildings"]:
            continue
        lines.append(f"### `{f['file']}`")
        for b in f["buildings"]:
            for e in b["errors"]:
                lines.append(f"- ❌ **{label.get(e['code'], e['code'])}** (`{b['id']}`): {e['detail']}")
            for w in b["warnings"]:
                lines.append(f"- ⚠️ {label.get(w['code'], w['code'])} (`{b['id']}`): {w['detail']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_cli(description: str):
    """Argument parser shared by citygml_lint / plateau_lint."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("files", nargs="*", type=Path, help=".gml files to check (all buildings)")
    p.add_argument("--json", action="store_true", help="output as JSON (default: Markdown)")
    p.add_argument("--repo", type=Path, default=None)
    p.add_argument("--base-sha", default=None)
    p.add_argument("--head-sha", default=None)
    p.add_argument("--file-list", type=Path, default=None, help="[CI] list of changed .gml files")
    return p


def run_main(argv, check_fn: CheckFn, marker: str, title: str, label: dict = None,
             check_fn_for: Optional[Callable] = None) -> int:
    """Main body shared by citygml_lint / plateau_lint.

    When check_fn_for(path) is given, use the check function it returns per file (default is check_fn).
    """
    p = build_cli(__doc__)
    args = p.parse_args(argv)
    if args.file_list is not None:
        if not (args.base_sha and args.head_sha):
            p.error("--base-sha and --head-sha are required when using --file-list.")
        listed = (ln.strip() for ln in args.file_list.read_text(encoding="utf-8").splitlines())
        gml_files = [p for p in listed if p.endswith(".gml")]
        files = collect_ci_files(args.repo or REPO_ROOT, args.base_sha, args.head_sha, gml_files,
                                 check_fn, check_fn_for)
    elif args.files:
        files = [lint_file(f, check_fn_for(f) if check_fn_for is not None else check_fn)
                 for f in args.files]
    else:
        p.error("Specify .gml files or --file-list (CI).")

    if args.json:
        json.dump({"files": files}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    elif files:
        sys.stdout.write(render_markdown(files, marker, title, label))
    return 1 if sum(f["n_errors"] for f in files) > 0 else 0


def main(argv: Optional[list] = None) -> int:
    return run_main(argv, check_building, CITYGML_LINT_MARKER, "🧪 CityGML data quality check (geometric structure)")


if __name__ == "__main__":
    raise SystemExit(main())
