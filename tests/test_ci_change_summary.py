#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for ci_change_summary (W2 / feature list D-2 and A-8).

Clean-room tests written from the functional requirements, not a copy of the old implementation.
Run: python -m unittest tests.test_ci_change_summary
"""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.ci_change_summary import SUMMARY_MARKER, render_ci, render_markdown
from scripts.diff_citygml import diff_files

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _result(new: str, buildings: list[dict], **counts) -> dict:
    summary = {"added": 0, "deleted": 0, "modified": 0, "unchanged": 0}
    summary.update(counts)
    return {"old": "old.gml", "new": new, "summary": summary, "buildings": buildings}


class TestRenderMarkdown(unittest.TestCase):
    def test_marker_is_first_line_and_distinct_from_preview(self) -> None:
        md = render_markdown(_result("f.gml", []))
        self.assertTrue(md.startswith(SUMMARY_MARKER))
        # Must differ from preview.yml's marker (so it upserts as a separate comment)
        self.assertNotEqual(SUMMARY_MARKER, "<!-- cesium-building-preview -->")

    def test_no_changes_message(self) -> None:
        md = render_markdown(_result("f.gml", []))
        self.assertIn("No buildings were changed", md)

    def test_modified_attribute_table(self) -> None:
        b = {
            "id": "bldg_x",
            "status": "modified",
            "geometry_changed": False,
            "attribute_diffs": [{"path": "/storeysAboveGround", "old": "9999", "new": "4"}],
        }
        md = render_markdown(_result("prA.gml", [b], modified=1))
        self.assertIn("`prA.gml`", md)
        self.assertIn("| `/storeysAboveGround` | `9999` | `4` |", md)
        self.assertIn("**Geometry**: unchanged", md)

    def test_geometry_only_change_notes_no_attr(self) -> None:
        b = {"id": "bldg_x", "status": "modified", "geometry_changed": True, "attribute_diffs": []}
        md = render_markdown(_result("prB.gml", [b], modified=1))
        self.assertIn("**Geometry**: changed ⚠️", md)
        self.assertIn("none (geometry-only change)", md)

    def test_attribute_add_remove_renders_dash(self) -> None:
        b = {
            "id": "bldg_x",
            "status": "modified",
            "geometry_changed": False,
            "attribute_diffs": [{"path": "/usage", "old": None, "new": "402"}],
        }
        md = render_markdown(_result("f.gml", [b], modified=1))
        self.assertIn("| `/usage` | — | `402` |", md)

    def test_added_deleted_id_lists(self) -> None:
        buildings = [
            {"id": "new1", "status": "added"},
            {"id": "old1", "status": "deleted"},
        ]
        md = render_markdown(_result("f.gml", buildings, added=1, deleted=1))
        self.assertIn("🟢 Added (1 building(s))", md)
        self.assertIn("- `new1`", md)
        self.assertIn("🔴 Deleted (1 building(s))", md)
        self.assertIn("- `old1`", md)

    def test_preview_url_optional_footer(self) -> None:
        self.assertNotIn("preview", render_markdown(_result("f.gml", [])))
        md = render_markdown(_result("f.gml", []), preview_url="https://example/#abc")
        self.assertIn("https://example/#abc", md)

    def test_attr_row_truncation(self) -> None:
        diffs = [{"path": f"/p{i}", "old": "0", "new": "1"} for i in range(150)]
        b = {"id": "b", "status": "modified", "geometry_changed": False, "attribute_diffs": diffs}
        md = render_markdown(_result("f.gml", [b], modified=1))
        self.assertIn("50 more omitted", md)  # 100 cap -> 50 omitted


class TestRenderCi(unittest.TestCase):
    """render_ci: multiple files into one comment (W5a)."""

    def test_empty_returns_empty_string(self) -> None:
        self.assertEqual(render_ci([]), "")  # no changed files -> post nothing

    def test_single_file_matches_single_render(self) -> None:
        b = {"id": "x", "status": "modified", "geometry_changed": True, "attribute_diffs": []}
        result = _result("a.gml", [b], modified=1)
        self.assertEqual(render_ci([result]), render_markdown(result))

    def test_multi_file_lists_each(self) -> None:
        b1 = {"id": "x", "status": "modified", "geometry_changed": True, "attribute_diffs": []}
        b2 = {"id": "y", "status": "added"}
        md = render_ci([_result("a.gml", [b1], modified=1), _result("b.gml", [b2], added=1)])
        self.assertIn("Changed files: 2", md)
        self.assertIn("`a.gml`", md)
        self.assertIn("`b.gml`", md)
        self.assertTrue(md.startswith(SUMMARY_MARKER))
        self.assertEqual(md.count(SUMMARY_MARKER), 1)  # exactly one marker


class TestEndToEndWithFixtures(unittest.TestCase):
    """Run W1->W2 with real-data fixtures."""

    def test_prA_summary(self) -> None:
        r = diff_files(FIXTURES / "base.gml", FIXTURES / "prA_attr.gml")
        md = render_markdown(r, preview_url="https://viewer/#target")
        self.assertIn("🟡 Modified", md)
        self.assertIn("`/storeysAboveGround`", md)
        self.assertIn("`9999`", md)
        self.assertIn("`4`", md)
        self.assertIn("**Geometry**: unchanged", md)  # geometry unchanged
        self.assertIn("https://viewer/#target", md)

    def test_prB_summary(self) -> None:
        r = diff_files(FIXTURES / "base.gml", FIXTURES / "prB_geom.gml")
        md = render_markdown(r)
        self.assertIn("**Geometry**: changed ⚠️", md)  # geometry changed
        self.assertIn("`/measuredHeight`", md)


if __name__ == "__main__":
    unittest.main()
