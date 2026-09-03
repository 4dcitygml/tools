<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Semantic attribute registry

`semantics/registry.json` gives every building attribute an **edition-independent
key** (`building.usage`, `detail.surveyYear`, `risk.river.rank`, …) and records
the concrete path of that attribute in each i-UR edition (`iur-2.0`, `iur-3.0`,
`iur-3.1`, `iur-3.2`). It is the piece that lets the repository's accumulated
changes survive an edition change: a change is remembered against a key, and
the key resolves to whatever path the current edition uses.

## What it encodes

- **Same**: most attributes keep their path across editions (`/usage`,
  `/buildingDetailAttribute/BuildingDetailAttribute/surveyYear`).
- **Renamed**: i-UR 3.1 renamed the data-quality, disaster-risk and key-value
  containers (`buildingDataQualityAttribute/BuildingDataQualityAttribute` →
  `bldgDataQualityAttribute/DataQualityAttribute`, …). The key stays, the path
  changes.
- **Split / merged**: i-UR 3.1 split `geometrySrcDesc` into one value per LoD;
  the registry records the predecessor/successor relation so a carry-forward
  can map the old single value onto the LoDs that exist.
- **Absent**: an edition without a path for a key does not have that attribute
  in its vocabulary (`quality.geometrySrcDesc.lod0` before 3.1).
- **Aliases**: early i-UR 2.0 datasets carry `rankOrg` where the revised
  schema has `rank`; both resolve to `risk.river.rank`.

Paths use element local names without prefixes, exactly as
`scripts/analyze_yearly_citygml_mesh.extract_attributes` produces them;
`[@name]` stands for a generic attribute's name. Every attribute carries the
attribute family used by the yearly planner (`storeys`, `source_quality`, …),
and `building.id` is marked `role: stable_id` (it is never carried forward as
a value change; identity is settled by `identity-baseline`).

## Using it

```bash
python3 scripts/semantic_registry.py crosswalk iur-3.0 iur-3.1     # what changed between editions
python3 scripts/semantic_registry.py lookup iur-2.0 /buildingDisasterRiskAttribute/BuildingRiverFloodingRiskAttribute/rankOrg
python3 scripts/semantic_registry.py edition FILE.gml               # which edition a file is
```

From Python: `key_for(path, edition)`, `path_for(key, edition)`,
`attributes_for(edition)`, `crosswalk(a, b)`, `detect_edition(raw)`.

## Adding an edition

1. Vendor the edition's XSD under `schemas/` (see `schemas/README.md`) and add
   it to `editions` with its `uro` namespace.
2. Add the edition's path to every key that exists in it; leave keys out that
   the edition does not have; add `predecessor` / `successors` for splits.
3. Run the tests: every registered path must exist in that edition's XSD, and
   every path observed in real data of the edition
   (`tests/fixtures/iur_paths_by_edition.json`) must resolve to a key.

Version 1 (2026-09) covers the attributes observed in PLATEAU building data
from 2020 to 2025 plus the schema-level building-detail attributes; i-UR 4.0
(CityGML 3.0) is added when the CityGML 3.0 path model is settled.

## Code-list crosswalks

`semantics/codelists/<from>__<to>.json` maps the codes of every coded
attribute between two editions. `scripts/codelist_crosswalk.py generate`
derives each edition's code list file from the attribute's path (PLATEAU
packages name them inconsistently: `BuildingDataQualityAttribute_*` in
i-UR 3.0 data, `DataQualityAttribute_*` from 3.1) and matches codes by label:

| relation | meaning | used by carry-forward |
|---|---|---|
| `exact` | same label once in the new list (or same code and label) | mapped automatically |
| `refined` | the old label is contained in one or more differently qualified new labels (1:n) | not mapped: the old code is **carried with the old edition's codeSpace** until a reviewer resolves it |
| `dropped` | no new code carries the label | carried with the old codeSpace |
| `added` | new codes without an old counterpart | informational |

Machine-generated entries have confidence `machine`; a reviewed overrides
file merged with `--reviewed` marks entries `reviewed`, and a reviewed entry
with a single target is then applied automatically. Identity code lists
(municipality, prefecture) are excluded — identity is never carried as a value.

Carrying with the old codeSpace is standard CityGML (`gml:CodeType`): the value
keeps its old code and its `codeSpace` points at that edition's list, which
the city repository keeps under `codelists/<edition>/`. Nothing is lost, the
pending decision is visible in the data, and the release gate can count what
is still unresolved before an official export.

A city keeps its own reviewed rules in `semantics/overrides.json` (the same
shape, grouped under `pairs["<from>__<to>"]`); the carry-forward merges them
over the shared crosswalk, and rules that turn out to be general are proposed
upstream by pull request.

Shipped crosswalks (2026-09): `iur-2.0__iur-3.0`, `iur-3.0__iur-3.1`,
`iur-3.1__iur-3.2`, generated from the code lists bundled in the PLATEAU
packages of those editions (file digests recorded per list).

## Known gap: code values

The registry maps *attributes*, not *values*. Editions also change code lists:
measured on PLATEAU packages, between 10 and 25 code lists change their code
set at every annual boundary, mostly by adding codes (harmless) but sometimes
by **recoding** (the 2024 data-quality source lists moved from one-digit to
three-digit codes with a finer meaning — "aerial photogrammetry" became either
"public survey result" or "non-public aerial photogrammetry") or by
**shrinking** (land use, detailed usage in 2023). A three-way comparison of a
coded attribute across such a boundary therefore reports a conflict even when
both sides mean the same thing, and a recoded value can be one-to-many.

Two rules apply: coded attributes (entries with a `codelist`) are compared
as **raw strings** — `000` and `0` are different codes, numeric normalization
must not touch them — and only 1:1 crosswalk relations are applied
automatically; everything else is carried with the old codeSpace (above) for
a reviewer. What remains open is the reviewed layer: rules for `refined`
codes (e.g. when a 2023 "aerial photogrammetry" was a public survey) depend
on how a city's data was produced and belong to the city's overrides.
