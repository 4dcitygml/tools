#!/usr/bin/env bash
# Copyright (c) 2026 4dcitygml
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
#   - PREVIEW_BASE_URL = optional override for the Cesium preview base URL
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
record() { printf '%s=%s\n' "$1" "$2" >> "$OUTCOMES"; }

# --- Prepare output dir + carry PR number (hard step) ---
# Pass the PR number to the posting side via the artifact (workflow_run.pull_requests is empty for forks).
mkdir -p out
echo "$PR_NUMBER" > out/pr.txt

# --- Base branch freshness (continue-on-error) ---
set +e
(
  set -euo pipefail
  if git merge-base --is-ancestor "$BASE_SHA" "$HEAD_SHA"; then
    echo "Consistency with the latest version: OK"
  else
    echo "::warning::Another change was applied first. Merge in the latest version and resubmit."
    exit 1
  fi
)
[ $? -eq 0 ] && record FRESHNESS_OUTCOME success || record FRESHNESS_OUTCOME failure
set -e

# --- Required explanation and evidence (continue-on-error) ---
set +e
"$PY" - "$EVENT" <<'PY'
import json
import re
import sys

event = json.load(open(sys.argv[1], encoding="utf-8"))
body = str(event.get("pull_request", {}).get("body") or "")
# Exchange format v2: prefer the <!--sec:reason--> anchor, fall back to heading strings
match = re.search(
    r"^##[^\n]*<!--\s*sec:reason\s*-->[^\n]*$\n(.*?)(?=^##\s+|\Z)",
    body, flags=re.MULTILINE | re.DOTALL,
) or re.search(
    r"^##\s+(?:Reason and supporting evidence|Summary of changes"
    r"|編集理由・根拠資料|変更理由|変更の理由|変更の概要)\s*$\n(.*?)(?=^##\s+|\Z)",  # Japanese literals: match contributor input — do not translate
    body, flags=re.MULTILINE | re.DOTALL,
)
reason = match.group(1).strip() if match else ""
reason = re.sub(r"<!--.*?-->", "", reason, flags=re.DOTALL).strip()
placeholders = ("please fill in", "not filled in", "記入してください", "未記入", "TODO", "TBD")  # Japanese literals: match contributor input — do not translate
ok = len(reason) >= 5 and not any(p.lower() in reason.lower() for p in placeholders)
if ok:
    print("Description and evidence: OK")
else:
    print("::warning::Please describe the reason for the change and the supporting evidence. CI will comment with items to confirm.")
    raise SystemExit(1)
PY
[ $? -eq 0 ] && record REASON_OUTCOME success || record REASON_OUTCOME failure
set -e

# --- Commit scope inspection (1 commit = 1 buildingID) (continue-on-error) ---
set +e
(
  set -uo pipefail
  # Failures are recorded in the outcome, but later checks continue and everything is collected into a polite comment at the end.
  "$PY" "$TOOLS_DIR/scripts/commit_building_scope.py" \
    --repo "$WORKSPACE" \
    --base-sha "$BASE_SHA" \
    --head-sha "$HEAD_SHA" \
    > /tmp/commit-scope.txt 2>&1
  rc=$?
  {
    echo "<!-- citygml-commit-scope -->"
    echo "## 🧱 1 commit = 1 buildingID check"
    echo ""
    if [ "$rc" = "0" ]; then
      echo "✅ All commits in this PR passed."
    else
      echo "❌ Some commits need attention. The automated checks will comment with details."
    fi
    echo ""
    echo '```text'
    cat /tmp/commit-scope.txt
    echo '```'
  } > out/commit-scope.md
  cat /tmp/commit-scope.txt
  exit "$rc"
)
[ $? -eq 0 ] && record COMMIT_SCOPE_OUTCOME success || record COMMIT_SCOPE_OUTCOME failure
set -e

# --- Find changed .gml files (hard step) ---
git diff --name-only --diff-filter=AMR "$BASE_SHA" "$HEAD_SHA" > all_changed.txt
grep -E '\.gml$' all_changed.txt > changed_gml.txt || true
GML_COUNT="$(wc -l < changed_gml.txt | tr -d ' ')"
record GML_COUNT "$GML_COUNT"
record CHANGED_OUTCOME success

