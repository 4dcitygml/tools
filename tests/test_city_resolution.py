#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for automatic resolution of the target city (upstream_url / upstream_nwo).

Priority: CITYGML_UPSTREAM env var > clone's 4dcitygml.json > git remote upstream > default.
Implements plan document §5.1b "install from a municipality repo, skipping city selection".
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(app_rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / app_rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


attr = _load("tools/attr_editor/app.py", "attr_city_res")
hub = _load("tools/hub/app.py", "hub_city_res")

DEFAULT = "4dcitygml/sample-tokyo-station"


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("CITYGML_UPSTREAM", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["CITYGML_UPSTREAM"] = self._saved
        else:
            os.environ.pop("CITYGML_UPSTREAM", None)


class TestNormalize(Base):
    def test_forms(self):
        for mod in (attr, hub):
            n = mod._normalize_upstream
            self.assertEqual(n("owner/repo"), "https://github.com/owner/repo")
            self.assertEqual(n("https://github.com/o/r"), "https://github.com/o/r")
            self.assertEqual(n("https://github.com/o/r.git"), "https://github.com/o/r")
            self.assertEqual(n("git@github.com:o/r.git"), "https://github.com/o/r")
            self.assertIsNone(n("https://evil.example/o/r"))
            self.assertIsNone(n("o/r/extra"))
            self.assertIsNone(n(""))


class TestPriority(Base):
    def _repo_with(self, city_repo: str | None, upstream_remote: str | None):
        d = Path(tempfile.mkdtemp())
        if city_repo is not None:
            (d / "4dcitygml.json").write_text(json.dumps({"repo": city_repo}), encoding="utf-8")
        if upstream_remote is not None:
            subprocess.run(["git", "init", "-q", str(d)], check=True)
            subprocess.run(["git", "-C", str(d), "remote", "add", "upstream",
                            upstream_remote], check=True)
        return d

    def test_default_without_context(self):
        for mod in (attr, hub):
            self.assertEqual(mod.upstream_nwo(), DEFAULT)
            self.assertEqual(mod.upstream_nwo(None), DEFAULT)

    def test_city_json_wins_over_default(self):
        d = self._repo_with("stadt-muenchen/13100-muenchen", None)
        for mod in (attr, hub):
            self.assertEqual(mod.upstream_nwo(d), "stadt-muenchen/13100-muenchen")

    def test_git_remote_upstream_used_when_no_city_json(self):
        d = self._repo_with(None, "git@github.com:city-of-x/13101-cityname.git")
        for mod in (attr, hub):
            self.assertEqual(mod.upstream_nwo(d), "city-of-x/13101-cityname")

    def test_city_json_wins_over_remote(self):
        d = self._repo_with("a/b", "https://github.com/c/d.git")
        for mod in (attr, hub):
            self.assertEqual(mod.upstream_nwo(d), "a/b")

    def test_env_wins_over_everything(self):
        os.environ["CITYGML_UPSTREAM"] = "env-owner/env-repo"
        d = self._repo_with("a/b", None)
        for mod in (attr, hub):
            self.assertEqual(mod.upstream_nwo(d), "env-owner/env-repo")

    def test_broken_city_json_falls_back(self):
        d = Path(tempfile.mkdtemp())
        (d / "4dcitygml.json").write_text("{not json", encoding="utf-8")
        for mod in (attr, hub):
            self.assertEqual(mod.upstream_nwo(d), DEFAULT)

    def test_invalid_repo_value_falls_back(self):
        d = self._repo_with("https://evil.example/o/r", None)
        for mod in (attr, hub):
            self.assertEqual(mod.upstream_nwo(d), DEFAULT)


class TestDemoCityJsons(unittest.TestCase):
    """Consistency check that the three demo cities' 4dcitygml.json files are readable by this resolution logic."""

    def test_demo_city_jsons_resolve(self):
        base = REPO_ROOT.parent
        expect = {
            "sample-tokyo-station": "4dcitygml/sample-tokyo-station",
            "sample-munich-station": "4dcitygml/sample-munich-station",
            "sample-newyork-station": "4dcitygml/sample-newyork-station",
        }
        for folder, nwo in expect.items():
            d = base / folder
            if not (d / "4dcitygml.json").is_file():
                self.skipTest(f"environment does not have {folder}/4dcitygml.json")
            self.assertEqual(attr.upstream_nwo(d), nwo)


class TestStableBuildingId(unittest.TestCase):
    def test_generic_source_placeholder_falls_back_to_gml_id(self):
        span = (
            b'<bldg:Building gml:id="gml_fallback">'
            b'<gen:stringAttribute name="BIN"><gen:value>1000000</gen:value>'
            b'</gen:stringAttribute></bldg:Building>'
        )
        self.assertEqual(
            attr.stable_building_id_from_span(
                span, "gml_fallback", "gen:BIN", {"1000000"}
            ),
            "gml_fallback",
        )

    def test_generic_non_placeholder_remains_primary_id(self):
        span = (
            b'<bldg:Building gml:id="gml_fallback">'
            b'<gen:stringAttribute name="BIN"><gen:value>1085630</gen:value>'
            b'</gen:stringAttribute></bldg:Building>'
        )
        self.assertEqual(
            attr.stable_building_id_from_span(
                span, "gml_fallback", "gen:BIN", {"1000000"}
            ),
            "1085630",
        )


if __name__ == "__main__":
    unittest.main()
