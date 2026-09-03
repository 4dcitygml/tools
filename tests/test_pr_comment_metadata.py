#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for pr_comment_metadata (W4 / feature list D-3).

Clean-room tests from the functional requirements; not a copy of the old implementation.
Run: python -m unittest tests.test_pr_comment_metadata
"""
from __future__ import annotations

import unittest

from scripts.citygml_constants import METADATA_MARKER
from scripts.pr_comment_metadata import build_metadata, parse_comment, render_comment


class TestMetadata(unittest.TestCase):
    def test_roundtrip(self) -> None:
        meta = build_metadata(
            pr=7,
            branch="demo/pr-a-attr",
            base_sha="a" * 40,
            head_sha="b" * 40,
            changed_files=["udx/bldg/x.gml"],
            preview_url="https://v/#H4sIA",
        )
        body = render_comment(meta)
        self.assertTrue(body.startswith(METADATA_MARKER))
        self.assertEqual(parse_comment(body), meta)

    def test_marker_hidden_payload_has_no_double_dash(self) -> None:
        # Even with `--` in preview_url, base64 encoding keeps the hidden comment intact
        meta = build_metadata(7, "b", "s", "h", ["f.gml"], preview_url="http://x/#aa--bb--cc")
        body = render_comment(meta)
        payload_line = [ln for ln in body.splitlines() if ln.startswith("<!-- citygml-meta:")][0]
        inner = payload_line[len("<!-- citygml-meta:"):-len(" -->")]
        self.assertNotIn("--", inner)  # the standard base64 alphabet does not include `-`
        self.assertEqual(parse_comment(body)["preview_url"], "http://x/#aa--bb--cc")

    def test_preview_url_optional(self) -> None:
        meta = build_metadata(1, "b", "s", "h", ["f.gml"])
        self.assertNotIn("preview_url", meta)
        self.assertEqual(parse_comment(render_comment(meta)), meta)

    def test_parse_returns_none_without_payload(self) -> None:
        self.assertIsNone(parse_comment("just a comment body"))


if __name__ == "__main__":
    unittest.main()
