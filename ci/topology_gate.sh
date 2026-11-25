#!/usr/bin/env bash
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
#
# CityGML PR analysis driver (topology gate) — shared CI logic, plan C.
# Runs after the wrapper has set up the val3dity toolchain (Java + cached
# citygml-tools + val3dity build). The wrapper only calls this when the main
# driver decided run=true for the topology scope.
#
# Contract with the wrapper:
#   - cwd = city repository checkout; the run folder ($CITYGML_RUN) has changed_gml.txt (written by main)
#   - TOOLS_DIR, CITYGML_TOOLS, VAL3DITY_CMD are set
#   - writes the topology row to $CITYGML_RUN/gates/topology.json (scripts/gate_result.py)
#
# The gate is differential and advisory: it validates only the buildings the PR
# changed and warns only about invalids newly introduced by the PR. Missing
# tools auto-skip (advisory).

set -uo pipefail

: "${TOOLS_DIR:?TOOLS_DIR must point at the 4dcitygml/tools checkout}"
export PYTHONPATH="${TOOLS_DIR}${PYTHONPATH:+:$PYTHONPATH}"
PY="$(command -v python || command -v python3)"   # setup-python provides `python` on runners; python3 is the local fallback

EVENT="${CITYGML_EVENT:-${GITHUB_EVENT_PATH:?}}"   # the PR the analysis judged (D23)
BASE_SHA="$(jq -r '.pull_request.base.sha' "$EVENT")"
HEAD_SHA="$(jq -r '.pull_request.head.sha' "$EVENT")"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"
RUN="${CITYGML_RUN:-${RUNNER_TEMP:-/tmp}/citygml}"
set +e
(
  set -uo pipefail
  # --enforce: new invalids exit 1 (a finding of this advisory row, shown as a warning); missing
  # tools in CI or a crash exit 2 (S17).
  "$PY" "$TOOLS_DIR/scripts/val3dity_gate.py" \
    --repo "$WORKSPACE" \
    --base-sha "$BASE_SHA" \
    --head-sha "$HEAD_SHA" \
    --file-list "$RUN/changed_gml.txt" --enforce > out/val3dity.md
  "$PY" "$TOOLS_DIR/scripts/gate_result.py" write --key topology --exit-code "$?" --out "$RUN" > /dev/null || true
  # A clean check writes nothing, so nothing is posted; new invalids stay as an advisory comment (U2/U3).
  [ -s out/val3dity.md ] || rm -f out/val3dity.md
)
exit 0
