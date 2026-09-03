#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the repo-language principle (案B).

Principle: repo-facing generated text (PR title, PR body, attribute labels in
PR text) follows the repository's working language (4dcitygml.json "lang");
the UI chrome follows the user's language; machine contracts (branch prefixes,
commit subjects, sec:reason anchor, placeholder literals) stay fixed.

These tests pin the cross-component contracts that make that safe:
- generated ja/de titles keep matching hub's review_kind() and the CI matcher
  literals (テクスチャ* / Textur*) even without a branch prefix
- the PR body stays CI-extractable (anchor) and placeholder-free per language
- label.* catalog keys cover exactly the LABELS/_RISK_TITLES tags (the static
  key-hygiene test in test_i18n cannot see these dynamically built keys)
- commit-facing labels resolve to English regardless of repo language
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("hub_app", REPO_ROOT / "tools" / "hub" / "app.py")
hub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hub)

_attr_spec = importlib.util.spec_from_file_location(
    "attr_app", REPO_ROOT / "tools" / "attr_editor" / "app.py")
attr = importlib.util.module_from_spec(_attr_spec)
_attr_spec.loader.exec_module(attr)

LANGS = ("en", "ja", "de")
CHANGES = [{
    "key": "storeysAboveGround#0", "tag": "storeysAboveGround",
    "index": 0, "old": "2", "new": "3", "label": "地上階数",
}]
SOURCES = {"storeysAboveGround#0": {"code": "801", "label": "Field survey"}}

# The exact extraction/placeholder logic from ci/pr_analysis_main.sh
CI_ANCHOR = re.compile(
    r"^##[^\n]*<!--\s*sec:reason\s*-->[^\n]*$\n(.*?)(?=^##\s+|\Z)",
    flags=re.MULTILINE | re.DOTALL,
)
CI_PLACEHOLDERS = ("please fill in", "not filled in", "記入してください",
                   "未記入", "TODO", "TBD")


def title_for(lang: str, many: bool = False) -> str:
    key = "pr.title_attr_many" if many else "pr.title_attr"
    default = ("Update building info: {label} and {n} more"
               if many else "Update building info: {label}")
    return attr.tr_lang(lang, key, default,
                        label=attr.label_in(lang, "storeysAboveGround", ""), n=2)


def tex_title_for(lang: str, add: bool = False) -> str:
    mod = attr.i18n_module()
    key = "pr.title_tex_add" if add else "pr.title_tex_update"
    default = ("Add textures ({n} faces): {bid}" if add
               else "Update textures ({n} faces): {bid}")
    return mod.translate("tex_editor", key, default, lang=lang,
                         n=3, bid="13101-bldg-1")


class _EnglishEnv(unittest.TestCase):
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


class TestTitlesKeepClassifying(_EnglishEnv):
    """Generated titles classify correctly in every language, title-only
    (no branch prefix), mirroring a manual PR."""

    def test_attr_titles_classify_as_attribute(self):
        for lang in LANGS:
            for many in (False, True):
                pr = {"title": title_for(lang, many), "head": {"ref": "feature-x"}}
                self.assertEqual(hub.review_kind(pr), "attribute",
                                 f"{lang} many={many}: {pr['title']!r}")

    def test_tex_titles_classify_as_texture(self):
        for lang in LANGS:
            for add in (False, True):
                pr = {"title": tex_title_for(lang, add), "head": {"ref": "feature-x"}}
                self.assertEqual(hub.review_kind(pr), "texture",
                                 f"{lang} add={add}: {pr['title']!r}")

    def test_tex_titles_match_ci_shell_literals(self):
        """The ja/de tex title prefixes and the CI matcher literals stay in sync."""
        analysis = (REPO_ROOT / "ci" / "pr_analysis_main.sh").read_text(encoding="utf-8")
        summary = (REPO_ROOT / "ci" / "inspection_summary.sh").read_text(encoding="utf-8")
        for sh in (analysis, summary):
            self.assertIn("テクスチャ", sh)
            self.assertIn("Textur", sh)
        self.assertTrue(tex_title_for("ja").startswith("テクスチャ"))
        self.assertTrue(tex_title_for("de").startswith("Textur"))


