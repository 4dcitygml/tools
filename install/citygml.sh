#!/bin/bash
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
#
# 4dcitygml: the bootstrap of the one-line command in every city's README (macOS):
#     /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/4dcitygml/tools/install-v1/install/citygml.sh)" -- <owner/repo>
# The install-v1 tag points here and never moves again (W8). This fetches the installer attached
# to the latest hub release (install/release/citygml.sh there) and runs it with the same arguments,
# as the one-line command (from text, not from a file). CITYGML_TOOLS_REPO names another tools
# repository; CITYGML_INSTALLER_URL another installer (a lab rehearsal, a test).
set -euo pipefail
url="${CITYGML_INSTALLER_URL:-https://github.com/${CITYGML_TOOLS_REPO:-4dcitygml/tools}/releases/latest/download/citygml.sh}"
installer="$(curl -fsSL "$url")" || {
  printf '%s\n' "Could not download the installer ($url). Check the internet connection and try again." >&2; exit 1; }
exec /bin/bash -c "$installer" -- "$@"
