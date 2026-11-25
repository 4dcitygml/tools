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
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.suggest_commit import MARKER, build_message, main, render_comment


class TestBuildMessage(unittest.TestCase):
    def test_single_names_the_stable_id(self):
        msg = build_message(["13101-bldg-3728"], [], [], [], "single")
        self.assertIn("update(13101-bldg-3728):", msg)
        self.assertIn("Building: 13101-bldg-3728", msg)

    def test_lifecycle_lists_added_deleted_and_asks_reason(self):
        msg = build_message([], ["new"], ["old1", "old2"], [], "lifecycle")
        self.assertIn("lifecycle(", msg)
        self.assertIn("fill in why", msg)
        self.assertIn("Building-Added: new", msg)
        self.assertIn("Building-Deleted: old1", msg)
        self.assertIn("Building-Deleted: old2", msg)

    def test_rename_keeps_stable_key_and_notes_change(self):
        # rename (gml:id-only change) keeps buildingID unchanged, so tracking continues under the same key.
        msg = build_message([], [], [], ["13101-bldg-3728"], "rename")
        self.assertIn("Building: 13101-bldg-3728", msg)
        self.assertIn("rename", msg)  # rename note in the body

    def test_trailer_is_grepable_per_building(self):
        msg = build_message(["b1", "b2", "b3"], [], [], [], "multi-modified")
        lines = [ln for ln in msg.splitlines() if ln.startswith("Building:")]
        self.assertEqual(sorted(lines), ["Building: b1", "Building: b2", "Building: b3"])


class TestRenderComment(unittest.TestCase):
    def test_marker_first_line_for_upsert(self):
        c = render_comment(build_message(["b"], [], [], [], "single"), "single", 1)
        self.assertTrue(c.startswith(MARKER))

    def test_notes_key_kind(self):
        self.assertIn("gml:id (stable ID)", render_comment("x", "single", 1))   # undeclared: gml:id
        self.assertIn("gen:BIN (stable ID)", render_comment("x", "single", 1, "gen:BIN"))


def _bin_tile(buildings: dict) -> str:
    return ('<CityModel xmlns="http://www.opengis.net/citygml/1.0" xmlns:bldg="http://www.opengis.net/citygml/building/1.0" '
            'xmlns:gen="http://www.opengis.net/citygml/generics/1.0" xmlns:gml="http://www.opengis.net/gml">\n'
            + "".join(f'<cityObjectMember><bldg:Building gml:id="{g}"><gen:stringAttribute name="BIN"><gen:value>{b}'
                      "</gen:value></gen:stringAttribute></bldg:Building></cityObjectMember>\n" for g, b in buildings.items())
            + "</CityModel>\n")


class TestAnyCity(unittest.TestCase):
    """The suggestion names the ID the commit scope gate expects: New York was told to write the gml:id."""

    def test_the_suggestion_is_the_gates_scope(self):
        # U4 / D-a2, D-b3: suggest_commit resolved gml:ids to stable IDs itself, through the changed
        # files and at its own revision, while the gate counted by its own; it now prints the gate's
        # scope (the base's rule, deleted buildings included), even in a PR that changes building_id
        import contextlib, io
        from scripts.commit_building_scope import _scope_or_error
        with tempfile.TemporaryDirectory() as tmp:
            run = lambda *a: subprocess.run(["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                             *a], check=True, capture_output=True, text=True).stdout.strip()
            run("init", "-q")
            Path(tmp, "4dcitygml.json").write_text(json.dumps({"building_id": {"type": "gen:BIN"}}), encoding="utf-8")
            Path(tmp, "tile.gml").write_text(_bin_tile({"gml_A": "1036448", "gml_B": "1036449"}), encoding="utf-8")
            run("add", ".")
            run("commit", "-q", "-m", "base")
            base = run("rev-parse", "HEAD")
            Path(tmp, "4dcitygml.json").write_text(json.dumps({"building_id": {"type": "gml:id"}}), encoding="utf-8")
            Path(tmp, "tile.gml").write_text(_bin_tile({"gml_A": "1036448"}), encoding="utf-8")   # gml_B deleted
            run("commit", "-qam", "delete B")
            scope = Path(tmp, "scope.json")
            scope.write_text(json.dumps(_scope_or_error(Path(tmp), base, run("rev-parse", "HEAD"))), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--scope-json", str(scope)])
        self.assertIn("Building-Deleted: 1036449", out.getvalue())
        self.assertIn("gen:BIN (stable ID)", out.getvalue())
        self.assertNotIn("gml_B", out.getvalue())

    def test_no_scope_no_comment(self):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--scope-json", "/nonexistent/scope.json"]), 0)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
