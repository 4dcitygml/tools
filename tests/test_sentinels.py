#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for the sentinel registry (scripts/sentinels.py)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import sentinels  # noqa: E402
from scripts.sentinels import is_sentinel  # noqa: E402


class TestSentinels(unittest.TestCase):
    def test_generic_plus_minus_9999(self):
        # ±9999 means "unknown" for any numeric attribute
        for name in ("measuredHeight", "storeysAboveGround", "anything"):
            self.assertTrue(is_sentinel(name, 9999))
            self.assertTrue(is_sentinel(name, -9999))

    def test_string_and_float_forms(self):
        self.assertTrue(is_sentinel("measuredHeight", "-9999"))
        self.assertTrue(is_sentinel("measuredHeight", -9999.0))

    def test_normal_values_not_sentinel(self):
        for v in (0, 1, 3, 10.5, 300, -5):
            self.assertFalse(is_sentinel("measuredHeight", v))

    def test_non_numeric_is_not_sentinel(self):
        self.assertFalse(is_sentinel("x", None))
        self.assertFalse(is_sentinel("x", "abc"))

    def test_attribute_specific_registry(self):
        # Attribute-specific sentinels apply only to that attribute (generic ones apply to all)
        sentinels.ATTRIBUTE_SENTINELS["testAttr"] = frozenset({12345.0})
        try:
            self.assertTrue(is_sentinel("testAttr", 12345))
            self.assertFalse(is_sentinel("otherAttr", 12345))
        finally:
            del sentinels.ATTRIBUTE_SENTINELS["testAttr"]


if __name__ == "__main__":
    unittest.main()
