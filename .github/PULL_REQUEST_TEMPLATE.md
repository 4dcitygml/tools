<!--
Copyright (c) 2026 4dcitygml
Common template for building-data PRs (the counterpart of the issue templates).
Maps to CI's 3-way triage (A churn / B scope / C lifecycle).
Specialist sections that do not apply to everyday fixes can be left blank.
Administrative PRs (source-update etc.) must fill in all required sections.
-->

## PR type
<!-- Pick exactly one. Types whose dedicated CI is not implemented yet must not be marked Ready for review. -->
- [ ] `correction` (everyday attribute / geometry / position / texture fixes)
- [ ] `lifecycle` (rebuild, split, merge)
- [ ] `identity-correction` (fixing a mis-linked ID in published history)
- [ ] `source-update` (applying an official source / annual edition)
- [ ] `schema-update` (adding edition-specific artifacts and validation profiles)
- [ ] `carry-forward` (re-basing the repository's changes onto a new official edition)
- [ ] `schema-migration` (registry-driven re-serialization into a new edition when the repository is the master copy)
- [ ] `layout` (semantics-preserving mesh subdivision)
- [ ] `texture-gc`
- [ ] `revert`
- [ ] code / documentation only

## Target buildings / scope
<!-- The stable uro:buildingID (e.g. 13101-bldg-3728). Multi-building PRs: one ID per commit. Administrative PRs: specify the mesh or manifest. -->
-

## Summary of changes <!--sec:reason-->
<!-- What was changed and why: position fix / height correction / texture replacement etc., in 1-2 lines. -->


## Change type
<!-- Check all that apply. If unsure, describe the situation under "Other". -->
- [ ] Attribute fix (storeys, usage, area, etc.)
- [ ] Geometry fix (shape, height, roof form, etc.)
- [ ] Position fix (correcting misalignment)
- [ ] Texture fix (replacement, filling gaps, etc.)
- [ ] Lifecycle (building merge / split / rebuild; involves add/delete)
- [ ] Other:

## Source / manifest (source-update / schema / layout etc.)
<!-- For everyday corrections, supporting evidence alone is fine. Required for the administrative PR types below. -->
- Source-From:
- Source-To:
- Scope-Mesh:
- Attribute-Family:
- Allowed-Paths:
- History-Manifest:
- Manifest-SHA256:
- Building-Count:
- First-Building-ID:
- Last-Building-ID:

## Checklist
- [ ] Created from the latest main, with no conflict against earlier PRs on the same mesh
- [ ] For normal updates, each commit is **1 commit = 1 `uro:buildingID`**
- [ ] For normal updates, fixes to the same buildingID are not split across multiple commits in the PR
- [ ] The `Building:` (etc.) trailer of each building commit matches the actually changed buildingID
- [ ] For multi-building PRs, if any single building fails a blocking CI check, the whole PR is fixed
- [ ] If a geometry preview was shown, the appearance was checked with 🔴 before / 🔵 after
- [ ] For lifecycle changes, the **reason for the merge / split / rebuild** is written under "Summary of changes"
- [ ] For texture changes, no existing image is **overwritten under the same name** (exception: `texture-override`)
- [ ] If there is a related issue, it is linked with `Fixes #<number>` or `Refs #<number>`
- [ ] The applicable checklist in the [post-publication PR procedures](https://github.com/4dcitygml/city-template/blob/main/docs/pr-operations.md) was reviewed

## Related issues
<!-- Fixes if the change closes it, Refs if merely related. "None" if none. -->


## Additional notes (optional)
<!-- Supporting documents, sources for the correct values, points you want reviewers to look at. -->
