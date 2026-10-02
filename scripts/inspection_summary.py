#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The automatic inspection summary: one row per gate, the resubmission comment, inspection.json.

Called by ci/inspection_summary.sh (the city workflow's entry point) after the analysis
drivers. Each gate recorded its row as $RUNNER_TEMP/gates/<key>.json (scripts/gate_result.py,
S17): the status comes from there and nowhere else - no scan of the gates' Markdown for signs
(D15), and an error of the gate itself is its own status (D16). This script decides only
which rows apply, from the facts the driver recorded in the environment.

    python3 scripts/inspection_summary.py "$GITHUB_EVENT_PATH"     # exit 1 under STRICT_GATE=1 when a row blocks

Writes out/inspection.md, out/resubmission.md and out/inspection.json in the city checkout
(the working directory). The comment texts follow the city's language (ci catalogs); the
machine contract - <!--cp:key--> anchors, result signs, <!-- status:... --> markers - does not.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import gate_result  # noqa: E402
from scripts import pr_classification  # noqa: E402


def _contract():
    """The report contract master (tools/hub/operator_explanation.py): inspection.json's context
    is the one the trusted side compares with the live PR (S9: it was defined twice)."""
    spec = importlib.util.spec_from_file_location("operator_explanation", REPO_ROOT / "tools/hub/operator_explanation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_pr(pr: dict, env=os.environ, fetch=None) -> dict:
    """The PR as GitHub holds it now (GET /repos/{repo}/pulls/{number}), so that a re-run reads it
    anew: an event payload keeps the base it was sent with, and a PR reopened after main moved
    showed a base the PR no longer had (rehearsal, 2026-10-01). Used only while its head is the
    commit this run analysed (a newer head gets its own run); the payload on any failure."""
    repo, number = env.get("GITHUB_REPOSITORY", ""), pr.get("number")
    token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or ""
    if not repo or not number:
        return pr
    if fetch is None:
        def fetch(url):
            request = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                **({"Authorization": f"Bearer {token}"} if token else {})})
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
    api = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    try:
        live = fetch(f"{api}/repos/{repo}/pulls/{number}")
    except Exception:  # noqa: BLE001 - the payload is the fallback
        return pr
    if not isinstance(live, dict) or (live.get("head") or {}).get("sha") != (pr.get("head") or {}).get("sha"):
        return pr
    return live


