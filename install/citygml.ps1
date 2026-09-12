# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
#
# 4dcitygml — install / launch the building data editing tools (Windows).
#
#   First time (from the city's README, in PowerShell):
#     & ([scriptblock]::Create((irm https://raw.githubusercontent.com/4dcitygml/tools/install-v1/install/citygml.ps1))) <owner/repo>
#   Afterwards this same file lives in %USERPROFILE%\Documents\citygml-tools\citygml.ps1 and
#   the desktop shortcut the hub creates runs it. Running the one-line command again is always
#   safe: it installs the newest release if there is one, then starts the tools.
#
# Steps mirror citygml.sh: decide the city → keep a copy of this script → the one-line command
# (this script arriving from the network, not from a file) installs the latest release when it
# is newer than what is installed (download verified against GitHub's SHA-256 digest; offline,
# an installed version still starts), while the desktop shortcut starts the newest installed hub
# of this generation (hub-v1.2.0 or newer) and downloads only when there is none → hand over to
# the hub (bundled Python and Git travel inside the download). Older installations (hub-v1.0.x)
# are left in place, untouched, never started.
# `citygml.ps1 -FetchLatest` is the hub's "Get it now": it installs the newest release next to
# the running one (same download and digest check) and prints its tag. This file is the one
# place that downloads, verifies and unpacks a release on Windows.
# Test overrides: CITYGML_TOOLS_DIR, CITYGML_RELEASES_JSON, CITYGML_ASSET_FILE, CITYGML_NO_EXEC=1.
param([string]$City = "", [switch]$FetchLatest)
$ErrorActionPreference = "Stop"

$installTag = if ($env:CITYGML_INSTALL_TAG) { $env:CITYGML_INSTALL_TAG } else { "install-v1" }
$selfUrl = "https://raw.githubusercontent.com/4dcitygml/tools/$installTag/install/citygml.ps1"
$toolsDir = if ($env:CITYGML_TOOLS_DIR) { $env:CITYGML_TOOLS_DIR } else { Join-Path $env:USERPROFILE "Documents\citygml-tools" }
$hubs = Join-Path $toolsDir "citygml-hub"
$releasesApi = if ($env:CITYGML_RELEASES_API) { $env:CITYGML_RELEASES_API } else { "https://api.github.com/repos/4dcitygml/tools/releases?per_page=30" }

# 1. City: argument, else a practice city by language.
if (-not $City -and -not $FetchLatest) {
  $lang = (Get-Culture).TwoLetterISOLanguageName
  $City = switch ($lang) { "ja" { "4dcitygml/sample-tokyo-station" } "de" { "4dcitygml/sample-munich-station" } default { "4dcitygml/sample-newyork-station" } }
  Write-Host "No city given: connecting to the practice city $City (nothing you do there can break anything)."
}
if (-not $FetchLatest -and $City -notmatch "^[^/]+/[^/]+$") { throw "The city must be given as owner/repo (for example 4dcitygml/sample-tokyo-station)." }

# 2. Keep a copy of this launcher in the tools folder.
New-Item -ItemType Directory -Force $toolsDir | Out-Null
$selfPath = Join-Path $toolsDir "citygml.ps1"
$oneLine = -not $PSCommandPath   # no file behind this run: the one-line command from the README
if ($PSCommandPath -and (Test-Path $PSCommandPath) -and ((Resolve-Path $PSCommandPath).Path -ne $selfPath)) {
  Copy-Item $PSCommandPath $selfPath -Force
} elseif (-not $PSCommandPath -and -not $env:CITYGML_RELEASES_JSON) {
  Invoke-WebRequest -UseBasicParsing $selfUrl -OutFile $selfPath
}

$generation = [version]::new(1, 2, 0)   # hub-v1.2.0: the first version with its own update screen

function Get-VersionKey([string]$tag) {
  if ($tag -match "^hub-v(\d+)\.(\d+)\.(\d+)") { return [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3]) }
  return $null
}

function Get-NewestInstalled {
  if (-not (Test-Path $hubs)) { return "" }
  $best = Get-ChildItem $hubs -Directory | Where-Object { (Get-VersionKey $_.Name) -and ((Get-VersionKey $_.Name) -ge $generation) -and (Test-Path (Join-Path $_.FullName "program\hub.py")) } |
    Sort-Object { Get-VersionKey $_.Name } -Descending | Select-Object -First 1
  if ($best) { return $best.Name } else { return "" }
}

