#!/usr/bin/env bash
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
#
# CityGML PR analysis driver (main part) — shared CI logic, plan C.
#
# This script contains the analysis logic that used to live inline in the city
# repository's pr-analysis.yml. City repositories now carry only a thin wrapper
# workflow (from city-template) that checks out 4dcitygml/tools at a pinned
# major tag (v1) and runs this script, so fixes here reach every city repo
# without touching their workflow files (policy §4 plan C / §4b).
#
# Contract with the wrapper (.github/workflows/pr-analysis.yml in city repos):
#   - cwd = the city repository checkout (full history, base fetched)
#   - TOOLS_DIR = absolute path of the tools checkout (this repo, pinned tag)
#   - PREVIEW_BASE_URL = where the Cesium preview viewer is served; unset = no preview link
#   - Python 3.12 with lxml + xmlschema installed (pinned by the wrapper)
#   - writes step outputs (run/kind for the val3dity toolchain steps) to
#     $GITHUB_OUTPUT and cross-script state to $RUNNER_TEMP/citygml_outcomes.env
#   - comment bodies are collected under out/ (uploaded by the wrapper as the
#     "pr-comments" artifact; posted by the trusted pr-comment.yml)
#
# Semantics are ported 1:1 from the original workflow steps: each check section
# is isolated (failures are recorded as an outcome, analysis continues) and the
# final verdict is assembled by ci/inspection_summary.sh. This script itself
# exits non-zero only on infrastructure errors (same as the original workflow,
# where only non-continue-on-error steps could fail the job).

set -euo pipefail

: "${TOOLS_DIR:?TOOLS_DIR must point at the 4dcitygml/tools checkout}"
export PYTHONPATH="${TOOLS_DIR}${PYTHONPATH:+:$PYTHONPATH}"
PY="$(command -v python || command -v python3)"   # setup-python provides `python` on runners; python3 is the local fallback

EVENT="${GITHUB_EVENT_PATH:?}"
PR_NUMBER="$(jq -r '.pull_request.number' "$EVENT")"
BASE_SHA="$(jq -r '.pull_request.base.sha' "$EVENT")"
HEAD_SHA="$(jq -r '.pull_request.head.sha' "$EVENT")"
PR_TITLE="$(jq -r '.pull_request.title // ""' "$EVENT")"
PR_BRANCH="$(jq -r '.pull_request.head.ref // ""' "$EVENT")"
TEXTURE_OVERRIDE="$(jq -r '[.pull_request.labels[]?.name] | index("texture-override") != null' "$EVENT")"
# identity-review label: tier-C identity links may be applied only under explicit human review (commit scope gate).
export CITYGML_IDENTITY_REVIEW="$(jq -r '[.pull_request.labels[]?.name] | index("identity-review") != null' "$EVENT")"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"

OUTCOMES="${RUNNER_TEMP:-/tmp}/citygml_outcomes.env"
: > "$OUTCOMES"
# A12: results of an earlier run in the same temporary folder never count for this one, and
# scratch files live in a folder of this run's own
( cd "${RUNNER_TEMP:-/tmp}" && rm -rf gates citygml_commit_scope.json citygml_repo_scope.json citygml_lint_counts.json \
    plausibility_lint_counts.json topology_scope_output.txt )
SCRATCH="$(mktemp -d "${RUNNER_TEMP:-/tmp}/citygml-scratch.XXXXXX")"
record() { printf '%s=%s\n' "$1" "$2" >> "$OUTCOMES"; }
# gate KEY EXIT_CODE [extra gate_result args]: the row's result in its one form, $RUNNER_TEMP/gates/KEY.json
# (S17; exit codes: 0 pass, 1 finding, >=2 error). A write that fails leaves no file, and the
# summary then shows the row as not run (it blocks), never as passed.
gate() {
  local key="$1" code="$2"; shift 2
  "$PY" "$TOOLS_DIR/scripts/gate_result.py" write --key "$key" --exit-code "$code" --out "${RUNNER_TEMP:-/tmp}" "$@" > /dev/null || true
}
gate_na() { "$PY" "$TOOLS_DIR/scripts/gate_result.py" write --key "$1" --not-applicable --out "${RUNNER_TEMP:-/tmp}" > /dev/null || true; }

