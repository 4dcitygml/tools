#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Check whether each commit in a PR adheres to "normal update: 1 commit = 1 building ID".

Viewing only the base→head diff of the entire PR makes it impossible to distinguish
between history where multiple buildings are changed in separate commits vs. changed in
one commit. This gate checks each commit in ``base..head`` sequentially and cross-references
the buildings actually changed in CityGML with commit trailers. A building is named by its
stable ID under the city's rule in 4dcitygml.json (``building_id``; scripts/building_identity.py).

Normal updates:

* Fix: ``Building: <building ID>``
* Add: ``Building-Added: <building ID>``
* Delete: ``Building-Deleted: <building ID>``

Exceptions are only ``Change-Type: lifecycle`` (enumerate multiple IDs as old→new relationships),
``Change-Type: layout`` (layout change with unchanged ID set), ``Change-Type: source-baseline``
(initial source recording), ``Change-Type: scope-extract`` (removal of non-target municipalities),
and the identity kinds ``identity-baseline`` / ``identity-correction`` (exactly one building's
ID replaced, declared by ``Building-ID-From`` / ``Building-ID-To`` and backed by a
``Provenance-Manifest`` — see docs/bulk-submission-provenance.md), ``schema-update``
(edition artifacts only — code lists, schema profiles — with no CityGML change at all), and
``practice-reset`` (a practice repository returning to its baseline: the city's data
directories equal those of the commit named by ``Reset-To`` and nothing outside them changes;
history is kept, nothing is rewritten).
Documentation/code-only commits do not require building trailers.

Usage:
    python scripts/commit_building_scope.py \
        --repo . --base-sha <PR_BASE_SHA> --head-sha <PR_HEAD_SHA>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.repo_git import GitError, blob as _blob, changed, commits as range_commits, git as _git  # noqa: E402
from scripts.provenance_manifest import parse_manifest_ref, sha256_hex, validate as validate_manifest  # noqa: E402
from scripts import city_declaration  # noqa: E402
from scripts.repo_scope import is_data, is_schema  # noqa: E402
from scripts.gate_result import malformed_input  # noqa: E402
from scripts.building_identity import (IdentityRule, building_spans, replace_stable_id,  # noqa: E402
                                       rule_from_config, stable_id)
from scripts.building_identity import (CHANGE_TYPES as ACCEPTED_CHANGE_TYPES, GENERIC_KINDS, IDENTITY_KINDS,  # noqa: E402
                                       WHOLE_FILE_KINDS, trailer_buildings, trailers as parse_trailers)
from scripts.texture_check import building_appearance_sig  # noqa: E402
from scripts.lifecycle_manifest import validate as validate_lifecycle  # noqa: E402



@dataclass
class Snapshot:
    """State keyed by stable ID, obtained from the changed GML files."""

    members: dict[str, bytes] = field(default_factory=dict)
    appearance: dict[str, frozenset[tuple[str, str, str]]] = field(default_factory=dict)
    gml_to_stable: dict[str, str] = field(default_factory=dict)
    municipalities: dict[str, str | None] = field(default_factory=dict)
    duplicates: set[str] = field(default_factory=set)


@dataclass
class CommitResult:
    sha: str
    subject: str
    change_type: str = ""
    changed_ids: set[str] = field(default_factory=set)
    added_ids: set[str] = field(default_factory=set)
    deleted_ids: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    identity_from: str = ""
    identity_to: str = ""
    manifest_ref: str = ""
    trailers: dict = field(default_factory=dict)   # every trailer of the commit, as parsed
    lifecycle: dict | None = None
    member_before: bytes | None = None   # manifest-backed normal commits: the building's bytes at the parent
    member_after: bytes | None = None    # ... and at the commit (kept so the PR-level check need not re-read blobs)

    @property
    def ok(self) -> bool:
        return not self.errors



def _snapshot(blobs: list[bytes], rule: IdentityRule = IdentityRule(), municipality=None) -> Snapshot:
    snapshot = Snapshot()
    appearance_sets: dict[str, set[tuple[str, str, str]]] = {}

    for raw in blobs:
        local_gml_to_stable: dict[str, str] = {}
        for gml_id, (start, end) in building_spans(raw).items():
            member = raw[start:end]
            stable = stable_id(member, gml_id, rule)
            local_gml_to_stable[gml_id] = stable
            snapshot.gml_to_stable[gml_id] = stable
            snapshot.municipalities[stable] = municipality(member) if municipality else None
            digest = hashlib.sha256(member).digest()
            if stable in snapshot.members:
                snapshot.duplicates.add(stable)
            else:
                snapshot.members[stable] = digest

        for gml_id, signatures in building_appearance_sig(raw).items():
            stable = local_gml_to_stable.get(gml_id, gml_id)
            appearance_sets.setdefault(stable, set()).update(signatures)

    snapshot.appearance = {
        stable: frozenset(signatures) for stable, signatures in appearance_sets.items()
    }
    return snapshot


