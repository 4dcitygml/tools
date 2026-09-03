#!/bin/zsh
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
# citygml-hub (.py) launcher — macOS
# In the zip this file sits at the top of the distribution folder as
# "start-mac.command"; everything else lives in the adjacent "program/"
# (the intent: users only ever see the guide HTML and this file).
# The bundled PythonPortable is preferred; otherwise use the CLT / system python3.
# Because the macOS zip bundles no binaries (M1 policy), the Command Line Tools
# python3 is the usual path.
HERE="$(cd "$(dirname "$0")" && pwd)"

# App location: distribution layout (program/) first, then repo-flat (development).
if [ -f "$HERE/program/hub.py" ]; then
  LIB="$HERE/program"
else
  LIB="$HERE"
fi

if [ -x "$LIB/PythonPortable/bin/python3" ]; then
  PY="$LIB/PythonPortable/bin/python3"
else
  PY="$(command -v python3)"
fi

if [ -z "$PY" ]; then
  echo "python3 was not found."
  echo "Install the Command Line Tools (xcode-select --install), use the"
  echo "PythonPortable bundled with the .py distribution, or install python3"
  echo "from python.org."
  exit 1
fi

# Version check: this tool needs Python 3.9+ (stdlib only; no dependence on a specific version).
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 9) else 1)' 2>/dev/null; then
  echo "Python 3.9 or newer is required (detected: $("$PY" --version 2>&1))."
  echo "Update macOS or the Command Line Tools (xcode-select --install)."
  exit 1
fi

exec "$PY" "$LIB/hub.py" "$@"