# --- Determine topology inspection scope (hard step, only when .gml changed) ---
TOPOLOGY_RUN="false"
PROPOSAL_KIND=""
if [ "$GML_COUNT" != "0" ]; then
  kind="attribute"
  if [[ "$PR_BRANCH" == tex/* || "$PR_TITLE" == "Update textures"* || "$PR_TITLE" == "Add textures"* || "$PR_TITLE" == テクスチャ* || "$PR_TITLE" == Textur* ]]; then  # ja/de literals: match generated repo-language titles and contributor input — do not translate
    kind="texture"
  elif [[ "$PR_TITLE" == *geometry* || "$PR_TITLE" == *"building shape"* || "$PR_TITLE" == *rebuild* || "$PR_TITLE" == *幾何* || "$PR_TITLE" == *建物形状* || "$PR_TITLE" == *建替* || "$PR_TITLE" == *建て替* || "$PR_BRANCH" == geom/* || "$PR_BRANCH" == geometry/* ]]; then  # Japanese literals: match contributor input — do not translate
    kind="geometry"
  fi
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
  cat "$SCOPE_OUT" >> "${GITHUB_OUTPUT:-/dev/null}"
fi
echo "kind=${PROPOSAL_KIND}" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "run=${TOPOLOGY_RUN}" >> "${GITHUB_OUTPUT:-/dev/null}"
record TOPOLOGY_APPLICABLE "$TOPOLOGY_RUN"
record PROPOSAL_KIND "$PROPOSAL_KIND"

# --- Detect municipality scope extraction (continue-on-error) ---
SCOPE_ENABLED="false"
SCOPE_MUNICIPALITY=""
set +e
(
  set -euo pipefail
  git log --format=%B "${BASE_SHA}..${HEAD_SHA}" > /tmp/pr-messages.txt
  if grep -q '^Change-Type: scope-extract$' /tmp/pr-messages.txt; then
    grep '^Scope-Municipality:' /tmp/pr-messages.txt \
      | sed 's/^Scope-Municipality:[[:space:]]*//' \
      | sort -u > /tmp/scope-municipalities.txt
    if [ "$(grep -c . /tmp/scope-municipalities.txt || true)" != "1" ]; then
      echo "::error::Scope-Municipality for scope-extract cannot be determined uniquely."
      exit 1
    fi
    echo "enabled" > /tmp/scope-extract-enabled
  else
    rm -f /tmp/scope-extract-enabled
  fi
)
scope_rc=$?
set -e
if [ "$scope_rc" -eq 0 ] && [ -f /tmp/scope-extract-enabled ]; then
  SCOPE_ENABLED="true"
  SCOPE_MUNICIPALITY="$(cat /tmp/scope-municipalities.txt)"
fi
record SCOPE_EXTRACT "$SCOPE_ENABLED"

# --- Detect source baseline (initial official-source recording) ---
# A source-baseline PR adds a whole official dataset (hundreds to thousands of
# buildings). Per-building reviewability warnings and the 3D preview are
# meaningless there and would only produce comments too large to post; the
# commit-scope gate already verifies the baseline semantics (no GML before it,
# no per-building trailers). Format, structure and plausibility checks still run.
BASELINE_ENABLED="false"
if grep -q '^Change-Type: source-baseline$' /tmp/pr-messages.txt 2>/dev/null; then
  BASELINE_ENABLED="true"
fi
record SOURCE_BASELINE "$BASELINE_ENABLED"

# --- Detect bulk (manifest-backed) submissions: identity-baseline / identity-correction / source-update ---
# Accepted by reproduction (docs/bulk-submission-provenance.md): the commit scope
# gate has already checked every commit against the provenance manifest; here the
# manifest's materials are re-fetched and the manifest regenerated and compared.
IDENTITY_ENABLED="false"
if grep -qE '^Change-Type: identity-(baseline|correction)$' /tmp/pr-messages.txt 2>/dev/null; then
  IDENTITY_ENABLED="true"
fi
record IDENTITY_KIND "$IDENTITY_ENABLED"
BULK_ENABLED="false"
if grep -q '^Provenance-Manifest:' /tmp/pr-messages.txt 2>/dev/null; then
  BULK_ENABLED="true"
fi
record BULK_KIND "$BULK_ENABLED"
if [ "$BULK_ENABLED" = "true" ]; then
  set +e
  (
    set -euo pipefail
    ref="$(grep -m1 '^Provenance-Manifest:' /tmp/pr-messages.txt | sed 's/^Provenance-Manifest:[[:space:]]*//')"
    manifest="${ref%%@sha256:*}"
    [ -f "$manifest" ] || { echo "::error::Provenance manifest not found at PR head: $manifest"; exit 1; }
    kind="$(jq -r '.kind // ""' "$manifest")"
    case "$kind" in
      identity-baseline|identity-correction) tool="identity_manifest.py" ;;
      source-update) tool="source_update_manifest.py" ;;
      carry-forward) tool="carry_forward_manifest.py" ;;
      *) echo "::error::No reproduction tool for manifest kind '$kind'"; exit 1 ;;
    esac
    materials="${RUNNER_TEMP:-/tmp}/citygml-materials"
    rm -rf "$materials"; mkdir -p "$materials"
    "$PY" "$TOOLS_DIR/scripts/fetch_materials.py" --manifest "$manifest" --outdir "$materials"
    "$PY" "$TOOLS_DIR/scripts/$tool" verify --manifest "$manifest" --materials-dir "$materials"
  ) > /tmp/bulk-reproduction.txt 2>&1
  rc=$?
  set -e
  {
    echo "<!-- citygml-bulk-reproduction -->"
    echo "## 🔁 Bulk conversion reproduction"
    echo ""
    if [ "$rc" = "0" ]; then echo "✅ The provenance manifest regenerates identically from its declared materials."; else echo "❌ The conversion could not be reproduced from the declared materials."; fi
    echo ""
    echo '```text'
    tail -c 4000 /tmp/bulk-reproduction.txt
    echo '```'
  } > out/reproduction.md
  [ "$rc" -eq 0 ] && record REPRODUCTION_OUTCOME success || record REPRODUCTION_OUTCOME failure
