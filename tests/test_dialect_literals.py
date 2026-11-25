# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""PLATEAU / i-UR knowledge lives in its dialect pack, dialects/plateau/ (X1, REQ-dialect-packs.md
section 1): no new literal outside it. The literals still elsewhere are counted per file in
dialect_literals.json; the count only goes down, so a file that loses some lowers its entry, and
a move into the pack shows as a smaller list."""
from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LITERAL = re.compile(r"plateau|\bi-?ur\b|\buro\b|\budx\b|geospatial\.jp", re.IGNORECASE)
PRODUCT = ("ci", "dialects", "scripts", "tools")
PACK = "dialects/plateau/"
BASELINE = Path(__file__).with_name("dialect_literals.json")


def counts() -> "dict[str, int]":
    files = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "--", *PRODUCT],
                           capture_output=True, text=True, check=True).stdout.split()
    found = {}
    for rel in files:
        path = REPO_ROOT / rel
        if rel.startswith(PACK) or not path.is_file():
            continue
        n = len(LITERAL.findall(path.read_text(encoding="utf-8", errors="ignore")))
        if n:
            found[rel] = n
    return found


class DialectLiterals(unittest.TestCase):
    def test_no_new_literal_outside_the_pack(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        now = counts()
        grown = {f: (baseline.get(f, 0), n) for f, n in now.items() if n > baseline.get(f, 0)}
        self.assertEqual(grown, {}, "PLATEAU / i-UR knowledge belongs in dialects/plateau/ (file: (allowed, found))")
        shrunk = {f: (n, now.get(f, 0)) for f, n in baseline.items() if now.get(f, 0) < n}
        self.assertEqual(shrunk, {}, "fewer literals: lower tests/dialect_literals.json to the new counts")

    def test_the_pattern_finds_the_profile(self):
        for text in ("PLATEAU", "i-UR", "iur-3.0", "uro:buildingID", "/udx/bldg/", "www.geospatial.jp"):
            self.assertTrue(LITERAL.search(text), text)
        for text in ("europe", "furo", "iurisdiction", "udxx"):
            self.assertFalse(LITERAL.search(text), text)


if __name__ == "__main__":
    unittest.main()
