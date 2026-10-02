#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the repo-language principle.

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

from tests.support import SUPPORTED  # noqa: E402  (the one list, tools/i18n/i18n_loader.py)

import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from tests.support import EnglishEnv, runtime

# which recorded outcome the driver turns into which gate row (ci/pr_analysis_main.sh, S17)
_GATE_OUTCOMES = {"reason": "REASON", "classification": "CLASSIFICATION", "commit-scope": "COMMIT_SCOPE",
                  "freshness": "FRESHNESS", "schema": "FORMAT", "minimal-diff": "REVIEWABILITY",
                  "texture": "TEXTURE", "structure": "STRUCTURE", "plausibility": "PLATEAU", "model": "PREVIEW",
                  "reproduction": "REPRODUCTION", "scope-reproducibility": "SCOPE_REPRODUCIBILITY",
                  "topology": "TOPOLOGY"}


def write_gates(runner_temp: Path, env: dict) -> None:
    """Record the gate rows as the driver would for these outcomes: success 0, failure 1, the
    classification's warn-only mode 0 with a warning; any other value leaves the row unrecorded."""
    from scripts import gate_result
    code = {"success": (0, 0), "failure": (1, 0), "warning": (0, 1)}
    for key, var in _GATE_OUTCOMES.items():
        if env.get(var + "_OUTCOME") in code:
            exit_code, warnings = code[env[var + "_OUTCOME"]]
            gate_result.write(runner_temp, gate_result.result(key, exit_code, warnings=warnings))
    repo, quality = env.get("REPO_SCOPE_OUTCOME"), env.get("QUALITY_OUTCOME")
    if repo or quality:                     # file-scope folds both (the driver writes it after the quality step)
        worst = 1 if "failure" in (repo, quality) else 0
        gate_result.write(runner_temp, gate_result.result("file-scope", worst))

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("hub_app", REPO_ROOT / "tools" / "hub" / "app.py")
hub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hub)

_attr_spec = importlib.util.spec_from_file_location(
    "attr_app", REPO_ROOT / "tools" / "attr_editor" / "app.py")
attr = importlib.util.module_from_spec(_attr_spec)
_attr_spec.loader.exec_module(attr)

_i18n_spec = importlib.util.spec_from_file_location(
    "i18n_loader", REPO_ROOT / "tools" / "i18n" / "i18n_loader.py")
i18n = importlib.util.module_from_spec(_i18n_spec)
_i18n_spec.loader.exec_module(i18n)

LANGS = SUPPORTED
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
    key = "pr.title_tex_add" if add else "pr.title_tex_update"
    default = ("Add textures ({n} faces): {bid}" if add
               else "Update textures ({n} faces): {bid}")
    return i18n.translate("tex_editor", key, default, lang=lang,
                          n=3, bid="13101-bldg-1")


class _EnglishEnv(EnglishEnv):
    home = True   # a temporary HOME as well: the editor reads the settings file


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
        # v3.0.0: the literals live in scripts/pr_classification.py (one table for CI and clients).
        table = (REPO_ROOT / "scripts" / "pr_classification.py").read_text(encoding="utf-8")
        self.assertIn("テクスチャ", table)
        self.assertIn("Textur", table)
        self.assertTrue(tex_title_for("ja").startswith("テクスチャ"))
        self.assertTrue(tex_title_for("de").startswith("Textur"))


