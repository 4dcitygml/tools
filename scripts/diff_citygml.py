#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Minimal diff engine comparing CityGML(bldg) by semantic unit (building) (W1 / feature list A).

Compares two `.gml` files by building (`bldg:Building`, `gml:id` unit) and classifies as
`added / deleted / modified / unchanged`. Detects attribute diffs (leaf node path→value)
and geometry diffs (numeric-normalized sha256 set hash of `gml:posList`/`gml:pos`).

Corresponding feature list items:
- A-1 Building-unit splitting … lxml streaming extraction of `bldg:Building` by `gml:id` unit
- A-2 Attribute extraction      … leaf node path→value. Excludes geometry (gml) and appearance (app)
- A-3 Geometry hashing          … numeric-normalized `gml:posList`/`gml:pos` → sha256 set comparison (match/mismatch only)
- A-4 Numeric normalization     … absorbs decimal format differences in attribute values (trailing 0s, notational diffs) (basic only)
- A-5 Change classification     … added / deleted / modified / unchanged
- A-6 Bulk diff                 … 2-file comparison, single-ID filtering

Not included (Tier2): displacement/shape similarity judgment, incoming traversal, full auto year-to-year extraction, advanced semantic classification.

Usage:
    python scripts/diff_citygml.py OLD.gml NEW.gml [--id GMLID] [--include-unchanged]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Optional, Union

# Input source for the diff: a filename (Path/str) or the content itself (bytes).
# bytes is accepted because CI fetches the base version as bytes via `git show`.
Source = Union[Path, str, bytes, None]

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.safe_xml import safe_iterparse  # noqa: E402

# --- Namespaces -------------------------------------------------------------
# Geometry appears in the gml namespace; textures etc. in the appearance namespace.
# Attribute extraction excludes elements in these two namespaces, i.e. "excludes geometry and appearance".
GML_NS = "http://www.opengis.net/gml"
APP_NS = "http://www.opengis.net/citygml/appearance/2.0"
EXCLUDED_NS = (GML_NS, APP_NS)

# Tag set so iterparse fires only on Building elements (CityGML 1.0/2.0).
_BUILDING_TAGS = (
    "{http://www.opengis.net/citygml/building/2.0}Building",
    "{http://www.opengis.net/citygml/building/1.0}Building",
)

# Elements carrying geometry coordinates (within the gml namespace).
_GEOM_LEAVES = {"posList", "pos"}


# --- Numeric normalization (A-3 / A-4) --------------------------------------
def _norm_num(token: str) -> str:
    """Normalize a numeric string to canonical form. Return it unchanged if not numeric.

    Absorbs trailing zeros and decimal notation differences (`13.80` vs `13.8`,
    `9` vs `9.0`), and settles on fixed-point notation without exponents.
    """
    try:
        value = Decimal(token)
    except (InvalidOperation, ValueError):
        return token
    # normalize() drops trailing zeros; format 'f' avoids exponent notation.
    return format(value.normalize(), "f")


def _values_equal(old: Optional[str], new: Optional[str]) -> bool:
    """Compare attribute values. If both are numeric, compare after numeric normalization (A-4)."""
    if old is None or new is None:
        return old == new
    if old == new:
        return True
    return _norm_num(old) == _norm_num(new)


# --- Extraction (A-1 / A-2 / A-3) -------------------------------------------
def _local(elem: etree._Element) -> tuple[str, str]:
    """Return the element's (namespace, localname)."""
    q = etree.QName(elem)
    return q.namespace or "", q.localname


def _gml_id(elem: etree._Element) -> Optional[str]:
    """Get the element's gml:id regardless of gml namespace version differences."""
    for key, val in elem.attrib.items():
        if etree.QName(key).localname == "id":
            return val
    return None


def _extract_attributes(building: etree._Element) -> dict[str, str]:
    """Extract leaf-node `path -> value` pairs from a building subtree (A-2).

    - Elements in the gml (geometry) / appearance namespaces are excluded.
    - Elements with a `name` attribute (gen:*Attribute etc.) get `[@name=...]` appended to the path to distinguish them.
    - When the same path appears multiple times, a `path[i]` index distinguishes them.
    """
    collected: dict[str, list[str]] = {}

    def walk(elem: etree._Element, path: str) -> None:
        for child in elem:
            if not isinstance(child.tag, str):
                continue  # skip comments/PIs
            ns, name = _local(child)
            if ns in EXCLUDED_NS:
                continue  # exclude geometry/appearance
            seg = name
            name_attr = child.get("name")
            if name_attr:
                seg = f"{name}[@name={name_attr}]"
            child_path = f"{path}/{seg}"
            if len(child) == 0:
                text = (child.text or "").strip()
                if text:
                    collected.setdefault(child_path, []).append(text)
            else:
                walk(child, child_path)

    walk(building, "")

    result: dict[str, str] = {}
    for path, values in collected.items():
        if len(values) == 1:
            result[path] = values[0]
        else:
            for i, value in enumerate(values):
                result[f"{path}[{i}]"] = value
    return result


