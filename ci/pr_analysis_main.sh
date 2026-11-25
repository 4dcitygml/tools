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
#     $GITHUB_OUTPUT, and every row's result to $CITYGML_RUN/gates/<key>.json (the only state)
#   - comment bodies are collected under out/ (uploaded by the wrapper as the
#     "pr-comments" artifact; posted by the trusted pr-comment.yml)
#
# Semantics are ported 1:1 from the original workflow steps: each check section
# is isolated (failures are recorded in its row, analysis continues) and the
# final verdict is assembled by ci/inspection_summary.sh. This script itself
# exits non-zero only on infrastructure errors (same as the original workflow,
# where only non-continue-on-error steps could fail the job).

set -euo pipefail

: "${TOOLS_DIR:?TOOLS_DIR must point at the 4dcitygml/tools checkout}"
export PYTHONPATH="${TOOLS_DIR}${PYTHONPATH:+:$PYTHONPATH}"
PY="$(command -v python || command -v python3)"   # setup-python provides `python` on runners; python3 is the local fallback

# U3: one folder holds this run's own files, made afresh as a whole (nothing of an earlier run counts,
# A12; nothing lands in the city checkout, D-a8); the later steps of the job get it as CITYGML_RUN.
RUN="${RUNNER_TEMP:-/tmp}/citygml"
rm -rf "$RUN"; mkdir -p "$RUN/scratch"
export CITYGML_RUN="$RUN"
[ -n "${GITHUB_ENV:-}" ] && echo "CITYGML_RUN=$RUN" >> "$GITHUB_ENV"

# D23: one live read of the PR feeds every gate and the report's context (scripts/pr_event.py);
# the later steps of the job read the same file (CITYGML_EVENT).
EVENT="$("$PY" "$TOOLS_DIR/scripts/pr_event.py" "${GITHUB_EVENT_PATH:?}")"
[ -n "${GITHUB_ENV:-}" ] && echo "CITYGML_EVENT=$EVENT" >> "$GITHUB_ENV"
PR_NUMBER="$(jq -r '.pull_request.number' "$EVENT")"
BASE_SHA="$(jq -r '.pull_request.base.sha' "$EVENT")"
HEAD_SHA="$(jq -r '.pull_request.head.sha' "$EVENT")"
PR_TITLE="$(jq -r '.pull_request.title // ""' "$EVENT")"
PR_BRANCH="$(jq -r '.pull_request.head.ref // ""' "$EVENT")"
TEXTURE_OVERRIDE="$(jq -r '[.pull_request.labels[]?.name] | index("texture-override") != null' "$EVENT")"
# identity-review label: tier-C identity links may be applied only under explicit human review (commit scope gate).
export CITYGML_IDENTITY_REVIEW="$(jq -r '[.pull_request.labels[]?.name] | index("identity-review") != null' "$EVENT")"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"
# The live base may be newer than the one the workflow fetched from the payload.
git cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null || git fetch --no-tags --quiet origin "$BASE_SHA" || true

SCRATCH="$RUN/scratch"
# gate KEY EXIT_CODE [extra gate_result args]: the row's result in its one form, $RUN/gates/KEY.json
# (S17; exit codes: 0 pass, 1 finding, >=2 error). A write that fails leaves no file, and the
# summary then shows the row as not run (it blocks), never as passed. The driver alone decides
# whether a row applies: every row gets a result or gate_na here (U2); the rows are the only state.
gate() {
  local key="$1" code="$2"; shift 2
  "$PY" "$TOOLS_DIR/scripts/gate_result.py" write --key "$key" --exit-code "$code" --out "$RUN" "$@" > /dev/null || true
}
gate_na() { "$PY" "$TOOLS_DIR/scripts/gate_result.py" write --key "$1" --not-applicable --out "$RUN" > /dev/null || true; }

