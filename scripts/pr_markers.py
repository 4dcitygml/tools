#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The machine-readable marks that CI and the clients exchange in pull requests
(Exchange Contract A6, A8, A10). One table: CI writes them, the hub reads them.

CI shell scripts cannot import this module and carry the same strings as literals;
tests/test_pr_markers.py keeps every literal in the repository equal to an entry here.
Shipped next to the hub as `program/pr_markers.py` (scripts/build_bundle.py).
"""
from __future__ import annotations

import re

# One comment per marker; each is posted or updated on its own (A10).
INSPECTION = "<!-- citygml-automatic-inspection -->"       # the inspection summary, one <!--cp:key--> row per gate
CHANGE_SUMMARY = "<!-- citygml-change-summary -->"          # the table of changed values from the diff
COMMIT_SCOPE = "<!-- citygml-commit-scope -->"              # findings of the commit-scope gate
REVIEWABILITY_LINT = "<!-- citygml-reviewability-lint -->"
QUALITY_LINT = "<!-- citygml-quality-lint -->"
PLAUSIBILITY_LINT = "<!-- plausibility-lint -->"
VAL3DITY = "<!-- val3dity-topology-gate -->"
PREVIEW = "<!-- cesium-building-preview -->"
METADATA = "<!-- citygml-metadata -->"
SUGGESTED_COMMIT = "<!-- citygml-suggested-commit -->"
BULK_REPRODUCTION = "<!-- citygml-bulk-reproduction -->"
AUTO_RESUBMISSION = "<!-- citygml-auto-resubmission -->"    # CI's request to fix and resubmit, for people
RETRY_REQUEST = "<!-- citygml-ci-retry-request -->"         # written by clients, read by CI (A8)
REVIEW_REPORT = "<!-- citygml-review-report -->"            # the report for people (A10); its owner is the contract master
                                                            # tools/hub/operator_explanation.py (REPORT_MARKER)

# Anchors inside a comment or a PR body.
def checkpoint(key: str) -> str:
    """The anchor of one gate's row in the inspection summary (A6)."""
    return f"<!--cp:{key}-->"


CHECKPOINT_RE = re.compile(r"<!--\s*cp:([a-z0-9-]+)\s*-->")
SECTION_REASON = "<!--sec:reason-->"                        # the heading of the reason section of a PR body (A2)


# The labels CI reads (A9), applied by maintainers only. The analysis driver reads the first two
# by name (shell), repo_scope the third. The tuple's owner is the contract master
# (tools/hub/operator_explanation.LABELS, which travels to the cities); tests keep this one,
# the master and the contract's table equal.
TEXTURE_OVERRIDE_LABEL = "texture-override"   # an existing image may be replaced (A4)
IDENTITY_REVIEW_LABEL = "identity-review"     # tier-C identity links in an identity-correction (A7)
TOOLING_LABEL = "tooling"                     # CI maintenance under .github/ (A11)
LABELS = (TEXTURE_OVERRIDE_LABEL, IDENTITY_REVIEW_LABEL, TOOLING_LABEL)


COMMENT_MARKERS = (INSPECTION, CHANGE_SUMMARY, COMMIT_SCOPE, REVIEWABILITY_LINT, QUALITY_LINT, PLAUSIBILITY_LINT,
                   VAL3DITY, PREVIEW, METADATA, SUGGESTED_COMMIT, BULK_REPRODUCTION, AUTO_RESUBMISSION,
                   RETRY_REQUEST, REVIEW_REPORT)
KNOWN = set(COMMENT_MARKERS) | {SECTION_REASON}
# Parameterised forms a literal may take: <!--cp:key-->, <!-- citygml-meta:base64 -->.
KNOWN_PATTERNS = (CHECKPOINT_RE, re.compile(r"<!-- citygml-meta:[^ >]+ -->"))
