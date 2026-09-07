#!/bin/bash
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
#
# Point the moving tag `install-v1` at a commit of main — the commit whose install/ is the
# one that was tested. Every city README's one-line command fetches install/citygml.sh (.ps1)
# at this tag, so moving it is the step that delivers a new launcher to every computer.
#
#     scripts/move_install_tag.sh [--dry-run] [<commit>]      default: origin/main (fetched first)
#
# The tag is deleted on origin and created anew, never force-updated. The local pre-push
# guard checks every commit between the old and the new target of an updated ref, and a
# squash merge made on GitHub carries GitHub's own committer address, so a force update is
# refused; a deletion has nothing to check, and a new ref is checked only for commits not
# yet on the remote — none, once main has been fetched. The guard is never bypassed.
# Between the deletion and the creation the raw URL answers 404 for about a second;
# afterwards raw.githubusercontent.com can serve the previous file for a few minutes.
set -euo pipefail
fail() { printf '%s\n' "$*" >&2; exit 1; }
TAG="${INSTALL_TAG:-install-v1}"
DRY=0
if [ "${1:-}" = "--dry-run" ]; then DRY=1; shift; fi
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "run this inside the tools clone"
git fetch --quiet origin
sha="$(git rev-parse --verify "${1:-origin/main}^{commit}")"
git merge-base --is-ancestor "$sha" origin/main || fail "$sha is not on origin/main; the tag only ever points at published main"
git diff --quiet "$sha" -- install/citygml.sh install/citygml.ps1 \
  || fail "install/ at $sha differs from the working tree — the tag must point at the launcher that was tested"
current="$(git ls-remote --tags origin "refs/tags/$TAG" | cut -f1)"
if [ "$current" = "$sha" ]; then echo "$TAG already points at $sha"; exit 0; fi
[ -n "$current" ] && run git push origin ":refs/tags/$TAG"
run git tag -f "$TAG" "$sha"
run git push origin "refs/tags/$TAG"
if [ "$DRY" = 0 ]; then
  now="$(git ls-remote --tags origin "refs/tags/$TAG" | cut -f1)"
  [ "$now" = "$sha" ] || fail "origin reports $TAG at '${now:-none}', expected $sha"
fi
echo "$TAG -> $sha (was ${current:-absent})"
