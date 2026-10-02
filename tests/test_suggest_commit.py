#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for suggest_commit (W7 / per-building traceability).

Embeds the **stable ID (the city's building_id rule)** as a `Building:` trailer in building commit messages,
ensuring `git log --grep`/`blame`/`bisect`/`revert` work at building granularity and across rebuilds.
Run: python -m unittest tests.test_suggest_commit
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.building_identity import IdentityRule
from scripts.suggest_commit import MARKER, build_message, building_id_map, city_rule, render_comment


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
        self.assertIn("uro:buildingID (stable ID)", c)  # states explicitly that the stable ID is used
        self.assertIn("gen:BIN (stable ID)", render_comment("x", "single", 1, resolved=True, rule=IdentityRule("gen:BIN")))


def _bin_tile(buildings: dict) -> str:
    return ('<CityModel xmlns="http://www.opengis.net/citygml/1.0" xmlns:bldg="http://www.opengis.net/citygml/building/1.0" '
            'xmlns:gen="http://www.opengis.net/citygml/generics/1.0" xmlns:gml="http://www.opengis.net/gml">\n'
            + "".join(f'<cityObjectMember><bldg:Building gml:id="{g}"><gen:stringAttribute name="BIN"><gen:value>{b}'
                      "</gen:value></gen:stringAttribute></bldg:Building></cityObjectMember>\n" for g, b in buildings.items())
            + "</CityModel>\n")


class TestAnyCity(unittest.TestCase):
    """The suggestion names the ID the commit scope gate expects: New York was told to write the gml:id."""

    def test_the_citys_rule_at_the_base_and_deleted_buildings(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True)
            run("init", "-q")
            Path(tmp, "4dcitygml.json").write_text(json.dumps({"building_id": {"type": "gen:BIN"}}), encoding="utf-8")
            Path(tmp, "tile.gml").write_text(_bin_tile({"gml_A": "1036448", "gml_B": "1036449"}), encoding="utf-8")
            run("add", ".")
            run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
            base = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            Path(tmp, "tile.gml").write_text(_bin_tile({"gml_A": "1036448"}), encoding="utf-8")   # gml_B deleted
            cwd = os.getcwd()
            os.chdir(tmp)
            self.addCleanup(os.chdir, cwd)
            rule = city_rule(base)
            self.assertEqual(rule, IdentityRule("gen:BIN"))
            self.assertEqual(building_id_map([Path("tile.gml")], rule, base), {"gml_A": "1036448", "gml_B": "1036449"})
            msg = build_message([], [], ["gml_B"], [], "lifecycle", building_id_map([Path("tile.gml")], rule, base))
            self.assertIn("Building-Deleted: 1036449", msg)


if __name__ == "__main__":
    unittest.main()
