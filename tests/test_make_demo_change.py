#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for make_demo_change (demo PR generator).

Key correctness: only the target building's byte range is rewritten; other buildings and bytes are unchanged.
Run: python -m unittest tests.test_make_demo_change
"""
from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from scripts.diff_citygml import diff_files
from scripts.make_demo_change import apply_prA, apply_prB, main
from tests.test_diff_citygml import _building, _model


def _two_building_doc() -> bytes:
    # Both buildings have storeys=9999 (collision condition matching real data).
    # target has Z>0 (so prB's height raise takes effect).
    return _model(
        _building("target", storeys="9999", height="13.8",
                  poslist="0 0 0 1 0 0 1 1 6 0 0 6"),
        _building("other", storeys="9999", height="20.0"),
    ).encode("utf-8")


class TestSpanIsolation(unittest.TestCase):
    """Change only the target building without breaking other buildings or other bytes."""

    def _span(self, raw: bytes, bid: str) -> bytes:
        from scripts.make_demo_change import _find_span
        s, e = _find_span(raw, bid)
        return raw[s:e]

    def test_prA_changes_only_target_building(self) -> None:
        raw = _two_building_doc()
        from scripts.make_demo_change import _find_span
        s, e = _find_span(raw, "target")
        new = raw[:s] + apply_prA(raw[s:e], "9999", "4") + raw[e:]
        # Bytes of the other building are unchanged
        self.assertEqual(self._span(new, "other"), self._span(raw, "other"))
        # Target changes 9999->4, and overall only other's 9999 remains
        self.assertIn(b"<bldg:storeysAboveGround>4</bldg:storeysAboveGround>", new)
        self.assertEqual(new.count(b"<bldg:storeysAboveGround>9999</bldg:storeysAboveGround>"), 1)

    def test_prB_changes_only_target_geometry(self) -> None:
        raw = _two_building_doc()
        from scripts.make_demo_change import _find_span
        s, e = _find_span(raw, "target")
        new = raw[:s] + apply_prB(raw[s:e], Decimal("3.0")) + raw[e:]
        self.assertEqual(self._span(new, "other"), self._span(raw, "other"))
        # other's height 20.0 is unchanged; target's 13.8->16.8
        self.assertIn(b"<bldg:measuredHeight uom=\"m\">20.0</bldg:measuredHeight>", new)
        self.assertIn(b"<bldg:measuredHeight uom=\"m\">16.8</bldg:measuredHeight>", new)


class TestPipelineViaMain(unittest.TestCase):
    """Generate files via main(), then W1 classifies them as expected."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.base = self.dir / "base.gml"
        self.base.write_bytes(_two_building_doc())

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_prA_pipeline(self) -> None:
        out = self.dir / "prA.gml"
        main([str(self.base), "--building", "target", "--change", "prA", "--output", str(out)])
        r = diff_files(self.base, out)
        (b,) = r["buildings"]
        self.assertEqual(b["id"], "target")
        self.assertFalse(b["geometry_changed"])
        self.assertEqual(b["attribute_diffs"],
                         [{"path": "/storeysAboveGround", "old": "9999", "new": "4"}])

    def test_prB_pipeline(self) -> None:
        out = self.dir / "prB.gml"
        main([str(self.base), "--building", "target", "--change", "prB", "--output", str(out)])
        r = diff_files(self.base, out)
        (b,) = r["buildings"]
        self.assertEqual(b["id"], "target")
        self.assertTrue(b["geometry_changed"])
        self.assertIn("/measuredHeight", {d["path"] for d in b["attribute_diffs"]})

    def test_missing_building_errors(self) -> None:
        out = self.dir / "x.gml"
        with self.assertRaises(SystemExit):
            main([str(self.base), "--building", "nope", "--change", "prA", "--output", str(out)])

    def test_prD_id_change(self) -> None:
        from scripts.reviewability_lint import analyze_file
        from scripts.diff_citygml import load_buildings
        out = self.dir / "prD.gml"
        main([str(self.base), "--building", "target", "--change", "prD",
              "--new-id", "renamed", "--output", str(out)])
        r = analyze_file(load_buildings(self.base), load_buildings(out.read_bytes()), "f")
        self.assertEqual([w["type"] for w in r["warnings"]], ["id_change"])
        self.assertEqual(r["warnings"][0]["new_id"], "renamed")

    def test_prC_rebuild_is_lifecycle(self) -> None:
        from scripts.reviewability_lint import analyze_file
        from scripts.diff_citygml import load_buildings
        out = self.dir / "prC.gml"
        main([str(self.base), "--building", "target", "--change", "prC",
              "--new-id", "rebuilt", "--output", str(out)])
        r = analyze_file(load_buildings(self.base), load_buildings(out.read_bytes()), "f")
        # Geometry also changes, so it is lifecycle rather than id_change
        self.assertEqual(set(w["type"] for w in r["warnings"]),
                         {"lifecycle_added", "lifecycle_deleted"})

    def test_prE_large_change(self) -> None:
        # Only two buildings (target/other) exist, so count=2
        out = self.dir / "prE.gml"
        main([str(self.base), "--change", "prE", "--count", "2", "--output", str(out)])
        # storeys 9999->4 is applied to both buildings, so 9999 disappears
        self.assertNotIn(b"<bldg:storeysAboveGround>9999</bldg:storeysAboveGround>", out.read_bytes())

    def test_prF_corrupts_xml_and_breaks_parsing(self) -> None:
        from scripts.diff_citygml import load_buildings
        out = self.dir / "prF.gml"
        main([str(self.base), "--building", "target", "--change", "prF", "--output", str(out)])
        self.assertIn(b"<ci_failure_demo>", out.read_bytes())
        # Parsing (W1 load) raises an exception = CI is able to fail
        with self.assertRaises(Exception):
            load_buildings(out.read_bytes())


if __name__ == "__main__":
    unittest.main()
