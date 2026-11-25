# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
#
# 4dcitygml: the bootstrap of the one-line command in every city's README (Windows, PowerShell):
#     & ([scriptblock]::Create((irm https://raw.githubusercontent.com/4dcitygml/tools/install-v1/install/citygml.ps1))) <owner/repo>
# The install-v1 tag points here and never moves again (W8). This fetches the installer attached
# to the latest hub release (install/release/citygml.ps1 there) and runs it with the same arguments,
# as the one-line command (from text, not from a file). CITYGML_TOOLS_REPO names another tools
# repository; CITYGML_INSTALLER_URL another installer (a lab rehearsal, a test).
$ErrorActionPreference = "Stop"
$repo = if ($env:CITYGML_TOOLS_REPO) { $env:CITYGML_TOOLS_REPO } else { "4dcitygml/tools" }
$url = if ($env:CITYGML_INSTALLER_URL) { $env:CITYGML_INSTALLER_URL } else { "https://github.com/$repo/releases/latest/download/citygml.ps1" }
try {
  # a release asset is served as bytes: decode it as the UTF-8 text it is (a local file: a test)
  $bytes = if (Test-Path -LiteralPath $url) { [IO.File]::ReadAllBytes($url) } else { (Invoke-WebRequest -UseBasicParsing $url).RawContentStream.ToArray() }
} catch { throw "Could not download the installer ($url). Check the internet connection and try again." }
$installer = [Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF)
& ([scriptblock]::Create($installer)) @args
exit $LASTEXITCODE