def _member_bytes(blobs: list[bytes], stable: str, rule: IdentityRule = IdentityRule()) -> bytes | None:
    for raw in blobs:
        for gml_id, (start, end) in building_spans(raw).items():
            member = raw[start:end]
            if stable_id(member, gml_id, rule) == stable:
                return member
    return None




def _data_path_test(repo: Path, sha: str):
    """Whether a path belongs to the city's data as declared at that commit (repo_scope.is_data:
    the declared data_dirs, else the CityGML files; and provenance/)."""
    dirs = city_declaration.read(repo, sha).data_dirs
    return lambda path: is_data(path, dirs)


def _data_blobs(repo: Path, sha: str, in_data) -> dict[str, str]:
    """path -> blob id of every data path at that commit."""
    out = {}
    for line in str(_git(repo, "ls-tree", "-r", sha)).splitlines():
        meta, _, path = line.partition("\t")
        if in_data(path):
            out[path] = meta.split()[2]
    return out


BASE_UPDATE = "base-update"   # a merge that only brings in the PR's base (A2, v4.0.0)


def is_base_update(repo: Path, sha: str, parents: list[str], base_sha: str) -> bool:
    """Whether a merge commit only brings the PR's base into it, as GitHub's "Update branch"
    makes one (default merge mode): two parents, one of them in the base's history, and nothing
    in the merge that git's own re-merge would not produce (`git show --remerge-diff` is empty:
    no hand-made resolution, no change of its own)."""
    def in_base(parent: str) -> bool:
        try:
            _git(repo, "merge-base", "--is-ancestor", parent, base_sha)   # exit 1: not an ancestor
            return True
        except GitError:
            return False
    if len(parents) != 2 or not base_sha or not any(in_base(parent) for parent in parents):
        return False
    try:
        return not str(_git(repo, "show", "--remerge-diff", "--format=", sha)).strip()
    except GitError:   # a git without --remerge-diff (before 2.36) cannot tell: refused as before
        return False


