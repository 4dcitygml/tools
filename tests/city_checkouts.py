# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The city repositories some tests read next to tools. A local run without them skips those
tests; CI checks them out (ci/city_checkouts.sh) and sets CITYGML_REQUIRE_CITIES=1, so there a
missing checkout fails the run instead of hiding the tests behind a skip. A refs file that names
no city asks for none: the tools release comes before the cities that pin it, and the refs follow
in their own pull request."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

_NAMED = json.loads((Path(__file__).resolve().parent.parent / "ci" / "city-refs.json").read_text(encoding="utf-8"))["repositories"]
REQUIRED = os.environ.get("CITYGML_REQUIRE_CITIES") == "1" and bool(_NAMED)


def missing(reason: str):
    """A test (or a test module) cannot read a city checkout: skip, or fail in CI."""
    if REQUIRED:
        raise AssertionError(f"city checkout required in CI: {reason}")
    raise unittest.SkipTest(reason)


def skip_unless(condition: bool, reason: str):
    """unittest.skipUnless for a class that reads city checkouts; in CI its tests fail instead."""
    if condition or not REQUIRED:
        return unittest.skipUnless(condition, reason)

    def fail(cls):
        cls.setUpClass = classmethod(lambda c: missing(reason))
        return cls
    return fail
