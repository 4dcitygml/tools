#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/building_identity.py: the one rule for what identifies a building, in every dialect
(PLATEAU's uro:buildingID, a gml:id city such as Munich, a generic-attribute city such as New
York's BIN), and the same rule as the attribute editor carries."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from scripts.building_identity import (IdentityRule, building_spans, citymodel_close, member_markers,
                                       rule_from_config, stable_id)

REPO_ROOT = Path(__file__).resolve().parents[1]

PLATEAU = (b'<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"><core:cityObjectMember>'
           b'<bldg:Building gml:id="g1"><uro:buildingIDAttribute><uro:BuildingIDAttribute>'
           b'<uro:buildingID>13101-bldg-1</uro:buildingID></uro:BuildingIDAttribute></uro:buildingIDAttribute>'
           b'</bldg:Building></core:cityObjectMember></core:CityModel>')
CITYGML10 = (b'<CityModel xmlns="http://www.opengis.net/citygml/1.0">\n<cityObjectMember>\n'
             b'<bldg:Building gml:id="DEBY_LOD2_59812"><gen:stringAttribute name="BIN"><gen:value>1036448</gen:value>'
             b'</gen:stringAttribute></bldg:Building>\n</cityObjectMember>\n<cityObjectMember>\n'
             b'<bldg:Building gml:id="DEBY_LOD2_2"><gen:stringAttribute name="BIN"><gen:value>1000000</gen:value>'
             b'</gen:stringAttribute></bldg:Building>\n</cityObjectMember>\n</CityModel>\n')


def _attr_module():
    spec = importlib.util.spec_from_file_location("attr_identity_test", REPO_ROOT / "tools" / "attr_editor" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRule(unittest.TestCase):
    def test_config_to_rule(self):
        self.assertEqual(rule_from_config(None), IdentityRule())
        self.assertEqual(rule_from_config({"building_id": {"type": "gml:id"}}), IdentityRule("gml:id"))
        self.assertEqual(rule_from_config({"building_id": {"type": "gen:BIN", "invalid_values": ["1000000"]}}),
                         IdentityRule("gen:BIN", frozenset({"1000000"})))

    def test_stable_id_per_dialect(self):
        spans = building_spans(PLATEAU)
        self.assertEqual(stable_id(PLATEAU[slice(*spans["g1"])], "g1"), "13101-bldg-1")
        spans = building_spans(CITYGML10)
        munich, ny = IdentityRule("gml:id"), IdentityRule("gen:BIN", frozenset({"1000000"}))
        first, second = spans["DEBY_LOD2_59812"], spans["DEBY_LOD2_2"]
        self.assertEqual(stable_id(CITYGML10[slice(*first)], "DEBY_LOD2_59812", munich), "DEBY_LOD2_59812")
        self.assertEqual(stable_id(CITYGML10[slice(*first)], "DEBY_LOD2_59812", ny), "1036448")
        # a placeholder BIN never identifies a building: the gml:id stands in
        self.assertEqual(stable_id(CITYGML10[slice(*second)], "DEBY_LOD2_2", ny), "DEBY_LOD2_2")
        # PLATEAU rule on data without uro:buildingID falls back to the gml:id
        self.assertEqual(stable_id(CITYGML10[slice(*first)], "DEBY_LOD2_59812"), "DEBY_LOD2_59812")

    def test_member_markers_follow_the_file(self):
        self.assertEqual(member_markers(PLATEAU), (b"<core:cityObjectMember>", b"</core:cityObjectMember>"))
        self.assertEqual(member_markers(CITYGML10), (b"<cityObjectMember>", b"</cityObjectMember>"))
        self.assertEqual(sorted(building_spans(CITYGML10)), ["DEBY_LOD2_2", "DEBY_LOD2_59812"])
        self.assertEqual(CITYGML10[citymodel_close(CITYGML10):].strip(), b"</CityModel>")
        self.assertEqual(PLATEAU[citymodel_close(PLATEAU):], b"</core:CityModel>")

    def test_attribute_editor_carries_the_same_rule(self):
        # The editor ships without scripts/, so it keeps its own copy: it must agree on every case.
        attr = _attr_module()
        for raw, cases in ((PLATEAU, [("g1", IdentityRule())]),
                           (CITYGML10, [("DEBY_LOD2_59812", IdentityRule("gml:id")),
                                        ("DEBY_LOD2_59812", IdentityRule("gen:BIN")),
                                        ("DEBY_LOD2_2", IdentityRule("gen:BIN", frozenset({"1000000"}))),
                                        ("DEBY_LOD2_2", IdentityRule())])):
            self.assertEqual(attr.building_spans(raw), building_spans(raw))
            for gml_id, rule in cases:
                span = raw[slice(*building_spans(raw)[gml_id])]
                self.assertEqual(attr.stable_building_id_from_span(span, gml_id, rule.type, rule.invalid_values),
                                 stable_id(span, gml_id, rule), (gml_id, rule))


if __name__ == "__main__":
    unittest.main()
