#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""PR classification — Exchange Contract A5, the single source of truth.

CI (ci/pr_analysis_main.sh, ci/inspection_summary.sh) and review clients import
this module instead of carrying their own copy of the table, so the contract,
the gate and the screens cannot drift apart. The table below mirrors the A5
table of docs/exchange-contract.md; tests/test_exchange_contract.py checks
that every literal here appears in the document.

Classes
  attribute / texture / geometry  — declared by branch prefix (preferred) or title
  administrative                  — declared by commit trailers (Change-Type / Provenance-Manifest)
  other                           — the PR changes no data files
  unclassified                    — a data PR that matches nothing: rejected with guidance

Usage (CLI)
  pr_classification.py --branch B --title T --data-changed true|false --administrative true|false
  prints the class; add --print kind to print the class used for check scoping
  (attribute for everything that is not attribute/texture/geometry).
  --guide [--lang xx] [--catalog path.json] prints the guidance Markdown.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

NAME_CLASSES = ("attribute", "texture", "geometry")
ADMINISTRATIVE = "administrative"
OTHER = "other"
UNCLASSIFIED = "unclassified"

# A5 table. Branch prefix wins over title; title prefixes are exact and
# front-anchored; keywords match anywhere in the title. ja/de literals match
# generated repo-language titles and contributor input — do not translate.
BRANCH_PREFIXES = {
    "attribute": ("edit/",),
    "texture": ("tex/",),
    "geometry": ("geom/", "geometry/"),
}
TITLE_PREFIXES = {
    "attribute": ("Update attributes", "Update building info", "属性修正", "Attributkorrektur"),
    "texture": ("Update textures", "Add textures", "テクスチャ", "Textur"),
    "geometry": (),
}
TITLE_KEYWORDS = {
    "attribute": (),
    "texture": (),
    "geometry": ("geometry", "building shape", "rebuild", "幾何", "建物形状", "建替", "建て替"),
}


def classify_by_name(branch: str, title: str) -> "str | None":
    """attribute / texture / geometry from the branch name or the title, else None."""
    branch = str(branch or "")
    title = str(title or "")
    for cls in NAME_CLASSES:
        if branch.startswith(BRANCH_PREFIXES[cls]):
            return cls
    for cls in NAME_CLASSES:
        if TITLE_PREFIXES[cls] and title.startswith(TITLE_PREFIXES[cls]):
            return cls
    for cls in NAME_CLASSES:
        if any(word in title for word in TITLE_KEYWORDS[cls]):
            return cls
    return None


def classify(branch: str, title: str, data_changed: bool, administrative: bool = False) -> str:
    """The PR's class under A5. Never returns None: an unclassifiable data PR is `unclassified`."""
    named = classify_by_name(branch, title)
    if named:
        return named
    if administrative:
        return ADMINISTRATIVE
    if not data_changed:
        return OTHER
    return UNCLASSIFIED


def kind_for_checks(cls: "str | None") -> str:
    """The class the gates scope their checks by (topology scope etc.)."""
    return cls if cls in NAME_CLASSES else "attribute"


def _to_bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes")


def load_catalog(lang: str, tools_dir: "Path | str | None" = None) -> dict:
    """The CI display catalog for lang (fail-open: {} for en or on any problem)."""
    try:
        lang = str(lang or "").split("-")[0].strip().lower()
        if not lang or lang == "en":
            return {}
        root = Path(tools_dir) if tools_dir else Path(__file__).resolve().parent.parent
        data = json.loads((root / "tools" / "i18n" / "catalogs" / "ci" / f"{lang}.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _code_list(items) -> str:
    return ", ".join(f"`{x}`" for x in items) if items else "—"


def guide_markdown(catalog: "dict | None" = None, advisory: bool = False) -> str:
    """The guidance posted when a data PR cannot be classified (A5).

    Machine-stable parts (class names, prefixes, keywords) are code spans; the
    prose localizes through the CI catalog. With advisory=True the heading says
    the requirement is announced rather than enforced (transition release)."""
    cat = catalog or {}

    def T(key, default):
        value = cat.get(key)
        return value if isinstance(value, str) and value else default

    heading = (T("ci.classify_advisory_heading", "Advisory: this proposal will need a classification")
               if advisory else T("ci.classify_heading", "How to classify this proposal"))
    lines = [
        "### " + heading,
        "",
        T("ci.classify_intro",
          "The automated checks could not tell what kind of change this proposal is. "
          "Every proposal that changes data files must say so in its branch name or its title. "
          "Pick the matching row and apply either fix."),
        "",
        "| " + T("ci.classify_col_class", "Kind of change") + " | "
        + T("ci.classify_col_branch", "Branch name starts with") + " | "
        + T("ci.classify_col_title", "Or the title starts with / contains") + " |",
        "|---|---|---|",
    ]
    labels = {
        "attribute": T("ci.classify_attribute", "Attribute values (storeys, usage, height …)"),
        "texture": T("ci.classify_texture", "Textures and photos"),
        "geometry": T("ci.classify_geometry", "Shape, position, rebuild"),
    }
    for cls in NAME_CLASSES:
        title_forms = _code_list(TITLE_PREFIXES[cls] + TITLE_KEYWORDS[cls])
        lines.append(f"| {labels[cls]} (`{cls}`) | {_code_list(BRANCH_PREFIXES[cls])} | {title_forms} |")
    lines += [
        "",
        "1. " + T("ci.classify_fix_branch",
                  "Rename the branch so it starts with the prefix, for example "
                  "`git branch -m edit/<building-id>-<date>` and push it again; or"),
        "2. " + T("ci.classify_fix_title",
                  "Edit the pull request title so it starts with one of the title forms. "
                  "Editing the title re-runs the checks without a new commit."),
        "",
        T("ci.classify_other",
          "Proposals that change no data files (documentation, code, tool pins) need no classification. "
          "Bulk conversions declare themselves with commit trailers instead."),
        "",
        T("ci.classify_contract", "Full rules: Exchange Contract, section A5."),
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--branch", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--data-changed", default="true")
    parser.add_argument("--administrative", default="false")
    parser.add_argument("--print", dest="what", choices=("class", "kind"), default="class")
    parser.add_argument("--guide", action="store_true", help="print the guidance Markdown and exit")
    parser.add_argument("--advisory", action="store_true", help="with --guide: advisory wording")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--tools-dir", default=None)
    args = parser.parse_args(argv)
    if args.guide:
        print(guide_markdown(load_catalog(args.lang, args.tools_dir), advisory=args.advisory))
        return 0
    cls = classify(args.branch, args.title, _to_bool(args.data_changed), _to_bool(args.administrative))
    print(kind_for_checks(cls) if args.what == "kind" else cls)
    return 1 if cls == UNCLASSIFIED and args.what == "class" else 0


if __name__ == "__main__":
    sys.exit(main())
