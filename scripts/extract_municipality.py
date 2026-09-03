#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Remove building blocks of municipalities other than the specified one from CityGML building GML.

Preserves target ``core:cityObjectMember`` and other byte strings unchanged,
deleting only members of non-target buildings. Same input and municipality code
always yields the same output byte string.

Example:
    python scripts/extract_municipality.py \
        --municipality 13101 --input source.gml --output extracted.gml
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.reconstruct_minimal import building_spans

_CITY_RE = re.compile(
    rb"<(?:\w+:)?city(?:\s[^>]*)?>([^<]+)</(?:\w+:)?city>"
)
_BUILDING_RE = re.compile(rb"<(?:\w+:)?Building\b")
_GML_ID = "{http://www.opengis.net/gml}id"
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def _missing_local_references(raw: bytes) -> set[str]:
    """Return the set of unresolved local ``#id`` references in the XML."""
    root = ET.fromstring(raw)
    ids: set[str] = set()
    references: set[str] = set()
    for element in root.iter():
        gml_id = element.attrib.get(_GML_ID)
        if gml_id:
            ids.add(gml_id)
        href = element.attrib.get(_XLINK_HREF)
        if href and href.startswith("#"):
            references.add(href[1:])
        # CityGML Appearance's <app:target uri="#..."></app:target>
        if element.tag.rsplit("}", 1)[-1] == "target":
            uri = element.attrib.get("uri")
            if uri and uri.startswith("#"):
                references.add(uri[1:])
    return references - ids


def _municipality(member: bytes, gml_id: str) -> str:
    values = {
        match.group(1).decode("utf-8", errors="strict").strip()
        for match in _CITY_RE.finditer(member)
    }
    values.discard("")
    if len(values) != 1:
        shown = ", ".join(sorted(values)) or "none"
        raise ValueError(
            f"{gml_id}: Cannot uniquely determine municipality code ({shown})"
        )
    value = next(iter(values))
    if not (len(value) == 5 and value.isdigit()):
        raise ValueError(f"{gml_id}: Invalid municipality code: {value}")
    return value


def extract(raw: bytes, municipality: str) -> tuple[bytes, dict[str, object]]:
    """Delete only the out-of-scope buildings and return the output and tallies."""
    if not (len(municipality) == 5 and municipality.isdigit()):
        raise ValueError("Municipality code must be 5 digits.")

    source_missing_references = _missing_local_references(raw)
    spans = building_spans(raw)
    building_tag_count = len(_BUILDING_RE.findall(raw))
    if building_tag_count != len(spans):
        raise ValueError(
            "Cannot resolve all building blocks as byte spans "
            f"(Building={building_tag_count}, spans={len(spans)})"
        )

    removed: list[str] = []
    retained: list[str] = []
    edits: list[tuple[int, int]] = []
    for gml_id, (start, end) in spans.items():
        member = raw[start:end]
        if _municipality(member, gml_id) == municipality:
            retained.append(gml_id)
        else:
            removed.append(gml_id)
            edits.append((start, end))

    output = raw
    for start, end in sorted(edits, reverse=True):
        output = output[:start] + output[end:]

    output_spans = building_spans(output)
    if set(output_spans) != set(retained):
        raise RuntimeError("Extracted building set does not match expected value.")
    for gml_id in retained:
        old_start, old_end = spans[gml_id]
        new_start, new_end = output_spans[gml_id]
        if raw[old_start:old_end] != output[new_start:new_end]:
            raise RuntimeError(f"{gml_id}: Retained building byte sequence changed.")

    new_missing_references = _missing_local_references(output) - source_missing_references
    if new_missing_references:
        sample = ", ".join(sorted(new_missing_references)[:10])
        raise RuntimeError(
            f"Extraction created {len(new_missing_references)} new missing local references: {sample}"
        )

    report: dict[str, object] = {
        "municipality": municipality,
        "source_buildings": len(spans),
        "retained_buildings": len(retained),
        "removed_buildings": len(removed),
        "source_existing_missing_local_references": len(source_missing_references),
        "new_missing_local_references": len(new_missing_references),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "output_sha256": hashlib.sha256(output).hexdigest(),
    }
    return output, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--municipality", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        output, report = extract(args.input.read_bytes(), args.municipality)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
