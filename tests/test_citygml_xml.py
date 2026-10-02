# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/citygml_xml.py: the XML vocabulary and helpers the scripts share."""
from __future__ import annotations

import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import citygml_xml as X  # noqa: E402

DOC = f'''<core:CityModel xmlns:core="{X.CORE}" xmlns:gml="{X.GML}">
<!-- a comment --><core:cityObjectMember><gml:Polygon>
<gml:posList>1 2 3 4 5 6</gml:posList><gml:pos>-1 0.5 9</gml:pos><gml:pos>x y z</gml:pos>
</gml:Polygon></core:cityObjectMember></core:CityModel>'''


class TestVocabulary(unittest.TestCase):
    def test_localname_and_coordinates(self):
        root = etree.fromstring(DOC.encode("utf-8"))
        self.assertEqual(X.localname(root), "CityModel")
        self.assertEqual(X.localname(root[0]), "")                      # the comment
        self.assertEqual(list(X.iter_coords(root)), [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (-1.0, 0.5, 9.0)])
        self.assertEqual(X.bbox(root), (-1.0, 0.5, 3.0, 4.0, 5.0, 9.0))
        self.assertIsNone(X.bbox(etree.Element("empty")))

    def test_plateau_serialization_and_source_form(self):
        root = etree.fromstring(f'<a xmlns:gml="{X.GML}"><b>x</b></a>'.encode("utf-8"))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.gml"
            X.write_plateau_gml(root, out)
            raw = out.read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\r\n<a'))
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertIsInstance(X.open_source(b"<a/>"), io.BytesIO)
        self.assertEqual(X.open_source(Path("/x/y.gml")), "/x/y.gml")


class TestEveryParseIsHardened(unittest.TestCase):
    """Fork-PR data reaches the scripts: every parse goes through scripts/safe_xml.py (A8)."""

    def test_no_script_parses_on_its_own(self):
        own = re.compile(r"\b(?:etree|ET|ElementTree)\.(?:parse|fromstring|iterparse|XMLParser)\(|import xml\.etree|from xml\.etree")
        found = [f"{p.name}:{n}" for p in sorted((REPO_ROOT / "scripts").glob("*.py")) if p.name != "safe_xml.py"
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if own.search(line)]
        self.assertEqual(found, [])

    def test_the_ci_preview_does_not_resolve_entities(self):
        from scripts import extract_building_preview as preview
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret.txt"
            secret.write_text("TOP-SECRET", encoding="utf-8")
            doc = (f'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
                   f'<core:CityModel xmlns:core="{X.CORE}"><core:name>&x;</core:name></core:CityModel>').encode()
            try:
                self.assertNotIn("TOP-SECRET", repr(preview._extract_buildings(doc)))
            except etree.XMLSyntaxError:
                pass   # refusing the document is as good


if __name__ == "__main__":
    unittest.main()
