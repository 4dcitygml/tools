#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for suggest_commit (W7 / per-building traceability).

Embeds the **stable ID (uro:buildingID)** as a `Building:` trailer in building commit messages,
ensuring `git log --grep`/`blame`/`bisect`/`revert` work at building granularity and across rebuilds.
Run: python -m unittest tests.test_suggest_commit
"""
from __future__ import annotations

import unittest

from scripts.suggest_commit import MARKER, build_message, render_comment


class TestBuildMessage(unittest.TestCase):
    def test_single_uses_stable_buildingid_when_resolvable(self):
        # id_map resolves gml:id -> uro:buildingID, so the trailer uses the stable ID.
        msg = build_message(["bldg_A"], [], [], [], "single",
                            id_map={"bldg_A": "13101-bldg-3728"})
        self.assertIn("update(13101-bldg-3728):", msg)
        self.assertIn("Building: 13101-bldg-3728", msg)
        self.assertNotIn("bldg_A", msg)  # gml:id is not emitted (use the stable ID)

    def test_falls_back_to_gmlid_when_no_map(self):
        msg = build_message(["bldg_A"], [], [], [], "single")
        self.assertIn("Building: bldg_A", msg)

    def test_lifecycle_lists_added_deleted_and_asks_reason(self):
        msg = build_message([], ["new"], ["old1", "old2"], [], "lifecycle")
        self.assertIn("lifecycle(", msg)
        self.assertIn("fill in why", msg)
        self.assertIn("Building-Added: new", msg)
        self.assertIn("Building-Deleted: old1", msg)
        self.assertIn("Building-Deleted: old2", msg)

    def test_rename_keeps_stable_key_and_notes_change(self):
        # rename (gml:id-only change) keeps buildingID unchanged, so tracking continues under the same key.
        msg = build_message([], [], [], ["bldg_new"], "rename",
                            id_map={"bldg_new": "13101-bldg-3728"})
        self.assertIn("Building: 13101-bldg-3728", msg)
        self.assertIn("rename", msg)  # rename note in the body

    def test_trailer_is_grepable_per_building(self):
        msg = build_message(["b1", "b2", "b3"], [], [], [], "multi-modified")
        lines = [ln for ln in msg.splitlines() if ln.startswith("Building:")]
        self.assertEqual(sorted(lines), ["Building: b1", "Building: b2", "Building: b3"])


class TestRenderComment(unittest.TestCase):
    def test_marker_first_line_for_upsert(self):
        c = render_comment(build_message(["b"], [], [], [], "single"), "single", 1, resolved=False)
        self.assertTrue(c.startswith(MARKER))

    def test_notes_key_kind(self):
        c = render_comment("x", "single", 1, resolved=True)
        self.assertIn("uro:buildingID", c)  # states explicitly that the stable ID is used


if __name__ == "__main__":
    unittest.main()
