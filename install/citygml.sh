#!/bin/bash
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
#
# 4dcitygml — install / launch the building data editing tools (macOS).
#
#   First time (from the city's README):
#     /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/4dcitygml/tools/install-v1/install/citygml.sh)" -- <owner/repo>
#   Afterwards this same file lives in ~/Documents/citygml-tools/citygml.sh and the
#   desktop icon the hub creates runs it. Running the one-line command again is always
#   safe: it installs the newest release if there is one, then starts the tools.
#
# What it does, in order:
#   1. Decides the city: the argument, else a practice city chosen by your language.
#   2. Copies itself into ~/Documents/citygml-tools/ (locally written files carry no
#      quarantine attribute, so the icon opens without a Gatekeeper warning).
#   3. The one-line command (this script arriving from the network, not from a file)
#      looks up the latest hub release of 4dcitygml/tools and installs it when it is
#      newer than what is installed, verifying the download against the SHA-256 digest
#      GitHub publishes for the asset. Nothing runs unless the digest matches. When the
#      lookup fails (offline) and a version is installed, that one starts. The desktop
#      icon (this file run from ~/Documents/citygml-tools/) starts the newest installed
#      hub of this generation (hub-v1.2.0 or newer: the versions that fetch updates from
#      their own screen) and downloads only when there is none.
#      Older installations (hub-v1.0.x, flat or in a version folder) are not this
#      file's business: they are left in place, untouched, and never started by it.
#   4. Hands over to the hub, which keeps your city's data up to date and offers
#      newer tool versions inside its own screen (applied at the next start).
#
# `citygml.sh --fetch-latest` is that "Get it now": it installs the newest release
# next to the running one (same download and digest check as step 3) and prints its
# tag. This file is the one place that downloads, verifies and unpacks a release.
#
# Cities distribute no code: everything that runs on your computer comes from
# 4dcitygml/tools releases. Environment overrides used by tests: CITYGML_TOOLS_DIR,
# CITYGML_RELEASES_JSON (file instead of the API), CITYGML_ASSET_FILE (zip instead
# of the download), CITYGML_NO_EXEC=1 (print the launch instead of running it).
set -euo pipefail

INSTALL_TAG="${CITYGML_INSTALL_TAG:-install-v1}"
SELF_URL="https://raw.githubusercontent.com/4dcitygml/tools/${INSTALL_TAG}/install/citygml.sh"
TOOLS_DIR="${CITYGML_TOOLS_DIR:-$HOME/Documents/citygml-tools}"
HUBS="$TOOLS_DIR/citygml-hub"
RELEASES_API="${CITYGML_RELEASES_API:-https://api.github.com/repos/4dcitygml/tools/releases?per_page=30}"

msg() { printf '%s\n' "$*"; }
fail() { printf '%s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || fail "python3 was not found. Install Apple's Command Line Tools (xcode-select --install) and run this again."

FETCH_ONLY=0
if [ "${1:-}" = "--fetch-latest" ]; then FETCH_ONLY=1; shift; fi

# 1. City: argument, else a practice city by language (ja → Tokyo, de → Munich, else New York).
CITY="${1:-}"
if [ -z "$CITY" ] && [ "$FETCH_ONLY" = 0 ]; then
  lang="${LANG:-}"
  if [ -z "$lang" ] && command -v defaults >/dev/null 2>&1; then
    lang="$(defaults read -g AppleLocale 2>/dev/null || true)"
  fi
  case "$lang" in
    ja*) CITY="4dcitygml/sample-tokyo-station" ;;
    de*) CITY="4dcitygml/sample-munich-station" ;;
    *)   CITY="4dcitygml/sample-newyork-station" ;;
  esac
  msg "No city given: connecting to the practice city $CITY (nothing you do there can break anything)."
