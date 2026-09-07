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
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import shutil

from tests.support import TempHome, accounts, fake_git, runtime

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


def setup_html(mode="setup"):
    """The setup / account screen as the hub serves it (values injected, English)."""
    with TempHome():
        return runtime.page((hub.APP_DIR / "setup.html").read_bytes(), "hub", None, {
            "UPSTREAM": "https://github.com/4dcitygml/sample-tokyo-station", "DEFAULT_DEST": "/tmp/dest", "MODE": mode,
        }).decode("utf-8")


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
        for token in ("%%INVITE%%", "copyInvite", "Waiting for an invitation"):
            self.assertNotIn(token, h)
        self.assertIn("The source data cannot be reached", h)

    def test_four_screens_and_no_url_input(self):
        h = setup_html()
        for fn in ("screenConnect", "screenFork", "screenClone", "screenDone"):
            self.assertIn(fn, h)
        # Zero input fields as a rule; only the destination override (folded into "Advanced
        # settings") and the hand-over screen's one checkbox (hub-v1.2.1).
        self.assertEqual(h.count("<input"), 2)
        self.assertIn('id="dest"', h)
        self.assertIn('type="checkbox" id="legacyRemove"', h)
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

    def test_account_is_an_explicit_choice_never_a_silent_reuse(self):
        # hub-v1.2.1: a saved connection, this computer's GitHub CLI and a new sign-in are
        # three buttons on the account screen; nothing connects without a click.
        h = setup_html()
        self.assertNotIn("s.reusedAuth", h)
        for fn in ("screenLegacy", "doUse(", "doMachine()", "doLegacy()"):
            self.assertIn(fn, h)
        for key in ("hub.account_use_machine", "hub.account_use_saved", "hub.account_use_new", "hub.account_title"):
            self.assertIn(key, h)
        self.assertIn("Which GitHub account should this city use?", h)
        # The clone-exists case shows the account screen (and the copy step when the account
        # has no fork yet), then goes to the dashboard.
        self.assertIn("if (MODE === 'account' && st && st.login && (st.fork || st.forkChecked === false)) { location.replace('/'); return; }", h)
        self.assertIn("if (MODE === 'account') return (s.login && !s.fork && s.forkChecked !== false) ? 1 : 0;", h)

    def test_template_values_are_json_escaped(self):
        # A Windows destination with backslashes must survive as a JS string literal, and a
        # value must not be able to close the script block.
        html = runtime.page(b"<html><head></head><body></body></html>", "hub", None,
                            {"DEFAULT_DEST": "C:\\Users\\naoko\\Documents\\CityGML Data (x)", "X": "</script><b>"}).decode("utf-8")
        self.assertIn('window.CITYGML = {"DEFAULT_DEST": "C:\\\\Users\\\\naoko', html)
        self.assertNotIn("</script><b>", html)
        self.assertIn("const { UPSTREAM, DEFAULT_DEST, MODE } = window.CITYGML;", setup_html())

    def test_hand_over_screen_lists_traces_and_removes_only_our_own(self):
        h = setup_html()
        self.assertIn("Hand-over from the earlier version", h)
        self.assertIn("The Git settings of this computer are not changed", h)
        self.assertIn("only that; your own Git identity and sign-ins stay", h)
        self.assertIn("legacyPending ? screenLegacy", h)

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
        self.assertIn("if (st.active && MODE !== 'account') { clearTimeout(timer);", h)

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


