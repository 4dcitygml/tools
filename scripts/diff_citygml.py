#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Minimal diff engine comparing CityGML(bldg) by semantic unit (building) (W1 / feature list A).

Compares two `.gml` files by building (`bldg:Building`, `gml:id` unit) and classifies as
`added / deleted / modified / unchanged`. Detects attribute diffs (leaf node path→value)
and geometry diffs (numeric-normalized sha256 set hash of `gml:posList`/`gml:pos`).

Corresponding feature list items:
- A-1 Building-unit splitting … streaming extraction of `bldg:Building` by `gml:id` unit (standard library, U13)
- A-2 Attribute extraction      … leaf node path→value, a leaf's uom / codeSpace too. Excludes geometry and appearance (app)
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
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Optional, Union

# Input source for the diff: a filename (Path/str) or the content itself (bytes).
# bytes is accepted because CI fetches the base version as bytes via `git show`.
Source = Union[Path, str, bytes, None]

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import citygml_xml  # noqa: E402

# --- Namespaces -------------------------------------------------------------
# Geometry appears in the gml namespace; textures etc. in the appearance namespace. Attribute
# extraction excludes appearance and gml's geometry, nothing else (D-c9, dialect-neutral): the
# feature's gml:name and gml:description are attributes, and so is a leaf's uom or codeSpace.
GML_NS = citygml_xml.GML
GML_ATTRIBUTES = {"name", "description"}
XML_ATTRIBUTES = ("uom", "codeSpace")

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
_local, _gml_id = citygml_xml.qname, citygml_xml.gml_id   # the XML vocabulary's own (U5)


def _extract_attributes(building) -> dict[str, str]:
    """Extract leaf-node `path -> value` pairs from a building subtree (A-2).

    - Appearance and gml geometry are excluded (gml:name / gml:description are not geometry).
    - A leaf's uom / codeSpace is kept as `path/@uom` / `path/@codeSpace`.
    - Elements with a `name` attribute (gen:*Attribute etc.) get `[@name=...]` appended to the path to distinguish them.
    - When the same path appears multiple times, a `path[i]` index distinguishes them.
    """
    collected: dict[str, list[str]] = {}

    def walk(elem, path: str) -> None:
        for child in elem:
            if not isinstance(child.tag, str):
                continue  # skip comments/PIs
            ns, name = _local(child)
            if ns in citygml_xml.APPEARANCE_NS or (ns == GML_NS and name not in GML_ATTRIBUTES):
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
                for key in XML_ATTRIBUTES:
                    if child.get(key):
                        collected.setdefault(f"{child_path}/@{key}", []).append(child.get(key))
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


def geometry_hash(building) -> Optional[str]:
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

    source is a file name (Path/str) or content (bytes); the buildings are read one at a time
    and released (citygml_xml.buildings).
    """
    for building in citygml_xml.buildings(source):
        bid = _gml_id(building)
        if bid is not None:
            yield bid, _extract_attributes(building), geometry_hash(building)


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


def buildings_at(repo, rev: str, path: str) -> dict[str, tuple[dict[str, str], Optional[str]]]:
    """load_buildings of path as committed at rev ({} when it is not there). Within an analysis
    run (CITYGML_RUN) each blob is parsed once and the gates share the result, kept in the run
    folder under the blob's id (U6: each changed file used to be parsed about 15 times a PR)."""
    from scripts.repo_git import GitError, git
    try:
        oid = git(repo, "rev-parse", f"{rev}:{path}", strip=True)
    except GitError:
        return {}
    cache = Path(os.environ["CITYGML_RUN"]) / "buildings" / f"{oid}.json" if os.environ.get("CITYGML_RUN") else None
    try:
        return {bid: tuple(value) for bid, value in json.loads(cache.read_text(encoding="utf-8")).items()}
    except (AttributeError, OSError, ValueError):
        pass
    found = _load(git(repo, "cat-file", "blob", oid, binary=True))
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    return found


def changed_ids(old: dict, new: dict) -> set[str]:
    """The ids of head's buildings that the change touched (added, modified, renamed), from
    diff_maps, so a check of the changed buildings asks the one classification (pre-existing
    defects of untouched buildings do not fail unrelated PRs)."""
    return {e["id"] for e in diff_maps(old, new)["buildings"] if e["status"] != "deleted"}


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


def signature(attrs: dict[str, str], geom: Optional[str], *extra) -> tuple:
    """A building's content as compared: attributes after numeric normalisation (A-4) and the
    geometry hash, plus what a caller adds (the gate: its appearance)."""
    return (frozenset((k, _norm_num(v)) for k, v in attrs.items()), geom, *extra)


def rename_pairs(gone: dict, new: dict) -> dict[str, str]:
    """The one rename rule (U6, D-c1): a building that left (id -> signature) and one that came
    with an equal signature are one building whose id changed, when the pair is unambiguous
    (exactly one each; identical twins stay a deletion and an addition). -> {new id: old id}."""
    by_gone: dict[tuple, list[str]] = {}
    by_new: dict[tuple, list[str]] = {}
    for bid, sig in gone.items():
        by_gone.setdefault(sig, []).append(bid)
    for bid, sig in new.items():
        by_new.setdefault(sig, []).append(bid)
    return {by_new[sig][0]: ids[0] for sig, ids in by_gone.items() if len(ids) == 1 and len(by_new.get(sig, ())) == 1}


def diff_maps(old: dict, new: dict, include_unchanged: bool = False) -> dict:
    """The one classification of two buildings maps (gml:id -> (attributes, geometry hash)):
    added / deleted / modified / unchanged, and renamed by rename_pairs (A-5)."""
    renames = rename_pairs({d: signature(*old[d]) for d in set(old) - set(new)},
                           {a: signature(*new[a]) for a in set(new) - set(old)})
    renamed_old = set(renames.values())
    buildings: list[dict] = []
    counts = {"added": 0, "deleted": 0, "modified": 0, "unchanged": 0, "renamed": 0}
    for bid in sorted(set(old) | set(new)):
        if bid not in new:
            if bid in renamed_old:
                continue  # the old side of a rename is folded into the new side's single entry
            entry = {"id": bid, "status": "deleted"}
        elif bid not in old:
            entry = ({"id": bid, "old_id": renames[bid], "status": "renamed"} if bid in renames
                     else {"id": bid, "status": "added"})
        else:
            attr_diffs = _attribute_diffs(old[bid][0], new[bid][0])
            geom_changed = old[bid][1] != new[bid][1]
            entry = {"id": bid, "status": "modified" if (attr_diffs or geom_changed) else "unchanged",
                     "attribute_diffs": attr_diffs, "geometry_changed": geom_changed}
        counts[entry["status"]] += 1
        if entry["status"] != "unchanged" or include_unchanged:
            buildings.append(entry)
    return {"summary": counts, "buildings": buildings}


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
    return {"old": old_label, "new": new_label, **diff_maps(old, new, include_unchanged)}


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
