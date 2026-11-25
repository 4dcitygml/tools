#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""How a city's data identifies a building — one rule for every gate and script.

A city declares in `4dcitygml.json` which value is its stable building ID
(`building_id.type`):

- ``gml:id`` (also when a city declares nothing): the ``gml:id`` of the ``Building`` element
- ``core:externalReference``: the object name of the Building's first external reference (the
  ALKIS identifier of Bavarian LoD2 data)
- ``gen:<NAME>``: the value of the generic string attribute ``<NAME>`` (New York's BIN)
- ``<prefix>:<leaf>``: the value of an ADE leaf of that local name, any prefix (a dialect pack's
  identity leaf; the pack gives the city's default)

``building_id.invalid_values`` lists source placeholders that never identify a
building; a building whose value is absent or invalid falls back to its ``gml:id``.

The hub bundle ships this module, so the editors read the same rule.

The trailers of a commit message that name buildings (Exchange Contract A2) are read here
too, for the commit scope gate, the topology scope, the history index and the hub.

Byte-level helpers: city object members are located by the file's own spelling
of ``cityObjectMember`` (``<core:cityObjectMember>`` with a prefix, the default
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

# Every trailer the commit scope gate reads (A2); Created-By and others are convention only.
TRAILER_KEYS = ("Building", "Building-Added", "Building-Deleted", "Change-Type", "Scope-Municipality",
                "Building-ID-From", "Building-ID-To", "Provenance-Manifest", "Lifecycle-Manifest", "Corrects", "Reset-To")
BUILDING_TRAILERS = ("Building", "Building-Added", "Building-Deleted")   # one building per value
_KEYS = {key.lower(): key for key in TRAILER_KEYS}
_TRAILER_LINE = re.compile(r"([A-Za-z0-9][A-Za-z0-9-]*)[ \t]*:[ \t]*(.*?)[ \t]*")

# The Change-Type kinds (A2; the commit scope gate enforces them). The identity kinds work for
# every city (S11); a dialect pack adds its own (dialects.Pack.kinds), the last two of CHANGE_TYPES.
IDENTITY_KINDS = frozenset({"identity-baseline", "identity-correction"})
WHOLE_FILE_KINDS = frozenset({"source-baseline", "layout", "scope-extract"})   # files at once, no building trailers
GENERIC_KINDS = frozenset({"lifecycle", "layout", "source-baseline", "practice-reset"}) | IDENTITY_KINDS
# (schema-migration is designed but has no gate yet, so it is not accepted)
CHANGE_TYPES = GENERIC_KINDS | {"scope-extract", "schema-update"}


def trailers(message: str) -> dict[str, list[str]]:
    """The A2 trailers of one commit message as git reads them (git interpret-trailers --parse;
    U9, D-b1): the final paragraph, never the first (the subject), and only when every line of it
    is a `Token: value` trailer (a line that starts with white space continues the one before);
    keys regardless of case. key -> values in order."""
    lines = (message or "").replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    blank = [i for i, line in enumerate(lines) if not line.strip()]
    if not blank or not any(line.strip() for line in lines[:blank[-1]]):
        return {}   # one paragraph: the subject alone
    entries: list[list[str]] = []
    for line in lines[blank[-1] + 1:]:
        if line[:1] in (" ", "\t") and entries:
            entries[-1][1] += " " + line.strip()
            continue
        m = _TRAILER_LINE.fullmatch(line)
        if m is None:
            return {}   # prose in the last paragraph: it is no trailer block
        entries.append([m.group(1), m.group(2)])
    result: dict[str, list[str]] = {}
    for key, value in entries:
        if key.lower() in _KEYS and value.strip():
            result.setdefault(_KEYS[key.lower()], []).append(value.strip())
    return result


def trailer_buildings(found: dict[str, list[str]]) -> list[str]:
    """The building values of Building / Building-Added / Building-Deleted, in that order."""
    return [value for key in BUILDING_TRAILERS for value in found.get(key, [])]


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
    type: str = "gml:id"   # a city that declares no building_id (v2.0.0: no dialect is the default)
    invalid_values: frozenset = frozenset()



def rule_from_config(cfg: dict | None) -> IdentityRule:
    """The rule a city's 4dcitygml.json declares; gml:id when it declares none."""
    spec = (cfg or {}).get("building_id") if isinstance(cfg, dict) else None
    if not isinstance(spec, dict) or not str(spec.get("type") or "").strip():
        return IdentityRule()
    kind = str(spec["type"]).strip()
    invalid = frozenset(str(v) for v in (spec.get("invalid_values") or []) if str(v))
    return IdentityRule(kind, invalid)


def leaf_pattern(tag_local: str) -> "re.Pattern[bytes]":
    """The leaf elements named tag_local in a member's bytes, any prefix: groups are the
    opening tag, the text and the closing tag, so a value can be replaced in place."""
    t = re.escape(tag_local.encode("utf-8"))
    return re.compile(rb"(<(?:\w+:)?" + t + rb"\b[^>]*>)([^<]*)(</(?:\w+:)?" + t + rb">)")


def _value_pattern(rule: IdentityRule, value: bytes = rb"[^<]*?") -> "re.Pattern[bytes]":
    """Where the rule keeps the stable ID in a member's bytes: groups are what precedes the value,
    the value and what follows it, so it can be read or replaced in place (one pattern per type)."""
    if rule.type == "gml:id":
        return re.compile(rb'(<(?:\w+:)?Building\b[^>]*?\sgml:id=")(' + value + rb')(")')
    if rule.type == "core:externalReference":   # the first external reference's object name (CityGML core)
        return re.compile(rb"(<(?:\w+:)?externalObject\b[^>]*>\s*<(?:\w+:)?name\b[^>]*>\s*)(" + value
                          + rb")(\s*</(?:\w+:)?name>)")
    if rule.type.startswith("gen:"):
        name = re.escape(rule.type[4:].encode("utf-8"))
        return re.compile(rb'(<(?:\w+:)?stringAttribute\s+name="' + name + rb'"\s*>\s*<(?:\w+:)?value>\s*)('
                          + value + rb')(\s*</(?:\w+:)?value>)')
    t = re.escape(rule.type.split(":", 1)[-1].encode("utf-8"))   # the declared leaf, by its local name
    return re.compile(rb"(<(?:\w+:)?" + t + rb"(?:\s[^>]*)?>\s*)(" + value + rb")(\s*</(?:\w+:)?" + t + rb">)")


def stable_id(span: bytes, gml_id: str, rule: IdentityRule = IdentityRule()) -> str:
    """The stable building ID of one member under the city's rule (gml_id when absent or invalid)."""
    if rule.type == "gml:id":
        return gml_id
    hit = _value_pattern(rule).search(span)
    value = hit.group(2).decode("utf-8", errors="replace").strip() if hit else ""
    return value if value and value not in rule.invalid_values else gml_id


def identity_leaf(span: bytes, rule: IdentityRule = IdentityRule()) -> "tuple[str, int] | None":
    """The leaf that holds the stable ID under the rule, as (local name, its occurrence among the
    member's leaves of that name, leaf_pattern's order); None for gml:id or when it is absent.
    The editors keep it read-only: an ID changes only by an identity change (A2)."""
    hit = None if rule.type == "gml:id" else _value_pattern(rule).search(span)
    if not hit:
        return None
    tag = re.search(rb"</(?:\w+:)?([\w.-]+)>", hit.group(3)).group(1).decode("utf-8")
    return tag, sum(1 for m in leaf_pattern(tag).finditer(span) if m.start(2) < hit.start(2))


def replace_stable_id(span: bytes, old: str, new: str, rule: IdentityRule = IdentityRule()) -> bytes:
    """The member with its stable ID changed from old to new where the rule keeps it (the
    Building's gml:id, the external reference, the generic attribute's value, or the declared
    leaf), every other byte as it was. Unchanged when old is not found there."""
    n = new.encode("utf-8")
    return _value_pattern(rule, re.escape(old.encode("utf-8"))).sub(lambda m: m.group(1) + n + m.group(3), span, count=1)

