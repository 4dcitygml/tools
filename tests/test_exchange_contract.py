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
        self.assertIn("Exchange Contract v3.1.0", DOC)   # current version (title)
        for published in ("v3.1.0", "v3.0.0", "v2.1.0", "v2.0.0"):   # the changelog keeps every published version
            self.assertIn(f"**{published}**", DOC)

    def test_city_repository_scope_a11(self):
        # A11 mirrors scripts/repo_scope.py: the accepted categories, the pin rule, the label.
        from scripts import repo_scope as rs
        for token in ("### A11.", "`data_dirs`", "`provenance/`", "`.github/CODEOWNERS`", "`CITYGML_TOOLS_REF:`",
                      "`tools-v*`", "`tooling`", "`install/**`", "`tools/**`"):
            self.assertIn(token, DOC, token)
        for suffix in (".py", ".sh", ".command", ".ps1", ".bat", ".js"):
            self.assertIn(suffix, rs.CODE_SUFFIXES)
        self.assertEqual(rs.TOOLING_LABEL, "tooling")
        self.assertIn("RFC 2119", DOC)

    def test_contract_is_the_authority(self):
        # v3.0.0: the document outranks every tool, and the official tools are reference clients only.
        self.assertIn("highest authority", DOC)
        self.assertIn("reference clients", DOC)
        self.assertIn("re-implement a gate", DOC)
        self.assertIn("MUST show every open PR", DOC)

    def test_trailers_read_by_the_scope_gate_are_all_documented(self):
        # Every trailer the commit scope gate parses must appear in the contract, and vice versa (A2).
        from scripts.commit_building_scope import _TRAILER_RE
        pattern = _TRAILER_RE.pattern
        names = pattern[pattern.index("^(") + 2:pattern.index("):")].split("|")
        for name in names:
            self.assertIn(f"`{name}:`", DOC, name)
        self.assertIn("`Scope-Municipality:`", DOC)

    def test_reinspection_request_interface(self):
        # A8 mirrors pr-recheck.yml: marker, optional workflow marker, authorization.
        for token in ("<!-- citygml-ci-retry-request -->",
                      "<!-- citygml-retry-workflow:pr-comment.yml -->",
                      "<!-- citygml-retry-workflow:review-report.yml -->",
                      "`OWNER`, `MEMBER` or `COLLABORATOR`"):
            self.assertIn(token, DOC, token)

    def test_labels_and_markers(self):
        for label in ("`texture-override`", "`identity-review`", "`tooling`"):
            self.assertIn(label, DOC)
        self.assertIn("`city-review` was a label", DOC)
        for marker in ("citygml-automatic-inspection", "citygml-change-summary",
                       "citygml-base-freshness", "citygml-auto-resubmission",
                       "citygml-commit-scope", "citygml-reviewability-lint", "citygml-quality-lint"):
            self.assertIn(f"<!-- {marker} -->", DOC, marker)

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
        # The A5 table in the contract and the one table CI uses (scripts/pr_classification.py) are the same.
        from scripts import pr_classification as pc
        for cls in pc.NAME_CLASSES:
            self.assertIn(f"`{cls}`", DOC)
            for literal in pc.BRANCH_PREFIXES[cls] + pc.TITLE_PREFIXES[cls] + pc.TITLE_KEYWORDS[cls]:
                self.assertIn(f"`{literal}`", DOC, literal)
        ci = (REPO_ROOT / "ci" / "pr_analysis_main.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/pr_classification.py", ci)
        self.assertIn("`other`", DOC)          # v3.0.0: `other` is an explicit class
        self.assertIn("`classification`", DOC)  # and its gate

    def test_inspection_keys(self):
        for key in ("reason", "classification", "commit-scope", "scope-reproducibility", "reproduction", "freshness",
                    "file-scope", "schema", "minimal-diff", "texture", "structure",
                    "plausibility", "topology", "model"):
            self.assertIn(f"`{key}`", DOC)


if __name__ == "__main__":
    unittest.main()
