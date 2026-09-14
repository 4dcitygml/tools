# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Guard: the test suite must never change the developer's own files.

Snapshots the real settings, account and git files under HOME before the session
and compares them afterwards. A change is reported as a failure and the originals
are put back, so a test that forgot to redirect a path cannot leave a trace on
the computer that runs the tests.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

GUARDED = [
    Path.home() / ".citygml_attr_editor.json",
    Path.home() / ".citygml_attr_editor.json.lock",
    Path.home() / ".citygml_auth.json",
    Path.home() / ".citygml_git_credentials",
    Path.home() / ".gitconfig",
]
GUARDED_DIRS = [Path.home() / ".citygml"]


def _snapshot() -> dict:
    snap = {}
    for p in GUARDED:
        snap[str(p)] = p.read_bytes() if p.is_file() else None
    for d in GUARDED_DIRS:
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    snap[str(p)] = p.read_bytes()
        else:
            snap[str(d)] = None
    return snap


def _digest(snap: dict) -> dict:
    return {k: (hashlib.sha256(v).hexdigest() if v is not None else None) for k, v in snap.items()}


@pytest.fixture(scope="session", autouse=True)
def home_files_untouched():
    before = _snapshot()
    yield
    after = _snapshot()
    changed = sorted(set(k for k in set(before) | set(after) if _digest(before).get(k) != _digest(after).get(k)))
    if not changed:
        return
    # put the originals back, then fail loudly
    for k in changed:
        p = Path(k)
        original = before.get(k)
        try:
            if original is None:
                if p.is_file():
                    p.unlink()
                elif p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(original)
        except OSError:
            pass
    pytest.fail("the test session changed files under HOME (restored): " + ", ".join(changed))
