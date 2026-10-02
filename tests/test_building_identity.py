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

from scripts.building_identity import (IdentityRule, building_id, building_spans, citymodel_close, member_markers,
                                       municipality, municipality_values, replace_stable_id, rule_from_config, stable_id)

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


class TestMemberValues(unittest.TestCase):
    """One reader of buildingID and uro:city for the gates, the history index, suggest_commit and
    extract_municipality (S19 A6, A11)."""

    def test_building_id(self):
        self.assertEqual(building_id(PLATEAU), "13101-bldg-1")
        self.assertEqual(building_id(b'<uro:buildingID codeSpace="x"> 13101-bldg-2 </uro:buildingID>'), "13101-bldg-2")
        self.assertIsNone(building_id(CITYGML10))

    def test_a_member_naming_two_cities_has_no_municipality(self):
        one = b"<uro:city>13101</uro:city><uro:city>13101</uro:city>"
        two = b"<uro:city>13101</uro:city><uro:city>13102</uro:city>"
        self.assertEqual(municipality(one), "13101")
        self.assertEqual(municipality_values(two), {"13101", "13102"})
        self.assertIsNone(municipality(two))          # the gate used to take the first
        self.assertIsNone(municipality(b"<uro:city> </uro:city>"))


class TestReplaceStableId(unittest.TestCase):
    """An identity commit changes the stable ID where the city keeps it, and nothing else."""

    def test_each_rule_replaces_its_own_value(self):
        span = CITYGML10.split(b"\n")[2]   # DEBY_LOD2_59812 with BIN 1036448
        self.assertEqual(replace_stable_id(span, "1036448", "1099999", IdentityRule("gen:BIN")),
                         span.replace(b">1036448<", b">1099999<"))
        self.assertEqual(replace_stable_id(span, "DEBY_LOD2_59812", "DEBY_X", IdentityRule("gml:id")),
                         span.replace(b'"DEBY_LOD2_59812"', b'"DEBY_X"'))
        self.assertEqual(replace_stable_id(PLATEAU, "13101-bldg-1", "13101-bldg-9"),
                         PLATEAU.replace(b">13101-bldg-1<", b">13101-bldg-9<"))
        self.assertEqual(replace_stable_id(span, "nope", "x", IdentityRule("gen:BIN")), span)


class TestTrailers(unittest.TestCase):
    def test_one_parser_for_the_gate_the_topology_scope_the_history_and_the_hub(self):
        # A5/S19: topology_scope and the hub had their own regexes (the hub's took only the first word)
        import re
        from scripts import building_identity, topology_scope
        message = "Update\n\nBuilding: 13101-bldg-1\nCreated-By: x/1.0\nBuilding-Added: 13101-bldg-2 \n"
        self.assertEqual(building_identity.trailer_buildings(building_identity.trailers(message)),
                         ["13101-bldg-1", "13101-bldg-2"])
        self.assertEqual(topology_scope.building_ids(message), {"13101-bldg-1", "13101-bldg-2"})
        root = Path(__file__).resolve().parents[1]
        copies = [p.relative_to(root).as_posix() for p in [*root.glob("scripts/*.py"), *root.glob("tools/**/*.py")]
                  if p.name != "building_identity.py"
                  and re.search(r"Building\|Building-Added|Building-Added\|Building-Deleted", p.read_text(encoding="utf-8"))]
        self.assertEqual(copies, [])


if __name__ == "__main__":
    unittest.main()
