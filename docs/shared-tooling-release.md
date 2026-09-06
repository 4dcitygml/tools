<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Shared tooling distribution

Introduced in hub-v1.1.0. Each release workflow builds and smoke-tests the
Windows, macOS and common source packages before publishing its assets.

The existing `hub-v<version>` release can now carry three assets:

Its display name is **4dcitygml tools**. The historical `hub-v` tag prefix is
retained for compatibility with existing download paths; the release covers
the shared tools as well as the Hub.

| Asset | Audience and contents |
|---|---|
| `citygml-hub-<version>-windows-full.zip` | Hub users on Windows; existing portable Python/Git entry point |
| `citygml-hub-<version>-macos.zip` | Hub users on macOS; existing launcher |
| `citygml-tools-<version>-source.zip` | Operators/developers; processing scripts, CI, schemas, semantic definitions, documentation and tool sources |

All three jobs select the same requested tag. A normal tag-triggered release
waits for all three build/smoke jobs. The common source archive contains a
`distribution.json` with its source commit, release tag and per-file hashes.
The archive SHA-256 is included in the release notes. The source package
requires Python and dependencies from `requirements.txt`; it is not a bundled
Python executable. City CI continues to fetch its immutable tools commit.

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

hub-v1.1.0 also includes the shared report and standard approval changes. Required machine checks remain `analyze` and
`ci-report`; the retired operator-only confirmation gate is not reinstated.
See [implementation status](implementation-status.md).

## Remaining distribution work

The agreed destination is a common release that also includes the starter kit
and passes each city's settings into it. This source archive is one step toward
that destination. City-specific starter kits still use their existing route.
Automatic latest-release selection, old-version management and the common
starter migration are not implemented by this packaging change.

Release publication and any adoption in an actual city are separate actions.
