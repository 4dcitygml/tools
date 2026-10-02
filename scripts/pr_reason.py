#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The reason section of a pull request body and whether it is filled in (Exchange Contract A1).

One rule for the `reason` row of CI, the hub's preview before CI has posted, and the
attribute editor's pretest; before S5 each had its own copy, and they disagreed (the hub
accepted any text without "please fill in", the editor accepted English placeholders, CI
knew no German heading). Shipped next to the hub as `program/pr_reason.py`
(scripts/build_bundle.py), so it imports nothing from this repository.

    python3 scripts/pr_reason.py "$GITHUB_EVENT_PATH"    # exit 0 filled in, 1 not, 2 error
"""
from __future__ import annotations

import json
import re
import sys

# Headings of the reason section in a body without the <!--sec:reason--> anchor: the editors'
# pr.heading_reason / pr.heading_summary in every language (tests keep them in step) and the
# headings of earlier templates. Japanese and German literals match contributor input.
HEADINGS = (
    "Reason and supporting evidence", "Summary of changes",
    "編集理由・根拠資料", "変更理由", "変更の理由", "変更の概要",
    "Begründung und Belege", "Zusammenfassung der Änderungen",
)
# Template text a proposer did not replace, in each language the tools speak (en, ja, de).
# Japanese and German literals match contributor input.
PLACEHOLDERS = ("please fill in", "not filled in", "記入してください", "未記入", "bitte ausfüllen", "nicht ausgefüllt",
                "TODO", "TBD")
MIN_LENGTH = 5

_ANCHORED = re.compile(r"^##[^\n]*<!--\s*sec:reason\s*-->[^\n]*$\n(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
_HEADED = re.compile(r"^##\s+(?:" + "|".join(map(re.escape, HEADINGS)) + r")\s*$\n(.*?)(?=^##\s+|\Z)",
                     re.MULTILINE | re.DOTALL)


def section(body: str) -> str:
    """The reason section's text without HTML comments; empty when the body has none."""
    match = _ANCHORED.search(body or "") or _HEADED.search(body or "")
    text = match.group(1) if match else ""
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def filled(reason: str) -> bool:
    """Whether a reason text says something: long enough and not a placeholder."""
    text = (reason or "").strip()
    return len(text) >= MIN_LENGTH and not any(p.lower() in text.lower() for p in PLACEHOLDERS)


def acceptable(body: str) -> bool:
    return filled(section(body))


def main(argv: list[str]) -> int:
    event = json.load(open(argv[0], encoding="utf-8"))
    if acceptable(str(event.get("pull_request", {}).get("body") or "")):
        print("Description and evidence: OK")
        return 0
    print("::warning::Please describe the reason for the change and the supporting evidence. "
          "CI will comment with items to confirm.")
    return 1


if __name__ == "__main__":
    # the gate exit convention (scripts/gate_result.guarded), inline: this file ships alone with the hub
    try:
        code = main(sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        code = 2
    sys.exit(code)