# --- Prepare output dir + carry PR number (hard step) ---
# Pass the PR number to the posting side via the artifact (workflow_run.pull_requests is empty for forks).
mkdir -p out
# An empty comment file is never posted: whichever step stops (a step's subshell exits on its
# first error, before any cleanup of its own), the driver removes empty files when it ends.
trap 'find out -maxdepth 1 -name "*.md" -empty -delete 2>/dev/null || true' EXIT
echo "$PR_NUMBER" > out/pr.txt

# --- Base branch freshness (continue-on-error) ---
set +e
git merge-base --is-ancestor "$BASE_SHA" "$HEAD_SHA"
rc=$?
set -e
case "$rc" in
  0) echo "Consistency with the latest version: OK" ;;
  1) echo "::warning::Another change was applied first. Merge in the latest version and resubmit." ;;
  *) echo "::error::git could not compare the PR with the latest version (exit $rc); a re-run may help."; rc=2 ;;
esac
gate freshness "$rc"

# --- Required explanation and evidence (continue-on-error) ---
set +e
"$PY" "$TOOLS_DIR/scripts/pr_reason.py" "$EVENT"   # one rule with the hub and the editors (S5)
rc=$?
gate reason "$rc"
set -e

# --- Commit scope inspection (1 commit = 1 building ID) (continue-on-error) ---
set +e
(
  set -uo pipefail
  # Failures are recorded in the outcome, but later checks continue and everything is collected into a polite comment at the end.
  "$PY" "$TOOLS_DIR/scripts/commit_building_scope.py" \
    --repo "$WORKSPACE" \
    --base-sha "$BASE_SHA" \
    --head-sha "$HEAD_SHA" \
    --json-output "${RUNNER_TEMP:-/tmp}/citygml_commit_scope.json" \
    > "$SCRATCH/commit-scope.txt" 2>&1
  rc=$?
  {
    echo "<!-- citygml-commit-scope -->"
    echo "## 🧱 Building / lifecycle event scope check"
    echo ""
    if [ "$rc" = "0" ]; then
      echo "✅ All commits in this PR passed."
    else
      echo "❌ Some commits need attention. The automated checks will comment with details."
    fi
    echo ""
    echo '```text'
    cat "$SCRATCH/commit-scope.txt"
    echo '```'
  } > out/commit-scope.md
  cat "$SCRATCH/commit-scope.txt"
  exit "$rc"
)
rc=$?
gate commit-scope "$rc"
set -e
# The PR's trailers, parsed once by the gate above (commit_building_scope.pr_facts): every
# decision below reads them here instead of grepping the commit messages. A missing or broken
# file reads as empty (flags false), as the greps found nothing.
fact() {
  "$PY" -c 'import json, sys
v = json.load(open(sys.argv[1])).get(sys.argv[2], "")
print(("true" if v else "false") if isinstance(v, bool) else "\n".join(v) if isinstance(v, list) else v)' \
    "${RUNNER_TEMP:-/tmp}/citygml_commit_scope.json" "$1" 2>/dev/null || true
}
flag() { [ "$(fact "$1")" = "true" ] && echo true || echo false; }
# A PR made only of accepted practice-reset commits (the gate above verified each against its
# Reset-To) returns many buildings at once; the one-building rule below does not apply to it.
RESET_ONLY="$(flag practiceResetOnly)"

# --- Find changed .gml files (hard step) ---
git diff --name-only --diff-filter=AMR "$BASE_SHA" "$HEAD_SHA" > all_changed.txt
grep -E '\.gml$' all_changed.txt > changed_gml.txt || true
GML_COUNT="$(wc -l < changed_gml.txt | tr -d ' ')"
record GML_COUNT "$GML_COUNT"

# Texture images are the image files inside the city's data directories (4dcitygml.json
# data_dirs, read at the base); a city that declares none treats every image as data.
DATA_IMAGES_RE="$("$PY" - "$BASE_SHA" <<'PY'
import json, re, subprocess, sys
raw = subprocess.run(["git", "show", f"{sys.argv[1]}:4dcitygml.json"], capture_output=True).stdout
try:
    dirs = [str(d).strip("/") for d in (json.loads(raw or b"{}").get("data_dirs") or []) if str(d).strip("/")]
