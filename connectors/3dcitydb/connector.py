#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed 3DCityDB -> GitHub synchronization core.

The connector exports a CityGML 2.0 snapshot (or accepts a pre-exported file),
compares it semantically with the reviewed file in Git, and produces a
minimal-diff candidate.  Planning never edits the repository.  Publication is
performed in a temporary Git worktree and is intentionally a separate action.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

HERE = Path(__file__).resolve().parent
_tool_roots = (HERE / "runtime", HERE.parents[1])
TOOLS_ROOT = next((path for path in _tool_roots if (path / "scripts").is_dir()), _tool_roots[-1])
import sys

for _import_root in (HERE, TOOLS_ROOT):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from scripts.diff_citygml import diff_sources  # noqa: E402
from scripts.reconstruct_minimal import Result, building_spans, reconstruct  # noqa: E402
from generic_to_uro import detect_uro_namespace, restore_generic_uro  # noqa: E402

CORE_NS = b"http://www.opengis.net/citygml/2.0"
SAFE_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_BUILDING = re.compile(r"[^A-Za-z0-9._-]+")


class SyncError(RuntimeError):
    """A safe, user-facing synchronization failure."""


@dataclass(frozen=True)
class SyncConfig:
    repository: Path
    citygml: Path
    export_file: Optional[Path] = None
    citydb_command: str = "citydb"
    citygml_version: str = "2.0"
    no_appearances: bool = True
    base_branch: str = "main"
    db_schema: str = "citydb"
    restore_uro: bool = True
    uro_namespace: Optional[str] = None

    @classmethod
    def load(
        cls,
        repository: Path,
        config_file: Optional[Path] = None,
        citygml: Optional[Path] = None,
        export_file: Optional[Path] = None,
        citydb_command: Optional[str] = None,
    ) -> "SyncConfig":
        data: dict[str, Any] = {}
        if config_file:
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SyncError(f"Cannot read configuration file {config_file}: {exc}") from exc

        repo = repository.expanduser().resolve()
        configured_citygml = citygml or _optional_path(data.get("citygml"))
        if configured_citygml is None:
            configured_citygml = _optional_path(os.getenv("FOURDCITYGML_CITYGML"))
        if configured_citygml is None:
            raise SyncError("The target CityGML is not configured. Use --citygml or config.citygml.")
        target = configured_citygml if configured_citygml.is_absolute() else repo / configured_citygml
        target = target.resolve()
        _require_inside(repo, target)

        configured_export = export_file or _optional_path(data.get("exportFile"))
        if configured_export is None:
            configured_export = _optional_path(os.getenv("FOURDCITYGML_DB_EXPORT"))
        if configured_export:
            configured_export = configured_export.expanduser().resolve()

        schema = str(data.get("dbSchema") or os.getenv("CITYDB_SCHEMA") or "citydb")
        if not SAFE_SCHEMA.fullmatch(schema):
            raise SyncError(f"Unsafe database schema name: {schema}")

        return cls(
            repository=repo,
            citygml=target,
            export_file=configured_export,
            citydb_command=citydb_command or str(data.get("citydbCommand") or "citydb"),
            citygml_version=str(data.get("citygmlVersion") or "2.0"),
            no_appearances=bool(data.get("noAppearances", True)),
            base_branch=str(data.get("baseBranch") or "main"),
            db_schema=schema,
            restore_uro=bool(data.get("restoreUro", True)),
            uro_namespace=str(data["uroNamespace"]) if data.get("uroNamespace") else None,
        )


@dataclass
class EnvironmentStatus:
    repository: str
    repository_ok: bool
    branch: str = ""
    clean: bool = False
    remote: str = ""
    github_authenticated: bool = False
    citygml: str = ""
    citygml_ok: bool = False
    db_configured: bool = False
    export_mode: str = "citydb-tool"
    messages: list[str] = field(default_factory=list)


@dataclass
class VersioningMetadata:
    objectid: str
    last_modification_date: Optional[str] = None
    updating_person: Optional[str] = None
    reason_for_update: Optional[str] = None
    lineage: Optional[str] = None
    creation_date: Optional[str] = None
    termination_date: Optional[str] = None


