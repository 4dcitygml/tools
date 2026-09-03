#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Positive/negative tests for citygml_lint (generic CityGML geometry structure lint)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import citygml_lint  # noqa: E402
from scripts.citygml_lint import (  # noqa: E402
    _changed_ids,
    check_building,
    geom_index_from_map,
    render_markdown,
    run_lint,
)
from scripts.citygml_constants import CITYGML_LINT_MARKER  # noqa: E402
from scripts.diff_citygml import load_buildings  # noqa: E402

SQUARE = "0 0 0 0 1 0 1 1 0 1 0 0 0 0 0"


def _ring(poslist):
    return ("<gml:Polygon><gml:exterior><gml:LinearRing>"
            f"<gml:posList>{poslist}</gml:posList>"
            "</gml:LinearRing></gml:exterior></gml:Polygon>")


def bldg(bid, footprint=None, outline_element="lod0FootPrint"):
    parts = [f'<bldg:Building gml:id="{bid}">']
    if footprint is not None:
        parts.append(f"<bldg:{outline_element}><gml:MultiSurface><gml:surfaceMember>"
                     f"{_ring(footprint)}</gml:surfaceMember></gml:MultiSurface></bldg:{outline_element}>")
    parts.append("</bldg:Building>")
    return "".join(parts)


def wrap(*bodies):
    inner = "".join(f"<core:cityObjectMember>{b}</core:cityObjectMember>" for b in bodies)
    return ('<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            'xmlns:gml="http://www.opengis.net/gml">'
            f"{inner}</core:CityModel>").encode("utf-8")


def lint_bytes(raw, only_ids=None):
    return run_lint(raw, "t.gml", check_building, only_ids=only_ids,
                    geom_index=geom_index_from_map(load_buildings(raw)))


def codes(result):
    errs, warns = set(), set()
    for b in result["buildings"]:
        errs |= {e["code"] for e in b["errors"]}
        warns |= {w["code"] for w in b["warnings"]}
    return errs, warns


class TestStructural(unittest.TestCase):
    def test_clean(self):
        res = lint_bytes(wrap(bldg("ok", SQUARE)))
        self.assertEqual((res["n_errors"], res["n_warnings"]), (0, 0))

    def test_ring_not_closed(self):
        errs, _ = codes(lint_bytes(wrap(bldg("b", "0 0 0 0 1 0 1 1 0 1 0 0"))))
        self.assertIn("ring_not_closed", errs)

    def test_coord_count(self):
        errs, _ = codes(lint_bytes(wrap(bldg("b", "0 0 0 0 1 0 1 1"))))
        self.assertIn("coord_count", errs)

    def test_too_few_points(self):
        errs, _ = codes(lint_bytes(wrap(bldg("b", "0 0 0 1 1 0 0 0 0"))))
        self.assertIn("too_few_points", errs)

    def test_degenerate_face(self):
        errs, _ = codes(lint_bytes(wrap(bldg("b", "0 0 0 0 0 0 0 0 0 0 0 0"))))
        self.assertIn("degenerate_face", errs)

    def test_self_intersection(self):
        errs, _ = codes(lint_bytes(wrap(bldg("b", "0 0 0 1 1 0 1 0 0 0 1 0 0 0 0"))))
        self.assertIn("self_intersection", errs)

    def test_roof_edge_self_intersection(self):
        errs, _ = codes(lint_bytes(wrap(bldg(
            "b", "0 0 0 1 1 0 1 0 0 0 1 0 0 0 0", outline_element="lod0RoofEdge"
        ))))
        self.assertIn("self_intersection", errs)

    def test_valid_square_no_self_intersection(self):
        errs, _ = codes(lint_bytes(wrap(bldg("b", SQUARE))))
        self.assertNotIn("self_intersection", errs)

    def test_duplicate_geometry(self):
        _, warns = codes(lint_bytes(wrap(bldg("b1", SQUARE), bldg("b2", SQUARE))))
        self.assertIn("duplicate_geometry", warns)


class TestEngine(unittest.TestCase):
    def test_only_ids_scope(self):
        raw = wrap(bldg("bad", "0 0 0 0 1 0 1 1 0 1 0 0"), bldg("ok", SQUARE))
        res = lint_bytes(raw, only_ids={"ok"})
        self.assertEqual(res["n_errors"], 0)

    def test_changed_ids(self):
        old = {"a": ({}, "h1"), "b": ({"x": "1"}, "h2")}
        new = {"a": ({}, "h1"), "b": ({"x": "2"}, "h2"), "c": ({}, "h3")}
        self.assertEqual(_changed_ids(old, new), {"b", "c"})

    def test_markdown_marker(self):
        res = lint_bytes(wrap(bldg("b", "0 0 0 0 1 0 1 1 0 1 0 0")))
        md = render_markdown([res], CITYGML_LINT_MARKER, "T")
        self.assertIn(CITYGML_LINT_MARKER, md)
        self.assertIn("error", md)

    def test_main_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.gml"
            bad.write_bytes(wrap(bldg("b", "0 0 0 0 1 0 1 1 0 1 0 0")))
            ok = Path(d) / "ok.gml"
            ok.write_bytes(wrap(bldg("b", SQUARE)))
            self.assertEqual(citygml_lint.main([str(bad), "--json"]), 1)
            self.assertEqual(citygml_lint.main([str(ok), "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
