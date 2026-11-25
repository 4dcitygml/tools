#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/release_tags.py: the one release tag grammar (W8: about ten copies before)."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from scripts import release_tags as R


class TestTheGrammar(unittest.TestCase):
    def test_the_two_series_and_a_suffix(self):
        self.assertEqual(R.parse("hub-v2.0.0"), ("hub", (2, 0, 0), ""))
        self.assertEqual(R.parse("tools-v2.0.0-rc.1"), ("tools", (2, 0, 0), "rc.1"))
        for bad in ("v2.0.0", "hub-2.0.0", "install-v1", "hub-v2.0", "hub-v2.0.0-", "hub-v2.0.0 ", None, 3):
            self.assertIsNone(R.parse(bad), bad)

    def test_versions_and_final_releases(self):
        self.assertEqual(R.version_key("hub-v2.0.0-rc.1"), (2, 0, 0))   # a running prerelease hub has a version
        self.assertIsNone(R.version_key("tools-v2.0.0"))                 # not a hub tag

    def test_a_suffixed_tag_is_a_prerelease_and_never_latest(self):
        # D-5-1: a suffixed hub tag was published as a normal (latest) release
        self.assertEqual(R.facts("hub", "hub-v2.0.0"), {"version": "v2.0.0", "prerelease": "false", "latest": "true"})
        self.assertEqual(R.facts("hub", "hub-v2.0.0-rc.1"), {"version": "v2.0.0-rc.1", "prerelease": "true", "latest": "false"})
        self.assertEqual(R.facts("tools", "tools-v2.0.0")["latest"], "false")   # the hub owns "latest"
        with self.assertRaises(ValueError):
            R.facts("hub", "tools-v2.0.0")

    def test_the_command_line_writes_github_output_lines(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(R.main(["check", "hub", "hub-v2.0.0-rc.1"]), 0)
        self.assertEqual(out.getvalue().split(), ["version=v2.0.0-rc.1", "prerelease=true", "latest=false"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(R.main(["check", "hub", "hub-vlatest"]), 1)


class TestTheInstallersCopies(unittest.TestCase):
    """The installers keep their own copies of the hub form (they run before any tools checkout);
    each accepts exactly the hub tags release_tags accepts (D-5-1: the ps1 test was unanchored)."""
    SAMPLES = ("hub-v2.0.0", "hub-v2.0.0-rc.1", "hub-v10.20.30", "hub-v2.0.0-", "hub-v2.0", "hub-v2.0.0x",
               "hub-v2.0.0-rc 1", "tools-v2.0.0", "v2.0.0", "hub-v2.0.0/../x")

    def copies(self):
        import re
        from pathlib import Path
        install = Path(__file__).resolve().parents[1] / "install" / "release"
        sh = re.findall(r're\.fullmatch\(r"(hub-v[^"]+)"', (install / "citygml.sh").read_text(encoding="utf-8"))
        ps1 = re.findall(r'-match "(\^hub-v[^"]+)"', (install / "citygml.ps1").read_text(encoding="utf-8"))
        self.assertEqual((len(sh), len(ps1)), (2, 1))
        return [re.compile(rx if rx.startswith("^") else f"^(?:{rx})$") for rx in sh + ps1]

    def test_every_copy_accepts_what_the_grammar_accepts(self):
        for rx in self.copies():
            for tag in self.SAMPLES:
                self.assertEqual(bool(rx.match(tag)), R.version_key(tag) is not None, (rx.pattern, tag))


if __name__ == "__main__":
    unittest.main()