except ValueError:
    dirs = []
prefix = "^(" + "|".join(re.escape(d) for d in dirs) + ")/.*" if dirs else ""
print(prefix + r"\.(jpg|jpeg|png|tif|tiff)$")
PY
)"

# IMAGES_CHANGED makes the summary's texture row applicable whenever image files
# are touched, even if the PR title/branch classifies as an attribute change
# (otherwise a bogus "image" in an attribute-titled PR would surface in no row).
IMAGES_CHANGED="$(grep -qE "$DATA_IMAGES_RE" all_changed.txt && echo true || echo false)"
record IMAGES_CHANGED "$IMAGES_CHANGED"

# --- Classification (Exchange Contract A5) (hard step) ---
# One table for CI and the review clients: scripts/pr_classification.py. A data PR
# (changed .gml or images) must be attribute / texture / geometry by branch or
# title, or administrative by trailers; anything else is rejected with guidance
# (the inspection summary appends the how-to-fix table). No silent default.
# CITYGML_CLASSIFICATION_WARN_ONLY=true (repository variable) turns the rejection
# into a warning for a city's first release under contract v3.0.0.
DATA_CHANGED=false
if [ "$GML_COUNT" != "0" ] || [ "$IMAGES_CHANGED" = "true" ]; then DATA_CHANGED=true; fi
ADMINISTRATIVE="$(flag administrative)"
# One call gives the class and the kind for checks (S3; both were computed separately, and the
# summary computed the kind a third time without the trailers). A failing call is not a success
# (A12: `|| true` turned a crash into "classified").
if classified="$("$PY" "$TOOLS_DIR/scripts/pr_classification.py" --branch "$PR_BRANCH" --title "$PR_TITLE" \
  --data-changed "$DATA_CHANGED" --administrative "$ADMINISTRATIVE" --print both)"; then
  read -r PR_CLASS CHECK_KIND <<<"$classified"
else
  PR_CLASS="error"; CHECK_KIND="attribute"
fi
record CHECK_KIND "$CHECK_KIND"
if [ "$PR_CLASS" = "error" ]; then
  echo "::error::The classification could not be computed (scripts/pr_classification.py failed); see the log."
  record CLASSIFICATION_OUTCOME failure
  gate classification 2
elif [ "$PR_CLASS" = "unclassified" ]; then
  if [ "${CITYGML_CLASSIFICATION_WARN_ONLY:-}" = "true" ]; then
    echo "::warning::This data proposal has no classification (branch prefix or title). It will be required; see the guidance comment."
    record CLASSIFICATION_OUTCOME warning
    gate classification 0 --warnings 1
  else
    echo "::error::This data proposal has no classification. Rename the branch (edit/ tex/ geom/) or edit the title; see the guidance comment."
    record CLASSIFICATION_OUTCOME failure
    gate classification 1
  fi
else
  echo "Classification: $PR_CLASS"
  record CLASSIFICATION_OUTCOME success
  gate classification 0
fi

# --- Repository scope (Exchange Contract A11) (continue-on-error) ---
# Cities distribute data, documents and configuration only; code belongs in
# 4dcitygml/tools. Workflow files may change only their CITYGML_TOOLS_REF pin
# (verified against the tools-v tags) unless a maintainer applied the `tooling`
# label. The result folds into the `file-scope` row of the inspection summary.
set +e
"$PY" "$TOOLS_DIR/scripts/repo_scope.py" --repo "$WORKSPACE" --base-sha "$BASE_SHA" --head-sha "$HEAD_SHA" \
  --event "$EVENT" --json-output "${RUNNER_TEMP:-/tmp}/citygml_repo_scope.json" > "$SCRATCH/repo-scope.txt" 2>&1
rc=$?
REPO_SCOPE_RC="$rc"
cat "$SCRATCH/repo-scope.txt"
[ "$rc" -eq 0 ] && record REPO_SCOPE_OUTCOME success || record REPO_SCOPE_OUTCOME failure
set -e