class TestBodyStaysCiSafe(_EnglishEnv):
    """Every language's generated body passes CI reason extraction and never
    contains a placeholder literal."""

    def test_reason_extracts_and_no_placeholder(self):
        for lang in LANGS:
            body = attr.build_pr_body("13101-bldg-1", "bldg-1", CHANGES,
                                      SOURCES, "", None, lang=lang)
            match = CI_ANCHOR.search(body)
            self.assertIsNotNone(match, f"{lang}: anchor missing")
            reason = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.DOTALL).strip()
            self.assertGreaterEqual(len(reason), 5, f"{lang}: reason too short")
            for p in CI_PLACEHOLDERS:
                self.assertNotIn(p.lower(), body.lower(),
                                 f"{lang}: placeholder {p!r} in body")
            self.assertNotIn(title_for(lang), CI_PLACEHOLDERS)


class TestLanguageResolution(_EnglishEnv):
    def test_norm_repo_lang(self):
        self.assertEqual(attr.norm_repo_lang("ja"), "ja")
        self.assertEqual(attr.norm_repo_lang("ja-JP"), "ja")
        self.assertEqual(attr.norm_repo_lang("DE"), "de")
        self.assertEqual(attr.norm_repo_lang(None), "en")
        self.assertEqual(attr.norm_repo_lang(""), "en")

    def test_read_repo_lang_fallback_en(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(attr.read_repo_lang(tmp), "en")  # no 4dcitygml.json
            (Path(tmp) / "4dcitygml.json").write_text('{"lang": "de"}', encoding="utf-8")
            self.assertEqual(attr.read_repo_lang(tmp), "de")

    def test_sample_repo_langs_are_catalog_languages(self):
        """The shipped city configs only use languages we have catalogs for."""
        base = REPO_ROOT.parent
        expected = {"sample-tokyo-station": "ja", "sample-newyork-station": "en",
                    "sample-munich-station": "de"}
        for repo, lang in expected.items():
            cfg = base / repo / "4dcitygml.json"
            if not cfg.is_file():
                continue  # tools repo may be checked out standalone
            meta = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertEqual(attr.norm_repo_lang(meta.get("lang")), lang, repo)


class TestLabelResolution(_EnglishEnv):
    """UI labels follow the user language; PR labels follow the repo language;
    commit-facing labels stay English."""

    def test_dual_resolution(self):
        os.environ["CITYGML_LANG"] = "de"
        self.assertEqual(attr.ui_label("storeysAboveGround"), "Geschosse über Grund")
        self.assertEqual(attr.label_in("ja", "storeysAboveGround", ""), "地上階数")
        self.assertEqual(attr.label_in("en", "storeysAboveGround", ""),
                         "Storeys Above Ground")

    def test_unknown_tags_keep_caller_fallback(self):
        self.assertEqual(attr.label_in("ja", "someDataName", "data label"), "data label")

    def test_label_catalog_covers_exactly_the_label_tables(self):
        """label.* keys ⇔ LABELS/_RISK_TITLES tags, in every language.

        (The static key-hygiene test in test_i18n cannot see these keys —
        they are built dynamically — so completeness is pinned here.)"""
        expected = ({f"label.{t}" for t in attr.LABELS}
                    | {f"label.risk_{t}" for t in attr._RISK_TITLES})
        for lang in LANGS:
            catalog = json.loads(
                (REPO_ROOT / "tools" / "i18n" / "catalogs" / "attr_editor" /
                 f"{lang}.json").read_text(encoding="utf-8"))
            got = {k for k in catalog if k.startswith("label.")}
            self.assertEqual(got, expected, f"{lang}: label key set mismatch")


class TestPreviewMatchesBody(_EnglishEnv):
    """The send-dialog preview is the same text as the posted PR body."""

    def test_summary_is_verbatim_in_body(self):
        for lang in LANGS:
            summary = attr.pr_summary(CHANGES, SOURCES, lang)
            body = attr.build_pr_body("id", "gid", CHANGES, SOURCES, lang=lang)
            self.assertIn(summary, body, f"{lang}: preview text drifted from body")

    def test_preview_endpoint_uses_repo_language(self):
        stub = attr.Repo.__new__(attr.Repo)
        stub._repo_lang = "ja"
        with tempfile.TemporaryDirectory() as tmp:
            stub.root = Path(tmp)  # no 4dcitygml.json: city name degrades to ""
            result = attr.Repo.preview_pr(stub, {
                "tile": "53394611", "gid": "bldg-1",
                "changes": CHANGES,
                "sourceSelections": [{"key": "storeysAboveGround#0", "code": "801"}],
            })
        self.assertEqual(result["repoLang"], "ja")
        self.assertIn("地上階数", result["summary"])  # repo language, not the en UI

    def test_preview_skips_changes_without_source(self):
        stub = attr.Repo.__new__(attr.Repo)
        stub._repo_lang = "en"
        with tempfile.TemporaryDirectory() as tmp:
            stub.root = Path(tmp)
            result = attr.Repo.preview_pr(stub, {
                "tile": "x", "gid": "g", "changes": CHANGES, "sourceSelections": [],
            })
        self.assertEqual(result["summary"], "")


class TestCiCommentsFollowRepoLanguage(_EnglishEnv):
    """The two orchestration comments (inspection summary / resubmission)
    localize to the repo language while every machine marker stays fixed."""

    def _run(self, city_json: "str | None", strict: bool = False):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            if city_json is not None:
                (tmpdir / "4dcitygml.json").write_text(city_json, encoding="utf-8")
            event = tmpdir / "event.json"
            event.write_text(json.dumps(
                {"pull_request": {"title": "x", "head": {"ref": "edit/x"}}}),
                encoding="utf-8")
            env = dict(os.environ,
                       GITHUB_EVENT_PATH=str(event), TOOLS_DIR=str(REPO_ROOT),
                       RUNNER_TEMP=str(tmpdir), GML_COUNT="1",
                       REASON_OUTCOME="failure", FRESHNESS_OUTCOME="success")
            if strict:
                env["STRICT_GATE"] = "1"
            proc = subprocess.run(
                ["bash", str(REPO_ROOT / "ci" / "inspection_summary.sh")],
                cwd=tmpdir, env=env, capture_output=True, text=True)
            inspection = (tmpdir / "out" / "inspection.md").read_text(encoding="utf-8")
            resubmission = (tmpdir / "out" / "resubmission.md").read_text(encoding="utf-8")
            return proc, inspection, resubmission

    def test_ja_repo_gets_japanese_comments_with_fixed_markers(self):
        proc, inspection, resubmission = self._run('{"lang": "ja"}')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # localized display text
        self.assertIn("自動検査の結果", inspection)
        self.assertIn("説明と根拠資料", inspection)
        self.assertIn("❌ 要確認", inspection)
        self.assertIn("自動検査からの確認事項", resubmission)
        # machine contract unchanged
        self.assertIn("<!-- citygml-automatic-inspection -->", inspection)
        self.assertIn("<!--cp:reason-->", inspection)
        self.assertIn("<!-- status:active -->", resubmission)

    def test_missing_config_keeps_english(self):
        proc, inspection, _ = self._run(None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Automated inspection results", inspection)
        self.assertIn("❌ Needs attention", inspection)

    def test_strict_gate_still_fails_on_localized_comments(self):
        proc, inspection, _ = self._run('{"lang": "ja"}', strict=True)
        self.assertEqual(proc.returncode, 1)  # emoji is code-side, gate holds
        self.assertIn("❌", inspection)


if __name__ == "__main__":
    unittest.main()
