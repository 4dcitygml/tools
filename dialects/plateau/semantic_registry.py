#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Semantic attribute registry (dialects/plateau/semantics/registry.json): edition-independent
keys for building attributes and their concrete paths per i-UR edition.

The registry is what makes the repository's accumulated changes survive an
edition change: a change is recorded against a semantic key, and the key is
resolved to a path in whatever edition the data is serialized in
(docs/semantic-registry.md).

Usage:
    python3 dialects/plateau/semantic_registry.py crosswalk iur-2.0 iur-3.2
    python3 dialects/plateau/semantic_registry.py lookup iur-3.1 /storeysAboveGround
    python3 dialects/plateau/semantic_registry.py edition FILE.gml
"""
from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = Path(__file__).resolve().parent / "semantics" / "registry.json"
_INDEX_RE = re.compile(r"\[\d+\]")
_NAME_RE = re.compile(r"\[@name=[^\]]*\]")
_XMLNS_RE = re.compile(rb"xmlns(?::[\w.-]+)?\s*=\s*([\"'])([^\"']+)\1")   # any prefix, either quote (D-4b3)


@lru_cache(maxsize=1)
def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def editions() -> list[str]:
    return list(load_registry()["editions"])


def normalize_path(path: str) -> str:
    """extract_attributes path -> registry path (drop repeat indices, fold the generic attribute name)."""
    return _NAME_RE.sub("[@name]", _INDEX_RE.sub("", path))


def edition_from_namespace(uri: str) -> str | None:
    for name, meta in load_registry()["editions"].items():
        if meta["uro"] == uri.rstrip("/"):
            return name
    return None


def detect_edition(raw: bytes) -> str | None:
    """Edition of a CityGML file: the registered edition whose i-UR namespace the file declares
    in its first 64 KiB, under any prefix and in either quote (D-4b3: `xmlns:uro="..."` alone was
    read, so another prefix or single quotes gave no edition and carry-forward fell back silently).
    The one sniffer: the pack's other tools ask it."""
    for m in _XMLNS_RE.finditer(raw[:65536]):
        found = edition_from_namespace(m.group(2).decode("utf-8", errors="replace"))
        if found:
            return found
    return None


def namespace_of(edition: str) -> str:
    """The i-UR namespace URI of a registered edition."""
    return load_registry()["editions"][edition]["uro"]


def path_for(key: str, edition: str) -> str | None:
    attr = load_registry()["attributes"].get(key)
    return attr["paths"].get(edition) if attr else None


@lru_cache(maxsize=None)
def _reverse(edition: str) -> dict[str, str]:
    table: dict[str, str] = {}
    for key, attr in load_registry()["attributes"].items():
        path = attr["paths"].get(edition)
        if path:
            table[path] = key
        for alias in attr.get("aliases", {}).get(edition, []):
            table[alias] = key
    return table


def key_for(path: str, edition: str) -> str | None:
    return _reverse(edition).get(normalize_path(path))


def attribute_key(path: str, edition: str) -> str:
    """The pack's answer to Pack.attribute_key: the registry key, else the normalised path."""
    return key_for(path, edition) or normalize_path(path)


def family_of(key: str) -> str | None:
    attr = load_registry()["attributes"].get(key)
    return attr["family"] if attr else None


def attributes_for(edition: str) -> dict[str, str]:
    """key -> path for every attribute that exists in the edition."""
    return {key: attr["paths"][edition] for key, attr in load_registry()["attributes"].items() if edition in attr["paths"]}


def crosswalk(edition_a: str, edition_b: str) -> list[dict]:
    """Per key: the path in each edition (None when absent) and the relation."""
    rows = []
    for key, attr in load_registry()["attributes"].items():
        a, b = attr["paths"].get(edition_a), attr["paths"].get(edition_b)
        if a is None and b is None:
            continue
        if a and b:
            relation = "same" if a == b else "renamed"
        elif a:
            relation = "removed" + (" (split: " + ", ".join(attr["successors"]) + ")" if attr.get("successors") else "")
        else:
            relation = "added" + (f" (from {attr['predecessor']})" if attr.get("predecessor") else "")
        rows.append({"key": key, "family": attr["family"], edition_a: a, edition_b: b, "relation": relation})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("crosswalk"); c.add_argument("edition_a"); c.add_argument("edition_b")
    lk = sub.add_parser("lookup"); lk.add_argument("edition"); lk.add_argument("path")
    ed = sub.add_parser("edition"); ed.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    if args.command == "crosswalk":
        for row in crosswalk(args.edition_a, args.edition_b):
            print(f"{row['key']:40s} {row['relation']:24s} {row[args.edition_a] or '-'}  ->  {row[args.edition_b] or '-'}")
    elif args.command == "lookup":
        key = key_for(args.path, args.edition)
        print(json.dumps({"edition": args.edition, "path": args.path, "key": key, "family": family_of(key) if key else None,
                          "paths": load_registry()["attributes"][key]["paths"] if key else None}, ensure_ascii=False, indent=1))
    else:
        print(detect_edition(args.file.read_bytes()) or "unknown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