else
  record REPRODUCTION_OUTCOME skipped
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
      git show "${BASE_SHA}:${f}" > /tmp/scope-source.gml
      "$PY" "$TOOLS_DIR/scripts/extract_municipality.py" \
        --municipality "$SCOPE_MUNICIPALITY" \
        --input /tmp/scope-source.gml \
        --output /tmp/scope-expected.gml
      if ! cmp -s /tmp/scope-expected.gml "$f"; then
        echo "::error::Re-running the extraction does not reproduce the GML in the commit: ${f}"
        exit 1
      fi
      "$PY" "$TOOLS_DIR/scripts/texture_check.py" --dangling "$f"
    done < changed_gml.txt
    echo "scope-extract reproducibility: OK"
  )
  [ $? -eq 0 ] && record SCOPE_REPRODUCIBILITY_OUTCOME success || record SCOPE_REPRODUCIBILITY_OUTCOME failure
  set -e
else
  record SCOPE_REPRODUCIBILITY_OUTCOME skipped
fi

# --- Texture immutability (R1) + image content (magic bytes) check (continue-on-error) ---
# IMAGES_CHANGED makes the summary's texture row applicable whenever image files
# are touched, even if the PR title/branch classifies as an attribute change
# (otherwise a bogus "image" in an attribute-titled PR would surface in no row).
IMAGES_CHANGED="$(git diff --name-only --diff-filter=AMR "$BASE_SHA" "$HEAD_SHA" \
  | grep -qE '_appearance/.*\.(jpg|jpeg|png|tif|tiff)$' && echo true || echo false)"
record IMAGES_CHANGED "$IMAGES_CHANGED"
set +e
(
  set -euo pipefail
  # R1 (immutable): forbid overwriting an existing texture image under the same name (= modification M).
  # Texture changes are done by "adding a new image and updating imageURI" (overwriting a shared image propagates to other buildings).
  MODIMG=$(git diff --name-only --diff-filter=M "$BASE_SHA" "$HEAD_SHA" \
    | grep -E '_appearance/.*\.(jpg|jpeg|png|tif|tiff)$' || true)
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
    | grep -E '_appearance/.*\.(jpg|jpeg|png|tif|tiff)$' > /tmp/new_images.txt || true
  if [ -s /tmp/new_images.txt ]; then
    tr '\n' '\0' < /tmp/new_images.txt \
      | xargs -0 "$PY" "$TOOLS_DIR/scripts/texture_check.py" --verify-images
  fi
)
[ $? -eq 0 ] && record TEXTURE_OUTCOME success || record TEXTURE_OUTCOME failure
set -e

# --- Validate changed .gml (well-formed + XSD) (continue-on-error) ---
if [ "$GML_COUNT" != "0" ]; then
  set +e
  # Invalid files are recorded in the outcome. The job continues and results are collected into the confirmation comment at the end.
  "$PY" "$TOOLS_DIR/scripts/validate_citygml.py" --file-list changed_gml.txt
  [ $? -eq 0 ] && record FORMAT_OUTCOME success || record FORMAT_OUTCOME failure
  set -e
