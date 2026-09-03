#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Positive/negative tests for plateau_lint (PLATEAU convention-layer lint: sentinels/plausibility/codelist consistency)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import plateau_lint  # noqa: E402
from scripts.citygml_lint import run_lint  # noqa: E402

SQUARE = "0 0 0 0 1 0 1 1 0 1 0 0 0 0 0"


def bldg(bid, height=None, storeys=None, usage=None, codespace="../../codelists/Building_usage.xml"):
    parts = [f'<bldg:Building gml:id="{bid}">']
    if height is not None:
        parts.append(f'<bldg:measuredHeight uom="m">{height}</bldg:measuredHeight>')
    if storeys is not None:
        parts.append(f"<bldg:storeysAboveGround>{storeys}</bldg:storeysAboveGround>")
    if usage is not None:
        parts.append(f'<bldg:usage codeSpace="{codespace}">{usage}</bldg:usage>')
    parts.append("<bldg:lod0FootPrint><gml:MultiSurface><gml:surfaceMember>"
                 "<gml:Polygon><gml:exterior><gml:LinearRing>"
                 f"<gml:posList>{SQUARE}</gml:posList>"
                 "</gml:LinearRing></gml:exterior></gml:Polygon>"
                 "</gml:surfaceMember></gml:MultiSurface></bldg:lod0FootPrint>")
    parts.append("</bldg:Building>")
    return "".join(parts)


def wrap(*bodies):
    inner = "".join(f"<core:cityObjectMember>{b}</core:cityObjectMember>" for b in bodies)
    return ('<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            'xmlns:gml="http://www.opengis.net/gml">'
            f"{inner}</core:CityModel>").encode("utf-8")


def lint_bytes(raw):
    return run_lint(raw, "t.gml", plateau_lint.check_building)


def warncodes(result):
    return {w["code"] for b in result["buildings"] for w in b["warnings"]}


class TestPlateauPlausibility(unittest.TestCase):
    def test_clean(self):
        res = lint_bytes(wrap(bldg("b", height=10, storeys=3)))
        self.assertEqual((res["n_errors"], res["n_warnings"]), (0, 0))

    def test_nonpositive_height_warning(self):
        res = lint_bytes(wrap(bldg("b", height=-5)))
        self.assertIn("nonpositive_height", warncodes(res))
        self.assertEqual(res["n_errors"], 0)  # PLATEAU layer is non-blocking

    def test_height_sentinel_ignored(self):
        self.assertNotIn("nonpositive_height", warncodes(lint_bytes(wrap(bldg("b", height=-9999)))))

    def test_storeys_sentinel_ignored(self):
        self.assertNotIn("storeys_out_of_range", warncodes(lint_bytes(wrap(bldg("b", storeys=9999)))))

    def test_storeys_out_of_range(self):
        self.assertIn("storeys_out_of_range", warncodes(lint_bytes(wrap(bldg("b", storeys=500)))))

    def test_height_out_of_range(self):
        self.assertIn("height_out_of_range", warncodes(lint_bytes(wrap(bldg("b", height=500)))))

    def test_no_errors_ever(self):
        # PLATEAU layer emits warnings only (never blocks)
        res = lint_bytes(wrap(bldg("b", height=-5, storeys=500)))
        self.assertEqual(res["n_errors"], 0)


# Codelist in the same format as real (PLATEAU) data. 411 = residential (valid), 461 = unknown (the canonical "unknown").
CODELIST = """<?xml version="1.0" encoding="UTF-8"?>
<gml:Dictionary xmlns:gml="http://www.opengis.net/gml" gml:id="cl">
  <gml:dictionaryEntry><gml:Definition gml:id="d1">
    <gml:description>住宅</gml:description><gml:name>411</gml:name>
  </gml:Definition></gml:dictionaryEntry>
  <gml:dictionaryEntry><gml:Definition gml:id="d2">
    <gml:description>不明</gml:description><gml:name>461</gml:name>
  </gml:Definition></gml:dictionaryEntry>
</gml:Dictionary>
"""


class TestCodelistCheck(unittest.TestCase):
    """Codelist consistency (invalid_code). Reproduces the PLATEAU directory layout
    (<root>/codelists/*.xml and <root>/udx/bldg/*.gml; codeSpace is a relative path from the gml file)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "codelists").mkdir()
        (root / "codelists" / "Building_usage.xml").write_text(CODELIST, encoding="utf-8")
        self.gml_path = root / "udx" / "bldg" / "t.gml"
        self.gml_path.parent.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def lint(self, raw):
        return run_lint(raw, str(self.gml_path), plateau_lint.check_fn_for(self.gml_path))

    def test_valid_code_no_warning(self):
        res = self.lint(wrap(bldg("b", usage="411")))
        self.assertNotIn("invalid_code", warncodes(res))

    def test_unknown_code_allowed(self):
        # The "unknown" code is the canonical "unknown" = not an anomaly
        res = self.lint(wrap(bldg("b", usage="461")))
        self.assertNotIn("invalid_code", warncodes(res))

    def test_invalid_code_warning(self):
        res = self.lint(wrap(bldg("b", usage="999")))
        self.assertIn("invalid_code", warncodes(res))
        self.assertEqual(res["n_errors"], 0)  # convention layer = non-blocking

    def test_missing_codelist_skipped(self):
        # Referenced codelist missing = unresolvable -> excluded from checking (no false positives)
        res = self.lint(wrap(bldg("b", usage="999", codespace="../../codelists/NoSuchList.xml")))
        self.assertNotIn("invalid_code", warncodes(res))

    def test_external_url_skipped(self):
        res = self.lint(wrap(bldg("b", usage="999", codespace="http://example.com/cl.xml")))
        self.assertNotIn("invalid_code", warncodes(res))

    def test_without_path_context_skipped(self):
        # Without a file path (plain check_building), skip codelist checking (backward compatibility)
        res = lint_bytes(wrap(bldg("b", usage="999")))
        self.assertNotIn("invalid_code", warncodes(res))


if __name__ == "__main__":
    unittest.main()
