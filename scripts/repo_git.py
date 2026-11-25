#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""One way for the scripts to run git in a repository.

    git(repo, "rev-parse", "HEAD")                    -> stdout as text (not stripped)
    git(repo, "show", f"{sha}:{path}", binary=True)   -> stdout as bytes
    git(repo, "rev-parse", "HEAD", strip=True)        -> stdout with surrounding whitespace removed
    blob(repo, sha, path)                             -> the file's bytes at that commit, or None
    changed(repo, base, head) / changed(repo, sha)    -> the paths a range / a commit touched
    commits(repo, base, head)                         -> the range's commits, oldest first, one git log

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


def blob(repo, sha: str, path: str) -> "bytes | None":
    """The bytes of path at commit sha, or None when git cannot show it (no such file at that
    commit, unknown sha). The working tree does not matter."""
    try:
        return git(repo, "show", f"{sha}:{path}", binary=True)
    except GitError:
        return None


def changed(repo, base: str, head: "str | None" = None, suffix: str = "") -> "list[str]":
    """The paths that base..head, or the commit base alone (head None: against its first parent,
    a root commit against the empty tree), touched, sorted; a rename is both of its paths (no
    rename detection), a type change counts. Only the paths ending with suffix when given. The one
    reader of changed paths (U9: eight `git diff --name-only` copies, an empty-tree literal)."""
    args = (["diff", "--name-only", "--no-renames", base, head] if head is not None else
            ["diff-tree", "-r", "--root", "--no-commit-id", "--name-only", "--no-renames", "--diff-merges=first-parent", base])
    return sorted({p for p in git(repo, *args).splitlines() if p and p.endswith(suffix)})


def commits(repo, base: str, head: str) -> "list[dict]":
    """The commits of base..head, oldest first in git's topological order, from one `git log`:
    {"sha", "parents", "subject", "message"} (U9: a rev-list and three `git show` per commit)."""
    out = git(repo, "log", "--reverse", "--topo-order", "--format=%H%x00%P%x00%s%x00%B%x01", f"{base}..{head}")
    records = []
    for entry in out.split("\x01"):
        if entry.count("\x00") >= 3:
            sha, parents, subject, message = entry.lstrip("\n").split("\x00", 3)
            records.append({"sha": sha.strip(), "parents": parents.split(), "subject": subject, "message": message})
    return records
