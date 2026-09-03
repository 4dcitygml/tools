#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Lightweight tests for first-time setup of the integrated frontend (hub) (#59 / #86).

Verifies what can be checked without touching the network or real GitHub:
- Token substitution in the setup screen HTML (no unreplaced `%%` remains)
- Auth (device flow) state machine: start guard, pending approval, success, failure, timeout
  (HTTP is stubbed out; nothing goes over the network)
- Fork-creation guard and reuse of an existing fork
- Automatic clone-destination selection (never asks the user)
Real fork/clone against GitHub is never called, as it would modify a real account."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("hub_app", REPO_ROOT / "tools" / "hub" / "app.py")
hub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hub)

_attr_spec = importlib.util.spec_from_file_location(
    "attr_app", REPO_ROOT / "tools" / "attr_editor" / "app.py")
attr = importlib.util.module_from_spec(_attr_spec)
_attr_spec.loader.exec_module(attr)

_tex_spec = importlib.util.spec_from_file_location(
    "tex_app", REPO_ROOT / "tools" / "tex_editor" / "app.py")
tex = importlib.util.module_from_spec(_tex_spec)
_tex_spec.loader.exec_module(tex)


def setup_html():
    return (hub.SETUP_HTML
            .replace("%%UPSTREAM%%", "https://github.com/4dcitygml/sample-tokyo-station")
            .replace("%%DEST%%", "/tmp/dest"))


