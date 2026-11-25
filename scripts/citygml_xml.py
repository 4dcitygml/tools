#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The CityGML vocabulary and the small XML helpers the scripts share.

Namespace URIs, the local name of an element, the coordinates and bounding
box of a city object, the buildings of a file one at a time, and the one tile writer. Parsing itself goes through scripts/safe_xml.py (the standard library; U13). The
helpers read element names as strings, so they serve ElementTree and lxml elements alike.
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    from scripts import safe_xml
    from scripts.citygml_dialect import NS, NS10
except ImportError:   # the hub bundle: the modules sit side by side
    import safe_xml
    from citygml_dialect import NS, NS10

# The namespace URIs have one owner, citygml_dialect.NS (it ships with the hub, which has no lxml).
GML, CORE, BLDG, APP, GEN, XLINK = (NS[k] for k in ("gml", "core", "bldg", "app", "gen", "xlink"))
# A Building in CityGML 2.0, 1.0 and 3.0; appearance in every version (it is never an attribute)
BUILDING_TAGS = (f"{{{BLDG}}}Building", f"{{{NS10['bldg']}}}Building", f"{{{BLDG.replace('/2.0', '/3.0')}}}Building")
APPEARANCE_NS = frozenset((APP, NS10["app"], APP.replace("/2.0", "/3.0")))

_COORD_TAGS = (f"{{{GML}}}posList", f"{{{GML}}}pos")
# The element names the tile tools rebuild (CityGML 2.0; the one set, W1: each tool had its own)
Q_MEMBER = f"{{{CORE}}}cityObjectMember"
Q_APPEARANCE_MEMBER = f"{{{APP}}}appearanceMember"
Q_APPEARANCE = f"{{{APP}}}Appearance"
Q_THEME = f"{{{APP}}}theme"
Q_SURFACE_DATA_MEMBER = f"{{{APP}}}surfaceDataMember"
Q_TARGET = f"{{{APP}}}target"
Q_PARAMETERIZED_TEXTURE = f"{{{APP}}}ParameterizedTexture"
Q_IMAGE_URI = f"{{{APP}}}imageURI"


def _split(name: str) -> "tuple[str, str]":
    """'{namespace}local' -> (namespace, local); a name without a namespace -> ('', name)."""
    if name[:1] == "{":
        ns, _, local = name[1:].partition("}")
        return ns, local
    return "", name


def qname(elem) -> "tuple[str, str]":
    """The element's (namespace, local name); ('', '') for comments and processing instructions."""
    return _split(elem.tag) if isinstance(elem.tag, str) else ("", "")


def gml_id(elem) -> "str | None":
    """The element's gml:id, whatever the gml namespace's version."""
    for key, val in elem.attrib.items():
        if _split(key)[1] == "id":
            return val
    return None


def localname(elem) -> str:
    """The local name of an element; empty for comments and processing instructions."""
    return qname(elem)[1]


def buildings(source):
    """Every Building (CityGML 1.0 / 2.0 / 3.0) of a source (bytes or a path), one at a time, read with
    the standard library: each is released once the next is read, and the members the walk has
    passed are dropped, so memory stays bounded on large tiles (U13; it was lxml's tag filter)."""
    root, depth = None, 0
    for event, elem in safe_xml.iterparse(source, events=("start", "end")):
        if event == "start":
            root = elem if root is None else root
            depth += 1
            continue
        depth -= 1
        if elem.tag in BUILDING_TAGS:
            yield elem
            elem.clear()
        if depth == 1:          # a member of the city model is done: nothing refers back to it
            root.clear()


_LOD = re.compile(r"[Ll]od(\d)")


def lod_of(name: str) -> "int | None":
    """The level of detail an element name carries (lod2Solid, geometrySrcDescLod1: 2, 1), or None.
    The one reading of it (W6: three patterns)."""
    m = _LOD.search(name)
    return int(m.group(1)) if m else None


def without_lod(name: str) -> str:
    """The element name without its level of detail (geometrySrcDescLod1 -> geometrySrcDesc)."""
    return _LOD.sub("", name) or name


def iter_coords(elem):
    """Every (x, y, z) triple of the posList / pos elements under elem (3D coordinates)."""
    for tag in _COORD_TAGS:
        for el in elem.iter(tag):
            if not el.text:
                continue
            nums = el.text.split()
            for i in range(0, len(nums) - 2, 3):
                try:
                    yield float(nums[i]), float(nums[i + 1]), float(nums[i + 2])
                except ValueError:
                    continue


