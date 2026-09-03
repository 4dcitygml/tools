<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 4D-CityGML PR Exchange Contract v2.1.0

This document is the **machine contract** between the city data repositories
and *any* client that submits pull requests — the official editors, your own
scripts, a company product, or a person using the GitHub web UI. Everything the
automated review enforces is specified here; **a submission that satisfies this
contract is treated identically regardless of which tool produced it**. We
actively welcome third-party clients: Part C lists the resources provided to
client developers.

- The key words MUST, MUST NOT, SHOULD, and MAY are to be interpreted as
  described in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).
- The contract is versioned with [Semantic Versioning](https://semver.org/)
  (this is v2.1.0; v2.0.0 was the formalization of the previously internal "exchange
  format v2"). Breaking changes bump the major version, are announced in the
  release notes of `4dcitygml/tools`, and get a deprecation window in which
  both old and new forms pass CI with a warning.
- Part A is machine-enforced (CI rejects violations). Part B is convention
  (reviewers expect it; violations are handled by human review, not by CI).

## Part A — machine-enforced (MUST)

### A1. Reason section in the PR body

The PR body MUST contain a level-2 heading line carrying the anchor
`<!--sec:reason-->`, followed by the reason and supporting evidence:

```markdown
## Summary of changes <!--sec:reason-->

Checked the field survey sheet and corrected the storey count from 2 to 3.
```

- Extraction regex (anchored form, language-independent):
  `^##[^\n]*<!--\s*sec:reason\s*-->[^\n]*$` — the section runs to the next
  `## ` heading. Without the anchor, CI falls back to these exact headings:
  `Reason and supporting evidence`, `Summary of changes`, `編集理由・根拠資料`,
  `変更理由`, `変更の理由`, `変更の概要`. New clients SHOULD always emit the anchor.
- After stripping HTML comments, the section MUST be ≥ 5 characters and MUST
  NOT contain a placeholder literal: `please fill in`, `not filled in`,
  `記入してください`, `未記入`, `TODO`, `TBD`. There is deliberately no
  "intentionally empty" sentinel: a reason is always required.

### A2. Commits: one change = one building, declared by git trailers

Trailers follow the standard
[git trailer format](https://git-scm.com/docs/git-interpret-trailers)
(`Token: value` lines in the last paragraph of the commit message), so any
language can read and write them with stock git tooling.

- A normal data commit MUST change exactly **one** building and MUST carry
  exactly one matching identity trailer:
  - `Building: <id>` — modification
  - `Building-Added: <id>` — addition
  - `Building-Deleted: <id>` — deletion

  `<id>` is the repository's stable building ID (`uro:buildingID`, e.g.
  `13101-bldg-3728`). CI cross-checks the trailer against the building
  actually changed in that commit's diff.
- Multi-building operations MUST declare exactly one `Change-Type:` trailer
  instead: `lifecycle` (merge/split/rebuild; list the old→new IDs),
  `layout` (mesh subdivision with an unchanged ID set), `source-baseline`
  (initial source recording), `scope-extract` (removing non-target
  municipalities). `identity-baseline` and `identity-correction` (replacing a
  building's `uro:buildingID`, one building per commit, with
  `Building-ID-From:` / `Building-ID-To:` / `Identity-Evidence:` trailers) are
  used by bulk submissions (A7): the commit scope gate checks the trailers,
  the byte-preserving replacement, the manifest reference and tier, and
  repository-wide ID uniqueness, and the `reproduction` gate re-executes the
  manifest from its declared materials.
- Commits that touch no CityGML data (docs, code) MUST NOT carry building
  trailers.
- Commit messages MUST be English (the history is a language-independent,
  greppable record; see A5 for how this differs from PR text).

Example commit message:

```text
Update attributes (Storeys Above Ground): 2 → 3

Checked the field survey sheet.

Building: 13101-bldg-3728
Created-By: my-city-editor/1.4 (https://example.com/contact)
```

### A3. Editing style: byte-preserving edits

Clients MUST NOT re-serialize whole CityGML files. Edit only the byte span of
the target building (or the specific leaf values), preserving the original
bytes everywhere else — whitespace, attribute order, encoding, newlines.
A naive "parse → modify → write the whole DOM" implementation will fail the
**minimal diff** check even when the semantic change is correct, because it
rewrites every line. This is the single most common reason a technically
correct third-party submission fails CI.

### A4. Textures

- Existing images MUST NOT be overwritten under the same name. Texture changes
  are made by **adding new image files and updating the `imageURI`** values
  (a shared image may be referenced by other buildings). The only exception is
  the maintainer-applied `texture-override` label.
- PRs that add or replace photos MUST include the rights confirmation
  (consent to the
  [Data Contribution Policy](https://github.com/4dcitygml/city-template/blob/main/docs/data-contribution-policy.md)
  §1–§2: own photo, lawful location, privacy masking, CC0 1.0).

### A5. Classification: branch prefix (preferred) or title prefix

- Branch names SHOULD use the classifying prefixes: `edit/…` (attribute
  changes), `tex/…` (texture changes). The official editors generate
  `edit/<building-id>-<timestamp>`.
- Without a branch prefix, CI and the review UI fall back to title prefixes
  (exact, front-anchored): attribute = `Update attributes` /
  `Update building info` / `属性修正` / `Attributkorrektur`; texture =
  `Update textures` / `Add textures` / `テクスチャ` / `Textur`.
- All other checks run regardless of classification; misclassification mainly
  degrades the review screen, so branch prefixes are the robust choice.

### A6. What CI checks (the thirteen gates)

Each PR gets an inspection-summary comment with one row per check, keyed by a
stable `<!--cp:key-->` anchor: `reason`, `commit-scope`,
`scope-reproducibility`, `reproduction`, `freshness`, `file-scope`, `schema`,
`minimal-diff`, `texture`, `structure`, `plausibility`, `topology`, `model`
(`reproduction` = re-execution of a manifest-backed bulk conversion, A7; not
applicable to ordinary PRs). Result cells
always carry a machine-stable emoji (✅ ❌ − …); display names follow the
repository language. In the practice repositories any ❌ blocks the merge
(strict gate); clients MAY parse this comment to show results in their own UI
(the hub review screen is the reference implementation of that parsing).

### A7. Bulk submissions: provenance manifest (verify by reproduction)

A PR whose data commits were generated by a program (`source-update`,
`carry-forward`, `identity-baseline`, `identity-correction`, `schema-update`, `schema-migration`, `layout`, and
the already-gated `source-baseline` / `scope-extract`) MUST ship the
provenance of that generation so CI can **reproduce** it instead of anyone
reading thousands of commits:

- a manifest file `provenance/<kind>/<mesh>-<from>-<to>.json` conforming to
  [`schemas/provenance/bulk-manifest.schema.json`](../schemas/provenance/bulk-manifest.schema.json)
  (materials with digests, builder = immutable `4dcitygml/tools` commit SHA,
  exact invocation, products with digests, per-building evidence, sample
  audit);
- `Provenance-Manifest: <path>@sha256:<hex>` on every data commit, in
  addition to A2's trailers;
- a plan issue opened before the PR and linked from the PR body
  (`Plan-Issue:`), and a dedicated submitting account.

CI re-fetches the materials, re-runs the invocation at the pinned tools
commit, and byte-compares the result with the PR; humans review the plan,
the manifest, and a random sample — never individual buildings. The full
policy, the submitter's checklist, and the gate status are in
[Bulk submissions: provenance, verification, and merge policy](bulk-submission-provenance.md).
Reserved trailers for the identity kinds: `Building-ID-From:`,
`Building-ID-To:`, `Identity-Evidence:`, `Corrects:`.

## Part B — conventions (SHOULD / MAY)

- **Language**: PR title and body SHOULD be written in the repository's
  working language (`lang` in `4dcitygml.json`; the readers are that city's
  reviewers). CI itself is language-independent, so other languages do not
  fail checks. Commit messages stay English (A2).
- **Client identification**: every commit created by a tool SHOULD carry a
  trailer `Created-By: <app>/<version> (<contact URL or email>)` — version and
  contact are optional but a reachable contact is strongly recommended.
  Clients MUST NOT impersonate another client's name. This is not (currently)
  machine-enforced; it exists so maintainers can reach the right author when a
  pattern of submissions needs discussion, and so the ecosystem can be
  credited. The official editors emit it themselves.
- **Start in the sandbox**: a new client SHOULD make its first submissions
  against a practice repository (see Part C) rather than a production city.
- **Bulk submissions**: before generating a large batch of PRs, open an issue
  describing the plan (scope, source, rate). Keep one logical change per PR
  (one mesh per PR for conversions — the PR is the rollback unit). The
  machine-checked part of a bulk submission is A7.

## Part C — resources for client developers

- **Sandbox**: the practice repositories (e.g. `sample-tokyo-station`,
  `sample-munich-station`, `sample-newyork-station`) run the **full real
  pipeline** — all thirteen gates, strict gate, auto-merge — and are reset
  periodically. Submitting practice PRs there is the intended way to develop
  and test a client; you cannot damage anything.
- **Local validators (identical to CI)** — run from a city-repo clone with
  this repository checked out:

  ```bash
  python3 scripts/commit_building_scope.py --repo . --base-sha <BASE> --head-sha <HEAD>
  python3 scripts/citygml_lint.py <changed.gml>      # geometric structure
  python3 scripts/plateau_lint.py <changed.gml>      # attribute plausibility
  python3 scripts/reviewability_lint.py …            # minimal diff
  python3 scripts/validate_citygml.py <changed.gml>  # schema
  ```

  The attribute editor's pre-send check ("pretest") runs the same suite; CI
  runs the same scripts with the same versions.
- **Machine-readable CI feedback**: parse the inspection comment by
  `<!--cp:key-->` + emoji (A6); never parse display names, which are
  localized.
- **Contact**: open an issue in `4dcitygml/tools` (for private matters, use the
  report form linked from the organization's `SUPPORT.md`). A "known clients" page will list published clients
  after launch — send yours.

## Examples

A complete manual attribute PR:

- Branch `edit/13101-bldg-3728-fix-storeys`, one commit as in A2's example.
- PR title: `属性修正: 地上階数` (tokyo repo — repo language; English also works).
- PR body: the repository's PR template with the reason section filled in
  under the `<!--sec:reason-->` heading.

## Contract changelog

- **v2.1.0** (2026-09) — adds A7 (bulk submissions: provenance manifest,
  `Provenance-Manifest:` trailer, verify-by-reproduction), the
  `identity-baseline` / `identity-correction` change types with their
  trailers, and the thirteenth gate `reproduction` (A6). Additive: every v2.0.0
  submission remains valid.

- **v2.0.0** (2026-09) — first published version. Formalizes: sec:reason
  anchor, building trailers + Change-Type exceptions, byte-preserving edits,
  texture R1, branch/title classification, cp:key inspection comments,
  repo-language convention, `Created-By:` client identification.
