# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/repo_git.py: blob(), the one `git show <sha>:<path>` of the scripts (A9)."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import repo_git


class TestBlob(unittest.TestCase):
    def test_bytes_at_the_commit_or_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True)
            run("init", "-q")
            (repo / "a.gml").write_bytes(b"<a/>\n")
            run("add", "a.gml")
            run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "a")
            (repo / "a.gml").write_bytes(b"changed in the working tree")
            self.assertEqual(repo_git.blob(repo, "HEAD", "a.gml"), b"<a/>\n")
            self.assertIsNone(repo_git.blob(repo, "HEAD", "missing.gml"))
            self.assertIsNone(repo_git.blob(repo, "0" * 40, "a.gml"))


if __name__ == "__main__":
    unittest.main()
