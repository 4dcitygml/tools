# 3DCityDB integration implementation specification

Status: implementation baseline  
Version: 0.1  
Date: 2026-08-27

This specification defines the optional 3DCityDB integration for 4dcitygml.
The rationale and operating model are documented in
[`3dcitydb-github-integration.md`](3dcitydb-github-integration.md).

## 1. Objective

Provide a safe bridge between:

- the official GitHub repository, which is the shared and reviewed source of
  truth;
- an individual local Git checkout; and
- a local 3DCityDB workspace that may contain official, unpublished,
  provisional, and local-only data.

The integration must make every transfer across the public/private boundary
explicit, previewable, attributable, reversible where practical, and
fail-closed.

3DCityDB integration is optional. The ordinary 4dcitygml editing and review
workflow must work without installing, configuring, or exposing any database
functionality.

## 2. Supported baseline

- Canonical public data: the repository's reviewed semantic content, currently serialized as CityGML 2.0 + i-UR 3.x (the edition of the official distribution; see the integration model, "What is canonical").
- Database: 3DCityDB v5.
- Client/plugin API target: citydb-tool 1.3.2.
- Derived CityGML 3.0 + i-UR 4.0 artifacts are outside the live sync path.
- CityGML 3.0 conversion may be run separately for validation and scheduled
  downloads, but is not a prerequisite for database synchronization.

New citydb-tool releases must be validated in CI before being declared
compatible. Unknown major versions must fail closed with a compatibility
message.

## 3. Authority and data layers

### 3.1 Authority

The official GitHub repository is authoritative for shared data. A local
3DCityDB is an operational workspace and is never silently promoted to the
shared source of truth.

The official branch can be changed only through reviewed pull requests. The
connector has no permission to push directly to it.

### 3.2 Local database content

A local 3DCityDB may contain:

1. an official projection imported from GitHub;
2. proposed corrections to shared properties;
3. local-only properties attached to official objects;
4. provisional or unpublished features; and
5. operational data that will never be published.

Presence in 3DCityDB does not imply permission or intent to publish.

### 3.3 Publication profile

Every connection must have an explicit publication profile. The profile
classifies content as one of:

- `shared`: eligible for comparison and pull-request generation;
- `local-only`: retained in 3DCityDB but removed before Git comparison;
- `blocked`: must stop processing because its publication status is unknown.

The initial profile is derived conservatively:

- feature IDs already present in the official CityGML are shared features;
- new database-only features are local-only until explicitly marked as a
  publication candidate;
- properties already present in the official baseline are shared properties;
- newly introduced namespaces and property paths are blocked until added to
  either the shared or local-only rules;
- local-only namespaces and paths are stripped before semantic diffing and PR
  generation;
- the preview shows every stripped, retained, and proposed item.

Local-only properties on an official feature must be preserved when a newer
official version is imported. The updater therefore carries forward only
properties classified as local-only; it must not copy unknown properties.

Export filters and publication profiles are workflow controls, not security
boundaries. Confidential content must use a separate database/schema or
sidecar store with its own access control.

## 4. User-visible operations

The integration exposes two separate operations. The generic label `Sync`
must not be used for an operation that can run in both directions.

### 4.1 Update database from official repository

Purpose: apply approved public changes to the local database while retaining
declared local-only content.

Flow:

1. Acquire a per-connection operation lock.
2. Verify the configured official repository URL and branch.
3. Fetch the official branch without changing the working tree.
4. Read the last successfully applied Git commit SHA.
5. Determine changed files and building IDs between that SHA and the fetched
   official commit.
6. Export the affected current database features only.
7. Restore URO semantics from the database Generic representation.
8. Compare base, local database, and new official states.
9. Stop if the shared portion of a building changed both locally and
   officially. A declared local-only change by itself is not a conflict.
10. Carry forward declared local-only properties.
11. Convert URO wrappers to the Generic representation required for import.
12. Run semantic round-trip checks.
13. Run `citydb import --preview` and display the exact insert, terminate,
    replacement, and retained-local-data plan.
14. Require a separate **Apply to 3DCityDB** confirmation.
15. Apply with the appropriate history-preserving import/termination mode.
16. Post-export the affected IDs and verify the resulting semantics.
17. Record the fetched commit SHA only after all checks succeed.

