#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for validate_citygml (validate / feature list C).

Confirms offline validation works with the bundled schemas (schemas/ + i-UR).
Run: python -m unittest tests.test_validate_citygml
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.validate_citygml import validate_file

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestValidate(unittest.TestCase):
    def test_valid_instance_passes_xsd(self) -> None:
        ok, errors = validate_file(FIXTURES / "base.gml")
        self.assertTrue(ok, msg=str(errors[:3]))
        self.assertEqual(errors, [])

    def test_iur20_instance_passes_xsd(self) -> None:
        """PLATEAU 2020/2021 datasets use i-UR 2.0 (uro/2.0); the bundled schema set
        must validate them offline (source-baseline PRs start from these editions)."""
        ok, errors = validate_file(FIXTURES / "iur20_building.gml")
        self.assertTrue(ok, msg=str(errors[:3]))
        self.assertEqual(errors, [])

    def test_not_well_formed_is_rejected(self) -> None:
        raw = (FIXTURES / "base.gml").read_bytes()
        broken = raw.replace(b"</bldg:Building>", b"<unclosed></bldg:Building>", 1)
        with tempfile.NamedTemporaryFile(suffix=".gml", delete=False) as tf:
            tf.write(broken)
            path = Path(tf.name)
        try:
            ok, errors = validate_file(path)
            self.assertFalse(ok)
            self.assertTrue(any("well-formed" in e for e in errors))
        finally:
            path.unlink()

    def test_schema_invalid_value_is_rejected(self) -> None:
        # measuredHeight is xs:double. A non-numeric value is well-formed but invalid per XSD
        raw = (FIXTURES / "base.gml").read_bytes()
        self.assertIn(b"measuredHeight", raw)
        bad = raw.replace(b">13.8</bldg:measuredHeight>", b">NOT_A_NUMBER</bldg:measuredHeight>", 1)
        self.assertNotEqual(bad, raw)
        with tempfile.NamedTemporaryFile(suffix=".gml", delete=False) as tf:
            tf.write(bad)
            path = Path(tf.name)
        try:
            ok, errors = validate_file(path)
            self.assertFalse(ok)
            self.assertTrue(errors and all("well-formed" not in e for e in errors))
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