def inspect_commit(repo: Path, commit: dict, base_sha: str = "") -> CommitResult:
    """One commit of the PR, as repo_git.commits lists it (sha, parents, subject, message)."""
    sha, parents = commit["sha"], commit["parents"]
    result = CommitResult(sha=sha, subject=commit["subject"].strip())
    if is_base_update(repo, sha, parents, base_sha):
        result.change_type = BASE_UPDATE   # its content is the base; the other commits are inspected
        return result
    if len(parents) != 1:
        result.errors.append(
            "Merge commits inside a PR branch cannot be inspected, except a merge that only brings in "
            "main (GitHub's Update branch). Rebase onto main for a linear history."
        )
        return result

    parent = parents[0]
    paths = changed(repo, sha, suffix=".gml")   # renames as both paths
    trailers = parse_trailers(commit["message"])
    result.trailers = trailers
    change_types = trailers.get("Change-Type", [])
    change_type = change_types[-1].lower() if change_types else ""
    result.change_type = change_type
    identities = trailer_buildings(trailers)
    if trailers.get('Lifecycle-Manifest') and change_type != 'lifecycle':
        result.errors.append('Lifecycle-Manifest requires Change-Type: lifecycle.')

    if len(change_types) > 1:
        result.errors.append("Specify exactly one Change-Type trailer.")
    # U1: the city's declaration as the PR's base holds it judges every commit of the PR
    declaration = city_declaration.read(repo, base_sha or sha)
    pack = declaration.pack
    if (change_type in ACCEPTED_CHANGE_TYPES - GENERIC_KINDS - pack.kinds
            or (trailers.get("Provenance-Manifest") and change_type not in IDENTITY_KINDS and not pack.bulk_kinds)):
        result.errors.append(f"{change_type or 'A manifest-backed change'} comes with a dialect pack: the city's "
                             "4dcitygml.json declares no dialect with it, so it does not apply here "
                             "(Exchange Contract A2).")
        return result
    if change_type and change_type not in ACCEPTED_CHANGE_TYPES:
        result.errors.append(f"Unknown Change-Type {change_type!r}. Accepted values: "
                             + ", ".join(sorted(ACCEPTED_CHANGE_TYPES)) + " (Exchange Contract A2).")

    if change_type == "practice-reset":
        # A practice repository returning to its baseline: the city's data directories
        # (and only they) become what the commit named by Reset-To holds. Compared on the
        # blob ids of every data path, so the reset can neither smuggle a change nor miss one;
        # workflows, documents and configuration on main are left as they are.
        targets = trailers.get("Reset-To", [])
        if identities:
            result.errors.append("practice-reset commits must not list per-building trailers.")
        if len(targets) != 1:
            result.errors.append("Specify exactly one Reset-To: <commit> trailer for practice-reset.")
            return result
        try:
            _git(repo, "rev-parse", "--verify", f"{targets[0]}^{{commit}}")
        except RuntimeError:
            result.errors.append(f"The Reset-To commit {targets[0]} is not available in this checkout.")
            return result
        in_data = _data_path_test(repo, base_sha or sha)
        outside = [p for p in changed(repo, sha) if not in_data(p)]
        if outside:
            result.errors.append("A practice-reset commit changes only the city's data directories; unexpected: "
                                 + ", ".join(outside[:5]))
        if _data_blobs(repo, sha, in_data) != _data_blobs(repo, targets[0], in_data):
            result.errors.append("A practice-reset commit must restore the data directories exactly as its Reset-To commit holds them.")
        return result

    if not paths:
        if change_type == 'lifecycle':
            result.errors.append('A lifecycle commit must change CityGML buildings.')
        if identities:
            result.errors.append("A commit without CityGML changes has a building-ID trailer.")
        if change_type in IDENTITY_KINDS:
            result.errors.append(f"A {change_type} commit carries no CityGML change (documentation-only commits must not declare it).")
        if change_type == "schema-update":
            # the artifacts an edition brings (repo_scope's schema category, U11), its provenance and docs/
            outside = [f for f in changed(repo, sha) if not (is_schema(f, declaration)
                                                  or f.startswith(("provenance/schema-update/", "docs/")))]
            if outside:
                result.errors.append("schema-update commits change only edition artifacts (" + ", ".join(
                    [*(d + "/" for d in pack.schema_dirs), "provenance/schema-update/", "docs/"])
                    + "); unexpected: " + ", ".join(outside[:5]))
        return result
    if change_type == "schema-update":
        result.errors.append("schema-update commits must not change CityGML data (add the edition's code lists / schema profile only).")
        return result

    old_blobs = [raw for path in paths if (raw := _blob(repo, parent, path)) is not None]
    new_blobs = [raw for path in paths if (raw := _blob(repo, sha, path)) is not None]
    rule = rule_from_config(declaration.resolved)
    old = _snapshot(old_blobs, rule, pack.municipality)
    new = _snapshot(new_blobs, rule, pack.municipality)

    if old.duplicates or new.duplicates:
        dup = sorted(old.duplicates | new.duplicates)
        result.errors.append(f"Duplicated building ID inside the changed GML: {', '.join(dup)}")

    old_ids, new_ids = set(old.members), set(new.members)
    result.added_ids = new_ids - old_ids
    result.deleted_ids = old_ids - new_ids
    for stable in old_ids | new_ids:
        if (
            old.members.get(stable) != new.members.get(stable)
            or old.appearance.get(stable, frozenset())
            != new.appearance.get(stable, frozenset())
        ):
            result.changed_ids.add(stable)

    if change_type == "source-baseline":
        parent_paths = str(_git(repo, "ls-tree", "-r", "--name-only", parent)).splitlines()
        if any(path.endswith(".gml") for path in parent_paths):
            result.errors.append("Commit is source-baseline, but the parent commit already has GML files.")
        if identities:
            result.errors.append("source-baseline commits must not list per-building trailers.")
        return result

    if change_type == "layout":
        if old_ids != new_ids:
            result.errors.append("The building ID set changed across a layout commit.")
        if result.changed_ids:
            result.errors.append("Building or Appearance content changed in a layout commit.")
        if identities:
            result.errors.append("layout commits must not include building-change trailers.")
        return result

    if change_type == "scope-extract":
        scope_values = trailers.get("Scope-Municipality", [])
        if len(scope_values) != 1:
            result.errors.append(
                "Specify exactly one Scope-Municipality trailer for scope-extract."
            )
            return result
        target = scope_values[0]
        if not re.fullmatch(pack.municipality_code or "", target):
            result.errors.append(f"Scope-Municipality: {target} is not a municipality code of the city's dialect pack.")
            return result
        if identities:
            result.errors.append("scope-extract commits must not list per-building trailers.")

        unknown = sorted(
            stable for stable, municipality in old.municipalities.items()
            if municipality is None
        )
        if unknown:
            result.errors.append(
                "Some buildings in the extraction source have no determinable municipality code: "
                + ", ".join(unknown[:10])
                + (f" and {len(unknown) - 10} more" if len(unknown) > 10 else "")
            )

        expected_ids = {
            stable for stable, municipality in old.municipalities.items()
            if municipality == target
        }
        missing = expected_ids - new_ids
        unexpected = new_ids - expected_ids
        if missing:
            result.errors.append(
                f"{len(missing)} building(s) of {target} were missed: "
                + ", ".join(sorted(missing)[:10])
            )
        if unexpected:
            result.errors.append(
                f"{len(unexpected)} building(s) remain that are outside {target} or absent from the source: "
                + ", ".join(sorted(unexpected)[:10])
            )

        retained_changed = {
            stable for stable in old_ids & new_ids
            if (
                old.members.get(stable) != new.members.get(stable)
                or old.appearance.get(stable, frozenset())
                != new.appearance.get(stable, frozenset())
            )
        }
        if retained_changed:
            result.errors.append(
                f"Content or Appearance of {len(retained_changed)} retained building(s) changed: "
                + ", ".join(sorted(retained_changed)[:10])
            )
        return result

    if change_type in IDENTITY_KINDS:
        froms = trailers.get("Building-ID-From", [])
        tos = trailers.get("Building-ID-To", [])
        refs = trailers.get("Provenance-Manifest", [])
        if len(froms) != 1 or len(tos) != 1:
            result.errors.append(f"{change_type} commits carry exactly one Building-ID-From and one Building-ID-To trailer.")
            return result
        if identities:
            result.errors.append(f"{change_type} commits must not list Building/Building-Added/Building-Deleted trailers.")
        source, target = froms[0], tos[0]
        result.identity_from, result.identity_to = source, target
        _check_manifest_ref(repo, sha, refs, result)
        if change_type == "identity-correction" and len(trailers.get("Corrects", [])) != 1:
            result.errors.append("identity-correction commits carry exactly one Corrects: <commit sha> trailer.")
        if result.deleted_ids != {source} or result.added_ids != {target} or result.changed_ids != {source, target}:
            result.errors.append(
                f"An identity commit replaces exactly one building ID: expected {source} -> {target}, "
                f"actual deleted={sorted(result.deleted_ids)} added={sorted(result.added_ids)}."
            )
            return result
        before = _member_bytes(old_blobs, source, rule)
        after = _member_bytes(new_blobs, target, rule)
        if before is None or after is None or replace_stable_id(before, source, target, rule) != after:
            result.errors.append(
                f"The building's bytes changed beyond its building ID value ({source} -> {target}); "
                "identity commits are byte-preserving."
            )
        if old.appearance.get(source, frozenset()) != new.appearance.get(target, frozenset()):
            result.errors.append("Appearance of the relinked building changed in an identity commit.")
        return result

    if change_type == "lifecycle":
        if not (result.added_ids or result.deleted_ids):
            result.errors.append("Commit is lifecycle, but no buildings were added or deleted.")
        for key, expected in [('Building-Added', result.added_ids), ('Building-Deleted', result.deleted_ids),
                              ('Building', result.changed_ids - result.added_ids - result.deleted_ids)]:
            values = trailers.get(key, [])
            if set(values) != expected or len(values) != len(set(values)):
                result.errors.append(f'{key} trailers do not match the actual lifecycle change in that category.')
        refs = trailers.get('Lifecycle-Manifest', [])
        ref = parse_manifest_ref(refs[0]) if len(refs) == 1 else None
        if ref is None or not re.fullmatch(r'provenance/lifecycle/[A-Za-z0-9][A-Za-z0-9._-]*\.json', ref[0]):
            result.errors.append('Exactly one Lifecycle-Manifest: provenance/lifecycle/<event>.json@sha256:<hex> is required.')
            return result
        raw = _blob(repo, sha, ref[0])
        changed_records = [p for p in changed(repo, sha) if p.startswith('provenance/lifecycle/')]
        if changed_records != [ref[0]]:
            result.errors.append('A lifecycle commit records exactly its referenced event; do not change other lifecycle records.')
        if raw is None or sha256_hex(raw) != ref[1]:
            result.errors.append('Lifecycle manifest is missing or does not match its SHA-256 digest.')
            return result
        try:
            manifest = json.loads(raw)
        except ValueError:
            result.errors.append('Lifecycle manifest is not JSON.')
            return result
        problems = validate_lifecycle(manifest)
        if problems:
            result.errors.extend(problems)
            return result
        result.lifecycle = manifest
        before, after = set(manifest['oldIds']), set(manifest['newIds'])
        if not before <= old_ids or not after <= new_ids:
            result.errors.append('Lifecycle old/new IDs must exist on their respective side of the changed GML.')
        if before - after != result.deleted_ids or after - before != result.added_ids:
            result.errors.append('Lifecycle old/new relation does not match the actual added/deleted IDs.')
        if not result.changed_ids <= before | after:
            result.errors.append('A building outside this lifecycle event was modified.')
        if trailers.get('Provenance-Manifest') or trailers.get('Building-ID-From') or trailers.get('Building-ID-To'):
            result.errors.append('Do not mix lifecycle with bulk conversion or identity correction trailers.')
        prior_paths = str(_git(repo, 'ls-tree', '-r', '--name-only', parent)).splitlines()
        for path in prior_paths:
            if re.fullmatch(r'provenance/lifecycle/[A-Za-z0-9][A-Za-z0-9._-]*\.json', path):
                try:
                    previous = json.loads(_blob(repo, parent, path) or b'null')
                except ValueError:
                    continue
                if isinstance(previous, dict) and previous.get('eventId') == manifest['eventId']:
                    result.errors.append('This lifecycle eventId is already recorded; use a separate correction proposal.')
                    break
        for side, ids, commit in [('old', before, parent), ('new', after, sha)]:
            all_ids = _repository_building_ids(repo, commit, rule)
            if any(len(all_ids.get(stable, [])) != 1 for stable in ids):
                result.errors.append(f'Lifecycle {side} IDs must be unique across the repository.')
        return result

    if len(result.changed_ids) != 1:
        result.errors.append(
            f"A normal commit must change exactly one building ID (actual: {len(result.changed_ids)})."
        )
        return result

    if trailers.get("Provenance-Manifest"):
        _check_manifest_ref(repo, sha, trailers["Provenance-Manifest"], result)
    stable = next(iter(result.changed_ids))
    if result.manifest_ref:
        result.member_before = _member_bytes(old_blobs, stable, rule)
        result.member_after = _member_bytes(new_blobs, stable, rule)
    expected_key = "Building"
    if stable in result.added_ids:
        expected_key = "Building-Added"
    elif stable in result.deleted_ids:
        expected_key = "Building-Deleted"

    if len(identities) != 1:
        result.errors.append(
            f"Exactly one building-ID trailer is required (expected: {expected_key}: {stable})."
        )
    elif identities[0] != stable or trailers.get(expected_key, []) != [stable]:
        result.errors.append(
            f"The trailer does not match the actual change (expected: {expected_key}: {stable})."
        )
    return result


