#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Lightweight tests for the integrated frontend's admin-facing PR review screen."""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dialects
from tests.support import REPO_ROOT, EnglishEnv, load_app, runtime

hub = load_app("hub_review_app", "tools/hub/app.py")
import operator_explanation as contract  # noqa: E402  (the contract master, on the hub's path)
import pr_markers  # noqa: E402


_EnglishEnv = EnglishEnv   # the display language pinned to en (tests/support.py)


BOT = {"login": "github-actions[bot]", "type": "Bot"}   # CI's comments (A10: trusted by the bot identity)
APP = {"slug": "github-actions"}                       # the trusted workflows' check runs (A10)

class FakeAuth:
    login = "reviewer"

    def token(self):
        return "test-token"

    def user(self):
        return {"login": "reviewer"}


def example_pr(number=123):
    return {
        "number": number,
        "title": "Attribute correction (storeys): 2 → 3",
        "body": (
            "## Changes\n\n"
            "| Item | Before | After |\n|---|---|---|\n| /storeysAboveGround | 2 | 3 |\n\n"
            "## Reason and supporting evidence\n\n2026 field survey sheet\n"
        ),
        "user": {"login": "proposer"},
        "head": {"ref": "edit/13101-bldg-1", "sha": "abc123"},
        "base": {"ref": "main", "sha": "base123"},
        "state": "open",
        "draft": False,
        "updated_at": "2026-08-18T00:00:00Z",
        "html_url": f"https://github.example/pull/{number}",
    }


def ci_checks(pr, rows=None, state="pass", reason="current", analyze="completed", app=APP):
    """The head's check runs as the trusted side leaves them (A10): `analyze`, and `ci-report`
    carrying a report of `pr` with `rows` (key -> status) through the contract master."""
    report = contract.encode_report({
        "version": 2, "repo": "city/sample", "pr": pr["number"], "context": contract.context(pr),
        "runId": 1, "runAttempt": 1, "runUrl": "u", "toolsRef": "t", "state": state, "lang": "en",
        "checks": [{"key": k, "label": k, "status": v, "severity": "blocking", "ran": True}
                   for k, v in (rows or {}).items()],
        "lifecycle": [], "heading": "h", "labels": {k: k for k in contract.FIELDS},
        "fields": {k: "" for k in contract.FIELDS}})
    result = contract.check_result(reason, report)
    return [{"id": 1, "name": "analyze", "app": APP, "status": analyze,
             "conclusion": "success" if analyze == "completed" else None,
             "details_url": "https://github.example/check/1"},
            {"id": 2, "name": "ci-report", "app": app, "status": "completed", "conclusion": result["conclusion"],
             "output": {"summary": result["summary"], "text": result["text"]},
             "details_url": "https://github.example/check/2"}]


def verdict(pr=None, **kw):
    pr = pr or example_pr()
    return hub.review.ci_verdict(ci_checks(pr, **kw), pr)


def paged(items, path):
    """A GitHub listing served 100 items per page, oldest first (page 1 when the path names none)."""
    page = int((re.search(r"[?&]page=(\d+)", path) or (None, 1))[1])
    return 200, items[(page - 1) * 100:page * 100]


# GitHub's `permissions` of a repository for each role (V5: the hub reads them, not a list of names)
ROLE_FLAGS = {"admin": ("admin", "maintain", "push", "triage", "pull"), "maintain": ("maintain", "push", "triage", "pull"),
              "write": ("push", "triage", "pull"), "push": ("push", "triage", "pull"), "triage": ("triage", "pull"),
              "read": ("pull",), "none": ()}


def repository(level="push"):
    return {"full_name": "4dcitygml/sample-munich-station", "permissions": {f: True for f in ROLE_FLAGS[level]}}


def fake_github_api(path, token, method="GET", payload=None, timeout=30):
    if re.fullmatch(r"/repos/[^/]+/[^/]+", path):
        return 200, repository()
    if path.endswith("/pulls?state=open&sort=updated&direction=desc&per_page=100&page=1"):
        return 200, [example_pr()]
    if path.endswith("/pulls/123"):
        return 200, example_pr()
    if path.endswith("/pulls/123/commits?per_page=100&page=1"):
        return 200, [{"sha": "abc123", "commit": {
            "message": "Update attributes (Storeys Above Ground): 2 → 3\n\nBuilding: 13101-bldg-1\n"}}]
    if path.endswith("/pulls/123/files?per_page=100&page=1"):
        return 200, [{
            "filename": "city/udx/bldg/53394611_bldg_6697_op.gml",
            "status": "modified", "additions": 1, "deletions": 1,
            "patch": "@@ -1 +1 @@\n-2\n+3",
        }]
    if path.endswith("/issues/123/comments?per_page=100&page=1"):
        return 200, []
    if path.endswith("/pulls/123/reviews?per_page=100&page=1"):
        return 200, []
    if path.endswith("/commits/abc123/check-runs?per_page=100&page=1"):
        return 200, {"check_runs": ci_checks(example_pr())}
    if "/actions/runs?status=action_required" in path:
        return 200, {"workflow_runs": []}
    if "/compare/main..." in path:   # up to date with main
        return 200, {"status": "ahead", "ahead_by": 1, "behind_by": 0}
    if path.startswith("/search/issues?"):
        return 200, {"items": [example_pr()]}
    if path.endswith("/pulls/123/reviews") and method == "POST":
        return 200, {"state": payload.get("event"), "payload": payload}
    raise AssertionError(f"unexpected API call: {method} {path}")


