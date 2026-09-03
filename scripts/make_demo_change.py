#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Reproducible tool to deterministically apply demo PR (PR-A / PR-B) changes to actual .gml.

A generator to make paper chapter 6 PR-A/B reproducible in the form "anyone can re-run and get the same head"
(reproducibility requirement of work plan D2, D5). Surgically rewrites only the byte range of one target building,
leaving everything else unchanged. Therefore:

- Preserves PLATEAU's official serialization (UTF-8 BOM + CRLF) as-is.
- `git diff` becomes the minimal diff showing only the target building's changed sections, preserving reviewability.

Demo definitions (defaults, overridable):
- prA: Attributes only. `bldg:storeysAboveGround` 9999 (unknown) → 4. Geometry unchanged. (positive)
- prB: Geometry change. Z above footprint baseline +delta (default 3.0). measuredHeight also +delta. (positive)
- prC: Rebuilding. gml:id change + geometry +delta (old ID disappears → new ID generated, geometry also changes) → W3 lifecycle warning.
- prD: gml:id only (attributes/geometry unchanged) → W3 suspicious ID change warning.
- prE: Large-scale. Change storeys in first count (default 6) buildings in bulk → W3 large-scale change warning.
- prF: CI failure demo. Insert unclosed element into target building to invalidate XML → parsing fails, CI fails.

Usage:
    python scripts/make_demo_change.py INPUT.gml --building GMLID --change prA --output OUT.gml
    python scripts/make_demo_change.py INPUT.gml --building GMLID --change prD --output OUT.gml
    python scripts/make_demo_change.py INPUT.gml --change prE --count 6 --output OUT.gml
