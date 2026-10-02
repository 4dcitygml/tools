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

from tests.support import REPO_ROOT, EnglishEnv, load_app, runtime

hub = load_app("hub_review_app", "tools/hub/app.py")


_EnglishEnv = EnglishEnv   # the display language pinned to en (tests/support.py)


BOT = {"login": "github-actions[bot]", "type": "Bot"}   # CI's comments (A10: trusted by the bot identity)

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


def fake_github_api(path, token, method="GET", payload=None, timeout=30):
    if "/collaborators/" in path:
        return 200, {"permission": "push"}
    if path.endswith("/pulls?state=open&sort=updated&direction=desc&per_page=100&page=1"):
        return 200, [example_pr()]
    if path.endswith("/pulls/123"):
        return 200, example_pr()
    if path.endswith("/pulls/123/commits?per_page=100"):
        return 200, [{"sha": "abc123", "commit": {
            "message": "Update attributes (Storeys Above Ground): 2 → 3\n\nBuilding: 13101-bldg-1\n"}}]
    if path.endswith("/pulls/123/files?per_page=100"):
        return 200, [{
            "filename": "city/udx/bldg/53394611_bldg_6697_op.gml",
            "status": "modified", "additions": 1, "deletions": 1,
            "patch": "@@ -1 +1 @@\n-2\n+3",
        }]
    if path.endswith("/issues/123/comments?per_page=100"):
        return 200, []
    if path.endswith("/pulls/123/reviews?per_page=100"):
        return 200, []
    if path.endswith("/commits/abc123/check-runs?per_page=100"):
        return 200, {"check_runs": [{
            "name": "analyze", "status": "completed", "conclusion": "success",
            "details_url": "https://github.example/check/1",
        }]}
    if "/actions/runs?status=action_required" in path:
        return 200, {"workflow_runs": []}
    if path.startswith("/search/issues?"):
        return 200, {"items": [example_pr()]}
    if path.endswith("/pulls/123/reviews") and method == "POST":
        return 200, {"state": payload.get("event"), "payload": payload}
    raise AssertionError(f"unexpected API call: {method} {path}")


