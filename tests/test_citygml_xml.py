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

    def test_the_writer_writes_like_its_source(self):
        # W1: one writer; PLATEAU's delivery form (BOM, CRLF) comes from the source file, not a writer of its own
        root = etree.fromstring(f'<a xmlns:gml="{X.GML}"><b>x</b></a>'.encode("utf-8"))
        with tempfile.TemporaryDirectory() as d:
            plateau, plain, out = Path(d) / "p.gml", Path(d) / "m.gml", Path(d) / "o.gml"
            plateau.write_bytes(b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\r\n<a/>\r\n')
            plain.write_bytes(b"<?xml version='1.0' encoding='UTF-8'?>\n<a/>\n")
            X.write_gml(root, out, like=plateau)
            raw = out.read_bytes()
            self.assertTrue(raw.startswith(b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\r\n<a'))
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
            X.write_gml(root, out, like=plain)
            self.assertTrue(out.read_bytes().startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<a'))
            self.assertNotIn(b"\r", out.read_bytes())

    def test_a_rebuilt_root_keeps_the_source_envelope(self):
        src = etree.fromstring(f'<c:CityModel xmlns:c="{X.CORE}" xmlns:gml="{X.GML}" xmlns:x="urn:x" x:a="1">'
                               f'<gml:boundedBy><gml:Envelope srsName="urn:crs" srsDimension="3" axisLabels="E N H">'
                               f'<gml:lowerCorner>0 0 0</gml:lowerCorner><gml:upperCorner>1 1 1</gml:upperCorner>'
                               f'</gml:Envelope></gml:boundedBy><c:cityObjectMember/></c:CityModel>'.encode("utf-8"))
        new = X.rebuild_root(src, X.members(src), X.union([(1, 2, 3, 4, 5, 6), (0, 3, 2, 3, 7, 6)]), name="t")
        env = new.find(f"{{{X.GML}}}boundedBy/{{{X.GML}}}Envelope")
        self.assertEqual(dict(env.attrib), {"srsName": "urn:crs", "srsDimension": "3", "axisLabels": "E N H"})
        self.assertEqual([env[0].text, env[1].text], ["0 2 2", "4 7 6"])
        self.assertEqual(new.get("{urn:x}a"), "1")
        self.assertEqual([X.localname(c) for c in new], ["name", "boundedBy", "cityObjectMember"])


class TestLxmlIsConfined(unittest.TestCase):
    """W4: lxml is needed only to keep a file's prefixes when it is written again. It stays in the
    parser module, the one tile writer and the CityGML 3 converter; everything else reads with the
    standard library or through these."""

    def test_three_modules_import_lxml(self):
        imports = re.compile(r"^\s*(?:from lxml|import lxml)", re.M)
        found = sorted(str(p.relative_to(REPO_ROOT)) for top in ("scripts", "tools", "dialects", "ci")
                       for p in (REPO_ROOT / top).rglob("*.py") if imports.search(p.read_text(encoding="utf-8")))
        self.assertEqual(found, ["dialects/plateau/convert_citygml2_to3_iur4.py", "scripts/citygml_xml.py", "scripts/safe_xml.py"])

    def test_a_malformed_file_is_one_error(self):
        from scripts import safe_xml
        for parse in (lambda: safe_xml.safe_fromstring(b"<a><b></a>"), lambda: list(safe_xml.safe_iterparse(io.BytesIO(b"<a><b></a>"))),
                      lambda: safe_xml.fromstring(b"<a><b></a>")):
            with self.assertRaises(safe_xml.ParseError):
                parse()


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
            # U13: the standard-library parse refuses any DOCTYPE, so no entity is expanded at all
            from scripts import safe_xml
            with self.assertRaises(safe_xml.ParseError):
                preview._extract_buildings(doc)
            with self.assertRaises(safe_xml.ParseError):
                list(X.buildings(doc))



class TestTheGatesRunOnTheStandardLibrary(unittest.TestCase):
    """U13: the analysis gates parse with the standard library (scripts/safe_xml.py); every script
    the CI drivers call imports without lxml. lxml stays for the PLATEAU tools (the bulk
    verifiers and the prefix-keeping writers) until they move into the pack (phase 4)."""

    def test_every_driver_script_imports_without_lxml(self):
        import subprocess
        import sys
        drivers = " ".join((REPO_ROOT / "ci" / name).read_text(encoding="utf-8") for name in
                           ("pr_analysis_main.sh", "topology_gate.sh", "inspection_summary.sh"))
        names = sorted(set(re.findall(r"scripts/([a-z_0-9]+)\.py", drivers)))
        self.assertIn("citygml_lint", names)
        code = ("import importlib, sys; sys.modules['lxml'] = sys.modules['lxml.etree'] = None\n"
                f"for n in {names!r}: importlib.import_module('scripts.' + n)")
        proc = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])

    def test_a_doctype_is_refused_in_every_form(self):
        from scripts import safe_xml
        for doc in (b'<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>', b'<?xml version="1.0"?><!DOCTYPE x SYSTEM "file:///etc/passwd"><x/>',
                    "<!DOCTYPE x><x/>".encode("utf-16")):
            for read in (safe_xml.fromstring, lambda d: list(safe_xml.iterparse(d))):
                with self.subTest(doc=doc[:20]), self.assertRaises(safe_xml.ParseError):
                    read(doc)
        self.assertEqual(safe_xml.fromstring(b'<?xml version="1.0"?><!-- c --><x/>').tag, "x")

if __name__ == "__main__":
    unittest.main()