class TestReviewParsers(_EnglishEnv):
    def test_editor_pr_kind(self):
        self.assertEqual(hub.review.review_kind(example_pr()), "attribute")
        tex = example_pr()
        tex["title"] = "テクスチャ更新(2面): 13101-bldg-1"
        # Exchange Contract A5: the branch prefix wins over the title …
        self.assertEqual(hub.review.review_kind(tex), "attribute")
        # … and the title decides only when the branch carries no prefix.
        tex["head"]["ref"] = "feature/x"
        self.assertEqual(hub.review.review_kind(tex), "texture")

    def test_markdown_change_table(self):
        tables = hub.review.markdown_tables(example_pr()["body"])
        self.assertEqual(tables[0]["rows"][0]["Before"], "2")
        self.assertEqual(tables[0]["rows"][0]["After"], "3")

    def test_change_table_words_come_from_every_catalog(self):
        # The editors write the before/after table in the repository language; the hub
        # reads the headers back from the same catalogs (all languages) and from the CI
        # change summary, so no language needs its own list here.
        words = hub.review.change_table_words()
        for before, after in (("Before", "After"), ("変更前", "変更後"), ("Vorher", "Nachher"),
                              ("Bild vorher", "Bild nachher (neu hinzugefügt)"),
                              ("変更前の画像", "変更後の画像（新規追加）"), ("Old", "New")):
            self.assertIn(before, words["before"]); self.assertIn(after, words["after"])
        self.assertTrue({"Item", "項目", "Feld", "path"} <= words["label"])

    def test_display_names_from_any_language_are_kept(self):
        # The editors write the attribute's display name in the repository language;
        # only a path or tag (the CI change summary) is looked up in the label table.
        labels = hub.review.attribute_labels()
        for name in ("Storeys Above Ground", "Geschosse über Grund", "地上階数"):
            self.assertEqual(hub.review.attribute_label(name, labels), name)
        self.assertEqual(hub.review.attribute_label("/storeysAboveGround", labels), "Storeys Above Ground")
        self.assertEqual(hub.review.attribute_label("/uro:notInTheTable[1]", labels), "Other attribute (notInTheTable)")

    def test_headings_come_from_the_catalogs_and_the_template(self):
        heads = hub.review.pr_reason.HEADINGS
        for h in ("Reason and supporting evidence", "Summary of changes", "編集理由・根拠資料", "変更の概要",
                  "Begründung und Belege", "Zusammenfassung der Änderungen"):
            self.assertIn(h, heads)
        body = "## Betroffenes Gebäude\n\n- x\n\n## Begründung und Belege\n\nBauakte 2026\n"
        self.assertEqual(hub.review.human_reason(body, "texture"), "Bauakte 2026")
        self.assertTrue({"Betroffenes Gebäude", "対象建物", "PR type", "Checklist"} <= hub.review.body_headings())

    def test_change_rows_read_a_german_table(self):
        with tempfile.TemporaryDirectory() as d:
            body = "## Details\n\n| Feld | Vorher | Nachher | Geprüfte Quelle |\n|---|---|---|---|\n| storeysAboveGround | 2 | 3 | Bauakte 2026 |\n"
            rows = hub.Hub(Path(d), hub.SESSION)._change_rows(body, [], "attribute")
        self.assertEqual([(r["before"], r["after"]) for r in rows], [("2", "3")])

    def test_reason_section(self):
        # V5: the hub reads the reason section as CI does (pr_reason), not with its own extractor
        self.assertEqual(hub.review.pr_reason.section(example_pr()["body"]), "2026 field survey sheet")
        self.assertFalse(any(hasattr(m, "section_by_key") for m in (hub, hub.review)))
        self.assertFalse(hub.review.pr_reason.filled("(please fill in)"))

    def test_attribute_labels_are_human_readable(self):
        labels = hub.review.attribute_labels()
        self.assertEqual(hub.review.attribute_label("/storeysAboveGround", labels), "Storeys Above Ground")
        self.assertEqual(hub.review.attribute_label("/uro:buildingFootprintArea", labels), "Building Footprint Area")

    def test_building_id_and_human_reason(self):
        # The reason is the `<!--sec:reason-->` section (or the whole body of a short
        # attribute / geometry proposal), with markers, fences and tables removed.
        body = (
            "Building 13101-bldg-3728: adjusting the number of storeys above ground to match the field survey.\n\n"
            "<!-- a hidden note -->\n| Item | Before | After |\n|---|---|---|\n| x | 1 | 2 |\n"
        )
        plateau = dialects.dialect("plateau").building_id_text
        self.assertEqual(hub.review.extract_building_id(plateau, body), "13101-bldg-3728")
        self.assertEqual(hub.review.extract_building_id(None, body), "")   # a plain city: no form in free text
        self.assertEqual(
            hub.review.human_reason(body, "attribute"),
            "Building 13101-bldg-3728: adjusting the number of storeys above ground to match the field survey.",
        )
        self.assertEqual(
            hub.review.human_reason("**Building shape** changed (`measuredHeight`).", "geometry"),
            "Building shape changed (Measured Height).",
        )

    def test_proposal_title_hides_technical_terms(self):
        title = "building shape changed (measuredHeight 13.8→16.8 + roof)"
        self.assertEqual(
            hub.review.human_proposal_title(title),
            "Building shape changed (Measured Height 13.8→16.8 + roof)",
        )

    def test_check_names_are_human_readable(self):
        self.assertEqual(hub.review.check_display_name("analyze"),
                         "Change scope and data format check")
        self.assertEqual(hub.review.check_display_name("CityGML validation"),
                         "CityGML format check")
        self.assertEqual(hub.review.check_display_name("preview"),
                         "3D view necessity and generation check")

    def test_check_names_keep_japanese_when_lang_ja(self):
        # Regression: with CITYGML_LANG=ja, the original Japanese text is returned via tr()
        os.environ["CITYGML_LANG"] = "ja"
        self.assertEqual(hub.review.check_display_name("analyze"), "変更範囲とデータ形式の検査")
        self.assertEqual(hub.review.check_display_name("preview"), "3D表示の要否・生成確認")
        self.assertEqual(hub.review.check_display_name("unknown-run"), "建物データの自動検査")

    def test_failed_checkpoint_explains_next_action(self):
        points = hub.review.review_checkpoints(
            reason_ok=False, unsafe_files=[], verdict=hub.review.ci_verdict([], example_pr()), model_available=True,
            changes_requested=True, adjustment_owner="reviewer",
        )
        reason = next(x for x in points if x["key"] == "reason")
        self.assertEqual(reason["status"], "fail")
        self.assertIn("Waiting for the proposer", reason["action"])

    def test_all_inspection_items_are_listed_and_not_applicable_is_gray(self):
        points = hub.review.review_checkpoints(
            reason_ok=True, unsafe_files=[], verdict=verdict(), model_available=True, kind="attribute",
        )
        # every row of CI, in CI's order (the bulk rows were missing: a failed reproduction could
        # look ready in the hub without the strict gate), plus the hub's freshness point: GitHub's
        # own fact since A6 v4.0.0 dropped the row (T14)
        from scripts import gate_result
        ci_points = [p for p in points if p["key"] != "freshness"]
        self.assertEqual([p["key"] for p in ci_points], [r.key for r in gate_result.ROWS])
        self.assertEqual([p["label"] for p in ci_points], [r.label for r in gate_result.ROWS])
        self.assertEqual(next(p for p in points if p["key"] == "freshness")["status"], "pass")   # not behind
        self.assertEqual(next(p for p in points if p["key"] == "texture")["status"], "na")
        self.assertEqual(next(p for p in points if p["key"] == "topology")["status"], "na")

    def test_a_row_of_the_report_marks_a_specific_failure(self):
        points = hub.review.review_checkpoints(
            reason_ok=True, unsafe_files=[], verdict=verdict(rows={"plausibility": "fail"}, state="fix"),
            model_available=True, kind="attribute",
        )
        target = next(p for p in points if p["key"] == "plausibility")
        self.assertEqual(target["status"], "fail")
        self.assertIn("Waiting for the proposer", target["action"])

    def test_warn_and_error_rows_are_their_own_states(self):
        # S17: warn is advisory (never read as a failure), error is a system error, not the data
        points = {p["key"]: p for p in hub.review.review_checkpoints(
            reason_ok=True, unsafe_files=[], verdict=verdict(rows={"plausibility": "warn", "schema": "error"},
                                                             state="system"),
            model_available=False, kind="attribute")}
        self.assertEqual(points["plausibility"]["status"], "warn")
        self.assertIn("does not block", points["plausibility"]["action"])
        self.assertEqual(points["schema"]["status"], "error")
        self.assertIn("not a problem in the data", points["schema"]["reason"])
        self.assertEqual(points["model"]["status"], "warn")      # advisory: no 3D view is a warning

    def test_the_verdict_is_read_once_from_ci_report(self):
        # T2 (A10): one reading, the trusted ci-report check run, while its report is the PR's
        pr = example_pr()
        rows = {"plausibility": "warn", "schema": "pass"}
        self.assertEqual(verdict(pr, rows=rows),
                         {"status": "pass", "state": "pass", "rows": rows, "reason": "current"})
        self.assertEqual(verdict(pr, rows=rows, state="fix")["status"], "fail")
        self.assertEqual(verdict(pr, rows=rows, state="system")["status"], "fail")
        checks = ci_checks(pr, rows=rows)
        self.assertEqual(hub.review.ci_verdict(checks, {**pr, "body": "edited after the report"})["status"], "pending")
        # A9 (T7): only the labels CI reads belong to the context; any other label changes nothing
        self.assertEqual(hub.review.ci_verdict(checks, {**pr, "labels": [{"name": "documentation"}]})["rows"], rows)
        self.assertEqual(hub.review.ci_verdict(checks, {**pr, "labels": [{"name": "texture-override"}]})["rows"], {})
        self.assertEqual(verdict(pr, rows=rows, analyze="in_progress")["status"], "pending")   # a run is going
        self.assertEqual(verdict(pr, rows=rows, app={"slug": "someone-else"})["status"], "pending")
        tampered = ci_checks(pr, rows=rows)
        tampered[1]["output"]["text"] = tampered[1]["output"]["text"].replace('"pass"', '"warn"')
        self.assertEqual(hub.review.ci_verdict(tampered, pr)["status"], "pending")                   # reportId fails
        self.assertEqual(verdict(pr, rows=rows, reason="workflow-change")["status"], "fail")   # a person acts
        self.assertEqual(verdict(pr, rows=rows, reason="report-pending")["status"], "pending")
        self.assertEqual(hub.review.ci_verdict(ci_checks(pr)[:1], pr)["status"], "pending")           # no ci-report yet
        # an unrelated failing check of the head no longer decides the hub's verdict
        other = {"id": 3, "name": "lint-something", "app": {"slug": "other-app"}, "status": "completed",
                 "conclusion": "failure"}
        self.assertEqual(hub.review.ci_verdict([*ci_checks(pr), other], pr)["status"], "pass")

    def test_ci_retry_is_only_offered_for_probable_system_failure(self):
        retry = hub.review.ci_retry_info(verdict(state="system"))
        self.assertTrue(retry["available"])
        self.assertEqual(retry["kind"], "system")
        retry = hub.review.ci_retry_info(verdict(state="fix"))      # T15: "items to confirm" is the report's state
        self.assertFalse(retry["available"])
        self.assertEqual(retry["kind"], "fix")


