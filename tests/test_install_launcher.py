#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""install/citygml.sh: the one-line installer / per-user launcher (macOS).

Runs the real script with the test hooks (no network): a fake releases payload and
a local asset stand in for the GitHub API and the download; CITYGML_NO_EXEC prints
the hand-over instead of starting the hub."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "install" / "citygml.sh"


def make_asset(path: Path, marker: str = "hub") -> str:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("citygml-hub/READ-ME-FIRST.html", "<p>x</p>")
        z.writestr("citygml-hub/program/hub.py", f"print('{marker}')\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def releases_json(path: Path, entries):
    """entries: [(tag, sha, prerelease)]"""
    rels = []
    for tag, sha, pre in entries:
        ver = tag.removeprefix("hub-")
        rels.append({"tag_name": tag, "draft": False, "prerelease": pre, "html_url": f"https://example/{tag}",
                     "assets": [{"name": f"citygml-hub-{ver}-macos.zip", "browser_download_url": f"https://example/{ver}.zip",
                                 "digest": f"sha256:{sha}"}]})
    path.write_text(json.dumps(rels), encoding="utf-8")


@unittest.skipUnless(sys.platform == "darwin", "macOS launcher")
class TestLauncher(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.tools = self.tmp / "tools"
        self.asset = self.tmp / "asset.zip"
        self.sha = make_asset(self.asset)
        self.rel = self.tmp / "releases.json"
        releases_json(self.rel, [("hub-v1.2.0", self.sha, False), ("hub-v1.10.0", "00", True), ("hub-v1.3.0", self.sha, False)])

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, *args, **env_over):
        env = dict(os.environ, CITYGML_TOOLS_DIR=str(self.tools), CITYGML_RELEASES_JSON=str(self.rel),
                   CITYGML_ASSET_FILE=str(self.asset), CITYGML_NO_EXEC="1", LANG="en_US.UTF-8")
        env.update(env_over)
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)

    def test_first_run_installs_newest_published_release_and_hands_over(self):
        r = self.run_script("4dcitygml/sample-munich-station")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tools / "citygml-hub" / "hub-v1.3.0" / "program" / "hub.py").is_file())   # 1.10.0 is a prerelease
        self.assertIn("EXEC hub-v1.3.0 4dcitygml/sample-munich-station", r.stdout)
        self.assertTrue((self.tools / "citygml.sh").is_file())                                        # self-copied for the icon
        self.assertEqual(oct((self.tools / "citygml.sh").stat().st_mode & 0o777), "0o755")

    def test_digest_mismatch_installs_nothing(self):
        releases_json(self.rel, [("hub-v1.3.0", "deadbeef", False)])
        r = self.run_script("o/r")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SHA-256", r.stderr)
        self.assertFalse((self.tools / "citygml-hub").exists() and any((self.tools / "citygml-hub").glob("hub-v*")))

    def test_second_run_uses_newest_installed_without_network(self):
        self.run_script("o/r")
        (self.tools / "citygml-hub" / "hub-v1.4.0" / "program").mkdir(parents=True)
        (self.tools / "citygml-hub" / "hub-v1.4.0" / "program" / "hub.py").write_text("print('new')")
        r = self.run_script("o/r", CITYGML_RELEASES_JSON="/nonexistent")      # API must not be needed
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("EXEC hub-v1.4.0 o/r", r.stdout)

    def _older_generation(self):
        """What a computer with hub-v1.0.x looks like: the flat layout the 1.0 zips used, and the
        version folder the 1.2.0 launcher made of it. Neither is this launcher's business."""
        hubs = self.tools / "citygml-hub"
        (hubs / "program").mkdir(parents=True)
        (hubs / "program" / "hub.py").write_text("print('old')")
        (hubs / "start-mac.command").write_text("old starter")
        (hubs / ".release-tag").write_text("hub-v1.0.2\n")
        (hubs / "hub-v1.1.0" / "program").mkdir(parents=True)
        (hubs / "hub-v1.1.0" / "program" / "hub.py").write_text("print('old')")
        return hubs

    def test_older_generation_is_left_in_place_and_the_latest_is_installed_next_to_it(self):
        hubs = self._older_generation()
        r = self.run_script("o/r")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("EXEC hub-v1.3.0 o/r", r.stdout)                        # not 1.1.0, not the flat 1.0.2
        self.assertTrue((hubs / "hub-v1.3.0" / "program" / "hub.py").is_file())
        for untouched in ("program/hub.py", "start-mac.command", ".release-tag", "hub-v1.1.0/program/hub.py"):
            self.assertTrue((hubs / untouched).is_file(), untouched)
        self.assertNotIn("Moved", r.stdout)

    def test_older_generation_without_network_fails_and_names_the_next_step(self):
        hubs = self._older_generation()
        r = self.run_script("o/r", CITYGML_RELEASES_JSON="/nonexistent")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Check the internet connection", r.stderr)
        self.assertNotIn("EXEC", r.stdout)
        self.assertTrue((hubs / "program" / "hub.py").is_file())                # still untouched

    def test_self_updating_version_is_started_without_network(self):
        cur = self.tools / "citygml-hub" / "hub-v1.2.0" / "program"
        cur.mkdir(parents=True)
        (cur / "hub.py").write_text("print('cur')")
        r = self.run_script("o/r", CITYGML_RELEASES_JSON="/nonexistent")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("EXEC hub-v1.2.0 o/r", r.stdout)

    def test_default_city_follows_the_language(self):
        for lang, city in (("ja_JP.UTF-8", "sample-tokyo-station"), ("de_DE.UTF-8", "sample-munich-station"), ("en_US.UTF-8", "sample-newyork-station")):
            r = self.run_script(LANG=lang)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"4dcitygml/{city}", r.stdout, lang)
            self.assertIn("practice city", r.stdout)

    def test_fetch_latest_installs_next_to_the_running_version_and_prints_the_tag(self):
        r = self.run_script("--fetch-latest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "hub-v1.3.0")
        self.assertTrue((self.tools / "citygml-hub" / "hub-v1.3.0" / "program" / "hub.py").is_file())
        self.assertNotIn("EXEC", r.stdout)                                                          # nothing started
        # already installed: the tag is printed without a download
        r = self.run_script("--fetch-latest", CITYGML_ASSET_FILE="/nonexistent")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "hub-v1.3.0")

    def test_missing_digest_is_refused(self):
        rels = json.loads(self.rel.read_text(encoding="utf-8"))
        for r in rels:
            for a in r["assets"]:
                a.pop("digest", None)
        self.rel.write_text(json.dumps(rels), encoding="utf-8")
        r = self.run_script("o/r")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("digest", r.stderr)
        self.assertFalse((self.tools / "citygml-hub").exists() and any((self.tools / "citygml-hub").glob("hub-v*")))

    def test_bad_city_argument_is_rejected(self):
        r = self.run_script("tokyo")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("owner/repo", r.stderr)

    def test_shell_syntax(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
