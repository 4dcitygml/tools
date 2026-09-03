#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Constants shared across the CityGML CI (feature list X-4: centralize thresholds and markers).

PR comment markers are kept distinct from each other so that each comment can be upserted independently.
"""
from __future__ import annotations

# --- PR comment markers (each posted/updated independently) ---
PREVIEW_MARKER = "<!-- cesium-building-preview -->"
SUMMARY_MARKER = "<!-- citygml-change-summary -->"
LINT_MARKER = "<!-- citygml-reviewability-lint -->"
METADATA_MARKER = "<!-- citygml-metadata -->"
# The data-quality lint has two layers (generic CityGML structure / PLATEAU conventions), each with its own comment.
CITYGML_LINT_MARKER = "<!-- citygml-quality-lint -->"
PLATEAU_LINT_MARKER = "<!-- plateau-quality-lint -->"
# Topological consistency gate (official engine val3dity, diff-based). Pre-existing defects are tolerated; only invalids introduced by the PR are warned about.
VAL3DITY_MARKER = "<!-- val3dity-topology-gate -->"

# --- val3dity topology gate (topological consistency per official standards, §6.3 L07-L14 / val3dity 100-405) ---
# Planarity tolerance [m]. Follows product spec §6.3 L12 (LOD2/3 "tolerance for treating surfaces as coplanar").
VAL3DITY_PLANARITY_D2P_M = 0.03

# --- reviewability lint (W3 / D-1-a) thresholds ---
# If the number of changed buildings per PR exceeds this, emit a "large change" warning.
LARGE_CHANGE_THRESHOLD = 5

# --- data lint (#13: valid != plausible / data quality rules) thresholds ---
# Coordinate dimension (EPSG:6697 = latitude, longitude, elevation triples). posList must be a multiple of this, else error.
COORD_DIM = 3
# Plausible upper bound for measuredHeight [m]. Exceeding it is a warning (0 or below is also a warning).
MAX_MEASURED_HEIGHT_M = 300.0
# Plausible upper bound for storeysAbove/BelowGround (exceeding it is a warning).
MAX_STOREYS = 200
# Note: the "unknown-value sentinel (e.g. +/-9999)" definitions are centralized in `scripts/sentinels.py` (shared by lint/stats/monitoring).
