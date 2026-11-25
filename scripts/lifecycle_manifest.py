#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Validate one lifecycle event. Factual relationships still require city approval."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import provenance_manifest  # noqa: E402


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "provenance" / "lifecycle.schema.json"


def validate(data: object) -> list[str]:
    """The event's violations: its shape by the schema (schemas/provenance/lifecycle.schema.json,
    the bulk manifests' validator; W6: the checks were written out here), then what a schema
    cannot say: no ID twice, the IDs a split or merge needs, real dates in their order."""
    errors = provenance_manifest.validate(data, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    if errors:
        return errors
    for key in ("oldIds", "newIds"):
        if len(set(data[key])) != len(data[key]):
            errors.append(f"{key} contains duplicate IDs.")
    if data["kind"] == "split" and not (len(data["oldIds"]) == 1 and len(data["newIds"]) >= 2):
        errors.append("split requires one old ID and at least two new IDs.")
    if data["kind"] == "merge" and not (len(data["oldIds"]) >= 2 and len(data["newIds"]) == 1):
        errors.append("merge requires at least two old IDs and one new ID.")
    for key in ("confirmedOn", "occurredOn"):
        try:
            if key in data:
                date.fromisoformat(data[key])
        except ValueError:
            errors.append(f"{key} must be a real date in YYYY-MM-DD form.")
    if not errors and data.get("occurredOn", "") > data["confirmedOn"]:
        errors.append("occurredOn must not be later than confirmedOn.")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path, help='provenance/lifecycle/<event>.json, relative to the city repository')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'provenance/lifecycle/[A-Za-z0-9][A-Za-z0-9._-]*\.json', args.manifest.as_posix()):
        print('Use provenance/lifecycle/<event>.json relative to the city repository.')
        return 1
    try:
        raw = args.manifest.read_bytes()
        errors = validate(json.loads(raw))
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
    if errors:
        print('\n'.join(errors))
        return 1
    print(f'Lifecycle-Manifest: {provenance_manifest.manifest_ref(args.manifest.as_posix(), raw)}')   # the one reference form
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
