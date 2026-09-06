#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""tools/git_sync.py: one ls-remote round trip, fetch only when main moved, no
wall-clock cut-off, fail-open, never discard local work."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("git_sync", REPO_ROOT / "tools" / "git_sync.py")
gs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gs)

GIT = shutil.which("git")


def git(*args, cwd):
    return subprocess.run([GIT, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@unittest.skipUnless(GIT, "git not available")
class TestSyncMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.up = tmp / "upstream.git"
        work = tmp / "work"
        git("init", "-q", "-b", "main", str(work), cwd=tmp)
        git("config", "user.email", "t@example.invalid", cwd=work)
        git("config", "user.name", "t", cwd=work)
        (work / "a.txt").write_text("1\n")
        git("add", "a.txt", cwd=work)
        git("commit", "-q", "-m", "one", cwd=work)
        git("clone", "-q", "--bare", str(work), str(self.up), cwd=tmp)
        self.clone = tmp / "clone"
        git("clone", "-q", str(self.up), str(self.clone), cwd=tmp)
        git("config", "user.email", "t@example.invalid", cwd=self.clone)
        git("config", "user.name", "t", cwd=self.clone)
        self.work = work
        self.base = [GIT]

    def tearDown(self):
        self._tmp.cleanup()

    def _push(self, text):
        (self.work / "a.txt").write_text(text)
        git("commit", "-qam", text, cwd=self.work)
        git("push", "-q", str(self.up), "main", cwd=self.work)

    def test_up_to_date_needs_no_fetch(self):
        result = gs.sync_main(self.clone, str(self.up), self.base)
        self.assertEqual(result["state"], "up-to-date")

    def test_fast_forward(self):
        self._push("2\n")
        result = gs.sync_main(self.clone, str(self.up), self.base)
        self.assertEqual(result["state"], "updated")
        self.assertEqual((self.clone / "a.txt").read_text(), "2\n")
        self.assertEqual(result["head"], git("rev-parse", "HEAD", cwd=self.clone))

    def test_diverged_upstream_is_followed(self):
        # practice repositories rewrite main; the local mirror follows (old state stays in reflog)
        git("commit", "-q", "--allow-empty", "-m", "local drift", cwd=self.clone)
        self._push("3\n")
        result = gs.sync_main(self.clone, str(self.up), self.base)
        self.assertEqual(result["state"], "updated")
        self.assertEqual((self.clone / "a.txt").read_text(), "3\n")

    def test_dirty_tree_is_never_overwritten(self):
        (self.clone / "a.txt").write_text("my edit\n")
        self._push("4\n")
        result = gs.sync_main(self.clone, str(self.up), self.base)
        self.assertEqual(result["state"], "dirty")
        self.assertEqual((self.clone / "a.txt").read_text(), "my edit\n")

    def test_unreachable_upstream_is_offline_not_error(self):
        result = gs.sync_main(self.clone, str(self.clone.parent / "nope"), self.base)
        self.assertEqual(result["state"], "offline")

    def test_not_a_clone(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gs.sync_main(d, str(self.up), self.base)["state"], "not-a-clone")

    def test_background_sync_reports_state(self):
        self._push("5\n")
        bg = gs.BackgroundSync(self.clone, lambda: str(self.up), lambda: self.base).start()
        self.assertTrue(bg.done.wait(30))
        self.assertEqual(bg.state["state"], "updated")
        self.assertIsNotNone(bg.state["finished"])

    def test_low_speed_args_are_git_config_not_a_wall_clock(self):
        args = gs._with_low_speed([GIT, "-c", "x=y"])
        self.assertEqual(args[0], GIT)
        self.assertIn("http.lowSpeedLimit=1000", args)
        self.assertIn("http.lowSpeedTime=30", args)
        self.assertIn("x=y", args)


if __name__ == "__main__":
    unittest.main()