Approved Git metadata is mapped to 3DCityDB import metadata:

- GitHub author or accepted public identity → `updating_person`;
- PR title/body reason → `reason_for_update`;
- repository, commit, PR, and CityGML path → `lineage`.

Preview never changes the Git working tree. Apply re-fetches the official
branch, requires the checkout to be clean and on the base branch, verifies
that the reviewed remote SHA has not moved, and then fast-forwards the local
checkout to that exact SHA before changing the database. If database Apply
subsequently fails, `lastAppliedCommit` remains unchanged and the same update
can be retried against the already-current local checkout.

### 4.2 Prepare PR from database changes

Purpose: propose selected shared database changes to the official repository.

Precondition: the database is synchronized with all semantically relevant
official CityGML changes.

Flow:

1. Acquire the operation lock.
2. Fetch the official branch.
3. Compare the fetched CityGML with the commit used by the last database
   update or the current review plan.
4. Stop if the official history no longer contains the base commit.
5. Stop with `Conflict detected` if the selected building changed officially.
6. Require **Update database from official repository** if other official
   buildings changed.
7. Permit continuation if the branch advanced but the tracked CityGML has no
   semantic change.
8. Export only publication candidates from 3DCityDB.
9. Restore Generic-encoded URO properties.
10. Apply the publication profile and report all excluded local-only content.
11. Compute semantic differences against the official CityGML.
12. Reconstruct a byte-minimal candidate based on official bytes.
13. Show one card per candidate building.
14. Use **Prepare PR** buttons, not multi-select checkboxes.
15. After selection, collapse the list to the selected building and provide
    **Change selection**.
16. Prefill public author, reason, and lineage from 3DCityDB FEATURE metadata;
    show the source field names as gray placeholders when no value exists.
17. Fetch and repeat the conflict check immediately before creating the PR.
18. Create a temporary worktree and a one-building branch and commit.
19. Push the proposal branch and open a pull request. Never push to the
    protected official branch.

The initial automatic PR scope is:

- one existing building;
- shared attribute changes;
- no geometry changes;
- no feature addition, deletion, split, merge, or rename; and
- no unresolved publication-profile item.

Unsupported changes remain visible but require manual review.

## 5. Conflict model

The connector uses optimistic concurrency control:

- Git commit SHA is the dataset base revision.
- Stable `gml:id` / 3DCityDB `objectid` is the feature identity.
- Building ID is the initial conflict unit after the publication profile has
  removed declared local-only content.

Conflict outcomes:

| Official state | Local DB state | Outcome |
|---|---|---|
| unchanged | changed | PR preparation allowed |
| changed, different building | changed | database update required |
| changed, same building | changed | stop for manual reconciliation |
| history rewritten/diverged | any | stop and rebuild the base state |
| formatting only | changed | continue after semantic verification |

The connector must not automatically merge two semantic or geometric changes
to the same building in the initial release.

## 6. Conversion pipeline

### 6.1 GitHub to database

```text
CityGML 2.0 + i-UR 3.x
  → select changed/public content
  → URO-to-Generic adapter
  → semantic verification
  → citydb import preview
  → history-preserving apply
```

No explicit CityGML 3.0 conversion is inserted into this path. 3DCityDB v5
accepts CityGML 2.0 directly, and avoiding a version switch reduces data-loss
risk.

### 6.2 Database to GitHub

```text
3DCityDB export as CityGML 2.0
  → Generic-to-URO adapter
  → publication profile
  → semantic diff
  → minimal reconstruction from official bytes
  → one-building PR
```

All transformations must be deterministic. The resulting candidate must be
semantically identical to the filtered database proposal and must leave every
unselected official building byte-identical.

## 7. State and audit

Non-secret connection state is stored in the OS application-data directory,
outside the public repository. It is keyed by official repository, database,
schema, and CityGML path and contains at least:

```json
{
  "schemaVersion": 1,
  "officialRepository": "https://github.com/example/city.git",
  "officialRemote": "origin",
  "baseBranch": "main",
  "citygmlPath": "citygml/mesh.gml",
  "databaseName": "citydb",
  "databaseSchema": "citydb",
  "lastAppliedCommit": "<40-character SHA>",
  "publicationProfile": "default"
}
```