def ci_catalog(city: Path) -> dict:
    """The ci catalog of the city's language; English (the defaults in the code) on any problem."""
    try:
        lang = str(json.loads((city / "4dcitygml.json").read_text(encoding="utf-8"))
                   .get("lang") or "").split("-")[0].strip().lower()
        if not lang or lang == "en":
            return {}
        data = json.loads((REPO_ROOT / "tools/i18n/catalogs/ci" / f"{lang}.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - fail open to English
        return {}


def applicable_rows(env) -> dict:
    """Which rows apply to this PR, from the facts the driver recorded. A row not named applies."""
    # GML_COUNT unset means the driver stopped before counting: the per-file rows then apply and,
    # without results, show as pending and block - never as not applicable.
    has_gml = env.get("GML_COUNT") != "0"
    scope_extract = env.get("SCOPE_EXTRACT") == "true"
    # source-baseline (bulk data), practice-reset (tree verified by commit-scope) and manifest-backed
    # bulk PRs (accepted by reproduction) skip the per-building lint and the 3D preview
    per_building = (has_gml and not scope_extract and env.get("SOURCE_BASELINE") != "true"
                    and env.get("PRACTICE_RESET") != "true" and env.get("BULK_KIND") != "true")
    kind = env.get("CHECK_KIND") or "attribute"   # computed once by the driver, with the trailers (S3)
    return {
        "scope-reproducibility": scope_extract,
        "reproduction": env.get("BULK_KIND") == "true",
        "schema": has_gml,
        "minimal-diff": per_building,
        "texture": kind == "texture" or env.get("IMAGES_CHANGED") == "true",
        "structure": has_gml and not scope_extract,
        "plausibility": has_gml and not scope_extract,
        "topology": env.get("TOPOLOGY_APPLICABLE") == "true" and not scope_extract,
        "model": per_building,
    }


def rows(env, runner_temp: Path, T) -> list[dict]:
    applicable = applicable_rows(env)
    out = []
    for row in gate_result.ROWS:
        ok = applicable.get(row.key, True)
        entry = gate_result.read(runner_temp, row.key) if ok else None
        status = "na" if not ok else (entry["status"] if entry else "pending")
        out.append({"key": row.key, "label": T(row.label_key, row.label), "status": status,
                    "severity": row.severity, "ran": bool(ok and entry and entry.get("ran"))})
    return out


def inspection_comment(checks: list[dict], T) -> str:
    # the signs stay code-side (the hub and older clients match on them), the words localize
    labels = {"pass": "✅ " + T("ci.label_pass", "Pass"),
              "warn": "⚠️ " + T("ci.label_warn", "Warning (does not block)"),
              "fail": "❌ " + T("ci.label_fail", "Needs attention"),
              "error": "⚙️ " + T("ci.label_error", "System error (not your data)"),
              "na": "− " + T("ci.label_na", "Not applicable"),
              "pending": "… " + T("ci.label_pending", "Checking")}
    lines = [
        "<!-- citygml-automatic-inspection -->",
        "## ✅ " + T("ci.inspection_heading", "Automated inspection results"),
        "",
        "| " + T("ci.col_check", "Check") + " | " + T("ci.col_result", "Result") + " |",
        "|---|---|",
    ]
    lines += [f"| {c['label']} <!--cp:{c['key']}--> | {labels[c['status']]} |" for c in checks]
    lines += ["", "<sub>" + T("ci.inspection_footnote",
                              "In the review screen, pass is green, not applicable is gray,"
                              " and unresolved is red.") + "</sub>"]
    return "\n".join(lines) + "\n"


def resubmission_comment(checks: list[dict], env, runner_temp: Path, catalog: dict, T) -> str:
    by_status = lambda status: [c for c in checks if c["status"] == status]
    failed, incomplete, system, warned = by_status("fail"), by_status("pending"), by_status("error"), by_status("warn")
    lines = ["<!-- citygml-auto-resubmission -->"]
    if system:
        # D16: a gate that could not judge is not the proposer's data; a re-run is the first step
        lines += [
            "<!-- status:pending -->",
            "## ⚙️ " + T("ci.error_heading", "Some checks could not run"),
            "", T("ci.error_body", "This is not a problem with your data. A re-run of the automated checks usually "
                                   "resolves it; if it keeps happening, the maintainers look into the check itself."),
            "",
        ]
        lines += [f"- ⚙️ {c['label']}" for c in system]
    elif incomplete:
        lines += [
            "<!-- status:pending -->",
            "## " + T("ci.incomplete_heading", "Automated inspection is incomplete"),
            "", T("ci.incomplete_body", "Some checks did not finish. Retry the failed process before deciding whether "
                                        "the proposer must fix the data. No operator transcription is required."),
            "",
        ]
        lines += [f"- {c['label']}" for c in incomplete]
    elif failed:
        lines += [
            "<!-- status:active -->",
            "## 💬 " + T("ci.resubmit_heading", "Items to confirm from the automated checks"),
            "",
            T("ci.resubmit_intro", "The proposal has been received. The following items are being"
                                   " worked out between the proposer and CI."),
            "",
        ]
        lines += [f"- ❌ {c['label']}" for c in failed]
        keys = {c["key"] for c in failed}
        if "freshness" in keys:
            lines += ["", T("ci.resubmit_freshness", "Another change was applied first. Please merge"
                                                     " in the latest version and resubmit.")]
        if "classification" in keys:
            # strict rule, helpful reply (A5): the table and both ways to fix it
            lines += ["", pr_classification.guide_markdown(catalog)]
        try:
            repo_scope = json.loads((runner_temp / "citygml_repo_scope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            repo_scope = {}
        if env.get("REPO_SCOPE_OUTCOME") == "failure" and repo_scope.get("rejected"):
            # A11: name the files a city repository does not accept and say where they belong
            lines += ["", "### " + T("ci.repo_scope_heading", "Files this city repository does not accept"), "",
                      T("ci.repo_scope_intro",
                        "A city repository holds data, documents and configuration. Tools, scripts and other code "
                        "belong in 4dcitygml/tools — please propose them there. Workflow files may change only "
                        "their CITYGML_TOOLS_REF pin."), ""]
            lines += [f"- `{r['path']}` — {r.get('note') or r.get('category')}" for r in repo_scope["rejected"]]
            lines += ["", T("ci.repo_scope_label_note",
                            "Maintainers can accept a CI maintenance change by applying the `tooling` label; "
                            "the report records that.")]
        lines += ["", T("ci.resubmit_outro", "Updating the PR after fixing re-runs the automated"
                                             " checks. No reviewer action is needed.")]
    else:
        lines += [
            "<!-- status:resolved -->",
            "## ✅ " + T("ci.resolved_heading", "Automated checks complete"),
            "",
            T("ci.resolved_body", "No items need resubmission. Waiting for reviewer confirmation."),
        ]
    if warned:
        lines += ["", "### ⚠️ " + T("ci.warnings_heading", "Warnings (no action required)"), "",
                  T("ci.warnings_body", "These checks noted something worth a look; they do not block the proposal. "
                                        "Details are in their own comments.")]
        lines += [f"- ⚠️ {c['label']}" for c in warned]
    if env.get("CLASSIFICATION_OUTCOME") == "warning":
        # CITYGML_CLASSIFICATION_WARN_ONLY: the rule is announced with the same guidance, not enforced
        lines += ["", pr_classification.guide_markdown(catalog, advisory=True)]
    return "\n".join(lines) + "\n"


def main(argv: list[str], env=os.environ, city: Path = Path(".")) -> int:
    event = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    pr = current_pr(event.get("pull_request", {}), env)
    runner_temp = Path(env.get("RUNNER_TEMP", "/tmp"))
    catalog = ci_catalog(city)

    def T(key: str, default: str) -> str:
        value = catalog.get(key)
        return value if isinstance(value, str) and value else default

    checks = rows(env, runner_temp, T)
    out = city / "out"
    out.mkdir(exist_ok=True)
    (out / "inspection.md").write_text(inspection_comment(checks, T), encoding="utf-8")
    try:
        lifecycle = json.loads((runner_temp / "citygml_commit_scope.json").read_text()).get("lifecycle", [])
    except (OSError, ValueError):
        lifecycle = []
    config = city / "4dcitygml.json"
    # structured evidence for the trusted report publisher (it never infers a status from a sign)
    (out / "inspection.json").write_text(json.dumps({
        "version": 1, "pr": pr.get("number"),
        "context": _contract().context(pr),
        "lang": json.loads(config.read_text()).get("lang", "en") if config.is_file() else "en",
        "checks": checks,
        "hasGml": env.get("GML_COUNT") != "0", "bulk": env.get("BULK_KIND") == "true",
        "scopeExtract": env.get("SCOPE_EXTRACT") == "true",
        "lifecycle": lifecycle,
        "toolsRef": env.get("CITYGML_TOOLS_REF", ""),
    }, ensure_ascii=False), encoding="utf-8")
    (out / "resubmission.md").write_text(resubmission_comment(checks, env, runner_temp, catalog, T), encoding="utf-8")
    if any(c["status"] == "fail" for c in checks):
        print("Items to confirm were collected into a comment. The PR itself remains accepted.")
    # production merge gate: fail, error and pending block; warnings do not (maintainer, 2026-09-30).
    # The comment bodies are written first and are posted anyway (the wrapper uses always()).
    if env.get("STRICT_GATE") == "1" and any(c["status"] in gate_result.BLOCKS for c in checks):
        print("::error::STRICT_GATE: some checks need attention; failing the job so auto-merge stays blocked "
              "(see the inspection comment).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
