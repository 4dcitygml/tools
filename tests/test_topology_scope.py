#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for the per-building first-time topology check decision."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.topology_scope import decide


class GitRepo:
    def __init__(self, root: Path):
        self.root = root
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.strip()

    def commit(self, subject: str, trailer: str = "") -> str:
        marker = self.root / "marker.txt"
        marker.write_text(marker.read_text() + subject + "\n" if marker.exists() else subject + "\n")
        self.git("add", "marker.txt")
        message = subject + (f"\n\n{trailer}" if trailer else "")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


class TestTopologyScope(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = GitRepo(Path(self.temp.name))
        self.initial = self.repo.commit("initial")

    def tearDown(self):
        self.temp.cleanup()

    def test_first_attribute_change_runs_baseline_inspection(self):
        head = self.repo.commit("attribute", "Building: bldg-1")
        result = decide(self.repo.root, self.initial, head, "attribute")
        self.assertTrue(result.run)
        self.assertIn("First occurrence", result.reason)

    def test_later_attribute_change_is_not_applicable(self):
        baseline = self.repo.commit("first", "Building: bldg-1")
        head = self.repo.commit("second", "Building: bldg-1")
        result = decide(self.repo.root, baseline, head, "attribute")
        self.assertFalse(result.run)
        self.assertIn("out of scope", result.reason)

    def test_geometry_change_always_runs(self):
        baseline = self.repo.commit("first", "Building: bldg-1")
        head = self.repo.commit("geometry", "Building: bldg-1")
        self.assertTrue(decide(self.repo.root, baseline, head, "geometry").run)

    def test_missing_building_trailer_fails_safe(self):
        head = self.repo.commit("no trailer")
        result = decide(self.repo.root, self.initial, head, "attribute")
        self.assertTrue(result.run)
        self.assertIn("safety", result.reason)

    def test_each_building_must_have_prior_baseline(self):
        baseline = self.repo.commit("first", "Building: bldg-1")
        head = self.repo.commit("two buildings", "Building: bldg-1\nBuilding: bldg-2")
        result = decide(self.repo.root, baseline, head, "texture")
        self.assertTrue(result.run)
        self.assertIn("bldg-2", result.reason)


if __name__ == "__main__":
    unittest.main()
