<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Building lifecycle events: rebuild, split and merge

City tooling is distributed independently as `tools-v1.1.0`.
See [distribution status](shared-tooling-release.md) before adopting it.

One real-world event is one PR with one dedicated commit, even when it involves
several `uro:buildingID` values. An event is not an arbitrary batch of unrelated
building corrections, and does not claim the reproducibility of a bulk conversion.
The normal one-building rule and manifest-backed bulk submissions remain available.

| Kind | Before → after | Meaning |
|---|---|---|
| `rebuild` | One or more → one or more | One documented rebuilding project |
| `split` | One → two or more | One building divided into several |
| `merge` | Two or more → one | Several buildings combined into one |

IDs present on both sides may be retained where the city's identity policy permits.
At least one actual addition or deletion is required for this dedicated route.
A geometry change without added/deleted building IDs follows the normal correction
route. A standalone new building or demolition of one building can use the normal
`Building-Added:` or `Building-Deleted:` route. Do not invent old or new IDs merely
to satisfy an event's cardinality. ID assignment itself remains a separate decision.

## Event record and commit

Start with [the example](examples/lifecycle-manifest.example.json), replace the
illustrative IDs and evidence, and save it as `provenance/lifecycle/<event>.json`
in the city repository. The record is submitted evidence, not a city approval.

- `version`: `1`.
- `eventId`: a stable event identifier not already recorded in this repository.
- `kind`, `oldIds`, `newIds`: one event and its participants on each side.
- `reason`: why these buildings form this event.
- `confirmedOn`: the date the proposer checked the evidence; it is not the approval date.
- `occurredOn`: optional actual event date, if known. Never substitute the survey date.
- `evidence`: at least one document title and HTTP(S) URL. CI checks the record's
  structure; it does not establish that the source proves the relationship.

From the city repository, validate the record and calculate its reference:

```sh
python /path/to/tools/scripts/lifecycle_manifest.py provenance/lifecycle/rebuild-2026-001.json
```

Include that printed reference and the data in the same commit:

```text
Record one rebuilding event

Change-Type: lifecycle
Building-Deleted: 40220-bldg-old-a
Building-Deleted: 40220-bldg-old-b
Building-Added: 40220-bldg-new-c
Lifecycle-Manifest: provenance/lifecycle/rebuild-2026-001.json@sha256:<printed SHA-256>
```

Use `Building:` only for a surviving building whose content actually changed.
Do not put retained, unchanged participants in change trailers; they remain in
both `oldIds` and `newIds`. Every actual changed ID must be listed once in its
correct trailer category. Do not combine a `Provenance-Manifest` conversion or
an identity-correction trailer with this event.

Before submission, run the normal commit-scope check against the PR base/head.
Fix the dedicated commit with amend/rebase when necessary; do not add a second
building commit or another event to the PR. Update the reference if the record
changes. Once recorded on main, do not silently replace an earlier event record
or reuse its eventId. Corrections to accepted event history need an explicit
correction proposal; a dedicated event-history correction tool is not provided.

## What CI verifies

The commit-scope check validates the event record, its SHA-256, cardinality and
required evidence/date fields. It checks added, deleted and modified trailers
separately; compares the old/new sets with the actual changes; rejects changes
to buildings outside the declared participants; and checks participant ID
uniqueness across the repository. The PR must contain one dedicated commit,
which changes exactly the referenced lifecycle record. Multiple records or
other commits cannot be bundled into it.

The normal XML, reference, texture, geometry and other applicable gates still
run. Ordinary multi-building additions/deletions cannot bypass the rule merely
because the aggregate diff was classified as lifecycle.

The generated CI report includes the event ID, old/new relation, reason, dates
and evidence, followed by the actual diff summary. Reviewers see the same report.
The city must decide whether the declared participants really form one event,
whether the sources establish it, and whether the proposed geometry and ID policy
are appropriate. Structural validation cannot prove those real-world facts.

## Deployment status

Included in tools-v1.1.0; city pin updates and pilot verification are still required. Test genuine
rebuild/split/merge PRs (including multiple meshes and comparison views) and rejection
cases in the pilot before enabling this submission route. The current editing
screens do not author lifecycle events; prepare the GML and event record separately.
