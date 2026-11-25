# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The analysis judges the PR as GitHub holds it (scripts/pr_event.py). Found in a rehearsal,
2026-10-02: a PR reopened after main moved kept the event's old base in the inspection context,
the poster refused it, and a re-run read the same payload. D23 (v2.0.0): the gates read the same
live PR as the report's context, so a re-run after main moved judges the new base."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.pr_event import live_pr as current_pr, main as write_event

REPO_ROOT = Path(__file__).resolve().parent.parent

PAYLOAD = {"number": 20, "head": {"sha": "h1"}, "base": {"sha": "72a5bf1"}, "title": "t"}
ENV = {"GITHUB_REPOSITORY": "o/city", "GITHUB_TOKEN": "tok"}


class TestCurrentPr(unittest.TestCase):
    def test_one_file_feeds_the_gates_and_the_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / "event.json"
            payload.write_text(json.dumps({"action": "synchronize", "pull_request": PAYLOAD}))
            live = {**PAYLOAD, "base": {"sha": "c8f1285"}, "title": "edited"}
            self.assertEqual(write_event([str(payload)], {**ENV, "RUNNER_TEMP": tmp}, fetch=lambda url: live), 0)
            event = json.loads((Path(tmp) / "citygml" / "event.json").read_text())   # in the run folder (U3)
        self.assertEqual((event["action"], event["pull_request"]), ("synchronize", live))
        # every step of the analysis reads that file, not the payload (D23)
        driver = (REPO_ROOT / "ci" / "pr_analysis_main.sh").read_text(encoding="utf-8")
        self.assertIn('EVENT="$("$PY" "$TOOLS_DIR/scripts/pr_event.py" "${GITHUB_EVENT_PATH:?}")"', driver)
        self.assertLess(driver.index("scripts/pr_event.py"), driver.index("BASE_SHA="))
        for later in ("topology_gate.sh", "inspection_summary.sh"):
            text = (REPO_ROOT / "ci" / later).read_text(encoding="utf-8")
            self.assertIn("${CITYGML_EVENT:-", text, later)
        for script in sorted((REPO_ROOT / "ci").glob("*.sh")):
            text = script.read_text(encoding="utf-8")
            reads = text.count("GITHUB_EVENT_PATH") - text.count("${CITYGML_EVENT:-") - text.count("scripts/pr_event.py")
            self.assertLessEqual(reads, 0, script.name)   # no step reads the payload on its own

    def test_the_live_base_while_the_head_is_the_analysed_one(self):
        urls = []
        live = {**PAYLOAD, "base": {"sha": "c8f1285"}}
        got = current_pr(PAYLOAD, ENV, fetch=lambda url: urls.append(url) or live)
        self.assertEqual(got["base"]["sha"], "c8f1285")
        self.assertEqual(urls, ["https://api.github.com/repos/o/city/pulls/20"])

    def test_a_newer_head_keeps_the_payload(self):
        # the run analysed h1; a report for h2 would claim a commit nobody inspected
        got = current_pr(PAYLOAD, ENV, fetch=lambda url: {**PAYLOAD, "head": {"sha": "h2"}, "base": {"sha": "x"}})
        self.assertIs(got, PAYLOAD)

    def test_the_payload_when_github_cannot_be_read(self):
        def fail(url):
            raise OSError("offline")
        self.assertIs(current_pr(PAYLOAD, ENV, fetch=fail), PAYLOAD)
        self.assertIs(current_pr(PAYLOAD, {}, fetch=lambda url: self.fail("no repository, no call")), PAYLOAD)


if __name__ == "__main__":
    unittest.main()
