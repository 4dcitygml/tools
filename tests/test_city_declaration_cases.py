# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Safety-net cases for the city declaration (v2.0.0 phase 1), recorded before the one reader
(F1) replaced the eight: a city without `building_id`, without a dialect, with a CRS the tools
do not know, and a PR that changes `building_id`. Each test states today's behaviour; the
batch that decides a new default (phase 1-2) changes the expectation and names the decision."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import citygml_dialect
from scripts.commit_building_scope import inspect_range
from tests.test_commit_building_scope import GitRepo, _gml


class DeclarationCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = GitRepo(Path(self.tmp.name), declaration=None)

    def tearDown(self):
        self.tmp.cleanup()

    def declare(self, declaration: "dict | None", message: str) -> str:
        path = self.repo.root / "4dcitygml.json"
        if declaration is None:
            path.unlink(missing_ok=True)
            self.repo.run("add", "-A")
        else:
            path.write_text(json.dumps(declaration), encoding="utf-8")
            self.repo.run("add", "4dcitygml.json")
        self.repo.run("commit", "-q", "--allow-empty", "-m", message)
        return self.repo.run("rev-parse", "HEAD")

    def test_a_city_without_building_id(self):
        # 1-2 (decided): a city that declares no building_id has no dialect: its stable ID is the
        # gml:id, not PLATEAU's uro:buildingID (before: "Building: 13101-bldg-1" passed)
        base = self.repo.commit_gml(1, 1, "baseline\n\nChange-Type: source-baseline")
        head = self.repo.commit_gml(2, 1, "update one\n\nBuilding: gml-13101-bldg-1")
        result = inspect_range(self.repo.root, base, head)
        self.assertTrue(result[0].ok, result[0].errors)
        self.assertEqual(result[0].changed_ids, {"gml-13101-bldg-1"})
        head = self.repo.commit_gml(3, 1, "update again\n\nBuilding: 13101-bldg-1")
        self.assertFalse(inspect_range(self.repo.root, base, head)[-1].ok)

    def test_a_city_without_a_dialect(self):
        # 1-2 (decided; X1: the pack, not an edition): without the plateau dialect the i-UR kinds do not
        # apply (before: a schema-update commit passed in any city); the identity kinds work everywhere (S11)
        self.declare({"repo": "o/r", "building_id": {"type": "gml:id"}}, "declare")
        base = self.repo.commit_gml(1, 1, "baseline\n\nChange-Type: source-baseline")
        (self.repo.root / "codelists").mkdir()
        (self.repo.root / "codelists" / "Building_usage.xml").write_text("<x/>", encoding="utf-8")
        self.repo.run("add", "codelists")
        self.repo.run("commit", "-q", "-m", "Add code lists\n\nChange-Type: schema-update")
        result = inspect_range(self.repo.root, base, self.repo.run("rev-parse", "HEAD"))
        self.assertEqual(result[0].change_type, "schema-update")
        self.assertIn("declares no dialect with it", " ".join(result[0].errors))
        iur = self.declare({"repo": "o/r", "building_id": {"type": "gml:id"}, "dialect": "plateau"}, "declare i-UR")
        self.repo.run("commit", "-q", "--allow-empty", "-m", "Add code lists\n\nChange-Type: schema-update")
        self.assertTrue(inspect_range(self.repo.root, iur, self.repo.run("rev-parse", "HEAD"))[0].ok)

    def test_a_crs_the_tools_do_not_know(self):
        # 1-2 (decided): a missing or unknown CRS gives no map and no preview, never "treat as
        # lat/lon"; a geographic CRS keeps its values (before: unknown and lat/lon both read as lat/lon)
        self.assertIsNone(citygml_dialect.crs_transformer("EPSG:31468"))   # DHDN / 3-degree Gauss-Kruger 4
        self.assertIsNone(citygml_dialect.crs_transformer(""))
        self.assertEqual(citygml_dialect.crs_transformer("EPSG:4326")(48.1, 11.5), (48.1, 11.5))
        from scripts import extract_building_preview as preview
        doc = (b'<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
               b'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml">'
               b'<gml:boundedBy><gml:Envelope srsName="EPSG:31468"><gml:lowerCorner>4468000 5333000 0</gml:lowerCorner>'
               b'<gml:upperCorner>4468100 5333100 10</gml:upperCorner></gml:Envelope></gml:boundedBy>'
               b'<core:cityObjectMember><bldg:Building gml:id="b1"><bldg:lod0RoofEdge><gml:MultiSurface>'
               b'<gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing><gml:posList>'
               b'4468000 5333000 0 4468010 5333000 0 4468010 5333010 0 4468000 5333000 0'
               b'</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>'
               b'</gml:MultiSurface></bldg:lod0RoofEdge></bldg:Building></core:cityObjectMember></core:CityModel>')
        self.assertEqual(preview._extract_buildings(doc), {})               # no preview

    def test_a_pr_that_changes_building_id(self):
        # 1-2 (decided, U1): the declaration is read at the PR's base, and a PR that changes the
        # dialect (dialect, building_id, data_dirs, crs) is configuration only. Before, each commit
        # was judged under the rule committed with it, so a PR could switch the rule and use it.
        self.declare({"repo": "o/r", "building_id": {"type": "uro:buildingID"}}, "declare")
        base = self.repo.commit_gml(1, 1, "baseline\n\nChange-Type: source-baseline")
        self.declare({"repo": "o/r", "building_id": {"type": "gml:id"}}, "Use gml:id")
        head = self.repo.commit_gml(2, 1, "update one\n\nBuilding: gml-13101-bldg-1")
        result = inspect_range(self.repo.root, base, head)
        self.assertIn("changes how the city's data is read (building_id", " ".join(result[0].errors))
        self.assertIn("13101-bldg-1", result[1].changed_ids)            # judged by the base's rule
        alone = self.declare({"repo": "o/r", "building_id": {"type": "gml:id"}, "lang": "de"}, "Declare alone")
        self.assertEqual([r.ok for r in inspect_range(self.repo.root, head, alone)], [True])   # no dialect change
        config_only = self.declare({"repo": "o/r", "building_id": {"type": "gen:BIN"}}, "Switch the rule")
        self.assertEqual([r.ok for r in inspect_range(self.repo.root, alone, config_only)], [True])

if __name__ == "__main__":
    unittest.main()
