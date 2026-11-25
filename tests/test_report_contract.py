# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The contract master's v4.0.0 report API (Exchange Contract A10) on its own, without a city
checkout: the ci-report check run's machine form, its verdict fields, and the one currency
judgement (T3). tests/test_operator_explanation.py runs the city scripts against the same master."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("contract_master", REPO_ROOT / "tools" / "hub" / "operator_explanation.py")
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)

REPO = "city/sample"


def pr(number=7, sha="a" * 40, **extra):
    return {"number": number, "state": "open", "title": "t", "body": "b", "labels": [],
            "head": {"sha": sha, "ref": "edit/x", "repo": {"full_name": REPO}}, "base": {"sha": "b" * 40}, **extra}


def report(of=None, run=(50, 1), state="pass", **extra):
    of = of or pr()
    return contract.encode_report({
        "version": 2, "repo": REPO, "pr": of["number"], "context": contract.context(of),
        "runId": run[0], "runAttempt": run[1], "runUrl": "u", "toolsRef": "t", "state": state, "lang": "en",
        "checks": [{"key": "schema", "label": "CityGML format", "status": "pass", "severity": "blocking", "ran": True}],
        "lifecycle": [], "heading": "CI report", "labels": {k: k.title() for k in contract.FIELDS},
        "fields": {k: "<b>x</b>" for k in contract.FIELDS}, **extra})


def check_run(reason, rep, app="github-actions", cid=1):
    result = contract.check_result(reason, rep)
    return {"id": cid, "name": "ci-report", "app": {"slug": app}, "status": "completed",
            "conclusion": result["conclusion"], "output": {"summary": result["summary"], "text": result["text"]}}


class GitHub:
    """A city repository as the master reads it: one PR, its analysis runs, its check runs."""

    def __init__(self, live, runs=None, checks=(), others=()):
        self.live, self.others, self.checks = live, list(others), list(checks)
        self.runs = runs if runs is not None else [{"id": 50, "run_attempt": 1, "status": "completed",
                                                    "conclusion": "success"}]

    def __call__(self, path):
        if path == f"/repos/{REPO}/pulls/{self.live['number']}":
            return 200, self.live
        if path.startswith(f"/repos/{REPO}/pulls?state=open"):
            return 200, [self.live, *self.others]
        if "/actions/workflows/pr-analysis.yml/runs?" in path:
            head = self.live["head"]
            return 200, {"workflow_runs": [{"head_sha": head["sha"], "head_branch": head["ref"],
                                            "head_repository": {"full_name": REPO},
                                            "path": ".github/workflows/pr-analysis.yml", **r} for r in self.runs]}
        if "/check-runs?check_name=ci-report" in path:
            return 200, {"total_count": len(self.checks), "check_runs": self.checks}
        raise AssertionError(path)


class TestMachineForm(unittest.TestCase):
    def test_the_check_run_carries_the_report_whole(self):
        rep = report()
        reason, read = contract.read_check(check_run("current", rep))
        self.assertEqual((reason, read), ("current", rep))
        data = json.loads(check_run("current", rep)["output"]["text"])
        self.assertEqual((data["version"], data["reason"], data["report"]["reportId"]), (2, "current", rep["reportId"]))

    def test_anything_but_a_whole_trusted_v2_report_is_not_read(self):
        rep = report()
        self.assertEqual(contract.read_check(check_run("current", rep, app="someone-else")), (None, None))
        tampered = check_run("current", rep)
        tampered["output"]["text"] = tampered["output"]["text"].replace('"state":"pass"', '"state":"fix"')
        self.assertEqual(contract.read_check(tampered), (None, None))           # the reportId no longer matches
        v1 = check_run("current", {**rep, "version": 1})
        self.assertEqual(contract.read_check(v1), (None, None))
        self.assertEqual(contract.read_check({**check_run("current", rep), "name": "analyze"}), (None, None))
        self.assertEqual(contract.read_check(None), (None, None))
        self.assertEqual(contract.read_check(check_run("report-pending", None)), ("report-pending", None))

    def test_the_verdict_fields_have_one_owner(self):
        rep = report()
        ok = contract.check_result("current", rep)
        self.assertEqual((ok["conclusion"], ok["external_id"]), ("success", rep["reportId"]))
        self.assertEqual(contract.check_result("current", report(state="fix"))["conclusion"], "failure")
        self.assertEqual(contract.check_result("report-stale", rep)["conclusion"], "failure")
        none = contract.check_result("report-pending", None)
        self.assertEqual((none["conclusion"], none["external_id"]), ("failure", "none"))

    def test_a_report_that_does_not_fit_is_not_attached(self):
        big = report(fields={k: "x" * 20000 for k in contract.FIELDS})
        result = contract.check_result("current", big)
        self.assertEqual((result["reason"], result["conclusion"], json.loads(result["text"])["report"]),
                         ("unavailable", "failure", None))
        self.assertLessEqual(len(contract.check_result("current", report())["text"]), contract.TEXT_LIMIT)

    def test_the_summary_is_for_people_and_carries_no_payload(self):
        summary = contract.report_summary(report())
        self.assertIn("## CI report", summary)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", summary)                         # report text is escaped
        self.assertNotIn("citygml-report:", summary)
        self.assertNotIn("<!--", summary)


