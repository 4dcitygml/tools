#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""source-update: apply one attribute family of a new official edition to the
buildings already in the repository — manifest generation, byte-preserving
application, per-building commits, and the reproduction check used by CI
(docs/bulk-submission-provenance.md).

Scope rules (PR operations guide §4.4 / pre-sorting procedure §3):
- one PR = one source transition × one mesh × one attribute family;
- targets are buildings whose stable ``uro:buildingID`` exists in both the
  current repository file and the new edition (identity is settled first by
  identity-baseline; buildings only on one side are lifecycle, not this PR);
- a change is a modified leaf value at a path of the family; the value is
  replaced in place (byte-preserving, exchange contract A3). Leaves that are
  added or removed, or whose tag/value is ambiguous inside the building, are
  excluded with a reason and left for a block-level PR.

Subcommands: generate / apply / commits / verify (same shape as identity_manifest.py).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import random
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import analyze_yearly_citygml_mesh as A  # noqa: E402
from scripts.identity_manifest import _environment, _file_material, load_edition  # noqa: E402
from scripts.plan_yearly_citygml_transition import attribute_family, base_path  # noqa: E402
from scripts.provenance_manifest import canonical_bytes, manifest_ref, sha256_hex, validate  # noqa: E402
from scripts.reconstruct_minimal import _tag_localname, building_spans  # noqa: E402

FAMILIES = ("address", "lod_quality", "source_quality", "disaster_risk", "storeys", "usage_class_landuse",
            "planning_zoning_rates", "survey_building_detail", "generic_attributes", "other_attributes")


def _leaf_pattern(tag_local: str) -> re.Pattern[bytes]:
    t = re.escape(tag_local.encode("utf-8"))
    return re.compile(rb"(<(?:\w+:)?" + t + rb"\b[^>]*>)([^<]*)(</(?:\w+:)?" + t + rb">)")


def _unique_leaf(span: bytes, tag_local: str, normalized_value: str) -> re.Match[bytes] | None:
    """The single leaf element of ``tag_local`` in ``span`` whose (numerically
    normalized) text equals ``normalized_value``; None when 0 or several."""
    wanted = A.norm_num(normalized_value)
    hits = []
    for m in _leaf_pattern(tag_local).finditer(span):
        text = m.group(2).decode("utf-8", errors="replace").strip()
        # raw equality first (codes are strings: "000" != "0"), numeric normalization as fallback
        if text == normalized_value or A.norm_num(text) == wanted:
            hits.append(m)
    return hits[0] if len(hits) == 1 else None


_CODESPACE_RE = re.compile(rb'\scodeSpace="[^"]*"')


def apply_changes_to_member(member: bytes, changes: list[dict]) -> bytes:
    """Apply [{path, old, new_raw[, code_space]}] to one building's bytes (each leaf must be unique).

    ``code_space`` rewrites (or adds) the leaf's codeSpace attribute: a carried
    code keeps its old edition's code list explicitly (gml:CodeType semantics)."""
    for change in changes:
        tag = _tag_localname(change["path"])
        m = _unique_leaf(member, tag, change["old"])
        if m is None:
            raise SystemExit(f"leaf {change['path']} with value {change['old']!r} is not unique in the building")
        open_tag = m.group(1)
        if change.get("code_space"):
            attr = b' codeSpace="' + change["code_space"].encode("utf-8") + b'"'
            open_tag = _CODESPACE_RE.sub(attr, open_tag, count=1) if _CODESPACE_RE.search(open_tag) else open_tag[:-1] + attr + b">"
        member = member[:m.start(1)] + open_tag + change["new_raw"].encode("utf-8") + member[m.end(2):]
    return member


def apply_manifest(raw: bytes, targets: dict[str, list[dict]], gml_ids: dict[str, str]) -> bytes:
    """Apply per-building changes to a whole file; ``gml_ids`` maps buildingID -> gml:id."""
    spans = building_spans(raw)
    edits: list[tuple[int, int, bytes]] = []
    for stable, changes in targets.items():
        gml_id = gml_ids[stable]
        start, end = spans[gml_id]
        edits.append((start, end, apply_changes_to_member(raw[start:end], changes)))
    out = raw
    for start, end, member in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + member + out[end:]
    return out