# --- Determine topology inspection scope (hard step, only when .gml changed) ---
TOPOLOGY_RUN="false"
PROPOSAL_KIND=""
if [ "$GML_COUNT" != "0" ]; then
  # The class for check scoping comes from scripts/pr_classification.py (A5), computed above.
  kind="$CHECK_KIND"
  PROPOSAL_KIND="$kind"
  SCOPE_OUT="${RUNNER_TEMP:-/tmp}/topology_scope_output.txt"
  : > "$SCOPE_OUT"
  "$PY" "$TOOLS_DIR/scripts/topology_scope.py" \
    --repo "$WORKSPACE" \
    --base-sha "$BASE_SHA" \
    --head-sha "$HEAD_SHA" \
    --kind "$kind" \
    --github-output "$SCOPE_OUT"
  TOPOLOGY_RUN="$(sed -n 's/^run=//p' "$SCOPE_OUT" | tail -1)"
  [ -n "$TOPOLOGY_RUN" ] || TOPOLOGY_RUN="false"
  grep -v '^run=' "$SCOPE_OUT" >> "${GITHUB_OUTPUT:-/dev/null}" || true   # run= is written once, below (S4)
fi
echo "kind=${PROPOSAL_KIND}" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "run=${TOPOLOGY_RUN}" >> "${GITHUB_OUTPUT:-/dev/null}"
record TOPOLOGY_APPLICABLE "$TOPOLOGY_RUN"
[ "$TOPOLOGY_RUN" = "true" ] || gate_na topology     # when it runs, ci/topology_gate.sh writes the row

# --- Detect municipality scope extraction (continue-on-error) ---
SCOPE_ENABLED="false"
SCOPE_MUNICIPALITY=""
if [ "$(flag scopeExtract)" = "true" ]; then
  municipalities="$(fact scopeMunicipalities)"
  if [ "$(printf '%s\n' "$municipalities" | grep -c .)" != "1" ]; then
    echo "::error::Scope-Municipality for scope-extract cannot be determined uniquely."
  else
    SCOPE_ENABLED="true"
    SCOPE_MUNICIPALITY="$municipalities"
  fi
fi
record SCOPE_EXTRACT "$SCOPE_ENABLED"

# --- Detect source baseline (initial official-source recording) ---
# A source-baseline PR adds a whole official dataset (hundreds to thousands of
# buildings). Per-building reviewability warnings and the 3D preview are
# meaningless there and would only produce comments too large to post; the
# commit-scope gate already verifies the baseline semantics (no GML before it,
# no per-building trailers). Format, structure and plausibility checks still run.
BASELINE_ENABLED="$(flag sourceBaseline)"
record SOURCE_BASELINE "$BASELINE_ENABLED"

# --- Detect a practice reset (a practice repository returning to its baseline) ---
# The commit-scope gate verifies the tree against the Reset-To commit; per-building
# reviewability and the 3D preview would only describe practice edits being undone.
RESET_ENABLED="$(flag practiceReset)"
record PRACTICE_RESET "$RESET_ENABLED"

# --- Detect bulk (manifest-backed) submissions: identity-baseline / identity-correction / source-update ---
# Accepted by reproduction (docs/bulk-submission-provenance.md): the commit scope
# gate has already checked every commit against the provenance manifest; here the
# manifest's materials are re-fetched and the manifest regenerated and compared.
IDENTITY_ENABLED="$(flag identity)"
BULK_ENABLED="$(flag bulk)"
record BULK_KIND "$BULK_ENABLED"
if [ "$BULK_ENABLED" = "true" ]; then
  set +e
  (
    set -euo pipefail
    ref="$(fact manifestRef)"
    manifest="${ref%%@sha256:*}"
    [ -f "$manifest" ] || { echo "::error::Provenance manifest not found at PR head: $manifest"; exit 1; }
    kind="$(jq -r '.kind // ""' "$manifest")"
    case "$kind" in
      identity-baseline|identity-correction) tool="identity_manifest.py" ;;
      source-update) tool="source_update_manifest.py" ;;
      carry-forward) tool="carry_forward_manifest.py" ;;
      semantic-correction)
        tool="lod0_semantic_manifest.py"
        # Validate paths, material names and recipe before fetching any input.
        # The recipe must run at the trusted tooling commit that generated it.
        actual="$(git -C "$TOOLS_DIR" rev-parse HEAD)"
        "$PY" "$TOOLS_DIR/scripts/$tool" check --manifest "$manifest" --ci --tools-commit "$actual"
        ;;
      *) echo "::error::No reproduction tool for manifest kind '$kind'"; exit 1 ;;
    esac
    materials="${RUNNER_TEMP:-/tmp}/citygml-materials"
    rm -rf "$materials"; mkdir -p "$materials"
    "$PY" "$TOOLS_DIR/scripts/fetch_materials.py" --manifest "$manifest" --outdir "$materials"
    "$PY" "$TOOLS_DIR/scripts/$tool" verify --manifest "$manifest" --materials-dir "$materials"
  ) > "$SCRATCH/bulk-reproduction.txt" 2>&1
  rc=$?
  set -e
  {
    echo "<!-- citygml-bulk-reproduction -->"
    echo "## 🔁 Bulk conversion reproduction"
    echo ""
    if [ "$rc" = "0" ]; then echo "✅ The provenance manifest regenerates identically from its declared materials."; else echo "❌ The conversion could not be reproduced from the declared materials."; fi
    echo ""
    echo '```text'
    tail -c 4000 "$SCRATCH/bulk-reproduction.txt"
    echo '```'
  } > out/reproduction.md
  gate reproduction "$rc"
