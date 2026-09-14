#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""scripts/release_guard.py: a release tag is pushed only when the production approval gate is in place."""
from __future__ import annotations

import unittest

from scripts.release_guard import check_environment, check_tag_name, check_workflow

REVIEWER = {"type": "required_reviewers", "prevent_self_review": False, "reviewers": [{"type": "User", "reviewer": {"login": "m"}}]}
ENV = {"name": "production", "protection_rules": [REVIEWER],
       "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
POLICIES = [{"name": "hub-v*", "type": "tag"}, {"name": "tools-v*", "type": "tag"}, {"name": "install-v*", "type": "tag"}]
WORKFLOW = "jobs:\n  common-tools:\n    runs-on: x\n  release:\n    needs: common-tools\n    environment: production\n"


class TestGuard(unittest.TestCase):
    def test_tag_names(self):
        self.assertEqual(check_tag_name("tools-v1.3.0"), [])
        self.assertEqual(check_tag_name("hub-v1.4.0-rc1"), [])
        self.assertTrue(check_tag_name("v1.3.0"))
        self.assertTrue(check_tag_name("tools-1.3.0"))

    def test_complete_environment_passes(self):
        self.assertEqual(check_environment(ENV, POLICIES, "tools-v1.3.0"), [])

    def test_missing_environment_or_reviewer_is_refused(self):
        self.assertTrue(any("does not exist" in p for p in check_environment(None, None, "tools-v1.3.0")))
        bare = {**ENV, "protection_rules": []}
        self.assertTrue(any("no required reviewer" in p for p in check_environment(bare, POLICIES, "tools-v1.3.0")))

    def test_self_review_prevention_is_refused(self):
        env = {**ENV, "protection_rules": [{**REVIEWER, "prevent_self_review": True}]}
        self.assertTrue(any("self-review" in p for p in check_environment(env, POLICIES, "tools-v1.3.0")))

    def test_tag_policy_must_admit_the_tag(self):
        self.assertTrue(any("no deployment tag policy" in p for p in check_environment(ENV, POLICIES[:1], "tools-v1.3.0")))
        self.assertTrue(any("no deployment branch/tag policy" in p
                            for p in check_environment({**ENV, "deployment_branch_policy": None}, POLICIES, "tools-v1.3.0")))

    def test_workflow_must_gate_its_release_job(self):
        self.assertEqual(check_workflow(WORKFLOW, "tools"), [])
        self.assertTrue(check_workflow(WORKFLOW.replace("environment: production", ""), "tools"))
        self.assertTrue(check_workflow(None, "hub"))
        self.assertEqual(check_workflow(None, "install"), [])   # the launcher tag has no build workflow


if __name__ == "__main__":
    unittest.main()