def plan_changes(current: dict[str, A.Building], new: dict[str, A.Building], family: str,
                 current_raw: bytes, new_raw: bytes) -> tuple[dict[str, list[dict]], list[dict], dict]:
    """Per target building: the family's modified leaves with raw old/new text."""
    cur_spans = building_spans(current_raw)
    new_spans = building_spans(new_raw)
    changes: dict[str, list[dict]] = {}
    excluded: list[dict] = []
    shared = sorted(set(current) & set(new))
    for stable in shared:
        a_old, a_new = current[stable].attrs, new[stable].attrs
        cur_member = current_raw[slice(*cur_spans[current[stable].id])]
        new_member = new_raw[slice(*new_spans[new[stable].id])]
        for path in sorted(set(a_old) | set(a_new)):
            if attribute_family(base_path(path)) != family:
                continue
            old, newv = a_old.get(path), a_new.get(path)
            if old == newv:
                continue
            if old is None or newv is None:
                excluded.append({"id": stable, "path": path, "reason": "leaf added or removed: needs a block-level update, not a value replacement"})
                continue
            tag = _tag_localname(path)
            m_old = _unique_leaf(cur_member, tag, old)
            m_new = _unique_leaf(new_member, tag, newv)
            if m_old is None or m_new is None:
                excluded.append({"id": stable, "path": path, "reason": "leaf tag/value not unique inside the building (ambiguous replacement)"})
                continue
            changes.setdefault(stable, []).append({
                "path": path, "old": old, "new": newv,
                "new_raw": m_new.group(2).decode("utf-8", errors="replace"),
            })
    counts = {"shared": len(shared), "only_current": len(set(current) - set(new)), "only_new": len(set(new) - set(current)),
              "buildings_changed": len(changes), "changes": sum(len(v) for v in changes.values()), "excluded": len(excluded)}
    return changes, excluded, counts