else
  record FORMAT_OUTCOME skipped
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
    : > /tmp/mod_ids; : > /tmp/add_ids; : > /tmp/del_ids; : > /tmp/ren_ids
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      # R3 (no dangling): does every imageURI in head point to an existing image?
      set +e
      "$PY" "$TOOLS_DIR/scripts/texture_check.py" --dangling "$f" 1>/dev/null
      [ "$?" != "0" ] && dangling="${dangling} ${f}"
      set -e
      if ! git cat-file -e "${BASE_SHA}:${f}" 2>/dev/null; then
        echo "NEWFILE:${f}" >> /tmp/add_ids; continue  # new .gml file = addition (lifecycle)
      fi
      git show "${BASE_SHA}:${f}" > /tmp/w6_base.gml
      set +e
      out=$("$PY" "$TOOLS_DIR/scripts/reconstruct_minimal.py" /tmp/w6_base.gml "$f" --check)
      rc=$?
      set -e
      printf '%s\n' "$out" | awk '/^BLDG modified/{print $3}' >> /tmp/mod_ids
      printf '%s\n' "$out" | awk '/^BLDG added/{print $3}' >> /tmp/add_ids
      printf '%s\n' "$out" | awk '/^BLDG deleted/{print $3}' >> /tmp/del_ids
      printf '%s\n' "$out" | awk '/^BLDG renamed/{print $2}' >> /tmp/ren_ids  # $2=new_id (surviving)
      # (a): buildings whose appearance changed (re-texturing etc.) also count as "modified buildings".
      "$PY" "$TOOLS_DIR/scripts/texture_check.py" --changed-buildings /tmp/w6_base.gml "$f" >> /tmp/mod_ids || true
      if [ "$rc" = "1" ]; then churn="${churn} ${f}";
      elif [ "$rc" = "2" ]; then manual="${manual} ${f}";
      elif [ "$rc" != "0" ]; then exit "$rc"; fi
    done < changed_gml.txt

    # Scope: modified buildings = (W1 modified ∪ appearance changes) − added − deleted − renamed (union of IDs).
    # A rename (identical content, only the id changed) is effectively no change, so it is excluded from M/A/D.
    sort -u /tmp/add_ids > /tmp/A_ids
    sort -u /tmp/del_ids > /tmp/D_ids
    sort -u /tmp/ren_ids > /tmp/R_ids
    sort -u /tmp/mod_ids | comm -23 - /tmp/A_ids | comm -23 - /tmp/D_ids | comm -23 - /tmp/R_ids > /tmp/M_ids
    M=$(grep -c . /tmp/M_ids || true); A=$(grep -c . /tmp/A_ids || true)
    D=$(grep -c . /tmp/D_ids || true); R=$(grep -c . /tmp/R_ids || true)
    CLASS=$("$PY" -c "from scripts.reconstruct_minimal import classify; print(classify($M,$A,$D,$R))")
    echo "PR scope: modified=$M added=$A deleted=$D renamed=$R -> ${CLASS}"

    # rename (id-only change): content is unchanged, so a notice only (PR-D type, intent confirmation).
    if [ "$R" -gt 0 ]; then
      echo "::notice::${R} building(s) changed only in gml:id with identical content (rename). Confirm the id change is intentional (content-based detection, gml:id-independent)."
    fi

    # W7: generate the recommended message (building-ID trailer) for single-building or lifecycle commits.
    # For a PR bundling multiple single-building commits, do not suggest one multi-building commit.
    # NEWFILE markers for new files are not building IDs, so exclude them.
    grep -v '^NEWFILE:' /tmp/A_ids > /tmp/A_clean 2>/dev/null || true
    # Keys resolve to the stable ID (uro:buildingID) first (pass the changed .gml files via --sources). Renames are included.
    if [ "$CLASS" != "multi-modified" ]; then
      "$PY" "$TOOLS_DIR/scripts/suggest_commit.py" --classification "$CLASS" \
        --modified /tmp/M_ids --added /tmp/A_clean --deleted /tmp/D_ids --renamed /tmp/R_ids \
        --sources changed_gml.txt > out/commit.md || true
    fi
    [ -s out/commit.md ] || rm -f out/commit.md

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
    # Even if the PR as a whole touches multiple buildings, it is allowed when each commit is single-building (already verified by the commit scope gate).
    if [ "$CLASS" = "multi-modified" ]; then
      echo "::notice::The PR as a whole changes multiple buildings (${M}). The one-buildingID-per-commit constraint is verified by the commit scope gate."
    fi
    # C: lifecycle changes require a stated reason and human review (the reason-field mechanism will be settled in the pilot).
    if [ "$CLASS" = "lifecycle" ]; then
      echo "::notice::Lifecycle change (merge/split/rebuild etc.). Stating the reason and human review are required (the reason-field mechanism will be settled in the pilot)."
    fi
  )
  [ $? -eq 0 ] && record QUALITY_OUTCOME success || record QUALITY_OUTCOME failure
  set -e