function Install-Latest {
  $rels = if ($env:CITYGML_RELEASES_JSON) { Get-Content $env:CITYGML_RELEASES_JSON -Raw | ConvertFrom-Json } else { Invoke-RestMethod -UseBasicParsing $releasesApi }
  $best = $rels | Where-Object { (Get-VersionKey $_.tag_name) -and -not $_.draft -and -not $_.prerelease } | Sort-Object { Get-VersionKey $_.tag_name } -Descending | Select-Object -First 1
  if (-not $best) { throw "No hub-v release found." }
  $tag = $best.tag_name
  if (Test-Path (Join-Path $hubs "$tag\program\hub.py")) { return $tag }   # already installed: nothing to download
  $want = "citygml-hub-" + $tag.Substring(4) + "-windows-full.zip"
  $asset = $best.assets | Where-Object { $_.name -eq $want } | Select-Object -First 1
  if (-not $asset) { throw "Asset $want is missing in $tag." }
  if (-not ($asset.digest -like "sha256:*")) { throw "${want}: GitHub published no sha256 digest." }
  $sha = $asset.digest.Substring(7).ToLowerInvariant()
  Write-Host "Downloading the editing tools ($tag) …"
  $tmp = Join-Path $env:TEMP "citygml-hub-download.zip"
  if ($env:CITYGML_ASSET_FILE) { Copy-Item $env:CITYGML_ASSET_FILE $tmp -Force } else { curl.exe -fLsS $asset.browser_download_url -o $tmp; if ($LASTEXITCODE -ne 0) { throw "Download failed." } }
  $actual = (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $sha) { Remove-Item $tmp; throw "The download does not match the SHA-256 GitHub published (expected $sha / actual $actual). Nothing was installed." }
  $stage = Join-Path $env:TEMP "citygml-hub-stage"
  if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
  Expand-Archive -LiteralPath $tmp -DestinationPath $stage -Force
  Remove-Item $tmp
  if (-not (Test-Path (Join-Path $stage "citygml-hub\program\hub.py"))) { throw "The downloaded archive has an unexpected layout." }
  New-Item -ItemType Directory -Force $hubs | Out-Null
  $target = Join-Path $hubs $tag
  if (-not (Test-Path $target)) { Move-Item (Join-Path $stage "citygml-hub") $target }
  Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
  return $tag
}

if ($FetchLatest) {
  try { $tag = Install-Latest } catch { throw "The latest version could not be installed ($($_.Exception.Message)). Check the internet connection and try again." }
  if (-not (Test-Path (Join-Path $hubs "$tag\program\hub.py"))) { throw "The latest version could not be installed. Check the internet connection and try again." }
  Write-Output $tag
  exit 0
}

$tag = Get-NewestInstalled
if ($oneLine -or -not $tag) {
  # The one-line command installs the newest release (an update when one is already installed);
  # a launcher run from a file starts what is installed (the hub offers newer versions on its own
  # screen). Without network, an installed version still starts.
  $new = ""; $why = ""
  try { $new = Install-Latest } catch { $why = $_.Exception.Message }
  if ($new -and (Test-Path (Join-Path $hubs "$new\program\hub.py"))) {
    if ($tag -and $new -eq $tag) { Write-Host "The editing tools are up to date ($tag)." }
    if ($tag -and $new -ne $tag) { Write-Host "Updated the editing tools: $new installed next to $tag." }
    $tag = Get-NewestInstalled
  } elseif ($tag) {
    Write-Host "Could not check for a newer version; starting the installed $tag."
  } else {
    throw "The latest version could not be installed ($why). Check the internet connection and try again."
  }
}
$app = Join-Path $hubs "$tag\program\hub.py"
$py = Join-Path $hubs "$tag\program\PythonPortable\python.exe"
if (-not $tag -or -not (Test-Path $app)) { throw "The latest version could not be installed. Check the internet connection and try again." }
if (-not (Test-Path $py)) { throw "Bundled Python not found: $py. Delete $hubs\$tag and run this again." }

# 4. Hand over to the hub.
$env:CITYGML_UPSTREAM = $City; $env:CITYGML_HUB_TAG = $tag
if ($env:CITYGML_NO_EXEC -eq "1") { Write-Host "EXEC $tag $City $app"; exit 0 }
& $py $app
exit $LASTEXITCODE