else
  gate_na reproduction
fi

# --- Scope extraction reproducibility (continue-on-error, scope-extract only) ---
if [ "$SCOPE_ENABLED" = "true" ] && [ "$GML_COUNT" != "0" ]; then
  set +e
  (
    set -euo pipefail
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      if ! git cat-file -e "${BASE_SHA}:${f}" 2>/dev/null; then
        echo "::error::scope-extract cannot add new GML files: ${f}"
        exit 1
      fi
      git show "${BASE_SHA}:${f}" > "$SCRATCH/scope-source.gml"
      "$PY" "$TOOLS_DIR/scripts/extract_municipality.py" \
        --municipality "$SCOPE_MUNICIPALITY" \
        --input "$SCRATCH/scope-source.gml" \
        --output "$SCRATCH/scope-expected.gml"
      if ! cmp -s "$SCRATCH/scope-expected.gml" "$f"; then
        echo "::error::Re-running the extraction does not reproduce the GML in the commit: ${f}"
        exit 1
      fi
      "$PY" "$TOOLS_DIR/scripts/texture_check.py" --dangling "$f"
    done < changed_gml.txt
    echo "scope-extract reproducibility: OK"
  )
  rc=$?
  gate scope-reproducibility "$rc"
  set -e
else
  gate_na scope-reproducibility
fi

# --- Texture immutability (R1) + image content (magic bytes) check (continue-on-error) ---
# (IMAGES_CHANGED was computed with the classification step above.)
set +e
(
  set -euo pipefail
  # R1 (immutable): forbid overwriting an existing texture image under the same name (= modification M).
  # Texture changes are done by "adding a new image and updating imageURI" (overwriting a shared image propagates to other buildings).
  MODIMG=$(git diff --name-only --diff-filter=M "$BASE_SHA" "$HEAD_SHA" \
    | grep -E "$DATA_IMAGES_RE" || true)
  if [ -z "$MODIMG" ]; then
    echo "R1 OK: no existing texture overwritten"
  elif [ "$TEXTURE_OVERRIDE" = "true" ]; then
    echo "::notice::Existing textures are overwritten, but allowed as an approved exception via 'texture-override'. Files:${MODIMG}"
  else
    echo "::error::Existing texture images are overwritten (immutable violation). Change textures by adding new images and updating imageURI (overwriting existing images affects other buildings). A maintainer can allow legitimate shared/atlas changes with 'texture-override'. Files:${MODIMG}"
    exit 1
  fi
  # Magic bytes: added/renamed image files must actually be the image type their
  # extension claims (extension-only checks would let non-image content in).
  git diff --name-only --diff-filter=AR "$BASE_SHA" "$HEAD_SHA" \
    | grep -E "$DATA_IMAGES_RE" > "$SCRATCH/new_images.txt" || true
  if [ -s "$SCRATCH/new_images.txt" ]; then
    tr '\n' '\0' < "$SCRATCH/new_images.txt" \
      | xargs -0 "$PY" "$TOOLS_DIR/scripts/texture_check.py" --verify-images
  fi
)
rc=$?
gate texture "$rc"
set -e