class TestOneDistribution(unittest.TestCase):
    def test_the_hub_zip_is_the_only_distribution(self):
        # hub-v1.2: cities distribute no code and the editors travel inside the hub zip;
        # the earlier standalone attribute-editor zips (release-attr-editor.yml) are retired.
        workflows = sorted(p.name for p in (REPO_ROOT / ".github" / "workflows").glob("release-*.yml"))
        self.assertEqual(workflows, ["release-hub.yml"])
        self.assertFalse((REPO_ROOT / "tools" / "attr_editor" / "packaging").exists())


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
        self._home = TempHome()
        self._home.__enter__()
        self.repo = object.__new__(attr.Repo)
        self.repo.root = None
        self.repo._origin_nwo = lambda: "beginner/sample-tokyo-station"
        accounts.save_account("tester", "saved-token", 1)
        self._login = patch.object(accounts, "login_for_clone", lambda root: "tester")
        self._login.start()

    def tearDown(self):
        self._login.stop()
        self._home.__exit__(None, None, None)
        super().tearDown()

    def test_saved_hub_connection_creates_upstream_pr_without_gh(self):
        captured = {}

        def fake_api(path, token, method="GET", payload=None, timeout=30):
            captured.update(path=path, token=token, method=method, payload=payload)
            return 201, {"html_url": "https://github.com/4dcitygml/sample-tokyo-station/pull/123"}

        with patch.object(runtime, "github_api", fake_api):
            url, note = self.repo._create_pr_api("edit/b-1", "title", "body")
        self.assertEqual(url, "https://github.com/4dcitygml/sample-tokyo-station/pull/123")
        self.assertIsNone(note)
        self.assertEqual(captured["path"], "/repos/4dcitygml/sample-tokyo-station/pulls")
        self.assertEqual(captured["token"], "saved-token")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["payload"]["head"], "beginner:edit/b-1")
        self.assertEqual(captured["payload"]["base"], "main")

    def test_missing_saved_connection_uses_fallback_without_api_call(self):
        accounts.delete_account("tester")
        with patch.object(runtime, "github_api", lambda *a, **k: self.fail("must not call API without token")):
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

        with patch.object(runtime, "github_api", offline):
            url, note = self.repo._create_pr_api("edit/b-1", "title", "body")
        self.assertIsNone(url)
        self.assertIn("Connection error", note)

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


class TestReleaseZipIsInstallerPayload(unittest.TestCase):
    def test_release_zip_is_an_installer_payload(self):
        # hub-v1.2.0: no entrance page and no top-level launchers are bundled; the zip
        # holds program/ only and carries the per-user launchers for the installer.
        from tests.test_bundle import build_bundle
        wf = (REPO_ROOT / ".github" / "workflows" / "release-hub.yml").read_text(encoding="utf-8")
        self.assertNotIn("%%BUNDLE_OS%%", wf)
        self.assertNotIn("READ-ME-FIRST", wf)
        for name in ("citygml.sh", "citygml.ps1", "runtime.py", "accounts.py", "git_sync.py", "shortcuts.py", "pr_classification.py"):
            self.assertIn(f"{build_bundle.LIB}/{name}", build_bundle.required("macos"), name)
        self.assertFalse((REPO_ROOT / "tools" / "hub" / "getting-started.html").exists())   # the entrance page is gone


