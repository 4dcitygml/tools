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
# v3.4.0: the former name of PLAUSIBILITY_LINT, deprecated; written beside it until v4.0.0
PLAUSIBILITY_LINT_LEGACY = "<!-- plateau-quality-lint -->"
VAL3DITY = "<!-- val3dity-topology-gate -->"
PREVIEW = "<!-- cesium-building-preview -->"
METADATA = "<!-- citygml-metadata -->"
SUGGESTED_COMMIT = "<!-- citygml-suggested-commit -->"
BULK_REPRODUCTION = "<!-- citygml-bulk-reproduction -->"
BASE_FRESHNESS = "<!-- citygml-base-freshness -->"          # a state comment: one per PR, edited between states
AUTO_RESUBMISSION = "<!-- citygml-auto-resubmission -->"    # CI asked the proposer to fix and resubmit
RETRY_REQUEST = "<!-- citygml-ci-retry-request -->"         # written by clients, read by CI (A8)
REVIEW_REPORT = "<!-- citygml-review-report -->"            # the machine report; its owner is the contract master
                                                            # tools/hub/operator_explanation.py (REPORT_MARKER)

# The state of a state comment, edited in place.
STATUS_PENDING = "<!-- status:pending -->"
STATUS_ACTIVE = "<!-- status:active -->"
STATUS_RESOLVED = "<!-- status:resolved -->"


def status(state: str) -> str:
    return f"<!-- status:{state} -->"


# Anchors inside a comment or a PR body.
def checkpoint(key: str) -> str:
    """The anchor of one gate's row in the inspection summary (A6)."""
    return f"<!--cp:{key}-->"


CHECKPOINT_RE = re.compile(r"<!--\s*cp:([a-z0-9-]+)\s*-->")
SECTION_REASON = "<!--sec:reason-->"                        # the heading of the reason section of a PR body (A2)


# The labels CI reads (A9), applied by maintainers only. The analysis driver reads the first two
# by name (shell), repo_scope the third; tests keep them and the contract's table equal to this.
TEXTURE_OVERRIDE_LABEL = "texture-override"   # an existing image may be replaced (A4)
IDENTITY_REVIEW_LABEL = "identity-review"     # tier-C identity links in an identity-correction (A7)
TOOLING_LABEL = "tooling"                     # CI maintenance under .github/ (A11)
LABELS = (TEXTURE_OVERRIDE_LABEL, IDENTITY_REVIEW_LABEL, TOOLING_LABEL)


def retry_workflow(name: str) -> str:
    """Names the workflow a re-inspection request asks to run again (A8)."""
    return f"<!-- citygml-retry-workflow:{name} -->"


COMMENT_MARKERS = (INSPECTION, CHANGE_SUMMARY, COMMIT_SCOPE, REVIEWABILITY_LINT, QUALITY_LINT, PLAUSIBILITY_LINT,
                   VAL3DITY, PREVIEW, METADATA, SUGGESTED_COMMIT, BULK_REPRODUCTION, BASE_FRESHNESS,
                   AUTO_RESUBMISSION, RETRY_REQUEST, REVIEW_REPORT)
KNOWN = set(COMMENT_MARKERS) | {PLAUSIBILITY_LINT_LEGACY, STATUS_PENDING, STATUS_ACTIVE, STATUS_RESOLVED, SECTION_REASON}
# Parameterised forms a literal may take: <!--cp:key-->, <!-- citygml-retry-workflow:x.yml -->,
# <!-- citygml-ci-context:digest:run:attempt -->, <!-- citygml-meta:base64 -->.
KNOWN_PATTERNS = (CHECKPOINT_RE, re.compile(r"<!-- citygml-retry-workflow:[A-Za-z0-9_.-]+ -->"),
                  re.compile(r"<!-- citygml-ci-context:[^ >]+ -->"), re.compile(r"<!-- citygml-meta:[^ >]+ -->"))


def has_active(comments: list, marker: str) -> bool:
    """Whether a state comment carrying `marker` is currently active."""
    return any(marker in str(c.get("body") or "") and STATUS_ACTIVE in str(c.get("body") or "") for c in comments)