DIALECT = ("dialect", "building_id", "data_dirs", "crs")   # how the data is read (schemas/city/4dcitygml.schema.json)


def _dialect_change_is_configuration_only(repo: Path, base_sha: str, head_sha: str, results: list) -> None:
    """A PR that changes how the city's data is read (its declared dialect) changes no data: the
    analysis reads the declaration at the base (U1), so data in the same PR would be judged by
    the rule it replaces."""
    before, after = city_declaration.read(repo, base_sha), city_declaration.read(repo, head_sha)
    if not results or all(before.raw.get(k) == after.raw.get(k) for k in DIALECT):
        return
    if any(p.endswith(".gml") or is_data(p, before.data_dirs) or is_data(p, after.data_dirs)
           for p in changed(repo, base_sha, head_sha)):
        results[0].errors.append(
            "This PR changes how the city's data is read (" + ", ".join(k for k in DIALECT if before.raw.get(k) != after.raw.get(k))
            + " in 4dcitygml.json) and also changes data. Propose the declaration on its own; the data follows "
            "in a PR after it is merged.")


def inspect_range(repo: Path, base_sha: str, head_sha: str) -> list[CommitResult]:
    inspected = [inspect_commit(repo, commit, base_sha) for commit in range_commits(repo, base_sha, head_sha)]
    # the base updates carry the base, not the PR's change: the range rules below see the rest
    results = [r for r in inspected if r.change_type != BASE_UPDATE]
    _dialect_change_is_configuration_only(repo, base_sha, head_sha, results)
    lifecycle = [item for item in results if item.change_type == 'lifecycle']
    if lifecycle and len(results) != 1:
        for item in lifecycle:
            item.errors.append('One lifecycle event requires one dedicated commit and PR; do not mix other commits.')
    if not lifecycle:
        ordinary = [r for r in results if not r.manifest_ref and r.change_type not in WHOLE_FILE_KINDS | IDENTITY_KINDS]
        if any(r.added_ids or r.deleted_ids for r in ordinary) and len(set().union(*(r.changed_ids for r in ordinary))) > 1:
            ordinary[0].errors.append('Multiple buildings with additions/deletions require a declared lifecycle event or a supported bulk submission.')
    scope_extracts = [item for item in results if item.change_type == "scope-extract"]
    if scope_extracts and len(results) != 1:
        for item in scope_extracts:
            item.errors.append(
                "scope-extract must be a dedicated commit/PR placed right after the source baseline."
            )
    _inspect_identity_range(repo, base_sha, head_sha, results)
    _inspect_source_update_range(repo, base_sha, head_sha, results)
    seen: dict[str, CommitResult] = {}
    for result in results:
        if result.change_type in WHOLE_FILE_KINDS | IDENTITY_KINDS:
            continue
        for stable in result.changed_ids:
            previous = seen.get(stable)
            if previous is not None:
                result.errors.append(
                    f"The same building ID is changed by multiple commits in this PR: {stable} "
                    f"(earlier commit {previous.sha[:12]}). Squash into one building commit."
                )
            else:
                seen[stable] = result
    return inspected


