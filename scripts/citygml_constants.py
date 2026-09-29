#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Constants shared across the CityGML CI (feature list X-4: centralize thresholds and markers).

PR comment markers are kept distinct from each other so that each comment can be upserted independently.
"""
from __future__ import annotations

import os


def tools_repo() -> str:
    """The repository that publishes the tools (owner/name): CITYGML_TOOLS_REPO, else 4dcitygml/tools.
    A city that verifies in a private mirror names it once, in this variable."""
    return os.environ.get("CITYGML_TOOLS_REPO") or "4dcitygml/tools"

# --- PR comment markers (each posted/updated independently): the table is scripts/pr_markers.py ---
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from scripts import pr_markers  # noqa: E402

PREVIEW_MARKER = pr_markers.PREVIEW
SUMMARY_MARKER = pr_markers.CHANGE_SUMMARY
LINT_MARKER = pr_markers.REVIEWABILITY_LINT
METADATA_MARKER = pr_markers.METADATA
# The data-quality lint has two layers (generic CityGML structure / PLATEAU conventions), each with its own comment.
CITYGML_LINT_MARKER = pr_markers.QUALITY_LINT
PLATEAU_LINT_MARKER = pr_markers.PLATEAU_LINT
# Topological consistency gate (official engine val3dity, diff-based). Pre-existing defects are tolerated; only invalids introduced by the PR are warned about.
VAL3DITY_MARKER = pr_markers.VAL3DITY

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