# --- Prepare output dir + carry PR number (hard step) ---
# Pass the PR number to the posting side via the artifact (workflow_run.pull_requests is empty for forks).
mkdir -p out
# An empty comment file is never posted: whichever step stops (a step's subshell exits on its
# first error, before any cleanup of its own), the driver removes empty files when it ends.
trap 'find out -maxdepth 1 -name "*.md" -empty -delete 2>/dev/null || true' EXIT
echo "$PR_NUMBER" > out/pr.txt

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
    --json-output "$RUN/citygml_commit_scope.json" \
    > "$SCRATCH/commit-scope.txt" 2>&1
  rc=$?
  "$PY" "$TOOLS_DIR/scripts/md_report.py" log --marker COMMIT_SCOPE --rc "$rc" --log "$SCRATCH/commit-scope.txt" \
    --title "🧱 Building / lifecycle event scope check" --ok "All commits in this PR passed." \
    --fail "Some commits need attention. The automated checks will comment with details." > out/commit-scope.md
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
  jq -r --arg k "$1" '.[$k] // "" | if type == "boolean" then tostring elif type == "array" then .[] else . end' \
    "$RUN/citygml_commit_scope.json" 2>/dev/null || true
}
flag() { [ "$(fact "$1")" = "true" ] && echo true || echo false; }
# A PR made only of accepted practice-reset commits (the gate above verified each against its
# Reset-To) returns many buildings at once; the one-building rule below does not apply to it.
RESET_ONLY="$(flag practiceResetOnly)"

# --- Find changed .gml files (hard step) ---
git diff --name-only --diff-filter=AMR "$BASE_SHA" "$HEAD_SHA" > "$RUN/all_changed.txt"
grep -E '\.gml$' "$RUN/all_changed.txt" > "$RUN/changed_gml.txt" || true
GML_COUNT="$(wc -l < "$RUN/changed_gml.txt" | tr -d ' ')"

# Texture images are the image files inside the city's data directories (4dcitygml.json
# data_dirs, read at the base); a city that declares none treats every image as data.
DATA_IMAGES_RE="$("$PY" - "$BASE_SHA" <<'PY'
import re, sys
from scripts import city_declaration
from scripts.texture_check import IMAGE_SUFFIXES
dirs = city_declaration.read(".", sys.argv[1]).data_dirs
prefix = "^(" + "|".join(re.escape(d) for d in dirs) + ")/.*" if dirs else ""
print(prefix + r"\.(" + "|".join(s.lstrip(".") for s in IMAGE_SUFFIXES) + ")$")
PY
)"

# Changed texture images make a PR a data PR (its classification is then required). The texture
# row runs on every PR and shows its own result (U2).
IMAGES_CHANGED="$(grep -qE "$DATA_IMAGES_RE" "$RUN/all_changed.txt" && echo true || echo false)"

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
if [ "$PR_CLASS" = "error" ]; then
  echo "::error::The classification could not be computed (scripts/pr_classification.py failed); see the log."
  gate classification 2
elif [ "$PR_CLASS" = "unclassified" ]; then
  if [ "${CITYGML_CLASSIFICATION_WARN_ONLY:-}" = "true" ]; then
    echo "::warning::This data proposal has no classification (branch prefix or title). It will be required; see the guidance comment."
    gate classification 0 --warnings 1
  else
    echo "::error::This data proposal has no classification. Rename the branch (edit/ tex/ geom/) or edit the title; see the guidance comment."
    gate classification 1
  fi
else
  echo "Classification: $PR_CLASS"
  gate classification 0
fi

# --- Repository scope (Exchange Contract A11) (continue-on-error) ---
# Cities distribute data, documents and configuration only; code belongs in
# 4dcitygml/tools. The CI tools pin is the declaration's `tools` key (verified against the
# tools-v tags); workflow files change only when a maintainer applied the `tooling` label.
# The result folds into the `file-scope` row of the inspection summary.
set +e
"$PY" "$TOOLS_DIR/scripts/repo_scope.py" --repo "$WORKSPACE" --base-sha "$BASE_SHA" --head-sha "$HEAD_SHA" \
  --event "$EVENT" --json-output "$RUN/citygml_repo_scope.json" > "$SCRATCH/repo-scope.txt" 2>&1
rc=$?
REPO_SCOPE_RC="$rc"
cat "$SCRATCH/repo-scope.txt"
set -e