def _repository_building_ids(repo: Path, sha: str, rule: IdentityRule) -> dict[str, list[str]]:
    """Every stable building ID (the city's rule) in every .gml of the tree at ``sha`` -> paths."""
    ids: dict[str, list[str]] = {}
    for path in str(_git(repo, "ls-tree", "-r", "--name-only", sha)).splitlines():
        if not path.endswith(".gml"):
            continue
        raw = _blob(repo, sha, path)
        if raw is None:
            continue
        for gml_id, (start, end) in building_spans(raw).items():
            ids.setdefault(stable_id(raw[start:end], gml_id, rule), []).append(path)
    return ids


def _check_manifest_ref(repo: Path, sha: str, refs: list, result: CommitResult) -> None:
    """A commit's manifest: exactly one Provenance-Manifest: <path>@sha256:<hex> that the commit
    holds with that digest (identity and bulk commits alike)."""
    if len(refs) != 1 or parse_manifest_ref(refs[0]) is None:
        result.errors.append("Exactly one Provenance-Manifest: <path>@sha256:<hex> trailer is required.")
        return
    result.manifest_ref = refs[0]
    ref_path, ref_sha = parse_manifest_ref(refs[0])
    manifest_blob = _blob(repo, sha, ref_path)
    if manifest_blob is None:
        result.errors.append(f"Provenance manifest {ref_path} is not present in the commit.")
    elif sha256_hex(manifest_blob) != ref_sha:
        result.errors.append(f"Provenance manifest {ref_path} does not match the digest in the trailer.")


