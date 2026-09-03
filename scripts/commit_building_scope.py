#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Check whether each commit in a PR adheres to "normal update: 1 commit = 1 buildingID".

Viewing only the base→head diff of the entire PR makes it impossible to distinguish
between history where multiple buildings are changed in separate commits vs. changed in
one commit. This gate checks each commit in ``base..head`` sequentially and cross-references
the actual changed ``uro:buildingID`` in CityGML with commit trailers.

Normal updates:

* Fix: ``Building: <uro:buildingID>``
* Add: ``Building-Added: <uro:buildingID>``
* Delete: ``Building-Deleted: <uro:buildingID>``

Exceptions are only ``Change-Type: lifecycle`` (enumerate multiple IDs as old→new relationships),
``Change-Type: layout`` (layout change with unchanged ID set), ``Change-Type: source-baseline``
(initial source recording), ``Change-Type: scope-extract`` (removal of non-target municipalities),
and the identity kinds ``identity-baseline`` / ``identity-correction`` (exactly one building's
``uro:buildingID`` replaced, declared by ``Building-ID-From`` / ``Building-ID-To`` and backed by a
``Provenance-Manifest`` — see docs/bulk-submission-provenance.md), and ``schema-update``
(edition artifacts only — code lists, schema profiles — with no CityGML change at all).
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
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.provenance_manifest import parse_manifest_ref, sha256_hex, validate as validate_manifest  # noqa: E402
from scripts.reconstruct_minimal import building_spans  # noqa: E402
from scripts.texture_check import _building_appearance_sig  # noqa: E402

_BUILDING_ID_VALUE_RE = re.compile(
    rb"<(?:\w+:)?buildingID(?:\s[^>]*)?>([^<]+)</(?:\w+:)?buildingID>"
)
_CITY_VALUE_RE = re.compile(
    rb"<(?:\w+:)?city(?:\s[^>]*)?>([^<]+)</(?:\w+:)?city>"
)
_TRAILER_RE = re.compile(
    r"^(Building|Building-Added|Building-Deleted|Change-Type|Scope-Municipality"
    r"|Building-ID-From|Building-ID-To|Provenance-Manifest|Corrects):"
    r"[ \t]*(.+?)[ \t]*$",
    re.MULTILINE,
)
IDENTITY_KINDS = {"identity-baseline", "identity-correction"}
# schema-update: the artifacts an edition brings (code lists, XSD profile, provenance), never data
SCHEMA_UPDATE_PREFIXES = ("codelists/", "schemas/", "provenance/schema-update/", "docs/")
_ANY_BUILDING_ID_RE = re.compile(rb"<(?:\w+:)?buildingID(?:\s[^>]*)?>([^<]+)</(?:\w+:)?buildingID>")


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
    member_before: bytes | None = None   # manifest-backed normal commits: the building's bytes at the parent
    member_after: bytes | None = None    # ... and at the commit (kept so the PR-level check need not re-read blobs)

    @property
    def ok(self) -> bool:
        return not self.errors


def _git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)}: {detail}")
    if binary:
        return proc.stdout
    return proc.stdout.decode("utf-8", errors="replace")