fi
case "$CITY" in
  */*) ;;
  "") [ "$FETCH_ONLY" = 1 ] || fail "The city must be given as owner/repo (for example 4dcitygml/sample-tokyo-station)." ;;
  *) fail "The city must be given as owner/repo (for example 4dcitygml/sample-tokyo-station)." ;;
esac

# 2. Keep a copy of this launcher in the tools folder (the desktop icon points here).
mkdir -p "$TOOLS_DIR"
SELF="${BASH_SOURCE[0]:-}"
ONE_LINE=0
[ -n "$SELF" ] || ONE_LINE=1   # no file behind this run: the one-line command from the README
if [ "$SELF" != "$TOOLS_DIR/citygml.sh" ]; then
  if [ -n "$SELF" ] && [ -f "$SELF" ]; then
    cp "$SELF" "$TOOLS_DIR/citygml.sh"
  elif [ -z "${CITYGML_RELEASES_JSON:-}" ]; then
    curl -fsSL "$SELF_URL" -o "$TOOLS_DIR/citygml.sh"
  fi
  [ -f "$TOOLS_DIR/citygml.sh" ] && chmod 755 "$TOOLS_DIR/citygml.sh"
fi

# 3. Newest installed version of this generation (semantic order), or install the latest release.
newest_installed() {
  python3 - "$HUBS" <<'PY'
import pathlib, re, sys
root = pathlib.Path(sys.argv[1])
GENERATION = (1, 2, 0)   # hub-v1.2.0: the first version with its own update screen
def key(p):
    m = re.fullmatch(r"hub-v(\d+)\.(\d+)\.(\d+)(?:-.*)?", p.name)
    return tuple(int(x) for x in m.groups()) if m else None
found = [p for p in root.glob("hub-v*") if key(p) and key(p) >= GENERATION and (p / "program" / "hub.py").is_file()] if root.is_dir() else []
print(max(found, key=key).name if found else "")
PY
}

install_latest() {
  local json tmp
  json="$(mktemp "${TMPDIR:-/tmp}/citygml-releases.XXXXXX")"
  if [ -n "${CITYGML_RELEASES_JSON:-}" ]; then cp "$CITYGML_RELEASES_JSON" "$json" 2>/dev/null; else curl -fsSL "$RELEASES_API" -o "$json"; fi \
    || fail "Could not reach GitHub to look up the latest version. Check the internet connection and try again."
  # tag, download url, expected sha256 of the macOS asset of the newest hub-v release
  read -r TAG URL SHA <<EOF
$(python3 - "$json" <<'PY'
import json, re, sys
try:
    rels = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit("the list of versions could not be read")
def key(t):
    m = re.fullmatch(r"hub-v(\d+)\.(\d+)\.(\d+)", t or "")
    return tuple(int(x) for x in m.groups()) if m else None
best = max((r for r in rels if key(r.get("tag_name")) and not r.get("draft") and not r.get("prerelease")),
           key=lambda r: key(r["tag_name"]), default=None)
if not best: raise SystemExit("no hub-v release found")
tag = best["tag_name"]; want = f"citygml-hub-{tag.removeprefix('hub-')}-macos.zip"
asset = next((a for a in best.get("assets", []) if a.get("name") == want), None)
if not asset: raise SystemExit(f"asset {want} missing in {tag}")
digest = str(asset.get("digest") or "")
if not digest.startswith("sha256:"): raise SystemExit(f"{want}: GitHub published no sha256 digest")
print(tag, asset["browser_download_url"], digest.split(":", 1)[1])
PY
)
EOF
  rm -f "$json"
  [ -n "${TAG:-}" ] || fail "Could not determine the latest version. Check the internet connection and try again."
  if [ -f "$HUBS/$TAG/program/hub.py" ]; then echo "$TAG"; return 0; fi   # already installed: nothing to download
  msg "Downloading the editing tools ($TAG) …" >&2   # progress on stderr: stdout carries the tag
  tmp="$(mktemp "${TMPDIR:-/tmp}/citygml-hub.XXXXXX")"
  if [ -n "${CITYGML_ASSET_FILE:-}" ]; then cp "$CITYGML_ASSET_FILE" "$tmp"; else curl -fL "$URL" -o "$tmp"; fi \
    || { rm -f "$tmp"; fail "The download of $TAG failed. Check the internet connection and try again."; }
  actual="$(shasum -a 256 "$tmp" | cut -d' ' -f1)"
  if [ "$actual" != "$SHA" ]; then rm -f "$tmp"; fail "The download does not match the SHA-256 GitHub published (expected ${SHA} / actual ${actual}). Nothing was installed."; fi
  local stage; stage="$(mktemp -d "${TMPDIR:-/tmp}/citygml-hub-stage.XXXXXX")"
  unzip -oq "$tmp" -d "$stage"; rm -f "$tmp"
  [ -f "$stage/citygml-hub/program/hub.py" ] || fail "The downloaded archive has an unexpected layout."
  mkdir -p "$HUBS"
  if [ ! -d "$HUBS/$TAG" ]; then mv "$stage/citygml-hub" "$HUBS/$TAG"; fi
  rm -rf "$stage"
  echo "$TAG"
}

if [ "$FETCH_ONLY" = 1 ]; then
  TAG="$(install_latest | tail -n 1)"
  [ -n "$TAG" ] && [ -f "$HUBS/$TAG/program/hub.py" ] || fail "The latest version could not be installed. Check the internet connection and try again."
  echo "$TAG"
  exit 0
fi

TAG="$(newest_installed)"
if [ "$ONE_LINE" = 1 ] || [ -z "$TAG" ]; then
  # The one-line command installs the newest release (an update when one is already
  # installed); a launcher run from a file starts what is installed (the hub offers
  # newer versions on its own screen). Without network, an installed version still starts.
  if NEW="$(install_latest | tail -n 1)" && [ -n "$NEW" ] && [ -f "$HUBS/$NEW/program/hub.py" ]; then
    if [ -n "$TAG" ] && [ "$NEW" = "$TAG" ]; then msg "The editing tools are up to date ($TAG)."; fi
    if [ -n "$TAG" ] && [ "$NEW" != "$TAG" ]; then msg "Updated the editing tools: $NEW installed next to $TAG."; fi
    TAG="$(newest_installed)"
  elif [ -n "$TAG" ]; then
    msg "Could not check for a newer version; starting the installed $TAG."
  fi
fi
APP="$HUBS/$TAG/program/hub.py"
[ -n "$TAG" ] && [ -f "$APP" ] || fail "The latest version could not be installed. Check the internet connection and try again."

# 4. Hand over to the hub.
export CITYGML_UPSTREAM="$CITY" CITYGML_HUB_TAG="$TAG"
if [ "${CITYGML_NO_EXEC:-}" = "1" ]; then
  msg "EXEC $TAG $CITY $APP"
  exit 0
fi
exec python3 "$APP"
