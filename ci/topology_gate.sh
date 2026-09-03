#!/usr/bin/env bash
# Copyright (c) 2026 4dcitygml
#
# CityGML PR analysis driver (topology gate) — shared CI logic, plan C.
# Runs after the wrapper has set up the val3dity toolchain (Java + cached
# citygml-tools + val3dity build). The wrapper only calls this when the main
# driver decided run=true for the topology scope.
#
# Contract with the wrapper:
#   - cwd = city repository checkout, changed_gml.txt exists (written by main)
#   - TOOLS_DIR, CITYGML_TOOLS, VAL3DITY_CMD are set
#   - appends TOPOLOGY_OUTCOME to $RUNNER_TEMP/citygml_outcomes.env
#
# The gate is differential and advisory: it validates only the buildings the PR
# changed and warns only about invalids newly introduced by the PR. Missing
# tools auto-skip (advisory).

set -uo pipefail

: "${TOOLS_DIR:?TOOLS_DIR must point at the 4dcitygml/tools checkout}"
export PYTHONPATH="${TOOLS_DIR}${PYTHONPATH:+:$PYTHONPATH}"
PY="$(command -v python || command -v python3)"   # setup-python provides `python` on runners; python3 is the local fallback

EVENT="${GITHUB_EVENT_PATH:?}"
BASE_SHA="$(jq -r '.pull_request.base.sha' "$EVENT")"
HEAD_SHA="$(jq -r '.pull_request.head.sha' "$EVENT")"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"
OUTCOMES="${RUNNER_TEMP:-/tmp}/citygml_outcomes.env"

set +e
(
  set -uo pipefail
  "$PY" "$TOOLS_DIR/scripts/val3dity_gate.py" \
    --repo "$WORKSPACE" \
    --base-sha "$BASE_SHA" \
    --head-sha "$HEAD_SHA" \
    --file-list changed_gml.txt > out/val3dity.md || true
  # If there is no regression (clean), do not post. If there is one, keep it as an advisory comment.
  if grep -q "introduce no new topological inconsistencies" out/val3dity.md 2>/dev/null; then rm -f out/val3dity.md; fi
  [ -s out/val3dity.md ] || rm -f out/val3dity.md
)
if [ $? -eq 0 ]; then
  echo "TOPOLOGY_OUTCOME=success" >> "$OUTCOMES"
else
  echo "TOPOLOGY_OUTCOME=failure" >> "$OUTCOMES"
fi
exit 0