def build_manifest(args: argparse.Namespace) -> tuple[dict, bytes]:
    current_path, new_label, new_path = Path(args.current), args.edition_new[0], Path(args.edition_new[1])
    current = load_edition(current_path, args.municipality)
    new = load_edition(new_path, args.municipality)
    current_raw, new_raw = current_path.read_bytes(), new_path.read_bytes()
    changes, excluded, counts = plan_changes(current, new, args.family, current_raw, new_raw)
    gml_ids = {stable: b.id for stable, b in current.items()}
    product = apply_manifest(current_raw, changes, gml_ids)
    targets = sorted(changes)
    rng = random.Random(args.seed)
    sample = sorted(rng.sample(targets, min(args.sample_size, len(targets)))) if targets else []
    change_list = [{"id": stable, **{k: v for k, v in c.items()}} for stable in targets for c in changes[stable]]
    manifest = {
        "schemaVersion": 1,
        "kind": "source-update",
        "repository": args.repository,
        "scope": {"mesh": args.mesh, "municipality": args.municipality, "edition_from": args.current_label, "edition_to": new_label},
        "plan_issue": args.plan_issue,
        "materials": [_file_material("current", current_path, args.current_uri), _file_material(new_label, new_path, args.new_uri)],
        "builder": {"tools_repo": args.tools_repo, "tools_commit": args.tools_commit, "script": "scripts/source_update_manifest.py"},
        "invocation": {
            "command": ["python3", "scripts/source_update_manifest.py", "generate", "--mesh", args.mesh, "--municipality", args.municipality,
                        "--family", args.family, "--current", current_path.name, "--edition-new", f"{new_label}={new_path.name}",
                        "--product", args.product, "--seed", str(args.seed), "--sample-size", str(args.sample_size)],
            "parameters": {"attribute_family": args.family},
            "environment": _environment(),
        },
        "products": [{"path": args.product, "sha256": sha256_hex(product), "buildings": len(current)}],
        "evidence": {
            "attribute_family": args.family,
            "allowed_paths": sorted({base_path(c["path"]) for c in change_list}),
            "targets": targets,
            "changes": change_list,
            "excluded": excluded,
            "counts": counts,
        },
        "sample_audit": {"seed": args.seed, "size": args.sample_size, "ids": sample, "reviewed_by": "", "result": "pending", "notes": ""},
        "generated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    return manifest, product


def _reproducible_view(manifest: dict) -> dict:
    return {"kind": manifest["kind"], "scope": manifest["scope"], "products": manifest["products"], "evidence": manifest["evidence"],
            "materials": [{k: m[k] for k in ("name", "sha256", "bytes")} for m in manifest["materials"]]}


def changes_by_building(manifest: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in manifest["evidence"]["changes"]:
        out.setdefault(c["id"], []).append({k: v for k, v in c.items() if k != "id"})
    return out


def commit_message(stable: str, changes: list[dict], family: str, edition_to: str, manifest_path: str, manifest_bytes: bytes) -> str:
    labels = ", ".join(f"{_tag_localname(c['path'])}: {c['old']} → {c['new']}" for c in changes[:3])
    more = f" (+{len(changes) - 3} more)" if len(changes) > 3 else ""
    return (f"Update {family} of {stable} from the {edition_to} edition: {labels}{more}\n\n"
            f"Building: {stable}\nAttribute-Family: {family}\nSource-To: {edition_to}\n"
            f"Provenance-Manifest: {manifest_ref(manifest_path, manifest_bytes)}\n"
            f"Created-By: source_update_manifest.py/source-update\n")


def cmd_generate(args: argparse.Namespace) -> int:
    manifest, product = build_manifest(args)
    errors = validate(manifest)
    if errors:
        print("manifest does not conform to the schema:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 2
    data = (json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_bytes(data)
    if args.apply_output:
        Path(args.apply_output).write_bytes(product)
    ev = manifest["evidence"]
    print(json.dumps({"output": args.output, "family": args.family, "counts": ev["counts"], "allowed_paths": ev["allowed_paths"]}, ensure_ascii=False))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    raw = Path(args.input).read_bytes()
    current = load_edition(Path(args.input), manifest["scope"]["municipality"])
    out = apply_manifest(raw, changes_by_building(manifest), {s: b.id for s, b in current.items()})
    Path(args.output).write_bytes(out)
    print(f"applied changes to {len(manifest['evidence']['targets'])} buildings -> {args.output} sha256 {sha256_hex(out)}")
    return 0


def cmd_commits(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    manifest_path = Path(args.manifest)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    rel_manifest = manifest_path.resolve().relative_to(repo.resolve()).as_posix()
    product = manifest["products"][0]["path"]
    target = repo / product
    per_building = changes_by_building(manifest)
    current = load_edition(target, manifest["scope"]["municipality"])
    gml_ids = {s: b.id for s, b in current.items()}
    raw = target.read_bytes()
    spans = building_spans(raw)  # computed once: commits are applied from the end of the file backwards,
    # so every remaining building's span stays valid while earlier ones are edited in place
    ordered = sorted(manifest["evidence"]["targets"], key=lambda s: spans[gml_ids[s]][0], reverse=True)
    for stable in ordered:
        start, end = spans[gml_ids[stable]]
        raw = raw[:start] + apply_changes_to_member(raw[start:end], per_building[stable]) + raw[end:]
        target.write_bytes(raw)
        subprocess.run(["git", "-C", str(repo), "-c", "core.looseCompression=1", "add", "--", product, rel_manifest], check=True)
        message = commit_message(stable, per_building[stable], manifest["evidence"]["attribute_family"],
                                 manifest["scope"]["edition_to"], rel_manifest, manifest_bytes)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-F", "-"], input=message.encode(), check=True)
    # hundreds of commits each store a full (multi-MB) blob: repack now so the clone stays small before push
    subprocess.run(["git", "-C", str(repo), "gc", "-q"], check=False)
    final = sha256_hex(target.read_bytes())
    if final != manifest["products"][0]["sha256"]:
        print(f"::error::product digest after applying all changes {final} != manifest {manifest['products'][0]['sha256']}", file=sys.stderr)
        return 1
    print(f"{len(manifest['evidence']['targets'])} commits created; product digest matches the manifest")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    committed = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    errors = validate(committed)
    if errors:
        print("::error::manifest schema: " + "; ".join(errors[:5]))
        return 1
    if getattr(args, "materials_dir", None):
        import urllib.parse
        import urllib.request
        base = Path(args.materials_dir)
        located: dict[str, str] = {}
        for material in committed["materials"]:
            members = material.get("members") or []
            uri = urllib.parse.urlparse(material["uri"])
            if members:
                located[material["name"]] = str(base / members[0]["path"])
            elif uri.scheme == "file":
                located[material["name"]] = urllib.request.url2pathname(uri.path)
            else:
                located[material["name"]] = str(base / material["name"])
        args.current = located["current"]
        new_label = committed["scope"]["edition_to"]
        args.edition_new = (new_label, located[new_label])
    if not getattr(args, "current", None) or not getattr(args, "edition_new", None):
        print("::error::verify needs --current and --edition-new (or --materials-dir)")
        return 1
    args.family = committed["evidence"]["attribute_family"]
    args.repository = committed["repository"]; args.mesh = committed["scope"]["mesh"]; args.municipality = committed["scope"]["municipality"]
    args.current_label = committed["scope"]["edition_from"]; args.plan_issue = committed["plan_issue"]
    args.tools_repo = committed["builder"]["tools_repo"]; args.tools_commit = committed["builder"]["tools_commit"]
    args.product = committed["products"][0]["path"]; args.seed = committed["sample_audit"]["seed"]; args.sample_size = committed["sample_audit"]["size"]
    args.current_uri = None; args.new_uri = None
    regenerated, _product = build_manifest(args)
    a, b = _reproducible_view(committed), _reproducible_view(regenerated)
    if canonical_bytes(a) != canonical_bytes(b):
        for key in a:
            if canonical_bytes(a[key]) != canonical_bytes(b[key]):
                print(f"::error::reproduction mismatch in '{key}'")
        return 1
    print("reproduction: OK (materials, evidence, and products regenerate identically)")
    return 0


def _edition(value: str) -> tuple[str, str]:
    label, _sep, path = value.partition("=")
    if not path:
        raise argparse.ArgumentTypeError("LABEL=PATH expected")
    return label, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--repository", required=True); g.add_argument("--mesh", required=True); g.add_argument("--municipality", required=True)
    g.add_argument("--family", required=True, choices=FAMILIES)
    g.add_argument("--current", required=True, help="the repository's current GML (parent commit state)")
    g.add_argument("--current-label", default="current"); g.add_argument("--current-uri", help="git:<sha>:<path> or URL of the current file")
    g.add_argument("--edition-new", type=_edition, required=True, help="LABEL=PATH of the new official edition GML")
    g.add_argument("--new-uri", help="<zip-url>#<member> or URL of the new edition file")
    g.add_argument("--product", required=True); g.add_argument("--tools-repo", default="4dcitygml/tools"); g.add_argument("--tools-commit", required=True)
    g.add_argument("--plan-issue", required=True); g.add_argument("--seed", type=int, default=20260902); g.add_argument("--sample-size", type=int, default=30)
    g.add_argument("--output", required=True); g.add_argument("--apply-output")
    g.set_defaults(func=cmd_generate)
    a = sub.add_parser("apply"); a.add_argument("--manifest", required=True); a.add_argument("--input", required=True); a.add_argument("--output", required=True); a.set_defaults(func=cmd_apply)
    c = sub.add_parser("commits"); c.add_argument("--repo", required=True); c.add_argument("--manifest", required=True); c.set_defaults(func=cmd_commits)
    v = sub.add_parser("verify"); v.add_argument("--manifest", required=True); v.add_argument("--current"); v.add_argument("--edition-new", type=_edition)
    v.add_argument("--materials-dir"); v.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
