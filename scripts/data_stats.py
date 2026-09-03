#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Data quality metrics (#13 extension) — output attribute statistics with sentinels excluded and compute "unknown rate".

Whereas lint (`citygml_lint`/`plateau_lint`) identifies anomalies at the **point level (single building)**,
this tool observes **distribution across the dataset**. Excludes unknown-value sentinels
(`sentinels.py` numeric ±9999 / `plateau_codelists.py` unknown codes) from statistics
and outputs the "**unknown rate (sentinel ratio)**" itself as a quality indicator.

- **Numeric attributes** (measuredHeight, floor count, etc.): count/mean/min/max excluding sentinels + unknown rate.
- **Code attributes** (with `codeSpace`): total value count and "unknown" code count → **unknown rate** (requires codelists directory).

Use cases:
- **Monitoring**: unknown rate over time = maintenance progress indicator (unknown decrease = progress).
- **Drift detection (#22)**: **save statistics output as baseline (JSON)** and warn if next-run diff exceeds threshold
  (point → time series). **Degradation** (unknown rate increase, count decrease, mean/max jump) = warn with exit 1;
  **improvement** (unknown rate decrease, count increase) = info (visualize "unknown rate decrease = maintenance progress").

Usage:
    python scripts/data_stats.py FILE.gml [FILE2.gml ...] [--codelists CODELISTS_DIR] [--json]
    python scripts/data_stats.py FILES... --save-baseline base.json          # save baseline
    python scripts/data_stats.py FILES... --baseline base.json               # compare (exit 1 if degraded)
        [--rate-threshold 0.05] [--rel-threshold 0.10]
"""
from __future__ import annotations

import argparse
import io
import json
import statistics as st
import sys
from pathlib import Path
from typing import Optional

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_lint import number_by_localname  # noqa: E402
from scripts.diff_citygml import _BUILDING_TAGS, _drop, _local  # noqa: E402
from scripts.plateau_codelists import (  # noqa: E402
    TIER_NAMES,
    is_quality_axis,
    load_codelists,
    quality_tier,
    unknown_codes,
)
from scripts.safe_xml import safe_iterparse  # noqa: E402
from scripts.sentinels import is_sentinel  # noqa: E402

# Numeric attributes (localname) to collect stats for by default. Replace via --numeric as needed.
DEFAULT_NUMERIC = ("measuredHeight", "storeysAboveGround", "storeysBelowGround")


def _open(source):
    if isinstance(source, (bytes, bytearray)):
        return io.BytesIO(source)
    return str(source)


def _iter_buildings(source):
    context = safe_iterparse(_open(source), events=("end",), tag=_BUILDING_TAGS)
    for _event, b in context:
        yield b
        _drop(b)


def collect(source, numeric_attrs, unknown_map: Optional[dict] = None):
    """Collect numeric-attribute statistics and coded-attribute unknown rates from one source."""
    num = {a: {"total": 0, "sentinel": 0, "values": []} for a in numeric_attrs}
    coded: dict = {}  # codelist name -> {"total":n, "unknown":n}
    for b in _iter_buildings(source):
        for a in numeric_attrs:
            v = number_by_localname(b, a)
            if v is None:
                continue
            num[a]["total"] += 1
            if is_sentinel(a, v):
                num[a]["sentinel"] += 1
            else:
                num[a]["values"].append(v)
        if unknown_map is not None:
            for el in b.iter():
                if not isinstance(el.tag, str):
                    continue
                cs = el.get("codeSpace")
                if not cs:
                    continue
                code = (el.text or "").strip()
                if not code:
                    continue
                name = cs.rsplit("/", 1)[-1]  # e.g. ../../codelists/Building_usage.xml -> Building_usage.xml
                d = coded.setdefault(name, {"total": 0, "unknown": 0})
                d["total"] += 1
                if code in unknown_map.get(name, frozenset()):
                    d["unknown"] += 1
    return num, coded


def summarize(num: dict, coded: dict) -> dict:
    """Summarize the raw counts from collect into statistics (mean/min/max, unknown rate)."""
    numeric = {}
    for a, d in num.items():
        if d["total"] == 0:
            continue
        vals = d["values"]
        numeric[a] = {
            "total": d["total"],
            "valid": len(vals),
            "sentinel": d["sentinel"],
            "unknown_rate": round(d["sentinel"] / d["total"], 4),
            "mean": round(st.mean(vals), 3) if vals else None,
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
        }
    categorical = {}
    for name, d in coded.items():
        if d["total"] == 0:
            continue
        categorical[name] = {
            "total": d["total"],
            "unknown": d["unknown"],
            "unknown_rate": round(d["unknown"] / d["total"], 4),
        }
    return {"numeric": numeric, "categorical": categorical}


def stats_for_files(paths, numeric_attrs, codelists_dir: Optional[Path] = None) -> dict:
    """Return statistics aggregated across multiple files (numeric + coded unknown rates)."""
    unknown_map = unknown_codes(codelists_dir) if codelists_dir else None
    num = {a: {"total": 0, "sentinel": 0, "values": []} for a in numeric_attrs}
    coded: dict = {}
    for p in paths:
        n, c = collect(Path(p).read_bytes(), numeric_attrs, unknown_map)
        for a in numeric_attrs:
            num[a]["total"] += n[a]["total"]
            num[a]["sentinel"] += n[a]["sentinel"]
            num[a]["values"].extend(n[a]["values"])
        for name, d in c.items():
            t = coded.setdefault(name, {"total": 0, "unknown": 0})
            t["total"] += d["total"]
            t["unknown"] += d["unknown"]
    return summarize(num, coded)


import collections as _collections  # noqa: E402
import re as _re  # noqa: E402

_LOD_PAT = _re.compile(r"[Ll]od(\d)")


def quality_profile(paths, codelists_dir: Path) -> dict:
    """For each DataQuality **element (quality axis x LOD)**, aggregate the value distribution into **quality tiers**.

    PLATEAU records provenance in per-LOD elements (`geometrySrcDescLod0..4`, `appearanceSrcDescLod2..4`, etc.).
    Emits the ordinal-scale distribution "surveyed -> drawings -> estimated -> not created" per element name
    (localname), making per-LOD maturity visible. Serves as the basis for tracking the progression
    "provisional -> measured, not created -> surveyed" over time via multi-party PRs (connects to #22).
    """
    tables = load_codelists(codelists_dir)
    per_elem: dict = {}  # element localname -> {"codelist":..., "counter":Counter}
    for p in paths:
        for b in _iter_buildings(Path(p).read_bytes()):
            for el in b.iter():
                if not isinstance(el.tag, str):
                    continue
                cs = el.get("codeSpace")
                if not cs:
                    continue
                cl = cs.rsplit("/", 1)[-1]
                if cl not in tables or not is_quality_axis(cl):
                    continue
                code = (el.text or "").strip()
                if not code:
                    continue
                ln = _local(el)[1]
                d = per_elem.setdefault(ln, {"codelist": cl, "counter": _collections.Counter()})
                d["counter"][code] += 1

    result: dict = {}
    for ln, d in per_elem.items():
        table = tables[d["codelist"]]
        tiers = _collections.Counter()
        for code, n in d["counter"].items():
            tiers[quality_tier(table.get(code, ""))] += n
        total = sum(tiers.values())
        m = _LOD_PAT.search(ln)
        result[ln] = {
            "total": total,
            "lod": int(m.group(1)) if m else None,
            "base": _LOD_PAT.sub("", ln) or ln,
            "codelist": d["codelist"],
            "tier_rates": {t: round(tiers[t] / total, 4) for t in TIER_NAMES if tiers[t]},
            # Per-code counts, rates, and tiers (most common first). Shown with --detail; used to inspect/calibrate the breakdown.
            "codes": [
                {"code": c, "label": table.get(c, c), "count": n,
                 "rate": round(n / total, 4), "tier": quality_tier(table.get(c, ""))}
                for c, n in d["counter"].most_common()
            ],
        }
    return result


_TIER_MARK = {"measured": "🟢 Surveyed", "documented": "🔵 Documented", "provisional": "🟡 Estimated/Provisional",
              "missing": "⚪ Not Created/Unknown", "other": "• Other"}


def render_profile_markdown(profile: dict, detail: bool = False) -> str:
    """Render the quality profile (maturity) as Markdown, per quality axis and per LOD.

    With detail=True, expands **per-code counts, rates, and tiers** (most common first) under each LOD.
    """
    lines = ["## 🏗️ Data Quality Profile (Maturity by Staged Implementation Level and LOD)", "",
             "Aggregated provenance on ordinal scale \"Surveyed → Documented → Estimated/Provisional → Not Created/Unknown\" (via `data_stats --profile`).", ""]
    groups: dict = _collections.defaultdict(list)
    for ln, s in profile.items():
        groups[s["base"]].append(s)
    # Axes with the most headroom (average of missing + provisional) come first
    def headroom(items):
        rs = [s["tier_rates"].get("missing", 0) + s["tier_rates"].get("provisional", 0) for s in items]
        return sum(rs) / len(rs) if rs else 0
    shown = 0
    for base, items in sorted(groups.items(), key=lambda kv: -headroom(kv[1])):
        # Exclude axes that do not map onto the tiers (accuracy level, LOD completeness, etc. — almost entirely "other").
        if all(s["tier_rates"].get("other", 0) >= 0.9 for s in items):
            continue
        lines.append(f"**{base}**（{items[0]['codelist']}）")
        for s in sorted(items, key=lambda x: (x["lod"] is None, x["lod"])):
            lod = f"LOD{s['lod']}" if s["lod"] is not None else "—"
            dist = " / ".join(f"{_TIER_MARK[t]} {r:.0%}" for t, r in s["tier_rates"].items())
            lines.append(f"- {lod} ({s['total']} items): {dist}")
            if detail:
                for c in s["codes"]:
                    lines.append(
                        f"    - {_TIER_MARK[c['tier']]} [{c['code']}] {c['label'][:40]} "
                        f"— **{c['count']} items ({c['rate']:.1%})**"
                    )
        lines.append("")
        shown += 1
    if shown == 0:
        lines.append("(No quality axes matching provenance tiers.)")
    return "\n".join(lines).rstrip() + "\n"


def code_distribution(paths, codelists_dir: Optional[Path] = None) -> list:
    """Collect counts and rates of every code for all coded attributes (with codeSpace), keyed by **element (localname) x codelist**.

    Emits **everything**, not just quality axes (verbose). Distinguishes cases where the same element
    name references different codelists. LOD is extracted from the element name (`geometrySrcDescLod2` -> LOD2).
    Labels are attached when codelists_dir is given.
    """
    tables = load_codelists(codelists_dir) if codelists_dir else {}
    buckets: dict = {}  # (localname, codelist) -> Counter
    for p in paths:
        for b in _iter_buildings(Path(p).read_bytes()):
            for el in b.iter():
                if not isinstance(el.tag, str):
                    continue
                cs = el.get("codeSpace")
                if not cs:
                    continue
                code = (el.text or "").strip()
                if not code:
                    continue
                key = (_local(el)[1], cs.rsplit("/", 1)[-1])
                buckets.setdefault(key, _collections.Counter())[code] += 1

    records: list = []
    for (ln, cl), cc in buckets.items():
        total = sum(cc.values())
        table = tables.get(cl, {})
        m = _LOD_PAT.search(ln)
        records.append({
            "element": ln,
            "codelist": cl,
            "lod": int(m.group(1)) if m else None,
            "base": _LOD_PAT.sub("", ln) or ln,
            "total": total,
            "codes": [
                {"code": c, "label": table.get(c, ""), "count": n, "rate": round(n / total, 4)}
                for c, n in cc.most_common()
            ],
        })
    records.sort(key=lambda r: (r["codelist"], r["lod"] is None, r["lod"] or 0, r["element"]))
    return records


def render_code_distribution_markdown(records: list) -> str:
    """Render the full code distribution (counts and rates) as Markdown."""
    lines = ["## 📋 Code Item Distribution (Counts and Rates)", "",
             f"All codes for attributes with codeSpace, aggregated by element × codelist ({len(records)} axes).", ""]
    for r in records:
        lod = f" [LOD{r['lod']}]" if r["lod"] is not None else ""
        lines.append(f"### `{r['element']}`{lod} — {r['codelist']} (total {r['total']} items)")
        for c in r["codes"]:
            lab = (c["label"] or "").replace("\n", " ")
            lines.append(f"- `{c['code']}` {lab} — **{c['count']} items ({c['rate']:.1%})**")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- Baseline + drift detection (#22) --------------------------------------
# Default thresholds: an absolute diff of 5pp in unknown rate, or a relative 10% change in count/mean/max, counts as a "sudden change".
DEFAULT_RATE_THRESHOLD = 0.05
DEFAULT_REL_THRESHOLD = 0.10


def save_baseline(path: Path, stats: dict, numeric_attrs, n_files: int) -> None:
    """Save statistics as a baseline JSON (the reference point for comparison with the next run)."""
    from datetime import datetime, timezone

    doc = {
        "schema": 1,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_files": n_files,
        "numeric_attrs": list(numeric_attrs),
        "stats": stats,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_baseline(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_stats(
    baseline_stats: dict,
    current_stats: dict,
    rate_threshold: float = DEFAULT_RATE_THRESHOLD,
    rel_threshold: float = DEFAULT_REL_THRESHOLD,
) -> list:
    """Compare baseline and current statistics; return over-threshold differences (drift) as a list of findings.

    severity: "warn" = regression (unknown rate up, count down, mean/max sudden change, attribute lost) -> fail CI.
              "info" = sudden change in the improving direction (unknown rate down, count up) or a new attribute -> record only (visualize progress).
    Thresholds: unknown_rate uses an **absolute diff** (rate_threshold); total/mean/max use a **relative change** (rel_threshold).
    """
    findings: list = []

    def add(severity, section, key, metric, base, now, detail):
        findings.append({
            "severity": severity, "section": section, "key": key,
            "metric": metric, "base": base, "now": now, "detail": detail,
        })

    def rel_change(base, now):
        """Relative change. None when the base is 0/None (not comparable)."""
        if base is None or now is None or base == 0:
            return None
        return (now - base) / abs(base)

    for section in ("numeric", "categorical"):
        b_sec = baseline_stats.get(section) or {}
        c_sec = current_stats.get(section) or {}
        for key in sorted(set(b_sec) | set(c_sec)):
            b, c = b_sec.get(key), c_sec.get(key)
            if b is None:
                add("info", section, key, "new", None, c.get("total"),
                    "New attribute/codelist not in baseline")
                continue
            if c is None:
                add("warn", section, key, "missing", b.get("total"), None,
                    "Attribute/codelist present in baseline but missing in current")
                continue

            d_rate = c["unknown_rate"] - b["unknown_rate"]
            if abs(d_rate) > rate_threshold:
                sev = "warn" if d_rate > 0 else "info"  # unknown rate up = regression, down = progress
                add(sev, section, key, "unknown_rate", b["unknown_rate"], c["unknown_rate"],
                    f"Unknown rate {b['unknown_rate']:.1%} → {c['unknown_rate']:.1%} "
                    f"({'+' if d_rate > 0 else ''}{d_rate:.1%})")

            r_total = rel_change(b["total"], c["total"])
            if r_total is not None and abs(r_total) > rel_threshold:
                sev = "warn" if r_total < 0 else "info"  # count decrease = suspected data loss
                add(sev, section, key, "total", b["total"], c["total"],
                    f"Count {b['total']} → {c['total']} ({r_total:+.1%})")

            if section == "numeric":
                for metric in ("mean", "max"):
                    bv, cv = b.get(metric), c.get(metric)
                    if (bv is None) != (cv is None):
                        add("warn", section, key, metric, bv, cv,
                            f"{metric} {bv} → {cv} (valid value presence changed)")
                        continue
                    r = rel_change(bv, cv)
                    if r is not None and abs(r) > rel_threshold:
                        add("warn", section, key, metric, bv, cv,
                            f"{metric} {bv} → {cv} ({r:+.1%}·sudden change)")
    return findings


def render_drift_markdown(findings: list, baseline_doc: dict,
                          rate_threshold: float, rel_threshold: float) -> str:
    """Render drift findings as Markdown (warn = regression, info = progress/new)."""
    warns = [f for f in findings if f["severity"] == "warn"]
    infos = [f for f in findings if f["severity"] == "info"]
    lines = ["## 📈 Data Quality Drift Detection (Baseline Comparison)", "",
             f"Baseline: {baseline_doc.get('generated', '?')} ({baseline_doc.get('n_files', '?')} files) "
             f"· Thresholds: unknown rate absolute diff {rate_threshold:.0%} / count·mean·max relative {rel_threshold:.0%}", ""]
    if not findings:
        lines += ["✅ No threshold-exceeding changes (equivalent to baseline).", ""]
        return "\n".join(lines).rstrip() + "\n"
    if warns:
        lines.append(f"**⚠️ {len(warns)} regression(s)** (review required·CI failure)")
        for f in warns:
            lines.append(f"- ⚠️ [{f['section']}] **{f['key']}**: {f['detail']}")
        lines.append("")
    if infos:
        lines.append(f"**ℹ️ {len(infos)} improvement(s)/new** (logged only)")
        for f in infos:
            lines.append(f"- ℹ️ [{f['section']}] **{f['key']}**: {f['detail']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(stats: dict) -> str:
    lines = ["## 📊 Data Quality Metrics (Sentinels Excluded, Unknown Rate)", ""]
    if stats["numeric"]:
        lines += ["### Numeric Attributes", "", "| Attribute | Valid | Unknown (Sentinels) | Unknown Rate | Mean | Min | Max |",
                  "|---|--:|--:|--:|--:|--:|--:|"]
        for a, s in sorted(stats["numeric"].items()):
            lines.append(f"| {a} | {s['valid']} | {s['sentinel']} | {s['unknown_rate']:.1%} | "
                         f"{s['mean']} | {s['min']} | {s['max']} |")
        lines.append("")
    if stats["categorical"]:
        lines += ["### Code Attributes (Unknown Rate)", "", "| Codelist | Total | Unknown | Unknown Rate |", "|---|--:|--:|--:|"]
        for name, s in sorted(stats["categorical"].items(), key=lambda kv: -kv[1]["unknown_rate"]):
            lines.append(f"| {name} | {s['total']} | {s['unknown']} | {s['unknown_rate']:.1%} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("files", nargs="+", type=Path, help="Target .gml file(s)")
    p.add_argument("--codelists", type=Path, default=None, help="Codelists directory (code unknown rate, quality profile)")
    p.add_argument("--numeric", nargs="+", default=list(DEFAULT_NUMERIC), help="Numeric attributes (localname) to collect statistics for")
    p.add_argument("--profile", action="store_true",
                   help="Output quality profile (DataQuality maturity tier distribution) (requires --codelists)")
    p.add_argument("--detail", action="store_true",
                   help="Expand per-code counts, rates, and tiers in --profile output")
    p.add_argument("--all-codes", action="store_true",
                   help="Output all codes for all coded attributes with counts and rates by element × codelist (verbose; includes non-quality-axes)")
    p.add_argument("--save-baseline", type=Path, default=None,
                   help="Save statistics as baseline JSON (reference point for drift detection #22)")
    p.add_argument("--baseline", type=Path, default=None,
                   help="Compare with baseline JSON and report threshold-exceeding differences (exit 1 if degraded)")
    p.add_argument("--rate-threshold", type=float, default=DEFAULT_RATE_THRESHOLD,
                   help=f"Absolute difference threshold for unknown rate (default {DEFAULT_RATE_THRESHOLD})")
    p.add_argument("--rel-threshold", type=float, default=DEFAULT_REL_THRESHOLD,
                   help=f"Relative change threshold for count/mean/max (default {DEFAULT_REL_THRESHOLD})")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.save_baseline and args.baseline:
        p.error("--save-baseline and --baseline cannot be specified together (choose save or compare).")

    if args.all_codes:
        recs = code_distribution(args.files, args.codelists)
        if args.json:
            json.dump(recs, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_code_distribution_markdown(recs))
        return 0

    if args.profile:
        if not args.codelists:
            p.error("--profile requires --codelists")
        prof = quality_profile(args.files, args.codelists)
        if args.json:
            json.dump(prof, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_profile_markdown(prof, detail=args.detail))
        return 0

    stats = stats_for_files(args.files, tuple(args.numeric), args.codelists)

    if args.save_baseline:
        save_baseline(args.save_baseline, stats, args.numeric, len(args.files))
        print(f"Baseline saved: {args.save_baseline}")
        return 0

    if args.baseline:
        doc = load_baseline(args.baseline)
        findings = compare_stats(doc["stats"], stats, args.rate_threshold, args.rel_threshold)
        if args.json:
            json.dump({"findings": findings}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_drift_markdown(findings, doc, args.rate_threshold, args.rel_threshold))
        return 1 if any(f["severity"] == "warn" for f in findings) else 0

    if args.json:
        json.dump(stats, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_markdown(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
