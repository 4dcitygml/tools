#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The published exchange contract (docs/exchange-contract.md) stays in sync
with the code that enforces it, and the Created-By client-identification
trailer behaves as specified (emitted by our editors, ignored by the gates)."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.commit_building_scope import _trailers  # noqa: E402

_attr_spec = importlib.util.spec_from_file_location(
    "attr_app", REPO_ROOT / "tools" / "attr_editor" / "app.py")
attr = importlib.util.module_from_spec(_attr_spec)
_attr_spec.loader.exec_module(attr)

DOC = (REPO_ROOT / "docs" / "exchange-contract.md").read_text(encoding="utf-8")


class TestCreatedByTrailer(unittest.TestCase):
    def test_with_and_without_release_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(attr.created_by_trailer(root, "citygml-attr-editor"),
                             "Created-By: citygml-attr-editor")
            (root / "install").mkdir()
            (root / "install" / "tools-release.json").write_text(
                json.dumps({"tag": "tools-v1.2.3"}), encoding="utf-8")
            self.assertEqual(attr.created_by_trailer(root, "citygml-attr-editor"),
                             "Created-By: citygml-attr-editor/tools-v1.2.3")

    def test_commit_gate_ignores_created_by(self):
        # Part B says Created-By is convention, not enforcement: the commit
        # scope gate must keep parsing identity trailers and skip Created-By.
        message = ("Update attributes (Usage): 401 → 402\n\n"
                   "Building: 13101-bldg-1\n"
                   "Created-By: some-third-party-tool/9.9 (mail@example.com)\n")
        trailers = _trailers(message)
        self.assertEqual(trailers.get("Building"), ["13101-bldg-1"])
        self.assertNotIn("Created-By", trailers)


class TestContractDocMatchesCode(unittest.TestCase):
    """Light doc↔code sync pins: if an enforced literal changes, the published
    contract must be updated in the same change."""

    def test_version_and_rfc2119(self):
        self.assertIn("v2.1.0", DOC)   # current version (title + changelog)
        self.assertIn("v2.0.0", DOC)   # the changelog keeps every published version
        self.assertIn("RFC 2119", DOC)

    def test_bulk_submission_clause(self):
        # A7 (v2.1.0): bulk submissions carry a provenance manifest and are verified by reproduction.
        for token in ("### A7.", "Provenance-Manifest:", "bulk-manifest.schema.json",
                      "bulk-submission-provenance.md", "identity-baseline", "identity-correction",
                      "Building-ID-From:", "Building-ID-To:", "Identity-Evidence:"):
            self.assertIn(token, DOC, token)
        self.assertTrue((REPO_ROOT / "docs" / "bulk-submission-provenance.md").is_file())
        self.assertTrue((REPO_ROOT / "schemas" / "provenance" / "bulk-manifest.schema.json").is_file())

    def test_reason_contract(self):
        self.assertIn("<!--sec:reason-->", DOC)
        for placeholder in ("please fill in", "not filled in", "記入してください",
                            "未記入", "TODO", "TBD"):
            self.assertIn(placeholder, DOC)

    def test_trailer_names(self):
        for trailer in ("Building:", "Building-Added:", "Building-Deleted:",
                        "Change-Type:", "Created-By:"):
            self.assertIn(trailer, DOC)
        for change_type in ("lifecycle", "layout", "source-baseline", "scope-extract"):
            self.assertIn(change_type, DOC)

    def test_classification_prefixes(self):
        for prefix in ("edit/", "tex/", "Update attributes", "Update building info",
                       "属性修正", "Attributkorrektur", "Update textures",
                       "Add textures", "テクスチャ", "Textur"):
            self.assertIn(prefix, DOC)

    def test_inspection_keys(self):
        for key in ("reason", "commit-scope", "scope-reproducibility", "reproduction", "freshness",
                    "file-scope", "schema", "minimal-diff", "texture", "structure",
                    "plausibility", "topology", "model"):
            self.assertIn(f"`{key}`", DOC)


if __name__ == "__main__":
    unittest.main()
