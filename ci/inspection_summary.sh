#!/usr/bin/env bash
# Copyright (c) 2026 4dcitygml
#
# CityGML PR analysis driver (inspection summary) — shared CI logic, plan C.
# Assembles the automatic inspection summary table and the resubmission-request
# comment from the outcomes recorded by ci/pr_analysis_main.sh (and, when it
# ran, ci/topology_gate.sh). Always runs (the wrapper calls it with
# `if: always()`), mirroring the original workflow's summary step.
#
# Contract with the wrapper:
#   - cwd = city repository checkout; out/ holds the comment bodies so far
#   - $RUNNER_TEMP/citygml_outcomes.env holds KEY=VALUE lines from the drivers
#   - STRICT_GATE=1 (practice repositories, set via the CITYGML_STRICT_GATE
#     repository variable) makes this script exit non-zero when any check is
#     "❌ Needs attention", so the job conclusion becomes a real merge gate for
#     branch protection / auto-merge. Default (unset) keeps the original
#     behavior: findings are comments only and the job stays green.

set -euo pipefail
PY="$(command -v python || command -v python3)"   # setup-python provides `python` on runners; python3 is the local fallback

OUTCOMES="${RUNNER_TEMP:-/tmp}/citygml_outcomes.env"
if [ -f "$OUTCOMES" ]; then
  set -a
  # KEY=VALUE lines produced by our own drivers (no quoting subtleties).
  . "$OUTCOMES"
  set +a
fi

mkdir -p out
"$PY" - "$GITHUB_EVENT_PATH" <<'PY'
import json
import os
from pathlib import Path
import sys

event = json.load(open(sys.argv[1], encoding="utf-8"))
pr = event.get("pull_request", {})
title = str(pr.get("title") or "")
branch = str(pr.get("head", {}).get("ref") or "")
kind = os.environ.get("PROPOSAL_KIND") or (
    "texture" if branch.startswith("tex/")
    or title.startswith(("Update textures", "Add textures", "テクスチャ", "Textur"))  # ja/de literals: match generated repo-language titles and contributor input — do not translate
    else "geometry" if any(x in title for x in (
        "geometry", "building shape", "rebuild",
        "幾何", "建物形状", "建替", "建て替"))  # Japanese literals: match contributor input — do not translate
    else "attribute"
)
has_gml = os.environ.get("GML_COUNT") != "0"
scope_extract = os.environ.get("SCOPE_EXTRACT") == "true"
# source-baseline PRs skip the per-building reviewability lint and the 3D preview (bulk data)
baseline = os.environ.get("SOURCE_BASELINE") == "true"
# manifest-backed bulk PRs (identity-*, source-update): accepted by reproduction; per-building lint and preview are not applicable
bulk = os.environ.get("BULK_KIND") == "true"

