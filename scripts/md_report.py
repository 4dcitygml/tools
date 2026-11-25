#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The Markdown reports the gates post as PR comments: one shape, one limit, one list reader (U7).

    report(marker, title, body)   -> the comment: the marker, the title, the body; "" when the body is
                                     empty (a clean check posts nothing); never longer than COMMENT_LIMIT
    capped(items, cap)            -> (the first cap items, how many more)
    changed_list(path, suffix)    -> the paths of a changed-files list (one per line) with that suffix

The driver renders a step's verdict and captured output (the last --tail bytes) as a report
through the command line, the marker named as in scripts/pr_markers.py (U3: no marker literal and
no Markdown in the shell):

    python3 scripts/md_report.py log --marker COMMIT_SCOPE --title T --rc "$rc" --ok TEXT --fail TEXT --log FILE
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Bytes of a comment body: GitHub refuses one over 65,536 characters, and the city's poster cuts at
# the same limit (its own post_comments.COMMENT_LIMIT, an intended duplicate: tests/test_city_workflows).
COMMENT_LIMIT = 60000
CUT_NOTE = ("\n<sub>⚠️ Shortened to fit the GitHub comment size limit; the full report is in the "
            "analysis run's artifact.</sub>\n")


def report(marker: str, title: str, body: "list[str]") -> str:
    if not body:
        return ""
    return fit("\n".join([marker, f"## {title}", "", *body]).rstrip() + "\n")


def fit(text: str, limit: int = COMMENT_LIMIT) -> str:
    """text, or its leading whole lines and a note when it would pass the limit (D-c2)."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    kept = data[:limit - len(CUT_NOTE.encode("utf-8"))].decode("utf-8", errors="ignore")
    return kept[:kept.rfind("\n") + 1] + CUT_NOTE


def capped(items: list, cap: int) -> "tuple[list, int]":
    return (items, 0) if len(items) <= cap else (items[:cap], len(items) - cap)


def changed_list(path, suffix: str = ".gml") -> "list[str]":
    lines = (ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines())
    return [ln for ln in lines if ln and ln.endswith(suffix)]


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    log = sub.add_parser("log", help="a step's verdict and captured output as its comment")
    log.add_argument("--marker", required=True, help="the marker's name in scripts/pr_markers.py")
    log.add_argument("--title", required=True)
    log.add_argument("--rc", type=int, required=True, help="the step's exit code: 0 is the ok verdict")
    log.add_argument("--ok", required=True)
    log.add_argument("--fail", required=True)
    log.add_argument("--log", type=Path, required=True)
    log.add_argument("--tail", type=int, default=None, help="show only the last bytes of the log")
    args = ap.parse_args(argv)
    try:
        from scripts import pr_markers
    except ImportError:   # run as a script from scripts/
        import pr_markers
    text = args.log.read_text(encoding="utf-8", errors="replace") if args.log.is_file() else ""
    shown = text.encode("utf-8")[-args.tail:].decode("utf-8", errors="ignore") if args.tail else text
    verdict = f"✅ {args.ok}" if args.rc == 0 else f"❌ {args.fail}"
    sys.stdout.write(report(getattr(pr_markers, args.marker), args.title,
                            [verdict, "", "```text", shown.rstrip("\n"), "```"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