else
  record QUALITY_OUTCOME skipped
fi

# --- Generate Cesium preview comment body (continue-on-error) ---
PREVIEW_URL=""
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ] && [ "$BASELINE_ENABLED" != "true" ] && [ "$BULK_ENABLED" != "true" ]; then
  set +e
  (
    set -euo pipefail
    BASE_URL="${PREVIEW_BASE_URL:-}"
    if [ -z "$BASE_URL" ]; then
      BASE_URL="https://${GITHUB_REPOSITORY%%/*}.github.io/${GITHUB_REPOSITORY##*/}"
    fi
    # An exception exit fails CI (no || true). No target (e.g. no geometry diff) is normal, with an empty URL.
    "$PY" "$TOOLS_DIR/scripts/extract_building_preview.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list changed_gml.txt \
      --base-url "$BASE_URL" > preview_url.txt
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
  if [ $? -eq 0 ]; then
    record PREVIEW_OUTCOME success
    PREVIEW_URL="$(cat preview_url.txt 2>/dev/null || true)"
  else
    record PREVIEW_OUTCOME failure
  fi
  set -e
else
  record PREVIEW_OUTCOME skipped
fi
record PREVIEW_URL "$PREVIEW_URL"

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
    [ -s out/summary.md ] || rm -f out/summary.md  # if empty, exclude it from posting
  )
  set -e
fi

# --- Generate reviewability lint (W3) (continue-on-error) ---
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ] && [ "$BASELINE_ENABLED" != "true" ] && [ "$BULK_ENABLED" != "true" ]; then
  set +e
  (
    set -euo pipefail
    "$PY" "$TOOLS_DIR/scripts/reviewability_lint.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list changed_gml.txt \
      > out/lint.md
    [ -s out/lint.md ] || rm -f out/lint.md
  )
  [ $? -eq 0 ] && record REVIEWABILITY_OUTCOME success || record REVIEWABILITY_OUTCOME failure
  set -e
else
  record REVIEWABILITY_OUTCOME skipped
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
    [ -s out/metadata.md ] || rm -f out/metadata.md
  )
  set -e
fi

# Data-quality lint is split into two layers (#13). Both apply only to the changed "buildings" (pre-existing defects must not fail unrelated PRs).
#   citygml_lint (generic, data-agnostic): structural geometry breakage = never appears in correct data = CI points it out in a comment.
#   plateau_lint (convention layer): implausible values (negative height, above the cap) = may already exist in base = warning = comment only.
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
      --file-list changed_gml.txt > out/citygml_lint.md
    rc=$?
    if grep -q "No warnings." out/citygml_lint.md 2>/dev/null; then rm -f out/citygml_lint.md; fi
    [ -s out/citygml_lint.md ] || rm -f out/citygml_lint.md
    if [ "$rc" != "0" ]; then
      echo "::error::CityGML geometric structure defects detected (inconsistencies that never appear in correct data). See the PR comment '🧪 CityGML data quality check' for details."
      exit 1
    fi
  )
  [ $? -eq 0 ] && record STRUCTURE_OUTCOME success || record STRUCTURE_OUTCOME failure
  set -e
else
  record STRUCTURE_OUTCOME skipped
fi

# --- PLATEAU quality lint (plausibility, advisory) ---
if [ "$GML_COUNT" != "0" ] && [ "$SCOPE_ENABLED" != "true" ]; then
  set +e
  (
    set -uo pipefail
    # The convention layer is warning-only (non-blocking). Comment when there are findings, stay silent otherwise.
    "$PY" "$TOOLS_DIR/scripts/plateau_lint.py" \
      --repo "$WORKSPACE" \
      --base-sha "$BASE_SHA" \
      --head-sha "$HEAD_SHA" \
      --file-list changed_gml.txt > out/plateau_lint.md || true
    if grep -q "No warnings." out/plateau_lint.md 2>/dev/null; then rm -f out/plateau_lint.md; fi
    [ -s out/plateau_lint.md ] || rm -f out/plateau_lint.md
  )
  [ $? -eq 0 ] && record PLATEAU_OUTCOME success || record PLATEAU_OUTCOME failure
  set -e
else
  record PLATEAU_OUTCOME skipped
fi

echo "Analysis main driver complete. Outcomes:"
cat "$OUTCOMES"
