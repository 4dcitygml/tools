#!/usr/bin/env bash
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
#
# CityGML PR analysis driver (inspection summary) — shared CI logic, plan C.
# The city workflow's entry point for the summary: it loads the facts the analysis drivers
# recorded and runs scripts/inspection_summary.py, which builds the inspection table, the
# resubmission comment and inspection.json from the gates' results. Always runs (the wrapper
# calls it with `if: always()`).
#
# Contract with the wrapper:
#   - cwd = city repository checkout; out/ holds the comment bodies so far
#   - the run folder ($CITYGML_RUN, made by the analysis driver) holds gates/<key>.json, one result
#     per gate (scripts/gate_result.py): the only state the summary reads (U2)
#   - STRICT_GATE=1 (required in production, set via the CITYGML_STRICT_GATE repository
#     variable) makes this script exit non-zero when a row fails, errs or is pending, so the
#     job conclusion becomes a real merge gate. Warnings never block.

set -euo pipefail
: "${TOOLS_DIR:?TOOLS_DIR must point at the 4dcitygml/tools checkout}"
PY="$(command -v python || command -v python3)"   # setup-python provides `python` on runners; python3 is the local fallback

exec "$PY" "$TOOLS_DIR/scripts/inspection_summary.py" "${CITYGML_EVENT:-$GITHUB_EVENT_PATH}"   # D23