# --- Validate changed .gml (well-formed + XSD) (continue-on-error) ---
if [ "$GML_COUNT" != "0" ]; then
  set +e
  # Invalid files are recorded in the outcome. The job continues and results are collected into the confirmation comment at the end.
  "$PY" "$TOOLS_DIR/scripts/validate_citygml.py" --file-list changed_gml.txt
  rc=$?
  gate schema "$rc"
  set -e
else
  gate_na schema
fi

# --- Quality gate (W6 minimal-diff + scope + texture R3/(a)) (continue-on-error) ---
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ]; then
  set +e
  (
    set -euo pipefail
    # Triage changes into 3 branches (division-of-responsibility principle: meaning = submitter / mechanical adjustments = system):
    #   A. machine-fixable (formatting churn)              -> not rejected, notice only (currently advisory; applying is manual).
    #   B. machine-detectable but not fixable (scope violation) -> CI points it out in a comment and works it out with the proposer.
    #   C. semantic judgment (validity of values/geometry/merge reasons) -> human review (not decided here).
    churn=""; manual=""; dangling=""
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      # R3 (no dangling): does every imageURI in head point to an existing image?
      set +e
      "$PY" "$TOOLS_DIR/scripts/texture_check.py" --dangling "$f" 1>/dev/null
      [ "$?" != "0" ] && dangling="${dangling} ${f}"
      set -e
      git cat-file -e "${BASE_SHA}:${f}" 2>/dev/null || continue   # a new file has no minimal diff to check
      git show "${BASE_SHA}:${f}" > "$SCRATCH/w6_base.gml"
      set +e
      # the minimal-diff check only: its per-file building counts are not used (the scope comes below)
      "$PY" "$TOOLS_DIR/scripts/reconstruct_minimal.py" "$SCRATCH/w6_base.gml" "$f" --check > /dev/null
      rc=$?
      set -e
      if [ "$rc" = "1" ]; then churn="${churn} ${f}";
      elif [ "$rc" = "2" ]; then manual="${manual} ${f}";
      elif [ "$rc" != "0" ]; then
        echo "::error::The minimal-diff check could not process ${f} (exit ${rc}); see the log above."
        exit "$rc"
      fi
    done < changed_gml.txt

    # Building scope: counted once, by the commit scope gate (commit_building_scope.pr_scope):
    # stable IDs under the city's rule, compared by meaning over the whole PR, texture changes and
    # deleted files included, a content-identical id change as a rename (S1; it replaced a second
    # count here by gml:id, D14/D17/D20/D21). Without that result the step fails, not counts nothing.
    scope_py='import json, sys
d = json.load(open(sys.argv[1]))
s = d.get("scope")
if not s:
    print("building scope unavailable: " + str(d.get("scopeError", "no result")), file=sys.stderr)
    sys.exit(1 if d.get("scopeMalformed") else 2)
if sys.argv[2] == "counts":
    print(len(s["modified"]), len(s["added"]), len(s["deleted"]), len(s["renamed"]), s["class"])
else:
    print("\n".join(s["gmlIds"][sys.argv[2]]))'
    SCOPE_JSON="${RUNNER_TEMP:-/tmp}/citygml_commit_scope.json"
    counts="$("$PY" -c "$scope_py" "$SCOPE_JSON" counts)" || {
      if [ "$?" = "1" ]; then
        echo "::error::A changed file is not well-formed XML, so its buildings cannot be read (see the CityGML format check)."
        exit 1
      fi
      echo "::error::The building scope of this PR could not be determined (see the commit scope step)."
      exit 2
    }
    read -r M A D R CLASS <<<"$counts"
    for kind in modified added deleted renamed; do
      "$PY" -c "$scope_py" "$SCOPE_JSON" "$kind" | grep . > "$SCRATCH/${kind}_ids" || true
    done
    echo "PR scope: modified=$M added=$A deleted=$D renamed=$R -> ${CLASS}"

    # rename (id-only change): content is unchanged, so a notice only (PR-D type, intent confirmation).
    if [ "$R" -gt 0 ]; then
      echo "::notice::${R} building(s) changed only in gml:id with identical content (rename). Confirm the id change is intentional (content-based detection, gml:id-independent)."
    fi

    # W7: generate the recommended message (building-ID trailer) for single-building or lifecycle commits.
    # For a PR bundling multiple single-building commits, do not suggest one multi-building commit.
    # suggest_commit takes gml:ids and resolves them to the stable ID through the changed files.
    if [ "$CLASS" != "multi-modified" ]; then
      "$PY" "$TOOLS_DIR/scripts/suggest_commit.py" --classification "$CLASS" \
        --modified "$SCRATCH/modified_ids" --added "$SCRATCH/added_ids" --deleted "$SCRATCH/deleted_ids" --renamed "$SCRATCH/renamed_ids" \
        --sources changed_gml.txt --base-sha "$BASE_SHA" > out/commit.md || true
    fi

    # R3: dangling references are machine-rejected (e.g. a deletion broke an unchanged building).
    if [ -n "$dangling" ]; then
      echo "::error::imageURI points to images that do not exist (dangling). Check for references to deleted images. Files:${dangling}"
      exit 1
    fi
    # C: files that cannot self-verify go to human review (the system does not guess).
    if [ -n "$manual" ]; then
      echo "::notice::Files whose change pattern cannot be diff-minimized (manual review recommended):${manual}"
    fi
    # A: churn is not rejected. Currently notified as advisory; auto-applying will be implemented later.
    if [ -n "$churn" ]; then
      echo "::notice::Formatting-only diff (churn) detected. Current CI only notifies; it neither auto-applies nor blocks. Apply the minimal-diff version manually with reconstruct_minimal.py. Files:${churn}"
    fi
    if [ "$CLASS" = "multi-modified" ] && [ "${RESET_ONLY:-false}" = "true" ]; then
      echo "::notice::A practice reset returns ${M} buildings to the baseline; the commit scope check verified it against Reset-To."
    elif [ "$CLASS" = "multi-modified" ] && [ "${BULK_ENABLED:-false}" != "true" ]; then
      echo "::error::An ordinary PR changes ${M} buildings. Submit one PR per building, or a supported reproducible bulk submission with a provenance manifest."
      exit 1
    fi
    # C: lifecycle changes require a stated reason and human review (the reason-field mechanism will be settled in the pilot).
    if [ "$CLASS" = "lifecycle" ]; then
      # Commit-scope already requires an explicit event for multiple additions/deletions.
      echo "::notice::CI checks the declared old/new IDs and event record. The city must judge the real-world relationship and supporting evidence."
    fi
  )
  QUALITY_RC=$?
  set -e
