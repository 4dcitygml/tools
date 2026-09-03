#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for plateau_codelists (codelist loading and unknown-code coverage)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.plateau_codelists import (  # noqa: E402
    is_quality_axis,
    is_unknown_label,
    load_codelist,
    quality_tier,
    unknown_codes,
)

_DICT = (
    '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml">'
    "<gml:name>Test</gml:name>"
    '<gml:dictionaryEntry><gml:Definition gml:id="d1">'
    "<gml:description>道路橋</gml:description><gml:name>01</gml:name>"
    "</gml:Definition></gml:dictionaryEntry>"
    '<gml:dictionaryEntry><gml:Definition gml:id="d2">'
    "<gml:description>不明</gml:description><gml:name>99</gml:name>"
    "</gml:Definition></gml:dictionaryEntry>"
    '<gml:dictionaryEntry><gml:Definition gml:id="d3">'
    "<gml:description>その他</gml:description><gml:name>90</gml:name>"
    "</gml:Definition></gml:dictionaryEntry>"
    "</gml:Dictionary>"
)


class TestUnknownLabel(unittest.TestCase):
    def test_unknown_family_true(self):
        for s in ("不明", "不詳", "調査時不明", "Unknown", "unknown value"):
            self.assertTrue(is_unknown_label(s))

    def test_sonota_and_valid_false(self):
        for s in ("その他", "道路橋", "その他施設", "", "住宅"):
            self.assertFalse(is_unknown_label(s))


class TestLoad(unittest.TestCase):
    def test_load_codelist_pairs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "Test.xml"
            p.write_text(_DICT, encoding="utf-8")
            table = load_codelist(p)
            self.assertEqual(table["01"], "道路橋")
            self.assertEqual(table["99"], "不明")

    def test_unknown_codes_excludes_sonota(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Test.xml").write_text(_DICT, encoding="utf-8")
            unk = unknown_codes(Path(d))
            self.assertEqual(unk["Test.xml"], frozenset({"99"}))  # 90 ("other") is not included


class TestQualityTier(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(quality_tier("（公共測量ではない）航空レーザ測量成果"), "measured")
        self.assertEqual(quality_tier("BIMモデル、CADデータ、設計図"), "documented")
        self.assertEqual(quality_tier("衛星写真"), "documented")
        self.assertEqual(quality_tier("疑似テクスチャ"), "provisional")
        self.assertEqual(quality_tier("階高3m×階数による推定値"), "provisional")
        self.assertEqual(quality_tier("取得不可のため一律値（3m）"), "missing")  # "unobtainable" takes precedence
        self.assertEqual(quality_tier("未作成"), "missing")
        self.assertEqual(quality_tier("住宅"), "other")

    def test_is_quality_axis(self):
        self.assertTrue(is_quality_axis("DataQualityAttribute_geometrySrcDesc.xml"))
        self.assertTrue(is_quality_axis("Common_MapLevel.xml"))
        self.assertFalse(is_quality_axis("Building_usage.xml"))


class TestRealCodelist(unittest.TestCase):
    def test_real_bridge_function_has_unknown(self):
        p = REPO_ROOT / "13101_chiyoda-ku_pref_2023_citygml_1_op" / "codelists" / "Bridge_function.xml"
        if not p.exists():
            self.skipTest("codelists 未収録の環境")
        table = load_codelist(p)
        self.assertTrue(any(is_unknown_label(v) for v in table.values()),
                        "Bridge_function に不明コードがあるはず")


if __name__ == "__main__":
    unittest.main()