class TestWindowsBundle(unittest.TestCase):
    """MinGit layout of the all-in-one Windows distribution and the Git selection rules."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = (
            REPO_ROOT / ".github" / "workflows" / "release-hub.yml"
        ).read_text(encoding="utf-8")

    def test_the_bundle_is_defined_once_and_tested_here(self):
        # A7: the city clone carries no tools/, so the editors and the packs travel inside the
        # hub zip. What travels is the manifest in scripts/build_bundle.py (tests/test_bundle.py
        # builds and verifies it from this tree); the workflow only calls the two scripts.
        self.assertIn("scripts/build_bundle.py", self.workflow)
        self.assertIn("scripts/verify_bundle.py", self.workflow)
        rt = (REPO_ROOT / "tools" / "runtime.py").read_text(encoding="utf-8")
        self.assertIn("from i18n import i18n_loader", rt)
        self.assertIn("from themes import theme_loader", rt)
        hub_src = (REPO_ROOT / "tools" / "hub" / "app.py").read_text(encoding="utf-8")
        self.assertIn("runtime.SHARED_DIR / rel", hub_src)   # the editors are found next to the hub, never in a clone

    def test_release_uses_pinned_mingit_and_python(self):
        # A5/A7: build-time downloads are pinned by version URL + SHA-256 and verified before
        # extraction; no moving releases/latest reference; bundled-Python zip only (2026-08-28).
        for marker in ("MINGIT_URL:", "MINGIT_SHA256:", "PYEMBED_URL:", "PYEMBED_SHA256:", "Fetch-Verified", "Expand-Archive"):
            self.assertIn(marker, self.workflow)
        self.assertNotIn("releases/latest", self.workflow)
        self.assertNotIn("pip install pyinstaller", self.workflow)
        self.assertIn("needs: [windows, macos-zip]", self.workflow)
        self.assertIn("GH_REPO: ${{ github.repository }}", self.workflow)

    def test_program_folder_is_the_one_shared_location(self):
        # program/ in the bundle, tools/ in the source tree: the folder that holds runtime.py
        self.assertEqual(runtime.SHARED_DIR, REPO_ROOT / "tools")
        self.assertEqual(hub.APP_DIR.parent, runtime.SHARED_DIR)
        self.assertEqual(attr.APP_DIR.parent, runtime.SHARED_DIR)

    def test_git_is_resolved_from_the_program_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bundled = root / "PortableGit" / "cmd" / ("git.exe" if runtime.WINDOWS else "git")
            bundled.parent.mkdir(parents=True)
            bundled.touch()
            with patch.object(runtime, "SHARED_DIR", root), patch.object(runtime.shutil, "which", lambda _n: None):
                runtime.reset_caches()
                self.assertEqual(Path(runtime.git_exe()), bundled)
                self.assertTrue(runtime.git_bundled())
            runtime.reset_caches()

    def test_bundled_git_is_preferred_when_present(self):
        # hub-v1.2.1 writes the identity into the clone and hands credentials over per command,
        # so the computer's git configuration no longer decides which git runs: the bundle first.
        with tempfile.TemporaryDirectory() as d:
            bundled = Path(d) / "PortableGit" / "cmd" / ("git.exe" if runtime.WINDOWS else "git")
            bundled.parent.mkdir(parents=True)
            bundled.touch()
            system = str(Path(d) / "system" / "git")
            with patch.object(runtime, "SHARED_DIR", Path(d)), patch.object(runtime.shutil, "which", lambda _n: system):
                runtime.reset_caches()
                self.assertEqual(runtime.git_exe(), str(bundled))
                self.assertTrue(runtime.git_bundled())
            runtime.reset_caches()

    def test_path_git_is_used_without_a_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            system = str(Path(d) / "system" / "git")
            with patch.object(runtime, "SHARED_DIR", Path(d)), patch.object(runtime.shutil, "which", lambda _n: system):
                runtime.reset_caches()
                self.assertEqual(runtime.git_exe(), system)
                self.assertFalse(runtime.git_bundled())
            runtime.reset_caches()

    def test_without_an_account_the_computers_credentials_are_never_used(self):
        # hub-v1.2.1: no account → helpers reset (anonymous fetch works, a push fails plainly);
        # the keychain / manager / global store of the computer is never consulted.
        for exe, bundled in (("C:/Program Files/Git/cmd/git.exe", False), ("C:/bundle/git.exe", True)):
            with fake_git(exe, bundled):
                self.assertEqual(runtime.git_args(net=True, store=accounts.store_for(None)), [exe, "-c", "credential.helper="])
                self.assertEqual(runtime.git_args(net=False), [exe])
                self.assertNotIn("manager", " ".join(runtime.git_args(net=True)))

    def test_origin_follows_the_accounts_fork(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "4dcitygml.json").write_text(json.dumps({"repo": "4dcitygml/sample-tokyo-station", "data_dirs": ["x"]}))
            (root / "x").mkdir(); (root / "x" / "a.gml").write_text("<x/>")
            subprocess.run([git, "-C", d, "init", "-q"], check=True)
            subprocess.run([git, "-C", d, "remote", "add", "origin", "https://github.com/olduser/sample-tokyo-station.git"], check=True)
            h = hub.Hub(root)
            self.assertTrue(h.ensure_origin("NewUser/sample-tokyo-station"))
            self.assertEqual(h.nwo(), "NewUser/sample-tokyo-station")
            self.assertFalse(h.ensure_origin("newuser/sample-tokyo-station"))   # same fork, case-insensitive: untouched
            self.assertFalse(h.ensure_origin(""))
            # a clone made straight from the city is repointed too (uploads never go to the city);
            # never another repository, never an unrecognised remote (SSH alias)
            subprocess.run([git, "-C", d, "remote", "set-url", "origin", "https://github.com/4dcitygml/sample-tokyo-station.git"], check=True)
            self.assertTrue(h.ensure_origin("NewUser/sample-tokyo-station"))
            self.assertEqual(h.nwo(), "NewUser/sample-tokyo-station")
            subprocess.run([git, "-C", d, "remote", "set-url", "origin", "git@github.com-alias:olduser/sample-tokyo-station.git"], check=True)
            self.assertFalse(h.ensure_origin("NewUser/sample-tokyo-station"))
            subprocess.run([git, "-C", d, "remote", "set-url", "origin", "https://github.com/olduser/other-repo.git"], check=True)
            self.assertFalse(h.ensure_origin("NewUser/sample-tokyo-station"))

    def test_network_git_uses_only_the_accounts_store_on_every_platform(self):
        # hub-v1.2.1: system Git and bundled Git alike — the account's store, the
        # computer's helper list reset, the token never in argv.
        with TempHome() as home:
            accounts.save_account("tester", "secret-token", 1)
            self.assertEqual(accounts.credentials_path("tester").read_text(encoding="utf-8"),
                             "https://x-access-token:secret-token@github.com\n")
            store = accounts.store_for("tester")
            self.assertEqual(store, home / ".citygml" / "auth" / "tester.git-credentials")
            for exe, bundled in (("C:/bundle/git.exe", True), ("/usr/bin/git", False)):
                with fake_git(exe, bundled):
                    args = runtime.git_args(net=True, store=store)
                    self.assertEqual(args[0], exe)
                    self.assertIn("credential.helper=", args)
                    self.assertTrue(any(a.startswith("credential.https://github.com.helper=store --file=") for a in args))
                    self.assertNotIn("secret-token", " ".join(args))
                    self.assertEqual(runtime.git_args(net=False), [exe])
            # a store path with a space is quoted for git's helper line
            spaced = home / "git credentials" / "tester.git-credentials"
            spaced.parent.mkdir()
            spaced.write_text("x")
            self.assertTrue(any("'" in a for a in runtime.git_args(net=True, store=spaced)))

    def test_hub_repo_commands_use_resolved_git(self):
        seen = []
        fake_run = lambda args, **kwargs: seen.append(args) or SimpleNamespace(returncode=0, stdout="true\n")
        with fake_git("/bundle/PortableGit/cmd/git.exe", True), patch.object(runtime.subprocess, "run", fake_run):
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(hub.Hub(Path(d))._git("status"), "true")
        self.assertEqual(seen[0][0], "/bundle/PortableGit/cmd/git.exe")

class TestAuthFlow(_EnglishEnv):
    """Verifies the device-flow state machine and the account choices with HTTP stubbed out."""

    def setUp(self):
        super().setUp()
        self._home = TempHome()
        self._home.__enter__()
        self.acc = accounts
        for target, name, value in (
            (hub, "oauth_client_id", lambda: "test-client-id"),
            (runtime, "github_user_status", lambda token: ((200, {"login": "tester", "id": 1}) if token and token != "dead"
                                                          else (401, None) if token == "dead" else (0, None))),
            (runtime, "post_form", lambda url, f, timeout=15: {"error": "expired_token"}),
            (runtime, "git_exe", lambda: None),
        ):
            self.patch(target, name, value)
        self.session = hub.Session()
        self.session.city = "4dcitygml/sample-tokyo-station"
        self.mgr = self.session.account

    def tearDown(self):
        self._home.__exit__(None, None, None)
        super().tearDown()

    def patch(self, target, name, value):
        p = patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

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
        runtime.post_form = fake
        r = self.mgr.start()
        self.assertEqual(r["userCode"], "ABCD-1234")
        st = self.mgr.state()
        self.assertTrue(st["waiting"])
        self.assertTrue(st["clientId"])

    def _poll_with(self, responses):
        """Runs _poll directly to reach a final state (no threads, no waiting)."""
        seq = list(responses)
        runtime.post_form = lambda url, f, timeout=15: seq.pop(0) if seq else {"error": "expired_token"}
        self.mgr._poll("cid", "dc", 0, 1)
        return self.mgr.state()

    def test_success_sets_login_and_binds_the_city(self):
        st = self._poll_with([{"error": "authorization_pending"}, {"access_token": "tok"}])
        self.assertFalse(st["waiting"])
        self.assertIsNone(st["error"])
        self.assertEqual(st["login"], "tester")
        self.assertTrue(st["chosen"])
        self.assertEqual(self.mgr.token(), "tok")
        self.assertEqual(self.acc.token_for("tester"), "tok")
        self.assertEqual(self.acc.city_login("4dcitygml/sample-tokyo-station"), "tester")

    def test_saved_account_is_offered_not_used(self):
        self.acc.save_account("tester", "saved-token", 1)
        st = self.mgr.state()
        self.assertIsNone(st["login"])
        self.assertFalse(st["chosen"])
        self.assertEqual(st["accounts"], ["tester"])
        self.assertEqual(self.mgr.token(), "")

    def test_choosing_a_saved_account_binds_it(self):
        self.acc.save_account("tester", "saved-token", 1)
        self.assertIsNone(self.mgr.use_saved("tester"))
        st = self.mgr.state()
        self.assertEqual((st["login"], st["chosen"]), ("tester", True))
        self.assertEqual(self.mgr.token(), "saved-token")
        self.assertIsNotNone(self.mgr.use_saved("../etc"))
        self.assertIsNotNone(self.mgr.use_saved("nobody"))

    def test_revoked_saved_account_is_dropped_with_a_reason(self):
        self.acc.save_account("tester", "dead", 1)
        err = self.mgr.use_saved("tester")
        self.assertIn("revoked", err)
        self.assertEqual(self.acc.list_accounts(), [])

    def test_machine_sign_in_is_used_only_on_request(self):
        self.patch(accounts, "machine_token", lambda run=None: "gho_machine")
        st = self.mgr.state()
        self.assertFalse(st["chosen"])
        self.assertIsNone(self.mgr.use_machine())
        self.assertEqual(self.mgr.state()["login"], "tester")
        self.assertEqual(self.acc.token_for("tester"), "gho_machine")

    def test_machine_sign_in_missing_is_explained(self):
        self.assertIn("is not signed in", self.mgr.use_machine())

    def test_recorded_account_is_adopted_at_start(self):
        self.acc.save_account("tester", "saved-token", 1)
        self.acc.bind_city_login("4dcitygml/sample-tokyo-station", "tester")
        session = hub.Session()
        session.start(None, "4dcitygml/sample-tokyo-station")
        self.assertEqual(session.login, "tester")
        self.assertEqual(session.token(), "saved-token")
        other = hub.Session()
        other.start(None, "4dcitygml/sample-munich-station")
        self.assertIsNone(other.login)   # another city never inherits the account

    def test_disconnect_unbinds_and_can_delete(self):
        self.acc.save_account("tester", "saved-token", 1)
        self.mgr.use_saved("tester")
        self.mgr.disconnect()
        self.assertIsNone(self.mgr.login)
        self.assertIsNone(self.acc.city_login("4dcitygml/sample-tokyo-station"))
        self.assertEqual(self.acc.list_accounts()[0]["login"], "tester")
        self.mgr.use_saved("tester")
        self.mgr.disconnect(delete=True)
        self.assertEqual(self.acc.list_accounts(), [])

    def test_clone_appearing_later_records_the_binding(self):
        self.acc.save_account("tester", "saved-token", 1)
        session = hub.Session()
        session.start(None, None)         # started by hand: no city known yet
        self.assertIsNone(session.account.use_saved("tester"))
        self.assertIsNone(self.acc.city_login("4dcitygml/sample-tokyo-station"))
        with tempfile.TemporaryDirectory() as d:
            Path(d, "4dcitygml.json").write_text(json.dumps({"repo": "4dcitygml/sample-tokyo-station"}))
            session.open_clone(d)
        self.assertEqual(session.city, "4dcitygml/sample-tokyo-station")
        self.assertEqual(self.acc.city_login("4dcitygml/sample-tokyo-station"), "tester")

    def test_renamed_or_recased_account_keeps_one_file(self):
        self.acc.save_account("Tester", "saved-token", 1)      # stored under the old spelling
        self.assertIsNone(self.mgr.use_saved("Tester"))        # GitHub answers "tester"
        self.assertEqual([a["login"] for a in self.acc.list_accounts()], ["tester"])
        self.assertEqual(self.mgr.login, "tester")

    def test_settings_payload_survives_offline(self):
        self.acc.save_account("tester", "saved-token", 1)
        self.mgr.use_saved("tester")
        self.session.fork.clear()
        def offline(token, login):
            raise hub.urllib.error.URLError("no network")
        with patch.object(hub, "find_fork", offline):
            payload = self.session.settings_payload()
        self.assertEqual(payload["login"], "tester")
        self.assertIsNone(payload["fork"])

    def test_unwritable_account_folder_is_explained(self):
        self.acc.save_account("tester", "saved-token", 1)
        orig = self.acc.save_account
        def failing(*a, **k):
            raise OSError("Permission denied")
        self.acc.save_account = failing
        try:
            err = self.mgr.use_saved("tester")
            self.assertIn("could not be saved", err)
            self.assertIn("Permission denied", err)
            self.assertIsNone(self.mgr.login)
            st = self._poll_with([{"access_token": "tok"}])
            self.assertIn("could not be saved", st["error"])
            self.assertFalse(st["waiting"])
        finally:
            self.acc.save_account = orig

    def test_binding_survives_the_clone_registration_at_start(self):
        # Session.start() registers the clone (runtime.remember_clone) right before account.attach(): the
        # city's entry must be merged, or the account chosen last time is lost at every start.
        self.acc.save_account("tester", "saved-token", 1)
        self.acc.bind_city_login("4dcitygml/sample-tokyo-station", "tester")
        runtime.remember_clone("4dcitygml/sample-tokyo-station", "/x/clone")
        runtime.save_config({"lang": "ja"})
        session = hub.Session()
        session.start(None, "4dcitygml/sample-tokyo-station")
        self.assertEqual(session.login, "tester")
        entry = runtime.read_config()["cities"]["4dcitygml/sample-tokyo-station"]
        self.assertEqual((entry["repo"], entry["login"]), ("/x/clone", "tester"))
        self.assertEqual(runtime.read_config()["lang"], "ja")

    def test_stale_401_never_deletes_a_newer_binding(self):
        self.acc.save_account("dead-one", "dead", 1)
        self.acc.save_account("tester", "saved-token", 2)
        self.mgr.login, self.mgr._token = "dead-one", "dead"
        real = runtime.github_user_status
        def slow_then_rebound(token):
            if token == "dead":
                self.mgr._bind("tester", "saved-token")   # the person chose another account meanwhile
                return 401, None
            return real(token)
        runtime.github_user_status = slow_then_rebound
        self.mgr.user()
        self.assertEqual(self.mgr.login, "tester")
        self.assertEqual({a["login"] for a in self.acc.list_accounts()}, {"dead-one", "tester"})
        self.assertEqual(self.acc.city_login("4dcitygml/sample-tokyo-station"), "tester")

    def test_machine_label_never_touches_github_or_the_token(self):
        self.patch(accounts, "machine_login_from_config", lambda: "mach")
        self.patch(accounts, "machine_token", lambda run=None: self.fail("the token is read only after the click"))
        runtime.github_user_status = lambda token: self.fail("no network for a label")
        st = self.mgr.state()
        self.assertEqual(st["machine"], {"login": "mach"})
        self.assertFalse(st["machineChecking"])

    def test_disconnect_clears_the_clone_identity(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([git, "-C", d, "init", "-q"], check=True)
            self.acc.save_account("tester", "saved-token", 1)
            with fake_git(git):
                self.session.start(d, "4dcitygml/sample-tokyo-station", sync=False)
                self.mgr.use_saved("tester")
                self.assertEqual(self.acc.clone_identity(d)["name"], "tester")
                self.mgr.disconnect()
                self.assertEqual(self.acc.clone_identity(d), {"name": "", "email": ""})

    def test_legacy_review_removes_only_on_request(self):
        legacy = runtime.legacy_credentials_path()
        legacy.write_text("https://x:y@github.com\n")
        st = self.mgr.state()
        self.assertTrue(st["legacy"]["plainStore"])
        self.assertFalse(st["legacyReviewed"])
        self.assertEqual(self.mgr.review_legacy(False), [])
        self.assertTrue(legacy.exists())
        self.assertTrue(self.mgr.state()["legacyReviewed"])
        self.assertEqual(self.mgr.review_legacy(True), ["plainStore"])
        self.assertFalse(legacy.exists())

    def test_sign_in_offline_is_a_plain_sentence(self):
        def fake(url, f, timeout=15):
            raise hub.urllib.error.URLError("no network")
        runtime.post_form = fake
        with self.assertRaises(RuntimeError) as ctx:
            self.mgr.start()
        self.assertIn("Check the internet connection", str(ctx.exception))
        self.assertNotIn("URLError", str(ctx.exception))

    def test_every_dead_end_names_the_way_out(self):
        # Each failure on the account screen ends in an instruction, not just a diagnosis.
        self.acc.save_account("tester", "dead", 1)
        self.assertIn("Choose one of the options", self.mgr.use_saved("tester"))
        self.assertIn("Choose one of the options", self.mgr.use_saved("nobody"))
        self.assertIn("Choose one of the other options", self.mgr.use_machine())
        runtime.github_user_status = lambda token: (0, None)
        self.acc.save_account("tester", "saved", 1)
        self.assertIn("press the same choice again", self.mgr.use_saved("tester"))
        self.assertEqual(self.acc.token_for("tester"), "saved")   # offline never deletes

    def test_access_denied_is_explained(self):
        st = self._poll_with([{"error": "access_denied"}])
        self.assertFalse(st["waiting"])
        self.assertIn("canceled", st["error"])

    def test_expired_is_explained(self):
        st = self._poll_with([{"error": "expired_token"}])
        self.assertIn("time limit expired", st["error"])


class TestFork(unittest.TestCase):
    def test_create_fork_requires_login(self):
        with patch.object(runtime, "github_user_status", lambda token: (0, None)):
            nwo, err = hub.create_fork("")
        self.assertIsNone(nwo)
        self.assertIn("GitHub", err)

    def test_existing_fork_is_reused_without_creating(self):
        calls = []
        def fake_api(path, token, method="GET", payload=None, timeout=30):
            calls.append((method, path))
            return 200, {"fork": True, "full_name": "tester/sample-tokyo-station"}
        with patch.object(runtime, "github_user_status", lambda token: (200, {"login": "tester", "id": 1})), \
                patch.object(runtime, "github_api", fake_api), TempHome():
            nwo, err = hub.create_fork("tok")
        self.assertEqual(nwo, "tester/sample-tokyo-station")
        self.assertIsNone(err)
        self.assertEqual([m for m, _ in calls], ["GET"])  # POST /forks is never called

    def test_upstream_nwo(self):
        with TempHome():
            self.assertEqual(runtime.upstream_nwo(), "4dcitygml/sample-tokyo-station")


class TestUpstreamAccess(unittest.TestCase):
    """Upstream access check (public operation: no invitation flow; removed in #9)."""

    def setUp(self):
        self._api = runtime.github_api
        self.session = hub.Session()
        self.session.account.login = "tester"

    def tearDown(self):
        runtime.github_api = self._api

    def test_upstream_ok_caches_success(self):
        seq = iter([(200, {})])
        runtime.github_api = lambda *a, **k: next(seq)
        self.assertTrue(self.session.upstream_ok())
        # once reachable, the API is never called again
        runtime.github_api = lambda *a, **k: self.fail("must not call API after access is confirmed")
        self.assertTrue(self.session.upstream_ok())

    def test_found_copy_is_kept_for_a_minute_missing_one_for_ten_seconds(self):
        calls = []
        with patch.object(hub, "find_fork", lambda token, login: calls.append(1) or None):
            self.assertIsNone(self.session.fork_nwo())
            _, until = self.session.fork.get(), self.session.fork._until
            self.assertLess(until - hub.time.time(), 11)                       # not found: asked again soon
        with patch.object(hub, "find_fork", lambda token, login: calls.append(1) or "tester/r"):
            self.assertEqual(self.session.fork_nwo(fresh=True), "tester/r")
            self.assertGreater(self.session.fork._until - hub.time.time(), 50)  # found: kept for a minute
            self.assertEqual(self.session.fork_nwo(), "tester/r")               # served from memory
        self.assertEqual(len(calls), 2)

    def test_upstream_ok_is_none_when_not_logged_in(self):
        runtime.github_api = lambda *a, **k: self.fail("must not call API when not connected")
        self.session.account.login = None
        self.assertIsNone(self.session.upstream_ok())

    def test_no_access_is_cached_briefly(self):
        # 404 → False. Repeat calls within the TTL never hit the API
        seq = iter([(404, {})])
        runtime.github_api = lambda *a, **k: next(seq)
        self.assertFalse(self.session.upstream_ok())
        runtime.github_api = lambda *a, **k: self.fail("must not call API within TTL")
        self.assertFalse(self.session.upstream_ok())

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
        self.assertIn("The account recorded for this city is used", text)
        self.assertIn("account screen may be skipped", text)

    def test_console_messages_keep_japanese_when_lang_ja(self):
        # Regression: with CITYGML_LANG=ja the original Japanese terminal guidance is returned
        os.environ["CITYGML_LANG"] = "ja"
        text = "\n".join(hub.setup_console_messages(True))
        self.assertIn("状態: 初回セットアップ中（正常です。停止していません）", text)
        self.assertIn("この都市に記録されたアカウントを使います。アカウント画面は省略されることがあります", text)
        self.assertIn("ブラウザの案内を進めてください", text)


class TestDest(unittest.TestCase):
    def test_default_dest_is_not_asked(self):
        d = Path(hub.Session().default_dest())
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
    def test_hub_has_no_global_git_writer(self):
        # hub-v1.2.1: the commit identity is written into the clone's own config by
        # accounts.apply_clone_identity; nothing writes `git config --global` any more.
        for name in ("git_config_set", "apply_git_identity", "write_git_credentials", "save_token", "load_token"):
            self.assertFalse(hasattr(hub, name), name)
        src = (REPO_ROOT / "tools" / "hub" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn('"config", "--global", key, val', src)
        self.assertNotIn("gh\", \"auth\", \"token", src)

    def test_git_choice_ignores_the_computers_git_configuration(self):
        # hub-v1.2.1: identity lives in the clone, credentials are handed over per command,
        # so whether the person configured git globally does not decide which git runs.
        exe = shutil.which("git")
        if not exe:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(Path(d) / "gitconfig")}):
            runtime.reset_caches()
            self.assertEqual(runtime.git_exe(), exe)
        runtime.reset_caches()

    def test_attr_config_write_failure_does_not_break_activation(self):
        with TempHome():
            runtime.config_path().mkdir()   # write_text on a directory raises OSError
            runtime.save_config({"repo": "C:/data/sample-tokyo-station"})

    def test_org_restriction_is_translated_for_fork_creation(self):
        def api(path, token, method="GET", payload=None, timeout=30):
            if path.endswith("/forks") and method == "POST":
                return 403, {"message": "Although you appear to have the correct authorization credentials, "
                                        "the city organization has enabled OAuth App access restrictions"}
            return 404, {}
        with patch.object(runtime, "github_user_status", lambda token: (200, {"login": "tester", "id": 1})), \
                patch.object(runtime, "github_api", api), TempHome():
            nwo, err = hub.create_fork("tok")
        self.assertIsNone(nwo)
        self.assertIn("has not approved this tool", err)
        self.assertIn("Organization access", err)


