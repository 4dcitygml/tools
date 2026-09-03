# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Semantic attribute registry: every registered path exists in that edition's
XSD, every path observed in real PLATEAU data of an edition resolves to a key,
lookups round-trip, and the crosswalk reports renames/splits correctly."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from scripts import semantic_registry as R

REPO_ROOT = Path(__file__).resolve().parent.parent
OBSERVED = json.loads((REPO_ROOT / "tests/fixtures/iur_paths_by_edition.json").read_text(encoding="utf-8"))["paths"]


class SemanticRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = R.load_registry()

    def test_every_registered_uro_segment_exists_in_that_editions_xsd(self) -> None:
        core = set(self.reg["core_segments"])
        for edition, meta in self.reg["editions"].items():
            xsd = (REPO_ROOT / meta["xsd"]).read_text(encoding="utf-8")
            names = set(re.findall(r'name="([A-Za-z0-9_]+)"', xsd))
            for key, attr in self.reg["attributes"].items():
                for path in [attr["paths"].get(edition)] + attr.get("aliases", {}).get(edition, []):
                    if not path:
                        continue
                    for segment in path.strip("/").split("/"):
                        if segment in core:
                            continue
                        self.assertIn(segment, names, f"{key}: {segment} not in {edition} XSD")

    def test_observed_paths_are_covered(self) -> None:
        for edition, paths in OBSERVED.items():
            unresolved = [p for p in paths if R.key_for(p, edition) is None]
            self.assertEqual(unresolved, [], f"{edition}: observed paths without a registry key")

    def test_lookup_round_trip_and_alias(self) -> None:
        for edition in R.editions():
            for key, path in R.attributes_for(edition).items():
                self.assertEqual(R.key_for(path, edition), key)
        self.assertEqual(R.key_for("/buildingDisasterRiskAttribute/BuildingRiverFloodingRiskAttribute/rankOrg", "iur-2.0"), "risk.river.rank")
        self.assertEqual(R.key_for("/stringAttribute[@name=地区計画]/value[0]", "iur-3.2"), "generic.string")
        self.assertIsNone(R.path_for("quality.geometrySrcDesc.lod0", "iur-2.0"))
        self.assertEqual(R.path_for("quality.geometrySrcDesc", "iur-2.0"), "/buildingDataQualityAttribute/BuildingDataQualityAttribute/geometrySrcDesc")

    def test_crosswalk_reports_renames_and_splits(self) -> None:
        rows = {r["key"]: r for r in R.crosswalk("iur-3.0", "iur-3.1")}
        self.assertEqual(rows["risk.river.rank"]["relation"], "renamed")
        self.assertTrue(rows["quality.geometrySrcDesc"]["relation"].startswith("removed (split:"))
        self.assertEqual(rows["quality.geometrySrcDesc.lod0"]["relation"], "added (from quality.geometrySrcDesc)")
        self.assertEqual(rows["building.usage"]["relation"], "same")
        same_20_32 = [r for r in R.crosswalk("iur-2.0", "iur-3.2") if r["relation"] == "same"]
        self.assertGreater(len(same_20_32), 20)

    def test_edition_detection_and_stable_id(self) -> None:
        self.assertEqual(R.detect_edition((REPO_ROOT / "tests/fixtures/iur20_building.gml").read_bytes()), "iur-2.0")
        self.assertEqual(R.detect_edition((REPO_ROOT / "tests/fixtures/base.gml").read_bytes()), "iur-3.0")
        self.assertEqual(self.reg["attributes"]["building.id"]["role"], "stable_id")
        self.assertEqual(R.family_of("building.id"), "source_identity")

    def test_families_match_the_yearly_planner(self) -> None:
        from scripts.plan_yearly_citygml_transition import attribute_family
        for key, attr in self.reg["attributes"].items():
            for edition, path in attr["paths"].items():
                planner = attribute_family(path.replace("[@name]", "[@name=x]"))
                self.assertEqual(planner, attr["family"], f"{key} ({edition}): registry {attr['family']} vs planner {planner}")


if __name__ == "__main__":
    unittest.main()
