<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Shared tooling distribution

Two independent release series serve different destinations. Confirm published assets and compatible city settings before adoption.

| Series | Destination | Assets |
|---|---|---|
| `hub-v<version>` | Contributors' Mac/Windows computers | `citygml-hub-<version>-macos.zip`, `citygml-hub-<version>-windows-full.zip` |
| `tools-v<version>` | City operators and processing environments | `citygml-tools-<version>-source.zip` |

`release-hub.yml` builds and smoke-tests both Hub clients. `release-tools.yml`
builds and smoke-tests the common source. Each workflow accepts only its own
series for publication, including a manually requested tag. A manual dispatch
on a branch with the release tag left empty builds without publishing a Release.
Existing assets are not silently overwritten on a rerun.

Hub releases keep their existing tag and asset naming. The first
independent tools release is `tools-v1.1.0`. The two series may advance
independently; matching numbers are not a compatibility check. Tools releases
use `--latest=false` so the repository's general latest-release link does not
switch from a client download to an operator package. Use explicit release tags
and asset names for both destinations.

The common source includes processing, CI, schemas, semantic definitions,
documentation and supporting tool sources. Its `distribution.json` records the
source commit, release tag and per-file hashes; the outer ZIP SHA-256 is included
in release notes. It requires Python and `requirements.txt` dependencies.
City CI continues to fetch its immutable tools commit.

### Transition from the mixed hub-v1.1.0 release

The published `hub-v1.1.0` initially contains both Hub clients and a common
source archive. Publish and verify `tools-v1.1.0` first, then link to it from the
Hub release notes and remove only the old common source attachment. Preserve
the two Hub assets, their hashes and the existing Hub tag. Build the tools
archive afresh: its recorded tag and commit must match its own release.

The source packager includes only the declared common source directories and
license/dependency files. City datasets, local records, caches and downloaded
build outputs are excluded. Release mode requires a clean checkout at the tag
and rejects files outside its tracked source tree. Local preview mode records
no release tag or source commit and cannot be mistaken for a release manifest.

## Verify a local preview

From a tools checkout:

```sh
python scripts/build_common_tools.py build --root . --local --output /tmp/common-tools-preview.zip
python scripts/build_common_tools.py verify /tmp/common-tools-preview.zip
```

Use a new output path each time. Verification checks the complete entry list,
regular-file layout, required contents and each file's hash. It checks archive
integrity; a downloaded archive's outer digest must also be compared with its
trusted release record. Smoke tests extract it into a separate directory and
run the CLI entry points from that directory.

## City adoption

Before enabling the LOD0 pilot, use a released tools SHA containing the recipe
and update the city's analysis/history pins through its normal reviewed change.
The generating tools SHA must match the trusted city CI SHA for this recipe.
Existing pins are not replaced with a guessed or local snapshot SHA.

tools-v1.1.0 includes shared reports and standard approvals.
Required machine checks are `analyze` and `ci-report`; the retired operator-only
confirmation gate is not reinstated. See [implementation status](implementation-status.md).

Cities pin only the CI tools version (`CITYGML_TOOLS_REF` in their workflows).
The **Hub client** is not pinned by cities: its version is decided by the
`hub-v*` releases of this repository (see below). A new client does not update
city CI or change an ongoing case's tools version.

## Client distribution (hub-v releases)

- **Entry**: one command from the city's README —
  `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/4dcitygml/tools/install-v1/install/citygml.sh)" -- <owner/repo>`
  (Windows: the `citygml.ps1` equivalent). The script comes from the moving tag
  `install-v1` of this repository, installs the newest `hub-v*` release after
  verifying it against the SHA-256 digest GitHub publishes for the asset, keeps
  a copy of itself in `~/Documents/citygml-tools/`, and hands over to the hub.
  Without an argument it connects to a practice city chosen by the system language.
- **Afterwards**: the hub creates a desktop launcher (`.app` / `.lnk`) that
  runs the per-user copy of the script with the city id; nothing else is
  needed. Versions live side by side in `citygml-tools/citygml-hub/<tag>/`; the
  launcher starts the newest installed one.
- **Updates**: the hub checks the releases API at each start and shows a banner
  when a newer version exists. *Get it now* downloads and digest-verifies it
  into its own folder; it is used from the next start. Nothing is downloaded
  without that click and nothing restarts by itself. A city may declare
  `min_hub` in `4dcitygml.json` (advisory) to say why an update matters.
- **Cities distribute no code** (Exchange Contract A11): no `install/`
  folder, no starter kit release, no client pin. Everything that runs on a
  contributor's computer comes from `hub-v*` release assets.

Remaining work: automatic tools-version selection for CI, Issue-to-PR
automation, and thinning the city-side `.github/` to a pin-only wrapper. The
source ZIP is not an automatic city installer.

Release publication and any adoption in an actual city are separate actions.
