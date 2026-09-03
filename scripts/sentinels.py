#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Registry of PLATEAU "unknown value" sentinels (reusable shared component).

PLATEAU/CityGML represents "value unknown" with numeric sentinels (mostly
9999 / -9999). These are the official "unknown" representation, not anomalous
values. Centralizing them lets multiple consumers share the same "unknown" definition:

- **Data-quality lint (`plateau_lint.py`)**: exclude from checks (do not falsely flag sentinels as anomalies).
- **Statistics (future `data_stats` etc.)**: exclude from aggregates such as mean/max so they do not distort reality.
- **Quality monitoring**: the trend of the "unknown rate (sentinel ratio)" itself can serve as a quality metric
  (decreasing unknowns = maintenance progress / sudden increase = data-degradation alert).

This module handles **unknown values of numeric attributes** (±9999 etc.). **"Unknown" codes
of classification attributes (code values)** come from codelists, so `scripts/plateau_codelists.py`
generates them exhaustively from the spec (e.g. Building_class=9999 / Building_usage=461).
Together the two form an exhaustive definition of "unknown". Threshold logic (`citygml_constants`) is kept separate.
"""
from __future__ import annotations

# Generic sentinels treated as "unknown" for any numeric attribute (widely used in PLATEAU).
GENERIC_SENTINELS: frozenset = frozenset({9999.0, -9999.0})

# Additional sentinels specific to an attribute (localname); add only when an attribute uses values not in the generic set.
# Example: "someAttr": frozenset({99999.0})
ATTRIBUTE_SENTINELS: dict = {}


def is_sentinel(localname: str, value) -> bool:
    """True if value is an "unknown-value sentinel" for the localname attribute.

    Values that cannot be numerified (None, non-numbers) are False (sentinel
    detection targets numeric attributes only). Generic sentinels (±9999) apply
    to all attributes; attribute-specific sentinels only to their attribute.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if v in GENERIC_SENTINELS:
        return True
    return v in ATTRIBUTE_SENTINELS.get(localname, frozenset())
