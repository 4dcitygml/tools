<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 4D-CityGML PR Exchange Contract v3.1.0

This document is the **machine contract** between the city data repositories
and *any* client that submits pull requests — the official editors, your own
scripts, a company product, a CAD add-in, an automated agent, or a person using
the GitHub web UI. Everything the automated review enforces is specified here;
**a submission that satisfies this contract is treated identically regardless
of which tool produced it**. We actively welcome third-party clients: Part C
lists the resources provided to client developers.

- **This contract is the highest authority for submissions.** Where a tool, a
  guide, or a CI implementation disagrees with this document, the document is
  right and the implementation is the defect. The official hub and editors are
  reference clients of this contract, nothing more; using them is never
  required.
- **Rules are applied strictly, and replies are helpful.** A submission that
  violates Part A is not merged. CI explains every rejection in a comment that
  says what to change and how, so that a first-time contributor can fix the
  submission without asking anyone. Being strict and being kind are both
  requirements.
- The key words MUST, MUST NOT, SHOULD, and MAY are to be interpreted as
  described in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).
- The contract is versioned with [Semantic Versioning](https://semver.org/)
  (this is v3.1.0; v2.0.0 was the formalization of the previously internal
  "exchange format v2"). Breaking changes bump the major version, are announced
  in the release notes of `4dcitygml/tools`, and get a deprecation window in
  which both old and new forms pass CI with a warning.
- Part A is machine-enforced (CI rejects violations). Part B is convention
  (reviewers expect it; violations are handled by human review, not by CI).
- Everything CI accepts or reads is listed here. If CI accepts something this
  document does not list, that is a documentation defect to be fixed by adding
  it here or removing it from CI — never by leaving it undocumented.

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
- Gate: `reason`. Editing the PR body re-runs CI (no new commit is needed).

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
  `13101-bldg-3728`; other datasets use the ID named by `building_id` in
  `4dcitygml.json`). CI cross-checks the trailer against the building actually
  changed in that commit's diff.
- Multi-building operations MUST declare exactly one `Change-Type:` trailer
  instead. The accepted values are exactly: `lifecycle` (merge/split/rebuild),
  `layout` (mesh subdivision with an unchanged ID set), `source-baseline`
  (initial source recording), `scope-extract` (removing non-target
  municipalities).
  - `lifecycle` commits MUST also carry exactly one
    `Lifecycle-Manifest: provenance/lifecycle/<event>.json@sha256:<hex>`
    trailer; the manifest lists the old→new IDs, the reason, and the evidence
    (see [Building lifecycle events](lifecycle-events.md)). `Lifecycle-Manifest:`
    on any other change type is rejected.
  - `scope-extract` commits MUST carry exactly one
    `Scope-Municipality: <municipality code>` trailer naming the municipality
    being kept, and MUST NOT list per-building trailers.
- `identity-baseline` and `identity-correction` (replacing a building's
  `uro:buildingID`, one building per commit, with `Building-ID-From:` /
  `Building-ID-To:` / `Identity-Evidence:` trailers) are used by bulk
  submissions (A7): the commit scope gate checks the trailers, the
  byte-preserving replacement, the manifest reference and tier, and
  repository-wide ID uniqueness, and the `reproduction` gate re-executes the
  manifest from its declared materials.
- Commits that touch no CityGML data (docs, code) MUST NOT carry building
  trailers.
- Commit messages MUST be English (the history is a language-independent,
  greppable record; see A5 for how this differs from PR text).
- The complete list of trailers the commit scope gate reads: `Building:`,
  `Building-Added:`, `Building-Deleted:`, `Change-Type:`, `Scope-Municipality:`,
  `Lifecycle-Manifest:`, `Provenance-Manifest:`, `Building-ID-From:`,
  `Building-ID-To:`, `Corrects:`. `Identity-Evidence:` is read from the
  provenance manifest, not from the commit. Trailers that official tools emit
  for human readers but no gate reads: `Created-By:` (Part B),
  `Carry-Forward-From:` and `Source-To:` (carry-forward commits). Any other
  trailer is ignored.
- Gate: `commit-scope`.

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
correct third-party submission fails CI. Gate: `minimal-diff`.