class TestReviewApiModel(_EnglishEnv):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.repo = hub.Hub(Path(self.temp.name), hub.SESSION)
        (Path(self.temp.name) / "4dcitygml.json").write_text(   # PLATEAU data: the city declares it
            '{"repo": "4dcitygml/sample-munich-station", "building_id": {"type": "uro:buildingID"}, "dialect": "plateau", "data_dirs": ["city"], "crs": "EPSG:6697"}', encoding="utf-8")
        source = Path(self.temp.name) / "city/udx/bldg/53394611_bldg_6697_op.gml"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            'xmlns:gml="http://www.opengis.net/gml" '
            'xmlns:uro="https://www.geospatial.jp/iur/uro/2.0">'
            '<core:cityObjectMember><bldg:Building gml:id="bldg-object-1">'
            '<bldg:measuredHeight>10</bldg:measuredHeight>'
            '<bldg:lod0RoofEdge><gml:MultiSurface><gml:surfaceMember><gml:Polygon>'
            '<gml:exterior><gml:LinearRing><gml:posList>'
            '35.0 139.0 0 35.0 139.1 0 35.1 139.1 0 35.0 139.0 0'
            '</gml:posList></gml:LinearRing></gml:exterior>'
            '</gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod0RoofEdge>'
            '<uro:buildingID>13101-bldg-1</uro:buildingID>'
            '</bldg:Building></core:cityObjectMember></core:CityModel>',
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()
        super().tearDown()

    def test_maintainer_card_only_for_an_account_that_can_approve(self):
        def permission(level):
            return lambda path, *a, **k: (200, repository(level)) if re.fullmatch(r"/repos/[^/]+/[^/]+", path) else (404, {})
        with patch.object(hub.SESSION, "account", FakeAuth()):
            for level, shown in [("admin", True), ("maintain", True), ("write", True), ("push", True),
                                 ("triage", False), ("read", False), ("none", False)]:
                with self.subTest(level=level), patch.object(runtime, "github_api", side_effect=permission(level)):
                    self.assertEqual(self.repo.review_entry(), {"ok": True, "canReview": shown})
            with patch.object(runtime, "github_api", side_effect=OSError("offline")):
                self.assertFalse(self.repo.review_entry()["canReview"])
        not_connected = type("NotConnected", (FakeAuth,), {"token": lambda self: ""})()
        with patch.object(hub.SESSION, "account", not_connected), patch.object(runtime, "github_api") as api:
            self.assertFalse(self.repo.review_entry()["canReview"])
        api.assert_not_called()

    def fork_api(self, level="admin", files=None, conclusion="action_required"):
        """GitHub as the pending-runs view sees it: two waiting runs, one of them for a closed PR."""
        calls = []
        files = files or {"8": ["city/udx/bldg/53394611_bldg_6697_op.gml"], "9": [".github/workflows/pr-analysis.yml"]}
        runs = [{"id": 101, "name": "PR Analysis", "head_sha": "a" * 40, "created_at": "2026-09-11T00:00:00Z", "conclusion": conclusion},
                {"id": 102, "name": "PR Analysis", "head_sha": "b" * 40, "created_at": "2026-09-12T00:00:00Z", "conclusion": conclusion},
                {"id": 103, "name": "PR Analysis", "head_sha": "c" * 40, "created_at": "2026-09-13T00:00:00Z", "conclusion": conclusion}]
        pulls = [{"number": 8, "title": "Storeys", "user": {"login": "resident"}, "head": {"sha": "a" * 40}, "html_url": "u8"},
                 {"number": 9, "title": "Workflow", "user": {"login": "other"}, "head": {"sha": "b" * 40}, "html_url": "u9"}]

        def api(path, token=None, method="GET", payload=None, **k):
            calls.append((method, path))
            if re.fullmatch(r"/repos/[^/]+/[^/]+", path):
                return 200, repository(level)
            if "/actions/runs?status=action_required" in path:
                return 200, {"workflow_runs": runs}
            if re.search(r"/actions/runs/\d+/approve$", path):
                return 201, {}
            m = re.search(r"/actions/runs/(\d+)$", path)
            if m:
                return 200, next(r for r in runs if r["id"] == int(m.group(1)))
            if "/pulls?state=open" in path:
                return 200, pulls
            m = re.search(r"/pulls/(\d+)/files", path)
            if m:
                return paged([{"filename": f} for f in files[m.group(1)]], path)
            return 404, {}
        return api, calls

    def test_pending_runs_lists_waiting_fork_runs_for_a_reviewer(self):
        api, _ = self.fork_api()
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api):
            got = self.repo.pending_runs()
        self.assertTrue(got["canReview"])
        self.assertEqual([(r["runId"], r["number"], r["author"], r["url"]) for r in got["runs"]],
                         [(101, 8, "resident", "u8"), (102, 9, "other", "u9")])   # run 103 has no open PR
        api, _ = self.fork_api(level="read")
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api):
            self.assertEqual(self.repo.pending_runs(), {"ok": True, "canReview": False, "runs": []})

    def test_queue_names_a_fork_pr_that_waits_for_approval(self):
        # D4: the analysis waits in action_required; the checks the PR has are not a failure
        def api(path, token=None, method="GET", payload=None, **k):
            if "/actions/runs?status=action_required" in path:
                return 200, {"workflow_runs": [{"id": 1, "head_sha": "a" * 40}]}
            if "/check-runs" in path:   # as on a real waiting fork PR (2026-09-30)
                return 200, {"check_runs": [{"name": "gatekeeper", "status": "completed", "conclusion": "skipped"},
                                            {"name": "comment", "status": "completed", "conclusion": "success"}]}
            return fake_github_api(path, token, method, payload)
        pr = example_pr()
        pr["head"] = {**pr.get("head", {}), "sha": "a" * 40}
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api):
            waiting = self.repo._review_queue_item("t", "city/sample", pr, self.repo._awaiting_approval("t", "city/sample"))
            pr["head"] = {**pr["head"], "sha": "b" * 40}
            running = self.repo._review_queue_item("t", "city/sample", pr, self.repo._awaiting_approval("t", "city/sample"))
        self.assertEqual((waiting["waitingSource"], waiting["queueStatus"]), ("approval", "proposer_waiting"))
        self.assertIn("first-time contributor", " ".join(waiting["adjustmentReasons"]))
        # the same checks without a waiting run: no ci-report yet, so CI is still working (T2)
        self.assertEqual(running["waitingSource"], "checking")

    def test_one_reader_pages_every_listing(self):
        runs = [{"id": i} for i in range(150)]

        def api(path, token=None, **k):
            if "/check-runs" in path:
                return 200, {"total_count": 150, "check_runs": paged(runs, path)[1]}
            if "/issues/1/comments" in path and "page=2" in path:
                return 502, {"message": "Server Error"}
            return paged([{"id": i} for i in range(120)], path)
        with patch.object(runtime, "github_api", side_effect=api):
            self.assertEqual(hub.review.github_list("t", "/repos/o/r/commits/x/check-runs", "check_runs"), runs)
            self.assertEqual(len(hub.review.github_list("t", "/repos/o/r/pulls?state=open")), 120)
            self.assertEqual(hub.review.github_list("t", "/repos/o/r/issues/1/comments"), [])   # fail-soft
            with self.assertRaisesRegex(RuntimeError, "Server Error"):
                hub.review.github_list("t", "/repos/o/r/issues/1/comments", strict=True)

    def test_cis_report_decides_and_pasted_markers_do_not(self):
        # T2 (A10): the queue and the detail take CI's verdict from ci-report alone. T15: a pasted
        # resubmission marker (no bot check before) no longer marks "items to confirm".
        pasted = [{"id": i, "user": {"login": "resident", "type": "User"},
                   "body": "<!-- citygml-auto-resubmission -->\n<!-- status:active -->\n❌"} for i in range(1, 102)]

        def api(rows, state):
            def answer(path, token=None, method="GET", payload=None, **k):
                if "/issues/123/comments?" in path:
                    return paged(pasted, path)
                if "/check-runs?" in path:
                    return 200, {"check_runs": ci_checks(example_pr(), rows=rows, state=state)}
                return fake_github_api(path, token, method, payload)
            return answer
        with patch.object(hub.SESSION, "account", FakeAuth()), \
                patch.object(runtime, "github_api", side_effect=api({"reason": "pass"}, "pass")):
            item = self.repo._review_queue_item("t", "city/sample", example_pr())
            detail = self.repo.review_detail(123)
        self.assertEqual((item["queueStatus"], item["autoResubmit"]), ("reviewer_waiting", False))
        self.assertTrue(detail["canApprove"])
        with patch.object(hub.SESSION, "account", FakeAuth()), \
                patch.object(runtime, "github_api", side_effect=api({"reason": "fail"}, "fix")):
            item = self.repo._review_queue_item("t", "city/sample", example_pr())
            detail = self.repo.review_detail(123)
        self.assertIn("Reason and evidence not filled in", item["adjustmentReasons"])   # CI's verdict wins (A6)
        self.assertEqual(detail["retry"]["kind"], "fix")
        self.assertFalse(detail["canApprove"])

    def test_only_cis_own_comments_are_read(self):
        # D-3a4: any commenter's text was read as CI's: a viewer link iframed as the 3D model (and
        # passing the model checkpoint), a pasted change summary steering the rows and the ID
        resident = {"login": "resident", "type": "User"}
        bot = {"login": "github-actions[bot]", "type": "Bot"}
        evil = [{"id": 1, "user": resident, "body": pr_markers.PREVIEW + "\n[3D](https://evil.example/citygml-viewer/#x)"},
                {"id": 2, "user": resident, "body": pr_markers.CHANGE_SUMMARY + "\n#### `evil-gml`\n| Item | Before | After |\n"
                                                    "|---|---|---|\n| Storeys | 9 | 99 |\n"}]
        ours = [{"id": 3, "user": bot, "body": pr_markers.PREVIEW + "\n[3D](https://ci.example/citygml-viewer/#ok)"}]

        def api(comments):
            def answer(path, token=None, method="GET", payload=None, **k):
                if "/issues/123/comments?" in path:
                    return paged(comments, path)
                return fake_github_api(path, token, method, payload)
            return answer
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api(evil)):
            detail = self.repo.review_detail(123)
        self.assertNotIn("evil", detail["previewUrl"] + detail["modelUrl"])
        self.assertFalse(any("99" in str(row) for row in detail["changes"]))
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api(evil + ours)):
            self.assertEqual(self.repo.review_detail(123)["previewUrl"], "https://ci.example/citygml-viewer/#ok")

    def test_the_reason_preview_judges_as_ci_does(self):
        # D-3a7: before CI's row, the preview judged the whole body of an attribute proposal, so
        # it said "filled" where CI (the reason section only) fails; it judges with pr_reason now
        pr = example_pr()
        pr["body"] = "Storeys corrected after the 2026 field survey of the building."   # no reason section
        rec = hub.review.PrRecords("t", "city/sample", pr)
        rec.check_runs, rec.reviews = [], []     # no CI report yet
        self.assertFalse(hub.Hub._pr_state(rec)["reasonOk"])
        pr["body"] = "## Reason and supporting evidence\n\n2026 field survey sheet\n"
        rec = hub.review.PrRecords("t", "city/sample", pr)
        rec.check_runs, rec.reviews = [], []
        self.assertTrue(hub.Hub._pr_state(rec)["reasonOk"])

    def test_each_listing_is_read_once(self):
        # V5: the queue item read the comments twice and the commits through its own chain
        calls = []

        def api(path, token=None, method="GET", payload=None, **k):
            calls.append(path.split("?")[0])
            return fake_github_api(path, token, method, payload)
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api):
            self.repo._review_queue_item("t", "city/sample", example_pr())
            item_calls = list(calls)
            calls.clear()
            self.repo.review_detail(123)
        for listing in ("/comments", "/reviews", "/commits", "/check-runs"):
            self.assertLessEqual(sum(c.endswith(listing) for c in item_calls), 1, (listing, item_calls))
            self.assertLessEqual(sum(c.endswith(listing) for c in calls), 1, (listing, calls))

    def test_no_existing_texture_in_every_language(self):
        # D-3a3: only the Japanese placeholder was skipped; an English texture-add PR showed a
        # broken before-image
        self.assertTrue({"(no existing texture)", "（既存テクスチャなし）", "(keine vorhandene Textur)"}
                        <= hub.review.no_texture_placeholders())

    def test_local_lookup_matches_the_whole_building_id(self):
        # 13101-bldg-83 is a prefix of 13101-bldg-8357, which comes first in the file
        def member(gid, stable, lat):
            return ('<core:cityObjectMember><bldg:Building gml:id="' + gid + '">'
                    '<bldg:lod0RoofEdge><gml:MultiSurface><gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>'
                    f'<gml:posList>{lat} 139.0 0 {lat} 139.1 0 {lat + 0.1} 139.1 0 {lat} 139.0 0</gml:posList>'
                    '</gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod0RoofEdge>'
                    f'<uro:buildingID>{stable}</uro:buildingID></bldg:Building></core:cityObjectMember>')
        rel = "city/udx/bldg/53394614_bldg_6697_op.gml"
        (Path(self.temp.name) / rel).write_text(
            '<core:CityModel xmlns:core="c" xmlns:bldg="b" xmlns:gml="g" xmlns:uro="u">'
            + member("bldg-long", "13101-bldg-8357", 35.0) + member("bldg-short", "13101-bldg-83", 36.0)
            + '</core:CityModel>', encoding="utf-8")
        ref = self.repo._building_ref_from_local_files([{"filename": rel}], [], "13101-bldg-83")
        self.assertEqual((ref["gid"], ref["buildingId"]), ("bldg-short", "13101-bldg-83"))
        self.assertEqual(self.repo._building_ref_from_local_files([{"filename": rel}], [], "13101-bldg-8"), {})
        self.assertEqual(hub.review._find_whole(b'<a>x-83</a><b id="x-8357"/>', "x-83"), 3)
        self.assertEqual(hub.review._find_whole(b'<b id="x-8357"/>', "x-83"), -1)

    def test_local_lookup_follows_the_citys_identity_rule(self):
        # S11: the lookup read PLATEAU's uro:buildingID in every city; a gml:id city (Munich) has
        # buildingID-like leaves that are not its stable ID
        (Path(self.temp.name) / "4dcitygml.json").write_text(
            json.dumps({"repo": "o/munich", "building_id": {"type": "gml:id"}, "data_dirs": ["city"]}), encoding="utf-8")
        rel = "city/udx/bldg/690_5334_1.gml"
        (Path(self.temp.name) / rel).write_text(
            '<core:CityModel xmlns:core="c" xmlns:bldg="b" xmlns:gml="g" xmlns:uro="u">'
            '<core:cityObjectMember><bldg:Building gml:id="DEBY_LOD2_1">'
            '<uro:buildingID>not-the-id</uro:buildingID></bldg:Building></core:cityObjectMember>'
            '</core:CityModel>', encoding="utf-8")
        ref = self.repo._building_ref_from_local_files([{"filename": rel}], [], "DEBY_LOD2_1")
        self.assertEqual(ref["buildingId"], "DEBY_LOD2_1")

    def test_a_city_outside_the_plateau_layout_is_reviewed_too(self):
        # Munich: data in lod2_citygml/ (data_dirs), UTM coordinates, file names that are not mesh
        # codes. Its files were all "unsafe", it got no building reference, and its tile was refused.
        root = Path(self.temp.name)
        (root / "4dcitygml.json").write_text(
            json.dumps({"repo": "o/munich", "building_id": {"type": "gml:id"}, "data_dirs": ["lod2_citygml"]}),
            encoding="utf-8")
        rel = "lod2_citygml/690_5334_1.gml"
        (root / "lod2_citygml").mkdir()
        (root / rel).write_text(
            '<CityModel xmlns="http://www.opengis.net/citygml/1.0" xmlns:bldg="b" xmlns:gml="g">'
            '<gml:boundedBy><gml:Envelope srsName="EPSG:25832"/></gml:boundedBy>'
            '<cityObjectMember><bldg:Building gml:id="DEBY_LOD2_1"><gml:posList>691000 5334000 518 691010 5334000 518 '
            '691010 5334010 518</gml:posList></bldg:Building></cityObjectMember></CityModel>', encoding="utf-8")
        self.assertTrue(self.repo._is_data_file(rel))
        self.assertTrue(self.repo._is_data_file("lod2_citygml/textures/a.jpg", gml=False))
        self.assertFalse(self.repo._is_data_file("docs/a.gml"))
        self.assertFalse(self.repo._is_data_file("lod2_citygml/../x.gml"))
        ref = self.repo._building_ref_from_local_files([{"filename": rel}], [], "DEBY_LOD2_1")
        self.assertEqual((ref["buildingId"], ref["tile"]), ("DEBY_LOD2_1", "690_5334_1"))
        self.assertAlmostEqual(ref["center"][0], 48.13, delta=0.05)   # latitude, not a UTM northing
        self.assertAlmostEqual(ref["center"][1], 11.58, delta=0.05)

    def test_owner_repo_has_one_parser(self):
        for url, want in (("https://github.com/o/r.git", "o/r"), ("git@github.com:O/R.git", "O/R"), ("o/r", "o/r"),
                          ("https://github.com/o/r/", None), ("https://example.com/o/r", None)):
            self.assertEqual(runtime.github_nwo(url), want, url)

    def test_queue_and_detail_are_human_readable(self):
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=fake_github_api
        ):
            queue = self.repo.review_queue()
            self.assertTrue(queue["canReview"])
            self.assertEqual(queue["items"][0]["kind"], "attribute")
            self.assertEqual(queue["items"][0]["title"], "Attribute correction (storeys): 2 → 3")
            self.assertEqual(queue["items"][0]["queueStatus"], "reviewer_waiting")
            self.assertEqual(queue["items"][0]["waitingSource"], "")
            detail = self.repo.review_detail(123)
        self.assertTrue(detail["canApprove"])
        self.assertEqual(detail["buildingId"], "13101-bldg-1")
        self.assertEqual(detail["changes"], [{"label": "Storeys Above Ground", "before": "2", "after": "3"}])
        self.assertEqual(detail["reason"], "2026 field survey sheet")
        self.assertEqual(detail["checks"][0]["name"], "Change scope and data format check")
        self.assertTrue(detail["history"][0]["current"])
        self.assertTrue(detail["allGreen"])
        self.assertIn("review-viewer.html", detail["modelUrl"])
        self.assertEqual(detail["center"], [35.05, 139.05])
        self.assertIn("google.com/maps", detail["googleMapsUrl"])

    def test_approval_posts_approve_review(self):
        calls = []

        def recorder(path, token, method="GET", payload=None, timeout=30):
            result = fake_github_api(path, token, method, payload, timeout)
            if method == "POST":
                calls.append(payload)
            return result

        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=recorder
        ):
            result = self.repo.submit_review(123)
        self.assertTrue(result["ok"])
        self.assertEqual(calls[-1]["event"], "APPROVE")
        self.assertEqual(calls[-1]["commit_id"], "abc123")

    def test_reviewer_feedback_posts_request_changes_review(self):
        calls = []

        def recorder(path, token, method="GET", payload=None, timeout=30):
            result = fake_github_api(path, token, method, payload, timeout)
            if method == "POST":
                calls.append(payload)
            return result

        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=recorder
        ):
            result = self.repo.submit_review_feedback(
                123, "The photo orientation looks incorrect. Please verify."
            )
        self.assertTrue(result["ok"])
        self.assertEqual(calls[-1]["event"], "REQUEST_CHANGES")
        self.assertEqual(calls[-1]["commit_id"], "abc123")
        self.assertIn("photo orientation", calls[-1]["body"])

    def test_current_reviewer_feedback_waits_for_proposer_with_source(self):
        def reviewer_api(path, token, method="GET", payload=None, timeout=30):
            if path.endswith("/pulls/123/reviews?per_page=100&page=1"):
                return 200, [{"state": "CHANGES_REQUESTED", "commit_id": "abc123"}]
            return fake_github_api(path, token, method, payload, timeout)

        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=reviewer_api
        ):
            queue = self.repo.review_queue()
            detail = self.repo.review_detail(123)
        self.assertEqual(queue["items"][0]["queueStatus"], "proposer_waiting")
        self.assertEqual(queue["items"][0]["waitingSource"], "reviewer")
        self.assertTrue(detail["reviewerFeedback"])
        self.assertIn("reviewer's comment", "; ".join(detail["blockers"]))

    def test_behind_is_a_note_not_a_hold(self):
        # P3 (maintainer, 2026-10-03): "behind" is GitHub's fact (T5) and no gate any more (T14);
        # the hub notes it with GitHub's "Update branch" and holds nothing for it
        def behind_api(path, token, method="GET", payload=None, timeout=30):
            if "/compare/" in path:
                return 200, {"status": "diverged", "ahead_by": 1, "behind_by": 2}
            return fake_github_api(path, token, method, payload, timeout)

        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=behind_api
        ):
            item = self.repo.review_queue()["items"][0]
            detail = self.repo.review_detail(123)
        self.assertEqual((item["queueStatus"], item["waitingSource"], item["behind"]), ("reviewer_waiting", "", True))
        self.assertEqual(item["adjustmentReasons"], [])
        freshness = next(c for c in detail["checkpoints"] if c["key"] == "freshness")
        older = hub.review.review_checkpoints(reason_ok=True, unsafe_files=[], model_available=True, behind=True,
                                       verdict=verdict(rows={"freshness": "pass"}))   # a pre-v1.6 report row
        self.assertEqual(next(c for c in older if c["key"] == "freshness")["status"], "warn")
        self.assertEqual(freshness["status"], "warn")
        self.assertIn("Update branch", freshness["action"])
        self.assertEqual((detail["canApprove"], detail["retry"]["kind"], detail["behind"]), (True, "none", True))

    def test_behind_is_read_from_the_compare_of_the_live_base(self):
        seen = []

        def api(path, token=None, **k):
            seen.append(path)
            return {"/repos/o/r/compare/main...a1?per_page=1": (200, {"behind_by": 2}),
                    "/repos/o/r/compare/main...a2?per_page=1": (200, {"behind_by": 0}),
                    "/repos/o/r/compare/release/v1...a3?per_page=1": (200, {"behind_by": 1})}.get(path, (404, {}))
        with patch.object(runtime, "github_api", side_effect=api):
            self.assertTrue(hub.review.behind_base("t", "o/r", "main", "a1"))
            self.assertFalse(hub.review.behind_base("t", "o/r", "main", "a2"))
            self.assertTrue(hub.review.behind_base("t", "o/r", "release/v1", "a3"))
            self.assertFalse(hub.review.behind_base("t", "o/r", "main", "gone"))   # fail-soft: unknown is not behind
            self.assertFalse(hub.review.behind_base("t", "o/r", "", "a1"))
        self.assertEqual(len(seen), 4)

    def test_feedback_demo_does_not_post(self):
        calls = []

        def recorder(path, token, method="GET", payload=None, timeout=30):
            if method == "POST":
                calls.append(payload)
            return fake_github_api(path, token, method, payload, timeout)

        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=recorder
        ):
            result = self.repo.submit_review_feedback(123, "Please verify the target photo.", demo=True)
        self.assertTrue(result["demo"])
        self.assertEqual(calls, [])

    def test_probable_system_failure_can_request_ci_retry(self):
        calls = []

        def retry_api(path, token, method="GET", payload=None, timeout=30):
            if path.endswith("/commits/abc123/check-runs?per_page=100&page=1"):
                return 200, {"check_runs": ci_checks(example_pr(), state="system")}
            if path.endswith("/issues/123/comments") and method == "POST":
                calls.append(payload)
                return 201, {"id": 1}
            if re.fullmatch(r"/repos/[^/]+/[^/]+", path):   # T4 (A8): CI's rule alone decides who may ask
                raise AssertionError("the hub asked for a permission of its own")
            return fake_github_api(path, token, method, payload, timeout)

        # the reviewer is neither the proposer nor asked for a permission: the hub posts, CI decides
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=retry_api
        ):
            result = self.repo.request_ci_retry(123)
        self.assertTrue(result["ok"])
        self.assertIn("citygml-ci-retry-request", calls[0]["body"])
        self.assertIn("CI answers on the proposal", result["message"])

    def test_data_failure_does_not_offer_pointless_retry(self):
        def retry_api(path, token, method="GET", payload=None, timeout=30):
            if path.endswith("/commits/abc123/check-runs?per_page=100&page=1"):
                return 200, {"check_runs": ci_checks(example_pr(), rows={"reason": "fail"}, state="fix")}
            return fake_github_api(path, token, method, payload, timeout)

        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=retry_api
        ):
            with self.assertRaisesRegex(RuntimeError, "re-runs the checks automatically"):
                self.repo.request_ci_retry(123)

    def test_texture_asset_is_restricted_and_proxied(self):
        path = "city/udx/bldg/mesh_appearance/photo.jpg"
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=fake_github_api
        ), patch.object(runtime, "github_raw", return_value=(200, b"jpeg-data", "image/jpeg")) as raw:
            data, mime = self.repo.review_asset(123, "base", path)
        self.assertEqual(data, b"jpeg-data")
        self.assertEqual(mime, "image/jpeg")
        self.assertIn("ref=base123", raw.call_args.args[0])
        with self.assertRaises(ValueError):
            self.repo.review_asset(123, "base", "tools/hub/app.py")

    def test_gml_id_is_resolved_to_stable_building_id(self):
        rel = Path("city/udx/bldg/example.gml")
        source = Path(self.temp.name) / rel
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            '<bldg:Building gml:id="bldg-object-1">'
            '<uro:buildingID>13101-bldg-3728</uro:buildingID>'
            '</bldg:Building>',
            encoding="utf-8",
        )
        comments = [{"user": BOT, "body": (
            "<!-- citygml-change-summary -->\n"
            "### 修正\n\n#### `bldg-object-1`\n"
        )}]
        result = self.repo._building_id_from_local_files(
            [{"filename": rel.as_posix()}], comments
        )
        self.assertEqual(result, "13101-bldg-3728")


