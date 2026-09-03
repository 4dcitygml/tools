#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Lightweight tests for the integrated frontend's admin-facing PR review screen."""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("hub_review_app", REPO_ROOT / "tools" / "hub" / "app.py")
hub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hub)


class _EnglishEnv(unittest.TestCase):
    """Pin the language to the default en so tr() output is deterministic regardless of env vars.

    Same approach as _EnvGuard in tests/test_i18n.py (save -> remove -> restore).
    """

    _ENV_KEYS = ("CITYGML_LANG", "LC_ALL", "LC_MESSAGES", "LANG")

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["CITYGML_LANG"] = "en"

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeAuth:
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
            "| Attribute | Before | After |\n|---|---|---|\n| /storeysAboveGround | 2 | 3 |\n\n"
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
    if path.endswith("/pulls?state=open&sort=updated&direction=desc&per_page=50"):
        return 200, [example_pr()]
    if path.endswith("/pulls/123"):
        return 200, example_pr()
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
        self.assertEqual(hub.review_kind(tex), "texture")

    def test_markdown_change_table(self):
        tables = hub.markdown_tables(example_pr()["body"])
        self.assertEqual(tables[0]["rows"][0]["Before"], "2")
        self.assertEqual(tables[0]["rows"][0]["After"], "3")

    def test_reason_section(self):
        self.assertEqual(
            hub.section_text(example_pr()["body"], "Reason and supporting evidence"),
            "2026 field survey sheet",
        )
        self.assertFalse(hub.review_ready_reason("(please fill in)"))

    def test_attribute_labels_are_human_readable(self):
        labels = hub.load_attribute_labels(REPO_ROOT)
        self.assertEqual(hub.attribute_label("/storeysAboveGround", labels), "Storeys Above Ground")
        self.assertEqual(hub.attribute_label("/uro:buildingFootprintArea", labels), "Building Footprint Area")

    def test_building_id_and_human_reason(self):
        body = (
            "Building 13101-bldg-3728: adjusting the number of storeys above ground to match the field survey.\n\n"
            "Expected CI: analyze\nValidation: schema"
        )
        self.assertEqual(hub.extract_building_id(body), "13101-bldg-3728")
        self.assertEqual(
            hub.human_reason(body, "attribute"),
            "Building 13101-bldg-3728: adjusting the number of storeys above ground to match the field survey.",
        )
        self.assertEqual(
            hub.human_reason(
                "**positive validation case**. Building shape changed. Expected CI: preview", "geometry"
            ),
            "Building shape changed.",
        )

    def test_proposal_title_hides_technical_terms(self):
        title = "PR-B: building shape changed (measured height 13.8→16.8 + roof) — positive validation case"
        self.assertEqual(
            hub.human_proposal_title(title),
            "Building shape changed (measured height 13.8→16.8 + roof)",
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
        self.assertEqual(
            [p["label"] for p in points],
            [
                "Description and evidence", "One change = one building",
                "Consistency with the latest version", "Changed file scope",
                "CityGML format", "Minimal diff", "Texture consistency",
                "Geometric structure", "Attribute value plausibility",
                "Topological consistency", "3D view",
            ],
        )
        self.assertEqual(next(p for p in points if p["key"] == "texture")["status"], "na")
        self.assertEqual(next(p for p in points if p["key"] == "topology")["status"], "na")

    def test_inspection_summary_can_mark_a_specific_failure(self):
        # Exchange format v2: match by the <!--cp:key--> anchor (display name is free-form)
        comments = [{"body": (
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
        comments = [{"body": (
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

    def test_queue_and_detail_are_human_readable(self):
        with patch.object(hub, "AUTH", FakeAuth()), patch.object(
            hub, "gh_api", side_effect=fake_github_api
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

        with patch.object(hub, "AUTH", FakeAuth()), patch.object(
            hub, "gh_api", side_effect=recorder
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

        with patch.object(hub, "AUTH", FakeAuth()), patch.object(
            hub, "gh_api", side_effect=recorder
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

        with patch.object(hub, "AUTH", FakeAuth()), patch.object(
            hub, "gh_api", side_effect=reviewer_api
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

        with patch.object(hub, "AUTH", FakeAuth()), patch.object(
            hub, "gh_api", side_effect=freshness_api
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

        with patch.object(hub, "AUTH", FakeAuth()), patch.object(
            hub, "gh_api", side_effect=recorder
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

        with patch.object(hub, "AUTH", FakeAuth()), patch.object(
            hub, "gh_api", side_effect=retry_api
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

        with patch.object(hub, "AUTH", FakeAuth()), patch.object(
            hub, "gh_api", side_effect=retry_api
        ):
            with self.assertRaisesRegex(RuntimeError, "re-runs the checks automatically"):
                self.repo.request_ci_retry(123)

    def test_texture_asset_is_restricted_and_proxied(self):
        path = "city/udx/bldg/mesh_appearance/photo.jpg"
        with patch.object(hub, "AUTH", FakeAuth()), patch.object(
            hub, "gh_api", side_effect=fake_github_api
        ), patch.object(hub, "gh_raw", return_value=(200, b"jpeg-data", "image/jpeg")) as raw:
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
        comments = [{"body": (
            "<!-- citygml-change-summary -->\n"
            "### 修正\n\n#### `bldg-object-1`\n"
        )}]
        result = self.repo._building_id_from_local_files(
            [{"filename": rel.as_posix()}], comments
        )
        self.assertEqual(result, "13101-bldg-3728")


class TestReviewHtml(unittest.TestCase):
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