else
  QUALITY_RC=0
fi
# file-scope folds repository scope and the quality step (A11): an error of either is an error,
# a finding of either is a finding
if [ "$REPO_SCOPE_RC" -ge 2 ] || [ "$QUALITY_RC" -ge 2 ]; then gate file-scope 2
elif [ "$REPO_SCOPE_RC" -eq 1 ] || [ "$QUALITY_RC" -eq 1 ]; then gate file-scope 1
else gate file-scope 0; fi

# --- Generate Cesium preview comment body (continue-on-error) ---
PREVIEW_URL=""
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ] && [ "$BASELINE_ENABLED" != "true" ] && [ "$RESET_ENABLED" != "true" ] && [ "$BULK_ENABLED" != "true" ]; then
  set +e
  (
    set -euo pipefail
    # An exception exit fails CI (no || true). No target (e.g. no geometry diff) is normal, with an empty URL.
    "$PY" "$TOOLS_DIR/scripts/extract_building_preview.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list changed_gml.txt \
      --base-url "${PREVIEW_BASE_URL:-}" > preview_url.txt
    # The link is posted only where a viewer is served (PREVIEW_BASE_URL); the model gate runs either way.
    [ -n "${PREVIEW_BASE_URL:-}" ] || : > preview_url.txt
    URL="$(cat preview_url.txt)"
    if [ -n "$URL" ]; then
      # The posting side just upserts this .md verbatim (the marker goes on the first line).
      {
        echo "<!-- cesium-building-preview -->"
        echo "## 🏙️ Building preview (Cesium 3D)"
        echo ""
        echo "Shows the changed buildings in 3D: before the update (🔴 red) and after (🔵 blue)."
        echo ""
        echo "**[▶ Open in the Cesium viewer](${URL})**"
        echo ""
        echo "<sub>The building data is embedded in the URL and decoded only in your browser (the fragment is never sent to a server). LOD0/1/2 and textures can be toggled at the top of the page.</sub>"
      } > out/preview.md
    else
      echo "Preview: nothing to generate (attribute-only change, no geometry diff, etc. Analysis is fine)"
    fi
  )
  rc=$?
  gate model "$rc"
  [ "$rc" -ne 0 ] || PREVIEW_URL="$(cat preview_url.txt 2>/dev/null || true)"
  set -e