@dataclass
class SyncPlan:
    base_sha: str
    original_branch: str
    classification: str
    verified: bool
    modified: list[str]
    added: list[str]
    deleted: list[str]
    renamed: list[tuple[str, str]]
    methods: dict[str, str]
    warnings: list[str]
    changes: list[dict[str, Any]]
    versioning: dict[str, VersioningMetadata]
    output: bytes = field(repr=False)
    base: bytes = field(repr=False)

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("output", None)
        data.pop("base", None)
        data["versioning"] = {key: asdict(value) for key, value in self.versioning.items()}
        return data


@dataclass
class ProposalInput:
    building_id: str
    reason: str
    source: str
    public_author: str
    notes: str = ""


@dataclass
class PRReadiness:
    building_id: str
    base_sha: str
    remote_sha: str
    ready: bool
    conflict: bool
    requires_database_update: bool
    official_changed_buildings: list[str]
    message: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _optional_path(value: Any) -> Optional[Path]:
    return Path(str(value)).expanduser() if value else None


def _require_inside(root: Path, target: Path) -> None:
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SyncError(f"The target file must be inside the repository: {target}") from exc


def run(
    args: Iterable[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    check: bool = True,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(arg) for arg in args]
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except OSError as exc:
        raise SyncError(f"Cannot run command {command[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        # A helper that waits for input (a keychain prompt, a stalled network call) must not
        # freeze the review screen: report it as a failed command instead.
        if check:
            raise SyncError(f"Command timed out after {timeout:g}s: {' '.join(command)}") from exc
        return subprocess.CompletedProcess(command, returncode=124, stdout="", stderr=f"timed out after {timeout:g}s")
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise SyncError(f"Command failed: {' '.join(command)}\n{detail}")
    return completed


def inspect_environment(config: SyncConfig) -> EnvironmentStatus:
    status = EnvironmentStatus(
        repository=str(config.repository),
        repository_ok=(config.repository / ".git").exists(),
        citygml=str(config.citygml),
        citygml_ok=config.citygml.is_file(),
        db_configured=all(os.getenv(key) for key in ("CITYDB_HOST", "CITYDB_NAME", "CITYDB_USERNAME")),
        export_mode="file" if config.export_file else "citydb-tool",
    )
    if not status.repository_ok:
        status.messages.append("The selected directory is not a Git repository.")
        return status

    status.branch = run(["git", "branch", "--show-current"], cwd=config.repository).stdout.strip()
    status.clean = not run(["git", "status", "--porcelain"], cwd=config.repository).stdout.strip()
    remote = run(["git", "remote", "get-url", "origin"], cwd=config.repository, check=False)
    status.remote = remote.stdout.strip() if remote.returncode == 0 else ""
    auth = run(["gh", "auth", "status"], cwd=config.repository, check=False, timeout=15)
    status.github_authenticated = auth.returncode == 0
    if not status.clean:
        status.messages.append("The working tree has uncommitted changes.")
    if status.branch != config.base_branch:
        status.messages.append(
            f"Switch to the {config.base_branch} branch before syncing."
        )
    if not status.citygml_ok:
        status.messages.append("The target CityGML file was not found.")
    if config.export_file and not config.export_file.is_file():
        status.messages.append("The configured database export file was not found.")
    return status


def normalize_citydb_export(raw: bytes) -> bytes:
    """Normalize only the core prefix required by byte-preserving tools."""
    default_decl = b'xmlns="' + CORE_NS + b'"'
    core_decl = default_decl + b' xmlns:core="' + CORE_NS + b'"'
    if b"xmlns:core=" not in raw:
        raw = raw.replace(default_decl, core_decl, 1)
    raw = raw.replace(b"<cityObjectMember>", b"<core:cityObjectMember>")
    raw = raw.replace(b"</cityObjectMember>", b"</core:cityObjectMember>")
    raw = raw.replace(b"</CityModel>", b"</core:CityModel>")
    raw = raw.replace(b"<CityModel ", b"<core:CityModel ", 1)
    return raw


def export_snapshot(config: SyncConfig, target: Path) -> Path:
    if config.export_file:
        if not config.export_file.is_file():
            raise SyncError(f"Database export file not found: {config.export_file}")
        shutil.copyfile(config.export_file, target)
        return target

    args = [
        config.citydb_command,
        "export",
        "citygml",
        "-v",
        config.citygml_version,
        "-o",
        str(target),
    ]
    if config.no_appearances:
        args.append("--no-appearances")
    run(args, cwd=config.repository)
    if not target.is_file():
        raise SyncError("citydb-tool completed without creating an export file.")
    return target


def plan_sync(config: SyncConfig) -> SyncPlan:
    status = inspect_environment(config)
    if not status.repository_ok or not status.citygml_ok:
        raise SyncError("Pre-sync checks failed: " + " ".join(status.messages))
    if not status.clean:
        raise SyncError("Cannot sync while the working tree has uncommitted changes.")
    if status.branch != config.base_branch:
        raise SyncError(
            f"Run Sync on the {config.base_branch} branch (current: {status.branch or 'detached HEAD'})."
        )

    base_sha = run(["git", "rev-parse", "HEAD"], cwd=config.repository).stdout.strip()
    remote_base = run(
        ["git", "rev-parse", "--verify", f"refs/remotes/origin/{config.base_branch}"],
        cwd=config.repository,
        check=False,
    )
    if remote_base.returncode == 0 and remote_base.stdout.strip() != base_sha:
        raise SyncError(
            f"Local {config.base_branch} does not match origin/{config.base_branch}. "
            "Fetch or pull, then run Sync again."
        )
    original_branch = status.branch
    base = config.citygml.read_bytes()
    with tempfile.TemporaryDirectory(prefix="4dcitygml-citydb-") as tmp:
        exported = export_snapshot(config, Path(tmp) / "export.gml")
        head = exported.read_bytes()
        if config.restore_uro:
            uro_namespace = config.uro_namespace or detect_uro_namespace(base)
            head, _restored = restore_generic_uro(head, uro_namespace=uro_namespace)
        head = normalize_citydb_export(head)

    result: Result = reconstruct(base, head)
    if not result.verified:
        raise SyncError("Minimal-diff verification failed. The repository was not changed.")
    diff = diff_sources(base, head, "repository", "3dcitydb", include_unchanged=False)
    ids = result.modified + result.added + result.deleted + [new for _old, new in result.renamed]
    metadata = load_versioning_metadata(config, ids)
    return SyncPlan(
        base_sha=base_sha,
        original_branch=original_branch,
        classification=result.classification,
        verified=result.verified,
        modified=result.modified,
        added=result.added,
        deleted=result.deleted,
        renamed=result.renamed,
        methods=result.methods,
        warnings=result.warnings,
        changes=diff["buildings"],
        versioning=metadata,
        output=result.output,
        base=base,
    )


def load_versioning_metadata(
    config: SyncConfig, building_ids: list[str]
) -> dict[str, VersioningMetadata]:
    """Read 3DCityDB FEATURE metadata through psql, failing open to manual input.

    A missing psql executable or unavailable DB must not make semantic diffing
    fail.  It only means the review form must collect the missing explanation.
    Passwords are passed through PGPASSWORD, never command arguments.
    """
    if not building_ids or not shutil.which("psql"):
        return {}
    required = ("CITYDB_HOST", "CITYDB_NAME", "CITYDB_USERNAME")
    if not all(os.getenv(key) for key in required):
        return {}
    if any("\x1f" in building_id for building_id in building_ids):
        return {}

    schema = config.db_schema
    ids = "\x1f".join(building_ids)
    sql = f"""
SELECT json_build_object(
  'objectid', objectid,
  'last_modification_date', last_modification_date,
  'updating_person', updating_person,
  'reason_for_update', reason_for_update,
  'lineage', lineage,
  'creation_date', creation_date,
  'termination_date', termination_date
)::text
FROM \"{schema}\".feature
WHERE objectid = ANY(string_to_array(:'sync_ids', E'\\x1f'));
""".strip()
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": os.environ["CITYDB_HOST"],
            "PGPORT": os.getenv("CITYDB_PORT", "5432"),
            "PGDATABASE": os.environ["CITYDB_NAME"],
            "PGUSER": os.environ["CITYDB_USERNAME"],
            "PGPASSWORD": os.getenv("CITYDB_PASSWORD", ""),
        }
    )
    completed = run(
        [
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set",
            f"sync_ids={ids}",
            "--command",
            sql,
        ],
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        return {}
    result: dict[str, VersioningMetadata] = {}
    for line in completed.stdout.splitlines():
        try:
            item = json.loads(line)
            metadata = VersioningMetadata(**item)
            result[metadata.objectid] = metadata
        except (json.JSONDecodeError, TypeError):
            continue
    return result


def render_pr_body(plan: SyncPlan, proposal: ProposalInput) -> str:
    change = next((item for item in plan.changes if item["id"] == proposal.building_id), {})
    metadata = plan.versioning.get(proposal.building_id)
    modified_at = metadata.last_modification_date if metadata else None
    lines = [
        "## PR type",
        "- [x] `correction` (3DCityDB synchronization)",
        "",
        "## Target buildings / scope",
        f"- `{proposal.building_id}`",
        "",
        "## Summary of changes <!--sec:reason-->",
        proposal.reason.strip(),
        "",
        "## 3DCityDB versioning",
        f"- Operation: `{change.get('status', 'modified')}`",
        f"- Updated at: {modified_at or 'not recorded'}",
        f"- Updated by: {proposal.public_author.strip()}",
        f"- Lineage / source: {proposal.source.strip()}",
        "",
        "## Semantic diff",
    ]
    attr_diffs = change.get("attribute_diffs") or []
    if attr_diffs:
        for item in attr_diffs:
            lines.append(f"- `{item['path']}`: `{item.get('old')}` → `{item.get('new')}`")
    if change.get("geometry_changed"):
        lines.append("- Geometry changed (review the building preview).")
    if not attr_diffs and not change.get("geometry_changed"):
        lines.append(f"- Building status: `{change.get('status', 'changed')}`")
    lines.extend(
        [
            "",
            "## Automated checks",
            f"- [x] Minimal diff (`{plan.methods.get(proposal.building_id, 'unknown')}`)",
            "- [x] Semantic reconstruction self-check",
            "- [x] One-building scope",
            "",
            "## Additional notes (optional)",
            proposal.notes.strip() or "None.",
            "",
            "<!-- 4dcitygml-client:3dcitydb-sync -->",
        ]
    )
    return "\n".join(lines) + "\n"


def check_pr_readiness(
    config: SyncConfig, plan: SyncPlan, building_id: str
) -> PRReadiness:
    """Fetch the official branch and detect building-level conflicts before PR work."""
    if building_id not in plan.modified:
        raise SyncError("Select one modified existing building from the sync results.")
    status = inspect_environment(config)
    if not status.remote:
        raise SyncError("The official repository is not configured as the origin remote.")
    if not status.clean:
        raise SyncError("The working tree must be clean before checking the official repository.")

    remote_ref = f"refs/remotes/origin/{config.base_branch}"
    run(
        [
            "git",
            "fetch",
            "--quiet",
            "origin",
            f"refs/heads/{config.base_branch}:{remote_ref}",
        ],
        cwd=config.repository,
    )
    remote_sha = run(["git", "rev-parse", "--verify", remote_ref], cwd=config.repository).stdout.strip()
    ancestry = run(
        ["git", "merge-base", "--is-ancestor", plan.base_sha, remote_sha],
        cwd=config.repository,
        check=False,
    )
    if ancestry.returncode != 0:
        raise SyncError(
            "The official branch no longer contains the commit used by Sync. "
            "Update the local repository and run Sync again."
        )

    if remote_sha == plan.base_sha:
        return PRReadiness(
            building_id=building_id,
            base_sha=plan.base_sha,
            remote_sha=remote_sha,
            ready=True,
            conflict=False,
            requires_database_update=False,
            official_changed_buildings=[],
            message="The database proposal is based on the current official repository.",
        )

    relative = config.citygml.relative_to(config.repository).as_posix()
    completed = subprocess.run(
        ["git", "show", f"{remote_sha}:{relative}"],
        cwd=config.repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SyncError(f"Cannot read the official CityGML at {remote_sha}: {detail}")

    official_diff = diff_sources(
        plan.base,
        completed.stdout,
        plan.base_sha,
        remote_sha,
        include_unchanged=False,
    )
    changed_ids: set[str] = set()
    for change in official_diff["buildings"]:
        changed_ids.add(change["id"])
        if change.get("old_id"):
            changed_ids.add(change["old_id"])
    changed = sorted(changed_ids)
    conflict = building_id in changed_ids
    requires_update = bool(changed_ids) and not conflict
    if conflict:
        message = (
            f"Conflict detected: {building_id} also changed in the official repository. "
            "Update the database and review the combined change before preparing a PR."
        )
    elif requires_update:
        message = (
            "The official repository contains changes to other buildings. "
            "Update the database from the official repository, then run Sync again."
        )
    else:
        message = (
            "The official branch advanced, but the tracked CityGML has no semantic changes. "
            "This proposal can be prepared safely."
        )
    return PRReadiness(
        building_id=building_id,
        base_sha=plan.base_sha,
        remote_sha=remote_sha,
        ready=not conflict and not requires_update,
        conflict=conflict,
        requires_database_update=requires_update,
        official_changed_buildings=changed,
        message=message,
    )


def create_proposal(config: SyncConfig, plan: SyncPlan, proposal: ProposalInput) -> str:
    """Create and publish a one-building PR from an isolated temporary worktree."""
    if proposal.building_id not in plan.modified:
        raise SyncError("Select one modified existing building from the sync results.")
    change = next((item for item in plan.changes if item["id"] == proposal.building_id), {})
    if change.get("geometry_changed"):
        raise SyncError("Geometry changes require manual review and cannot be published by this version.")
    readiness = check_pr_readiness(config, plan, proposal.building_id)
    if not readiness.ready:
        raise SyncError(readiness.message)
    status = inspect_environment(config)
    if not status.remote:
        raise SyncError("The official repository is not configured as the origin remote.")
    if not status.github_authenticated:
        raise SyncError("GitHub CLI is not authenticated. Run gh auth login and try again.")
    if not proposal.reason.strip() or not proposal.source.strip() or not proposal.public_author.strip():
        raise SyncError("Public author, reason for update, and source are required.")
    current_sha = run(["git", "rev-parse", "HEAD"], cwd=config.repository).stdout.strip()
    if current_sha != plan.base_sha:
        raise SyncError("HEAD changed after Sync. Run Sync again.")
    if run(["git", "status", "--porcelain"], cwd=config.repository).stdout.strip():
        raise SyncError("The working tree changed after Sync. Run Sync again.")

    proposal_output = output_for_building(plan, proposal.building_id)

    short_id = SAFE_BUILDING.sub("-", proposal.building_id).strip("-")[:60] or "building"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    branch = f"citydb/{short_id}-{stamp}"
    relative_citygml = config.citygml.relative_to(config.repository)
    title = f"3DCityDB: update {proposal.building_id}"
    body = render_pr_body(plan, proposal)

    with tempfile.TemporaryDirectory(prefix="4dcitygml-pr-") as tmp:
        worktree = Path(tmp) / "worktree"
        worktree_added = False
        try:
            run(
                ["git", "worktree", "add", "-b", branch, str(worktree), plan.base_sha],
                cwd=config.repository,
            )
            worktree_added = True
            target = worktree / relative_citygml
            target.write_bytes(proposal_output)
            run(["git", "add", "--", str(relative_citygml)], cwd=worktree)
            message = f"3DCityDB: update {proposal.building_id}\n\nBuilding: {proposal.building_id}"
            run(["git", "commit", "-m", message], cwd=worktree)
            run(["git", "push", "-u", "origin", branch], cwd=worktree)
            created = run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--base",
                    config.base_branch,
                    "--head",
                    branch,
                    "--title",
                    title,
                    "--body",
                    body,
                ],
                cwd=worktree,
            )
            return created.stdout.strip()
        finally:
            if worktree_added:
                run(
                    ["git", "worktree", "remove", "--force", str(worktree)],
                    cwd=config.repository,
                    check=False,
                )


def plan_to_json(plan: SyncPlan) -> str:
    return json.dumps(plan.public_dict(), ensure_ascii=False, sort_keys=True)


def output_for_building(plan: SyncPlan, building_id: str) -> bytes:
    """Extract one reviewed building change from a possibly multi-building plan."""
    base_spans = building_spans(plan.base)
    output_spans = building_spans(plan.output)
    if building_id not in base_spans or building_id not in output_spans:
        raise SyncError("The selected building cannot be isolated as a one-building pull request.")
    base_start, base_end = base_spans[building_id]
    output_start, output_end = output_spans[building_id]
    return plan.base[:base_start] + plan.output[output_start:output_end] + plan.base[base_end:]
