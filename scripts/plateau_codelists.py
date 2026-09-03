#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Load PLATEAU codelists and exhaustively generate "unknown-value codes" (categorical part of sentinel list).

PLATEAU encodes values of classification attributes (e.g., `bldg:usage`) and references bundled codelists
(gml:Dictionary in `codelists/*.xml`) via `codeSpace`. Codelists include codes representing "**unknown**"
(e.g., `08 = unknown` in `Bridge_function`). This is the **categorical equivalent of numeric sentinels (±9999)**
and is the source material to **exhaustively generate** the unified definition of "unknown" (`scripts/sentinels.py`) from conventions.

Use cases:
- **Lint (`plateau_lint`)**: verify code values exist in codelists (codelist consistency). Unknown codes are "unknown" = not anomalous.
- **Statistics (`data_stats`)**: compute distribution and "unknown rate" excluding unknown codes.
- **Monitoring**: unknown rate (unknown code ratio) over time = maintenance progress indicator.

Picks up "unknown/unspecified/unknown"; does **not pick up** "**other**" (valid classification) (avoid over-exclusion).

Usage:
    python scripts/plateau_codelists.py CODELISTS_DIR --unknowns   # output unknown code list
    python scripts/plateau_codelists.py CODELISTS_DIR --summary    # count summary
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.safe_xml import safe_parse  # noqa: E402


def is_unknown_label(label: str) -> bool:
    """True if the label denotes "unknown". Legitimate classifications such as "other" are False."""
    if not label:
        return False
    low = label.lower()
    return ("不明" in label) or ("不詳" in label) or ("unknown" in low)


# Quality tiers (ordinal scale, low→high). Classified by label keywords (heuristic based on PLATEAU conventions).
# missing (not created/unknown/unobtainable) < provisional (estimated/pseudo/tentative) < documented (drawings/registers/documents) < measured (survey/actual measurement/point cloud).
TIER_ORDER = {"missing": 0, "provisional": 1, "documented": 2, "measured": 3, "other": -1}
TIER_NAMES = ("measured", "documented", "provisional", "missing", "other")
_TIER_KEYWORDS = (
    # Evaluation order is the priority (top-down; missing takes precedence).
    ("missing", ("未作成", "不明", "不詳", "取得不可", "定義なし", "未設定", "該当なし")),
    ("provisional", ("推定", "疑似", "仮", "一律", "概略", "みなし")),
    ("measured", ("測量", "実測", "点群", "レーザ", "写真測量", "現地測量", "数値実測", "LidarSLAM")),
    ("documented", ("図面", "ＣＡＤ", "CAD", "設計図", "完成図", "一般図", "台帳", "図書",
                    "既成図", "数値化", "地図編集", "統計", "資料", "現地調査", "写真判読",
                    "ＢＩＭ", "BIM", "基盤地図", "数値地形図", "図化", "GIS",
                    "写真", "カメラ", "衛星")),
)


def quality_tier(label: str) -> str:
    """Classify a label into a quality tier (measured/documented/provisional/missing/other).

    Heuristic corresponding to the ordinal scale of staged data maturation:
    "survey/actual measurement → drawings/registers → estimated/pseudo → not created/unknown".
    Used to compute maturity profiles of DataQuality-type codelists.
    """
    if not label:
        return "other"
    for tier, kws in _TIER_KEYWORDS:
        if any(k in label for k in kws):
            return tier
    return "other"


import re as _re  # noqa: E402

_QUALITY_AXIS_PAT = _re.compile(
    r"SrcDesc|HeightType|srcScale|MapLevel|precisionType|_status|validType|yearType|lodType|_scale",
    _re.IGNORECASE,
)


def is_quality_axis(codelist_name: str) -> bool:
    """True if the codelist name is a "quality axis" type (DataQuality/precision/status/completeness)."""
    return bool(_QUALITY_AXIS_PAT.search(codelist_name))


def _localname(elem) -> str:
    if not isinstance(elem.tag, str):  # skip comments/PIs
        return ""
    return etree.QName(elem).localname


def load_codelist(path: Path) -> dict:
    """Read a single codelist (gml:Dictionary) as {code: label}."""
    out: dict = {}
    tree = safe_parse(str(path))
    for defn in tree.getroot().iter():
        if _localname(defn) != "Definition":
            continue
        code = label = None
        for c in defn:
            ln = _localname(c)
            if ln == "name":
                code = (c.text or "").strip()
            elif ln == "description":
                label = (c.text or "").strip()
        if code is not None:
            out[code] = label or ""
    return out


def load_codelists(codelists_dir: Path) -> dict:
    """Read all *.xml in the codelists directory as {filename: {code: label}}."""
    result: dict = {}
    for p in sorted(Path(codelists_dir).glob("*.xml")):
        try:
            result[p.name] = load_codelist(p)
        except etree.XMLSyntaxError:
            continue
    return result


def unknown_codes(codelists_dir: Path) -> dict:
    """{filename: frozenset(unknown codes)}. Only includes codelists that have unknown codes."""
    out: dict = {}
    for name, table in load_codelists(codelists_dir).items():
        codes = frozenset(code for code, label in table.items() if is_unknown_label(label))
        if codes:
            out[name] = codes
    return out


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("codelists_dir", type=Path, help="PLATEAU codelists directory")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--unknowns", action="store_true", help="Output unknown code list")
    mode.add_argument("--summary", action="store_true", help="Output count summary only")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    args = p.parse_args(argv)

    unk = unknown_codes(args.codelists_dir)
    total_lists = len(list(Path(args.codelists_dir).glob("*.xml")))
    total_codes = sum(len(c) for c in unk.values())

    if args.summary or (not args.unknowns and not args.json):
        print(f"Total codelists: {total_lists}")
        print(f"Codelists with unknown codes: {len(unk)}")
        print(f"Total unknown codes: {total_codes}")
        return 0
    if args.json:
        json.dump({k: sorted(v) for k, v in unk.items()}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for name in sorted(unk):
            print(f"{name}: {', '.join(sorted(unk[name]))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
