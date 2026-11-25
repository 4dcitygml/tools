#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""CityGML validity check (validate / feature list C).

Two-stage validation:
1. well-formed check (catches unclosed tags etc. Fast and reliable)
2. XSD schema validation (**xmlschema**. CityGML 2.0 and 1.0 + GML 3.1.1 + the bundled ADE schemas)

Why we use xmlschema as the XSD engine:
- libxml2 (lxml) XSD validation has a known limitation of **false-rejecting** ADE data
  (a bug that skips numerous optional elements and cannot transition to the final substitutionGroup ADE hook.
  Manifested when buildings have only lod1, etc. Confirmed in actual data validation).
- xmlschema (pure Python) correctly validates the same data and detects real errors such as type violations.

Schemas are bundled with the repository and resolved offline (network-independent):
- `schemas/<host>/` (a mirror per schema host: one folder each, named by the host)
- `schemas/master.xsd` imports all namespaces (CityGML 2.0 and 1.0 modules, the ADEs'). http(s) references are mapped to local by uri_mapper.

A profile's own quality checker (a desktop tool, post-hoc) is not included.

Usage:
    python scripts/validate_citygml.py FILE.gml [FILE2.gml ...]
    python scripts/validate_citygml.py --file-list changed_gml.txt   # CI mode
Exit code: all valid=0 / any invalid=1.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.md_report import changed_list  # noqa: E402
from scripts import safe_xml  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_CACHE = REPO_ROOT / "schemas"
MASTER = SCHEMA_CACHE / "master.xsd"
# The schema hosts mirrored locally: one folder per host under schemas/ (the mirror says which)
_MIRRORED_HOSTS = frozenset(p.name for p in SCHEMA_CACHE.iterdir() if p.is_dir() and "." in p.name) if SCHEMA_CACHE.is_dir() else frozenset()


def _uri_mapper(url: str) -> str:
    """Resolve http(s) references inside schemas to the repo-bundled mirror (no network needed)."""
    s = urlsplit(url)
    if s.scheme in ("http", "https") and s.netloc in _MIRRORED_HOSTS:
        local = SCHEMA_CACHE / s.netloc / s.path.lstrip("/")
        if local.exists():
            return local.as_uri()
    return url


_schema = None  # xmlschema.XMLSchema (built on first use only)


def load_schema():
    """Compile master.xsd with local resolution (first call only; cached afterwards).

    Schema construction uses validation='lax' to tolerate a known quirk
    (illegal restriction) in the GML 3.1.1 schemas. Instance validation
    itself remains strict.
    """
    global _schema
    if _schema is None:
        import xmlschema  # lazy import (dependency needed only when validate runs)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _schema = xmlschema.XMLSchema(
                str(MASTER), validation="lax", uri_mapper=_uri_mapper
            )
    return _schema


def validate_file(path: Path) -> tuple[bool, list[str]]:
    """Validate one file and return (ok, error list). Well-formedness failures are also reported."""
    # 1) well-formed check (the standard library; a DOCTYPE is refused, U13)
    try:
        safe_xml.parse(path)
    except safe_xml.ParseError as e:
        return False, [f"not well-formed: {e}"]
    # 2) XSD validation (xmlschema)
    schema = load_schema()
    errors: list[str] = []
    for err in schema.iter_errors(str(path)):
        line = getattr(err, "sourceline", None) or getattr(getattr(err, "elem", None), "sourceline", None)
        reason = getattr(err, "reason", None) or getattr(err, "message", None) or str(err)
        prefix = f"line {line}: " if line else ""
        errors.append((prefix + str(reason))[:200])
        if len(errors) >= 20:
            break
    return (not errors), errors


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("files", nargs="*", type=Path, help=".gml files to validate")
    p.add_argument("--file-list", type=Path, default=None, help="[CI] list of changed .gml files")
    args = p.parse_args(argv)

    files = list(args.files)
    if args.file_list is not None:
        files += [Path(p) for p in changed_list(args.file_list)]
    if not files:
        p.error("Specify the .gml files to validate (arguments or --file-list).")

    all_ok = True
    report: dict = {}
    for f in files:
        ok, errors = validate_file(f)
        report[str(f)] = {"valid": ok, "errors": errors[:20]}
        if not ok:
            all_ok = False
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    from scripts.gate_result import guarded
    raise SystemExit(guarded(main))
