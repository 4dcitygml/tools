# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""One entry point for the operator tools (S14, stage 1): python3 -m scripts."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import __main__ as entry
from tests.support import REPO_ROOT


def runnable() -> "set[str]":
    return {p.stem for p in (REPO_ROOT / "scripts").glob("*.py")
            if p.stem != "__main__" and re.search(r"__name__ == [\"']__main__[\"']", p.read_text(encoding="utf-8"))}


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "scripts", *args], capture_output=True, text=True, cwd=REPO_ROOT)


class TestOperatorTools(unittest.TestCase):
    def test_every_runnable_tool_is_in_exactly_one_group(self):
        listed = [t for tools in entry.GROUPS.values() for t in tools]
        self.assertEqual(len(listed), len(set(listed)), "a tool is in two groups")
        self.assertEqual(set(listed), runnable())

    def test_every_tool_says_what_it_does(self):
        for tools in entry.GROUPS.values():
            for tool in tools:
                self.assertTrue(entry.summary(tool), tool)

    def test_the_page_is_generated(self):
        page = (REPO_ROOT / "docs" / "operator-tools.md").read_text(encoding="utf-8")
        self.assertEqual(page, entry.markdown(), "run: python3 -m scripts --markdown > docs/operator-tools.md")

    def test_a_tool_runs_as_by_its_path(self):
        self.assertEqual(run("--list").returncode, 0)
        self.assertEqual(run("no_such_tool").returncode, 2)
        with tempfile.TemporaryDirectory() as t:
            event = Path(t) / "event.json"
            event.write_text(json.dumps({"pull_request": {"body": "## Reason <!--sec:reason-->\nTODO\n"}}), encoding="utf-8")
            by_entry = run("pr_reason", str(event))
            by_path = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "pr_reason.py"), str(event)],
                                     capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual((by_entry.returncode, by_entry.stdout), (by_path.returncode, by_path.stdout))
        self.assertEqual(by_entry.returncode, 1)


if __name__ == "__main__":
    unittest.main()
