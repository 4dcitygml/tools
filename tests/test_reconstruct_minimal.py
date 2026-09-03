#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for reconstruct_minimal (W6 / diff minimization).

Clean-room tests of the core requirement: "a change to one building must not look
like a change to another building" — guaranteed by unmodified buildings being
byte-identical to base after reconstruction, and by the output being semantically identical to head (self-verification).
Run: python -m unittest tests.test_reconstruct_minimal
"""
from __future__ import annotations

import unittest

from scripts.diff_citygml import diff_sources
from scripts.reconstruct_minimal import building_spans, classify, reconstruct

NS = (
    'xmlns:core="http://www.opengis.net/citygml/2.0" '
    'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
    'xmlns:gen="http://www.opengis.net/citygml/generics/2.0" '
    'xmlns:app="http://www.opengis.net/citygml/appearance/2.0" '
    'xmlns:gml="http://www.opengis.net/gml"'
)


def _bldg(bid: str, *, height: str = "10.0", storeys: str = "3",
          poslist: str = "0 0 0 1 0 0 1 1 0 0 0 0") -> str:
    return (
        "\t<core:cityObjectMember>\r\n"
        f'\t\t<bldg:Building gml:id="{bid}">\r\n'
        f'\t\t\t<bldg:measuredHeight uom="m">{height}</bldg:measuredHeight>\r\n'
        f"\t\t\t<bldg:storeysAboveGround>{storeys}</bldg:storeysAboveGround>\r\n"
        "\t\t\t<bldg:lod1Solid><gml:Solid><gml:exterior><gml:CompositeSurface>"
        "<gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>"
        f"<gml:posList>{poslist}</gml:posList>"
        "</gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>"
        "</gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod1Solid>\r\n"
        "\t\t</bldg:Building>\r\n"
        "\t</core:cityObjectMember>\r\n"
    )


def _model(*members: str) -> bytes:
    body = "".join(members)
    xml = (
        '﻿<?xml version="1.0" encoding="UTF-8"?>\r\n'
        f"<core:CityModel {NS}>\r\n{body}</core:CityModel>\r\n"
    )
    return xml.encode("utf-8")


def _reindent(raw: bytes) -> bytes:
    """Simulate indentation churn: bulk-convert tabs to four spaces (meaning unchanged)."""
    return raw.replace(b"\t", b"    ")


class TestBuildingSpans(unittest.TestCase):
    def test_spans_cover_only_their_building(self):
        raw = _model(_bldg("A"), _bldg("B"))
        spans = building_spans(raw)
        self.assertEqual(set(spans), {"A", "B"})
        for bid in ("A", "B"):
            s, e = spans[bid]
            self.assertIn(f'gml:id="{bid}"'.encode(), raw[s:e])
        # Spans do not overlap.
        (sa, ea), (sb, eb) = spans["A"], spans["B"]
        self.assertLessEqual(ea, sb)


class TestAttributeOnly(unittest.TestCase):
    def test_leaf_replace_is_minimal_and_localized(self):
        base = _model(_bldg("A", storeys="3"), _bldg("B", storeys="9"))
        head = _model(_bldg("A", storeys="5"), _bldg("B", storeys="9"))
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        self.assertEqual(r.classification, "single")
        self.assertEqual(r.methods, {"A": "leaf"})
        # Output equals head (this head is already a minimal diff).
        self.assertEqual(r.output, head)
        # B's bytes are unchanged from base.
        self.assertEqual(building_spans(base)["B"], building_spans(r.output)["B"])
        self.assertEqual(_span_bytes(base, "B"), _span_bytes(r.output, "B"))

    def test_reindented_head_churn_is_removed(self):
        # Head where the proposer bulk-converted the indentation and changed A's storeys 3->5.
        base = _model(_bldg("A", storeys="3"), _bldg("B", storeys="9"))
        head = _reindent(_model(_bldg("A", storeys="5"), _bldg("B", storeys="9")))
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        # Churn is gone; B is byte-identical to base (indentation also stays as in base).
        self.assertEqual(_span_bytes(r.output, "B"), _span_bytes(base, "B"))
        # In A only the storeys leaf becomes 5; indentation keeps base's tabs.
        a = _span_bytes(r.output, "A")
        self.assertIn(b"<bldg:storeysAboveGround>5</bldg:storeysAboveGround>", a)
        self.assertIn(b"\t\t<bldg:Building", a)  # base's tab indentation is preserved
        self.assertNotIn(b"    <bldg:Building", a)  # head's space indentation does not get in
        # Across the whole output the only diff is A's storeys: the building diff of output vs base is A only.
        d = diff_sources(base, r.output, "b", "h")
        self.assertEqual([x["id"] for x in d["buildings"]], ["A"])

    def test_only_changed_leaf_replaced_when_two_buildings_share_value(self):
        # A and B share the same storeys=9. Only A changes 9->4; B is untouched.
        base = _model(_bldg("A", storeys="9"), _bldg("B", storeys="9"))
        head = _model(_bldg("A", storeys="4"), _bldg("B", storeys="9"))
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        self.assertEqual(_span_bytes(r.output, "B"), _span_bytes(base, "B"))
        self.assertIn(b"<bldg:storeysAboveGround>4</bldg:storeysAboveGround>",
                      _span_bytes(r.output, "A"))


class TestGeometry(unittest.TestCase):
    def test_geometry_change_uses_block_swap_and_localizes(self):
        base = _model(_bldg("A", poslist="0 0 0 1 0 0 1 1 0 0 0 0"),
                      _bldg("B"))
        head = _model(_bldg("A", height="13.0", poslist="0 0 0 1 0 3 1 1 3 0 0 0"),
                      _bldg("B"))
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        self.assertEqual(r.methods, {"A": "block"})
        # B unchanged; A has head's geometry.
        self.assertEqual(_span_bytes(r.output, "B"), _span_bytes(base, "B"))
        d = diff_sources(base, r.output, "b", "h")
        self.assertEqual([x["id"] for x in d["buildings"]], ["A"])
        self.assertTrue(d["buildings"][0]["geometry_changed"])


class TestLifecycle(unittest.TestCase):
    def test_added_building_inserted_and_others_untouched(self):
        base = _model(_bldg("A"), _bldg("B"))
        head = _model(_bldg("A"), _bldg("B"), _bldg("C", storeys="7"))
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        self.assertEqual(r.classification, "lifecycle")
        self.assertIn("C", r.added)
        # A and B are byte-identical to base.
        self.assertEqual(_span_bytes(r.output, "A"), _span_bytes(base, "A"))
        self.assertEqual(_span_bytes(r.output, "B"), _span_bytes(base, "B"))
        # C exists semantically.
        self.assertIn("C", building_spans(r.output))

    def test_merge_delete_two_add_one(self):
        # Merge: delete A and B, add M (several buildings -> one). C unchanged.
        base = _model(_bldg("A"), _bldg("B"), _bldg("C"))
        head = _model(_bldg("C"), _bldg("M", storeys="8"))
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        self.assertEqual(r.classification, "lifecycle")
        self.assertEqual(set(r.deleted), {"A", "B"})
        self.assertEqual(r.added, ["M"])
        self.assertEqual(_span_bytes(r.output, "C"), _span_bytes(base, "C"))
        self.assertNotIn("A", building_spans(r.output))
        self.assertNotIn("B", building_spans(r.output))


class TestMultiModified(unittest.TestCase):
    def test_two_independent_modifications_classified_multi(self):
        base = _model(_bldg("A", storeys="3"), _bldg("B", storeys="3"))
        head = _model(_bldg("A", storeys="5"), _bldg("B", storeys="6"))
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        self.assertEqual(r.classification, "multi-modified")
        self.assertEqual(set(r.modified), {"A", "B"})


class TestClassifyPR(unittest.TestCase):
    """Scope classification over PR-wide aggregate counts (core of CI's mechanical rejection)."""

    def test_none(self):
        self.assertEqual(classify(0, 0, 0), "none")

    def test_single_ok(self):
        self.assertEqual(classify(1, 0, 0), "single")

    def test_two_modifications_rejected_as_multi(self):
        # Two or more modifications with no add/delete -> mechanically rejected (B).
        self.assertEqual(classify(2, 0, 0), "multi-modified")
        self.assertEqual(classify(6, 0, 0), "multi-modified")

    def test_lifecycle_not_rejected(self):
        # With an add/delete it is a merge/split/rebuild = human review (not rejected).
        self.assertEqual(classify(0, 1, 0), "lifecycle")   # add
        self.assertEqual(classify(0, 0, 1), "lifecycle")   # delete
        self.assertEqual(classify(0, 1, 2), "lifecycle")   # merge (several buildings -> one)
        self.assertEqual(classify(1, 1, 0), "lifecycle")   # modify+add is also lifecycle (not rejected)


class TestRename(unittest.TestCase):
    """Content-based rename detection (id-only change = effectively no change; a rebuild is different)."""

    def test_id_only_change_is_rename_inplace(self):
        # Same content with only gml:id changed -> rename. Replace the id in place (do not move it).
        base = _model(_bldg("keep"), _bldg("bldg_oldid", storeys="7"))
        head = _model(_bldg("keep"), _bldg("bldg_newid", storeys="7"))
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        self.assertEqual(r.classification, "rename")
        self.assertEqual(r.renamed, [("bldg_oldid", "bldg_newid")])
        # keep is byte-identical to base. The renamed one exists under the new id; the old id is gone.
        self.assertEqual(_span_bytes(r.output, "keep"), _span_bytes(base, "keep"))
        self.assertIn("bldg_newid", building_spans(r.output))
        self.assertNotIn("bldg_oldid", building_spans(r.output))
        # Output is semantically identical to head (no churn = minimal diff of the id only).
        d = diff_sources(base, r.output, "b", "h")
        self.assertEqual([x["status"] for x in d["buildings"]], ["renamed"])

    def test_rebuild_is_not_rename(self):
        # A rebuild (both id and geometry change) is lifecycle (add/delete), not rename.
        base = _model(_bldg("bldg_oldid", poslist="0 0 0 1 0 0 1 1 0 0 0 0"))
        head = _model(_bldg("bldg_newid", poslist="0 0 0 1 0 5 1 1 5 0 0 0"))
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        self.assertEqual(r.classification, "lifecycle")
        self.assertEqual(r.renamed, [])

    def test_classify_rename_counts(self):
        self.assertEqual(classify(0, 0, 0, 1), "rename")
        self.assertEqual(classify(1, 0, 0, 1), "single")      # one modification + id churn -> single
        self.assertEqual(classify(0, 1, 0, 1), "lifecycle")   # lifecycle takes precedence if there is an add
        self.assertEqual(classify(2, 0, 0, 1), "multi-modified")


class TestNoChange(unittest.TestCase):
    def test_pure_reindent_produces_base_bytes(self):
        base = _model(_bldg("A"), _bldg("B"))
        head = _reindent(base)  # only the indentation changed (no semantic change)
        r = reconstruct(base, head)
        self.assertTrue(r.verified)
        self.assertEqual(r.classification, "none")
        # All churn is gone; fully restored to base bytes.
        self.assertEqual(r.output, base)


def _span_bytes(raw: bytes, bid: str) -> bytes:
    s, e = building_spans(raw)[bid]
    return raw[s:e]


if __name__ == "__main__":
    unittest.main()
