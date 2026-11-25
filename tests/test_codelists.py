#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/codelists.py: the one reader of GML code lists (W6; fixes D-4b2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dialects.plateau import codelist_crosswalk
from scripts import codelists

DICTIONARY = ('<gml:Dictionary xmlns:gml="http://www.opengis.net/gml">'
              '<gml:dictionaryEntry><gml:Definition><gml:description>Shops &amp; offices</gml:description>'
              '<gml:name>411</gml:name></gml:Definition></gml:dictionaryEntry>'
              '<gml:dictionaryEntry><gml:Definition><gml:name>412</gml:name>'           # name before description
              '<gml:description>Hotel</gml:description></gml:Definition></gml:dictionaryEntry>'
              '<gml:dictionaryEntry><gml:Definition><gml:name>413</gml:name></gml:Definition></gml:dictionaryEntry>'
              '</gml:Dictionary>')


class TestCodelists(unittest.TestCase):
    def test_every_entry_as_xml_reads_it(self):
        # D-4b2: the crosswalk's regex kept "&amp;", and dropped 412 (name first) and 413 (no description)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "Building_usage.xml"
            path.write_text(DICTIONARY, encoding="utf-8")
            expected = {"411": "Shops & offices", "412": "Hotel", "413": ""}
            self.assertEqual(codelists.load_codelist(path), expected)
            self.assertEqual(codelist_crosswalk.load_codelist(path), expected)   # the crosswalk reads the same
            self.assertEqual(codelists.load_codelists(d), {"Building_usage.xml": expected})


if __name__ == "__main__":
    unittest.main()