class TestReviewViewerRoute(_EnglishEnv):
    """The review screen embeds the attribute editor's 3D view at /review-viewer.html. Served as raw
    bytes it had no t() and stopped before loading the building (the globe stayed empty)."""

    def get(self, city_json: "dict | None" = None) -> str:
        import json
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            if city_json is not None:
                (Path(tmp) / "4dcitygml.json").write_text(json.dumps(city_json), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with patch.object(hub.SESSION, "hub", hub.Hub(Path(tmp), hub.SESSION)):
                    url = f"http://127.0.0.1:{server.server_address[1]}/review-viewer.html?tile=1&bid=b"
                    with urllib.request.urlopen(url, timeout=10) as r:
                        return r.read().decode("utf-8")
            finally:
                server.shutdown()
                server.server_close()

    def test_viewer_is_served_with_the_attribute_editors_language_pack(self):
        html = self.get()
        self.assertIn('id="city-i18n"', html)
        self.assertLess(html.index("window.t = function"), html.index("t('viewer.loading_mesh'"))
        with patch.object(runtime, "ui_lang", lambda: "ja"):
            html = self.get()
        self.assertIn('"viewer.loading_mesh"', html)   # the viewer's keys come from the attribute editor catalog

    def test_viewer_receives_the_citys_map_settings(self):
        html = self.get({"map": {"tiles": "osm", "center": [48.14, 11.56], "zoom": 15}})
        self.assertIn('"center": [48.14, 11.56]', html)
        self.assertIn('"photo": null', html)   # OpenStreetMap: no aerial layer


class TestReviewHtml(unittest.TestCase):
    def test_pending_runs_panel_is_for_reviewers_and_links_to_github(self):
        # V3: the waiting runs are approved on GitHub's own page (it shows every file and warns on
        # workflow changes); the hub lists them and links there, and approves nothing itself
        html = (REPO_ROOT / "tools" / "hub" / "review.html").read_text(encoding="utf-8")
        self.assertIn('<section id="pendingRuns" hidden></section>', html)
        self.assertIn("if (demo || !queueData || !queueData.canReview) { box.hidden=true; return; }", html)
        self.assertIn("api('/api/reviews/pending-runs')", html)
        self.assertIn("review.pending_open", html)
        self.assertNotIn("/approve", html.split("async function loadPendingRuns")[1])
        self.assertFalse(hasattr(hub.Hub, "approve_run"))
        # before: only in the panel (for a reviewer); after starting: back in the list, which
        # refreshes itself while checks run
        self.assertIn("queueData.items.filter(it=>!(queueData.canReview && !demo && it.waitingSource==='approval'))", html)
        self.assertIn("items.some(it=>it.waitingSource==='checking')", html)

    def test_review_ui_has_evidence_gate_and_demo_notice(self):
        html = (REPO_ROOT / "tools" / "hub" / "review.html").read_text(encoding="utf-8")
        self.assertIn("What changes in this proposal", html)
        self.assertIn("I have checked the changes, the supporting documents, and the automated check results", html)
        self.assertIn("Approve this proposal", html)
        self.assertIn("This re-runs the automated checks only", html)
        self.assertIn("/retry", html)
        self.assertIn("Buildings", html)
        self.assertIn("Change history of this building", html)
        self.assertIn("iframe class=\"previewFrame\"", html)
        self.assertIn("Automated checks passed", html)
        self.assertIn("Waiting for the reviewer", html)
        self.assertIn("Waiting for the proposer", html)
        self.assertIn("Question from the reviewer", html)
        self.assertIn("review.behind_note", html)            # P3: a note, not a waiting source
        self.assertNotIn("review.source_latest", html)
        self.assertIn("The photo orientation looks wrong. Please check it.", html)
        self.assertIn("Other (write your own)", html)
        self.assertIn("/feedback", html)
        self.assertIn("button.checkpoint.na", html)
        self.assertIn("Compare the 3D model with the site", html)
        self.assertIn("Open the 3D view in Google Maps", html)
        self.assertIn("approving changes nothing in the real data or history", html)
        self.assertIn("initialParams.get('building')", html)
        self.assertIn("url.searchParams.set('building',buildingId)", html)
        self.assertIn('data-building="${esc(g.buildingId)}"', html)
        self.assertNotIn("Open GitHub", html)

        queue = html[html.index("async function loadQueue()") : html.index("async function selectPr(number)")]
        self.assertLess(queue.index("requestedButton"), queue.index("reviewerItems.length) selectPr"))

        detail = html[html.index("function renderDetail()") :]
        self.assertLess(detail.index("${renderCheckpoints(d)}"), detail.index("class=\"card changeCard\""))
        self.assertLess(detail.index("class=\"card changeCard\""), detail.index("${renderVisuals(d)}"))
        self.assertLess(detail.index("🛡️ Approval prerequisites"),
                        detail.index("🕘 Change history of this building"))

        checkpoints = html[html.index("function renderCheckpoints(d)") : html.index("function showCheckpoint(key)")]
        self.assertIn("${renderTechnical(d)}", checkpoints)


if __name__ == "__main__":
    unittest.main()
