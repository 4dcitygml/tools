#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Code-list crosswalk between two i-UR editions (semantics/codelists/<from>__<to>.json).

For every coded attribute of the semantic registry, the code list file of each
edition is derived from the attribute's path (container type + leaf, LoD suffix
dropped: ``.../DataQualityAttribute/geometrySrcDescLod0`` ->
``DataQualityAttribute_geometrySrcDesc.xml``; CityGML core leaves ->
``Building_<leaf>.xml``). Codes are matched by label:

  exact    the same label exists once in the new list (or the same code with the same label)
  refined  the old label is contained in several / a differently qualified new label (1:n)
  dropped  no new code carries the label
  added    new codes without an old counterpart (informational)

Machine-generated entries carry confidence "machine"; a reviewed overrides file
(same shape) is merged on top with confidence "reviewed". The carry-forward
tool uses only 1:1 relations (exact, or reviewed refined/merged with a single
target); everything else keeps the old code with the old edition's codeSpace.

Usage:
    python3 scripts/codelist_crosswalk.py generate --from-edition iur-3.0 --from-dir <codelists dir> \\
        --to-edition iur-3.1 --to-dir <codelists dir> [--reviewed overrides.json] --output semantics/codelists/iur-3.0__iur-3.1.json
    python3 scripts/codelist_crosswalk.py report semantics/codelists/iur-3.0__iur-3.1.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import semantic_registry as R  # noqa: E402

_ENTRY_RE = re.compile(r"<gml:description>([^<]*)</gml:description>\s*<gml:name>([^<]+)</gml:name>")
_LOD_RE = re.compile(r"Lod\d$")
_NORMALIZE_RE = re.compile(r"[（）()\s]|の測量成果|測量成果")


