<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Production protection: a deliberate human step for every production change

Written 2026-09-13. Its home is the organization's `.github` repository once
the common-settings thread exists; until then this copy is the reference.

## Principle

Development happens in private development copies of each repository, where
trial and error, pushes and pull requests are free. The production repositories
(owner `4dcitygml`) receive only tidy changes, and **every production change
requires one explicit human action inside GitHub** (merging a pull request, or
approving a deployment). No path from a development copy, a script or a
workflow may reach production without that action.

One maintainer account administers everything. GitHub does not let an account
approve its own pull request, so the human step is the merge (with zero
required approvals) and, for releases, the deployment approval (where
self-approval is allowed). A second account would turn the merge into a
review; that is a separate decision.

## Current state (read on 2026-09-13)

| Repository | `main` | Bypass | Release tags | Environments |
|---|---|---|---|---|
| tools | ruleset `main-pr`: PR required (0 approvals), no deletion, no force push | **none** | ruleset `release-tags` (`install-v*`, `hub-v*`, `tools-v*`): creation/update/deletion restricted | none |
| city-template | **no ruleset** (direct push possible) | — | — | none |
| sample-tokyo-station | ruleset `main protection`: PR required (1 approval, code owner), status check `analyze`, no deletion, no force push; ruleset `baseline protection` on `baseline` | **repository role, always** (the single account cannot self-approve, so merges use the bypass) | — | github-pages |
| sample-munich-station | same as tokyo | repository role, always | — | github-pages |
| sample-newyork-station | same as tokyo | repository role, always | — | github-pages |
| 4dcitygml.github.io | **no ruleset** | — | — | github-pages |
| .github | **no ruleset** | — | — | none |

The organization is on the Free plan: organization-level rulesets are not
available, so protection is configured per repository (seven times). The
development copies carry no remote that names production, and their CI has no
secrets; the only remaining path to production is the maintainer account
itself, which is why the rules below allow no bypass.

## Target state

### 1. `main` of every production repository: pull requests only, no bypass

- Rules: `pull_request` (0 required approvals, dismiss stale reviews),
  `deletion`, `non_fast_forward`; where the repository has CI on pull
  requests, `required_status_checks` (`analyze` for city repositories).
- Bypass actors: none. The administrator has no shortcut either; a mistake in
  a development copy cannot land on `main` without a merge click.
- Decision (2026-09-13): the practice repositories' `daily-reset.yml` (a
  force push of `main` from `baseline`, needing a `RESET_TOKEN` secret that no
  repository has) is **removed**, together with its optional practice-log
  archive. The no-bypass ruleset is kept. Resetting becomes a manual,
  history-preserving operation (see "Practice reset" below).
- Practice repositories keep `practice-auto-merge.yml`: it approves in-scope
  practice pull requests and enables auto-merge, and GitHub merges them once
  the required `analyze` check passes. That is compatible with a no-bypass
  ruleset; the review requirements decide how much the path allowlist is
  backed by CODEOWNERS (see "Open decision" below).

### Practice reset (replaces the daily reset)

`manual-reset.yml`, `workflow_dispatch` with the typed confirmation `RESET`,
guarded by `vars.PRACTICE_REPO`, using only `GITHUB_TOKEN`:

1. Close open practice pull requests with a note.
2. Create branch `reset/<date>` from `main` with one commit that replaces
   the city's data directories (`data_dirs` in `4dcitygml.json`) with their
   content in `baseline` and touches nothing else, carrying the trailers
   `Change-Type: practice-reset` and `Reset-To: <baseline commit>`. The
   `baseline` branch holds the pristine data; workflows, documents and
   configuration evolve on `main` and are never part of a reset, so `baseline`
   is re-cut only when the data itself changes.
3. Open a pull request to `main` marked `<!-- citygml-practice-reset -->`,
   whose description tells the person to add the label `practice-reset` (the
   workflow creates the label if missing).
4. A person adds the label, which runs the checks: a pull request opened by
   `GITHUB_TOKEN` triggers no workflow by itself, and `pr-analysis.yml` also
   runs on `labeled` and `edited`.
5. The person merges it: the deliberate step. `main` returns to the baseline
   content without any rewrite; history stays.

Prerequisites: CI must accept `Change-Type: practice-reset` as an
administrative change (commit scope gate, Exchange Contract A2; tools since
2026-09-13), otherwise the reset pull request fails `analyze`; and the
repository setting "Allow GitHub Actions to create and approve pull requests"
must be on, otherwise the workflow cannot open the pull request (the practice
auto-merge needs the same setting for its approval). `vars.PRACTICE_REPO`
must name the repository itself, or both workflows stay inert.

### Open decision: review requirements of the practice repositories

- *1 approval + code-owner review + no bypass*: the single account cannot
  approve its own pull requests, so the owner's maintenance changes to a
  practice repository could never be merged. Not workable with one account.
- *0 approvals + no bypass*: workable; the CODEOWNERS layer behind the
  auto-merge is then only as strong as the workflow's path allowlist.
- *0 approvals + code-owner review + no bypass*: keeps the CODEOWNERS layer
  if GitHub still demands the owner's review for owned paths at count 0.
  To be verified in the rehearsal; adopt it if it holds.
The reset pull request is authored by the bot, so the owner may approve it
under any of these settings.