"""
from __future__ import annotations

import argparse
import re
import sys
from decimal import Decimal
from pathlib import Path

_COM_OPEN = b"<core:cityObjectMember>"
_COM_CLOSE = b"</core:cityObjectMember>"

_STOREYS_RE = b"<bldg:storeysAboveGround>%s</bldg:storeysAboveGround>"
_POSLIST_RE = re.compile(rb"(<gml:posList[^>]*>)([^<]+)(</gml:posList>)")
_MEASURED_RE = re.compile(rb"(<bldg:measuredHeight[^>]*>)([^<]+)(</bldg:measuredHeight>)")


def _find_span(raw: bytes, building_id: str) -> tuple[int, int]:
    """Return the byte range [start, end) of the core:cityObjectMember containing the target building."""
    needle = f'gml:id="{building_id}"'.encode("utf-8")
    idx = raw.find(needle)
    if idx < 0:
        raise SystemExit(f"Building not found: {building_id}")
    start = raw.rfind(_COM_OPEN, 0, idx)
    end = raw.find(_COM_CLOSE, idx)
    if start < 0 or end < 0:
        raise SystemExit("Cannot identify cityObjectMember range.")
    return start, end + len(_COM_CLOSE)


def _num(value: Decimal) -> bytes:
    """Byte string in fixed-point notation, avoiding exponent notation."""
    return format(value.normalize(), "f").encode("ascii")


def apply_prA(span: bytes, from_value: str, to_value: str) -> bytes:
    """Attribute-only change: storeysAboveGround from→to (first occurrence within the span)."""
    old = _STOREYS_RE % from_value.encode("ascii")
    new = _STOREYS_RE % to_value.encode("ascii")
    if span.count(old) < 1:
        raise SystemExit(f"storeysAboveGround={from_value} not found in span.")
    return span.replace(old, new, 1)


def apply_prB(span: bytes, delta: Decimal) -> bytes:
    """Geometry change: add +delta to Z values above the base plane (minimum Z). Also +delta to measuredHeight."""
    zmin: Decimal | None = None
    for m in _POSLIST_RE.finditer(span):
        toks = m.group(2).split()
        for k in range(2, len(toks), 3):
            z = Decimal(toks[k].decode("ascii"))
            zmin = z if zmin is None else min(zmin, z)
    if zmin is None:
        raise SystemExit("No posList in span (geometry change not possible).")

    def raise_z(m: re.Match) -> bytes:
        toks = m.group(2).split()
        for k in range(2, len(toks), 3):
            z = Decimal(toks[k].decode("ascii"))
            if z > zmin:
                toks[k] = _num(z + delta)
        return m.group(1) + b" ".join(toks) + m.group(3)

    def bump_height(m: re.Match) -> bytes:
        return m.group(1) + _num(Decimal(m.group(2).decode("ascii")) + delta) + m.group(3)

    span = _POSLIST_RE.sub(raise_z, span)
    span = _MEASURED_RE.sub(bump_height, span, count=1)
    return span


def apply_prD(span: bytes, old_id: str, new_id: str) -> bytes:
    """ID change only: gml:id of the target building old→new. Attributes and geometry unchanged (unnecessary-ID-change demo)."""
    old = old_id.encode("ascii")
    if span.count(old) < 1:
        raise SystemExit(f"gml:id={old_id} not found in span.")
    return span.replace(old, new_id.encode("ascii"))


def apply_prC(span: bytes, old_id: str, new_id: str, delta: Decimal) -> bytes:
    """Rebuild: change gml:id and raise the roof by +delta (geometry also changes = treated as a new building / lifecycle)."""
    return apply_prB(apply_prD(span, old_id, new_id), delta)


def apply_prE(raw: bytes, from_value: str, to_value: str, count: int) -> bytes:
    """Large-scale: storeysAboveGround from→to for the first count buildings (whole file, multiple buildings)."""
    old = _STOREYS_RE % from_value.encode("ascii")
    new = _STOREYS_RE % to_value.encode("ascii")
    have = raw.count(old)
    if have < count:
        raise SystemExit(f"storeys={from_value} found {have} times, expected {count}.")
    return raw.replace(old, new, count)


def apply_prF(span: bytes) -> bytes:
    """CI failure demo: insert an unclosed element into the target building to make the XML invalid (parsing fails).

    Example for reviewers to confirm via a failing Check that CI is not treated as
    successful when parsing fails.
    """
    marker = b"<ci_failure_demo></bldg:Building>"
    if b"</bldg:Building>" not in span:
        raise SystemExit("</bldg:Building> not found in span.")
    return span.replace(b"</bldg:Building>", marker, 1)


# Deterministic new gml:id for each demo (fixed for reproducibility)
_DEMO_NEW_ID = {
    "prC": "bldg_11111111-2222-3333-4444-555555555555",
    "prD": "bldg_00000000-1111-2222-3333-444444444444",
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="Base .gml file")
    p.add_argument("--change", required=True, choices=["prA", "prB", "prC", "prD", "prE", "prF"])
    p.add_argument("--output", type=Path, required=True, help="Output .gml file")
    p.add_argument("--building", help="Target building gml:id (required for prA/B/C/D)")
    p.add_argument("--from-value", default="9999", help="[prA/E] storeys value before change")
    p.add_argument("--to-value", default="4", help="[prA/E] storeys value after change")
    p.add_argument("--delta", default="3.0", help="[prB/C] Z and height increment (m)")
    p.add_argument("--new-id", default=None, help="[prC/D] gml:id after change (default is fixed value)")
    p.add_argument("--count", type=int, default=6, help="[prE] number of buildings to change")
    args = p.parse_args(argv)

    raw = args.input.read_bytes()

    if args.change == "prE":
        out = apply_prE(raw, args.from_value, args.to_value, args.count)
        detail = f"{args.count} buildings, storeys {args.from_value}→{args.to_value}"
    else:
        if not args.building:
            p.error(f"--change {args.change} requires --building.")
        start, end = _find_span(raw, args.building)
        span = raw[start:end]
        if args.change == "prA":
            new_span = apply_prA(span, args.from_value, args.to_value)
        elif args.change == "prB":
            new_span = apply_prB(span, Decimal(args.delta))
        elif args.change == "prD":
            new_span = apply_prD(span, args.building, args.new_id or _DEMO_NEW_ID["prD"])
        elif args.change == "prF":
            new_span = apply_prF(span)
        else:  # prC
            new_span = apply_prC(
                span, args.building, args.new_id or _DEMO_NEW_ID["prC"], Decimal(args.delta)
            )
        if new_span == span:
            raise SystemExit("No changes applied (span has no diff).")
        out = raw[:start] + new_span + raw[end:]
        detail = f"{args.building} rewritten (span {len(span)}→{len(new_span)} bytes)"

    if out == raw:
        raise SystemExit("No changes applied.")
    args.output.write_bytes(out)
    print(f"{args.change}: {detail} (total {len(out) - len(raw):+d} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
