#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Hardened lxml parsing shared by every XML consumer (reusable shared component).

Fork-PR data flows through the analysis workflows into these parsers, so all
XML input is treated as untrusted. lxml's default parser still resolves
entities declared in an inline DOCTYPE (`resolve_entities=True`), which allows
XXE-style local file reads even with `load_dtd=False`; `no_network` and
`load_dtd` merely default to safe values and are pinned here so a future lxml
default change cannot silently reopen them. CityGML never legitimately uses
DTDs or entities, so nothing functional is lost.

Every parse in scripts/ goes through this module. `huge_tree` stays a
per-call-site opt-in (production meshes exceed libxml2's default node limits;
it lifts size limits only and does not re-enable entity resolution).
"""

from __future__ import annotations

from lxml import etree

_SAFE_OPTIONS = {
    "resolve_entities": False,
    "no_network": True,
    "load_dtd": False,
}


def safe_parser(**overrides) -> etree.XMLParser:
    """Return an XMLParser with entity/DTD/network access disabled.

    Keyword arguments are passed through to ``etree.XMLParser`` and may add
    options such as ``huge_tree=True`` or ``remove_blank_text=False``; the
    safety pins above cannot be weakened by callers that simply omit them.
    """
    options = dict(_SAFE_OPTIONS)
    options.update(overrides)
    return etree.XMLParser(**options)


def safe_parse(source, **overrides) -> etree._ElementTree:
    """``etree.parse`` with the hardened parser."""
    return etree.parse(source, safe_parser(**overrides))


def safe_fromstring(text, **overrides) -> etree._Element:
    """``etree.fromstring`` with the hardened parser."""
    return etree.fromstring(text, safe_parser(**overrides))


def safe_iterparse(source, **kwargs):
    """``etree.iterparse`` with the hardened options merged in.

    Accepts the usual ``events`` / ``tag`` / ``huge_tree`` keywords.
    """
    options = dict(_SAFE_OPTIONS)
    options.update(kwargs)
    return etree.iterparse(source, **options)
