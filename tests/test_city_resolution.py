#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for automatic resolution of the target city (upstream_url / upstream_nwo).

Priority: clone's 4dcitygml.json > git remote upstream > CITYGML_UPSTREAM env var > default
(the variable chooses the city while there is no clone; S18, F7).
Implements plan document §5.1b "install from a municipality repo, skipping city selection".
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tests import city_checkouts
from tests.support import TempHome, runtime

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(app_rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / app_rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


attr = _load("tools/attr_editor/app.py", "attr_city_res")
hub = _load("tools/hub/app.py", "hub_city_res")

DEFAULT = "4dcitygml/sample-munich-station"   # a practice city, as the launcher names it


class Base(unittest.TestCase):
    def setUp(self):
        self._home = TempHome()
        self.home = self._home.__enter__()

    def tearDown(self):
        self._home.__exit__(None, None, None)


class TestNormalize(Base):
    def test_forms(self):
        n = runtime.normalize_upstream
        self.assertEqual(n("owner/repo"), "https://github.com/owner/repo")
        self.assertEqual(n("https://github.com/o/r"), "https://github.com/o/r")
        self.assertEqual(n("https://github.com/o/r.git"), "https://github.com/o/r")
        self.assertEqual(n("git@github.com:o/r.git"), "https://github.com/o/r")
        self.assertIsNone(n("https://evil.example/o/r"))
        self.assertIsNone(n("o/r/extra"))
        self.assertIsNone(n(""))


class TestCityFacts(Base):
    """4dcitygml.json is read in one place (runtime.city_meta); the facts the tools need
    come from it through one function each."""

    def test_language_is_the_primary_subtag_or_en(self):
        for value, lang in (("ja", "ja"), ("ja-JP", "ja"), ("DE", "de"), (" en ", "en"), (None, "en"), ("", "en")):
            self.assertEqual(runtime.norm_lang(value), lang, value)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(runtime.repo_lang(d), "en")                      # no file
            (Path(d) / "4dcitygml.json").write_text('{"lang": "de-DE"}', encoding="utf-8")
            self.assertEqual(runtime.repo_lang(d), "de")
            (Path(d) / "4dcitygml.json").write_text('not json', encoding="utf-8")
            self.assertEqual(runtime.repo_lang(d), "en")                      # broken file

    def test_data_dirs_are_existing_directories_inside_the_clone(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / "citygml").mkdir()
            (root / "4dcitygml.json").write_text(json.dumps(
                {"data_dirs": ["citygml", "missing", "../outside", 42]}), encoding="utf-8")
            self.assertEqual(runtime.data_dirs(root), [root / "citygml"])
            self.assertFalse(runtime.has_building_data(root))              # no .gml yet
            (root / "citygml" / "a.gml").write_text("<x/>", encoding="utf-8")
            self.assertTrue(runtime.has_building_data(root))
            self.assertEqual(runtime.data_dirs(root / "nowhere"), [])


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

    def test_no_city_without_context(self):
        # 5-14: the launcher chooses the practice city (CITYGML_UPSTREAM); the hub has no default of its own
        self.assertIsNone(runtime.upstream_nwo())
        with mock.patch.dict(os.environ, {"CITYGML_UPSTREAM": DEFAULT}):
            self.assertEqual(runtime.upstream_nwo(None), DEFAULT)

    def test_city_json_wins_over_default(self):
        d = self._repo_with("stadt-muenchen/13100-muenchen", None)
        self.assertEqual(runtime.upstream_nwo(d), "stadt-muenchen/13100-muenchen")

    def test_a_clone_without_a_city_has_none(self):
        # V1 / D-3c1: the `upstream` remote and the practice city were fallbacks for a clone, so a
        # clone without 4dcitygml.json was synced (reset) to the practice city; it has no city now
        d = self._repo_with(None, "git@github.com:city-of-x/13101-cityname.git")
        self.assertIsNone(runtime.upstream_nwo(d))
        self.assertIsNone(runtime.city_of(d))

    def test_city_json_wins_over_remote(self):
        d = self._repo_with("a/b", "https://github.com/c/d.git")
        self.assertEqual(runtime.upstream_nwo(d), "a/b")

    def test_the_clone_wins_over_the_environment(self):
        os.environ["CITYGML_UPSTREAM"] = "env-owner/env-repo"
        d = self._repo_with("a/b", None)
        self.assertEqual(runtime.upstream_nwo(d), "a/b")
        self.assertEqual(runtime.upstream_nwo(), "env-owner/env-repo")   # no clone yet: the start script's city

    def test_a_broken_or_foreign_declaration_names_no_city(self):
        d = Path(tempfile.mkdtemp())
        (d / "4dcitygml.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(runtime.upstream_nwo(d))                       # was the practice city
        self.assertIsNone(runtime.upstream_nwo(self._repo_with("https://evil.example/o/r", None)))


class TestDemoCityJsons(unittest.TestCase):
    """Consistency check that the three demo cities' 4dcitygml.json files are readable by this resolution logic."""

    def setUp(self):
        self._home = TempHome()
        self.home = self._home.__enter__()

    def tearDown(self):
        self._home.__exit__(None, None, None)

    def test_demo_city_jsons_resolve(self):
        # each practice city resolves to the repository it is: the one ci/city-refs.json names for it
        # (the public ones in production, the development copies in a lab)
        base = REPO_ROOT.parent
        refs = json.loads((REPO_ROOT / "ci" / "city-refs.json").read_text(encoding="utf-8"))["repositories"]
        for folder in ("sample-tokyo-station", "sample-munich-station", "sample-newyork-station"):
            d = base / folder
            if not (d / "4dcitygml.json").is_file() or folder not in refs:
                city_checkouts.missing(f"environment does not have {folder}/4dcitygml.json, or ci/city-refs.json does not name it")
            self.assertEqual(runtime.upstream_nwo(d).lower(), refs[folder]["repository"].lower())


class TestStableBuildingId(unittest.TestCase):
    def setUp(self):
        self._home = TempHome()
        self.home = self._home.__enter__()

    def tearDown(self):
        self._home.__exit__(None, None, None)

    def test_generic_source_placeholder_falls_back_to_gml_id(self):
        span = (
            b'<bldg:Building gml:id="gml_fallback">'
            b'<gen:stringAttribute name="BIN"><gen:value>1000000</gen:value>'
            b'</gen:stringAttribute></bldg:Building>'
        )
        self.assertEqual(
            attr.building_identity.stable_id(
                span, "gml_fallback", attr.building_identity.IdentityRule("gen:BIN", frozenset({"1000000"}))
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
            attr.building_identity.stable_id(
                span, "gml_fallback", attr.building_identity.IdentityRule("gen:BIN", frozenset({"1000000"}))
            ),
            "1085630",
        )


if __name__ == "__main__":
    unittest.main()