else
  gate_na model
fi

# --- Generate change summary (W2) (continue-on-error, untracked) ---
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ] && [ "$BULK_ENABLED" != "true" ]; then
  set +e
  (
    set -euo pipefail
    "$PY" "$TOOLS_DIR/scripts/ci_change_summary.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list changed_gml.txt \
      --preview-url "$PREVIEW_URL" \
      > out/summary.md
  )
  set -e
fi

# --- Generate reviewability lint (W3) (continue-on-error) ---
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ] && [ "$BASELINE_ENABLED" != "true" ] && [ "$RESET_ENABLED" != "true" ] && [ "$BULK_ENABLED" != "true" ]; then
  set +e
  (
    set -euo pipefail
    set +e
    "$PY" "$TOOLS_DIR/scripts/reviewability_lint.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list changed_gml.txt \
      > out/lint.md
    rc=$?
    set -e
    gate minimal-diff "$rc"
    [ "$rc" -le 1 ] || exit "$rc"     # warnings (1) are reported in the comment; an error fails
  )
  set -e
else
  gate_na minimal-diff
fi

# --- Generate PR metadata (W4) (continue-on-error, untracked) ---
if [ "$GML_COUNT" != "0" ]; then
  set +e
  (
    set -euo pipefail
    "$PY" "$TOOLS_DIR/scripts/pr_comment_metadata.py" \
      --pr "$PR_NUMBER" \
      --branch "$PR_BRANCH" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list changed_gml.txt \
      --preview-url "$PREVIEW_URL" \
      > out/metadata.md
  )
  set -e
fi

# Data-quality lint is split into two layers (#13). Both apply only to the changed "buildings" (pre-existing defects must not fail unrelated PRs).
#   citygml_lint (generic, data-agnostic): structural geometry breakage = never appears in correct data = CI points it out in a comment.
#   plausibility_lint (convention layer): implausible values (negative height, above the cap) = may already exist in base = warning = comment only.
#                          PLATEAU's unknown-value sentinels (±9999) are legitimate "unknown" and excluded from checks (sentinels.py).
# --- CityGML quality lint (structural inspection) (continue-on-error) ---
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ]; then
  set +e
  (
    set -uo pipefail
    "$PY" "$TOOLS_DIR/scripts/citygml_lint.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list changed_gml.txt --counts-json "${RUNNER_TEMP:-/tmp}/citygml_lint_counts.json" > out/citygml_lint.md
    rc=$?
    gate structure "$rc" --counts-json "${RUNNER_TEMP:-/tmp}/citygml_lint_counts.json"
    if grep -q "No warnings." out/citygml_lint.md 2>/dev/null; then rm -f out/citygml_lint.md; fi
    if [ "$rc" != "0" ]; then
      echo "::error::CityGML geometric structure defects detected (inconsistencies that never appear in correct data). See the PR comment '🧪 CityGML data quality check' for details."
      exit 1
    fi
  )
  set -e
else
  gate_na structure
fi

# --- PLATEAU quality lint (plausibility, advisory) ---
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ]; then
  set +e
  (
    set -uo pipefail
    # The convention layer is warning-only (non-blocking). Comment when there are findings, stay silent otherwise.
    "$PY" "$TOOLS_DIR/scripts/plausibility_lint.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list changed_gml.txt --counts-json "${RUNNER_TEMP:-/tmp}/plausibility_lint_counts.json" > out/plausibility_lint.md
    gate plausibility "$?" --counts-json "${RUNNER_TEMP:-/tmp}/plausibility_lint_counts.json"
    if grep -q "No warnings." out/plausibility_lint.md 2>/dev/null; then rm -f out/plausibility_lint.md; fi
  )
  set -e
else
  gate_na plausibility
fi

echo "Analysis main driver complete. Outcomes:"
cat "$OUTCOMES"