### A4. Textures

- Existing images MUST NOT be overwritten under the same name. Texture changes
  are made by **adding new image files and updating the `imageURI`** values
  (a shared image may be referenced by other buildings). The only exception is
  the maintainer-applied `texture-override` label (A9).
- PRs that add or replace photos MUST include the rights confirmation
  (consent to the
  [Data Contribution Policy](https://github.com/4dcitygml/city-template/blob/main/docs/data-contribution-policy.md)
  §1–§2: own photo, lawful location, privacy masking, CC0 1.0).
- Gate: `texture`.

### A5. Classification: every data PR declares what kind of change it is

A PR that changes data files MUST be classifiable by CI into exactly one of
the classes below. Classification decides which checks apply (for example the
topology scope) and how reviewers see the proposal. **A data PR that CI cannot
classify is rejected** by the `classification` gate; the rejection comment
shows this table and the two ways to fix it (rename the branch, or edit the PR
title — editing the title re-runs CI without a new commit).

| Class | Branch prefix (preferred) | Title prefix fallback (exact, front-anchored) | Title keyword fallback (anywhere in the title) |
|---|---|---|---|
| `attribute` | `edit/` | `Update attributes`, `Update building info`, `属性修正`, `Attributkorrektur` | — |
| `texture` | `tex/` | `Update textures`, `Add textures`, `テクスチャ`, `Textur` | — |
| `geometry` | `geom/`, `geometry/` | — | `geometry`, `building shape`, `rebuild`, `幾何`, `建物形状`, `建替`, `建て替` |
| administrative | — | — | declared by trailers instead: `Change-Type:` (A2) or `Provenance-Manifest:` (A7) |
| `other` | — | — | the PR changes **no** data files (documentation, configuration, the CI tools pin); building trailers are forbidden (A2). Code is not accepted by a city repository at all (A11) |

- Branch prefix wins over title. The official editors generate
  `edit/<building-id>-<timestamp>` and `tex/<building-id>-<timestamp>`.
- A data PR that matches none of the rows above is not classified as anything;
  it is rejected with guidance. There is no silent default.
- `other` is a real class, not a leftover: it is how documentation, configuration
  and CI-pin PRs travel. An `other` PR that turns out to change data files is
  rejected by `classification` (it must be reclassified) and by `file-scope`.
- Review clients MUST show every open PR whatever its class, including
  `other` and unclassified ones; a client that hides a PR because it cannot
  classify it violates this contract.
- Gate: `classification` (new in v3.0.0; see A6 and the changelog for the
  transition).

### A6. What CI checks (the fourteen gates)

Each PR gets an inspection-summary comment with one row per check, keyed by a
stable `<!--cp:key-->` anchor: `reason`, `classification`, `commit-scope`,
`scope-reproducibility`, `reproduction`, `freshness`, `file-scope`, `schema`,
`minimal-diff`, `texture`, `structure`, `plausibility`, `topology`, `model`
(`reproduction` = re-execution of a manifest-backed bulk conversion, A7; not
applicable to ordinary PRs). Result cells always carry a machine-stable emoji
(✅ ❌ − …); display names follow the repository language. In the practice
repositories any ❌ blocks the merge (strict gate); clients MAY parse this
comment to show results in their own UI (the hub review screen is the
reference implementation of that parsing). Review clients MUST take a gate's
result from this comment or from the GitHub check runs; they MUST NOT
re-implement a gate with their own rules and show a different verdict.

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
`Building-ID-To:`, `Corrects:` (with `Identity-Evidence:` recorded in the
manifest); carry-forward commits additionally carry the informational
`Carry-Forward-From:` and `Source-To:` (A2). Gates: `scope-reproducibility`,
`reproduction`.

### A8. Requesting a re-inspection

Any client — or a person typing in the GitHub comment box — MAY ask CI to run
the automated inspection again by posting an issue comment on the PR that
contains the marker `<!-- citygml-ci-retry-request -->`.

- Who may ask: the PR author, or an account whose association with the
  repository is `OWNER`, `MEMBER` or `COLLABORATOR`. Anyone else receives the
  reply "Only the proposer or a maintainer can request a re-inspection." and
  nothing runs.
- What runs: by default the most recent completed analysis run for the PR's
  current head commit is re-run. The comment MAY additionally contain
  `<!-- citygml-retry-workflow:pr-comment.yml -->` to re-publish the existing
  results, or `<!-- citygml-retry-workflow:review-report.yml -->` to re-verify
  the machine report; any other value is ignored and the default applies.
- Replies: "🔄 The automated inspection was started again." on success; "No
  re-runnable automated inspection record was found." when the head commit has
  no completed run; nothing when the PR is closed.
- A re-inspection never changes the data under test. It repeats the same
  checks on the same head commit; to change the result, push a fix.

### A9. Labels CI reads

CI reads exactly three labels. All are applied by maintainers; a contributor
MUST NOT rely on them and cannot apply them.

| Label | Effect |
|---|---|
| `texture-override` | Permits replacing an existing image under the same name (A4). |
| `identity-review` | Permits tier-C identity links in an `identity-correction` submission (A7, commit scope gate). |
| `tooling` | Accepts a CI maintenance change under `.github/**` that is more than a pin update (A11); the report records it. |

No other label has any effect on CI. (`city-review` was a label of an earlier
manual procedure; CI ignores it and it is no longer part of any contract.)

### A10. Machine-readable outputs

CI communicates through issue comments marked with HTML comments. The markers
are stable identifiers; the surrounding text is localized and MUST NOT be
parsed.

| Marker | Meaning |
|---|---|
| `<!-- citygml-automatic-inspection -->` | Inspection summary, one `<!--cp:key-->` row per gate (A6). |
| `<!-- citygml-change-summary -->` | Table of changed values derived from the diff (tool-independent). |
| `<!-- citygml-commit-scope -->`, `<!-- citygml-reviewability-lint -->`, `<!-- citygml-quality-lint -->` | Detailed findings of the `commit-scope`, `minimal-diff` and `file-scope` / `structure` gates. |
| `<!-- citygml-base-freshness -->` | The PR is behind main and must be updated. |
| `<!-- citygml-auto-resubmission -->` | CI asked the proposer to fix and resubmit. |
| `<!-- citygml-ci-retry-request -->` | A re-inspection request (A8; written by clients, read by CI). |

Check runs on the head commit: `analyze` (the inspection run) and, where the
city publishes machine reports, `ci-report`. GitHub's required-checks setting
on `main` is the authoritative list; this contract does not add to it.

### A11. What a city repository accepts

A city repository holds **data, documents and configuration**. Every line of
code that runs on a contributor's or a resident's computer — the hub, the
editors, the launcher — comes from `4dcitygml/tools` releases, verified against
the SHA-256 digest GitHub publishes for each asset. Cities neither distribute
code nor pin a client version.

The `file-scope` gate therefore accepts a PR only when every changed file is:

- **data**: under a directory named in `data_dirs` of `4dcitygml.json` (or a
  `*/udx/` folder of the PLATEAU layout), or under `provenance/`;
- **documents**: under `docs/`, or README / LICENSE / NOTICE / CONTRIBUTING /
  SUPPORT / CHANGELOG / SECURITY and other `*.md` files at the root;
- **configuration**: `4dcitygml.json`, `theme.json`, the logo image,
  `.gitignore`, `.gitattributes`, `.github/CODEOWNERS`,
  `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/**`;
- **the CI tools pin**: a `.github/workflows/*.yml` file whose only changed
  lines are `CITYGML_TOOLS_REF:` values, where the new value is a commit that a
  `tools-v*` tag of `4dcitygml/tools` points to (CI checks the tags API);
- **CI maintenance under the `tooling` label** (A9): any other change under
  `.github/**`, accepted only when a maintainer applied the label.

Everything else is rejected — in particular executable files anywhere
(`.py .sh .command .ps1 .bat .js …`), `install/**`, `tools/**` — with a comment
that names the files and says where such a change belongs (`4dcitygml/tools`).
This is what lets a city's approvers review data and never read code.

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
- **Automated and AI-assisted submissions** are welcome under the same rules
  as any client. In addition they SHOULD: name the automation in
  `Created-By:` (for example `Created-By: acme-agent/2.0 (automated;
  https://example.com/contact)`) so reviewers know no person checked the
  change before submission; state in the reason section what evidence the
  change is based on; open a plan issue before any batch of more than a few
  PRs (rate, scope, source); and stop submitting when a maintainer asks in
  that issue. A human is accountable for every account that submits.
- **Start in the sandbox**: a new client SHOULD make its first submissions
  against a practice repository (see Part C) rather than a production city.
- **Bulk submissions**: before generating a large batch of PRs, open an issue
  describing the plan (scope, source, rate). Keep one logical change per PR
  (one mesh per PR for conversions — the PR is the rollback unit). The
  machine-checked part of a bulk submission is A7.

## Part C — resources for client developers

- **Sandbox**: the practice repositories (e.g. `sample-tokyo-station`,
  `sample-munich-station`, `sample-newyork-station`) run the **full real
  pipeline** — all gates, strict gate, auto-merge — and are reset
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
  `<!--cp:key-->` + emoji (A6) and the markers in A10; never parse display
  names, which are localized.
- **Reference clients**: the hub (review screen, re-inspection requests) and
  the attribute/texture editors (submission) in this repository implement this
  contract and nothing beyond it. Where their behavior and this document
  differ, file an issue: the document wins.
- **Contact**: open an issue in `4dcitygml/tools` (for private matters, use the
  report form linked from the organization's `SUPPORT.md`). A "known clients" page will list published clients
  after launch — send yours.

## Examples

A complete manual attribute PR:

- Branch `edit/13101-bldg-3728-fix-storeys`, one commit as in A2's example.
- PR title: `属性修正: 地上階数` (tokyo repo — repo language; English also works).
- PR body: the repository's PR template with the reason section filled in
  under the `<!--sec:reason-->` heading.

A documentation-only PR (`other`): branch name free, no building trailers, the
reason section still filled in (A1 applies to every PR).

## Contract changelog

- **v3.1.0** (2026-09) — adds A11 (what a city repository accepts: data,
  documents, configuration, the verified CI tools pin; code is rejected with
  guidance) folded into the `file-scope` gate, and the maintainer label
  `tooling` (A9). Clarifies that `other` covers documentation and
  configuration, not code. Additive for every data submission; a code change
  proposed to a city repository was never part of the contract.

- **v3.0.0** (2026-09) — **breaking**: A5 makes classification mandatory for
  data PRs and adds the `classification` gate (fourteen gates, A6). Until now
  CI silently treated an unclassified data PR as `attribute`; from v3.0.0 it
  is rejected with guidance. Transition: CI releases that implement v3.0.0
  block by default; a city may set the repository variable
  `CITYGML_CLASSIFICATION_WARN_ONLY=true` for its first release under v3.0.0,
  which posts the same guidance as an advisory instead of blocking. No
  third-party client had been published under v2, so no longer window is
  needed. Also: the contract is declared the highest
  authority and the official tools reference clients; review clients must
  show every PR and must not re-implement gates (A5, A6); the `geometry`
  class and its prefixes/keywords, which CI already accepted, are documented;
  `Lifecycle-Manifest:`, `Scope-Municipality:` and the complete list of
  trailers the gates read are documented (A2, A7); the re-inspection request interface (A8), the
  labels CI reads (A9), and the comment markers (A10) are documented; Part B
  covers automated and AI-assisted clients. Every v2.1.0 submission that was
  classifiable remains valid.

- **v2.1.0** (2026-09) — adds A7 (bulk submissions: provenance manifest,
  `Provenance-Manifest:` trailer, verify-by-reproduction), the
  `identity-baseline` / `identity-correction` change types with their
  trailers, and the thirteenth gate `reproduction` (A6). Additive: every v2.0.0
  submission remains valid.

- **v2.0.0** (2026-09) — first published version. Formalizes: sec:reason
  anchor, building trailers + Change-Type exceptions, byte-preserving edits,
  texture R1, branch/title classification, cp:key inspection comments,
  repo-language convention, `Created-By:` client identification.
