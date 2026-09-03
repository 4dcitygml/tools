#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tests for data_stats (sentinel-excluded statistics and unknown rate)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data_stats import (  # noqa: E402
    code_distribution,
    collect,
    compare_stats,
    load_baseline,
    quality_profile,
    render_drift_markdown,
    render_markdown,
    save_baseline,
    summarize,
)


def bldg(bid, height=None, usage=None):
    parts = [f'<bldg:Building gml:id="{bid}">']
    if height is not None:
        parts.append(f'<bldg:measuredHeight uom="m">{height}</bldg:measuredHeight>')
    if usage is not None:
        parts.append(f'<bldg:usage codeSpace="../../codelists/Building_usage.xml">{usage}</bldg:usage>')
    parts.append("</bldg:Building>")
    return "".join(parts)


def wrap(*bodies):
    inner = "".join(f"<core:cityObjectMember>{b}</core:cityObjectMember>" for b in bodies)
    return ('<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            'xmlns:gml="http://www.opengis.net/gml">'
            f"{inner}</core:CityModel>").encode("utf-8")


class TestNumeric(unittest.TestCase):
    def test_sentinel_excluded_from_stats(self):
        raw = wrap(bldg("a", height=10), bldg("b", height=20), bldg("c", height=-9999))
        num, _ = collect(raw, ("measuredHeight",), None)
        self.assertEqual(num["measuredHeight"]["total"], 3)
        self.assertEqual(num["measuredHeight"]["sentinel"], 1)  # -9999 is a sentinel
        self.assertEqual(sorted(num["measuredHeight"]["values"]), [10.0, 20.0])

    def test_summary_rates_and_aggregates(self):
        raw = wrap(bldg("a", height=10), bldg("b", height=20), bldg("c", height=-9999))
        s = summarize(*collect(raw, ("measuredHeight",), None))
        m = s["numeric"]["measuredHeight"]
        self.assertEqual(m["valid"], 2)
        self.assertEqual(m["unknown_rate"], round(1 / 3, 4))
        self.assertEqual((m["mean"], m["min"], m["max"]), (15.0, 10.0, 20.0))  # unknowns excluded


class TestCategorical(unittest.TestCase):
    def test_coded_unknown_rate(self):
        raw = wrap(bldg("a", usage="461"), bldg("b", usage="402"))
        unknown_map = {"Building_usage.xml": frozenset({"461"})}  # 461 = unknown
        _, coded = collect(raw, (), unknown_map)
        self.assertEqual(coded["Building_usage.xml"], {"total": 2, "unknown": 1})
        s = summarize({}, coded)
        self.assertEqual(s["categorical"]["Building_usage.xml"]["unknown_rate"], 0.5)


_QAXIS = (
    '<gml:Dictionary xmlns:gml="http://www.opengis.net/gml"><gml:name>T</gml:name>'
    '<gml:dictionaryEntry><gml:Definition gml:id="d1">'
    "<gml:description>航空レーザ測量成果</gml:description><gml:name>01</gml:name>"
    "</gml:Definition></gml:dictionaryEntry>"
    '<gml:dictionaryEntry><gml:Definition gml:id="d2">'
    "<gml:description>未作成</gml:description><gml:name>99</gml:name>"
    "</gml:Definition></gml:dictionaryEntry></gml:Dictionary>"
)


def _bldg_coded(bid, code):
    return (f'<bldg:Building gml:id="{bid}">'
            f'<uro:geometrySrcDescLod2 codeSpace="../../codelists/Test_geometrySrcDesc.xml">'
            f"{code}</uro:geometrySrcDescLod2>"
            "</bldg:Building>")


def _wrap_coded(*bodies):
    inner = "".join(f"<core:cityObjectMember>{b}</core:cityObjectMember>" for b in bodies)
    return ('<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            'xmlns:uro="https://www.geospatial.jp/iur/uro/3.2" '
            'xmlns:gml="http://www.opengis.net/gml">'
            f"{inner}</core:CityModel>").encode("utf-8")


class TestProfile(unittest.TestCase):
    def test_quality_profile_tiers(self):
        with tempfile.TemporaryDirectory() as d:
            cl = Path(d) / "codelists"
            cl.mkdir()
            (cl / "Test_geometrySrcDesc.xml").write_text(_QAXIS, encoding="utf-8")
            gml = Path(d) / "a.gml"
            gml.write_bytes(_wrap_coded(_bldg_coded("a", "01"), _bldg_coded("b", "99")))
            prof = quality_profile([gml], cl)
            axis = prof["geometrySrcDescLod2"]  # keyed by element name (axis x LOD)
            self.assertEqual(axis["total"], 2)
            self.assertEqual(axis["lod"], 2)
            self.assertEqual(axis["base"], "geometrySrcDesc")
            self.assertEqual(axis["tier_rates"]["measured"], 0.5)  # 01 = airborne laser survey
            self.assertEqual(axis["tier_rates"]["missing"], 0.5)   # 99 = not created
            # Per-code counts, rates, and tiers
            byc = {c["code"]: c for c in axis["codes"]}
            self.assertEqual(byc["01"]["count"], 1)
            self.assertEqual(byc["01"]["rate"], 0.5)
            self.assertEqual(byc["01"]["tier"], "measured")
            self.assertEqual(byc["99"]["tier"], "missing")