def _pr_manifest(repo: Path, head_sha: str, commits: list, refs: set) -> "tuple[dict, str] | None":
    """The one manifest every commit of a PR references, as the PR head holds it and valid; the
    errors go to the commits, and None when there is none to use."""
    if len(refs) != 1:
        commits[0].errors.append("All commits of a bulk PR reference the same Provenance-Manifest.")
        return None
    ref_path, ref_sha = parse_manifest_ref(next(iter(refs)))
    blob = _blob(repo, head_sha, ref_path)
    try:
        manifest = json.loads(blob.decode("utf-8")) if blob is not None else None
    except ValueError:
        manifest = None
    if not isinstance(manifest, dict):
        commits[0].errors.append(f"Provenance manifest {ref_path} is missing or not JSON at the PR head.")
        return None
    if sha256_hex(blob) != ref_sha:
        commits[0].errors.append(
            f"Provenance manifest {ref_path} at the PR head does not match the digest the commits reference "
            "(the manifest was changed after the commits were made)."
        )
        return None
    problems = validate_manifest(manifest)
    if problems:
        for r in commits:
            r.errors.append("Provenance manifest violates the schema: " + "; ".join(problems[:3]))
        return None
    return manifest, ref_path


def _inspect_identity_range(repo: Path, base_sha: str, head_sha: str, results: list[CommitResult]) -> None:
    """PR-level rules for identity commits: one manifest for the whole PR, every
    From->To pair listed in it with tier A/B (C only under review), and no
    target ID colliding with an ID that exists anywhere in the repository at the
    moment the commit is applied (IDs freed by earlier commits of the same PR
    are fine)."""
    identity = [r for r in results if r.change_type in IDENTITY_KINDS]
    if not identity:
        return
    for r in results:
        if r.change_type not in IDENTITY_KINDS and (r.changed_ids or r.change_type):
            r.errors.append("This commit does not belong in an identity PR (identity commits plus documentation only).")
    found = _pr_manifest(repo, head_sha, identity, {r.manifest_ref for r in identity if r.manifest_ref})
    if found is None:
        return
    manifest, ref_path = found
    kinds = {r.change_type for r in identity}
    if manifest.get("kind") not in kinds or len(kinds) != 1:
        for r in identity:
            r.errors.append(f"Manifest kind {manifest.get('kind')!r} does not match the commits' Change-Type.")
    links = {(l["from"], l["to"]): l for l in manifest.get("evidence", {}).get("links", [])}
    review_allowed = os.environ.get("CITYGML_IDENTITY_REVIEW") == "true"
    current = _repository_building_ids(repo, base_sha, rule_from_config(city_declaration.read(repo, base_sha).resolved))
    seen_pairs: set[tuple[str, str]] = set()
    for r in identity:
        pair = (r.identity_from, r.identity_to)
        if not all(pair):
            continue
        link = links.get(pair)
        if link is None:
            r.errors.append(f"{pair[0]} -> {pair[1]} is not listed in the manifest's evidence.links.")
        elif link.get("tier") == "C" and not review_allowed:
            r.errors.append(f"{pair[0]} -> {pair[1]} is tier C (needs human review): allowed only with the identity-review label.")
        if pair in seen_pairs:
            r.errors.append(f"{pair[0]} -> {pair[1]} appears in more than one commit.")
        seen_pairs.add(pair)
        holders = current.get(pair[1], [])
        if holders:
            r.errors.append(
                f"Target building ID {pair[1]} already exists in the repository ({holders[0]}) when this commit applies; "
                "IDs must be unique across the whole repository."
            )
        # apply: free the source, occupy the target
        current.pop(pair[0], None)
        current.setdefault(pair[1], []).append("(this PR)")
    listed = {(l["from"], l["to"]) for l in links.values()}
    if manifest.get("kind") == "identity-baseline" and listed and listed != seen_pairs and not any(r.errors for r in identity):
        missing = sorted(listed - seen_pairs)
        for r in identity[:1]:
            r.errors.append(
                f"The PR applies {len(seen_pairs)} of the manifest's {len(listed)} links; "
                f"an identity-baseline PR applies all of them (missing e.g. {missing[0][0]} -> {missing[0][1]})."
            )