# Repo-language comments (language policy: these two comments are repo-facing).
# Machine contract stays fixed: <!--cp:key--> anchors, result emoji, and the
# <!-- status:... --> markers are code-side; only display text localizes.
# Fail-open: any problem with the catalog keeps the English defaults.
def _load_ci_catalog():
    try:
        lang = str(json.load(open("4dcitygml.json", encoding="utf-8"))
                   .get("lang") or "").split("-")[0].strip().lower()
        if not lang or lang == "en":
            return {}
        path = (Path(os.environ.get("TOOLS_DIR") or "") / "tools" / "i18n"
                / "catalogs" / "ci" / f"{lang}.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

_CAT = _load_ci_catalog()

def T(key, default):
    value = _CAT.get(key)
    return value if isinstance(value, str) and value else default

def warning(path):
    p = Path(path)
    if not p.is_file():
        return False
    text = p.read_text(encoding="utf-8", errors="replace")
    return "❌" in text or "⚠️" in text

def result(outcome, applicable=True, warning_path=None):
    if not applicable:
        return "na"
    if warning_path and warning(warning_path):
        return "fail"
    if outcome == "success":
        return "pass"
    if outcome in ("failure", "skipped", "cancelled") or not outcome:
        return "fail"
    return "pending"

# Exchange format v2: matching uses the key (<!--cp:key-->) + emoji; display
# names follow the repo language (English defaults; hub matches by key/emoji).
rows = [
    ("reason", T("ci.check_reason", "Description and evidence"),
     result(os.environ.get("REASON_OUTCOME"))),
    ("commit-scope", T("ci.check_commit_scope", "One change = one building"),
     result(os.environ.get("COMMIT_SCOPE_OUTCOME"))),
    ("scope-reproducibility",
     T("ci.check_scope_reproducibility", "Ward extraction reproducibility"), result(
        os.environ.get("SCOPE_REPRODUCIBILITY_OUTCOME"), scope_extract
    )),
    ("reproduction", T("ci.check_reproduction", "Bulk conversion reproduction"), result(
        os.environ.get("REPRODUCTION_OUTCOME"), bulk, "out/reproduction.md"
    )),
    ("freshness", T("ci.check_freshness", "Consistency with the latest version"),
     result(os.environ.get("FRESHNESS_OUTCOME"))),
    ("file-scope", T("ci.check_file_scope", "Changed file scope"), result(
        os.environ.get("QUALITY_OUTCOME"), has_gml and not scope_extract
    )),
    ("schema", T("ci.check_schema", "CityGML format"),
     result(os.environ.get("FORMAT_OUTCOME"), has_gml)),
    ("minimal-diff", T("ci.check_minimal_diff", "Minimal diff"), result(
        os.environ.get("REVIEWABILITY_OUTCOME"),
        has_gml and not scope_extract and not baseline and not bulk, "out/lint.md"
    )),
    ("texture", T("ci.check_texture", "Texture consistency"),
     result(os.environ.get("TEXTURE_OUTCOME"),
            kind == "texture"
            or os.environ.get("IMAGES_CHANGED") == "true")),
    ("structure", T("ci.check_structure", "Geometric structure"), result(
        os.environ.get("STRUCTURE_OUTCOME"),
        has_gml and not scope_extract, "out/citygml_lint.md"
    )),
    ("plausibility", T("ci.check_plausibility", "Attribute value plausibility"), result(
        os.environ.get("PLATEAU_OUTCOME"),
        has_gml and not scope_extract, "out/plateau_lint.md"
    )),
    ("topology", T("ci.check_topology", "Topological consistency"), result(
        os.environ.get("TOPOLOGY_OUTCOME"),
        os.environ.get("TOPOLOGY_APPLICABLE") == "true" and not scope_extract,
        "out/val3dity.md"
    )),
    ("model", T("ci.check_model", "3D view"), result(
        os.environ.get("PREVIEW_OUTCOME"), has_gml and not scope_extract and not baseline and not bulk
    )),
]
# Emoji stay code-side (hub and STRICT_GATE match on them), words localize.
labels = {"pass": "✅ " + T("ci.label_pass", "Pass"),
          "fail": "❌ " + T("ci.label_fail", "Needs attention"),
          "na": "− " + T("ci.label_na", "Not applicable"),
          "pending": "… " + T("ci.label_pending", "Checking")}
inspection = [
    "<!-- citygml-automatic-inspection -->",
    "## ✅ " + T("ci.inspection_heading", "Automated inspection results"),
    "",
    "| " + T("ci.col_check", "Check") + " | " + T("ci.col_result", "Result") + " |",
    "|---|---|",
]
inspection += [
    f"| {name} <!--cp:{key}--> | {labels[state]} |"
    for key, name, state in rows
]
inspection += ["", "<sub>" + T(
    "ci.inspection_footnote",
    "In the review screen, pass is green, not applicable is gray,"
    " and unresolved is red.") + "</sub>"]
Path("out/inspection.md").write_text("\n".join(inspection) + "\n", encoding="utf-8")

failed = [(key, name) for key, name, state in rows if state == "fail"]
resubmit = ["<!-- citygml-auto-resubmission -->"]
if failed:
    resubmit += [
        "<!-- status:active -->",
        "## 💬 " + T("ci.resubmit_heading",
                     "Items to confirm from the automated checks"),
        "",
        T("ci.resubmit_intro",
          "The proposal has been received. The following items are being"
          " worked out between the proposer and CI."),
        "",
    ]
    resubmit += [f"- ❌ {name}" for _, name in failed]
    if any(key == "freshness" for key, _ in failed):
        resubmit += ["", T("ci.resubmit_freshness",
                           "Another change was applied first. Please merge"
                           " in the latest version and resubmit.")]
    resubmit += ["", T("ci.resubmit_outro",
                       "Updating the PR after fixing re-runs the automated"
                       " checks. No reviewer action is needed.")]
else:
    resubmit += [
        "<!-- status:resolved -->",
        "## ✅ " + T("ci.resolved_heading", "Automated checks complete"),
        "",
        T("ci.resolved_body",
          "No items need resubmission. Waiting for reviewer confirmation."),
    ]
Path("out/resubmission.md").write_text("\n".join(resubmit) + "\n", encoding="utf-8")
if failed:
    print("Items to confirm were collected into a comment. The PR itself remains accepted.")
PY

# Practice-repository merge gate (policy §2/§3): with STRICT_GATE=1, any
# "❌ Needs attention" row turns the job red so branch protection blocks
# auto-merge until the submitter fixes and resubmits. The comment bodies above
# are already written and will still be uploaded/posted (wrapper uses always()).
if [ "${STRICT_GATE:-}" = "1" ] && grep -q "❌" out/inspection.md; then
  echo "::error::STRICT_GATE: some checks need attention; failing the job so auto-merge stays blocked (see the inspection comment)."
  exit 1
fi
