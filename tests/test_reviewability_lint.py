#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for reviewability_lint (W3 / feature list D-1).

Clean-room tests from the functional requirements; not a copy of the old implementation.
Run: python -m unittest tests.test_reviewability_lint
"""
from __future__ import annotations

import unittest

from scripts.citygml_constants import LINT_MARKER
from scripts.diff_citygml import load_buildings
from scripts.reviewability_lint import analyze_file, render_markdown
from tests.test_diff_citygml import _building, _model


def _maps(old_members: list[str], new_members: list[str]):
    old = load_buildings(_model(*old_members).encode("utf-8"))
    new = load_buildings(_model(*new_members).encode("utf-8"))
    return old, new


def _types(result: dict) -> list[str]:
    return [w["type"] for w in result["warnings"]]


class TestWarnings(unittest.TestCase):
    def test_no_warning_on_small_modify(self) -> None:
        old, new = _maps([_building("b1", storeys="1")], [_building("b1", storeys="2")])
        r = analyze_file(old, new, "f.gml")
        self.assertEqual(r["warnings"], [])
        self.assertEqual(r["counts"], {"added": 0, "deleted": 0, "modified": 1})

    def test_lifecycle_added(self) -> None:
        old, new = _maps(
            [_building("b1")],
            [_building("b1"), _building("brand_new", poslist="9 9 9 8 8 8")],
        )
        r = analyze_file(old, new, "f.gml")
        self.assertEqual(_types(r), ["lifecycle_added"])
        self.assertEqual(r["warnings"][0]["id"], "brand_new")

    def test_lifecycle_deleted(self) -> None:
        old, new = _maps([_building("b1"), _building("gone")], [_building("b1")])
        r = analyze_file(old, new, "f.gml")
        self.assertEqual(_types(r), ["lifecycle_deleted"])

    def test_id_change_suspected_when_attrs_and_geom_identical(self) -> None:
        # old_x removed and new_y added, but attributes/geometry identical -> suspected unnecessary ID change
        common = _building("b1")
        old, new = _maps(
            [common, _building("old_x", storeys="3", height="10.0", poslist="1 1 1 2 2 2")],
            [common, _building("new_y", storeys="3", height="10.0", poslist="1 1 1 2 2 2")],
        )
        r = analyze_file(old, new, "f.gml")
        self.assertEqual(_types(r), ["id_change"])
        self.assertEqual((r["warnings"][0]["old_id"], r["warnings"][0]["new_id"]), ("old_x", "new_y"))

    def test_rebuild_is_lifecycle_not_id_change(self) -> None:
        # Rebuild: old ID gone, new ID created, geometry differs -> both lifecycle warnings, not id_change
        old, new = _maps(
            [_building("old_x", poslist="1 1 1 2 2 2")],
            [_building("new_y", poslist="5 5 5 6 6 9")],
        )
        r = analyze_file(old, new, "f.gml")
        self.assertEqual(set(_types(r)), {"lifecycle_added", "lifecycle_deleted"})
        self.assertNotIn("id_change", _types(r))


class TestRender(unittest.TestCase):
    def test_marker_and_clean_message(self) -> None:
        old, new = _maps([_building("b1", storeys="1")], [_building("b1", storeys="2")])
        md = render_markdown([analyze_file(old, new, "f.gml")])
        self.assertTrue(md.startswith(LINT_MARKER))
        self.assertIn("No operational warnings", md)
        self.assertIn("| `f.gml` | 0 | 0 | 1 |", md)  # change breakdown table

    def test_large_change_warning_over_threshold(self) -> None:
        olds = [_building(f"b{i}", storeys="1") for i in range(6)]
        news = [_building(f"b{i}", storeys="2") for i in range(6)]
        old, new = _maps(olds, news)
        files = [analyze_file(old, new, "f.gml")]
        md = render_markdown(files, threshold=5)
        self.assertIn("Large change", md)
        self.assertIn("6 changed buildings exceed the threshold of 5", md)

    def test_large_change_counts_across_files(self) -> None:
        # 6 buildings total across 2 files -> exceeds threshold of 5 (judged over the whole PR)
        o1, n1 = _maps([_building(f"a{i}", storeys="1") for i in range(3)],
                       [_building(f"a{i}", storeys="2") for i in range(3)])
        o2, n2 = _maps([_building(f"b{i}", storeys="1") for i in range(3)],
                       [_building(f"b{i}", storeys="2") for i in range(3)])
        files = [analyze_file(o1, n1, "a.gml"), analyze_file(o2, n2, "b.gml")]
        md = render_markdown(files, threshold=5)
        self.assertIn("Large change", md)
        self.assertIn("6 changed buildings", md)

    def test_id_change_and_lifecycle_render(self) -> None:
        old, new = _maps(
            [_building("old_x", poslist="1 1 1 2 2 2"), _building("bye")],
            [_building("new_y", poslist="1 1 1 2 2 2"), _building("hello", poslist="7 7 7 8 8 8")],
        )
        md = render_markdown([analyze_file(old, new, "f.gml")])
        self.assertIn("Suspected unnecessary ID change", md)
        self.assertIn("`old_x` → `new_y`", md)
        self.assertIn("Reason required for deleted building", md)  # bye
        self.assertIn("Reason required for added building", md)  # hello


if __name__ == "__main__":
    unittest.main()
