#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Upstream auto-sync of the machine-managed local main (checklist task 9).

Covers, without touching the network or real GitHub (upstreams are local paths):
- startup sync: fast-forward, hard reset after a history rewrite (daily practice
  reset), dirty-tree skip, untracked files not blocking, ref-only move when main
  is not checked out, fail-open on unreachable upstream / non-clone folders
  (attr_editor and the hub's self-contained copy behave identically)
- PR flow: the edit branch is cut from the freshly fetched upstream main; when
  upstream moved the target file itself the user is asked to reload (and main is
  synced so the reload serves fresh bytes); offline falls back to the local HEAD.
"""
from __future__ import annotations

import importlib.util
import os
import shutil as real_shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent

_attr_spec = importlib.util.spec_from_file_location(
    "attr_app", REPO_ROOT / "tools" / "attr_editor" / "app.py")
attr = importlib.util.module_from_spec(_attr_spec)
_attr_spec.loader.exec_module(attr)

_hub_spec = importlib.util.spec_from_file_location(
    "hub_app", REPO_ROOT / "tools" / "hub" / "app.py")
hub = importlib.util.module_from_spec(_hub_spec)
_hub_spec.loader.exec_module(hub)

GML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
    'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
    'xmlns:gml="http://www.opengis.net/gml" '
    'xmlns:uro="https://www.geospatial.jp/iur/uro/3.2">'
    '<core:cityObjectMember><bldg:Building gml:id="gml-bldg-1">'
    '<core:creationDate>2024-01-01</core:creationDate>'
    '<bldg:storeysAboveGround>2</bldg:storeysAboveGround>'
    '<uro:buildingID>bldg-1</uro:buildingID>'
    '</bldg:Building></core:cityObjectMember></core:CityModel>'
)
CODELIST = (
    '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml">'
    '<gml:dictionaryEntry><gml:Definition>'
    '<gml:name>801</gml:name><gml:description>現地調査</gml:description>'
    '</gml:Definition></gml:dictionaryEntry></gml:Dictionary>'
)
GIT_USER = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *GIT_USER, *args],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def make_upstream(base: Path) -> Path:
    up = base / "upstream"
    (up / "city/udx/bldg").mkdir(parents=True)
    (up / "city/codelists").mkdir(parents=True)
    (up / "city/udx/bldg/53394611_bldg_6697_op.gml").write_text(GML, encoding="utf-8")
    (up / "city/codelists/DataQualityAttribute_thematicSrcDesc.xml").write_text(
        CODELIST, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(up)], check=True)
    git(up, "add", ".")
    git(up, "commit", "-q", "-m", "initial")
    return up


def clone_of(up: Path, base: Path, name: str = "clone") -> Path:
    dest = base / name
    subprocess.run(["git", "clone", "-q", str(up), str(dest)],
                   capture_output=True, check=True)
    git(dest, "config", "user.name", "Test")
    git(dest, "config", "user.email", "test@example.com")
    return dest


def advance_upstream(up: Path, relpath: str = "docs/note.txt",
                     text: str = "hello") -> str:
    p = up / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(up, "add", relpath)
    git(up, "commit", "-q", "-m", f"update {relpath}")
    return git(up, "rev-parse", "HEAD")


class _SyncFixture(unittest.TestCase):
    """One upstream + one clone; both module copies (attr / hub) point at it."""

    modules = (attr, hub)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.up = make_upstream(base)
        self.clone = clone_of(self.up, base)
        self._saved = [(m, m.upstream_url) for m in self.modules]
        for m in self.modules:
            m.upstream_url = lambda root=None, _u=self.up: str(_u)

    def tearDown(self):
        for m, fn in self._saved:
            m.upstream_url = fn
        self.temp.cleanup()


class TestStartupSync(_SyncFixture):
    def test_fast_forward(self):
        for i, m in enumerate(self.modules):
            with self.subTest(module=m.__name__):
                new = advance_upstream(self.up, f"docs/note{i}.txt")
                got = m.sync_upstream_main(self.clone)
                self.assertEqual(got, new)
                self.assertEqual(git(self.clone, "rev-parse", "main"), new)
                # A second run is a silent no-op
                self.assertIsNone(m.sync_upstream_main(self.clone))

    def test_hard_reset_after_history_rewrite(self):
        old = git(self.clone, "rev-parse", "HEAD")
        git(self.up, "commit", "-q", "--amend", "-m", "rewritten (daily reset)")
        new = git(self.up, "rev-parse", "HEAD")
        self.assertNotEqual(old, new)
        got = attr.sync_upstream_main(self.clone)
        self.assertEqual(got, new)
        self.assertEqual(git(self.clone, "rev-parse", "main"), new)

    def test_dirty_tracked_file_skips_sync(self):
        advance_upstream(self.up)
        gml = self.clone / "city/udx/bldg/53394611_bldg_6697_op.gml"
        gml.write_text(GML + "<!-- local -->", encoding="utf-8")
        before = git(self.clone, "rev-parse", "main")
        self.assertIsNone(attr.sync_upstream_main(self.clone))
        self.assertEqual(git(self.clone, "rev-parse", "main"), before)
        self.assertIn("<!-- local -->", gml.read_text(encoding="utf-8"))

    def test_untracked_files_do_not_block(self):
        new = advance_upstream(self.up)
        extra = self.clone / "scratch.txt"
        extra.write_text("keep me", encoding="utf-8")
        self.assertEqual(attr.sync_upstream_main(self.clone), new)
        self.assertEqual(extra.read_text(encoding="utf-8"), "keep me")

    def test_on_branch_moves_main_ref_only(self):
        git(self.clone, "checkout", "-q", "-b", "edit/x")
        marker = self.clone / "wip.txt"
        marker.write_text("wip", encoding="utf-8")
        git(self.clone, "add", "wip.txt")
        new = advance_upstream(self.up)
        self.assertEqual(attr.sync_upstream_main(self.clone), new)
        self.assertEqual(git(self.clone, "rev-parse", "main"), new)
        self.assertEqual(git(self.clone, "rev-parse", "--abbrev-ref", "HEAD"), "edit/x")
        self.assertTrue(marker.is_file())  # the working tree was not touched

    def test_unreachable_upstream_is_silent(self):
        attr_url, hub_url = attr.upstream_url, hub.upstream_url
        for m in self.modules:
            m.upstream_url = lambda root=None: str(Path(self.temp.name) / "nope")
        try:
            before = git(self.clone, "rev-parse", "main")
            for m in self.modules:
                with self.subTest(module=m.__name__):
                    self.assertIsNone(m.sync_upstream_main(self.clone))
            self.assertEqual(git(self.clone, "rev-parse", "main"), before)
        finally:
            attr.upstream_url, hub.upstream_url = attr_url, hub_url

    def test_plain_folder_is_silent(self):
        plain = Path(self.temp.name) / "plain"
        plain.mkdir()
        self.assertIsNone(attr.sync_upstream_main(plain))


class TestPrBranchBase(_SyncFixture):
    """create_pr cuts the edit branch from the freshly fetched upstream main."""

    _ENV_KEYS = ("CITYGML_LANG", "LC_ALL", "LC_MESSAGES", "LANG")

    def setUp(self):
        super().setUp()
        self._saved_env = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["CITYGML_LANG"] = "en"
        # No hub token and no gh CLI: create_pr must end at the compare-URL fallback
        self._token = attr.load_hub_token
        attr.load_hub_token = lambda: ""
        self._shutil = attr.shutil
        attr.shutil = SimpleNamespace(
            which=lambda name: None if name == "gh" else real_shutil.which(name))
        self.repo = attr.Repo(self.clone)
        self.payload = {
            "tile": "53394611", "gid": "gml-bldg-1",
            "reason": "現地調査票で地上階数を確認しました。",
            "changes": [{
                "key": "storeysAboveGround#0",
                "tag": "storeysAboveGround", "index": 0, "old": "2", "new": "3",
                "label": "地上階数",
            }],
            "sourceSelections": [{"key": "storeysAboveGround#0", "code": "801"}],
        }

    def tearDown(self):
        attr.load_hub_token = self._token
        attr.shutil = self._shutil
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_branch_cut_from_fresh_upstream_main(self):
        new = advance_upstream(self.up)  # unrelated upstream progress
        result = self.repo.create_pr(self.payload)
        self.assertTrue(result["ok"])
        self.assertTrue(result["pushed"])
        # The commit's parent is the just-fetched upstream main, not the stale local HEAD
        self.assertEqual(git(self.clone, "rev-parse", f"{result['branch']}~1"), new)
        # The pushed branch exists on origin (the upstream fixture repo)
        self.assertEqual(git(self.up, "rev-parse", f"refs/heads/{result['branch']}~1"), new)

    def test_overlap_asks_reload_and_syncs_main(self):
        new = advance_upstream(
            self.up, "city/udx/bldg/53394611_bldg_6697_op.gml",
            GML.replace(">2<", ">5<"))
        with self.assertRaises(ValueError) as ctx:
            self.repo.create_pr(self.payload)
        self.assertIn("updated in the city repository", str(ctx.exception))
        # main was synced so that a page reload serves the latest bytes
        self.assertEqual(git(self.clone, "rev-parse", "main"), new)
        self.assertEqual(self.repo._tile_cache, {})

    def test_offline_falls_back_to_local_head(self):
        head = git(self.clone, "rev-parse", "HEAD")
        for m in self.modules:
            m.upstream_url = lambda root=None: str(Path(self.temp.name) / "nope")
        result = self.repo.create_pr(self.payload)
        self.assertTrue(result["ok"])
        self.assertEqual(git(self.clone, "rev-parse", f"{result['branch']}~1"), head)


if __name__ == "__main__":
    unittest.main()
