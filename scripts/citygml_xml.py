#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The CityGML vocabulary and the small XML helpers the scripts share.

Namespace URIs, the local name of an element, the coordinates and bounding
box of a city object, PLATEAU's serialization and the "bytes or path" source
form. Parsing itself goes through scripts/safe_xml.py.
"""
from __future__ import annotations

import io
from pathlib import Path

from lxml import etree

GML = "http://www.opengis.net/gml"
CORE = "http://www.opengis.net/citygml/2.0"
BLDG = "http://www.opengis.net/citygml/building/2.0"
APP = "http://www.opengis.net/citygml/appearance/2.0"
GEN = "http://www.opengis.net/citygml/generics/2.0"
XLINK = "http://www.w3.org/1999/xlink"

_COORD_TAGS = (f"{{{GML}}}posList", f"{{{GML}}}pos")


def localname(elem) -> str:
    """The local name of an element; empty for comments and processing instructions."""
    if not isinstance(elem.tag, str):
        return ""
    return etree.QName(elem).localname


def iter_coords(elem: etree._Element):
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


def bbox(elem: etree._Element):
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


def write_plateau_gml(root: etree._Element, out_path: Path) -> None:
    """Write in PLATEAU's official serialization: UTF-8 BOM, CRLF, double-quoted declaration."""
    body = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    body = b'<?xml version="1.0" encoding="UTF-8"?>\n' + body
    body = body.replace(b"\n", b"\r\n")
    body = b"\xef\xbb\xbf" + body
    out_path.write_bytes(body)


def open_source(source):
    """What lxml can read: a BytesIO for bytes, the path as a string otherwise."""
    if isinstance(source, (bytes, bytearray)):
        return io.BytesIO(source)
    return str(source)
