# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts/plan_yearly_citygml_transition.py"
SPEC = importlib.util.spec_from_file_location("plan_yearly_citygml_transition", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def match(**overrides):
    result = {
        "old_id": "old",
        "new_id": "new",
        "changed_paths": [],
        "hausdorff_m": 0.0,
        "geometry_equal_1mm": True,
        "lod_changed": False,
        "id_changed": False,
    }
    result.update(overrides)
    return result


class YearlyTransitionPlanTest(unittest.TestCase):
    def test_path_index_is_normalized(self):
        self.assertEqual(MODULE.base_path("/foo/bar[12]"), "/foo/bar")
        self.assertEqual(MODULE.base_path("/foo[@name=x]/bar"), "/foo[@name=x]/bar")

    def test_review_priority_keeps_one_building_in_one_lane(self):
        lane, record = MODULE.classify_match(
            match(
                changed_paths=[
                    "/address/Address/LocalityName[0]",
                    "/bldgDataQualityAttribute/DataQualityAttribute/srcScaleLod0",
                    "/storeysAboveGround",
                ],
                lod_changed=True,
            ),
            geometry_tolerance_m=0.01,
        )
        self.assertEqual(lane, "geometry_or_lod_review")
        self.assertEqual(
            record["dimensions"],
            {
                "source_metadata": True,
                "address": True,
                "attribute_residual": True,
                "geometry_over_tolerance": False,
                "geometry_fingerprint_changed_1mm": False,
                "lod": True,
                "gml_id_changed": False,
            },
        )

    def test_source_metadata_only_lane(self):
        lane, _record = MODULE.classify_match(
            match(
                changed_paths=[
                    "/buildingDataQualityAttribute/BuildingDataQualityAttribute/geometrySrcDesc"
                ]
            ),
            geometry_tolerance_m=0.01,
        )
        self.assertEqual(lane, "source_metadata_review")

    def test_geometry_tolerance_is_strictly_over_threshold(self):
        within, _ = MODULE.classify_match(match(hausdorff_m=0.01), 0.01)
        over, _ = MODULE.classify_match(match(hausdorff_m=0.010001), 0.01)
        self.assertEqual(within, "unchanged")
        self.assertEqual(over, "geometry_or_lod_review")

    def test_signature_id_is_stable_for_same_canonical_paths(self):
        paths = ("/a", "/b")
        self.assertEqual(MODULE.signature_id(paths), MODULE.signature_id(paths))
        self.assertNotEqual(MODULE.signature_id(paths), MODULE.signature_id(("/a", "/c")))

    def test_attribute_families_are_semantic_and_exhaustive(self):
        self.assertEqual(MODULE.attribute_family("/storeysBelowGround"), "storeys")
        self.assertEqual(
            MODULE.attribute_family("/buildingDetailAttribute/BuildingDetailAttribute/detailedUsage"),
            "usage_class_landuse",
        )
        self.assertEqual(
            MODULE.attribute_family(
                "/bldgDataQualityAttribute/DataQualityAttribute/srcScaleLod0"
            ),
            "source_quality",
        )
        self.assertEqual(MODULE.attribute_family("/notYetClassified"), "other_attributes")

    def test_source_identity_is_source_evidence_only(self):
        self.assertIn("source_identity", MODULE.SOURCE_EVIDENCE_ONLY_FAMILIES)

    def test_pilot_pr_count_does_not_create_empty_remainder(self):
        self.assertEqual(MODULE.pr_counts_with_pilots([0, 1, 2, 100]), (3, 5))


if __name__ == "__main__":
    unittest.main()
