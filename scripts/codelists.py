#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""GML code lists (gml:Dictionary): the one reader of {code: label} (W6: a regex reader and an
lxml one; the regex kept `&amp;` as written and dropped an entry whose name came before its
description or that had no description, D-4b2)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from scripts import safe_xml  # noqa: E402
    from scripts.citygml_xml import localname  # noqa: E402
except ImportError:   # the hub bundle: the modules sit side by side
    import safe_xml  # noqa: E402
    from citygml_xml import localname  # noqa: E402


def load_codelist(path) -> dict:
    """A code list file as {code (gml:name): label (gml:description, "" when absent)}."""
    out: dict = {}
    for definition in safe_xml.parse(path).iter():
        if localname(definition) != "Definition":
            continue
        code = label = None
        for child in definition:
            if localname(child) == "name":
                code = (child.text or "").strip()
            elif localname(child) == "description":
                label = (child.text or "").strip()
        if code:
            out[code] = label or ""
    return out


def resolve(codespace, gml_dir):
    """The list file a relative codeSpace names for a .gml file in gml_dir (the standard reading of a
    relative reference), or None: an external URL or a missing file is out of scope. The one rule
    of where a value's code list is; the plausibility lint and the attribute editor both ask it."""
    if not codespace or "://" in codespace:
        return None
    path = (Path(gml_dir) / codespace).resolve()
    return path if path.is_file() else None


def read(path):
    """load_codelist, or None when the file is not well-formed XML (out of scope too)."""
    try:
        return load_codelist(path)
    except safe_xml.ParseError:
        return None


def load_codelists(folder) -> dict:
    """Every *.xml code list of a folder as {file name: {code: label}} (a malformed one is skipped)."""
    result: dict = {}
    for p in sorted(Path(folder).glob("*.xml")):
        try:
            result[p.name] = load_codelist(p)
        except safe_xml.ParseError:
            continue
    return result
