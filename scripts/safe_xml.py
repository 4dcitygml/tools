#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Hardened XML parsing shared by every XML consumer (reusable shared component).

The analysis gates parse with the standard library (U13): fromstring, parse and iterparse below
refuse any DOCTYPE (and so do the lxml readers further down), so no entity is ever expanded (XXE, entity expansion) and nothing external is
read. CityGML never legitimately uses a DTD, so nothing functional is lost. The prefix-keeping
writers (the dialect pack's tools, until phase 4) still use lxml through the safe_* functions below.

A malformed document raises this module's ParseError from every function here, so callers catch
one error without importing lxml (W4: lxml stays in this module, citygml_xml's writer and the
CityGML 3 converter).

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

import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ParseError = ET.ParseError
_REFUSED = "a DOCTYPE is not accepted in CityGML data (no entities, no external references)"
_ROOT_START = re.compile(rb"<[^?!]")   # the first element: the end of the prolog


class _NoDoctype(ET.TreeBuilder):
    def doctype(self, name, pubid, system):   # expat reports any DOCTYPE before its entities are used
        raise ParseError(_REFUSED)


def fromstring(data):
    """The root element of an XML document (bytes or text); any DOCTYPE is refused (ParseError)."""
    return ET.fromstring(data, parser=ET.XMLParser(target=_NoDoctype()))


def tostring(root) -> bytes:
    """A tree read here, serialized with its XML declaration (UTF-8)."""
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8")


def element_bytes(element) -> bytes:
    """One element of a tree read here (lxml's or the standard library's), serialized."""
    if hasattr(element, "getroottree"):
        from lxml import etree
        return etree.tostring(element)
    return ET.tostring(element)


def parse(path):
    """The root element of an XML file; any DOCTYPE is refused (ParseError)."""
    return fromstring(Path(path).read_bytes())


def _prolog(head: bytes) -> "tuple[bytes, bool]":
    """(everything before the root element, whether the root element was reached)."""
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):   # UTF-16: look at it as UTF-8 text
        head = head.decode("utf-16", errors="ignore").encode("utf-8")
    m = _ROOT_START.search(head)
    return (head[:m.start()], True) if m else (head, False)


def iterparse(source, events=("end",)):
    """ElementTree's iterparse over bytes or a path, after the prolog (everything before the root
    element) is checked: any DOCTYPE is refused (ParseError). The event machinery takes only
    ElementTree's own tree builder, so the DOCTYPE is found in the prolog here instead."""
    if isinstance(source, (bytes, bytearray)):
        prolog, _ = _prolog(bytes(source))
        stream = io.BytesIO(source)
    else:
        head, reached = b"", False
        with open(source, "rb") as fh:
            while not reached:
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                head += chunk
                prolog, reached = _prolog(head)
        prolog, _ = _prolog(head)
        stream = str(source)
    if b"<!DOCTYPE" in prolog:
        raise ParseError(_REFUSED)
    return ET.iterparse(stream, events=events)


# --- lxml, for the prefix-keeping writers -----------------------------------
_SAFE_OPTIONS = {
    "resolve_entities": False,
    "no_network": True,
    "load_dtd": False,
}


def safe_parser(**overrides):
    """Return an XMLParser with entity/DTD/network access disabled.

    Keyword arguments are passed through to ``etree.XMLParser`` and may add
    options such as ``huge_tree=True`` or ``remove_blank_text=False``; the
    safety pins above cannot be weakened by callers that simply omit them.
    """
    from lxml import etree
    options = dict(_SAFE_OPTIONS)
    options.update(overrides)
    return etree.XMLParser(**options)


def _no_doctype(tree):
    """A tree read by lxml, refused when its document has a DOCTYPE (as the readers above)."""
    if tree.docinfo.doctype:
        raise ParseError(_REFUSED)
    return tree


def safe_parse(source, **overrides):
    """``etree.parse`` with the hardened parser; any DOCTYPE is refused (ParseError)."""
    from lxml import etree
    try:
        return _no_doctype(etree.parse(source, safe_parser(**overrides)))
    except etree.XMLSyntaxError as e:
        raise ParseError(str(e)) from e


def safe_fromstring(text, **overrides):
    """``etree.fromstring`` with the hardened parser; any DOCTYPE is refused (ParseError)."""
    from lxml import etree
    try:
        root = etree.fromstring(text, safe_parser(**overrides))
    except etree.XMLSyntaxError as e:
        raise ParseError(str(e)) from e
    _no_doctype(root.getroottree())
    return root


def safe_iterparse(source, **kwargs):
    """``etree.iterparse`` with the hardened options merged in.

    Accepts the usual ``events`` / ``tag`` / ``huge_tree`` keywords. Any DOCTYPE is refused
    (ParseError), found in the prolog as iterparse above.
    """
    from lxml import etree
    options = dict(_SAFE_OPTIONS)
    options.update(kwargs)
    if isinstance(source, (bytes, bytearray, str, Path)):
        if isinstance(source, (bytes, bytearray)):
            head = bytes(source[:1 << 16])
        else:
            with open(source, "rb") as fh:
                head = fh.read(1 << 16)
        if b"<!DOCTYPE" in _prolog(head)[0]:
            raise ParseError(_REFUSED)
    try:
        yield from etree.iterparse(source, **options)
    except etree.XMLSyntaxError as e:
        raise ParseError(str(e)) from e