### 2. Release tags (tools): keep the tag ruleset, gate the release itself

`release-tags` restricts creation, update and deletion of `install-v*`,
`hub-v*`, `tools-v*`. It keeps the repository-role bypass: without it nobody,
including the administrator, could create a release tag. The human step for a
release is therefore not the tag but the deployment approval below.

### 3. Environment `production` with a required reviewer (tools)

- Create environment `production` in `4dcitygml/tools` with the maintainer
  as required reviewer; leave "prevent self-review" off; restrict deployment
  branches and tags to `hub-v*`, `tools-v*`, `install-v*`.
- The upload jobs of `release-hub.yml` and `release-tools.yml` declare
  `environment: production`. The build and
  the smoke tests still run unattended; the job that publishes assets waits
  for "Approve deployment" in the Actions view. Nothing reaches users without
  that click.
- A release tag is pushed only through `scripts/release_guard.py`, which
  reads the environment back (a required reviewer, self-review allowed, a tag
  policy admitting the tag), checks that the commit is on `main`, that its
  workflow gates the release job and, with `--expect-tree`, that its tree is
  the rehearsed one; it pushes the tag only when all of that holds:
  `python3 scripts/release_guard.py --repo 4dcitygml/tools --tag tools-v1.3.0 --commit <sha> --expect-tree <tree> --push`.
  A manual re-publish (`release_tag` input) must be dispatched from the tag,
  not from `main`, unless the policy also admits `main`.

### 4. Repositories without releases (city-template, .github, 4dcitygml.github.io)

Rule 1 only. For `4dcitygml.github.io` the existing `github-pages` environment
can additionally require a reviewer if publishing the site should also be a
deliberate step.

## Procedure

1. **Rehearse in the development copies.** Apply the same rulesets and
   environment to their remotes; run the affected workflows there (a practice
   reset, a release from a tag, a pull request merge). Record what is refused
   and what passes. One feature cannot be rehearsed privately: on GitHub Free,
   Pro and Team plans, required reviewers (and wait timers) exist only in
   public repositories, so a private copy's `production` environment runs
   without the approval step. The approval itself is verified once, on a
   history-free public rehearsal repository or at the first production
   release, after reading back the environment's rules.
2. **Fix what the rehearsal breaks** in the development copies, then in
   production through the normal pull-request route.
3. **Apply to production** from the common-settings thread, one repository at
   a time, with the read-only check afterwards.
4. **Record** the applied state in this document (table above) with the date.

### Bringing a change to production

- Verification finishes in the development copies; production is never used
  to verify. A tools change is released (tagged, built, approved) on the tools
  copy's own remote first, the city copies pin that tagged commit through
  their own CI (`CITYGML_TOOLS_REPO` naming the copy, `CITYGML_TOOLS_TOKEN`
  for a private copy), and only what passed there is ported.
- A city may pin only a commit a `tools-v*` tag points to (A11): the release
  precedes every pin, in the copies and in production alike.
- Production never sees the development copies' history: the change is
  ported by **copying the final files**, committed once per repository with a
  plain English subject such as "Update the shared runtime and the release
  workflows" (the detailed history stays in the development copy).
- Before committing, check the ported text for anything that belongs to the
  development copies only (their repository names, local paths, account ids,
  drafting notes) and generalize it.
- Confirm that production `main` is at the commit the development copy
  started from; run the full test suite on the ported tree; then open the
  pull request and merge it (the human step).
- Order across repositories: tools first (city CI pins a tools commit), then
  city-template and the practice repositories, then the protection settings,
  then the release. Moving the `install-v1` tag is a separate, explicit step.

### Commands

Ruleset for `main` (POST `/repos/{owner}/{repo}/rulesets`):

```json
{
  "name": "main-pr",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": true,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false}}
  ]
}
```

For city repositories add
`{"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": false, "required_status_checks": [{"context": "analyze"}]}}`.

```
gh api -X POST repos/<owner>/<repo>/rulesets --input main-pr.json
gh api -X PUT repos/4dcitygml/tools/environments/production \
  --input - <<'EOF'
{"reviewers": [{"type": "User", "id": <maintainer user id>}],
 "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
gh api -X POST repos/4dcitygml/tools/environments/production/deployment-branch-policies \
  -f name='hub-v*' -f type=tag
```
(repeat the last command for `tools-v*` and `install-v*`).

Read-only check, for every repository:

```
gh api repos/<owner>/<repo>/rulesets --jq '.[] | [.name, .target, .enforcement]'
gh api repos/<owner>/<repo>/rulesets/<id> --jq '{bypass: .bypass_actors, rules: [.rules[].type]}'
gh api repos/<owner>/<repo>/environments --jq '.environments[] | [.name, [.protection_rules[].type]]'
```

## Development-copy side (already in force)

- Development copies name no production remote; `origin` is their own
  repository and `remote.pushDefault=origin`. Production updates are fetched
  by explicit URL when needed, never by adding a remote.
- Development copies hold no secrets or variables; their CI acts on their own
  repository only.
- A development copy of a city that carries `4dcitygml.json` still names the
  production city; a hub or editor pointed at such a clone would open pull
  requests there. Until the tools stop naming cities (generalization work),
  point that `4dcitygml.json` at the development copy.
