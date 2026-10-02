#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""hub-v1.2.0 tool updates: announced automatically, fetched on request, verified
against GitHub's digest, installed next to the running version, applied at the next start."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import TempHome, runtime

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("hub_update_app", REPO_ROOT / "tools" / "hub" / "app.py")
hub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hub)


def make_asset(path: Path) -> str:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("citygml-hub/READ-ME-FIRST.html", "<p>x</p>")
        info = zipfile.ZipInfo("citygml-hub/start-mac.command"); info.external_attr = (0o755 & 0xFFFF) << 16
        z.writestr(info, "#!/bin/bash\n")
        z.writestr("citygml-hub/program/hub.py", "print('new')\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def releases(*entries):
    out = []
    for tag, sha, pre in entries:
        ver = tag.removeprefix("hub-")
        out.append({"tag_name": tag, "draft": False, "prerelease": pre, "html_url": f"https://example/{tag}",
                    "assets": [{"name": f"citygml-hub-{ver}-macos.zip", "browser_download_url": f"https://example/{ver}-mac.zip", "digest": f"sha256:{sha}"},
                               {"name": f"citygml-hub-{ver}-windows-full.zip", "browser_download_url": f"https://example/{ver}-win.zip", "digest": f"sha256:{sha}"}]})
    return out


class TestVersions(unittest.TestCase):
    def setUp(self):
        self._home = TempHome()
        self.tmp = self._home.__enter__()

    def tearDown(self):
        self._home.__exit__(None, None, None)

    def test_version_tuple(self):
        self.assertEqual(runtime.version_tuple("hub-v1.10.2"), (1, 10, 2))
        self.assertIsNone(runtime.version_tuple("tools-v1.1.0"))
        self.assertIsNone(runtime.version_tuple(""))

    def test_latest_release_skips_prerelease_and_orders_numerically(self):
        best = hub.latest_hub_release(releases(("hub-v1.2.0", "a", False), ("hub-v1.10.0", "b", True), ("hub-v1.9.0", "c", False)))
        self.assertEqual(best["tag"], "hub-v1.9.0")
        self.assertEqual(best["notesUrl"], "https://example/hub-v1.9.0")
        self.assertIsNone(hub.latest_hub_release([]))

    def test_the_check_asks_the_repository_the_launcher_installs_from(self):
        # D10: the check was fixed to 4dcitygml/tools while the launcher honoured CITYGML_TOOLS_REPO
        with patch.dict(os.environ, {"CITYGML_TOOLS_REPO": "", "CITYGML_RELEASES_API": ""}):
            self.assertEqual(hub.releases_api(), "https://api.github.com/repos/4dcitygml/tools/releases?per_page=30")
        with patch.dict(os.environ, {"CITYGML_TOOLS_REPO": "someone/tools-copy", "CITYGML_RELEASES_API": ""}):
            self.assertEqual(hub.releases_api(), "https://api.github.com/repos/someone/tools-copy/releases?per_page=30")
        with patch.dict(os.environ, {"CITYGML_TOOLS_REPO": "bad/../x?y", "CITYGML_RELEASES_API": ""}):
            self.assertIn("/repos/4dcitygml/tools/", hub.releases_api())
        with patch.dict(os.environ, {"CITYGML_RELEASES_API": "http://127.0.0.1:9/releases"}):
            self.assertEqual(hub.releases_api(), "http://127.0.0.1:9/releases")
        launcher = (REPO_ROOT / "install/citygml.sh").read_text(encoding="utf-8")
        self.assertIn('RELEASES_API="${CITYGML_RELEASES_API:-https://api.github.com/repos/${TOOLS_REPO}/releases?per_page=30}"',
                      launcher)

    def test_running_tag_from_env_or_folder(self):
        with patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.2.0"}):
            runtime.reset_caches()
            self.assertEqual(runtime.running_hub_tag(), "hub-v1.2.0")
        with patch.dict(os.environ, {"CITYGML_HUB_TAG": ""}):
            runtime.reset_caches()
            self.assertIsNone(runtime.running_hub_tag())   # source tree: tools/hub/.. is not a version folder


class TestFetchLatestHub(unittest.TestCase):
    """The hub's "Get it now" is the launcher's fetch mode, run from the copy shipped with
    the running version (the person's copy in the tools folder may be older)."""

    def setUp(self):
        self._home = TempHome()
        self._home.__enter__()

    def tearDown(self):
        self._home.__exit__(None, None, None)

    def _run(self, calls, returncode=0, stdout="Downloading the editing tools (hub-v1.3.0) …\nhub-v1.3.0\n", stderr=""):
        def run(cmd, **kw):
            calls.append((cmd, kw))
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
        return run

    def test_runs_the_bundled_launcher_and_returns_the_last_line(self):
        calls = []
        self.assertEqual(runtime.fetch_latest_hub(run=self._run(calls)), "hub-v1.3.0")
        cmd, kw = calls[0]
        self.assertEqual(Path(cmd[1]), runtime.launcher_source())                 # never the person's copy
        self.assertIn("--fetch-latest" if not runtime.WINDOWS else "-FetchLatest", cmd)
        self.assertEqual(kw["env"]["CITYGML_TOOLS_DIR"], str(runtime.tools_dir()))

    def test_failure_names_the_launchers_last_line(self):
        with self.assertRaises(RuntimeError) as ctx:
            runtime.fetch_latest_hub(run=self._run([], returncode=1, stdout="", stderr="curl: (6) no host\nThe download failed."))
        self.assertEqual(str(ctx.exception), "The download failed.")
        with self.assertRaises(RuntimeError):
            runtime.fetch_latest_hub(run=self._run([], stdout="not a tag\n"))   # a tag is required


class TestUpdateManager(unittest.TestCase):
    def setUp(self):
        self._home = TempHome()
        self._home.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.asset = self.tmp / "asset.zip"
        self.sha = make_asset(self.asset)
        self.root = self.tmp / "citygml-hub"

    def tearDown(self):
        self._tmp.cleanup()
        self._home.__exit__(None, None, None)

    def _wait(self, um, key, want, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            snap = um.snapshot()
            value = snap
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if value == want:
                return snap
            time.sleep(0.05)
        self.fail(f"{key} never became {want!r}: {um.snapshot()}")

    def test_announce_only_then_fetch_on_request(self):
        with patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.2.0"}), patch.object(runtime, "hubs_dir", return_value=self.root):
            runtime.reset_caches()
            um = hub.UpdateManager()
            um.check_async(fetch_json=lambda: releases(("hub-v1.3.0", self.sha, False)))
            snap = self._wait(um, "checked", True)
            self.assertTrue(snap["available"]); self.assertEqual(snap["latest"], "hub-v1.3.0")
            self.assertEqual(snap["fetch"]["state"], "idle")
            self.assertFalse((self.root / "hub-v1.3.0").exists())          # nothing downloaded unasked
            fetched = []
            um.fetch_async(fetch=lambda: fetched.append(1) or "hub-v1.3.0")   # the launcher's fetch mode
            snap = self._wait(um, "fetch.state", "done")
            self.assertEqual((snap["fetch"]["tag"], fetched), ("hub-v1.3.0", [1]))

    def test_no_update_when_running_is_newest(self):
        with patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.3.0"}), patch.object(runtime, "hubs_dir", return_value=self.root):
            runtime.reset_caches()
            um = hub.UpdateManager()
            um.check_async(fetch_json=lambda: releases(("hub-v1.3.0", self.sha, False)))
            snap = self._wait(um, "checked", True)
            self.assertFalse(snap["available"])

    def test_below_min_hub_blocks_sending_and_is_judged_offline(self):
        # maintainer 2026-09-30: an older hub can view, not send or request a retry
        (self.tmp / "4dcitygml.json").write_text(json.dumps({"repo": "o/r", "min_hub": "hub-v1.5.0"}))
        with patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.3.3"}):
            runtime.reset_caches()
            self.assertEqual(runtime.below_min_hub(self.tmp), "hub-v1.5.0")
            with self.assertRaisesRegex(RuntimeError, "hub-v1.5.0"):
                runtime.require_min_hub(self.tmp)
            um = hub.UpdateManager()
            um.set_min_hub(self.tmp)                     # no network needed (it used to wait for the releases)
            snap = um.snapshot()
            self.assertEqual((snap["minHubOk"], snap["minHub"], snap["minHubFinal"]), (False, "hub-v1.5.0", False))
        with patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.5.0"}):
            runtime.reset_caches()
            self.assertIsNone(runtime.below_min_hub(self.tmp))
            runtime.require_min_hub(self.tmp)
        with patch.dict(os.environ, {"CITYGML_HUB_TAG": ""}):
            runtime.reset_caches()
            self.assertIsNone(runtime.below_min_hub(self.tmp))   # a tools checkout is never below

    def test_min_hub_is_judged_again_after_the_sync(self):
        # the sync may bring the city's new min_hub: hub-v1.3.3 saw it only from the second start
        import git_sync
        (self.tmp / "4dcitygml.json").write_text(json.dumps({"repo": "o/r"}))
        def sync(root, url, args, log):
            (Path(root) / "4dcitygml.json").write_text(json.dumps({"repo": "o/r", "min_hub": "hub-v1.5.0"}))
            return {"state": "updated", "head": "abc", "message": ""}
        with patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.4.0"}), patch.object(git_sync, "sync_main", sync):
            runtime.reset_caches()
            um = hub.UpdateManager()
            um.set_min_hub(self.tmp)
            self.assertTrue(um.snapshot()["minHubOk"])
            job = git_sync.BackgroundSync(self.tmp, lambda: "u", lambda: [],
                                          on_done=lambda _s: um.set_min_hub(self.tmp, final=True)).start()
            self.assertTrue(job.done.wait(10))
            snap = um.snapshot()
            self.assertEqual((snap["minHubOk"], snap["minHub"], snap["minHubFinal"]), (False, "hub-v1.5.0", True))

    def test_the_editors_and_the_retry_refuse_below_min_hub(self):
        for rel, needle in (("tools/attr_editor/app.py", "def send_proposal("),   # both editors send through it (S10)
                            ("tools/hub/app.py", "def request_ci_retry(")):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            body = text[text.index(needle):][:1500]
            self.assertIn("runtime.require_min_hub(self.root)", body, rel)

    def test_offline_check_is_silent(self):
        with patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.2.0"}):
            runtime.reset_caches()
            um = hub.UpdateManager()
            def boom():
                raise OSError("no network")
            um.check_async(fetch_json=boom)
            snap = self._wait(um, "checked", True)
            self.assertFalse(snap["available"]); self.assertIn("no network", snap["error"])

    def test_min_hub_reads_clone_config(self):
        (self.tmp / "4dcitygml.json").write_text(json.dumps({"repo": "o/r", "min_hub": "hub-v1.3.0"}))
        self.assertEqual(runtime.min_hub(self.tmp), "hub-v1.3.0")
        (self.tmp / "4dcitygml.json").write_text(json.dumps({"repo": "o/r", "min_hub": "latest"}))
        self.assertIsNone(runtime.min_hub(self.tmp))


if __name__ == "__main__":
    unittest.main()
