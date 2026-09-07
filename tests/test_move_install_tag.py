# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/move_install_tag.sh moves the installer tag by deleting and re-creating it on
origin (a force update is what the maintainer's pre-push guard refuses)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent   # no tests.support: this file is shared with tools main
SCRIPT = REPO_ROOT / "scripts" / "move_install_tag.sh"
IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    return subprocess.run(["git", *IDENT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@unittest.skipIf(sys.platform.startswith("win"), "bash script")
class TestMoveInstallTag(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.origin = tmp / "origin.git"
        git(tmp, "init", "--bare", "-q", "-b", "main", str(self.origin))
        self.clone = tmp / "clone"
        git(tmp, "clone", "-q", str(self.origin), str(self.clone))
        (self.clone / "install").mkdir()
        for name in ("citygml.sh", "citygml.ps1"):
            (self.clone / "install" / name).write_text("v1\n")
        git(self.clone, "add", "."); git(self.clone, "commit", "-q", "-m", "one")
        self.old = git(self.clone, "rev-parse", "HEAD")
        git(self.clone, "push", "-q", "origin", "main")
        git(self.clone, "tag", "install-v1", self.old); git(self.clone, "push", "-q", "origin", "install-v1")
        (self.clone / "install" / "citygml.sh").write_text("v2\n")
        git(self.clone, "commit", "-q", "-am", "two")
        self.new = git(self.clone, "rev-parse", "HEAD")
        git(self.clone, "push", "-q", "origin", "main")

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, *args):
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        return subprocess.run(["bash", str(SCRIPT), *args], cwd=self.clone, capture_output=True, text=True, env=env)

    def remote_tag(self):
        out = git(self.clone, "ls-remote", "--tags", "origin", "refs/tags/install-v1")
        return out.split()[0] if out else ""

    def test_dry_run_plans_delete_then_create_and_changes_nothing(self):
        r = self.run_script("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("+ git push origin :refs/tags/install-v1", r.stdout)
        self.assertIn("+ git push origin refs/tags/install-v1", r.stdout)
        self.assertNotIn("push -f", r.stdout)
        self.assertEqual(self.remote_tag(), self.old)

    def test_moves_the_tag_to_main_and_verifies_it(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.remote_tag(), self.new)
        self.assertIn(f"install-v1 -> {self.new} (was {self.old})", r.stdout)
        again = self.run_script()
        self.assertIn("already points at", again.stdout)

    def test_refuses_a_commit_that_is_not_on_main_or_whose_installer_was_not_tested(self):
        git(self.clone, "checkout", "-q", "-b", "side")
        (self.clone / "install" / "citygml.sh").write_text("v3\n")
        git(self.clone, "commit", "-q", "-am", "three")
        side = git(self.clone, "rev-parse", "HEAD")
        r = self.run_script(side)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not on origin/main", r.stderr)
        git(self.clone, "checkout", "-q", "main")
        (self.clone / "install" / "citygml.sh").write_text("edited but not committed\n")
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("differs from the working tree", r.stderr)
        self.assertEqual(self.remote_tag(), self.old)


if __name__ == "__main__":
    unittest.main()