def load_codelist(path: Path) -> dict[str, str]:
    """gml:Dictionary -> {code: label}."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return {code.strip(): label.strip() for label, code in _ENTRY_RE.findall(text)}


def codelist_file_candidates(key: str, edition: str) -> list[str]:
    """Candidate code list file names for a coded attribute in an edition, most
    specific first: the registry's list name, then names derived from the path
    (container type + leaf without LoD suffix, with/without the ``Building``
    prefix PLATEAU packages use inconsistently, ``Common_``/``Building_`` forms)."""
    attr = R.load_registry()["attributes"].get(key)
    if not attr or not attr.get("codelist"):
        return []
    path = attr["paths"].get(edition)
    if not path:
        return []
    segments = path.strip("/").split("/")
    leaf = _LOD_RE.sub("", segments[-1])
    container = segments[-2] if len(segments) >= 2 else "Building"
    # the data's codeSpace points at the container-derived list, so that comes first
    names = [f"{container}_{leaf}.xml", attr["codelist"] + ".xml"]
    if container.startswith("Building") and container != "Building":
        names.append(f"{container[len('Building'):]}_{leaf}.xml")
    else:
        names.append(f"Building{container}_{leaf}.xml")
    names += [f"Common_{leaf}.xml", f"Building_{leaf}.xml"]
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def codelist_file_for(key: str, edition: str, directory: Path | None = None) -> str | None:
    """The code list file an edition uses for a coded attribute (first candidate that exists in ``directory``)."""
    candidates = codelist_file_candidates(key, edition)
    if directory is None:
        return candidates[0] if candidates else None
    for name in candidates:
        if (directory / name).is_file():
            return name
    return None


def _edition_key(key: str, edition: str) -> str | None:
    """The key to read in ``edition``: the key itself, else its predecessor/first successor (split attributes)."""
    attrs = R.load_registry()["attributes"]
    if edition in attrs[key]["paths"]:
        return key
    for other in attrs[key].get("successors", []) + [attrs[key].get("predecessor")]:
        if other and edition in attrs[other]["paths"]:
            return other
    return None


def _norm(label: str) -> str:
    return _NORMALIZE_RE.sub("", label)


def match_codes(old: dict[str, str], new: dict[str, str]) -> dict[str, dict]:
    by_label: dict[str, list[str]] = {}
    for code, label in new.items():
        by_label.setdefault(label, []).append(code)
    out: dict[str, dict] = {}
    relabel_only = set(old) == set(new)  # identical code set: the edition only reworded labels
    for code, label in old.items():
        exact = by_label.get(label, [])
        same_code = new.get(code)
        if relabel_only:
            out[code] = {"label": label, "to": [code], "relation": "exact", "confidence": "machine",
                         **({"note": f"relabeled: {same_code}"} if same_code != label else {})}
            continue
        if same_code == label:
            out[code] = {"label": label, "to": [code], "relation": "exact", "confidence": "machine"}
        elif same_code is not None and _norm(label) and (_norm(label) in _norm(same_code) or _norm(same_code) in _norm(label)):
            # same code, reworded label (an edition that only relabels its list)
            out[code] = {"label": label, "to": [code], "relation": "exact", "confidence": "machine", "note": f"relabeled: {same_code}"}
        elif len(exact) == 1:
            out[code] = {"label": label, "to": exact, "relation": "exact", "confidence": "machine"}
        else:
            key = _norm(label)
            fuzzy = [c for c, l in new.items() if key and key in _norm(l)] if not exact else exact
            if same_code is not None and code not in fuzzy:
                fuzzy.append(code)  # the same code with an unrelated label is still a candidate a reviewer must see
            if fuzzy:
                out[code] = {"label": label, "to": sorted(fuzzy), "relation": "refined", "confidence": "machine",
                             "candidates": {c: new[c] for c in sorted(fuzzy)}}
            else:
                out[code] = {"label": label, "to": [], "relation": "dropped", "confidence": "machine"}
    matched = {c for entry in out.values() for c in entry["to"]}
    added = {c: l for c, l in new.items() if c not in matched}
    return {"codes": out, "added": added}


def build(from_edition: str, from_dir: Path, to_edition: str, to_dir: Path, reviewed: dict | None) -> dict:
    lists: dict[str, dict] = {}
    for key, attr in R.load_registry()["attributes"].items():
        if not attr.get("codelist") or attr.get("family") == "source_identity":
            continue  # identity codes (municipality etc.) are never carried forward as values
        k_old, k_new = _edition_key(key, from_edition), _edition_key(key, to_edition)
        if not k_old or not k_new or k_old != key:
            continue  # handled under the key that exists in the source edition
        f_old, f_new = codelist_file_for(k_old, from_edition, from_dir), codelist_file_for(k_new, to_edition, to_dir)
        if not f_old or not f_new:
            lists[key] = {"status": "missing", "missing": [n for n, f in (("from", f_old), ("to", f_new)) if not f],
                          "candidates": {"from": codelist_file_candidates(k_old, from_edition), "to": codelist_file_candidates(k_new, to_edition)}}
            continue
        p_old, p_new = from_dir / f_old, to_dir / f_new
        old, new = load_codelist(p_old), load_codelist(p_new)
        entry = {"from_file": f_old, "to_file": f_new, "status": "ok",
                 "from_sha256": hashlib.sha256(p_old.read_bytes()).hexdigest(), "to_sha256": hashlib.sha256(p_new.read_bytes()).hexdigest(),
                 "from_count": len(old), "to_count": len(new), **match_codes(old, new)}
        lists[key] = entry
    result = {"schemaVersion": 1, "from": from_edition, "to": to_edition, "lists": lists}
    if reviewed:
        for key, overrides in reviewed.get("lists", {}).items():
            target = result["lists"].setdefault(key, {"status": "reviewed-only", "codes": {}, "added": {}})
            for code, entry in overrides.get("codes", {}).items():
                target.setdefault("codes", {})[code] = {**entry, "confidence": "reviewed"}
    # summary
    counts = {"exact": 0, "refined": 0, "dropped": 0, "reviewed": 0, "lists": 0, "missing": 0}
    for entry in lists.values():
        if entry.get("status") == "missing":
            counts["missing"] += 1
            continue
        counts["lists"] += 1
        for c in entry.get("codes", {}).values():
            counts[c["relation"] if c["relation"] in counts else "exact"] += 1
            if c.get("confidence") == "reviewed":
                counts["reviewed"] += 1
    result["summary"] = counts
    return result


def resolve(crosswalk: dict, key: str, code: str) -> str | None:
    """The single new-edition code for an old code, or None when not 1:1 (keep the old codeSpace)."""
    entry = crosswalk.get("lists", {}).get(key, {}).get("codes", {}).get(code)
    if not entry:
        return None
    if entry["relation"] == "exact" or (entry.get("confidence") == "reviewed" and len(entry.get("to", [])) == 1):
        return entry["to"][0] if entry.get("to") else None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--from-edition", required=True); g.add_argument("--from-dir", required=True, type=Path)
    g.add_argument("--to-edition", required=True); g.add_argument("--to-dir", required=True, type=Path)
    g.add_argument("--reviewed", type=Path); g.add_argument("--output", required=True, type=Path)
    r = sub.add_parser("report"); r.add_argument("crosswalk", type=Path)
    args = parser.parse_args(argv)
    if args.command == "generate":
        reviewed = json.loads(args.reviewed.read_text(encoding="utf-8")) if args.reviewed else None
        result = build(args.from_edition, args.from_dir, args.to_edition, args.to_dir, reviewed)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), **result["summary"]}, ensure_ascii=False))
    else:
        cw = json.loads(args.crosswalk.read_text(encoding="utf-8"))
        for key, entry in sorted(cw["lists"].items()):
            if entry.get("status") == "missing":
                print(f"{key}: MISSING {entry['missing']}"); continue
            rel = {}
            for c in entry.get("codes", {}).values():
                rel[c["relation"]] = rel.get(c["relation"], 0) + 1
            print(f"{key:40s} {entry['from_file']} ({entry['from_count']}) -> {entry['to_file']} ({entry['to_count']}): {rel} added={len(entry.get('added', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
