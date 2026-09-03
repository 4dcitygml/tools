#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the val3dity topology gate (diff-based): regression detection and output.

Only exercises the pure logic (find_regressions / render) that has no dependency on
external tools (val3dity/citygml-tools/pyproj). E2E with the tools is separate (local manual; CI live in #20).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.val3dity_gate import find_regressions, render  # noqa: E402
from scripts.citygml_constants import VAL3DITY_MARKER  # noqa: E402


def V(valid, codes=()):
    return {"valid": valid, "codes": list(codes)}


class TestFindRegressions(unittest.TestCase):
    def test_new_invalid_building_is_regression(self):
        # A building absent from base was added as invalid = regression.
        regs = find_regressions({"b1": V(False, ["104"])}, {}, "f.gml")
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["id"], "b1")
        self.assertEqual(regs[0]["was"], "new")
        self.assertEqual(regs[0]["codes"], ["104"])

    def test_valid_to_invalid_is_regression(self):
        # A building valid in base became invalid = regression.
        regs = find_regressions({"b1": V(False, ["306"])}, {"b1": V(True)}, "f.gml")
        self.assertEqual([r["id"] for r in regs], ["b1"])
        self.assertEqual(regs[0]["was"], "valid in base")

    def test_preexisting_defect_is_not_regression(self):
        # A building already invalid in base (pre-existing PLATEAU defect) is not a regression = the core of the diff-based approach.
        regs = find_regressions({"b1": V(False, ["104"])}, {"b1": V(False, ["104"])}, "f.gml")
        self.assertEqual(regs, [])

    def test_valid_change_is_not_regression(self):
        regs = find_regressions({"b1": V(True)}, {"b1": V(True)}, "f.gml")
        self.assertEqual(regs, [])

    def test_fix_invalid_to_valid_is_not_regression(self):
        # Fixing an existing invalid to valid is not a regression (a welcome change).
        regs = find_regressions({"b1": V(True)}, {"b1": V(False, ["104"])}, "f.gml")
        self.assertEqual(regs, [])

    def test_mixed_only_flags_regressions(self):
        head = {"ok": V(True), "new_bad": V(False, ["204"]),
                "old_bad": V(False, ["104"]), "broke": V(False, ["306"])}
        base = {"ok": V(True), "old_bad": V(False, ["104"]), "broke": V(True)}
        ids = sorted(r["id"] for r in find_regressions(head, base, "f.gml"))
        self.assertEqual(ids, ["broke", "new_bad"])


class TestRender(unittest.TestCase):
    def test_clean_has_marker_and_check(self):
        out = render({"regressions": [], "checked": 3, "skipped": 0})
        self.assertTrue(out.startswith(VAL3DITY_MARKER))
        self.assertIn("✅", out)

    def test_regression_lists_building_and_code(self):
        out = render({"regressions": [
            {"file": "f.gml", "id": "bldg_x", "codes": ["104"], "was": "valid in base"}],
            "checked": 1, "skipped": 0})
        self.assertTrue(out.startswith(VAL3DITY_MARKER))
        self.assertIn("bldg_x", out)
        self.assertIn("104", out)
        self.assertIn("ring self-intersection", out)


if __name__ == "__main__":
    unittest.main()
