#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Count coded values that still point at an older edition's code list
(``codeSpace`` under ``codelists/<edition>/``) — the codes a carry-forward kept
with their old codeSpace pending a reviewer's decision.

Release gate use: an official export should not carry such values unless the
official channel accepts them; this report lists what is left, per edition,
list, and code. Exit code 1 when ``--max`` is exceeded.

Usage:
    python3 scripts/carried_codespace_report.py FILE.gml [FILE2.gml ...] [--max 0] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_LEAF_RE = re.compile(rb'<(?:\w+:)?([A-Za-z0-9_]+)\b[^>]*\scodeSpace="([^"]*codelists/(iur-[0-9.]+|citygml-[0-9.]+)/([^"/]+))"[^>]*>([^<]*)<')


def scan(path: Path) -> list[dict]:
    raw = path.read_bytes()
    rows = []
    for m in _LEAF_RE.finditer(raw):
        rows.append({"file": str(path), "attribute": m.group(1).decode(), "edition": m.group(3).decode(),
                     "list": m.group(4).decode(), "code": m.group(5).decode().strip()})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--max", type=int, default=None, help="fail when more carried values than this remain")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = [r for f in args.files for r in scan(f)]
    by = Counter((r["edition"], r["list"], r["code"]) for r in rows)
    if args.json:
        print(json.dumps({"total": len(rows), "by": [{"edition": e, "list": l, "code": c, "count": n} for (e, l, c), n in sorted(by.items())]}, ensure_ascii=False, indent=1))
    else:
        print(f"carried old-codeSpace values: {len(rows)}")
        for (e, l, c), n in sorted(by.items()):
            print(f"  {e}  {l}  code {c}: {n}")
    if args.max is not None and len(rows) > args.max:
        print(f"::error::{len(rows)} carried values exceed the allowed {args.max}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
