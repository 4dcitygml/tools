# 3DCityDB and GitHub integration model

The normative implementation requirements are defined in
[`3dcitydb-integration-implementation-spec.md`](3dcitydb-integration-implementation-spec.md).

## Positioning

The integration uses the official GitHub repository as the shared and
reviewed source of truth. Each operator has an individual local Git checkout
and may use a local 3DCityDB as a search, visualization, and editing workspace.
The database is not assumed to contain only publishable data. Moving a change
between GitHub and the database is therefore an explicit, reviewed operation,
not transparent bidirectional replication.

This is a GitOps-like projection model rather than direct database
synchronization:

```text
official GitHub repository (shared, reviewed)
              ↓ update
local Git checkout (individual)
              ↓ selected import
local 3DCityDB (working and possibly private data)
              ↓ selected proposal
GitHub pull request
```

The model is a good fit for CityGML because the public dataset is file-based,
features have stable identifiers, and semantic changes can be reviewed at the
building level. GitHub history records the accepted public state; 3DCityDB
provides operational querying, visualization, local enrichment, and feature
history.

## What is canonical, and where 3DCityDB sits

The repository is canonical for the *semantic content* of the city: per
building, the reviewed attribute values, geometry, sources, and the history of
who changed what and why. The CityGML edition in which that content is
serialized (today CityGML 2.0 with i-UR 3.x) is the format of the current
official distribution, not part of what is canonical. When the official
edition changes (a new i-UR version, CityGML 3.0), the repository re-bases on
the new edition and carries its accumulated changes forward; the content and
its provenance survive the format change.

3DCityDB therefore plays three distinct roles, none of which makes it a second
source of truth:

1. **Working copy** — an operator's local editing, search, and visualization
   workspace, on the same footing as the attribute editor or any third-party
   client. Its changes reach the repository only through pull requests under
   the [exchange contract](exchange-contract.md); bulk changes ship a
   provenance manifest and are accepted by reproduction.
2. **Semantic index** — a queryable, schema-aware view of the repository
   (feature history, spatial queries, enrichment with local-only data). It is
   rebuilt from the repository and never edited in place as a way of changing
   the public data.
3. **Conversion and export engine** — 3DCityDB v5 stores the CityGML 3.0
   conceptual model and exports CityGML 2.0 or 3.0. This is the natural path
   for producing the *official* edition from the repository once the
   repository is the master copy of a city (repository state → import →
   export in the edition the official channel requires), and for the CityGML
   3.0 transition. Every export in this role is gated by the semantic
   round-trip check below; ADE (i-UR) fidelity through the generic-attribute
   adapter is the known risk and the reason the check is mandatory.

Merging an official edition or a database-side change into the repository is
a three-way comparison per building and attribute: the previous official
value, the repository value, and the new value. The previous value tells
*who changed what*; a change on one side only is applied, an identical change
on both sides is recorded as absorbed, and a change on both sides to
different values stops for human review. The connector's conflict check
(next section) is the same idea applied at building granularity with the Sync
base commit as the previous state; property-level merging is a later
refinement, not a different model.

### Edition updates: what 3DCityDB guides, and what it does not

Checked against the 3DCityDB v5 / citydb-tool 1.3 documentation (2026-09):

