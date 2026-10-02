#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""One result per inspection gate, in one form (S17).

Every gate of the analysis driver ends with an exit code under one convention -
0 pass, 1 finding, 2 or more error (the gate could not judge: a crash, git, the network,
a missing input) - and this module turns it into the row of the inspection summary:

    blocking gate: 0 -> pass, 1 -> fail, >=2 -> error
    advisory gate: 0 -> pass, 1 -> warn, >=2 -> error
    not applicable -> na; applicable but no result recorded -> pending

`fail`, `error` and `pending` block the merge under STRICT_GATE; `warn` does not. The table
ROWS is the one place that says which rows exist, in which order, and which of them block
(maintainer decision, 2026-09-30). The driver writes out/gates/<key>.json through the CLI;
the inspection summary reads only those files. They stay in the runner's temporary folder: the
analysis artifact the trusted side receives carries only the summary's inspection.json.

    python3 scripts/gate_result.py write --key schema --exit-code 1 [--summary TEXT] [--out "$RUNNER_TEMP"]
    python3 scripts/gate_result.py write --key model --not-applicable
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

BLOCKING, ADVISORY = "blocking", "advisory"


@dataclass(frozen=True)
class Row:
    key: str             # the <!--cp:key--> anchor of the inspection comment (Exchange Contract A6)
    label_key: str       # ci catalog key of the display name
    label: str           # English display name
    severity: str        # BLOCKING or ADVISORY


ROWS: tuple[Row, ...] = (
    Row("reason", "ci.check_reason", "Description and evidence", BLOCKING),
    Row("classification", "ci.check_classification", "Change classification", BLOCKING),
    Row("commit-scope", "ci.check_commit_scope", "One change = one building", BLOCKING),
    Row("scope-reproducibility", "ci.check_scope_reproducibility", "Ward extraction reproducibility", BLOCKING),
    Row("reproduction", "ci.check_reproduction", "Bulk conversion reproduction", BLOCKING),
    Row("freshness", "ci.check_freshness", "Consistency with the latest version", BLOCKING),
    Row("file-scope", "ci.check_file_scope", "Changed file scope", BLOCKING),
    Row("schema", "ci.check_schema", "CityGML format", BLOCKING),
    Row("minimal-diff", "ci.check_minimal_diff", "Minimal diff", ADVISORY),
    Row("texture", "ci.check_texture", "Texture consistency", BLOCKING),
    Row("structure", "ci.check_structure", "Geometric structure", BLOCKING),
    Row("plausibility", "ci.check_plausibility", "Attribute value plausibility", ADVISORY),
    Row("topology", "ci.check_topology", "Topological consistency", ADVISORY),
    Row("model", "ci.check_model", "3D view", ADVISORY),
)
BY_KEY = {row.key: row for row in ROWS}
STATUSES = ("pass", "warn", "fail", "error", "na", "pending")
BLOCKS = frozenset({"fail", "error", "pending"})     # what STRICT_GATE refuses


def status_for(key: str, exit_code: int | None, applicable: bool = True, warnings: int = 0) -> str:
    """The row status of a gate from its exit code (None: applicable but nothing recorded).
    A clean exit with warnings is `warn` for any gate: a blocking gate's warnings do not block
    but show in the row."""
    if not applicable:
        return "na"
    if exit_code is None:
        return "pending"
    if exit_code == 0:
        return "warn" if warnings else "pass"
    if exit_code == 1:
        return "fail" if BY_KEY[key].severity == BLOCKING else "warn"
    return "error"


def result(key: str, exit_code: int | None, applicable: bool = True, summary: str = "", warnings: int = 0) -> dict:
    status = status_for(key, exit_code, applicable, warnings)
    return {
        "key": key,
        "status": status,
        "severity": BY_KEY[key].severity,
        # the gate reached a result (D5, TOOLS-REPLY-2026-09-30-d5-keys.md)
        "ran": status in ("pass", "warn", "fail"),
        "exitCode": exit_code,
        "warnings": warnings,
        "summary": summary,
    }


def write(out: Path, entry: dict) -> Path:
    path = out / "gates" / f"{entry['key']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read(out: Path, key: str) -> dict | None:
    path = out / "gates" / f"{key}.json"
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) and entry.get("status") in STATUSES else None


# The parsers' errors for a file that is not well-formed XML (lxml, ElementTree, expat).
MALFORMED = frozenset({"XMLSyntaxError", "ParseError", "ExpatError"})


def malformed_input(exc: BaseException) -> bool:
    """Whether an exception says the submitted file is not well-formed XML: that is a finding
    in the data (the schema row names it), not a failure of the check."""
    return any(cls.__name__ in MALFORMED for cls in type(exc).__mro__)


def guarded(main_fn) -> int:
    """Run a gate's main under the exit convention: its own 0/1 (and >=2) pass through, an
    unexpected exception becomes 2. Python exits 1 on an exception, which CI read as a finding
    (D16). A changed file that is not well-formed XML is a finding (1), not an error."""
    try:
        return int(main_fn() or 0)
    except Exception as exc:  # noqa: BLE001
        if malformed_input(exc):
            print(f"finding: a file is not well-formed XML: {exc}", file=sys.stderr)
            return 1
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("write", help="record one gate's result as out/gates/<key>.json")
    w.add_argument("--key", required=True, choices=sorted(BY_KEY))
    w.add_argument("--exit-code", type=int)
    w.add_argument("--not-applicable", action="store_true")
    w.add_argument("--summary", default="")
    w.add_argument("--warnings", type=int, default=0, help="warnings of a clean run: the row shows warn")
    w.add_argument("--counts-json", type=Path, help="a lint's {errors, warnings}: the warnings count is read from it")
    w.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args(argv)
    warnings = args.warnings
    if args.counts_json is not None:
        try:
            warnings = int(json.loads(args.counts_json.read_text(encoding="utf-8")).get("warnings") or 0)
        except (OSError, ValueError):
            pass    # no counts: the exit code alone decides
    entry = result(args.key, None if args.not_applicable else args.exit_code, not args.not_applicable,
                   args.summary, warnings)
    write(args.out, entry)
    print(f"{args.key}: {entry['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
