#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""PR comment hidden metadata (W4 / feature list D-3).

Assists context reconstruction during audits by storing PR number, branch, base/head SHA, changed files,
and preview-url as **hidden HTML comments** in the PR comment (supplementary to reviewability condition 3;
condition 3 is mostly handled by GitHub's standard history).

To avoid breakage from `--` inside HTML comments, the payload is **standard-base64 encoded**
(`+ / =` and alphanumeric only, no `-`) to structurally avoid it
(satisfies D-3's "`--` escaping" requirement). Decoding recovers the original JSON.

Usage:
    python scripts/pr_comment_metadata.py --pr 1 --branch demo/x \\
        --base-sha B --head-sha H --file-list changed.txt [--preview-url URL]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.citygml_constants import METADATA_MARKER  # noqa: E402

# Hidden payload line. base64 contains no `-`, so it cannot break the HTML comment.
_PAYLOAD_RE = re.compile(r"<!-- citygml-meta:([A-Za-z0-9+/=]+) -->")


def build_metadata(
    pr: int,
    branch: str,
    base_sha: str,
    head_sha: str,
    changed_files: list[str],
    preview_url: Optional[str] = None,
) -> dict:
    """Assemble the metadata dict to store."""
    meta = {
        "pr": pr,
        "branch": branch,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "changed_files": changed_files,
    }
    if preview_url:
        meta["preview_url"] = preview_url
    return meta


def _encode(meta: dict) -> str:
    raw = json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def render_comment(meta: dict) -> str:
    """Return the comment body with marker (small visible note + hidden payload)."""
    lines = [
        METADATA_MARKER,
        "<sub>🔖 Audit metadata (change context stored in this comment)</sub>",
        f"<!-- citygml-meta:{_encode(meta)} -->",
    ]
    return "\n".join(lines) + "\n"


def parse_comment(body: str) -> Optional[dict]:
    """Recover the hidden metadata from a comment body (for audit tooling/tests)."""
    m = _PAYLOAD_RE.search(body)
    if not m:
        return None
    return json.loads(base64.b64decode(m.group(1)).decode("utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--base-sha", required=True)
    p.add_argument("--head-sha", required=True)
    p.add_argument("--file-list", type=Path, required=True, help="File listing changed .gml files")
    p.add_argument("--preview-url", default=None)
    args = p.parse_args(argv)

    changed = [
        line.strip()
        for line in args.file_list.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    meta = build_metadata(
        args.pr, args.branch, args.base_sha, args.head_sha, changed, args.preview_url
    )
    sys.stdout.write(render_comment(meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