# --- Determine topology inspection scope (hard step, only when .gml changed) ---
TOPOLOGY_RUN="false"
PROPOSAL_KIND=""
if [ "$GML_COUNT" != "0" ]; then
  # The class for check scoping comes from scripts/pr_classification.py (A5), computed above.
  kind="$CHECK_KIND"
  PROPOSAL_KIND="$kind"
  # the tool writes its step outputs (run, reason, building_ids) itself, once (S4, U3)
  TOPOLOGY_RUN="$("$PY" "$TOOLS_DIR/scripts/topology_scope.py" \
    --repo "$WORKSPACE" \
    --base-sha "$BASE_SHA" \
    --head-sha "$HEAD_SHA" \
    --kind "$kind" \
    --github-output "${GITHUB_OUTPUT:-/dev/null}" | sed -n 's/^run=//p')"
  [ -n "$TOPOLOGY_RUN" ] || TOPOLOGY_RUN="false"
else
  echo "run=false" >> "${GITHUB_OUTPUT:-/dev/null}"
fi
echo "kind=${PROPOSAL_KIND}" >> "${GITHUB_OUTPUT:-/dev/null}"
[ "$TOPOLOGY_RUN" = "true" ] || gate_na topology     # when it runs, ci/topology_gate.sh writes the row

# --- Detect municipality scope extraction (continue-on-error) ---
# A scope-extract PR is one whatever its trailers say; a municipality that is not unique fails
# its reproducibility row below (D-a4: before, the per-building gates ran instead and no row failed).
SCOPE_ENABLED="$(flag scopeExtract)"
SCOPE_MUNICIPALITY="$(fact scopeMunicipalities)"

# --- Detect source baseline (initial official-source recording) ---
# A source-baseline PR adds a whole official dataset (hundreds to thousands of
# buildings). Per-building reviewability warnings and the 3D preview are
# meaningless there and would only produce comments too large to post; the
# commit-scope gate already verifies the baseline semantics (no GML before it,
# no per-building trailers). Format, structure and plausibility checks still run.
BASELINE_ENABLED="$(flag sourceBaseline)"

# --- Detect a practice reset (a practice repository returning to its baseline) ---
# The commit-scope gate verifies the tree against the Reset-To commit; per-building
# reviewability and the 3D preview would only describe practice edits being undone.
RESET_ENABLED="$(flag practiceReset)"

# --- Detect bulk (manifest-backed) submissions: identity-baseline / identity-correction / source-update ---
# Accepted by reproduction (docs/bulk-submission-provenance.md): the commit scope
# gate has already checked every commit against the provenance manifest; here the
# manifest's materials are re-fetched and the manifest regenerated and compared.
IDENTITY_ENABLED="$(flag identity)"
BULK_ENABLED="$(flag bulk)"
# Which per-file and per-building rows apply, decided once (U2): the format/structure/plausibility
# rows need CityGML files outside a scope extraction; the per-building rows (minimal diff, 3D
# preview) also not for a whole-dataset PR (source baseline, practice reset, manifest-backed bulk).
PER_FILE=false; PER_BUILDING=false
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ]; then
  PER_FILE=true
  [ "$BASELINE_ENABLED" = "true" ] || [ "$RESET_ENABLED" = "true" ] || [ "$BULK_ENABLED" = "true" ] || PER_BUILDING=true
fi
if [ "$BULK_ENABLED" = "true" ]; then
  set +e
  (
    set -euo pipefail
    ref="$(fact manifestRef)"
    manifest="${ref%%@sha256:*}"
    [ -f "$manifest" ] || { echo "::error::Provenance manifest not found at PR head: $manifest"; exit 1; }
    kind="$(jq -r '.kind // ""' "$manifest")"
    # the tool that reproduces this kind: generic for the identity kinds, else the city's dialect pack's
    tool="$(PYTHONPATH="$TOOLS_DIR" "$PY" -m scripts --manifest-tool "$kind")" \
      || { echo "::error::No reproduction tool for manifest kind '$kind'"; exit 1; }
    if [ "$kind" = "semantic-correction" ]; then
      # Validate paths, material names and recipe before fetching any input.
      # The recipe must run at the trusted tooling commit that generated it.
      actual="$(git -C "$TOOLS_DIR" rev-parse HEAD)"
      "$PY" "$tool" check --manifest "$manifest" --ci --tools-commit "$actual"
    fi
    materials="$RUN/citygml-materials"
    rm -rf "$materials"; mkdir -p "$materials"
    "$PY" "$TOOLS_DIR/scripts/fetch_materials.py" --manifest "$manifest" --outdir "$materials"
    "$PY" "$tool" verify --manifest "$manifest" --materials-dir "$materials"
  ) > "$SCRATCH/bulk-reproduction.txt" 2>&1
  rc=$?
  set -e
  "$PY" "$TOOLS_DIR/scripts/md_report.py" log --marker BULK_REPRODUCTION --rc "$rc" --tail 4000 \
    --log "$SCRATCH/bulk-reproduction.txt" --title "🔁 Bulk conversion reproduction" \
    --ok "The provenance manifest regenerates identically from its declared materials." \
    --fail "The conversion could not be reproduced from the declared materials." > out/reproduction.md
  gate reproduction "$rc"