class _FakeAuth:
    def __init__(self, token="", login=None):
        self._token, self._login = token, login
        self.login = login

    def token(self):
        return self._token

    def user(self):
        return {"login": self._login, "id": 1} if self._login else None


class TestContributions(_EnglishEnv):
    """Dashboard PR / Issue retrieval (GraphQL version without the gh CLI; follow-up to #61)."""

    def setUp(self):
        super().setUp()
        self._auth, self._api = hub.SESSION.account, runtime.github_api
        with tempfile.TemporaryDirectory() as d:
            self.hub_obj = hub.Hub(Path(d))
        self.hub_obj.nwo = lambda: "4dcitygml/sample-tokyo-station"

    def tearDown(self):
        hub.SESSION.account, runtime.github_api = self._auth, self._api
        super().tearDown()

    def test_disconnected_returns_reason_without_network(self):
        hub.SESSION.account = _FakeAuth(token="")
        runtime.github_api = lambda *a, **k: self.fail("must not call API when not connected")
        r = self.hub_obj._fetch_contributions()
        self.assertFalse(r["ok"])
        self.assertIsNone(r["login"])
        self.assertIn("GitHub is not connected", r["reason"])
        self.assertEqual(r["badge"], hub.badge_for(0))

    def test_maps_graphql_fields_like_gh(self):
        hub.SESSION.account = _FakeAuth(token="tok", login="tester")
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
        runtime.github_api = fake_api
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
        hub.SESSION.account = _FakeAuth(token="tok", login="tester")
        runtime.github_api = lambda *a, **k: (200, {"errors": [{"message": "rate limited"}]})
        r = self.hub_obj._fetch_contributions()
        self.assertFalse(r["ok"])
        self.assertIn("rate limited", r["reason"])
        self.assertEqual(r["login"], "tester")

    def test_status_does_not_depend_on_gh(self):
        # presence of gh does not appear in status (the connection state comes from the session's account)
        st = self.hub_obj.status()
        self.assertNotIn("gh", st)
        self.assertIn("runtime", st)