- **CityGML version changes are guided.** The v5 schema implements the
  CityGML 3.0 conceptual model; `citydb-tool` imports 1.0/2.0/3.0 and exports
  any version (`--citygml-version`), i.e. "on-the-fly upgrading and
  downgrading". Two rules matter for us: data is lossless only when the same
  version is used for import and export, and there are no downgrade options —
  3.0 content that cannot be expressed in 2.0 is skipped on export. Upgrading
  2.0 data needs explicit choices (`--use-lod4-as-lod3`,
  `--map-lod0-roof-edge`, `--map-lod1-surface`). Consequence: an export in
  the conversion role records the import version and the options used in its
  provenance manifest, and the semantic round-trip check decides whether the
  skipped or re-mapped content is acceptable.
  ([import](https://docs.3dcitydb.org/1.3/citydb-tool/import-citygml/),
  [export](https://docs.3dcitydb.org/1.3/citydb-tool/export-citygml/),
  [compatibility](https://docs.3dcitydb.org/1.3/compatibility/))
- **Database version changes are "export and re-import".** There is no tool
  that upgrades a v4 database in place; the documented path is CityGML 2.0
  export from v4, a fresh v5, and re-import. This matches the model above:
  the database is rebuilt from the repository and is never the place where
  data survives a migration.
  ([migration](https://docs.3dcitydb.org/1.3/first-steps/migration/))
- **ADE (i-UR) edition changes are not guided.** v5 stores properties in a
  type-enforced `PROPERTY` table with a namespace registry and can keep
  original XML in `val_content`, and lists ADE support as a feature, but the
  citydb-tool 1.3 documentation describes no ADE registration or mapping.
  Measured with 3DCityDB 5.1.4 and citydb-tool 1.4.0 (2026-09): importing a
  PLATEAU building file (CityGML 2.0 + i-UR 3.0) succeeds, generic attributes
  survive, but **every i-UR element is silently dropped** — no `uro`
  namespace is registered, no property row carries i-UR content, and an
  export contains none of the 8,758 `uro:` elements of the input. Typed i-UR
  storage therefore needs both the registry rows (`ade`, `namespace`,
  `datatype`/`objectclass` JSON schemas) and an import side that knows them
  (a citydb-tool extension, or an adapter around the standard import and
  export). The connector's adapter already exists: `citydb-tool -x` applies
  `connectors/3dcitydb/xslt/uro_to_generic.xsl` on import (i-UR wrappers
  become generic attribute sets, codes keep their codeSpace, measures their
  uom) and `generic_to_uro.py` restores the wrappers on export; measured on a
  full mesh (807 buildings, 44,276 i-UR leaves) it retained 100 % (2026-07).
  Moving from one i-UR edition to the next (2.0 → 3.x → 4.0) remains the
  repository's own job: the semantic attribute registry and the carry-forward
  merge described in the city repository's PR operations guide, not a
  database feature.
  ([feature module](https://docs.3dcitydb.org/1.3/3dcitydb/feature-module/))

## Shared and local-only database content

3DCityDB v5 can store content beyond the official repository and citydb-tool
can restrict exports by feature ID, feature type, CQL2, SQL, bounding box, and
other query criteria. This makes a mixed local workspace technically possible.
However, an export filter is a workflow boundary, not a confidentiality or
access-control boundary.

The connector therefore follows these rules:

1. A feature is not publishable merely because it exists in the database.
2. Existing official features are selected by stable `gml:id` / `objectid`.
3. New database-only features require an explicit publication decision.
4. Private properties must be removed by an explicit publication profile
   before semantic comparison and PR generation.
5. Confidential data should use a separate database/schema or a sidecar data
   store. It must not rely only on an export filter in a mixed schema.
6. A preview must show the exact buildings and properties that would cross the
   boundary.

Official references:

- [3DCityDB export configuration and CQL2 filters](https://docs.3dcitydb.org/1.3/citydb-tool/export-config/)
- [3DCityDB database schema connection option](https://docs.3dcitydb.org/1.3/citydb-tool/export-citygml/)

## Synchronization directions

The user interface presents two separate operations:

- **Update database from official repository**: pulls approved changes into
  the local working database after preview and conflict checks.
- **Prepare PR from database changes**: proposes selected database changes to
  the official repository. It never pushes directly to the protected branch.

Before preparing a PR, the connector fetches the official branch and compares
the selected building against changes made after the Sync base commit:

1. If the same building changed on both sides, preparation stops with a
   conflict.
2. If other official buildings changed, database update is required before a
   PR can be prepared.
3. If the official branch advanced without a semantic change to the tracked
   CityGML, preparation may continue.
4. The check is repeated immediately before PR creation.

This is optimistic concurrency control using the Git commit SHA as the base
revision and the building ID as the conflict unit. It reduces routine Git
conflict adjustment without silently merging semantic or geometric changes.

## Safety and provenance

- Sync and import operations use preview-before-apply.
- Import updates preserve database history with `terminate` rather than
  destructive replacement where appropriate.
- Conversion is deterministic and must pass semantic round-trip checks.
- Pull requests contain one building change and record source, reason, and
  public author.
- 3DCityDB `lineage`, `reason_for_update`, and `updating_person` are used for
  local audit metadata and PR form prefill.
- The official branch requires PR review and CI; the connector has no direct
  push permission to it.
- Credentials are not stored in the distribution ZIP or public repository.

## Optional installation

3DCityDB integration is an optional component of each OS-specific 4dcitygml
distribution. It is disabled by default and can be enabled later from the
integration settings. Enabling it installs the citydb-tool plugin and bundled
connector runtime, then asks for the local database, official repository, and
GitHub connection details.
