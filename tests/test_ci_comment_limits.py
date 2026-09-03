# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Bulk PRs (source baselines, annual source updates) must not produce CI comments
that GitHub refuses (65,536-character limit): the reviewability lint caps the
listed warnings and the preview extractor gives up instead of emitting a URL
fragment of hundreds of kilobytes.
"""
from __future__ import annotations

import unittest

from scripts import extract_building_preview as preview
from scripts.reviewability_lint import MAX_LISTED_WARNINGS, render_markdown


class ReviewabilityWarningCapTest(unittest.TestCase):
    def _files(self, n: int) -> list[dict]:
        return [{
            "file": "a.gml",
            "counts": {"added": n, "deleted": 0, "modified": 0},
            "warnings": [
                {"type": "lifecycle_added", "file": "a.gml", "id": f"bldg_{i}"}
                for i in range(n)
            ],
        }]

    def test_small_lists_are_complete(self) -> None:
        md = render_markdown(self._files(3), threshold=100)
        self.assertEqual(md.count("Reason required for added building"), 3)
        self.assertNotIn("more warnings not listed", md)

    def test_bulk_lists_are_capped_but_counted(self) -> None:
        n = MAX_LISTED_WARNINGS * 30  # a baseline-sized PR
        md = render_markdown(self._files(n), threshold=5)
        self.assertEqual(md.count("Reason required for added building"), MAX_LISTED_WARNINGS)
        self.assertIn(f"… and {n - MAX_LISTED_WARNINGS} more warnings not listed", md)
        self.assertIn(f"Warnings ({n + 1})", md)  # exact count incl. the large-change line
        self.assertLess(len(md), 20000)


class PreviewUrlCapTest(unittest.TestCase):
    def _pairs(self, n: int) -> list[dict]:
        import random
        rnd = random.Random(1)
        return [{
            "id": f"bldg_{i}",
            "old": {"tex": [rnd.random() for _ in range(50)], "geom": [rnd.random() for _ in range(300)]},
            "new": {"tex": [rnd.random() for _ in range(50)], "geom": [rnd.random() for _ in range(300)]},
        } for i in range(n)]

    def test_texture_stripping_still_returns_a_url(self) -> None:
        url = preview._finalize_url(self._pairs(3), "https://x.example/v")
        self.assertTrue(url.startswith("https://x.example/v/#"))
        self.assertLessEqual(len(url), preview.MAX_URL_LEN)

    def test_oversized_payload_yields_no_url(self) -> None:
        url = preview._finalize_url(self._pairs(400), "https://x.example/v")
        self.assertEqual(url, "")


if __name__ == "__main__":
    unittest.main()