def _geometry_hash(building: etree._Element) -> Optional[str]:
    """Fold a building's geometry into a single sha256 hash (A-3).

    Numerically normalize the coordinates of all `gml:posList`/`gml:pos`,
    then sha256 them as a sorted set so the result is independent of face order.
    Returns None when there is no geometry.
    """
    faces: list[str] = []
    for elem in building.iter():
        if not isinstance(elem.tag, str):
            continue
        ns, name = _local(elem)
        if ns == GML_NS and name in _GEOM_LEAVES:
            tokens = (elem.text or "").split()
            if tokens:
                faces.append(" ".join(_norm_num(t) for t in tokens))
    if not faces:
        return None
    faces.sort()
    digest = hashlib.sha256("\n".join(faces).encode("utf-8"))
    return digest.hexdigest()


def iter_buildings(source: Source) -> Iterator[tuple[str, dict[str, str], Optional[str]]]:
    """Scan a source in streaming mode, yielding (gml:id, attribute dict, geometry hash) (A-1).

    source is a file name (Path/str) or content (bytes). After each Building
    element is processed, its subtree and the processed cityObjectMember are
    released to keep memory usage bounded.
    """
    if isinstance(source, (bytes, bytearray)):
        target: object = io.BytesIO(source)
    else:
        target = str(source)
    context = safe_iterparse(target, events=("end",), tag=_BUILDING_TAGS)
    for _event, building in context:
        bid = _gml_id(building)
        if bid is None:
            _drop(building)
            continue
        attrs = _extract_attributes(building)
        geom = _geometry_hash(building)
        yield bid, attrs, geom
        _drop(building)


def _drop(building: etree._Element) -> None:
    """Release the processed Building and the siblings preceding its cityObjectMember."""
    building.clear()
    parent = building.getparent()  # normally core:cityObjectMember
    if parent is None:
        return
    parent.clear()
    grand = parent.getparent()
    if grand is not None:
        while parent.getprevious() is not None:
            del grand[0]


def _load(source: Source) -> dict[str, tuple[dict[str, str], Optional[str]]]:
    """Load a source and return a dict of gml:id -> (attributes, geometry hash).

    None (meaning `git show` returned nothing = the file does not exist in that
    revision) and nonexistent paths yield an empty dict (handles file adds/deletes).
    """
    if source is None:
        return {}
    if not isinstance(source, (bytes, bytearray)):
        path = Path(source)
        if not path.exists():
            return {}
        source = path
    result: dict[str, tuple[dict[str, str], Optional[str]]] = {}
    for bid, attrs, geom in iter_buildings(source):
        result[bid] = (attrs, geom)
    return result


# --- Diff / classification (A-5 / A-6) --------------------------------------
def load_buildings(source: Source) -> dict[str, tuple[dict[str, str], Optional[str]]]:
    """Load a source and return gml:id -> (attributes, geometry hash) (public API for reuse by W3 etc.)."""
    return _load(source)


def attribute_diffs(
    old_attrs: dict[str, str], new_attrs: dict[str, str]
) -> list[dict[str, Optional[str]]]:
    """Public API for attribute diffs (used by W3 for attribute-equality checks)."""
    return _attribute_diffs(old_attrs, new_attrs)


def _attribute_diffs(
    old_attrs: dict[str, str], new_attrs: dict[str, str]
) -> list[dict[str, Optional[str]]]:
    """Return the list of attribute diffs (values equal after numeric normalization are not diffs)."""
    diffs: list[dict[str, Optional[str]]] = []
    for key in sorted(set(old_attrs) | set(new_attrs)):
        old_val = old_attrs.get(key)
        new_val = new_attrs.get(key)
        if not _values_equal(old_val, new_val):
            diffs.append({"path": key, "old": old_val, "new": new_val})
    return diffs