class TestBodyStaysCiSafe(_EnglishEnv):
    """Every language's generated body passes CI reason extraction and never
    contains a placeholder literal."""

    def test_the_reason_section_is_the_proposers_words(self):
        # maintainer decision 1 (2026-10-01): the anchored section holds the proposer's reason; a
        # generated sentence there meant A1 could never fail. Empty, it fails CI's check.
        from scripts import pr_reason
        for lang in LANGS:
            body = attr.build_pr_body("13101-bldg-1", "bldg-1", CHANGES, SOURCES,
                                      "Field survey sheet of 2026-09-01", None, lang=lang)
            match = CI_ANCHOR.search(body)
            self.assertIsNotNone(match, f"{lang}: anchor missing")
            self.assertEqual(pr_reason.section(body), "Field survey sheet of 2026-09-01", lang)
            self.assertTrue(pr_reason.acceptable(body), lang)
            empty = attr.build_pr_body("13101-bldg-1", "bldg-1", CHANGES, SOURCES, "", None, lang=lang)
            self.assertFalse(pr_reason.acceptable(empty), f"{lang}: an empty reason must not pass")
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

    def _run(self, city_json: "str | None", strict: bool = False, incomplete: bool = False,
             classification: str = "success"):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            if city_json is not None:
                (tmpdir / "4dcitygml.json").write_text(city_json, encoding="utf-8")
            event = tmpdir / "event.json"
            event.write_text(json.dumps(
                {"pull_request": {"number": 1, "title": "x", "head": {"ref": "edit/x", "sha": "a" * 40}, "base": {"sha": "b" * 40}}}),
                encoding="utf-8")
            env = dict(os.environ, GITHUB_REPOSITORY="",
                       GITHUB_EVENT_PATH=str(event), TOOLS_DIR=str(REPO_ROOT),
                       RUNNER_TEMP=str(tmpdir), GML_COUNT="1",
                       REASON_OUTCOME="failure", FRESHNESS_OUTCOME="success",
                       CLASSIFICATION_OUTCOME=classification)
            for key in ('COMMIT_SCOPE', 'QUALITY', 'FORMAT', 'REVIEWABILITY', 'STRUCTURE', 'PLATEAU', 'PREVIEW'):
                env[key + '_OUTCOME'] = 'success'
            if incomplete:
                env['REASON_OUTCOME'] = 'success'
                env['FORMAT_OUTCOME'] = 'cancelled'
            if strict:
                env["STRICT_GATE"] = "1"
            write_gates(tmpdir, env)
            proc = subprocess.run(
                ["bash", str(REPO_ROOT / "ci" / "inspection_summary.sh")],
                cwd=tmpdir, env=env, capture_output=True, text=True)
            inspection = (tmpdir / "out" / "inspection.md").read_text(encoding="utf-8")
            resubmission = (tmpdir / "out" / "resubmission.md").read_text(encoding="utf-8")
            self.structured = json.loads((tmpdir / 'out/inspection.json').read_text())
            return proc, inspection, resubmission

    def test_incomplete_process_blocks_without_asking_proposer_to_fix_data(self):
        proc, inspection, resubmission = self._run('{"lang":"ja"}', strict=True, incomplete=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('<!-- status:pending -->', resubmission)
        self.assertNotIn('<!-- status:active -->', resubmission)
        self.assertEqual(len(self.structured['checks']), 14)   # v3.0.0: + classification
        self.assertEqual(next(r['status'] for r in self.structured['checks'] if r['key']=='schema'), 'pending')

    def test_unclassified_pr_gets_the_guidance_table_in_repo_language(self):
        # A5: strict rule, helpful reply — the ❌ row plus the how-to-fix table.
        proc, inspection, resubmission = self._run('{"lang": "ja"}', strict=True, classification="failure")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("<!--cp:classification-->", inspection)
        self.assertEqual(next(r['status'] for r in self.structured['checks'] if r['key'] == 'classification'), 'fail')
        self.assertIn("この提案の分類のしかた", resubmission)
        self.assertIn("`edit/`", resubmission)
        self.assertIn("`Update attributes`", resubmission)
        self.assertNotIn("予告", resubmission)

    def test_repo_scope_rejection_folds_into_file_scope_with_guidance(self):
        # A11: code in a city PR fails `file-scope`; the reply names the files and where they belong.
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "4dcitygml.json").write_text('{"lang": "ja"}', encoding="utf-8")
            event = tmpdir / "event.json"
            event.write_text(json.dumps({"pull_request": {"number": 1, "title": "x", "head": {"ref": "edit/x", "sha": "a" * 40}, "base": {"sha": "b" * 40}}}), encoding="utf-8")
            (tmpdir / "citygml_repo_scope.json").write_text(json.dumps(
                {"ok": False, "rejected": [{"path": "install/start-mac.command", "note": "executable code", "category": "rejected"}]}),
                encoding="utf-8")
            env = dict(os.environ, GITHUB_REPOSITORY="", GITHUB_EVENT_PATH=str(event), TOOLS_DIR=str(REPO_ROOT), RUNNER_TEMP=str(tmpdir),
                       GML_COUNT="0", REASON_OUTCOME="success", FRESHNESS_OUTCOME="success",
                       CLASSIFICATION_OUTCOME="success", REPO_SCOPE_OUTCOME="failure", STRICT_GATE="1",
                       COMMIT_SCOPE_OUTCOME="success", TEXTURE_OUTCOME="success")
            write_gates(tmpdir, env)
            proc = subprocess.run(["bash", str(REPO_ROOT / "ci" / "inspection_summary.sh")], cwd=tmpdir, env=env,
                                  capture_output=True, text=True)
            structured = json.loads((tmpdir / "out" / "inspection.json").read_text())
            resubmission = (tmpdir / "out" / "resubmission.md").read_text(encoding="utf-8")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(next(r["status"] for r in structured["checks"] if r["key"] == "file-scope"), "fail")
        self.assertIn("この都市リポジトリが受け付けないファイル", resubmission)
        self.assertIn("`install/start-mac.command`", resubmission)
        self.assertIn("4dcitygml/tools", resubmission)

    def test_warn_only_transition_passes_with_advisory(self):
        # CITYGML_CLASSIFICATION_WARN_ONLY: the row warns and does not block (S17: warnings show in
        # the row, maintainer decision 2026-09-30); the guidance is posted as an advisory.
        env_reason_ok = '{"lang": "de"}'
        proc, inspection, resubmission = self._run(env_reason_ok, strict=True, classification="warning")
        self.assertEqual(next(r['status'] for r in self.structured['checks'] if r['key'] == 'classification'), 'warn')
        self.assertIn("### Hinweis", resubmission)
        self.assertIn("`geom/`", resubmission)

    def run_rows(self, statuses: dict, lang='{"lang": "en"}'):
        """The summary over explicit gate rows (every other applicable row passes)."""
        import subprocess
        from scripts import gate_result
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "4dcitygml.json").write_text(lang, encoding="utf-8")
            (tmpdir / "event.json").write_text(json.dumps({"pull_request": {"number": 1, "title": "x", "head": {"ref": "edit/x", "sha": "a" * 40}, "base": {"sha": "b" * 40}}}),
                                               encoding="utf-8")
            (tmpdir / "out").mkdir()
            (tmpdir / "out" / "plausibility_lint.md").write_text("**❌ 0 error(s) / ⚠️ 1 warning(s)**", encoding="utf-8")
            for row in gate_result.ROWS:
                status = statuses.get(row.key, "pass")
                code = {"pass": 0, "fail": 1, "warn": 1, "error": 2}[status]
                gate_result.write(tmpdir, gate_result.result(row.key, code))
            env = dict(os.environ, GITHUB_REPOSITORY="", GITHUB_EVENT_PATH=str(tmpdir / "event.json"), TOOLS_DIR=str(REPO_ROOT),
                       RUNNER_TEMP=str(tmpdir), GML_COUNT="1", STRICT_GATE="1", TOPOLOGY_APPLICABLE="true")
            proc = subprocess.run(["bash", str(REPO_ROOT / "ci" / "inspection_summary.sh")], cwd=tmpdir, env=env,
                                  capture_output=True, text=True)
            return (proc, json.loads((tmpdir / "out/inspection.json").read_text()),
                    (tmpdir / "out/inspection.md").read_text(encoding="utf-8"),
                    (tmpdir / "out/resubmission.md").read_text(encoding="utf-8"))

    def test_every_recorded_value_has_a_reader(self):
        # S4: records nothing read (PR_CLASS, PREVIEW_URL, a dozen *_OUTCOME) are gone; the rows come
        # from the gates' result files, and the outcomes file carries only what the summary reads
        import re
        ci = REPO_ROOT / "ci"
        recorded = set()
        for script in ci.glob("*.sh"):
            recorded |= set(re.findall(r"\brecord ([A-Z_]+)", script.read_text(encoding="utf-8")))
        summary = (REPO_ROOT / "scripts" / "inspection_summary.py").read_text(encoding="utf-8")
        self.assertTrue(recorded)
        self.assertEqual(sorted(v for v in recorded if f'"{v}"' not in summary), [])

    def test_the_driver_keeps_no_state_between_runs(self):
        # A12: fixed /tmp scratch files, and result files of an earlier run read as this run's
        import re
        driver = (REPO_ROOT / "ci" / "pr_analysis_main.sh").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"(?<!:-)/tmp/\S*", driver), [])
        self.assertIn('rm -rf gates citygml_commit_scope.json', driver)

    def test_a_warning_shows_and_does_not_block(self):
        # D15: plausibility_lint's header always carries the cross and warning signs; the row now comes
        # from the gate's result, and an advisory finding is a warning that does not block
        proc, structured, inspection, resubmission = self.run_rows({"plausibility": "warn"})
        rows = {r["key"]: r for r in structured["checks"]}
        self.assertEqual((rows["plausibility"]["status"], rows["plausibility"]["severity"], rows["plausibility"]["ran"]),
                         ("warn", "advisory", True))
        self.assertNotIn("outcomes", structured)   # the trusted side reads each row's ran
        # S9: the context the trusted side compares with the live PR has one definition, the contract master's
        import importlib.util
        spec = importlib.util.spec_from_file_location("contract", REPO_ROOT / "tools/hub/operator_explanation.py")
        contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract)
        pr = {"number": 1, "title": "x", "head": {"ref": "edit/x", "sha": "a" * 40}, "base": {"sha": "b" * 40}}
        self.assertEqual(structured["context"], contract.context(pr))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("⚠️ Warning (does not block)", inspection)
        self.assertIn("<!-- status:resolved -->", resubmission)
        self.assertIn("Warnings (no action required)", resubmission)

    def test_a_system_error_blocks_and_is_not_the_proposers_data(self):
        # D16: the gate could not judge; the row says so and the proposer is not asked to fix data
        proc, structured, inspection, resubmission = self.run_rows({"schema": "error"}, lang='{"lang": "ja"}')
        rows = {r["key"]: r for r in structured["checks"]}
        self.assertEqual((rows["schema"]["status"], rows["schema"]["ran"]), ("error", False))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("⚙️ 仕組みのエラー", inspection)
        self.assertIn("データの問題ではありません", resubmission)
        self.assertNotIn("<!-- status:active -->", resubmission)

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