def _inspect_source_update_range(repo: Path, base_sha: str, head_sha: str, results: list[CommitResult]) -> None:
    """PR-level rules for the bulk commits of a manifest kind (normal Building: commits that carry
    a Provenance-Manifest): one manifest per PR, then its kind's rules, which the city's dialect
    pack gives (Pack.bulk_kinds: a pack's own kinds, such as the source update; the identity
    kinds are checked above, for every city)."""
    bulk = [r for r in results if r.manifest_ref and r.change_type not in IDENTITY_KINDS and r.changed_ids]
    if not bulk:
        return
    found = _pr_manifest(repo, head_sha, bulk, {r.manifest_ref for r in bulk})
    if found is None:
        return
    manifest, ref_path = found
    # the rules of the city's dialect at the PR base: a PR cannot choose its own (trusted side)
    check = next((c for k, _tool, c in city_declaration.read(repo, base_sha).pack.bulk_kinds if k == manifest.get("kind")), None)
    if check is None:
        for r in bulk:
            r.errors.append(f"Manifest kind {manifest.get('kind')!r} is not a bulk kind of this city's data.")
        return
    check(repo, base_sha, head_sha, results, manifest, ref_path)


def render(results: list[CommitResult]) -> str:
    lines = ["1 commit = 1 building ID gate"]
    bulk_ok = [r for r in results if (r.change_type in IDENTITY_KINDS or r.manifest_ref) and r.ok]
    compact = len(bulk_ok) > 20  # bulk PRs: summarize passing commits instead of listing hundreds
    if compact:
        lines.append(f"OK   {len(bulk_ok)} manifest-backed commits passed (listing suppressed for size)")
    for result in results:
        if compact and result.ok and (result.change_type in IDENTITY_KINDS or result.manifest_ref):
            continue
        short = result.sha[:12]
        if result.ok:
            if result.change_type in IDENTITY_KINDS:
                ids = f"{result.change_type}: {result.identity_from} -> {result.identity_to}"
            elif result.change_type == "scope-extract":
                ids = f"scope-extract: deleted={len(result.deleted_ids)}"
            elif result.lifecycle:
                event = result.lifecycle
                ids = f"lifecycle {event['kind']} ({event['eventId']}): {', '.join(event['oldIds'])} -> {', '.join(event['newIds'])}"
            else:
                sorted_ids = sorted(result.changed_ids)
                # administrative kinds without per-building ids (source-baseline, layout,
                # schema-update, practice-reset) are named by their change type
                ids = ", ".join(sorted_ids[:10]) or result.change_type or "no semantic building change"
                if len(sorted_ids) > 10:
                    ids += f" and {len(sorted_ids) - 10} more"
            lines.append(f"OK   {short}  {ids}  {result.subject}")
        else:
            lines.append(f"FAIL {short}  {result.subject}")
            lines.extend(f"  - {error}" for error in result.errors)
    failures = sum(not result.ok for result in results)
    lines.append(f"Result: {len(results)} commits / failures={failures}")
    return "\n".join(lines) + "\n"


class _FileBuildings:
    """One file's buildings by stable ID: member bytes and appearance at once (cheap); attributes
    and geometry only for the members asked for, parsed inside the file's own CityModel header so
    the namespaces hold (CityGML 1.0 files too)."""

    def __init__(self, raw: bytes, rule: IdentityRule):
        self.raw = raw
        spans = building_spans(raw) if raw else {}
        self.gml = {}                                   # stable -> gml:id
        self.member = {}                                # stable -> member bytes
        for gml_id, (start, end) in spans.items():
            stable = stable_id(raw[start:end], gml_id, rule)
            self.gml[stable], self.member[stable] = gml_id, raw[start:end]
        self.first = min((start for start, _ in spans.values()), default=0)
        appearance = building_appearance_sig(raw) if raw else {}
        self.appearance = {stable: frozenset(appearance.get(gml_id, ())) for stable, gml_id in self.gml.items()}

    def meaning(self, stables) -> dict[str, tuple]:
        """stable -> (attributes, geometry hash, appearance) for the given buildings."""
        from scripts.building_identity import citymodel_close
        from scripts.diff_citygml import iter_buildings, signature
        wanted = [s for s in stables if s in self.member]
        if not wanted:
            return {}
        close = citymodel_close(self.raw)
        doc = self.raw[:self.first] + b"".join(self.member[s] for s in wanted) + (self.raw[close:] if close >= 0 else b"")
        by_gml = {gml_id: (attrs, geom) for gml_id, attrs, geom in iter_buildings(doc)}
        return {s: signature(*by_gml.get(self.gml[s], ({}, None)), self.appearance[s]) for s in wanted}