class _EnglishEnv(unittest.TestCase):
    """Pin the language to the default en so tr() output is deterministic despite env vars.

    Same approach as _EnvGuard in tests/test_i18n.py (save, remove, restore).
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


class TestSetupHtml(unittest.TestCase):
    def test_no_token_left(self):
        self.assertNotIn("%%", setup_html())

    def test_invite_flow_is_removed(self):
        # Public operation only (#9): no invitation wait, application flow, or mode branching
        h = setup_html()
        for token in ("MODE", "%%INVITE%%", "copyInvite", "Waiting for an invitation"):
            self.assertNotIn(token, h)
        self.assertIn("The source data cannot be reached", h)

    def test_four_screens_and_no_url_input(self):
        h = setup_html()
        for fn in ("screenConnect", "screenFork", "screenClone", "screenDone"):
            self.assertIn(fn, h)
        # Zero input fields as a rule; only the destination override is folded into "Advanced settings".
        self.assertEqual(h.count("<input"), 1)
        self.assertIn('id="dest"', h)
        self.assertNotIn("fork URL", h)

    def test_device_flow_explains_tab_round_trip_before_opening_github(self):
        h = setup_html()
        self.assertIn("Copy the number and open GitHub", h)
        self.assertIn("close the GitHub tab", h)
        self.assertIn("← Return here", h)
        self.assertIn("document.visibilityState === 'hidden'", h)
        # Never auto-jump to another tab right after connecting; open only via the explicit button after reading the explanation.
        self.assertNotIn("window.open(r.verifyUrl", h)
        self.assertIn("function openVerification", h)

    def test_oauth_security_email_is_explained(self):
        h = setup_html()
        self.assertIn("A third-party OAuth application has been added to your account", h)
        self.assertIn("It is unrelated to any Mac prompt", h)
        self.assertIn("no reply or action inside the email is needed", h)

    def test_reused_login_is_explained_in_the_screen(self):
        h = setup_html()
        self.assertIn("s.reusedAuth", h)
        self.assertIn("Your previous GitHub connection was carried over", h)
        self.assertIn("the 8-digit code screen was skipped", h)
        self.assertIn("This is not a malfunction or a mistake", h)

    def test_clone_steps_explain_waiting_and_safe_retry(self):
        h = setup_html()
        self.assertIn("Creating (up to 1 minute)", h)
        self.assertIn("The progress text may not change for several minutes", h)
        self.assertIn("Import again to an empty location", h)
        self.assertIn("The partial data is kept, not deleted", h)
        self.assertIn("st.dest !== c.dest", h)

    def test_completion_waits_for_explicit_start_and_sets_welcome(self):
        h = setup_html()
        self.assertIn('first press "Launch" on the Attribute Editor', h)
        self.assertIn("location.href='/?welcome=1'", h)
        self.assertNotIn("if (st.active) { location.href = '/';", h)
        self.assertIn("if (st.active) { clearTimeout(timer);", h)

    def test_status_poll_keeps_current_screen_on_temporary_error(self):
        h = setup_html()
        self.assertIn("if (s.ok === false) throw new Error", h)
        self.assertIn("Checking the status is temporarily delayed", h)
        self.assertIn("Checking the connection status", h)
        self.assertIn("It checks again automatically", h)


class TestPostSetupDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (REPO_ROOT / "tools" / "hub" / "index.html").read_text(encoding="utf-8")

    def test_first_dashboard_has_one_recommended_action(self):
        self.assertIn('id="welcome"', self.html)
        self.assertIn("Ready to go — what to do first", self.html)
        self.assertIn("Attribute Editor", self.html)
        self.assertIn("Start here", self.html)
        self.assertIn("opens in a new tab", self.html)
        self.assertIn("t.key === 'attr_editor'", self.html)
        self.assertIn("?welcome=1", self.html)

    def test_welcome_can_be_dismissed_and_query_removed(self):
        self.assertIn('id="welcomeDismiss"', self.html)
        self.assertIn("history.replaceState", self.html)
        self.assertIn("hubQuery.delete('welcome')", self.html)

    def test_admin_panel_can_be_hidden_by_demo_query(self):
        self.assertIn('id="reviewEntry"', self.html)
        self.assertIn("const adminPanelMode = (hubQuery.get('admin') || 'on').toLowerCase()", self.html)
        self.assertIn("!['off', '0', 'false'].includes(adminPanelMode)", self.html)
        self.assertIn("$('reviewEntry').hidden = !showAdminPanel", self.html)
        self.assertIn("queryString ? `?${queryString}`", self.html)

    def test_github_failure_does_not_block_local_tools(self):
        self.assertIn("The tools remain fully usable.", self.html)
        self.assertIn("const statusTask", self.html)
        self.assertIn("const toolsTask", self.html)
        self.assertIn("Promise.all", self.html)
        self.assertIn("t('hub.tools_reload', 'Reload')", self.html)

    def test_failed_ci_can_be_retried_from_own_pr_list(self):
        self.assertIn("Re-run automated checks", self.html)
        self.assertIn("/retry", self.html)

    def test_feedback_is_submitted_inside_hub_with_defaults_and_badge(self):
        self.assertIn('id="feedbackBack"', self.html)
        self.assertIn("Send it to the maintainer from this screen, without opening GitHub.", self.html)
        self.assertIn("you do not need to sign in to GitHub again", self.html)
        self.assertIn("const d = await api('/api/feedback')", self.html)
        self.assertIn("method: 'POST'", self.html)
        self.assertIn("Reference for the maintainer", self.html)
        self.assertIn("merged PRs", self.html)
        self.assertIn("navigator.userAgent", self.html)
        self.assertIn('id="feedbackSubmit" class="primary" disabled>Send</button>', self.html)
        self.assertIn("Missing required fields", self.html)
        self.assertIn("All required fields are filled in. Ready to send.", self.html)
        self.assertNotIn("value.trim().length >= 5", self.html)
        self.assertNotIn("Create an issue with this content", self.html)
        self.assertIn("Sent to the maintainer", self.html)
        self.assertNotIn('id="feedback" href=', self.html)


class TestAttributeEditorFirstUse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (
            REPO_ROOT / "tools" / "attr_editor" / "index.html"
        ).read_text(encoding="utf-8")

    def test_welcome_query_opens_beginner_guide(self):
        self.assertIn('id="firstGuide"', self.html)
        self.assertIn("query.get('welcome') !== '1'", self.html)
        self.assertIn("Getting started", self.html)
        self.assertIn("Start with the map", self.html)

    def test_guide_explains_two_stage_selection_and_safe_editing(self):
        self.assertIn("blue square", self.html)
        self.assertIn("light-blue building", self.html)
        self.assertIn("Wait until the buildings finish loading.", self.html)
        self.assertIn("does not send anything by itself", self.html)
        self.assertIn('press "Send changes"', self.html)

    def test_guide_can_be_closed_without_returning_on_refresh(self):
        self.assertIn("query.delete('welcome')", self.html)
        self.assertIn("history.replaceState", self.html)
        self.assertIn("guide.hidden = true", self.html)

    def test_two_stage_hint_remains_after_guide(self):
        hint_en = "1. Click a blue square on the map in the top left → 2. Click one of the light-blue buildings that appear"
        self.assertIn(hint_en, self.html)                    # static display (English base)
        self.assertIn("t('editor.cards_empty'", self.html)   # dynamic side re-renders with the same key

    def test_each_state_shows_one_next_action(self):
        self.assertIn("Next: click one light-blue building on the map", self.html)
        self.assertIn("Next: click a value on the right that you want to change", self.html)
        self.assertIn('Next: press "Send changes" at the bottom right', self.html)

    def test_sending_uses_beginner_language_and_has_manual_fallback(self):
        self.assertIn("Send changes to the maintainer", self.html)
        self.assertIn("The source data is not modified directly", self.html)
        self.assertIn("Awaiting review by the maintainer", self.html)
        self.assertIn("Open GitHub to finish sending", self.html)
        self.assertIn("Create pull request", self.html)

    def test_local_pretest_runs_before_submission(self):
        self.assertIn('id="btnPretest"', self.html)
        self.assertIn("Pre-submission check", self.html)
        self.assertIn("/api/pretest", self.html)
        self.assertIn("pretestPassed", self.html)
        self.assertIn('id="btnSubmit" class="primary" disabled', self.html)

    def test_success_state_offers_a_clear_return_to_editor(self):
        self.assertIn('id="btnDoneClose"', self.html)
        self.assertIn("Close and return to the attribute editor", self.html)
        self.assertIn("if (res.prUrl)", self.html)
        self.assertIn("$('btnDoneClose').style.display = ''", self.html)
        self.assertIn("$('btnDoneClose').focus()", self.html)
        self.assertIn("$('btnDoneClose').onclick = closePrModal", self.html)

    def test_changed_attributes_require_an_explicit_source_before_sending(self):
        self.assertIn("Next, choose the source you checked for this change (required)", self.html)
        self.assertIn("sourceSelections = new Map()", self.html)
        self.assertIn("missingSourceKeys()", self.html)
        self.assertIn("n === 0 || missing > 0", self.html)
        self.assertIn("sourceSelections: [...sourceSelections.values()]", self.html)
        self.assertIn("code === '898' || code === '999'", self.html)

    def test_pr_preview_is_server_rendered(self):
        # The preview is fetched from /api/pr-preview (same code path as the
        # posted PR body), so the client no longer duplicates the sentences.
        self.assertIn('id="prPreview"', self.html)
        self.assertIn("loadPrPreview", self.html)
        self.assertIn("/api/pr-preview", self.html)
        self.assertNotIn("automaticSummary", self.html)
        self.assertIn("editor.pr_lang_note", self.html)  # repo-language mismatch note
        self.assertIn("Notes / supporting document URL (optional)", self.html)


class TestAttributeEditorReleaseWorkflow(unittest.TestCase):
    def test_packages_upload_directly_to_release_without_actions_artifacts(self):
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "release-attr-editor.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("actions/upload-artifact", workflow)
        self.assertIn("release_tag:", workflow)
        self.assertIn('gh release upload "$RELEASE_TAG"', workflow)
        self.assertIn("only verify the build", workflow)


class TestEditor3DPreviews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.attr_index = (
            REPO_ROOT / "tools" / "attr_editor" / "index.html"
        ).read_text(encoding="utf-8")
        cls.viewer = (
            REPO_ROOT / "tools" / "attr_editor" / "viewer.html"
        ).read_text(encoding="utf-8")
        cls.tex_index = (
            REPO_ROOT / "tools" / "tex_editor" / "index.html"
        ).read_text(encoding="utf-8")

    def test_attribute_editor_labels_3d_preview_and_disables_missing_lod2(self):
        self.assertIn("3D preview | LOD1 / LOD2", self.attr_index)
        self.assertIn("b.disabled = !hasLod2", self.viewer)
        self.assertIn("aria-disabled", self.viewer)
        self.assertIn("This building has no LOD2 (detailed shape)", self.viewer)
        self.assertIn("if (lod === 'lod2' && !hasLod2) return", self.viewer)

    def test_texture_comparison_places_3d_above_flat_images(self):
        three_d = self.tex_index.index("3D comparison: before and after")
        flat = self.tex_index.index("Flat image comparison: before and after")
        face_grid = self.tex_index.index('id="faceGrid"')
        self.assertLess(three_d, flat)
        self.assertLess(flat, face_grid)
        self.assertIn('id="viewerBefore"', self.tex_index)
        self.assertIn('id="viewerAfter"', self.tex_index)

    def test_texture_comparison_syncs_camera_and_replays_changed_textures(self):
        self.assertIn("type: 'viewerCamera'", self.viewer)
        self.assertIn("m.type === 'setCamera'", self.viewer)
        self.assertIn("comparisonCameraDriver", self.tex_index)
        self.assertIn("target.postMessage({ type: 'setCamera'", self.tex_index)
        self.assertIn("function syncComparisonViewer(role)", self.tex_index)
        self.assertIn("function changedFacePreview(f)", self.tex_index)

    def test_texture_comparison_flies_to_static_building_bounds(self):
        self.assertIn("function buildingCartesianPoints()", self.viewer)
        self.assertIn("Cesium.BoundingSphere.fromPoints(points)", self.viewer)
        self.assertIn("viewer.camera.flyToBoundingSphere", self.viewer)
        self.assertIn("await flyToBuilding(1.0)", self.viewer)
        self.assertNotIn("viewer.flyTo(viewer.entities", self.viewer)

    def test_texture_comparison_renders_before_and_after_flat_canvases(self):
        self.assertIn("function renderFlatComparisons()", self.tex_index)
        self.assertIn("wallThumb(wall, 260, 130, 'before')", self.tex_index)
        self.assertIn("wallThumb(wall, 260, 130, 'after')", self.tex_index)
        self.assertIn("version === 'before' ? atlas.image", self.tex_index)

    def test_flat_comparison_has_independent_display_tone_sliders(self):
        self.assertIn("const flatToneAdjustments = new Map()", self.tex_index)
        self.assertIn("toneInput.type = 'range'", self.tex_index)
        self.assertIn("? t('tex.tone_display', 'Display tone') : t('tex.tone_submit', 'Submitted tone')", self.tex_index)
        self.assertIn("canvas.style.filter = `brightness(", self.tex_index)
        self.assertIn("the After tone is also applied to the submitted data and the 3D preview", self.tex_index)
        self.assertIn("function applySubmittedTone(pids, value)", self.tex_index)
        self.assertIn("workCanvas(face.img).getContext('2d').drawImage(adjusted", self.tex_index)
        self.assertIn("syncComparisonViewer('after')", self.tex_index)
        self.assertIn("function restoreFlatToneBases(pids)", self.tex_index)
        self.assertIn("function reapplyCommittedTones(pids)", self.tex_index)

    def test_camera_alignment_replays_vertex_mode_and_marks_selection_orange(self):
        self.assertIn("let vertexModeRequested = false", self.viewer)
        self.assertIn("if (vertexModeRequested) setVertexMode(true)", self.viewer)
        self.assertIn("? Cesium.Color.ORANGE", self.viewer)
        self.assertIn("marker.point.pixelSize = selected ? 18", self.viewer)
        self.assertIn("ev.source === $('viewerFrame').contentWindow", self.tex_index)

    def test_camera_alignment_numbers_matched_points_in_both_views(self):
        self.assertIn("m.type === 'setMatchedPoints'", self.viewer)
        self.assertIn("function circledPointNumber", self.viewer)
        self.assertIn("`${index + 1}`", self.viewer)
        self.assertIn("marker.label.text = matchedIndex", self.viewer)
        self.assertIn("function syncCameraMarkers()", self.tex_index)
        self.assertIn("pt: camPendingPoint && camPendingPoint.slice()", self.tex_index)
        self.assertIn("syncCameraMarkers();", self.tex_index)


class TestAttributeEditorSourceAndPrBody(_EnglishEnv):
    def setUp(self):
        super().setUp()
        self.changes = [{
            "key": "storeysAboveGround#0",
            "tag": "storeysAboveGround",
            "index": 0,
            "old": "2",
            "new": "3",
            "label": "地上階数",
        }]
        self.codes = {"201": "都市計画基礎調査", "801": "現地調査",
                      "898": "不明", "999": "未作成"}

    def test_server_rejects_value_change_without_source(self):
        with self.assertRaisesRegex(ValueError, "地上階数"):
            attr.validate_source_selections(self.changes, [], self.codes)

    def test_server_rejects_unknown_and_uncreated_as_new_evidence(self):
        for code in ("898", "999"):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "cannot be chosen"):
                attr.validate_source_selections(
                    self.changes,
                    [{"key": "storeysAboveGround#0", "code": code}],
                    self.codes,
                )

    def test_server_resolves_selected_code_to_authoritative_label(self):
        selected = attr.validate_source_selections(
            self.changes,
            [{"key": "storeysAboveGround#0", "code": "801"}],
            self.codes,
        )
        self.assertEqual(
            selected["storeysAboveGround#0"],
            {"code": "801", "label": "現地調査"},
        )

    def test_pr_body_is_plain_english_and_includes_source_column(self):
        body = attr.build_pr_body(
            "13101-bldg-1",
            "bldg-1",
            self.changes,
            {"storeysAboveGround#0": {"code": "801", "label": "現地調査"}},
            "写真: https://example.test/evidence",
        )
        self.assertIn(
            'Checked "現地調査" and corrected "Storeys Above Ground" from "2" to "3".', body)
        self.assertIn("| Item | Before | After | Confirmed source |", body)
        self.assertIn("現地調査 (801)", body)
        self.assertIn("写真: https://example.test/evidence", body)
        self.assertNotIn("storeysAboveGround", body)
        # Exchange format v2: the reason section carries a key anchor
        self.assertIn("## Summary of changes <!--sec:reason-->", body)

    def test_pr_body_follows_repo_language_not_ui_language(self):
        # Repo-facing text follows the repository language (lang param from
        # 4dcitygml.json), NOT the user's UI language: even with an English UI,
        # lang="ja" yields Japanese prose and Japanese attribute labels, while
        # the sec:reason anchor stays outside the translated heading so CI
        # reason extraction is language-independent.
        self.assertEqual(os.environ.get("CITYGML_LANG"), "en")  # UI stays en
        body = attr.build_pr_body(
            "13101-bldg-1",
            "bldg-1",
            self.changes,
            {"storeysAboveGround#0": {"code": "801", "label": "現地調査"}},
            lang="ja",
        )
        self.assertIn("## 変更の概要 <!--sec:reason-->", body)
        self.assertIn("「現地調査」を確認し、「地上階数」を「2」から「3」へ修正しました。", body)
        self.assertIn("| 項目 | 変更前 | 変更後 | 確認した出典 |", body)
        self.assertIn("現地調査（801）", body)
        self.assertIn("補足はありません。", body)

    def test_pr_body_groups_multiple_changes_using_same_source(self):
        changes = self.changes + [{
            "key": "usage#0", "tag": "usage", "index": 0,
            "old": "401", "new": "402", "label": "用途",
        }]
        sources = {
            "storeysAboveGround#0": {"code": "201", "label": "都市計画基礎調査"},
            "usage#0": {"code": "201", "label": "都市計画基礎調査"},
        }
        body = attr.build_pr_body("id", "gid", changes, sources)
        self.assertEqual(body.count('Checked "都市計画基礎調査"'), 1)
        self.assertIn("corrected the following:", body)

    def test_selected_source_is_synced_to_building_source_list(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:gml="http://www.opengis.net/gml"
 xmlns:uro="https://www.geospatial.jp/iur/uro/3.2">
 <core:cityObjectMember>
  <bldg:Building gml:id="bldg-1">
   <core:creationDate>2026-01-01</core:creationDate>
   <bldg:measuredHeight uom="m">10</bldg:measuredHeight>
   <uro:bldgDataQualityAttribute><uro:DataQualityAttribute>
    <uro:thematicSrcDesc codeSpace="../../codelists/DataQualityAttribute_thematicSrcDesc.xml">201</uro:thematicSrcDesc>
   </uro:DataQualityAttribute></uro:bldgDataQualityAttribute>
  </bldg:Building>
 </core:cityObjectMember>
</core:CityModel>'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "tile.gml"
            path.write_bytes(xml)
            repo = object.__new__(attr.Repo)
            repo.root = root
            repo._tile_cache = {}
            repo.tile_files = lambda: {"tile": path}
            result = repo.apply_edits(
                "tile",
                "bldg-1",
                [{"tag": "measuredHeight", "index": 0, "old": "10", "new": "12"}],
                [{"key": "measuredHeight#0", "code": "801"}],
            )
            updated = path.read_text(encoding="utf-8")
        self.assertIn(">12</bldg:measuredHeight>", updated)
        self.assertIn(">801</uro:thematicSrcDesc>", updated)
        self.assertEqual(result["r28"], ["801"])


class TestSavedOAuthPrCreation(_EnglishEnv):
    def setUp(self):
        super().setUp()
        self.repo = object.__new__(attr.Repo)
        self.repo._origin_nwo = lambda: "beginner/sample-tokyo-station"
        self._token, self._api = attr.load_hub_token, attr.github_api
        self._urlopen = attr.urllib.request.urlopen

    def tearDown(self):
        attr.load_hub_token, attr.github_api = self._token, self._api
        attr.urllib.request.urlopen = self._urlopen
        super().tearDown()

    def test_saved_hub_connection_creates_upstream_pr_without_gh(self):
        captured = {}
        attr.load_hub_token = lambda: "saved-token"

        def fake_api(path, token, method="GET", payload=None, timeout=30):
            captured.update(path=path, token=token, method=method, payload=payload)
            return 201, {"html_url": "https://github.com/4dcitygml/sample-tokyo-station/pull/123"}

        attr.github_api = fake_api
        url, note = self.repo._create_pr_api("edit/b-1", "title", "body")
        self.assertEqual(url, "https://github.com/4dcitygml/sample-tokyo-station/pull/123")
        self.assertIsNone(note)
        self.assertEqual(captured["path"], "/repos/4dcitygml/sample-tokyo-station/pulls")
        self.assertEqual(captured["token"], "saved-token")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["payload"]["head"], "beginner:edit/b-1")
        self.assertEqual(captured["payload"]["base"], "main")

    def test_missing_saved_connection_uses_fallback_without_api_call(self):
        attr.load_hub_token = lambda: ""
        attr.github_api = lambda *a, **k: self.fail("must not call API without token")
        self.assertEqual(self.repo._create_pr_api("b", "t", "x"), (None, None))

    def test_manual_fallback_compares_fork_branch_to_upstream_main(self):
        self.assertEqual(
            self.repo._compare_url("edit/b-1"),
            "https://github.com/4dcitygml/sample-tokyo-station/compare/"
            "main...beginner:edit/b-1?expand=1",
        )

    def test_temporary_api_connection_error_becomes_a_fallback_message(self):
        def offline(*args, **kwargs):
            raise attr.urllib.error.URLError("offline")

        attr.urllib.request.urlopen = offline
        code, data = attr.github_api("/repos/x/y/pulls", "token")
        self.assertEqual(code, 0)
        self.assertIn("Connection error", data["message"])

    def test_push_failure_is_retryable_in_both_editors(self):
        attr_src = (REPO_ROOT / "tools" / "attr_editor" / "app.py").read_text(encoding="utf-8")
        tex_src = (REPO_ROOT / "tools" / "tex_editor" / "app.py").read_text(encoding="utf-8")
        for source in (attr_src, tex_src):
            self.assertIn("Your edits remain on this screen", source)
            self.assertIn('self._git("branch", "-D", branch, check=False)', source)


class TestAttributeEditorPretest(_EnglishEnv):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        bldg = root / "city/udx/bldg"
        bldg.mkdir(parents=True)
        codelists = root / "city/codelists"
        codelists.mkdir(parents=True)
        (codelists / "DataQualityAttribute_thematicSrcDesc.xml").write_text(
            '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml">'
            '<gml:dictionaryEntry><gml:Definition>'
            '<gml:name>801</gml:name><gml:description>現地調査</gml:description>'
            '</gml:Definition></gml:dictionaryEntry></gml:Dictionary>',
            encoding="utf-8",
        )
        self.gml = bldg / "53394611_bldg_6697_op.gml"
        self.gml.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            'xmlns:gml="http://www.opengis.net/gml" '
            'xmlns:uro="https://www.geospatial.jp/iur/uro/3.2">'
            '<core:cityObjectMember><bldg:Building gml:id="gml-bldg-1">'
            '<bldg:storeysAboveGround>2</bldg:storeysAboveGround>'
            '<uro:buildingID>bldg-1</uro:buildingID>'
            '</bldg:Building></core:cityObjectMember></core:CityModel>',
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=Test", "-c",
             "user.email=test@example.com", "commit", "-q", "-m", "initial"],
            check=True,
        )
        self.repo = attr.Repo(root)
        self.payload = {
            "tile": "53394611", "gid": "gml-bldg-1",
            "reason": "Confirmed the number of storeys above ground from the field survey sheet.",
            "changes": [{
                "key": "storeysAboveGround#0",
                "tag": "storeysAboveGround", "index": 0, "old": "2", "new": "3",
                "label": "Number of storeys above ground",
            }],
            "sourceSelections": [{"key": "storeysAboveGround#0", "code": "801"}],
        }

    def tearDown(self):
        self.temp.cleanup()
        super().tearDown()

    def test_pretest_checks_proposed_bytes_without_writing_file(self):
        before = self.gml.read_bytes()
        result = self.repo.pretest(self.payload)
        self.assertTrue(result["passed"])
        self.assertEqual(result["buildingID"], "bldg-1")
        self.assertEqual(self.gml.read_bytes(), before)
        self.assertEqual(
            [item["label"] for item in result["checks"]],
            ["Notes (optional)", "Changes", "Source", "Target building",
             "CityGML format", "Changed file scope", "Source list sync"],
        )

    def test_pretest_allows_missing_optional_supplement(self):
        self.payload["reason"] = ""
        result = self.repo.pretest(self.payload)
        self.assertTrue(result["passed"])
        reason = next(item for item in result["checks"] if item["key"] == "reason")
        self.assertEqual(reason["status"], "na")

    def test_pretest_rejects_missing_source_before_submission(self):
        self.payload["sourceSelections"] = []
        result = self.repo.pretest(self.payload)
        self.assertFalse(result["passed"])
        source = next(item for item in result["checks"] if item["key"] == "source")
        self.assertEqual(source["status"], "fail")


class TestAttrServerMessagesJapanese(_EnglishEnv):
    """Wave 3b regression: with CITYGML_LANG=ja, server-generated messages keep returning the original Japanese."""

    def setUp(self):
        super().setUp()
        os.environ["CITYGML_LANG"] = "ja"
        self.changes = [{
            "key": "storeysAboveGround#0", "tag": "storeysAboveGround",
            "index": 0, "old": "2", "new": "3", "label": "地上階数",
        }]
        self.codes = {"801": "現地調査", "898": "不明", "999": "未作成"}

    def test_unknown_code_message_matches_legacy_japanese(self):
        with self.assertRaises(ValueError) as ctx:
            attr.validate_source_selections(
                self.changes,
                [{"key": "storeysAboveGround#0", "code": "898"}],
                self.codes,
            )
        self.assertEqual(
            str(ctx.exception),
            "「不明」または「未作成」は、変更した属性の出典には選べません",
        )

    def test_missing_source_message_matches_legacy_japanese(self):
        with self.assertRaises(ValueError) as ctx:
            attr.validate_source_selections(self.changes, [], self.codes)
        self.assertEqual(
            str(ctx.exception),
            "変更した属性の出典を選んでください: 地上階数",
        )

    def test_pretest_note_and_api_error_match_legacy_japanese(self):
        self.assertEqual(
            attr.tr("editor.pretest_server_note",
                    "Detailed schema checks and more run again in the automated"
                    " checks after you send"),
            "詳細なスキーマ検査などは送信後の自動検査でもう一度確認します",
        )
        self.assertEqual(
            attr.tr("editor.api_conn_error", "Connection error: {reason}",
                    reason="offline"),
            "接続エラー: offline",
        )


class TestTexServerMessagesJapanese(_EnglishEnv):
    """Wave 3b regression: with CITYGML_LANG=ja, tex_editor server-generated messages keep returning the original Japanese."""

    def setUp(self):
        super().setUp()
        os.environ["CITYGML_LANG"] = "ja"

    def test_server_messages_match_legacy_japanese(self):
        self.assertEqual(
            tex.tr("tex.err_push_failed",
                   "Could not send to GitHub. Your edits remain on this screen."
                   " Check your internet connection and try again.\n{stderr}",
                   stderr="x"),
            "GitHub へ送信できませんでした。編集内容はこの画面に残っています。"
            "インターネット接続を確認して、もう一度お試しください。\nx",
        )
        # list values in params are filled with the same repr as the original f-string
        self.assertEqual(
            tex.tr("tex.err_shared_atlas",
                   "{orig} cannot be replaced because it is shared by multiple"
                   " buildings (owners: {owners})",
                   orig="a.jpg", owners=["b1"]),
            "a.jpg は複数の建物で共有されているため置き換えできません（対象: ['b1']）",
        )


class TestEntranceOsMismatch(unittest.TestCase):
    """Entrance OS-mismatch detection (#94): the zip's target-OS marker and the guidance screen."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO_ROOT / "tools" / "hub" / "getting-started.html").read_text(encoding="utf-8")

    def test_has_bundle_os_placeholder_once(self):
        # the release WF replaces this marker with mac / win before packing into the zip
        self.assertEqual(self.html.count("'%%BUNDLE_OS%%'"), 1)

    def test_mismatch_screen_and_zip_names(self):
        self.assertIn("renderMismatch", self.html)
        # points to the correct zip by file name (for both OSes)
        self.assertIn("citygml-hub-vX.Y.Z-windows-full.zip", self.html)
        self.assertIn("citygml-hub-vX.Y.Z-macos.zip", self.html)
        # keeps an escape hatch for misdetection (user can still proceed to the instructions)
        self.assertIn("Instructions for Mac (", self.html)

    def test_release_wf_injects_both_os(self):
        wf = (REPO_ROOT / ".github" / "workflows" / "release-hub.yml").read_text(encoding="utf-8")
        self.assertIn('replace("%%BUNDLE_OS%%", "win")', wf)
        self.assertIn('sub=("%%BUNDLE_OS%%", "mac")', wf)

    def test_mac_steps_cover_tcc_dialog(self):
        # Folder-access confirmation (TCC) screen (#95). Beginner testing reported it
        # as a "popup not in the manual". Also notes the wording varies by extraction location.
        self.assertIn("wants to access", self.html)
        self.assertIn("Allow", self.html)
        self.assertIn("folder name may differ", self.html)

    def test_windows_smartscreen_is_two_separate_actions(self):
        self.assertIn('click <b>"More info"</b> on the blue screen', self.html)
        self.assertIn('Click the "Run" button that appears', self.html)
        self.assertIn("start-windows.bat", self.html)
        self.assertNotIn("start-windows.exe", self.html)
        self.assertIn("Publisher: Unknown publisher", self.html)


class TestWindowsBundle(unittest.TestCase):
    """MinGit layout of the all-in-one Windows distribution and the Git selection rules."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = (
            REPO_ROOT / ".github" / "workflows" / "release-hub.yml"
        ).read_text(encoding="utf-8")

    def test_release_bundles_editors_and_language_pack_in_both_zips(self):
        # A7: the city clone carries no tools/, so the hub zip must ship the editors and
        # the language pack under program/ where hub.py's fallback lookup finds them.
        loops = self.workflow.count('for sub in ("attr_editor", "tex_editor", "i18n"):')
        self.assertEqual(loops, 2, "both the Windows and the macOS assembly must bundle them")
        for entry in ('f"{LIB}/attr_editor/app.py"', 'f"{LIB}/tex_editor/app.py"',
                      'f"{LIB}/i18n/i18n_loader.py"', 'f"{LIB}/i18n/catalogs/hub/ja.json"'):
            self.assertEqual(self.workflow.count(entry), 2, entry)
        hub = (REPO_ROOT / "tools" / "hub" / "app.py").read_text(encoding="utf-8")
        self.assertIn('APP_DIR / "i18n" / "i18n_loader.py"', hub)
        self.assertIn("for cand in (APP_DIR / rel, APP_DIR.parent / rel)", hub)

    def test_release_uses_pinned_mingit_and_python_and_size_gate(self):
        # A5/A7: build-time downloads are pinned by version URL + SHA-256 and
        # verified before extraction; no moving releases/latest reference remains.
        self.assertIn("MINGIT_URL:", self.workflow)
        self.assertIn("MINGIT_SHA256:", self.workflow)
        self.assertIn("PYEMBED_URL:", self.workflow)
        self.assertIn("PYEMBED_SHA256:", self.workflow)
        self.assertIn("Fetch-Verified", self.workflow)
        self.assertNotIn("releases/latest", self.workflow)
        self.assertIn("Expand-Archive", self.workflow)
        self.assertIn("70 * 1024 * 1024", self.workflow)
        attr_wf = (
            REPO_ROOT / ".github" / "workflows" / "release-attr-editor.yml"
        ).read_text(encoding="utf-8")
        for marker in ("MINGIT_SHA256:", "PYEMBED_SHA256:", "Fetch-Verified"):
            self.assertIn(marker, attr_wf)
        self.assertNotIn("releases/latest", attr_wf)
        # 2026-08-28 decision: bundled-Python zip only, no PyInstaller build step.
        self.assertNotIn("pip install pyinstaller", self.workflow)
        self.assertNotIn("pip install pyinstaller", attr_wf)

    def test_release_packages_include_tag_version(self):
        self.assertIn('f"citygml-hub-{version}-windows-full.zip"', self.workflow)
        self.assertIn('f"citygml-hub-{version}-macos.zip"', self.workflow)
        self.assertIn("needs: [windows, macos-zip]", self.workflow)
        self.assertIn("GH_REPO: ${{ github.repository }}", self.workflow)

    def test_release_packages_include_admin_review_ui(self):
        self.assertIn('z.write("../review.html", f"{LIB}/review.html")', self.workflow)
        self.assertIn('add(z, f"{base}/review.html", f"{LIB}/review.html")', self.workflow)

    def test_release_packages_include_licenses_and_bundled_python(self):
        # A2/A7: every distribution zip carries LICENSE / NOTICE / third-party
        # notices, and the Windows zip bundles PythonPortable as the only runtime.
        self.assertIn("THIRD_PARTY_NOTICES.md", self.workflow)
        self.assertIn('f"{LIB}/PythonPortable/', self.workflow)
        self.assertIn("start-windows.bat", self.workflow)
        self.assertNotIn("start-windows.exe", self.workflow)

    def test_bundle_dirs_include_hidden_program_directory(self):
        self.assertIn(hub.EXE_DIR / hub.LIB_SUBDIR, hub.BUNDLE_DIRS)
        self.assertIn(attr.EXE_DIR / attr.LIB_SUBDIR, attr.BUNDLE_DIRS)

    def test_hub_and_attr_resolve_git_from_program_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bundled = root / "program" / "PortableGit" / "cmd" / "git.exe"
            bundled.parent.mkdir(parents=True)
            bundled.touch()

            old_which = hub.shutil.which
            old_hub_dirs, old_hub_resolved = hub.BUNDLE_DIRS, hub._git_resolved
            old_attr_dirs, old_attr_resolved = attr.BUNDLE_DIRS, attr._git_resolved
            try:
                hub.shutil.which = lambda _name: None
                hub.BUNDLE_DIRS = [root, root / "program"]
                attr.BUNDLE_DIRS = [root, root / "program"]
                hub._git_resolved = attr._git_resolved = None
                self.assertEqual(Path(hub.git_cmd()[0]), bundled)
                self.assertEqual(Path(attr.git_cmd()[0]), bundled)
            finally:
                hub.shutil.which = old_which
                hub.BUNDLE_DIRS, hub._git_resolved = old_hub_dirs, old_hub_resolved
                attr.BUNDLE_DIRS, attr._git_resolved = old_attr_dirs, old_attr_resolved

    def test_configured_system_git_is_preferred_to_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            bundled = Path(d) / "PortableGit" / "cmd" / "git.exe"
            bundled.parent.mkdir(parents=True)
            bundled.touch()
            system = str(Path(d) / "system" / "git.exe")

            old_which = hub.shutil.which
            old_hub = (hub.BUNDLE_DIRS, hub._git_resolved, hub._system_git_is_configured)
            old_attr = (attr.BUNDLE_DIRS, attr._git_resolved, attr._system_git_is_configured)
            try:
                hub.shutil.which = lambda _name: system
                hub.BUNDLE_DIRS = attr.BUNDLE_DIRS = [Path(d)]
                hub._git_resolved = attr._git_resolved = None
                hub._system_git_is_configured = attr._system_git_is_configured = lambda _exe: True
                self.assertEqual(hub.git_cmd(), (system, False))
                self.assertEqual(attr.git_cmd(), (system, False))
            finally:
                hub.shutil.which = old_which
                hub.BUNDLE_DIRS, hub._git_resolved, hub._system_git_is_configured = old_hub
                attr.BUNDLE_DIRS, attr._git_resolved, attr._system_git_is_configured = old_attr

    def test_unconfigured_system_git_falls_back_to_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            bundled = Path(d) / "PortableGit" / "cmd" / "git.exe"
            bundled.parent.mkdir(parents=True)
            bundled.touch()
            system = str(Path(d) / "system" / "git.exe")

            old_which = hub.shutil.which
            old_hub = (hub.BUNDLE_DIRS, hub._git_resolved, hub._system_git_is_configured)
            old_attr = (attr.BUNDLE_DIRS, attr._git_resolved, attr._system_git_is_configured)
            try:
                hub.shutil.which = lambda _name: system
                hub.BUNDLE_DIRS = attr.BUNDLE_DIRS = [Path(d)]
                hub._git_resolved = attr._git_resolved = None
                hub._system_git_is_configured = attr._system_git_is_configured = lambda _exe: False
                self.assertEqual(hub.git_cmd(), (str(bundled), True))
                self.assertEqual(attr.git_cmd(), (str(bundled), True))
            finally:
                hub.shutil.which = old_which
                hub.BUNDLE_DIRS, hub._git_resolved, hub._system_git_is_configured = old_hub
                attr.BUNDLE_DIRS, attr._git_resolved, attr._system_git_is_configured = old_attr

    def test_system_git_keeps_its_credential_helper(self):
        old_cmd, old_cred = hub.git_cmd, hub.GIT_CRED_PATH
        try:
            with tempfile.TemporaryDirectory() as d:
                cred = Path(d) / "credentials"
                hub.GIT_CRED_PATH = cred
                hub.git_cmd = lambda: ("C:/Program Files/Git/cmd/git.exe", False)
                self.assertEqual(
                    hub.git_base_args(net=True),
                    ["C:/Program Files/Git/cmd/git.exe"],
                )
                hub.write_git_credentials("secret-token")
                self.assertFalse(cred.exists())
        finally:
            hub.git_cmd, hub.GIT_CRED_PATH = old_cmd, old_cred

    def test_bundled_git_uses_hub_credential_file_without_global_config(self):
        old_hub = (hub.git_cmd, hub.GIT_CRED_PATH)
        old_attr = (attr.git_cmd, attr.GIT_CRED_PATH)
        try:
            with tempfile.TemporaryDirectory(prefix="git credentials ") as d:
                cred = Path(d) / "credentials"
                hub.GIT_CRED_PATH = attr.GIT_CRED_PATH = cred
                hub.git_cmd = attr.git_cmd = lambda: ("C:/bundle/git.exe", True)
                hub.write_git_credentials("secret-token")

                self.assertEqual(
                    cred.read_text(encoding="utf-8"),
                    "https://x-access-token:secret-token@github.com\n",
                )
                for args in (hub.git_base_args(net=True), attr.git_base_args(net=True)):
                    self.assertEqual(args[0], "C:/bundle/git.exe")
                    self.assertIn("credential.helper=", args)
                    self.assertTrue(any("credential.https://github.com.helper=store --file=" in a
                                        and "'" in a for a in args))
        finally:
            hub.git_cmd, hub.GIT_CRED_PATH = old_hub
            attr.git_cmd, attr.GIT_CRED_PATH = old_attr

    def test_hub_repo_commands_use_resolved_git(self):
        seen = []
        old_cmd, old_run = hub.git_cmd, hub.subprocess.run
        hub.git_cmd = lambda: ("/bundle/PortableGit/cmd/git.exe", True)
        hub.subprocess.run = lambda args, **kwargs: (
            seen.append(args) or SimpleNamespace(returncode=0, stdout="true\n"))
        try:
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(hub.Hub(Path(d))._git("status"), "true")
        finally:
            hub.git_cmd, hub.subprocess.run = old_cmd, old_run
        self.assertEqual(seen[0][0], "/bundle/PortableGit/cmd/git.exe")

class TestAuthFlow(_EnglishEnv):
    """Verifies the device-flow state machine with HTTP stubbed out."""

    def setUp(self):
        super().setUp()
        self.mgr = hub.AuthManager()
        self._post, self._user = hub._post_form, hub.github_user
        self._cid = hub.oauth_client_id
        hub.oauth_client_id = lambda: "test-client-id"
        hub.github_user = lambda token: {"login": "tester", "id": 1} if token else None
        # suppress side effects so real files (~/.citygml_auth.json etc.) are never touched
        self._save, self._cred, self._ident = hub.save_token, hub.write_git_credentials, hub.apply_git_identity
        hub.save_token = lambda t: None
        hub.write_git_credentials = lambda t: None
        hub.apply_git_identity = lambda u: None
        self._load = hub.load_token
        hub.load_token = lambda: ""

    def tearDown(self):
        (hub._post_form, hub.github_user, hub.oauth_client_id) = (self._post, self._user, self._cid)
        (hub.save_token, hub.write_git_credentials, hub.apply_git_identity) = (
            self._save, self._cred, self._ident)
        hub.load_token = self._load
        super().tearDown()

    def test_requires_client_id(self):
        hub.oauth_client_id = lambda: ""
        with self.assertRaises(RuntimeError):
            self.mgr.start()

    def test_start_returns_user_code(self):
        # dispatch responses by URL (the token endpoint keeps returning "authorization pending")
        def fake(url, f, timeout=15):
            if url == hub.DEVICE_CODE_URL:
                return {"device_code": "dc", "user_code": "ABCD-1234",
                        "verification_uri": "https://github.com/login/device",
                        "interval": 30, "expires_in": 900}
            return {"error": "authorization_pending"}
        hub._post_form = fake
        r = self.mgr.start()
        self.assertEqual(r["userCode"], "ABCD-1234")
        st = self.mgr.state()
        self.assertTrue(st["waiting"])
        self.assertTrue(st["clientId"])

    def _poll_with(self, responses):
        """Runs _poll directly to reach a final state (no threads, no waiting)."""
        seq = list(responses)
        hub._post_form = lambda url, f, timeout=15: seq.pop(0) if seq else {"error": "expired_token"}
        self.mgr._poll("cid", "dc", 0, 1)
        return self.mgr.state()

    def test_success_sets_login(self):
        st = self._poll_with([{"error": "authorization_pending"}, {"access_token": "tok"}])
        self.assertFalse(st["waiting"])
        self.assertIsNone(st["error"])
        self.assertEqual(st["login"], "tester")
        self.assertEqual(self.mgr.token(), "tok")
        self.assertFalse(st["reusedAuth"])

    def test_saved_login_is_marked_as_reused(self):
        hub.load_token = lambda: "saved-token"
        st = self.mgr.state()
        self.assertEqual(st["login"], "tester")
        self.assertTrue(st["reusedAuth"])

    def test_access_denied_is_explained(self):
        st = self._poll_with([{"error": "access_denied"}])
        self.assertFalse(st["waiting"])
        self.assertIn("canceled", st["error"])

    def test_expired_is_explained(self):
        st = self._poll_with([{"error": "expired_token"}])
        self.assertIn("time limit expired", st["error"])


class TestFork(unittest.TestCase):
    def test_create_fork_requires_login(self):
        orig = hub.github_user
        hub.github_user = lambda token: None
        try:
            nwo, err = hub.create_fork("")
            self.assertIsNone(nwo)
            self.assertIn("GitHub", err)
        finally:
            hub.github_user = orig

    def test_existing_fork_is_reused_without_creating(self):
        orig_user, orig_api = hub.github_user, hub.gh_api
        calls = []
        hub.github_user = lambda token: {"login": "tester", "id": 1}
        def fake_api(path, token, method="GET", payload=None, timeout=30):
            calls.append((method, path))
            return 200, {"fork": True, "full_name": "tester/sample-tokyo-station"}
        hub.gh_api = fake_api
        try:
            nwo, err = hub.create_fork("tok")
            self.assertEqual(nwo, "tester/sample-tokyo-station")
            self.assertIsNone(err)
            self.assertEqual([m for m, _ in calls], ["GET"])  # POST /forks is never called
        finally:
            hub.github_user, hub.gh_api = orig_user, orig_api

    def test_upstream_nwo(self):
        self.assertEqual(hub.upstream_nwo(), "4dcitygml/sample-tokyo-station")


class TestUpstreamAccess(unittest.TestCase):
    """Upstream access check (public operation: no invitation flow; removed in #9)."""

    def setUp(self):
        self._api = hub.gh_api
        hub.Handler._access_cache = (0.0, False)

    def tearDown(self):
        hub.gh_api = self._api
        hub.Handler._access_cache = (0.0, False)

    def test_upstream_ok_caches_success(self):
        seq = iter([(200, {})])
        hub.gh_api = lambda *a, **k: next(seq)
        self.assertTrue(hub.Handler.upstream_ok("tester"))
        # once reachable, the API is never called again
        hub.gh_api = lambda *a, **k: self.fail("must not call API after access is confirmed")
        self.assertTrue(hub.Handler.upstream_ok("tester"))

    def test_upstream_ok_is_none_when_not_logged_in(self):
        hub.gh_api = lambda *a, **k: self.fail("must not call API when not connected")
        self.assertIsNone(hub.Handler.upstream_ok(None))

    def test_no_access_is_cached_briefly(self):
        # 404 → False. Repeat calls within the TTL never hit the API
        seq = iter([(404, {})])
        hub.gh_api = lambda *a, **k: next(seq)
        self.assertFalse(hub.Handler.upstream_ok("tester"))
        hub.gh_api = lambda *a, **k: self.fail("must not call API within TTL")
        self.assertFalse(hub.Handler.upstream_ok("tester"))

    def test_setup_screen_explains_unreachable_upstream(self):
        # Public operation: no access shows "unpublished/unreachable" guidance, not an invitation wait (#9)
        h = setup_html()
        self.assertIn("The source data cannot be reached", h)
        self.assertIn("Check again", h)


class TestSetupConsole(_EnglishEnv):
    def test_waiting_is_described_as_running_not_stopped(self):
        lines = hub.setup_console_messages(False)
        text = "\n".join(lines)
        self.assertIn("normal — not stopped", text)
        self.assertIn("stays open to run the browser screen", text)
        self.assertIn("same launcher file", text)
        self.assertNotIn("クローンが見つかりません", text)

    def test_saved_auth_explains_skipped_code_screen(self):
        text = "\n".join(hub.setup_console_messages(True))
        self.assertIn("The previous GitHub connection is reused", text)
        self.assertIn("8-digit code screen may be skipped", text)

    def test_console_messages_keep_japanese_when_lang_ja(self):
        # Regression: with CITYGML_LANG=ja the original Japanese terminal guidance is returned
        os.environ["CITYGML_LANG"] = "ja"
        text = "\n".join(hub.setup_console_messages(True))
        self.assertIn("状態: 初回セットアップ中（正常です。停止していません）", text)
        self.assertIn("前回の GitHub 接続情報を再利用します。8桁コードの画面は省略されることがあります", text)
        self.assertIn("ブラウザの案内を進めてください", text)


class TestDest(unittest.TestCase):
    def test_default_dest_is_not_asked(self):
        d = Path(hub.Handler.default_dest())
        self.assertEqual(d.parent.name, "Documents")
        self.assertTrue(d.name.startswith("CityGML Data"))

    def test_partial_clone_is_preserved_and_next_empty_dest_is_selected(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "CityGML Data"
            base.mkdir()
            (base / "途中データ").write_text("keep", encoding="utf-8")
            self.assertEqual(hub.next_available_dest(base), Path(d) / "CityGML Data2")
            self.assertTrue((base / "途中データ").is_file())

    def test_file_at_default_path_is_also_avoided(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "CityGML Data"
            base.write_text("do not overwrite", encoding="utf-8")
            self.assertEqual(hub.next_available_dest(base), Path(d) / "CityGML Data2")


class TestGitConfig(unittest.TestCase):
    def test_git_identity_keys(self):
        self.assertEqual(set(hub.git_identity()), {"name", "email"})

    def test_system_git_requires_global_name_and_email(self):
        exe = hub.shutil.which("git")
        if not exe:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as d:
            gc = Path(d) / "gitconfig"
            old = os.environ.get("GIT_CONFIG_GLOBAL")
            os.environ["GIT_CONFIG_GLOBAL"] = str(gc)
            try:
                self.assertFalse(hub._system_git_is_configured(exe))
                hub.subprocess.run([exe, "config", "--global", "user.name", "Taro Test"], check=True)
                self.assertFalse(hub._system_git_is_configured(exe))
                hub.subprocess.run(
                    [exe, "config", "--global", "user.email", "taro@example.com"], check=True
                )
                self.assertTrue(hub._system_git_is_configured(exe))
                self.assertTrue(attr._system_git_is_configured(exe))
            finally:
                if old is None:
                    os.environ.pop("GIT_CONFIG_GLOBAL", None)
                else:
                    os.environ["GIT_CONFIG_GLOBAL"] = old

    def test_git_config_set_writes_to_global(self):
        # GIT_CONFIG_GLOBAL isolates the write target so the real ~/.gitconfig is left untouched
        with tempfile.TemporaryDirectory() as d:
            gc = Path(d) / "gitconfig"
            old = os.environ.get("GIT_CONFIG_GLOBAL")
            os.environ["GIT_CONFIG_GLOBAL"] = str(gc)
            try:
                err = hub.git_config_set("Taro Test", "taro@example.com")
            finally:
                if old is None:
                    os.environ.pop("GIT_CONFIG_GLOBAL", None)
                else:
                    os.environ["GIT_CONFIG_GLOBAL"] = old
            self.assertIsNone(err)
            txt = gc.read_text(encoding="utf-8")
            self.assertIn("Taro Test", txt)
            self.assertIn("taro@example.com", txt)

    def test_attr_config_write_failure_does_not_break_activation(self):
        old = attr.CONFIG_PATH
        try:
            with tempfile.TemporaryDirectory() as d:
                attr.CONFIG_PATH = Path(d)  # write_text on a directory raises OSError
                attr.save_config({"repo": "C:/data/sample-tokyo-station"})
        finally:
            attr.CONFIG_PATH = old

    def test_git_config_set_requires_both(self):
        self.assertIsNotNone(hub.git_config_set("", ""))
        self.assertIsNotNone(hub.git_config_set("name only", ""))

    def test_apply_git_identity_uses_noreply_when_email_hidden(self):
        # never prompts even for accounts with a private email (builds the noreply address)
        seen = {}
        orig_set, orig_id = hub.git_config_set, hub.git_identity
        hub.git_identity = lambda: {"name": "", "email": ""}
        hub.git_config_set = lambda n, e: seen.update(name=n, email=e)
        try:
            hub.apply_git_identity({"login": "tester", "id": 42, "name": None, "email": None})
        finally:
            hub.git_config_set, hub.git_identity = orig_set, orig_id
        self.assertEqual(seen["name"], "tester")
        self.assertEqual(seen["email"], "42+tester@users.noreply.github.com")


class _FakeAuth:
    def __init__(self, token="", login=None):
        self._token, self._login = token, login

    def token(self):
        return self._token

    def user(self):
        return {"login": self._login, "id": 1} if self._login else None


class TestContributions(_EnglishEnv):
    """Dashboard PR / Issue retrieval (GraphQL version without the gh CLI; follow-up to #61)."""

    def setUp(self):
        super().setUp()
        self._auth, self._api = hub.AUTH, hub.gh_api
        with tempfile.TemporaryDirectory() as d:
            self.hub_obj = hub.Hub(Path(d))
        self.hub_obj.nwo = lambda: "4dcitygml/sample-tokyo-station"

    def tearDown(self):
        hub.AUTH, hub.gh_api = self._auth, self._api
        super().tearDown()

    def test_disconnected_returns_reason_without_network(self):
        hub.AUTH = _FakeAuth(token="")
        hub.gh_api = lambda *a, **k: self.fail("must not call API when not connected")
        r = self.hub_obj._fetch_contributions()
        self.assertFalse(r["ok"])
        self.assertIsNone(r["login"])
        self.assertIn("GitHub is not connected", r["reason"])
        self.assertEqual(r["badge"], hub.badge_for(0))

    def test_maps_graphql_fields_like_gh(self):
        hub.AUTH = _FakeAuth(token="tok", login="tester")
        captured = {}

        def fake_api(path, token, method="GET", payload=None, timeout=30):
            captured.update(path=path, method=method, payload=payload)
            return 200, {"data": {
                "prs": {"nodes": [
                    {"number": 5, "title": "fix roof", "state": "MERGED",
                     "url": "u5", "isDraft": False, "reviewDecision": "APPROVED",
                     "updatedAt": "2026-08-01T00:00:00Z"},
                    {"number": 6, "title": "wip", "state": "OPEN",
                     "url": "u6", "isDraft": True, "reviewDecision": None,
                     "updatedAt": "2026-08-02T00:00:00Z"},
                ]},
                "issues": {"nodes": [
                    {"number": 7, "title": "ask", "state": "CLOSED", "url": "u7",
                     "comments": {"totalCount": 2}, "updatedAt": "2026-08-03T00:00:00Z"},
                    {"number": 8, "title": "quiet", "state": "OPEN", "url": "u8",
                     "comments": {"totalCount": 0}, "updatedAt": "2026-08-04T00:00:00Z"},
                ]},
            }}
        hub.gh_api = fake_api
        r = self.hub_obj._fetch_contributions()
        # completes in a single request, with repo / author in the search query
        self.assertEqual(captured["path"], "/graphql")
        self.assertIn("repo:4dcitygml/sample-tokyo-station", captured["payload"]["variables"]["qPr"])
        self.assertIn("author:tester", captured["payload"]["variables"]["qPr"])
        # same shape as the gh pr list / gh issue list era (keeps the frontend contract)
        self.assertTrue(r["ok"])
        self.assertEqual(r["login"], "tester")
        self.assertEqual(r["merged"], 1)
        self.assertEqual(r["prs"][0]["reacted"], True)   # has a review
        self.assertEqual(r["prs"][1]["draft"], True)
        self.assertEqual(r["issues"][0]["comments"], 2)
        self.assertEqual(r["issues"][0]["reacted"], True)
        self.assertEqual(r["issues"][1]["reacted"], False)
        self.assertEqual(r["badge"], hub.badge_for(1))

    def test_graphql_error_is_reported(self):
        hub.AUTH = _FakeAuth(token="tok", login="tester")
        hub.gh_api = lambda *a, **k: (200, {"errors": [{"message": "rate limited"}]})
        r = self.hub_obj._fetch_contributions()
        self.assertFalse(r["ok"])
        self.assertIn("rate limited", r["reason"])
        self.assertEqual(r["login"], "tester")

    def test_status_does_not_depend_on_gh(self):
        # presence of gh does not appear in status (Handler derives connection state from AUTH)
        st = self.hub_obj.status()
        self.assertNotIn("gh", st)
        self.assertIn("runtime", st)


class TestFeedbackIssues(_EnglishEnv):
    def setUp(self):
        super().setUp()
        self._auth, self._api = hub.AUTH, hub.gh_api
        with tempfile.TemporaryDirectory() as d:
            self.hub_obj = hub.Hub(Path(d))
        self.contributions = {
            "ok": True,
            "login": "tester",
            "merged": 3,
            "badge": hub.badge_for(3),
            "prs": [],
            "issues": [],
        }
        self.hub_obj.contributions = lambda force=False: self.contributions

    def tearDown(self):
        hub.AUTH, hub.gh_api = self._auth, self._api
        super().tearDown()

    def test_defaults_include_existing_badge_and_prefilled_context(self):
        hub.AUTH = _FakeAuth(token="tok", login="tester")
        result = self.hub_obj.feedback_defaults()
        self.assertTrue(result["connected"])
        self.assertEqual(result["login"], "tester")
        self.assertEqual(result["badge"]["name"], "Regular contributor")
        self.assertEqual(result["merged"], 3)
        self.assertIn("building data editing tools", result["goal"])
        self.assertEqual(result["categories"], list(hub.feedback_categories()))

    def test_submit_uses_hub_token_and_records_badge_in_issue(self):
        hub.AUTH = _FakeAuth(token="tok", login="tester")
        captured = {}

        def fake_api(path, token, method="GET", payload=None, timeout=30):
            captured.update(path=path, token=token, method=method, payload=payload)
            return 201, {
                "number": 321,
                "title": payload["title"],
                "html_url": "https://github.example/issues/321",
            }

        hub.gh_api = fake_api
        self.hub_obj._contrib_cache = (1.0, self.contributions)
        result = self.hub_obj.submit_feedback({
            "title": "Feature request",
            "category": "The controls are hard to understand",
            "goal": "Verification",
            "problem": "Not convenient",
            "expected": "I want to send it directly from the hub.",
            "context": "Integrated Hub / macOS / Safari",
            "building": "13101-bldg-3728",
            "additional": "It reproduces every time.",
        })
        self.assertEqual(captured["path"], "/repos/4dcitygml/sample-tokyo-station/issues")
        self.assertEqual(captured["token"], "tok")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["payload"]["title"], "[UX] Feature request")
        issue_body = captured["payload"]["body"]
        self.assertIn("@tester", issue_body)
        self.assertIn("🌿 Regular contributor", issue_body)
        self.assertIn("Merged PRs: 3", issue_body)
        self.assertIn("13101-bldg-3728", issue_body)
        self.assertIn("does not decide issue priority", issue_body)
        self.assertEqual(result["number"], 321)
        self.assertIsNone(self.hub_obj._contrib_cache)

    def test_submit_rejects_unknown_category_before_network(self):
        hub.AUTH = _FakeAuth(token="tok", login="tester")
        hub.gh_api = lambda *a, **k: self.fail("must not call API with invalid input")
        with self.assertRaisesRegex(ValueError, "Choose the type of problem or suggestion"):
            self.hub_obj.submit_feedback({
                "title": "This is an input test.",
                "category": "Undefined",
                "goal": "I was trying to verify the operation.",
                "problem": "I am verifying the form input.",
            })


class TestPreset(unittest.TestCase):
    def test_shipped_preset_has_client_id(self):
        # The preset.json bundled in the distribution zip (tools/hub/preset.json) must be readable.
        # Without it the device flow cannot start and first-time setup stalls.
        cid = hub.load_preset().get("oauthClientId")
        if not cid:
            # This skip disappears once the value is set after creating the 4dcitygml OAuth App (M4 of the launch steps).
            # Must be set before the first Release (this test is the distribution gate).
            self.skipTest("oauthClientId not set (set in preset.json after creating the OAuth App)")
        self.assertEqual(hub.oauth_client_id(), cid)

    def test_preset_has_no_client_secret(self):
        # The device flow needs no client_secret; keep it out (prevents leakage).
        self.assertNotIn("oauthClientSecret", hub.load_preset())

    def test_preset_has_no_mode(self):
        # Public operation only (private/invitation modes removed in #9); no mode key at all.
        self.assertNotIn("mode", hub.load_preset())


if __name__ == "__main__":
    unittest.main()