else
  gate_na reproduction
fi

# --- Scope extraction reproducibility (continue-on-error, scope-extract only) ---
if [ "$SCOPE_ENABLED" = "true" ] && [ "$GML_COUNT" != "0" ]; then
  set +e
  (
    set -euo pipefail
    if [ "$(printf '%s\n' "$SCOPE_MUNICIPALITY" | grep -c .)" != "1" ]; then
      echo "::error::Scope-Municipality for scope-extract cannot be determined uniquely."
      exit 1
    fi
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
    done < "$RUN/changed_gml.txt"
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
  # git first, on its own: a git failure stops the check (an error row), never reads as "OK" (D-a6)
  git diff --name-only --diff-filter=M "$BASE_SHA" "$HEAD_SHA" > "$SCRATCH/modified_files.txt"
  MODIMG=$(grep -E "$DATA_IMAGES_RE" "$SCRATCH/modified_files.txt" || true)
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
  git diff --name-only --diff-filter=AR "$BASE_SHA" "$HEAD_SHA" > "$SCRATCH/added_files.txt"
  grep -E "$DATA_IMAGES_RE" "$SCRATCH/added_files.txt" > "$SCRATCH/new_images.txt" || true
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
  "$PY" "$TOOLS_DIR/scripts/validate_citygml.py" --file-list "$RUN/changed_gml.txt"
  rc=$?
  gate schema "$rc"
  set -e
else
  gate_na schema
fi

# --- Quality gate (W6 minimal-diff + scope + texture R3/(a)) (continue-on-error) ---
if [ "$PER_FILE" = "true" ]; then
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
    done < "$RUN/changed_gml.txt"

    # Building scope: counted once, by the commit scope gate (commit_building_scope.pr_scope):
    # stable IDs under the city's rule, compared by meaning over the whole PR, texture changes and
    # deleted files included, a content-identical id change as a rename (S1; it replaced a second
    # count here by gml:id, D14/D17/D20/D21). Without that result the step fails, not counts nothing.
    SCOPE_JSON="$RUN/citygml_commit_scope.json"
    counts="$(jq -r '.scope // empty | "\(.modified | length) \(.added | length) \(.deleted | length) \(.renamed | length) \(.class)"' \
      "$SCOPE_JSON" 2>/dev/null || true)"
    if [ -z "$counts" ]; then
      echo "building scope unavailable: $(jq -r '.scopeError // "no result"' "$SCOPE_JSON" 2>/dev/null || echo "no result")" >&2
      if [ "$(jq -r '.scopeMalformed // false' "$SCOPE_JSON" 2>/dev/null)" = "true" ]; then
        echo "::error::A changed file is not well-formed XML, so its buildings cannot be read (see the CityGML format check)."
        exit 1
      fi
      echo "::error::The building scope of this PR could not be determined (see the commit scope step)."
      exit 2
    fi
    read -r M A D R CLASS <<<"$counts"
    echo "PR scope: modified=$M added=$A deleted=$D renamed=$R -> ${CLASS}"

    # rename (id-only change): content is unchanged, so a notice only (PR-D type, intent confirmation).
    if [ "$R" -gt 0 ]; then
      echo "::notice::${R} building(s) changed only in gml:id with identical content (rename). Confirm the id change is intentional (content-based detection, gml:id-independent)."
    fi

    # W7: generate the recommended message (building-ID trailer) for single-building or lifecycle commits.
    # For a PR bundling multiple single-building commits, do not suggest one multi-building commit.
    # suggest_commit names the stable IDs of the scope above, the ones this gate expects (U4).
    if [ "$CLASS" != "multi-modified" ]; then
      "$PY" "$TOOLS_DIR/scripts/suggest_commit.py" --scope-json "$SCOPE_JSON" > out/commit.md || true
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
if [ "$PER_BUILDING" = "true" ]; then
  set +e
  (
    set -euo pipefail
    # An exception exit fails CI (no || true). No target (e.g. no geometry diff) is normal, with an empty URL.
    "$PY" "$TOOLS_DIR/scripts/extract_building_preview.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list "$RUN/changed_gml.txt" \
      --base-url "${PREVIEW_BASE_URL:-}" --comment out/preview.md > "$RUN/preview_url.txt"
    # The link is posted only where a viewer is served (PREVIEW_BASE_URL); the model gate runs either way.
    [ -n "${PREVIEW_BASE_URL:-}" ] || : > "$RUN/preview_url.txt"
  )
  rc=$?
  gate model "$rc"
  [ "$rc" -ne 0 ] || PREVIEW_URL="$(cat "$RUN/preview_url.txt" 2>/dev/null || true)"
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
      --file-list "$RUN/changed_gml.txt" \
      --preview-url "$PREVIEW_URL" \
      > out/summary.md
  )
  set -e
