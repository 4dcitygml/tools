# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Code-list crosswalks: label matching (exact / refined / dropped / added), file
name resolution per edition, the 1:1 resolution rule, and the shipped
iur-3.0 -> iur-3.1 crosswalk's known facts."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import codelist_crosswalk as X

REPO_ROOT = Path(__file__).resolve().parent.parent


def dictionary(entries: dict[str, str]) -> str:
    items = "".join(f"<gml:dictionaryEntry><gml:Definition gml:id=\"id{c}\"><gml:description>{l}</gml:description><gml:name>{c}</gml:name></gml:Definition></gml:dictionaryEntry>" for c, l in entries.items())
    return f'<gml:Dictionary xmlns:gml="http://www.opengis.net/gml">{items}</gml:Dictionary>'


class CrosswalkTest(unittest.TestCase):
    def test_label_matching(self) -> None:
        old = {"1": "現地測量", "5": "空中写真測量", "9": "現地調査", "3": "廃止項目"}
        new = {"000": "公共測量成果", "101": "（公共測量ではない）現地測量の測量成果", "103": "（公共測量ではない）空中写真測量の測量成果", "801": "現地調査"}
        m = X.match_codes(old, new)
        self.assertEqual(m["codes"]["9"]["relation"], "exact"); self.assertEqual(m["codes"]["9"]["to"], ["801"])
        self.assertEqual(m["codes"]["5"]["relation"], "refined"); self.assertEqual(m["codes"]["5"]["to"], ["103"])
        self.assertEqual(m["codes"]["3"]["relation"], "dropped")
        self.assertEqual(set(m["added"]), {"000"})

    def test_resolve_only_accepts_one_to_one(self) -> None:
        cw = {"lists": {"k": {"codes": {"9": {"relation": "exact", "to": ["801"]}, "5": {"relation": "refined", "to": ["103", "000"]},
                                          "7": {"relation": "refined", "to": ["105"], "confidence": "reviewed"}, "3": {"relation": "dropped", "to": []}}}}}
        self.assertEqual(X.resolve(cw, "k", "9"), "801")
        self.assertIsNone(X.resolve(cw, "k", "5"))       # 1:n needs a reviewer
        self.assertEqual(X.resolve(cw, "k", "7"), "105")  # reviewed single target
        self.assertIsNone(X.resolve(cw, "k", "3")); self.assertIsNone(X.resolve(cw, "k", "x"))

    def test_file_resolution_follows_the_edition_and_the_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "BuildingDataQualityAttribute_geometrySrcDesc.xml").write_text(dictionary({"1": "a"}), encoding="utf-8")
            (d / "DataQualityAttribute_geometrySrcDesc.xml").write_text(dictionary({"1": "a"}), encoding="utf-8")
            self.assertEqual(X.codelist_file_for("quality.geometrySrcDesc", "iur-3.0", d), "BuildingDataQualityAttribute_geometrySrcDesc.xml")
            self.assertEqual(X.codelist_file_for("quality.geometrySrcDesc.lod0", "iur-3.1", d), "DataQualityAttribute_geometrySrcDesc.xml")
        self.assertEqual(X.codelist_file_candidates("building.usage", "iur-3.2")[0], "Building_usage.xml")
        self.assertEqual(X.codelist_file_candidates("detail.landUseType", "iur-3.2")[1], "Common_landUseType.xml")

    def test_shipped_crosswalk_3_0_to_3_1(self) -> None:
        cw = json.loads((REPO_ROOT / "semantics/codelists/iur-3.0__iur-3.1.json").read_text(encoding="utf-8"))
        self.assertEqual((cw["from"], cw["to"]), ("iur-3.0", "iur-3.1"))
        geo = cw["lists"]["quality.geometrySrcDesc"]["codes"]
        self.assertEqual(geo["9"]["relation"], "exact"); self.assertEqual(geo["9"]["to"], ["801"])
        self.assertEqual(geo["5"]["relation"], "refined")  # 空中写真測量: public (000) or non-public (103) -> reviewer
        self.assertNotIn("building.city", cw["lists"])     # identity code lists are never carried
        self.assertEqual(X.resolve(cw, "quality.thematicSrcDesc", "1"), "201")


if __name__ == "__main__":
    unittest.main()