def bbox(elem):
    """(xmin, ymin, zmin, xmax, ymax, zmax) over the coordinates under elem, or None when it has none."""
    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    for x, y, z in iter_coords(elem):
        xmin, xmax = min(xmin, x), max(xmax, x)
        ymin, ymax = min(ymin, y), max(ymax, y)
        zmin, zmax = min(zmin, z), max(zmax, z)
    if xmin == float("inf"):
        return None
    return xmin, ymin, zmin, xmax, ymax, zmax


def union(boxes) -> "list | None":
    """The bounding box over several (xmin, ymin, zmin, xmax, ymax, zmax), None when there is none."""
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[i] for b in boxes) for i in (0, 1, 2)] + [max(b[i] for b in boxes) for i in (3, 4, 5)]


def children(root, local: str) -> list:
    """The root's child elements of one local name (cityObjectMember, appearanceMember; any version)."""
    return [c for c in root if localname(c) == local]


def members(root) -> list:
    """The city objects of a model: its cityObjectMember children."""
    return children(root, "cityObjectMember")


def refuse_global_appearance(root, what: str) -> None:
    """Stop for a model whose appearance lies beside its members (the tool would lose textures)."""
    if children(root, "appearanceMember"):
        raise SystemExit(f"{what}: files with global appearance are not supported (texture redistribution required)")


# --- the tile writer: one way to write a tile (W1). lxml keeps the source's prefixes, so the
# trees here are the safe_xml.safe_parse ones; every other module stays on the standard library. ---

def sub(parent, tag: str, text: "str | None" = None, attrib: "dict | None" = None):
    """A new child element of parent (the parent's own element type)."""
    el = parent.makeelement(tag, dict(attrib or {}))
    el.text = text
    parent.append(el)
    return el


def rebuild_root(source_root, objects=(), bbox=None, name: "str | None" = None, appearances=()):
    """A new model like source_root (its element, prefixes and attributes): gml:name, the
    Envelope of bbox with every attribute of the source's Envelope (CRS, dimension, labels),
    then the appearance members and the city object members (moved, not copied)."""
    from lxml import etree
    root = etree.Element(source_root.tag, nsmap=source_root.nsmap)
    for key, value in source_root.attrib.items():
        root.set(key, value)
    if name is not None:
        sub(root, f"{{{GML}}}name", name)
    if bbox:
        source_env = source_root.find(f"{{{GML}}}boundedBy/{{{GML}}}Envelope")
        env = sub(sub(root, f"{{{GML}}}boundedBy"), f"{{{GML}}}Envelope",
                  attrib=dict(source_env.attrib) if source_env is not None else None)
        sub(env, f"{{{GML}}}lowerCorner", f"{bbox[0]} {bbox[1]} {bbox[2]}")
        sub(env, f"{{{GML}}}upperCorner", f"{bbox[3]} {bbox[4]} {bbox[5]}")
    for el in [*appearances, *objects]:
        root.append(el)
    return root


def to_bytes(root, declaration: bool = True) -> bytes:
    """A tree read for writing, as UTF-8 bytes (with lxml's declaration when asked)."""
    from lxml import etree
    return etree.tostring(root, encoding="UTF-8", xml_declaration=declaration)


def serialize(el) -> str:
    """An element of a tree read for writing, as text (no declaration)."""
    from lxml import etree
    return etree.tostring(el, encoding="unicode")


def write_gml(root, out_path: Path, like: "Path | None" = None) -> None:
    """Write a model the way its source file is written: the source's UTF-8 BOM and CRLF line ends
    when it has them (some national delivery files do), a double-quoted UTF-8 declaration. One
    writer for every tile tool (W1: a profile's own writer and three lxml writers before)."""
    from lxml import etree
    head = Path(like).read_bytes()[:65536] if like is not None else b""
    body = b'<?xml version="1.0" encoding="UTF-8"?>\n' + etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    if b"\r\n" in head:
        body = body.replace(b"\n", b"\r\n")
    if head.startswith(b"\xef\xbb\xbf"):
        body = b"\xef\xbb\xbf" + body
    Path(out_path).write_bytes(body)