def _blob(repo: Path, sha: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{sha}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _changed_gml_paths(repo: Path, parent: str, commit: str) -> list[str]:
    # Rename detection is intentionally disabled so renames yield both the old and new paths.
    output = _git(
        repo, "diff", "--name-only", "--no-renames", parent, commit, "--", "*.gml"
    )
    return sorted({line for line in str(output).splitlines() if line.endswith(".gml")})


def _stable_id(member: bytes, gml_id: str) -> str:
    match = _BUILDING_ID_VALUE_RE.search(member)
    if match is None:
        return gml_id
    value = match.group(1).decode("utf-8", errors="replace").strip()
    return value or gml_id


def _municipality(member: bytes) -> str | None:
    match = _CITY_VALUE_RE.search(member)
    if match is None:
        return None
    value = match.group(1).decode("utf-8", errors="replace").strip()
    return value or None


def _snapshot(blobs: list[bytes]) -> Snapshot:
    snapshot = Snapshot()
    appearance_sets: dict[str, set[tuple[str, str, str]]] = {}

    for raw in blobs:
        local_gml_to_stable: dict[str, str] = {}
        for gml_id, (start, end) in building_spans(raw).items():
            member = raw[start:end]
            stable = _stable_id(member, gml_id)
            local_gml_to_stable[gml_id] = stable
            snapshot.gml_to_stable[gml_id] = stable
            snapshot.municipalities[stable] = _municipality(member)
            digest = hashlib.sha256(member).digest()
            if stable in snapshot.members:
                snapshot.duplicates.add(stable)
            else:
                snapshot.members[stable] = digest

        for gml_id, signatures in _building_appearance_sig(raw).items():
            stable = local_gml_to_stable.get(gml_id, gml_id)
            appearance_sets.setdefault(stable, set()).update(signatures)

    snapshot.appearance = {
        stable: frozenset(signatures) for stable, signatures in appearance_sets.items()
    }
    return snapshot


def _member_bytes(blobs: list[bytes], stable: str) -> bytes | None:
    for raw in blobs:
        for gml_id, (start, end) in building_spans(raw).items():
            member = raw[start:end]
            if _stable_id(member, gml_id) == stable:
                return member
    return None


def _replace_building_id(member: bytes, old: str, new: str) -> bytes:
    pattern = re.compile(
        rb"(<(?:\w+:)?buildingID(?:\s[^>]*)?>)" + re.escape(old.encode()) + rb"(</(?:\w+:)?buildingID>)"
    )
    return pattern.sub(lambda m: m.group(1) + new.encode() + m.group(2), member, count=1)


def _trailers(message: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, value in _TRAILER_RE.findall(message):
        result.setdefault(key, []).append(value.strip())
    return result


def _all_identity_trailers(trailers: dict[str, list[str]]) -> list[str]:
    return [
        value
        for key in ("Building", "Building-Added", "Building-Deleted")
        for value in trailers.get(key, [])
    ]


def inspect_commit(repo: Path, sha: str) -> CommitResult:
    parents = str(_git(repo, "show", "-s", "--format=%P", sha)).strip().split()
    subject = str(_git(repo, "show", "-s", "--format=%s", sha)).strip()
    result = CommitResult(sha=sha, subject=subject)
    if len(parents) != 1:
        result.errors.append(
            "Merge commits inside a PR branch cannot be inspected. Rebase onto main for a linear history."
        )
        return result

    parent = parents[0]
    paths = _changed_gml_paths(repo, parent, sha)
    message = str(_git(repo, "show", "-s", "--format=%B", sha))
    trailers = _trailers(message)
    change_types = trailers.get("Change-Type", [])
    change_type = change_types[-1].lower() if change_types else ""
    result.change_type = change_type
    identities = _all_identity_trailers(trailers)

    if len(change_types) > 1:
        result.errors.append("Specify exactly one Change-Type trailer.")

    if not paths:
        if identities:
            result.errors.append("A commit without CityGML changes has a building-ID trailer.")
        if change_type in IDENTITY_KINDS:
            result.errors.append(f"A {change_type} commit carries no CityGML change (documentation-only commits must not declare it).")
        if change_type == "schema-update":
            changed = str(_git(repo, "diff", "--name-only", "--no-renames", parent, sha)).splitlines()
            outside = [f for f in changed if not f.startswith(SCHEMA_UPDATE_PREFIXES)]
            if outside:
                result.errors.append("schema-update commits change only edition artifacts (" + ", ".join(SCHEMA_UPDATE_PREFIXES)
                                     + "); unexpected: " + ", ".join(outside[:5]))
        return result
    if change_type == "schema-update":
        result.errors.append("schema-update commits must not change CityGML data (add the edition's code lists / schema profile only).")
        return result

    old_blobs = [raw for path in paths if (raw := _blob(repo, parent, path)) is not None]
    new_blobs = [raw for path in paths if (raw := _blob(repo, sha, path)) is not None]
    old = _snapshot(old_blobs)
    new = _snapshot(new_blobs)

    if old.duplicates or new.duplicates:
        dup = sorted(old.duplicates | new.duplicates)
        result.errors.append(f"Duplicated buildingID inside the changed GML: {', '.join(dup)}")

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
            result.errors.append("The buildingID set changed across a layout commit.")
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
        if len(refs) != 1 or parse_manifest_ref(refs[0]) is None:
            result.errors.append("Exactly one Provenance-Manifest: <path>@sha256:<hex> trailer is required.")
        else:
            result.manifest_ref = refs[0]
            ref_path, ref_sha = parse_manifest_ref(refs[0])
            manifest_blob = _blob(repo, sha, ref_path)
            if manifest_blob is None:
                result.errors.append(f"Provenance manifest {ref_path} is not present in the commit.")
            elif sha256_hex(manifest_blob) != ref_sha:
                result.errors.append(f"Provenance manifest {ref_path} does not match the digest in the trailer.")
        if change_type == "identity-correction" and len(trailers.get("Corrects", [])) != 1:
            result.errors.append("identity-correction commits carry exactly one Corrects: <commit sha> trailer.")
        if result.deleted_ids != {source} or result.added_ids != {target} or result.changed_ids != {source, target}:
            result.errors.append(
                f"An identity commit replaces exactly one buildingID: expected {source} -> {target}, "
                f"actual deleted={sorted(result.deleted_ids)} added={sorted(result.added_ids)}."
            )
            return result
        before = _member_bytes(old_blobs, source)
        after = _member_bytes(new_blobs, target)
        if before is None or after is None or _replace_building_id(before, source, target) != after:
            result.errors.append(
                f"The building's bytes changed beyond the buildingID value ({source} -> {target}); "
                "identity commits are byte-preserving."
            )
        if old.appearance.get(source, frozenset()) != new.appearance.get(target, frozenset()):
            result.errors.append("Appearance of the relinked building changed in an identity commit.")
        return result

    if change_type == "lifecycle":
        if not (result.added_ids or result.deleted_ids):
            result.errors.append("Commit is lifecycle, but no buildings were added or deleted.")
        if set(identities) != result.changed_ids or len(identities) != len(set(identities)):
            result.errors.append(
                "The lifecycle Building-Added/Deleted trailers do not match the actually changed buildingID set."
            )
        return result

    if len(result.changed_ids) != 1:
        result.errors.append(
            f"A normal commit must change exactly one buildingID (actual: {len(result.changed_ids)})."
        )
        return result

    refs = trailers.get("Provenance-Manifest", [])
    if refs:
        if len(refs) != 1 or parse_manifest_ref(refs[0]) is None:
            result.errors.append("Exactly one Provenance-Manifest: <path>@sha256:<hex> trailer is allowed.")
        else:
            result.manifest_ref = refs[0]
            ref_path, ref_sha = parse_manifest_ref(refs[0])
            manifest_blob = _blob(repo, sha, ref_path)
            if manifest_blob is None:
                result.errors.append(f"Provenance manifest {ref_path} is not present in the commit.")
            elif sha256_hex(manifest_blob) != ref_sha:
                result.errors.append(f"Provenance manifest {ref_path} does not match the digest in the trailer.")
    stable = next(iter(result.changed_ids))
    if result.manifest_ref:
        result.member_before = _member_bytes(old_blobs, stable)
        result.member_after = _member_bytes(new_blobs, stable)
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


def inspect_range(repo: Path, base_sha: str, head_sha: str) -> list[CommitResult]:
    commits = str(
        _git(repo, "rev-list", "--reverse", "--topo-order", f"{base_sha}..{head_sha}")
    ).splitlines()
    results = [inspect_commit(repo, sha) for sha in commits if sha]
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
        if result.change_type in {"layout", "source-baseline", "scope-extract"} | IDENTITY_KINDS:
            continue
        for stable in result.changed_ids:
            previous = seen.get(stable)
            if previous is not None:
                result.errors.append(
                    f"The same buildingID is changed by multiple commits in this PR: {stable} "
                    f"(earlier commit {previous.sha[:12]}). Squash into one building commit."
                )
            else:
                seen[stable] = result
    return results


def _repository_building_ids(repo: Path, sha: str) -> dict[str, list[str]]:
    """Every uro:buildingID value in every .gml of the tree at ``sha`` -> paths."""
    ids: dict[str, list[str]] = {}
    for path in str(_git(repo, "ls-tree", "-r", "--name-only", sha)).splitlines():
        if not path.endswith(".gml"):
            continue
        raw = _blob(repo, sha, path)
        if raw is None:
            continue
        for match in _ANY_BUILDING_ID_RE.finditer(raw):
            ids.setdefault(match.group(1).decode("utf-8", errors="replace").strip(), []).append(path)
    return ids


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
    refs = {r.manifest_ref for r in identity if r.manifest_ref}
    if len(refs) != 1:
        identity[0].errors.append("All identity commits of a PR reference the same Provenance-Manifest.")
        return
    ref_path, ref_sha = parse_manifest_ref(next(iter(refs)))
    blob = _blob(repo, head_sha, ref_path)
    manifest = None
    if blob is not None:
        try:
            manifest = json.loads(blob.decode("utf-8"))
        except ValueError:
            manifest = None
    if manifest is None:
        identity[0].errors.append(f"Provenance manifest {ref_path} is missing or not JSON at the PR head.")
        return
    if sha256_hex(blob) != ref_sha:
        identity[0].errors.append(
            f"Provenance manifest {ref_path} at the PR head does not match the digest the commits reference "
            "(the manifest was changed after the commits were made)."
        )
        return
    problems = validate_manifest(manifest)
    if problems:
        for r in identity:
            r.errors.append("Provenance manifest violates the schema: " + "; ".join(problems[:3]))
        return
    kinds = {r.change_type for r in identity}
    if manifest.get("kind") not in kinds or len(kinds) != 1:
        for r in identity:
            r.errors.append(f"Manifest kind {manifest.get('kind')!r} does not match the commits' Change-Type.")
    links = {(l["from"], l["to"]): l for l in manifest.get("evidence", {}).get("links", [])}
    review_allowed = os.environ.get("CITYGML_IDENTITY_REVIEW") == "true"
    current = _repository_building_ids(repo, base_sha)
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
                f"Target buildingID {pair[1]} already exists in the repository ({holders[0]}) when this commit applies; "
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
    """PR-level rules for bulk source-update commits (normal Building: commits
    that carry a Provenance-Manifest): one manifest per PR, every changed
    building listed in evidence.targets exactly once, and each commit's building
    bytes equal to the parent's bytes with the manifest's changes applied."""
    bulk = [r for r in results if r.manifest_ref and r.change_type not in IDENTITY_KINDS and r.changed_ids]
    if not bulk:
        return
    refs = {r.manifest_ref for r in bulk}
    if len(refs) != 1:
        bulk[0].errors.append("All commits of a bulk PR reference the same Provenance-Manifest.")
        return
    ref_path, ref_sha = parse_manifest_ref(next(iter(refs)))
    blob = _blob(repo, head_sha, ref_path)
    try:
        manifest = json.loads(blob.decode("utf-8")) if blob is not None else None
    except ValueError:
        manifest = None
    if manifest is None:
        bulk[0].errors.append(f"Provenance manifest {ref_path} is missing or not JSON at the PR head.")
        return
    if sha256_hex(blob) != ref_sha:
        bulk[0].errors.append(
            f"Provenance manifest {ref_path} at the PR head does not match the digest the commits reference "
            "(the manifest was changed after the commits were made)."
        )
        return
    problems = validate_manifest(manifest)
    if problems:
        for r in bulk:
            r.errors.append("Provenance manifest violates the schema: " + "; ".join(problems[:3]))
        return
    if manifest.get("kind") not in ("source-update", "carry-forward"):
        for r in bulk:
            r.errors.append(f"Manifest kind {manifest.get('kind')!r} does not match Building: commits (expected source-update or carry-forward).")
        return
    from scripts.source_update_manifest import apply_changes_to_member  # lazy: lxml-heavy module

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


def render(results: list[CommitResult]) -> str:
    lines = ["1 commit = 1 buildingID gate"]
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
            else:
                sorted_ids = sorted(result.changed_ids)
                ids = ", ".join(sorted_ids[:10]) or "no semantic building change"
                if len(sorted_ids) > 10:
                    ids += f" and {len(sorted_ids) - 10} more"
            lines.append(f"OK   {short}  {ids}  {result.subject}")
        else:
            lines.append(f"FAIL {short}  {result.subject}")
            lines.extend(f"  - {error}" for error in result.errors)
    failures = sum(not result.ok for result in results)
    lines.append(f"Result: {len(results)} commits / failures={failures}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args(argv)

    try:
        results = inspect_range(args.repo, args.base_sha, args.head_sha)
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render(results))
    return 1 if any(not result.ok for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
