#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""One way for the scripts to run git in a repository.

    git(repo, "rev-parse", "HEAD")                    -> stdout as text (not stripped)
    git(repo, "show", f"{sha}:{path}", binary=True)   -> stdout as bytes
    git(repo, "rev-parse", "HEAD", strip=True)        -> stdout with surrounding whitespace removed

A failing command raises GitError, which is both a RuntimeError and a
subprocess.CalledProcessError, so callers may catch either; its message names
the command and carries git's stderr.
"""
from __future__ import annotations

import subprocess


class GitError(RuntimeError, subprocess.CalledProcessError):
    def __init__(self, args: tuple, returncode: int, stderr: bytes):
        detail = stderr.decode("utf-8", errors="replace").strip()
        subprocess.CalledProcessError.__init__(self, returncode, ["git", *args], stderr=stderr)
        RuntimeError.__init__(self, f"git {' '.join(args)}: {detail}" if detail else f"git {' '.join(args)} failed")

    def __str__(self) -> str:
        return RuntimeError.__str__(self)


def git(repo, *args: str, binary: bool = False, strip: bool = False):
    proc = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise GitError(args, proc.returncode, proc.stderr)
    if binary:
        return proc.stdout
    text = proc.stdout.decode("utf-8", errors="replace")
    return text.strip() if strip else text