class TestReviewParsers(_EnglishEnv):
    def test_editor_pr_kind(self):
        self.assertEqual(hub.review_kind(example_pr()), "attribute")
        tex = example_pr()
        tex["title"] = "テクスチャ更新(2面): 13101-bldg-1"
        # Exchange Contract A5: the branch prefix wins over the title …
        self.assertEqual(hub.review_kind(tex), "attribute")
        # … and the title decides only when the branch carries no prefix.
        tex["head"]["ref"] = "feature/x"
        self.assertEqual(hub.review_kind(tex), "texture")

    def test_markdown_change_table(self):
        tables = hub.markdown_tables(example_pr()["body"])
        self.assertEqual(tables[0]["rows"][0]["Before"], "2")
        self.assertEqual(tables[0]["rows"][0]["After"], "3")

    def test_change_table_words_come_from_every_catalog(self):
        # The editors write the before/after table in the repository language; the hub
        # reads the headers back from the same catalogs (all languages) and from the CI
        # change summary, so no language needs its own list here.
        words = hub.change_table_words()
        for before, after in (("Before", "After"), ("変更前", "変更後"), ("Vorher", "Nachher"),
                              ("Bild vorher", "Bild nachher (neu hinzugefügt)"),
                              ("変更前の画像", "変更後の画像（新規追加）"), ("Old", "New")):
            self.assertIn(before, words["before"]); self.assertIn(after, words["after"])
        self.assertTrue({"Item", "項目", "Feld", "path"} <= words["label"])

    def test_display_names_from_any_language_are_kept(self):
        # The editors write the attribute's display name in the repository language;
        # only a path or tag (the CI change summary) is looked up in the label table.
        labels = hub.attribute_labels()
        for name in ("Storeys Above Ground", "Geschosse über Grund", "地上階数"):
            self.assertEqual(hub.attribute_label(name, labels), name)
        self.assertEqual(hub.attribute_label("/storeysAboveGround", labels), "Storeys Above Ground")
        self.assertEqual(hub.attribute_label("/uro:notInTheTable[1]", labels), "Other attribute (notInTheTable)")

    def test_headings_come_from_the_catalogs_and_the_template(self):
        heads = hub.pr_reason.HEADINGS
        for h in ("Reason and supporting evidence", "Summary of changes", "編集理由・根拠資料", "変更の概要",
                  "Begründung und Belege", "Zusammenfassung der Änderungen"):
            self.assertIn(h, heads)
        body = "## Betroffenes Gebäude\n\n- x\n\n## Begründung und Belege\n\nBauakte 2026\n"
        self.assertEqual(hub.human_reason(body, "texture"), "Bauakte 2026")
        self.assertTrue({"Betroffenes Gebäude", "対象建物", "PR type", "Checklist"} <= hub.body_headings())

    def test_change_rows_read_a_german_table(self):
        with tempfile.TemporaryDirectory() as d:
            body = "## Details\n\n| Feld | Vorher | Nachher | Geprüfte Quelle |\n|---|---|---|---|\n| storeysAboveGround | 2 | 3 | Bauakte 2026 |\n"
            rows = hub.Hub(Path(d))._change_rows(body, [], "attribute")
        self.assertEqual([(r["before"], r["after"]) for r in rows], [("2", "3")])

    def test_marker_status_reads_the_result_icons(self):
        comments = [{"body": "<!-- citygml-quality-lint -->\n| item | ✅ |"},
                    {"body": "<!-- plausibility-lint -->\n❌ 1 issue"}]
        self.assertEqual(hub._marker_status(comments, "<!-- citygml-quality-lint -->", "pending"), "pass")
        self.assertEqual(hub._marker_status(comments, "<!-- plausibility-lint -->", "pending"), "fail")
        self.assertEqual(hub._marker_status(comments, "<!-- other -->", "pending"), "pending")

    def test_reason_section(self):
        self.assertEqual(
            hub.section_text(example_pr()["body"], "Reason and supporting evidence"),
            "2026 field survey sheet",
        )
        self.assertFalse(hub.pr_reason.filled("(please fill in)"))

    def test_attribute_labels_are_human_readable(self):
        labels = hub.attribute_labels()
        self.assertEqual(hub.attribute_label("/storeysAboveGround", labels), "Storeys Above Ground")
        self.assertEqual(hub.attribute_label("/uro:buildingFootprintArea", labels), "Building Footprint Area")

    def test_building_id_and_human_reason(self):
        # The reason is the `<!--sec:reason-->` section (or the whole body of a short
        # attribute / geometry proposal), with markers, fences and tables removed.
        body = (
            "Building 13101-bldg-3728: adjusting the number of storeys above ground to match the field survey.\n\n"
            "<!-- a hidden note -->\n| Item | Before | After |\n|---|---|---|\n| x | 1 | 2 |\n"
        )
        self.assertEqual(hub.extract_building_id(body), "13101-bldg-3728")
        self.assertEqual(
            hub.human_reason(body, "attribute"),
            "Building 13101-bldg-3728: adjusting the number of storeys above ground to match the field survey.",
        )
        self.assertEqual(
            hub.human_reason("**Building shape** changed (`measuredHeight`).", "geometry"),
            "Building shape changed (Measured Height).",
        )

    def test_proposal_title_hides_technical_terms(self):
        title = "building shape changed (measuredHeight 13.8→16.8 + roof)"
        self.assertEqual(
            hub.human_proposal_title(title),
            "Building shape changed (Measured Height 13.8→16.8 + roof)",
        )

    def test_check_names_are_human_readable(self):
        self.assertEqual(hub.check_display_name("analyze"),
                         "Change scope and data format check")
        self.assertEqual(hub.check_display_name("CityGML validation"),
                         "CityGML format check")
        self.assertEqual(hub.check_display_name("preview"),
                         "3D view necessity and generation check")

    def test_check_names_keep_japanese_when_lang_ja(self):
        # Regression: with CITYGML_LANG=ja, the original Japanese text is returned via tr()
        os.environ["CITYGML_LANG"] = "ja"
        self.assertEqual(hub.check_display_name("analyze"), "変更範囲とデータ形式の検査")
        self.assertEqual(hub.check_display_name("preview"), "3D表示の要否・生成確認")
        self.assertEqual(hub.check_display_name("unknown-run"), "建物データの自動検査")

    def test_failed_checkpoint_explains_next_action(self):
        points = hub.review_checkpoints(
            reason_ok=False, unsafe_files=[], checks=[], model_available=True,
            changes_requested=True, adjustment_owner="reviewer",
        )
        reason = next(x for x in points if x["key"] == "reason")
        self.assertEqual(reason["status"], "fail")
        self.assertIn("Waiting for the proposer", reason["action"])

    def test_all_inspection_items_are_listed_and_not_applicable_is_gray(self):
        points = hub.review_checkpoints(
            reason_ok=True, unsafe_files=[],
            checks=[{"status": "completed", "conclusion": "success"}],
            model_available=True, kind="attribute",
        )
        # every row of CI, in CI's order (the bulk rows were missing: a failed reproduction could
        # look ready in the hub without the strict gate)
        from scripts import gate_result
        self.assertEqual([p["key"] for p in points], [r.key for r in gate_result.ROWS])
        self.assertEqual([p["label"] for p in points], [r.label for r in gate_result.ROWS])
        self.assertEqual(next(p for p in points if p["key"] == "texture")["status"], "na")
        self.assertEqual(next(p for p in points if p["key"] == "topology")["status"], "na")

    def test_inspection_summary_can_mark_a_specific_failure(self):
        # Exchange format v2: match by the <!--cp:key--> anchor (display name is free-form)
        comments = [{"user": BOT, "body": (
            "<!-- citygml-automatic-inspection -->\n"
            "| Check | Result |\n|---|---|\n"
            "| Attribute value plausibility <!--cp:plausibility--> | ❌ Needs attention |\n"
        )}]
        points = hub.review_checkpoints(
            reason_ok=True, unsafe_files=[],
            checks=[{"status": "completed", "conclusion": "success"}],
            model_available=True, kind="attribute", comments=comments,
        )
        target = next(p for p in points if p["key"] == "plausibility")
        self.assertEqual(target["status"], "fail")

    def test_inspection_summary_falls_back_to_english_names(self):
        # Comments without a key can be matched by the English display name (fallback)
        comments = [{"user": BOT, "body": (
            "<!-- citygml-automatic-inspection -->\n"
            "| Check | Result |\n|---|---|\n"
            "| Attribute value plausibility | ❌ Needs attention |\n"
        )}]
        points = hub.review_checkpoints(
            reason_ok=True, unsafe_files=[],
            checks=[{"status": "completed", "conclusion": "success"}],
            model_available=True, kind="attribute", comments=comments,
        )
        target = next(p for p in points if p["key"] == "plausibility")
        self.assertEqual(target["status"], "fail")
        self.assertIn("Waiting for the proposer", target["action"])

    def test_warn_and_error_signs_are_their_own_states(self):
        # S17: ⚠️ is advisory (never read as a failure), ⚙️ is a system error, not the data
        comments = [{"user": BOT, "body": (
            "<!-- citygml-automatic-inspection -->\n| Check | Result |\n|---|---|\n"
            "| Attribute value plausibility <!--cp:plausibility--> | ⚠️ Warning (does not block) |\n"
            "| CityGML format <!--cp:schema--> | ⚙️ System error |\n"
        )}]
        points = {p["key"]: p for p in hub.review_checkpoints(
            reason_ok=True, unsafe_files=[], checks=[{"status": "completed", "conclusion": "success"}],
            model_available=False, kind="attribute", comments=comments)}
        self.assertEqual(points["plausibility"]["status"], "warn")
        self.assertIn("does not block", points["plausibility"]["action"])
        self.assertEqual(points["schema"]["status"], "error")
        self.assertIn("not a problem in the data", points["schema"]["reason"])
        self.assertEqual(points["model"]["status"], "warn")      # advisory: no 3D view is a warning

    def test_the_machine_report_rows_come_first(self):
        import operator_explanation as contract
        pr = {"number": 7, "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}, "title": "t", "body": "b", "labels": []}
        report = contract.encode_report({
            "version": 1, "repo": "city/sample", "pr": 7, "context": contract.context(pr), "runId": 1, "runAttempt": 1,
            "runUrl": "u", "state": "pass", "heading": "h", "labels": {k: k for k in contract.FIELDS},
            "fields": {k: "" for k in contract.FIELDS},
            "checks": [{"key": "plausibility", "status": "warn", "severity": "advisory", "ran": True},
                       {"key": "schema", "status": "pass", "severity": "blocking", "ran": True}]})
        bot = {"login": "github-actions[bot]", "type": "Bot"}
        comments = [{"id": 5, "user": bot, "body": contract.report_comment(report)},
                    {"id": 4, "user": bot, "body": "<!-- citygml-automatic-inspection -->\n|x|y|\n"
                                      "| x <!--cp:schema--> | ❌ Needs attention |\n"}]
        self.assertEqual(hub.report_rows(comments, pr), {"plausibility": "warn", "schema": "pass"})
        points = {p["key"]: p["status"] for p in hub.review_checkpoints(
            reason_ok=True, unsafe_files=[], checks=[{"status": "completed", "conclusion": "success"}],
            model_available=True, comments=comments, rows=hub.report_rows(comments, pr))}
        self.assertEqual((points["plausibility"], points["schema"]), ("warn", "pass"))
        self.assertEqual(hub.report_rows(comments, {**pr, "body": "edited after the report"}), {})   # stale
        forged = [{**comments[0], "user": {"login": "someone", "type": "User"}}]
        self.assertEqual(hub.report_rows(forged, pr), {})                                         # not the bot

    def test_signs_count_only_from_ci_and_an_unknown_sign_is_not_a_pass(self):
        # A6/A10 review: anyone could paste the marker, and an unknown sign fell back to the hub's reading
        table = ("<!-- citygml-automatic-inspection -->\n| Check | Result |\n|---|---|\n"
                 "| CityGML format <!--cp:schema--> | {sign} |\n")
        ok = [{"status": "completed", "conclusion": "success"}]
        pasted = [{"user": {"login": "someone", "type": "User"}, "body": table.format(sign="✅ Pass")}]
        self.assertEqual(hub._inspection_summary_statuses(pasted), {})
        unknown = [{"user": BOT, "body": table.format(sign="🆕 Something new")}]
        points = {p["key"]: p["status"] for p in hub.review_checkpoints(
            reason_ok=True, unsafe_files=[], checks=ok, model_available=True, comments=unknown)}
        self.assertEqual(points["schema"], "pending")

    def test_ci_retry_is_only_offered_for_probable_system_failure(self):
        retry = hub.ci_retry_info("fail", [])
        self.assertTrue(retry["available"])
        self.assertEqual(retry["kind"], "system")
        data_comment = [{"body": (
            "<!-- citygml-auto-resubmission -->\n<!-- status:active -->\n"
            "Please verify the attribute values."
        )}]
        retry = hub.ci_retry_info("fail", data_comment)
        self.assertFalse(retry["available"])
        self.assertEqual(retry["kind"], "fix")
        stale_comment = [{"body": (
            "<!-- citygml-base-freshness -->\n<!-- status:active -->\n"
            "Please incorporate the latest version."
        )}]
        retry = hub.ci_retry_info("fail", stale_comment)
        self.assertFalse(retry["available"])
        self.assertEqual(retry["kind"], "update")


class TestReviewApiModel(_EnglishEnv):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.repo = hub.Hub(Path(self.temp.name))
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
            return lambda path, *a, **k: (200, {"permission": level}) if path.endswith("/permission") else (404, {})
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
            if path.endswith("/permission"):
                return 200, {"permission": level}
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
                return 200, [{"filename": f} for f in files[m.group(1)]]
            return 404, {}
        return api, calls

    def test_pending_runs_lists_waiting_fork_runs_for_a_reviewer(self):
        api, _ = self.fork_api()
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api):
            got = self.repo.pending_runs()
        self.assertTrue(got["canReview"])
        self.assertEqual([(r["runId"], r["number"], r["author"], r["touchesWorkflows"]) for r in got["runs"]],
                         [(101, 8, "resident", False), (102, 9, "other", True)])   # run 103 has no open PR
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
        self.assertEqual(running["waitingSource"], "ci")   # the same checks without a waiting run

    def test_approve_run_checks_again_and_approves_only_that_run(self):
        api, calls = self.fork_api()
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api):
            self.assertEqual(self.repo.approve_run(101)["number"], 8)
            with self.assertRaisesRegex(RuntimeError, r"\.github/"):
                self.repo.approve_run(102)                 # its PR changes a workflow
            with self.assertRaisesRegex(RuntimeError, "no longer waiting"):
                self.repo.approve_run(103)                 # its PR was closed
        posts = [p for m, p in calls if m == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].endswith("/actions/runs/101/approve"), posts)
        api, calls = self.fork_api(conclusion="success")
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api):
            with self.assertRaisesRegex(RuntimeError, "no longer waiting"):
                self.repo.approve_run(101)                 # already approved and run
        api, calls = self.fork_api(level="read")
        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=api):
            with self.assertRaisesRegex(RuntimeError, "can approve"):
                self.repo.approve_run(101)
        self.assertFalse([p for m, p in calls if m == "POST"])

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
        self.assertEqual(hub._find_whole(b'<a>x-83</a><b id="x-8357"/>', "x-83"), 3)
        self.assertEqual(hub._find_whole(b'<b id="x-8357"/>', "x-83"), -1)

    def test_local_lookup_follows_the_citys_identity_rule(self):
        # S11: the lookup read PLATEAU's uro:buildingID in every city; a gml:id city (Munich) has
        # buildingID-like leaves that are not its stable ID
        (Path(self.temp.name) / "4dcitygml.json").write_text(
            json.dumps({"repo": "o/munich", "building_id": {"type": "gml:id"}}), encoding="utf-8")
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
            if path.endswith("/pulls/123/reviews?per_page=100"):
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

    def test_latest_base_request_waits_for_proposer_with_source(self):
        def freshness_api(path, token, method="GET", payload=None, timeout=30):
            if path.endswith("/issues/123/comments?per_page=100"):
                return 200, [{"body": (
                    "<!-- citygml-base-freshness -->\n<!-- status:active -->\n"
                    "Please incorporate the latest version."
                )}]
            return fake_github_api(path, token, method, payload, timeout)

        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=freshness_api
        ):
            queue = self.repo.review_queue()
        self.assertEqual(queue["items"][0]["queueStatus"], "proposer_waiting")
        self.assertEqual(queue["items"][0]["waitingSource"], "latest")
        self.assertIn("Waiting for the latest version to be merged in",
                      queue["items"][0]["adjustmentReasons"])

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
            if path.endswith("/commits/abc123/check-runs?per_page=100"):
                return 200, {"check_runs": [{
                    "name": "analyze", "status": "completed", "conclusion": "failure",
                }]}
            if path.endswith("/issues/123/comments") and method == "POST":
                calls.append(payload)
                return 201, {"id": 1}
            return fake_github_api(path, token, method, payload, timeout)

        with patch.object(hub.SESSION, "account", FakeAuth()), patch.object(runtime, "github_api", side_effect=retry_api
        ):
            result = self.repo.request_ci_retry(123)
        self.assertTrue(result["ok"])
        self.assertIn("citygml-ci-retry-request", calls[0]["body"])

    def test_data_failure_does_not_offer_pointless_retry(self):
        def retry_api(path, token, method="GET", payload=None, timeout=30):
            if path.endswith("/commits/abc123/check-runs?per_page=100"):
                return 200, {"check_runs": [{
                    "name": "analyze", "status": "completed", "conclusion": "failure",
                }]}
            if path.endswith("/issues/123/comments?per_page=100"):
                return 200, [{"body": (
                    "<!-- citygml-auto-resubmission -->\n<!-- status:active -->\n"
                    "Please verify the reason for change."
                )}]
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
                with patch.object(hub.SESSION, "hub", hub.Hub(Path(tmp))):
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
        self.assertIn("window.CITY_MAP", html)
        self.assertIn('"tiles": "osm"', html)


class TestReviewHtml(unittest.TestCase):
    def test_pending_runs_panel_is_for_reviewers_and_refuses_workflow_changes(self):
        html = (REPO_ROOT / "tools" / "hub" / "review.html").read_text(encoding="utf-8")
        self.assertIn('<section id="pendingRuns" hidden></section>', html)
        self.assertIn("if (demo || !queueData || !queueData.canReview) { box.hidden=true; return; }", html)
        self.assertIn("api('/api/reviews/pending-runs')", html)
        self.assertIn("/api/reviews/pending-runs/${b.dataset.run}/approve", html)
        self.assertIn("r.touchesWorkflows", html)   # no button for a PR that changes .github/
        # before: only in the panel (for a reviewer); after starting: back in the list, which
        # refreshes itself while checks run
        self.assertIn("queueData.items.filter(it=>!(queueData.canReview && !demo && it.waitingSource==='approval'))", html)
        self.assertIn("await loadQueue();\n    loadPendingRuns();", html)
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
        self.assertIn("Merge in the latest version", html)
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
