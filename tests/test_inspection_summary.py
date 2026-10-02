# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/inspection_summary.py reads the PR as GitHub holds it (found in a rehearsal,
2026-10-02): a PR reopened after main moved kept the event's old base in the inspection context,
the poster refused it, and a re-run read the same payload."""
from __future__ import annotations

import unittest

from scripts.inspection_summary import current_pr

PAYLOAD = {"number": 20, "head": {"sha": "h1"}, "base": {"sha": "72a5bf1"}, "title": "t"}
ENV = {"GITHUB_REPOSITORY": "o/city", "GITHUB_TOKEN": "tok"}


class TestCurrentPr(unittest.TestCase):
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
