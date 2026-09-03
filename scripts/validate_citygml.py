#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""CityGML / PLATEAU validity check (validate / feature list C).

Two-stage validation:
1. well-formed check (lxml; catches unclosed tags etc. Fast and reliable)
2. XSD schema validation (**xmlschema**. CityGML 2.0 + GML 3.1.1 + i-UR 2.0/3.0/3.1/3.2)

Why we use xmlschema as the XSD engine:
- libxml2 (lxml) XSD validation has a known limitation of **false-rejecting** i-UR ADE data
  (a bug that skips numerous optional elements and cannot transition to the final substitutionGroup ADE hook.
  Manifested when buildings have only lod1, etc. Confirmed in actual data validation).
- xmlschema (pure Python) correctly validates the same data and detects real errors such as type violations.

Schemas are bundled with the repository and resolved offline (network-independent):
- `schemas/` (mirror of `schemas.opengis.net`/`www.w3.org`/`docs.oasis-open.org`/`www.geospatial.jp`)
- `schemas/master.xsd` imports all namespaces (including i-UR 2.0/3.0/3.1/3.2). http(s) references are mapped to local by uri_mapper.

Quality checks by PLATEAU-Builder (C-1~3) are not included (Windows/Java dependent, post-hoc).

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

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.safe_xml import safe_parse  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_CACHE = REPO_ROOT / "schemas"
MASTER = SCHEMA_CACHE / "master.xsd"
# Schema distribution hosts mirrored locally (i-UR is at www.geospatial.jp)
_MIRRORED_HOSTS = {
    "schemas.opengis.net",
    "www.w3.org",
    "docs.oasis-open.org",
    "www.geospatial.jp",
}


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
    # 1) well-formed check (lxml; fast and reliable)
    try:
        safe_parse(str(path))
    except etree.XMLSyntaxError as e:
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
        files += [
            Path(line.strip())
            for line in args.file_list.read_text(encoding="utf-8").splitlines()
            if line.strip().endswith(".gml")
        ]
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
    raise SystemExit(main())
