#!/usr/bin/env bash
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
#
# Check out the city repositories the contract tests read, next to the tools checkout:
#   bash ci/city_checkouts.sh <refs file> <folder that holds tools/>
# Each repository named in the refs file (ci/city-refs.json) is fetched at its pinned commit,
# one commit deep, without file contents until they are needed (--filter=blob:none), and
# checked out without its data folders (data_dirs of its 4dcitygml.json, and provenance/):
# the tests read workflows, scripts and documents, never the data.
#
# CITY_READ_TOKEN (optional) is a read-only token for private repositories; without it the
# repositories are read anonymously. It is passed to git as a header, never in a URL or argv.
set -euo pipefail
REFS="$1"
DEST="$2"
started=$(date +%s)

auth=()
if [ -n "${CITY_READ_TOKEN:-}" ]; then
  basic="$(printf 'x-access-token:%s' "$CITY_READ_TOKEN" | base64 | tr -d '\n')"
  echo "::add-mask::${basic}"
  auth=(-c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${basic}")
fi

python3 - "$REFS" <<'PY' | while IFS=$'\t' read -r folder repository ref; do
import json, re, sys
for folder, entry in json.load(open(sys.argv[1], encoding="utf-8"))["repositories"].items():
    repo, ref = entry["repository"], entry["ref"]
    if not (re.fullmatch(r"[A-Za-z0-9_.-]+", folder) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            and re.fullmatch(r"[0-9a-f]{40}", ref)):
        sys.exit(f"invalid entry {folder}: a folder name, owner/repo and a 40-character commit are required")
    print(folder, repo, ref, sep="\t")
PY
  target="$DEST/$folder"
  rm -rf "$target"
  git init -q "$target"
  git -C "$target" remote add origin "https://github.com/${repository}.git"
  git "${auth[@]}" -C "$target" fetch -q --depth 1 --filter=blob:none origin "$ref"
  data="$(git "${auth[@]}" -C "$target" show "FETCH_HEAD:4dcitygml.json" 2>/dev/null | python3 -c '
import json, sys
try:
    dirs = json.load(sys.stdin).get("data_dirs") or []
except ValueError:
    dirs = []
for d in [*dirs, "provenance"]:
    if isinstance(d, str) and d.strip("/"):
        print("!/" + d.strip("/") + "/")
' || true)"
  git -C "$target" sparse-checkout set --no-cone '/*' $data
  git "${auth[@]}" -C "$target" checkout -q --detach FETCH_HEAD
  echo "$folder: ${repository}@${ref:0:12} ($(git -C "$target" ls-files | wc -l | tr -d ' ') tracked files)"
done
echo "City checkouts took $(( $(date +%s) - started )) s."