Passwords, GitHub tokens, and private keys must not be stored in this state,
the distribution ZIP, logs, command-line arguments, or PR bodies.

Every crossing of the boundary records:

- direction;
- base and target Git SHAs;
- database/schema identity;
- affected building IDs;
- transformation and publication-profile versions;
- initiating public identity;
- reason and lineage;
- preview result and final result; and
- timestamp.

## 8. Optional installation and packaging

3DCityDB integration is disabled by default.

Each supported OS/architecture distribution contains an optional payload:

```text
optional/3dcitydb/
├── manifest.json
├── plugin/
│   └── 4dcitygml-citydb-sync.jar
├── connector/
│   └── platform-specific standalone executable
└── setup/
```

Initial supported release targets:

- macOS arm64 and x86_64;
- Windows x86_64; and
- Linux x86_64.

Selecting **Enable 3DCityDB integration** performs:

1. citydb-tool discovery or explicit selection;
2. compatibility check;
3. plugin installation into the citydb-tool plugin directory;
4. connector executable registration;
5. official repository and database configuration;
6. publication-profile setup; and
7. read-only connection tests.

The bundled connector executable removes the end-user Python and `lxml`
installation requirement. The setup remains reversible: disabling the
integration stops loading the plugin but does not delete Git, database, or
audit data.

Code signing, notarization, checksums, SBOM generation, and license notices are
release requirements for platform binaries.

## 9. Security requirements

- Bind the local UI to loopback by default.
- Use CSRF protection and no-store responses.
- Verify the configured official repository URL before fetch, push, or PR.
- Use a least-privilege GitHub App in the production design. Until then, check
  GitHub CLI authentication and scope before mutation.
- Use a least-privilege database role for preview/export and a separate role or
  explicit elevation for Apply.
- Protect `main` with PR review and required CI checks.
- Never log database passwords, access tokens, full environment dumps, or
  confidential local-only values.
- Treat publication filters as data-flow rules, not authorization controls.

## 10. Failure and recovery

- Any failed check leaves Git and the database unchanged where supported.
- State SHAs advance only after post-apply verification.
- A failed temporary worktree is removed; its proposal branch is retained only
  when useful for recovery and clearly reported.
- A partial database operation must not be reported as synchronized. Recovery
  requires post-export verification or restoration from the previous active
  feature versions/backup.
- Re-running Preview with identical inputs must produce the same plan.

## 11. Acceptance criteria

The initial implementation is acceptable when all of the following hold:

1. The integration is absent from normal UI until enabled.
2. Installation requires no separate Python package installation.
3. Official repository identity and a clean base are verified.
4. Update Preview does not mutate Git or the database.
5. Local-only sample data survives an official update and is absent from PR
   output.
6. Unknown data classification stops publication.
7. Same-building concurrent changes stop with a reproducible conflict report.
8. Different-building official changes require database update.
9. PR preparation repeats the remote check immediately before mutation.
10. One selected attribute change produces one building-only commit and PR.
11. No-op synchronization produces no Git or database change.
12. URO round-trip and semantic reconstruction checks pass.
13. Credentials do not appear in generated files, logs, or process arguments.
14. Platform packages load in citydb-tool 1.3.2 and pass signed-artifact smoke
    tests on every supported target.

## 12. Initial non-goals

- Automatic same-building semantic conflict merging.
- Automatic geometry pull requests.
- Automatic lifecycle PRs for adds, deletes, merges, splits, or renames.
- Treating same-schema filters as protection for confidential data.
- Making CityGML 3.0 the canonical live-sync representation.
- Synchronizing the entire database without a publication profile.
- Continuous background reconciliation without an explicit operator preview.

## 13. Required implementation sequence

1. Publication-profile model and redaction/carry-forward tests.
2. Persistent connection state and last-applied SHA.
3. Read-only official-to-database preview.
4. History-preserving database Apply and post-verification.
5. PR-preparation prerequisite enforcement and conflict UI.
6. Optional integration setup and disable flow.
7. Standalone connector builds and signed OS-specific distributions.
8. Real 3DCityDB end-to-end tests, failure injection, and public release gate.
