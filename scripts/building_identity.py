#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""How a city's data identifies a building — one rule for every gate and script.

A city declares in `4dcitygml.json` which value is its stable building ID
(`building_id.type`):

- ``uro:buildingID`` (default, PLATEAU): the value of ``<uro:buildingID>``
- ``gml:id``: the ``gml:id`` of the ``Building`` element (Munich etc.)
- ``gen:<NAME>``: the value of the generic string attribute ``<NAME>`` (New York's BIN)

``building_id.invalid_values`` lists source placeholders that never identify a
building; a building whose value is absent or invalid falls back to its ``gml:id``.

The attribute editor carries the same rule in ``tools/attr_editor/app.py``
(it is shipped without ``scripts/``); a test keeps the two in step.

Byte-level helpers: city object members are located by the file's own spelling
of ``cityObjectMember`` (``<core:cityObjectMember>`` in PLATEAU, the default
namespace ``<cityObjectMember>`` in CityGML 1.0 data), so edits stay
byte-preserving in every dialect.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_COM_OPEN = b"<core:cityObjectMember>"
_COM_CLOSE = b"</core:cityObjectMember>"
_COM_ANY_RE = re.compile(rb"<((?:\w+:)?cityObjectMember)[ >]")
_CITYMODEL_CLOSE_RE = re.compile(rb"</(?:\w+:)?CityModel>")
# gml:id of a Building (1.0/2.0, any prefix) inside a member
_BUILDING_GML_ID_RE = re.compile(rb'<(?:\w+:)?Building\b[^>]*?\sgml:id="([^"]+)"')
_BUILDINGID_RE = re.compile(rb"<(?:\w+:)?buildingID(?:\s[^>]*)?>([^<]+)</(?:\w+:)?buildingID>")


def member_markers(raw: bytes) -> tuple[bytes, bytes]:
    """The open/close tags of this file's cityObjectMember, decided once so scanning stays a byte find."""
    if raw.find(_COM_OPEN) >= 0:
        return _COM_OPEN, _COM_CLOSE
    m = _COM_ANY_RE.search(raw)
    if m:
        tag = m.group(1)
        return b"<" + tag + b">", b"</" + tag + b">"
    return _COM_OPEN, _COM_CLOSE


def citymodel_close(raw: bytes) -> int:
    """Offset of the file's closing CityModel tag (any prefix), or -1."""
    matches = list(_CITYMODEL_CLOSE_RE.finditer(raw))
    return matches[-1].start() if matches else -1


def building_spans(raw: bytes) -> dict[str, tuple[int, int]]:
    """gml:id -> [start, end) of the member holding that building; members without a Building are skipped."""
    spans: dict[str, tuple[int, int]] = {}
    com_open, com_close = member_markers(raw)
    pos = 0
    while True:
        start = raw.find(com_open, pos)
        if start < 0:
            break
        close = raw.find(com_close, start)
        if close < 0:
            break
        end = close + len(com_close)
        m = _BUILDING_GML_ID_RE.search(raw, start, end)
        if m:
            spans[m.group(1).decode("utf-8")] = (start, end)
        pos = end
    return spans


@dataclass(frozen=True)
class IdentityRule:
    type: str = "uro:buildingID"
    invalid_values: frozenset = frozenset()


def rule_from_config(cfg: dict | None) -> IdentityRule:
    """The rule a city's 4dcitygml.json declares (PLATEAU's uro:buildingID when it declares none)."""
    spec = (cfg or {}).get("building_id") if isinstance(cfg, dict) else None
    if not isinstance(spec, dict):
        return IdentityRule()
    kind = str(spec.get("type") or "uro:buildingID").strip() or "uro:buildingID"
    invalid = frozenset(str(v) for v in (spec.get("invalid_values") or []) if str(v))
    return IdentityRule(kind, invalid)


def stable_id(span: bytes, gml_id: str, rule: IdentityRule = IdentityRule()) -> str:
    """The stable building ID of one member under the city's rule (gml_id when absent or invalid)."""
    if rule.type == "gml:id":
        return gml_id
    if rule.type.startswith("gen:"):
        name = re.escape(rule.type[4:].encode("utf-8"))
        m = re.search(rb'<(?:\w+:)?stringAttribute\s+name="' + name + rb'"\s*>\s*<(?:\w+:)?value>([^<]+)</', span)
        value = m.group(1).decode("utf-8").strip() if m else ""
        return value if value and value not in rule.invalid_values else gml_id
    hit = _BUILDINGID_RE.search(span)
    value = hit.group(1).decode("utf-8").strip() if hit else ""
    return value if value and value not in rule.invalid_values else gml_id
