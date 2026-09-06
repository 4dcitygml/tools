<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Implementation status of the review gates

City tooling is distributed independently as `tools-v1.1.0`.
See [distribution status](shared-tooling-release.md) before adopting it.

What the shared CI and tools check today, and what is still to be built
before the corresponding PR types are unlocked. Moved here from the city
repositories' PR operations guide (its §7) because it describes the tools,
not a city's procedure. Updated with each tools release.

## 1. What the current gates check

Included in tools-v1.1.0 (2026-09-06): CI generates a shared versioned
report. All reviewers use standard GitHub Approve. The city can change its
required approval count and reviewer membership over time. The hub reads active
rulesets and classic protection, counts distinct current eligible approvals,
and filters inspected PRs by remaining count. Preferences are per account and
repository in the browser. Read failures appear as unavailable; zero remaining
is not a mergeability judgment.

The prototype operator-only confirmation gate/button and automatic Draft/Ready
transitions have been removed. Required machine checks are `analyze` and
`ci-report`; human approvals and their invalidation follow GitHub rules. The
trusted report verifier checks only current machine evidence. It never dismisses
human reviews or forces role order. The shared helper retains its historical
filename for portable package compatibility.

These changes are not in hub-v1.0.2. Adopt tools-v1.1.0 with updated city pins and
complete GitHub acceptance tests before operation. Remove the prototype
`operator-explanation` required check on migration. Browser integration remains
a release verification item; executable filter/persistence logic is checked locally.

- LOD0 semantic correction: a pilot CityGML 2.0 / i-UR 3.1 recipe renames FootPrint to RoofEdge with input/rationale digests, per-building commits, exact whole-file and intermediate-commit checks, reproduction and a generated review note. Included in tools-v1.1.0; city GitHub pilot pending. This does not unlock arbitrary semantic corrections. See [recipe scope and workflow](lod0-semantic-correction.md).
- Ordinary multi-building PRs without a supported reproducible bulk submission are rejected (included in tools-v1.1.0)
- The 1-buildingID constraint and trailer match for normal commits; prohibition of duplicate buildingID commits within a PR
- Lifecycle events: one dedicated commit/PR, digest-bound event record, old/new ID and trailer-category matching, repository-wide participant ID uniqueness, explicit evidence and date fields, and generated event explanation. Real-world relationships remain a city judgment; see [event specification](lifecycle-events.md). Included in tools-v1.1.0; city pilot verification pending.
- Commit-scope exceptions for `lifecycle`, `layout`, `source-baseline` (first history entry only), `scope-extract`
- `identity-baseline` / `identity-correction` commits: trailer and manifest reference, byte-preserving ID replacement, tier rule, repository-wide ID uniqueness
- `source-update` value replacements within one attribute family: manifest-backed `Building:` commits, byte-exact application of the manifest's changes, all targets applied
- The `reproduction` gate re-fetches a bulk manifest's materials and regenerates it (identity and source-update kinds)
- Per-building history derived from git regardless of commit granularity (`scripts/building_history.py`: follows the building through ID changes, whole-file baselines and manifest-backed commits, reporting registry-keyed changes per commit); the `history-index.yml` workflow publishes it as a static Pages site (`history/index.html` + `history/buildings/<id>.json`) on every push to main
- Target-municipality set and retained-building invariance for `scope-extract`
- XML/XSD (i-UR 2.0–3.2 bundled), structure, references, textures, geometry checks and comparison views; PR comments on large PRs are truncated at 60,000 characters with a pointer to the artifacts
- Base-freshness guidance
- The hub: device-flow sign-in, fork → PR, checkpoint list in the repository language, Approve / Request changes, and a queue separating Draft / checking / waiting-for-latest-main / waiting-for-review
- The starter kit (release `starter-kit`) and its update mechanism (`.release-tag`)

## 2. To implement before unlocking the corresponding PR types

- Per-edition schema profiles as a validation option (today one master schema covers i-UR 2.0–3.2)
- Pilot verification of the implemented `identity-baseline` / `identity-correction` gates (commit scope rules + `identity` reproduction) on a real repository
- Pilot verification of the `source-update` value-replacement gate (one attribute family per PR; manifest-backed commits, reproduction) on a real repository
- Edition restructurings (attribute containers added or removed by a new edition) are handled by `carry-forward` (implemented) while official editions exist; the registry-driven `schema-migration` for the master-copy phase (re-serializer, semantic-equality gate, i-UR 4.0 registry) is designed but not implemented
- Full matching of Allowed-Paths, old/new values, and manifest IDs for `source-update`
- The release gate for final path signature and official-source consistency
- Subdivision / re-aggregation tools for deeper mesh levels where needed
- Extending the history index from buildingID → commit → PR → merge commit to the release
- Automatic application of the minimal-diff version for same-repo and fork PRs
- The ADR for patch-release tag naming, cadence, and urgency criteria
- A per-theme `source-baseline` gate (adding transportation, flooding, terrain … later) and broader gates for city-wide semantic corrections (the narrow LOD0 pilot recipe above does not unlock all such changes)
- A bulk-submission kind that takes an arbitrary external dataset as material (for example `external-update`): the city's published lists, cadastral maps, national land numerical information, 3DCityDB … as `materials`, the mapping table and replacement logic in the manifest, `reproduction` re-running through the connector
- A scheduled `tooling` workflow inside the city repository that checks upstream releases and opens the update PR itself (the Dependabot pattern), replacing PRs pushed by 4dcitygml
- Evaluate GitHub merge queue as a replacement for the manual rule that PRs on the same mesh are serialized
- Verify city-specific `oauthClientId` onboarding in the production pilot

Unimplemented dedicated gates are never substituted by documentation alone.
Confirm reject and revert behavior on a real repository before putting them
into public operation.