class TestAllCodes(unittest.TestCase):
    def test_includes_non_quality_axes(self):
        # --all-codes includes all coded attributes, not just quality axes (usage is not a quality axis)
        body = ('<bldg:Building gml:id="a">'
                '<bldg:usage codeSpace="../../codelists/Building_usage.xml">402</bldg:usage>'
                '</bldg:Building>')
        with tempfile.TemporaryDirectory() as d:
            g = Path(d) / "a.gml"
            g.write_bytes(_wrap_coded(body))
            recs = code_distribution([g])
            by_elem = {r["element"]: r for r in recs}
            self.assertIn("usage", by_elem)
            self.assertEqual(by_elem["usage"]["codes"][0]["code"], "402")
            self.assertEqual(by_elem["usage"]["codes"][0]["rate"], 1.0)


class TestOutput(unittest.TestCase):
    def test_markdown(self):
        s = summarize(*collect(wrap(bldg("a", height=10)), ("measuredHeight",), None))
        md = render_markdown(s)
        self.assertIn("Unknown Rate", md)


def _stats(unknown_rate=0.1, total=100, mean=15.0, mx=30.0, cat_rate=0.2, cat_total=100):
    """Build a stats dict for comparison tests (same shape as summarize output)."""
    return {
        "numeric": {"measuredHeight": {
            "total": total, "valid": total - int(total * unknown_rate),
            "sentinel": int(total * unknown_rate), "unknown_rate": unknown_rate,
            "mean": mean, "min": 0.0, "max": mx,
        }},
        "categorical": {"Building_usage.xml": {
            "total": cat_total, "unknown": int(cat_total * cat_rate), "unknown_rate": cat_rate,
        }},
    }


def _sev(findings, metric, key=None):
    return {f["severity"] for f in findings
            if f["metric"] == metric and (key is None or f["key"] == key)}


class TestDrift(unittest.TestCase):
    """Baseline comparison (#22): regression=warn / improvement=info / within threshold=no finding."""

    def test_no_drift(self):
        self.assertEqual(compare_stats(_stats(), _stats()), [])

    def test_within_threshold_no_finding(self):
        # Unknown rate +4pp and count +5% are within default thresholds (5pp/10%)
        base, cur = _stats(unknown_rate=0.10, total=100), _stats(unknown_rate=0.14, total=105)
        self.assertEqual([f for f in compare_stats(base, cur) if f["metric"] in ("unknown_rate", "total")], [])

    def test_unknown_rate_increase_is_warn(self):
        findings = compare_stats(_stats(unknown_rate=0.10), _stats(unknown_rate=0.20))
        self.assertEqual(_sev(findings, "unknown_rate", "measuredHeight"), {"warn"})

    def test_unknown_rate_decrease_is_info(self):
        # Decrease in unknown rate = data maintenance progress (record only, does not fail CI)
        findings = compare_stats(_stats(unknown_rate=0.20), _stats(unknown_rate=0.10))
        self.assertEqual(_sev(findings, "unknown_rate", "measuredHeight"), {"info"})

    def test_total_drop_is_warn(self):
        findings = compare_stats(_stats(total=100, cat_total=100), _stats(total=80, cat_total=80))
        self.assertEqual(_sev(findings, "total", "measuredHeight"), {"warn"})
        self.assertEqual(_sev(findings, "total", "Building_usage.xml"), {"warn"})

    def test_mean_shift_is_warn(self):
        findings = compare_stats(_stats(mean=15.0), _stats(mean=20.0))
        self.assertEqual(_sev(findings, "mean"), {"warn"})

    def test_missing_attr_is_warn_new_is_info(self):
        base, cur = _stats(), _stats()
        cur["numeric"] = {"storeysAboveGround": dict(base["numeric"]["measuredHeight"])}
        findings = compare_stats(base, cur)
        self.assertEqual(_sev(findings, "missing", "measuredHeight"), {"warn"})
        self.assertEqual(_sev(findings, "new", "storeysAboveGround"), {"info"})

    def test_custom_threshold(self):
        # Loosening the threshold yields no finding for the same diff
        base, cur = _stats(unknown_rate=0.10), _stats(unknown_rate=0.20)
        self.assertEqual(
            [f for f in compare_stats(base, cur, rate_threshold=0.15) if f["metric"] == "unknown_rate"], [])

    def test_baseline_roundtrip_and_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "base.json"
            save_baseline(p, _stats(), ("measuredHeight",), 3)
            doc = load_baseline(p)
            self.assertEqual(doc["schema"], 1)
            self.assertEqual(doc["n_files"], 3)
            findings = compare_stats(doc["stats"], _stats(unknown_rate=0.20))
            md = render_drift_markdown(findings, doc, 0.05, 0.10)
            self.assertIn("regression(s)", md)
            self.assertIn("measuredHeight", md)
            # No diff yields ✅
            self.assertIn("✅", render_drift_markdown([], doc, 0.05, 0.10))


if __name__ == "__main__":
    unittest.main()