def diff_sources(
    old_source: Source,
    new_source: Source,
    old_label: str,
    new_label: str,
    only_id: Optional[str] = None,
    include_unchanged: bool = False,
) -> dict:
    """Compare two arbitrary sources (file name/bytes/None) (the general form of A-6).

    In CI, pass the base revision as `git show` bytes and the head revision as
    the working-tree file. old_label/new_label become "old"/"new" in the output
    (identifiers such as relative paths).
    """
    old = _load(old_source)
    new = _load(new_source)

    if only_id is not None:
        old = {k: v for k, v in old.items() if k == only_id}
        new = {k: v for k, v in new.items() if k == only_id}

    # Rename detection (content-based, like git diff -M): a deleted<->added pair whose
    # content (attributes + geometry) matches 1:1 is treated as a "rename (id changed,
    # content unchanged)". This reduces reliance on gml:id, while a rebuild (content
    # changes substantially) does not match and correctly remains a separate building (lifecycle).
    deleted_ids = set(old) - set(new)
    added_ids = set(new) - set(old)
    renames = _detect_renames(old, new, deleted_ids, added_ids)  # {new_id: old_id}
    renamed_old = set(renames.values())
    renamed_new = set(renames)

    buildings: list[dict] = []
    counts = {"added": 0, "deleted": 0, "modified": 0, "unchanged": 0, "renamed": 0}

    for bid in sorted(set(old) | set(new)):
        in_old = bid in old
        in_new = bid in new
        if in_old and not in_new:
            if bid in renamed_old:
                continue  # the old side of a rename is folded into the new side's single entry
            status = "deleted"
            entry = {"id": bid, "status": status}
        elif in_new and not in_old:
            if bid in renamed_new:
                status = "renamed"  # old_id -> bid (same content, only id changed)
                entry = {"id": bid, "old_id": renames[bid], "status": status}
            else:
                status = "added"
                entry = {"id": bid, "status": status}
        else:
            old_attrs, old_geom = old[bid]
            new_attrs, new_geom = new[bid]
            attr_diffs = _attribute_diffs(old_attrs, new_attrs)
            geom_changed = old_geom != new_geom
            status = "modified" if (attr_diffs or geom_changed) else "unchanged"
            entry = {
                "id": bid,
                "status": status,
                "attribute_diffs": attr_diffs,
                "geometry_changed": geom_changed,
            }
        counts[status] += 1
        if status == "unchanged" and not include_unchanged:
            continue
        buildings.append(entry)

    return {
        "old": old_label,
        "new": new_label,
        "summary": counts,
        "buildings": buildings,
    }


def _detect_renames(
    old: dict[str, tuple[dict[str, str], Optional[str]]],
    new: dict[str, tuple[dict[str, str], Optional[str]]],
    deleted_ids: set[str],
    added_ids: set[str],
) -> dict[str, str]:
    """Treat deleted<->added 1:1 pairs with matching content (attributes + geometry hash) as renames.

    Returns {new_id: old_id}. Ambiguous pairs (multiple entries with identical
    content) are not treated as renames (err on the safe side). gml:id is the
    building's key (outside the attributes), so identical attributes + geometry
    means "only the id changed".
    """
    def sig(store: dict, bid: str) -> tuple:
        attrs, geom = store[bid]
        return (frozenset(attrs.items()), geom)

    del_by_sig: dict[tuple, list[str]] = {}
    add_by_sig: dict[tuple, list[str]] = {}
    for d in deleted_ids:
        del_by_sig.setdefault(sig(old, d), []).append(d)
    for a in added_ids:
        add_by_sig.setdefault(sig(new, a), []).append(a)

    renames: dict[str, str] = {}
    for s, ds in del_by_sig.items():
        adds = add_by_sig.get(s, [])
        if len(ds) == 1 and len(adds) == 1:  # only unambiguous 1:1 pairs
            renames[adds[0]] = ds[0]
    return renames


def diff_files(
    old_path: Path,
    new_path: Path,
    only_id: Optional[str] = None,
    include_unchanged: bool = False,
) -> dict:
    """Compare two files and return per-building changes as structured data (A-6).

    When only_id is given, restrict to that gml:id only (single-ID filtering).
    """
    return diff_sources(
        old_path,
        new_path,
        str(old_path),
        str(new_path),
        only_id=only_id,
        include_unchanged=include_unchanged,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_gml", type=Path, help="old .gml")
    parser.add_argument("new_gml", type=Path, help="new .gml")
    parser.add_argument("--id", dest="only_id", default=None, help="filter to this gml:id only")
    parser.add_argument(
        "--include-unchanged",
        action="store_true",
        help="include unchanged buildings in output",
    )
    args = parser.parse_args(argv)

    result = diff_files(
        args.old_gml,
        args.new_gml,
        only_id=args.only_id,
        include_unchanged=args.include_unchanged,
    )
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
