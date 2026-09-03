#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Reconstruct head by applying only semantically changed parts to base (W6 / minimal diff).

A tool to **structurally guarantee** the core reviewability requirement: "when changing the height or
attributes of one building, no other building should appear changed."

## What it does
Compares base (version committed to GitHub) and head (version submitted by proposer) at the building unit level
and outputs a clean version by applying only **semantically changed buildings** to base's byte sequence.

- Buildings/appearance/element order/indentation/line breaks/BOM where meaning hasn't changed: **base bytes as-is**.
  → Even if proposer bulk-converts indentation, reorders elements, or changes line endings, that churn vanishes automatically.
- Among changed buildings:
  - **Attribute-only changes** → Replace only **changed leaf values** within that building (minimal diff).
  - **Changes with geometry** → Wholesale replacement of that building's `core:cityObjectMember` block with head's version
    (**building-block granularity is final = specification**. Any shape change means building-unit change; shape details
    are reviewed in 3D preview. No face-unit minimal diffing or face canonicalization = design limitation).
- Added/deleted buildings → insert/remove at block unit (supports lifecycle like merger = multiple deletes + 1 add, split, etc.).

## Why version-independent
Output is constructed by **concatenating original bytes**, not re-serialized by lxml.
Thus no dependence on lxml/libxml2 version differences (avoids normalize's "canonical form is version-dependent" problem).

## Self-verification (core to this tool's trustworthiness)
After reconstruction, validates with `diff_citygml`: **output == head (semantically identical at building unit)**.
If true:
- No real changes are lost (head's meaning fully preserved).
- AND unchanged buildings are byte-identical to base (untouched).
→ Guarantees "other building appears changed" objections **are structurally impossible**.
If false (tool cannot handle this change form), error out without output, pass to manual review.

Usage:
    # write clean version (auto-fix)
    python scripts/reconstruct_minimal.py BASE.gml HEAD.gml --output CLEAN.gml
    # check if head is already minimal diff (exit 1 if churn exists). For CI gate
    python scripts/reconstruct_minimal.py BASE.gml HEAD.gml --check
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.diff_citygml import diff_sources  # noqa: E402

_COM_OPEN = b"<core:cityObjectMember>"
_COM_CLOSE = b"</core:cityObjectMember>"
_CITYMODEL_CLOSE = b"</core:CityModel>"

# Extract gml:id from a Building (1.0/2.0, any prefix) inside a cityObjectMember.
_BUILDING_ID_RE = re.compile(rb'<(?:\w+:)?Building\b[^>]*?\sgml:id="([^"]+)"')


# --- Byte-span extraction ---------------------------------------------------
def building_spans(raw: bytes) -> dict[str, tuple[int, int]]:
    """Return gml:id -> [start, end) of the core:cityObjectMember containing that building.

    end is just past `</core:cityObjectMember>`. Members without a Building
    (terrain etc.) are out of scope (= left untouched).
    """
    spans: dict[str, tuple[int, int]] = {}
    pos = 0
    while True:
        start = raw.find(_COM_OPEN, pos)
        if start < 0:
            break
        close = raw.find(_COM_CLOSE, start)
        if close < 0:
            break
        end = close + len(_COM_CLOSE)
        m = _BUILDING_ID_RE.search(raw, start, end)
        if m:
            spans[m.group(1).decode("utf-8")] = (start, end)
        pos = end
    return spans


def _tag_localname(path: str) -> str:
    """Extract the element localname from the last segment of an attribute path.

    Examples: '/bldg:storeysAboveGround' -> 'storeysAboveGround',
        '/stringAttribute[@name=x]/value' -> 'value'
    """
    seg = path.rstrip("/").split("/")[-1]
    seg = re.sub(r"\[@name=[^\]]*\]", "", seg)
    seg = re.sub(r"\[\d+\]$", "", seg)
    return seg


def _leaf_replace(span: bytes, tag_local: str, old: str, new: str) -> bytes | None:
    """Replace <…:tag_local …>old</…:tag_local> with new inside span (only when unique).

    - Any prefix is allowed (`bldg:` etc.). Attributes on the open tag (uom etc.) are preserved.
    - If there are 0 or multiple matches (ambiguous), return None (caller falls back to block replacement).
    """
    t = re.escape(tag_local.encode("utf-8"))
    o = re.escape(old.encode("utf-8"))
    pat = re.compile(rb"(<(?:\w+:)?" + t + rb"\b[^>]*>)" + o + rb"(</(?:\w+:)?" + t + rb">)")
    hits = list(pat.finditer(span))
    if len(hits) != 1:
        return None
    n = new.encode("utf-8")
    return pat.sub(lambda m: m.group(1) + n + m.group(2), span, count=1)


def _eol_and_indent(raw: bytes, member_start: int) -> tuple[bytes, bytes]:
    """Infer the EOL and leading indentation (line-start whitespace) of an existing member, for insertion."""
    line_start = raw.rfind(b"\n", 0, member_start) + 1
    indent = raw[line_start:member_start]
    eol = b"\r\n" if b"\r\n" in raw[:member_start] else b"\n"
    return eol, indent


@dataclass
class Result:
    """Reconstruction result. output is the byte string of the minimal-diff clean version."""

    output: bytes
    verified: bool  # whether the output is semantically identical to head per building (self-verification)
    classification: str  # 'none' | 'single' | 'multi-modified' | 'lifecycle' | 'rename'
    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)  # (old_id, new_id) id-only changes
    methods: dict[str, str] = field(default_factory=dict)  # id -> leaf|block|add|delete|rename
    warnings: list[str] = field(default_factory=list)


def classify(n_modified: int, n_added: int, n_deleted: int, n_renamed: int = 0) -> str:
    """Structural classification of changes (count-based; also usable for whole-PR aggregation).

    Enforces the "one coherent change" protocol as structural rules (no semantic inference):
    - none          : no changes.
    - lifecycle     : involves additions/deletions (merge, split, rebuild). Requires a rationale and **human review** (C).
    - multi-modified: 2+ buildings modified and no additions/deletions. **Mechanically rejected** as a violation (B).
    - single        : exactly 1 building modified.
    - rename        : content unchanged, **only the id changed** (content-based rename detection; id-only = effectively no change) -> notice.
    """
    if not (n_modified or n_added or n_deleted or n_renamed):
        return "none"
    if n_added or n_deleted:
        return "lifecycle"
    if n_modified > 1:
        return "multi-modified"
    if n_modified == 1:
        return "single"
    if n_renamed:
        return "rename"
    return "none"


def _classify(modified: list[str], added: list[str], deleted: list[str]) -> str:
    return classify(len(modified), len(added), len(deleted))


def reconstruct(base: bytes, head: bytes) -> Result:
    """Assemble and return a clean version: base with only head's semantic changes applied."""
    diff = diff_sources(base, head, "base", "head", include_unchanged=False)
    base_spans = building_spans(base)
    head_spans = building_spans(head)

    modified: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    renamed: list[tuple[str, str]] = []  # (old_id, new_id) identical content, id-only change
    methods: dict[str, str] = {}
    warnings: list[str] = []

    # Edit operations on the base byte string (start, end, replacement). Insertions have start==end.
    edits: list[tuple[int, int, bytes]] = []

    for entry in diff["buildings"]:
        bid = entry["id"]
        status = entry["status"]

        if status == "modified":
            modified.append(bid)
            bs, be = base_spans[bid]
            geom_changed = entry.get("geometry_changed", False)
            attr_diffs = entry.get("attribute_diffs", [])
            new_span: bytes | None = None
            if not geom_changed:
                # Attributes only: replace just the changed leaves within the base span (minimal diff).
                span = base[bs:be]
                ok = True
                for d in attr_diffs:
                    old, new = d.get("old"), d.get("new")
                    if old is None or new is None:
                        ok = False  # leaf added/removed (structural change) -> fall back to block replacement
                        break
                    replaced = _leaf_replace(span, _tag_localname(d["path"]), old, new)
                    if replaced is None:
                        ok = False  # ambiguous/no match -> fall back to block replacement
                        break
                    span = replaced
                if ok:
                    new_span = span
                    methods[bid] = "leaf"
            if new_span is None:
                # Geometry change or leaf-level replacement impossible -> replace the whole block with head's version.
                if bid not in head_spans:
                    warnings.append(f"{bid}: block not found in head, cannot replace")
                    continue
                hs, he = head_spans[bid]
                new_span = head[hs:he]
                methods[bid] = "block"
            edits.append((bs, be, new_span))

        elif status == "added":
            added.append(bid)
            methods[bid] = "add"
            # Insertions are batched at the anchor in a later pass (only recorded here).

        elif status == "deleted":
            deleted.append(bid)
            methods[bid] = "delete"
            bs, be = base_spans[bid]
            # Also remove the leading indentation and trailing newline so no blank line is left.
            del_start = base.rfind(b"\n", 0, bs) + 1
            del_end = be
            if base[be : be + 2] == b"\r\n":
                del_end = be + 2
            elif base[be : be + 1] == b"\n":
                del_end = be + 1
            edits.append((del_start, del_end, b""))

        elif status == "renamed":
            # Identical content, only the id changed = the minimal diff is "replace only the id string within the base block"
            # (in place; do not move the building. Formatting churn is also absorbed on the base side). Classified as rename.
            old_id = entry["old_id"]
            renamed.append((old_id, bid))
            methods[bid] = "rename"
            if old_id in base_spans:
                bs, be = base_spans[old_id]
                new_span = base[bs:be].replace(
                    old_id.encode("utf-8"), bid.encode("utf-8")
                )
                edits.append((bs, be, new_span))

    # --- Insert new blocks for added buildings (right after the last member, or just before the CityModel close) -------
    insert_ids = added
    if insert_ids:
        last_close = base.rfind(_COM_CLOSE)
        if last_close >= 0:
            anchor = last_close + len(_COM_CLOSE)
            last_open = base.rfind(_COM_OPEN, 0, last_close)
            eol, indent = _eol_and_indent(base, last_open if last_open >= 0 else anchor)
        else:
            cm = base.rfind(_CITYMODEL_CLOSE)
            anchor = cm if cm >= 0 else len(base)
            eol, indent = (b"\r\n" if b"\r\n" in base else b"\n"), b"\t"
        blocks = b""
        for bid in insert_ids:
            if bid not in head_spans:
                warnings.append(f"{bid}: block not found in head, cannot add")
                continue
            hs, he = head_spans[bid]
            blocks += eol + indent + head[hs:he]
        if blocks:
            edits.append((anchor, anchor, blocks))

    # --- appearance note ------------------------------------------------------
    # appearance keeps base's bytes as-is (untouched). Only when buildings are added/deleted/renamed,
    # warn that added/removed texture targets may be left unhandled (e.g. LOD2 merges).
    if added or deleted or renamed:
        warnings.append(
            "Addition/deletion of buildings included: appearance(target) changes unhandled (base unchanged). "
            "For textured buildings, manually verify appearance handling."
        )

    # --- Apply edits (splice from the end to avoid offset drift) -------------------
    output = base
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        output = output[:start] + repl + output[end:]

    # --- Self-verification: is the output semantically identical to head per building? ----------------------
    check = diff_sources(head, output, "head", "reconstructed", include_unchanged=False)
    verified = len(check["buildings"]) == 0
    if not verified:
        ids = ", ".join(b["id"] for b in check["buildings"][:5])
        warnings.append(
            f"Self-verification failed: output not semantically identical to head ({len(check['buildings'])} diffs: {ids}). "
            "Tool cannot minimize this change type; refer to manual review."
        )

    return Result(
        output=output,
        verified=verified,
        classification=classify(len(modified), len(added), len(deleted), len(renamed)),
        modified=modified,
        added=added,
        deleted=deleted,
        renamed=renamed,
        methods=methods,
        warnings=warnings,
    )


def _print_report(r: Result, base: bytes, head: bytes) -> None:
    churn = head != r.output
    print(f"classification : {r.classification}", file=sys.stderr)
    print(
        f"modified={len(r.modified)} added={len(r.added)} deleted={len(r.deleted)} "
        f"methods={r.methods}",
        file=sys.stderr,
    )
    print(f"verified(head==reconstructed): {r.verified}", file=sys.stderr)
    print(
        f"churn_removed(head!=reconstructed): {churn} "
        f"(head {len(head)}B -> clean {len(r.output)}B)",
        file=sys.stderr,
    )
    for w in r.warnings:
        print(f"  ! {w}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("base", type=Path, help="base (committed) .gml")
    p.add_argument("head", type=Path, help="head (proposed) .gml")
    p.add_argument("--output", type=Path, help="output path for clean version (omit to not write)")
    p.add_argument(
        "--check",
        action="store_true",
        help="check if head is already minimal diff; exit 1 if churn exists (do not auto-fix)",
    )
    args = p.parse_args(argv)

    base = args.base.read_bytes()
    head = args.head.read_bytes()
    r = reconstruct(base, head)
    _print_report(r, base, head)

    # Emit per-file counts + building IDs to stdout for whole-PR scope determination
    # (CI unions the IDs across all files and classifies them together with appearance-changed buildings (a)).
    print(
        f"COUNTS modified={len(r.modified)} added={len(r.added)} "
        f"deleted={len(r.deleted)} renamed={len(r.renamed)} classification={r.classification}"
    )
    for bid in r.modified:
        print(f"BLDG modified {bid}")
    for bid in r.added:
        print(f"BLDG added {bid}")
    for bid in r.deleted:
        print(f"BLDG deleted {bid}")
    for old_id, new_id in r.renamed:
        print(f"BLDG renamed {new_id} {old_id}")

    if not r.verified:
        # Semantics cannot be preserved = this tool cannot handle it. Error out without writing.
        return 2

    if args.check:
        if head != r.output:
            print("check: churn present (minimal diff conversion needed)", file=sys.stderr)
            return 1
        print("check: already minimal diff (no churn)", file=sys.stderr)
        return 0

    if args.output is not None:
        args.output.write_bytes(r.output)
        print(f"written: {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