class TestFeedbackIssues(_EnglishEnv):
    def setUp(self):
        super().setUp()
        self._auth, self._api = hub.SESSION.account, runtime.github_api
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
        hub.SESSION.account, runtime.github_api = self._auth, self._api
        super().tearDown()

    def test_defaults_include_existing_badge_and_prefilled_context(self):
        hub.SESSION.account = _FakeAuth(token="tok", login="tester")
        result = self.hub_obj.feedback_defaults()
        self.assertTrue(result["connected"])
        self.assertEqual(result["login"], "tester")
        self.assertEqual(result["badge"]["name"], "Regular contributor")
        self.assertEqual(result["merged"], 3)
        self.assertIn("building data editing tools", result["goal"])
        self.assertEqual(result["categories"], list(hub.feedback_categories()))

    def test_submit_uses_hub_token_and_records_badge_in_issue(self):
        hub.SESSION.account = _FakeAuth(token="tok", login="tester")
        captured = {}

        def fake_api(path, token, method="GET", payload=None, timeout=30):
            captured.update(path=path, token=token, method=method, payload=payload)
            return 201, {
                "number": 321,
                "title": payload["title"],
                "html_url": "https://github.example/issues/321",
            }

        runtime.github_api = fake_api
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
        hub.SESSION.account = _FakeAuth(token="tok", login="tester")
        runtime.github_api = lambda *a, **k: self.fail("must not call API with invalid input")
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
