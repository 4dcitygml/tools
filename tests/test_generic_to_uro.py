#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The PLATEAU pack's i-UR restore after a 3DCityDB round trip (dialects/plateau/generic_to_uro.py)."""
from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from dialects.plateau.generic_to_uro import restore_generic_uro

URO = "https://www.geospatial.jp/iur/uro/3.1"
GEN = "http://www.opengis.net/citygml/generics/2.0"


def model(sets: str) -> bytes:
    return (f'<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            f'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gen="{GEN}">'
            f'<core:cityObjectMember><bldg:Building>{sets}</bldg:Building></core:cityObjectMember></core:CityModel>').encode()


def entry(name: str, value: str) -> str:
    return f'<gen:stringAttribute name="{name}"><gen:value>{value}</gen:value></gen:stringAttribute>'


class TestRestore(unittest.TestCase):
    def test_a_set_without_a_wrapper_name_is_kept(self):
        # D-4b1: a set named "uro:" was counted, removed and not written again: its content was lost
        raw = model(f'<gen:genericAttributeSet name="uro:">{entry("x", "1")}</gen:genericAttributeSet>')
        out, count = restore_generic_uro(raw, uro_namespace=URO)
        self.assertEqual(count, 0)
        self.assertIn(b'name="uro:"', out)
        self.assertIn(b"<gen:value>1</gen:value>", out)

    def test_a_leaf_keeps_its_unit_and_its_code_space(self):
        # D-4b6: a leaf with both lost its codeSpace
        raw = model('<gen:genericAttributeSet name="uro:buildingDetailAttribute">'
                    + entry("BuildingDetailAttribute/buildingRoofEdgeArea", "12.5")
                    + entry("BuildingDetailAttribute/buildingRoofEdgeArea@uom", "m2")
                    + entry("BuildingDetailAttribute/buildingRoofEdgeArea@codeSpace", "../codelists/x.xml")
                    + "</gen:genericAttributeSet>")
        out, count = restore_generic_uro(raw, uro_namespace=URO)
        self.assertEqual(count, 1)
        leaf = next(el for el in ET.fromstring(out).iter() if el.tag == f"{{{URO}}}buildingRoofEdgeArea")
        self.assertEqual((leaf.text, leaf.get("uom"), leaf.get("codeSpace")), ("12.5", "m2", "../codelists/x.xml"))

    def test_a_doctype_is_refused(self):
        # the restore parsed with its own lxml parser; it reads through safe_xml now
        from scripts.safe_xml import ParseError
        with self.assertRaises(ParseError):
            restore_generic_uro(b'<!DOCTYPE x [<!ENTITY e "y">]>' + model(""), uro_namespace=URO)


if __name__ == "__main__":
    unittest.main()