fi

# --- Generate reviewability lint (W3) (continue-on-error) ---
if [ "$PER_BUILDING" = "true" ]; then
  set +e
  (
    set -euo pipefail
    set +e
    "$PY" "$TOOLS_DIR/scripts/reviewability_lint.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list "$RUN/changed_gml.txt" \
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
      --file-list "$RUN/changed_gml.txt" \
      --preview-url "$PREVIEW_URL" \
      > out/metadata.md
  )
  set -e
fi

# Data-quality lint is split into two layers (#13). Both apply only to the changed "buildings" (pre-existing defects must not fail unrelated PRs).
#   citygml_lint (generic, data-agnostic): structural geometry breakage = never appears in correct data = CI points it out in a comment.
#   plausibility_lint (convention layer): implausible values (negative height, above the cap) = may already exist in base = warning = comment only.
#                          A dialect pack's unknown-value sentinels are legitimate "unknown" and excluded from checks (Pack.unknown_value).
# --- CityGML quality lint (structural inspection) (continue-on-error) ---
if [ "$PER_FILE" = "true" ]; then
  set +e
  (
    set -uo pipefail
    "$PY" "$TOOLS_DIR/scripts/citygml_lint.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list "$RUN/changed_gml.txt" --counts-json "$RUN/citygml_lint_counts.json" > out/citygml_lint.md
    rc=$?
    gate structure "$rc" --counts-json "$RUN/citygml_lint_counts.json"
    if [ "$rc" = "1" ]; then
      echo "::error::CityGML geometric structure defects detected (inconsistencies that never appear in correct data). See the PR comment '🧪 CityGML data quality check' for details."
      exit 1
    elif [ "$rc" != "0" ]; then   # D-a3: a crash (exit >= 2) is no finding in the data
      echo "::error::The structure check could not run (exit $rc); this is not a problem in the data, a re-run may help."
      exit 1
    fi
  )
  set -e
else
  gate_na structure
fi

# --- Data-convention quality lint (plausibility, advisory; code-list, blocking) ---
# One run, two rows: exit 1 means an error, an invalid code this PR wrote (code-list, blocking); the
# warnings (implausible values, invalid codes base already had) are the plausibility row, which never blocks.
if [ "$PER_FILE" = "true" ]; then
  set +e
  (
    set -uo pipefail
    # Comment when there are findings, stay silent otherwise.
    "$PY" "$TOOLS_DIR/scripts/plausibility_lint.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list "$RUN/changed_gml.txt" --counts-json "$RUN/plausibility_lint_counts.json" > out/plausibility_lint.md
    rc=$?
    gate code-list "$rc"
    [ "$rc" = "1" ] && rc=0    # the errors are the code-list row's; this row shows the warnings
    gate plausibility "$rc" --counts-json "$RUN/plausibility_lint_counts.json"
  )
  set -e
else
  gate_na code-list
  gate_na plausibility
fi

echo "Analysis main driver complete. Rows:"
jq -r '"\(.key): \(.status)"' "$RUN"/gates/*.json
