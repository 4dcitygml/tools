#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""D-3b4: the attribute editor on CityGML 1.0 data (Munich, New York). A source note added to a
1.0 building declared the generics 2.0 namespace, and 1.0 elements lost their prefixes in the
note keys (the prefix table knew only the 2.0 namespaces)."""
from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

import dialects
from tests.support import load_app

attr = load_app("attr_citygml10", "tools/attr_editor/app.py")

BUILDING = (b'<bldg:Building gml:id="b1">\n'
            b'\t<core:creationDate>2020-01-01</core:creationDate>\n'
            b'\t<bldg:measuredHeight uom="m">9.0</bldg:measuredHeight>\n'
            b'</bldg:Building>')


def city_model(version: str) -> bytes:
    return (f'<core:CityModel xmlns:core="http://www.opengis.net/citygml/{version}" '
            f'xmlns:bldg="http://www.opengis.net/citygml/building/{version}" '
            'xmlns:gml="http://www.opengis.net/gml">\n<core:cityObjectMember>\n').encode() + BUILDING + \
           b'\n</core:cityObjectMember>\n</core:CityModel>\n'


class TestCityGML10(unittest.TestCase):
    def note(self, version: str) -> bytes:
        raw = city_model(version)
        repo = attr.Repo.__new__(attr.Repo)   # the writer reads only its arguments and the pack
        repo._pack = dialects.dialect("plateau")
        return repo._apply_src_change(raw, BUILDING, "bldg:measuredHeight", "", "801")

    def test_a_new_note_declares_the_files_own_generics(self):
        self.assertIn(b'xmlns:gen="http://www.opengis.net/citygml/generics/1.0"', self.note("1.0"))
        self.assertIn(b'xmlns:gen="http://www.opengis.net/citygml/generics/2.0"', self.note("2.0"))

    def test_1_0_elements_keep_their_prefixes(self):
        root = ET.fromstring(city_model("1.0"))
        height = root.find(".//{http://www.opengis.net/citygml/building/1.0}measuredHeight")
        self.assertEqual(attr._qname(height), "bldg:measuredHeight")
        self.assertEqual(attr._qname(root), "core:CityModel")


if __name__ == "__main__":
    unittest.main()