def pr_scope(repo: Path, base_sha: str, head_sha: str) -> dict:
    """The PR's net building scope (base -> head), counted once for the analysis driver (S1).

    Merges the two computations that disagreed: by stable ID, as this gate counts (the city's
    identity rule; CityGML 1.0 files too), and by meaning over the whole PR, as the quality step
    counted with reconstruct_minimal (formatting churn is not a change; a building whose content is
    unchanged but whose id changed is a rename, a notice). A change of texture counts as a change
    of its building. Deleted GML files count too (the quality step listed only A/M/R paths).
    """
    rule = rule_from_config(city_declaration.read(repo, base_sha).resolved)   # U1: the base's declaration
    paths = changed(repo, base_sha, head_sha, suffix=".gml")
    files = [(_FileBuildings(_blob(repo, base_sha, p) or b"", rule), _FileBuildings(_blob(repo, head_sha, p) or b"", rule))
             for p in paths]
    gml_before = {s: g for old, _ in files for s, g in old.gml.items()}
    gml_after = {s: g for _, new in files for s, g in new.gml.items()}
    member_before = {s: (m, old.appearance[s]) for old, _ in files for s, m in old.member.items()}
    member_after = {s: (m, new.appearance[s]) for _, new in files for s, m in new.member.items()}
    # identical bytes and appearance: unchanged without parsing. Only buildings that differ are
    # compared by meaning; added and deleted ones only when both exist (a rename to pair up), so a
    # whole new dataset is not parsed for nothing.
    both = set(member_before) & set(member_after)
    differ = {s for s in both if member_before[s] != member_after[s]}
    gone, new_ids = set(member_before) - both, set(member_after) - both
    pairable = gone | new_ids if gone and new_ids else set()
    content_before: dict[str, tuple] = {s: ("same",) for s in both - differ}
    content_after: dict[str, tuple] = dict(content_before)
    for old, new in files:
        content_before.update(old.meaning((differ | pairable) & set(old.member)))
        content_after.update(new.meaning((differ | pairable) & set(new.member)))
    before, after = set(gml_before), set(gml_after)
    added = sorted(after - before)
    deleted = sorted(before - after)
    renamed: list[str] = []
    for stable in sorted(before & after):
        if content_before[stable] == content_after[stable] and gml_before[stable] != gml_after[stable]:
            renamed.append(stable)                        # gml:id changed, stable ID and content kept
    # the stable ID changed with the gml:id (e.g. Munich): diff_citygml's one rename rule (U6)
    from scripts.diff_citygml import rename_pairs
    for new_id, old_id in rename_pairs({d: content_before[d] for d in deleted if d in content_before},
                                       {a: content_after[a] for a in added if a in content_after}).items():
        added.remove(new_id)
        deleted.remove(old_id)
        renamed.append(new_id)
    modified = sorted(s for s in before & after if content_before[s] != content_after[s])
    from scripts.reconstruct_minimal import classify
    return {
        "modified": modified, "added": added, "deleted": deleted, "renamed": sorted(renamed),
        "class": classify(len(modified), len(added), len(deleted), len(renamed)),
        "rule": rule.type,   # the stable ID's kind, for the suggested commit (U4)
    }


def _scope_or_error(repo: Path, base_sha: str, head_sha: str) -> dict:
    """The scope for the JSON; a failure is recorded, and the quality step then fails instead of
    counting nothing."""
    try:
        return {"scope": pr_scope(repo, base_sha, head_sha)}
    except Exception as exc:  # noqa: BLE001
        # a changed file that is not well-formed XML is the proposer's to fix, not a system error
        return {"scope": None, "scopeError": f"{type(exc).__name__}: {exc}", "scopeMalformed": malformed_input(exc)}


def pr_facts(results: list[CommitResult]) -> dict:
    """What the analysis driver needs to know about the PR's trailers, read once from the parsed
    commits (the driver used to grep the commit messages for each of these)."""
    def values(key: str) -> list[str]:
        return [v for r in results for v in r.trailers.get(key, [])]
    kinds = {r.change_type for r in results if r.change_type}
    manifests = values("Provenance-Manifest")
    scope_extract = "scope-extract" in kinds
    return {
        "changeTypes": [r.change_type for r in results],
        # administrative only by a declared, accepted change type (D3: an unknown value skipped
        # the classification gate) or a provenance manifest
        "administrative": bool(kinds & ACCEPTED_CHANGE_TYPES or manifests),
        "sourceBaseline": "source-baseline" in kinds,
        "practiceReset": "practice-reset" in kinds,
        "identity": bool(kinds & IDENTITY_KINDS),
        "bulk": bool(manifests),
        # the first manifest line of the newest commit that has one, as `git log` lists it first
        "manifestRef": next((r.trailers["Provenance-Manifest"][0] for r in reversed(results)
                             if r.trailers.get("Provenance-Manifest")), ""),
        "scopeExtract": scope_extract,
        "scopeMunicipalities": sorted(set(values("Scope-Municipality"))) if scope_extract else [],
    }


def practice_reset_only(results: list[CommitResult]) -> bool:
    """Every commit of the range is an accepted practice reset. Such a PR returns many buildings
    at once; this gate has verified it against Reset-To, so the one-building rule does not apply."""
    return bool(results) and all(r.ok and r.change_type == "practice-reset" for r in results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument('--json-output', type=Path)
    args = parser.parse_args(argv)

    try:
        results = inspect_range(args.repo, args.base_sha, args.head_sha)
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render(results))
    if args.json_output:
        args.json_output.write_text(json.dumps({'lifecycle': [r.lifecycle for r in results if r.lifecycle],
                                                  'practiceResetOnly': practice_reset_only(results),
                                                  **pr_facts(results), **_scope_or_error(args.repo, args.base_sha, args.head_sha)},
                                                 ensure_ascii=False), encoding='utf-8')
    return 1 if any(not result.ok for result in results) else 0


if __name__ == "__main__":
    from scripts.gate_result import guarded
    raise SystemExit(guarded(main))
