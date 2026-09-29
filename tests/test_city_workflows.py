# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Contract between tools/ci and the thin wrapper workflows shipped in city-template
(mirrored into sample cities when they adopt that tools version).

Runs only when the sibling repositories are checked out next to tools/ (the
public layout: <root>/tools, <root>/city-template, <root>/sample-*-station).
"""
from __future__ import annotations

from collections import defaultdict
try:
    from scripts.audit_tools_pins import audit
except ImportError:      # the pin audit script is not published yet
    audit = None
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT.parent
SIBLING_SUFFIX = REPO_ROOT.name.removeprefix("tools")   # sibling checkouts share this checkout's suffix
TEMPLATE = ROOT / ("city-template" + SIBLING_SUFFIX)
SAMPLES = sorted(ROOT.glob("sample-*-station" + SIBLING_SUFFIX))
MIRRORED = ("pr-analysis.yml", "pr-comment.yml", "pr-recheck.yml", "pr-base-freshness.yml", "history-index.yml",
            "review-report.yml")   # the starter kit is gone (hub-v1.2.0); review-report is compared where a repository has it


@unittest.skipUnless(TEMPLATE.is_dir() and SAMPLES, "sibling city repositories not checked out")
class CityWorkflowContractTest(unittest.TestCase):
    def _wf(self, repo: Path, name: str) -> str:
        return (repo / ".github" / "workflows" / name).read_text(encoding="utf-8")

    @unittest.skipIf(audit is None, "scripts/audit_tools_pins.py not present")
    def test_city_pins_are_consistent_and_same_version_wrappers_match(self) -> None:
        cohorts = defaultdict(list)
        for repo in [TEMPLATE, *SAMPLES]:
            result = audit(repo)
            self.assertTrue(result['ok'], result['errors'])
            cohorts[(result['tools_repository_declaration'], result['tools_commit'])].append(repo)
        for version, repos in cohorts.items():
            for name in MIRRORED:
                # Older cohorts may not yet include a newer optional workflow.
                contents = {(r / '.github/workflows' / name).read_bytes()
                            if (r / '.github/workflows' / name).exists() else None for r in repos}
                self.assertEqual(len(contents), 1, f'{name} differs within adopted version {version}: {repos}')

    def test_tools_repository_defaults_to_4dcitygml_for_federated_city_repos(self) -> None:
        """A city repository hosted outside the 4dcitygml organization (a
        municipality's own org) must still fetch the shared CI logic; deriving the
        owner from the city repository would point at a non-existent <org>/tools."""
        wf = self._wf(TEMPLATE, "pr-analysis.yml")
        self.assertNotIn("github.repository_owner", wf)
        self.assertIn("CITYGML_TOOLS_REPO: ${{ vars.CITYGML_TOOLS_REPO || '4dcitygml/tools' }}", wf)

    def test_posting_workflow_truncates_instead_of_failing_on_long_comments(self) -> None:
        # The posting logic is one script the thin workflow calls; its limits are the contract.
        wf = self._wf(TEMPLATE, "pr-comment.yml")
        self.assertIn("python .github/scripts/post_comments.py", wf)
        import importlib.util, sys
        from unittest.mock import patch
        scripts = TEMPLATE / ".github" / "scripts"
        spec = importlib.util.spec_from_file_location("post_comments", scripts / "post_comments.py")
        post = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(scripts), *sys.path]):   # it imports its siblings by name
            spec.loader.exec_module(post)
        self.assertEqual(post.COMMENT_LIMIT, 60000)          # GitHub rejects bodies over 65,536 characters
        self.assertEqual(post.ARTIFACT_LIMIT, 4194304)       # absurd sizes are still rejected
        self.assertIn("Truncated to fit the GitHub comment size limit", post.TRUNCATED)
        body = post.truncated(b"x" * (post.COMMENT_LIMIT + 10))
        self.assertLessEqual(len(body), post.COMMENT_LIMIT + len(post.TRUNCATED.encode()))
        self.assertTrue(body.endswith(post.TRUNCATED.encode()))


if __name__ == "__main__":
    unittest.main()
