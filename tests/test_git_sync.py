#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""tools/git_sync.py: one ls-remote round trip, fetch only when main moved, no
wall-clock cut-off, fail-open, never discard local work."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401  (puts tools/ on sys.path: git_sync imports runtime)

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

    def tearDown(self):
        self._tmp.cleanup()

    def _push(self, text):
        (self.work / "a.txt").write_text(text)
        git("commit", "-qam", text, cwd=self.work)
        git("push", "-q", str(self.up), "main", cwd=self.work)

    def test_up_to_date_needs_no_fetch(self):
        result = gs.sync_main(self.clone, str(self.up), None)
        self.assertEqual(result["state"], "up-to-date")

    def test_fast_forward(self):
        self._push("2\n")
        result = gs.sync_main(self.clone, str(self.up), None)
        self.assertEqual(result["state"], "updated")
        self.assertEqual((self.clone / "a.txt").read_text(), "2\n")
        self.assertEqual(result["head"], git("rev-parse", "HEAD", cwd=self.clone))

    def test_diverged_upstream_is_followed(self):
        # practice repositories rewrite main; the local mirror follows (old state stays in reflog)
        git("commit", "-q", "--allow-empty", "-m", "local drift", cwd=self.clone)
        self._push("3\n")
        result = gs.sync_main(self.clone, str(self.up), None)
        self.assertEqual(result["state"], "updated")
        self.assertEqual((self.clone / "a.txt").read_text(), "3\n")

    def test_dirty_tree_is_never_overwritten(self):
        (self.clone / "a.txt").write_text("my edit\n")
        self._push("4\n")
        result = gs.sync_main(self.clone, str(self.up), None)
        self.assertEqual(result["state"], "dirty")
        self.assertEqual((self.clone / "a.txt").read_text(), "my edit\n")

    def test_unreachable_upstream_is_offline_not_error(self):
        result = gs.sync_main(self.clone, str(self.clone.parent / "nope"), None)
        self.assertEqual(result["state"], "offline")

    def test_not_a_clone(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gs.sync_main(d, str(self.up), None)["state"], "not-a-clone")

    def test_background_sync_reports_state(self):
        self._push("5\n")
        bg = gs.BackgroundSync(self.clone, lambda: str(self.up), lambda: None).start()
        self.assertTrue(bg.done.wait(30))
        self.assertEqual(bg.state["state"], "updated")
        self.assertIsNotNone(bg.state["finished"])

    def test_low_speed_args_are_git_config_not_a_wall_clock(self):
        calls = []
        real = gs.runtime.git

        def record(root, *args, **kw):
            calls.append((args, kw))
            return real(root, *args, **kw)
        self._push("two")
        with patch.object(gs.runtime, "git", record):
            gs.sync_main(self.clone, str(self.up), None)
        args, kw = next(c for c in calls if "fetch" in c[0])
        self.assertIn("http.lowSpeedLimit=1000", args)
        self.assertIn("http.lowSpeedTime=30", args)
        self.assertLess(args.index("http.lowSpeedTime=30"), args.index("fetch"))
        self.assertEqual((kw["net"], kw["timeout"]), (True, None))   # the network command, no wall clock



class TestTheFetchedCommit(TestSyncMain):
    """D-3c7: the sync merged FETCH_HEAD, which another process's fetch (an editor's, outside the
    clone's lock) may overwrite between the fetch and the merge; D-3c8: its own runner decoded
    without errors="replace". It merges the commit ls-remote named and runs git through runtime."""

    def test_an_overwritten_fetch_head_does_not_matter(self):
        old = git("rev-parse", "HEAD", cwd=self.clone)
        self._push("newer")
        new = git("rev-parse", "HEAD", cwd=self.work)
        real = gs._fetch

        def fetch_then_overwritten(root, url, sha, store=None, log=None):
            got = real(root, url, sha, store, log)
            (Path(root) / ".git" / "FETCH_HEAD").write_text(f"{old}\t\tbranch 'main' of elsewhere\n")
            return got
        with patch.object(gs, "_fetch", fetch_then_overwritten):
            result = gs.sync_main(self.clone, str(self.up), None)
        self.assertEqual((result["state"], git("rev-parse", "main", cwd=self.clone)), ("updated", new))

    def test_one_runner(self):
        src = (Path(gs.__file__)).read_text(encoding="utf-8")
        self.assertNotIn("FETCH_HEAD\"", src)
        self.assertEqual(src.count("subprocess.run("), 0)   # runtime.git; the clone's progress stream is Popen


class TestCloneProgress(unittest.TestCase):
    """#58: the setup screen said "several GB" before anything was asked; it shows what git has
    received instead, from git's own transfer line (its words may be translated, the numbers not)."""

    GIT_TALK = ("import sys; w = sys.stdout.write; "
                "w('Cloning into x...\\n'); "
                "w('remote: Enumerating objects: 1000, done.\\n'); "
                "w('Receiving objects:  10% (100/1000), 1.20 MiB | 1.00 MiB/s\\r'); "
                "w('Empfange Objekte:  45% (450/1000), 12.30 MiB | 2.00 MiB/s\\r'); "
                "w('Resolving deltas:  50% (5/10)\\n')")

    def test_the_received_amount_is_gits_own(self):
        job = gs.CloneJob(lambda event, **p: event, lambda: [sys.executable, "-c", self.GIT_TALK], lambda: True)
        with tempfile.TemporaryDirectory() as t:
            job.start("file:///nowhere", str(Path(t) / "dest"))
            for _ in range(200):
                if not job.running:
                    break
                time.sleep(0.05)
            state = job.state()
        self.assertTrue(state["done"], state)
        self.assertEqual(state["progress"], {"percent": 45, "received": "12.30 MiB"})
        self.assertNotIn("size_note", state["log"])   # no size claimed in advance
        self.assertEqual(state["log"][0], "start")

if __name__ == "__main__":
    unittest.main()
