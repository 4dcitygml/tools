# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""One lock for git's work in a clone, between the hub's sync and both editors (D18, S18).

The three are separate processes on one working tree. Each used to lock only itself, so a
send could check out its branch while the sync merged main, or while the other editor sent.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from tests.support import REPO_ROOT

import git_sync   # noqa: E402  (tests.support puts tools/ on sys.path)

HOLDER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {tools!r})
    import git_sync
    with git_sync.clone_lock({clone!r}):
        print("held", flush=True)
        time.sleep({seconds})
    print("released", flush=True)
""")


class TestCloneLock(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clone = Path(self.temp.name) / "clone"
        (self.clone / ".git").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_another_process_waits_for_the_lock(self):
        # the other editor, or the hub's sync, as a second process holding the clone's lock
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER.format(tools=str(REPO_ROOT / "tools"), clone=str(self.clone), seconds=1.5)],
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            started = time.monotonic()
            with git_sync.clone_lock(self.clone):
                waited = time.monotonic() - started
            self.assertGreater(waited, 0.8, "the lock did not wait for the other process")
        finally:
            holder.wait(10)
        self.assertTrue((self.clone / ".git" / "citygml.lock").exists())

    def test_the_holding_thread_may_take_it_again(self):
        # send_proposal holds it and _fresh_pr_base may sync main inside the same send
        with git_sync.clone_lock(self.clone):
            with git_sync.clone_lock(self.clone):
                pass
        with git_sync.clone_lock(self.clone):   # released completely afterwards
            pass

    def test_every_writer_takes_it(self):
        attr_src = (REPO_ROOT / "tools/attr_editor/app.py").read_text(encoding="utf-8")
        self.assertNotIn("_git_lock", attr_src)
        send = attr_src[attr_src.index("    def send_proposal("):attr_src.index("    def create_pr(")]
        self.assertIn("with git_sync.clone_lock(self.root):", send)
        sync = (REPO_ROOT / "tools/git_sync.py").read_text(encoding="utf-8")
        body = sync[sync.index("def sync_main("):]
        self.assertIn("with clone_lock(root):", body[:body.index('run("merge", "--ff-only"')])
        self.assertIn("with git_sync.clone_lock(self.root):",
                      (REPO_ROOT / "tools/hub/app.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
