#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The commit scope gate's rules for the PLATEAU pack's bulk manifest kinds (Pack.bulk_checks):
a source update or carry-forward applies the manifest's changes building by building; a
semantic correction is the narrow LOD0 recipe, reproduced exactly. Moved from
scripts/commit_building_scope.py unchanged."""
from __future__ import annotations

import os

from dialects.plateau import lod0_semantic_manifest as L
from dialects.plateau.source_update_manifest import apply_changes_to_member
from scripts.building_identity import IDENTITY_KINDS
from scripts.repo_git import blob as _blob, changed


def source_update(repo, base_sha, head_sha, results, manifest, ref_path) -> None:
    """Bulk source-update and carry-forward commits: every changed building listed in
    evidence.targets exactly once, and each commit's building bytes equal to the parent's bytes
    with the manifest's changes applied."""
    if manifest.get("kind") not in ("source-update", "carry-forward"):
        for r in results:
            r.errors.append(f"Manifest kind {manifest.get('kind')!r} does not match Building: commits (expected source-update or carry-forward).")
        return
    bulk = [r for r in results if r.manifest_ref and r.change_type not in IDENTITY_KINDS and r.changed_ids]

    per_building: dict[str, list[dict]] = {}
    for change in manifest.get("evidence", {}).get("changes", []):
        per_building.setdefault(change["id"], []).append(change)
    targets = set(manifest.get("evidence", {}).get("targets", []))
    seen: set[str] = set()
    unlisted = [r for r in results if r.changed_ids and not r.manifest_ref and r.change_type not in IDENTITY_KINDS]
    for r in unlisted:
        r.errors.append("A bulk PR contains only manifest-backed commits; this commit has no Provenance-Manifest trailer.")
    for r in bulk:
        stable = next(iter(r.changed_ids))
        if stable not in targets:
            r.errors.append(f"{stable} is not among the manifest's targets.")
            continue
        if stable in seen:
            r.errors.append(f"{stable} appears in more than one commit of this PR.")
        seen.add(stable)
        before, after = r.member_before, r.member_after
        try:
            expected = apply_changes_to_member(before, per_building.get(stable, [])) if before is not None else None
        except SystemExit as exc:
            expected = None
            r.errors.append(f"Manifest changes for {stable} cannot be applied: {exc}")
        if expected is not None and expected != after:
            r.errors.append(f"The bytes of {stable} differ from the parent with the manifest's changes applied (extra or missing edits).")
    missing = sorted(targets - seen)
    if missing and not any(r.errors for r in bulk):
        bulk[0].errors.append(f"The PR applies {len(seen)} of the manifest's {len(targets)} targets (missing e.g. {missing[0]}).")


def semantic_correction(repo, base_sha, head_sha, results, manifest, ref_path):
    """Exact file-level and per-commit verification of the narrow LOD0 recipe."""
    try:
        L.contract(manifest)
        L.safe_path(ref_path)
        if not ref_path.startswith('provenance/semantic-correction/'):
            raise ValueError('Semantic manifest must be under provenance/semantic-correction/')
        product = manifest['products'][0]['path']
        if manifest['materials'][0]['uri'] != f'git:{base_sha}:{product}':
            raise ValueError('Current material must identify the PR base and exact product path')
        if os.environ.get('GITHUB_REPOSITORY') and manifest['repository'] != os.environ['GITHUB_REPOSITORY']:
            raise ValueError('Manifest repository differs from the city repository')
        before = _blob(repo, base_sha, product)
        if before is None:
            raise ValueError('Current product missing at PR base')
        expected = L.reproduce(manifest, before)
        if _blob(repo, head_sha, product) != expected:
            raise ValueError('Full PR product differs from the exact semantic transformation')
        seen = set()
        for r in results:
            # (a merge has no building change: the check below refuses it)
            if not set(changed(repo, r.sha)) <= {product, ref_path}:
                raise ValueError('Semantic PR changes a file outside its product and manifest')
            if r.change_type or len(r.changed_ids) != 1 or not r.manifest_ref:
                raise ValueError('Each semantic commit must be a manifest-backed single Building change')
            stable = next(iter(r.changed_ids))
            if stable in seen or stable not in manifest['evidence']['targets']:
                raise ValueError('Repeated or unlisted semantic target')
            seen.add(stable)
            raw = _blob(repo, f"{r.sha}^", product)   # its parent's bytes
            out, _ = L.transform(raw, manifest['scope']['municipality'], [stable])
            if _blob(repo, r.sha, product) != out:
                raise ValueError('Commit changes bytes beyond the declared building property names')
        if seen != set(manifest['evidence']['targets']):
            raise ValueError('Semantic PR must apply every declared target exactly once')
    except (ValueError, KeyError, TypeError, RuntimeError, L.safe_xml.ParseError, L.expat.ExpatError) as exc:
        results[0].errors.append('Semantic correction: ' + str(exc))
