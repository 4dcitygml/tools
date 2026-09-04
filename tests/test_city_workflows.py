# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Contract between tools/ci and the thin wrapper workflows shipped in city-template
(and mirrored verbatim into the sample city repositories).

Runs only when the sibling repositories are checked out next to tools/ (the
public layout: <root>/tools, <root>/city-template, <root>/sample-*-station).
"""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE = ROOT / "city-template"
SAMPLES = sorted(ROOT.glob("sample-*-station"))
MIRRORED = ("pr-analysis.yml", "pr-comment.yml", "pr-recheck.yml", "pr-base-freshness.yml", "history-index.yml",
            "starter-kit.yml")


@unittest.skipUnless(TEMPLATE.is_dir() and SAMPLES, "sibling city repositories not checked out")
class CityWorkflowContractTest(unittest.TestCase):
    def _wf(self, repo: Path, name: str) -> str:
        return (repo / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_wrapper_workflows_are_identical_across_city_repositories(self) -> None:
        for name in MIRRORED:
            digests = {hashlib.sha256(self._wf(r, name).encode()).hexdigest(): r.name
                       for r in [TEMPLATE, *SAMPLES]}
            self.assertEqual(len(digests), 1, f"{name} differs between: {sorted(digests.values())}")

    def test_tools_repository_defaults_to_4dcitygml_for_federated_city_repos(self) -> None:
        """A city repository hosted outside the 4dcitygml organization (a
        municipality's own org) must still fetch the shared CI logic; deriving the
        owner from the city repository would point at a non-existent <org>/tools."""
        wf = self._wf(TEMPLATE, "pr-analysis.yml")
        self.assertNotIn("github.repository_owner", wf)
        self.assertIn("CITYGML_TOOLS_REPO: ${{ vars.CITYGML_TOOLS_REPO || '4dcitygml/tools' }}", wf)

    def test_posting_workflow_truncates_instead_of_failing_on_long_comments(self) -> None:
        wf = self._wf(TEMPLATE, "pr-comment.yml")
        self.assertIn('if [ "$size" -gt 60000 ]; then', wf)
        self.assertIn("head -c 60000", wf)
        self.assertIn("Truncated to fit the GitHub comment size limit", wf)
        self.assertIn('-gt 4194304', wf)  # absurd sizes are still rejected


if __name__ == "__main__":
    unittest.main()