class TestCurrency(unittest.TestCase):
    def judge(self, gh, rep, given=None):
        return contract.currency(gh, REPO, given or gh.live, rep)[1]

    def test_current(self):
        self.assertEqual(self.judge(GitHub(pr()), report()), "current")

    def test_each_reason(self):
        self.assertEqual(self.judge(GitHub(pr(state="closed")), report()), "head-changed")
        self.assertEqual(self.judge(GitHub(pr(sha="c" * 40)), report(), pr()), "head-changed")
        self.assertEqual(self.judge(GitHub(pr(), others=[pr(number=8)]), report()), "duplicate-head")
        self.assertEqual(self.judge(GitHub(pr(title="edited")), report()), "report-stale")
        self.assertEqual(self.judge(GitHub(pr()), None), "report-pending")
        # A9 (T7): a label CI does not read changes nothing
        self.assertEqual(self.judge(GitHub(pr(labels=[{"name": "documentation"}])), report()), "current")

    def test_a_newer_run_of_the_head_keeps_the_report_waiting(self):
        # safety net verify/newer-run-in-progress and poster-edge/superseded-by-newer-run (Q1: today's rule)
        runs = [{"id": 50, "run_attempt": 1, "status": "completed", "conclusion": "success"},
                {"id": 51, "run_attempt": 1, "status": "in_progress", "conclusion": None}]
        self.assertEqual(self.judge(GitHub(pr(), runs=runs), report()), "report-pending")
        self.assertEqual(self.judge(GitHub(pr(), runs=runs), report(run=(51, 1))), "report-pending")   # not done
        runs[1].update(status="completed", conclusion="cancelled")
        self.assertEqual(self.judge(GitHub(pr(), runs=runs), report()), "current")   # a cancelled run has no result
        retried = [{"id": 50, "run_attempt": 2, "status": "completed", "conclusion": "success"}]
        self.assertEqual(self.judge(GitHub(pr(), runs=retried), report()), "report-pending")   # attempt 2 supersedes

    def test_listings_are_read_whole(self):
        runs = [{"id": i, "run_attempt": 1, "status": "completed", "conclusion": "cancelled"} for i in range(1, 151)]
        runs.append({"id": 50, "run_attempt": 1, "status": "completed", "conclusion": "success"})
        gh = GitHub(pr(), runs=runs)

        def paged(path):
            code, data = gh(path)
            if isinstance(data, dict) and "workflow_runs" in data:
                page = int(path.rsplit("page=", 1)[1])
                return code, {"workflow_runs": data["workflow_runs"][(page - 1) * 100:page * 100]}
            return code, data
        gh.live = pr()
        self.assertEqual(contract.latest_run(paged, REPO, pr())["id"], 50)   # T12: the run on page 2 is found


class TestEvaluate(unittest.TestCase):
    def test_the_heads_check_is_judged_again(self):
        rep = report()
        gh = GitHub(pr(), checks=[check_run("current", rep, cid=1)])
        result = contract.evaluate(gh, REPO, pr())
        self.assertEqual((result["valid"], result["reason"], result["reportId"]), (True, "current", rep["reportId"]))
        gh.live = pr(body="edited after the report")
        self.assertEqual(contract.evaluate(gh, REPO, pr())["reason"], "report-stale")

    def test_only_the_trusted_apps_latest_check_counts(self):
        rep, newer = report(), report(state="fix")
        gh = GitHub(pr(), checks=[check_run("current", rep, cid=1), check_run("current", newer, cid=2),
                                  check_run("current", rep, app="someone-else", cid=3)])
        result = contract.evaluate(gh, REPO, pr())
        self.assertEqual((result["valid"], result["report"]["state"]), (False, "fix"))

    def test_another_prs_report_or_a_failure_is_never_valid(self):
        other = report(of=pr(number=9))
        gh = GitHub(pr(), checks=[check_run("current", other)])
        self.assertEqual(contract.evaluate(gh, REPO, pr())["reason"], "report-pending")
        broken = GitHub(pr(), runs=[])
        broken.checks = [check_run("current", report())]
        self.assertEqual(contract.evaluate(broken, REPO, pr()),
                         {"valid": False, "reason": "unavailable", "report": None, "reportId": "none", "live": None})

    def test_pages_unwraps_object_listings(self):
        def api(path):
            page = int(path.rsplit("page=", 1)[1])
            return 200, {"total_count": 130, "check_runs": [{"id": i} for i in range(130)][(page - 1) * 100:page * 100]}
        self.assertEqual(len(contract.pages(api, "/x", "check_runs")), 130)
        with self.assertRaises(RuntimeError):
            contract.pages(lambda path: (200, {"check_runs": None}), "/x", "check_runs")


if __name__ == "__main__":
    unittest.main()
