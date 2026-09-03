#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""carry-forward: re-base the repository's accumulated attribute changes onto a
new official edition (possibly a different i-UR edition) by a three-way
comparison per building and semantic attribute.

Inputs (all for one mesh and municipality):
  base     the official edition the repository currently derives from
  current  the repository's current file (same edition as base; carries the
           reviewed local changes)
  new      the new official edition (the next baseline)

For every building present in all three (by uro:buildingID) and every semantic
key (semantics/registry.json), with old = base value, cur = current value,
new = new value at the key's path in the new edition:

  cur == old            -> nothing to carry (the official value is taken)
  key absent in new ed. -> unmappable   (held; extension namespace later)
  new == cur            -> absorbed     (the official edition took our change)
  new == old            -> reapply      (our change is re-applied on the new file)
  otherwise             -> conflict     (both sides changed; human decision)
  leaf missing in new   -> insert-needed (value replacement cannot add a leaf)

Keys split by an edition change (registry predecessor/successors) distribute
the single old value to each successor present. Multi-valued keys (repeated
elements) with a local change are reported as conflicts (manual).

Subcommands: generate / apply / commits / verify (same shape as identity_manifest.py).
The product is the new official file with the reapplied values; commits are
one `Building:` commit per building, gated like source-update.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import random
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import analyze_yearly_citygml_mesh as A  # noqa: E402
from scripts import codelist_crosswalk as X  # noqa: E402
from scripts import semantic_registry as R  # noqa: E402
from scripts.identity_manifest import _environment, _file_material, load_edition  # noqa: E402
from scripts.provenance_manifest import canonical_bytes, manifest_ref, sha256_hex, validate  # noqa: E402
from scripts.reconstruct_minimal import _tag_localname, building_spans  # noqa: E402
from scripts.source_update_manifest import _unique_leaf, apply_changes_to_member, apply_manifest  # noqa: E402


def _values_by_key(attrs: dict[str, str], edition: str, member: bytes | None = None) -> dict[str, list[str]]:
    """Semantic key -> list of values (repeated elements keep their order).

    Numeric attributes use the numerically normalized value; **coded**
    attributes (registry entries with a codelist) use the raw text of the
    leaf, because codes are strings: "000" and "0" are different codes."""
    reg = R.load_registry()["attributes"]
    out: dict[str, list[str]] = defaultdict(list)
    for path in sorted(attrs, key=lambda p: (R.normalize_path(p), p)):
        key = R.key_for(path, edition)
        if not key:
            continue
        value = attrs[path]
        if member is not None and reg[key].get("codelist"):
            m = _unique_leaf(member, _tag_localname(path), value)
            if m is not None:
                value = m.group(2).decode("utf-8", errors="replace").strip()
        out[key].append(value)
    return dict(out)


def _raw_leaf(member: bytes, path: str, value: str) -> str | None:
    m = _unique_leaf(member, _tag_localname(path), value) or _unique_leaf(member, _tag_localname(path), A.norm_num(value))
    return m.group(2).decode("utf-8", errors="replace") if m else None


def load_crosswalk(edition_from: str, edition_to: str, explicit: str | None = None, overrides: str | None = None) -> dict:
    """The shared code-list crosswalk for the edition pair (semantics/codelists/<from>__<to>.json),
    with the city's reviewed overrides (same shape, e.g. semantics/overrides.json in the city
    repository) merged on top: a city's rule for a refined code wins over the shared candidates."""
    path = Path(explicit) if explicit else REPO_ROOT / "semantics" / "codelists" / f"{edition_from}__{edition_to}.json"
    crosswalk = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"from": edition_from, "to": edition_to, "lists": {}}
    if overrides and Path(overrides).is_file():
        extra = json.loads(Path(overrides).read_text(encoding="utf-8"))
        pairs = extra.get("pairs", {}).get(f"{edition_from}__{edition_to}", extra if extra.get("from") == edition_from and extra.get("to") == edition_to else {})
        for key, entry in pairs.get("lists", {}).items():
            target = crosswalk["lists"].setdefault(key, {"codes": {}})
            for code, rule in entry.get("codes", {}).items():
                target.setdefault("codes", {})[code] = {**rule, "confidence": "reviewed"}
        crosswalk["overrides_sha256"] = sha256_hex(Path(overrides).read_bytes())
    return crosswalk


def _code_space(product: str, edition_from: str, crosswalk: dict, key: str) -> str:
    """codeSpace pointing at the old edition's code list kept in the city repository (codelists/<edition>/<file>)."""
    entry = crosswalk.get("lists", {}).get(key, {})
    name = entry.get("from_file") or (X.codelist_file_candidates(key, edition_from) or ["unknown.xml"])[0]
    return os.path.relpath(f"codelists/{edition_from}/{name}", os.path.dirname(product) or ".").replace(os.sep, "/")


def three_way(base: dict[str, A.Building], current: dict[str, A.Building], new: dict[str, A.Building],
              edition_from: str, edition_to: str, current_raw: bytes, new_raw: bytes, base_raw: bytes,
              crosswalk: dict | None = None, product: str = "udx/bldg/x.gml") -> dict:
    reg = R.load_registry()["attributes"]
    crosswalk = crosswalk or {"lists": {}}
    carried: list[dict] = []
    cur_spans, new_spans, base_spans = building_spans(current_raw), building_spans(new_raw), building_spans(base_raw)
    ops: dict[str, list[dict]] = {}          # reapply: building -> changes for apply_changes_to_member
    absorbed: list[dict] = []
    conflicts: list[dict] = []
    unmappable: list[dict] = []
    insert_needed: list[dict] = []
    counts = defaultdict(int)
    shared = sorted(set(base) & set(current) & set(new))
    for stable in shared:
        cur_member = current_raw[slice(*cur_spans[current[stable].id])]
        new_member = new_raw[slice(*new_spans[new[stable].id])]
        base_member = base_raw[slice(*base_spans[base[stable].id])]
        vb = _values_by_key(base[stable].attrs, edition_from, base_member)
        vc = _values_by_key(current[stable].attrs, edition_from, cur_member)
        vn = _values_by_key(new[stable].attrs, edition_to, new_member)
        for key in sorted(set(vb) | set(vc)):
            if reg[key].get("role") == "stable_id":
                continue
            old_vals, cur_vals = vb.get(key, []), vc.get(key, [])
            if old_vals == cur_vals:
                counts["unchanged_locally"] += 1
                continue
            counts["local_changes"] += 1
            if len(cur_vals) != 1 or len(old_vals) > 1:
                conflicts.append({"id": stable, "key": key, "old": old_vals, "cur": cur_vals, "new": vn.get(key, []), "reason": "multi-valued attribute changed locally; manual"})
                continue
            old, cur = (old_vals[0] if old_vals else None), cur_vals[0]
            targets = [key]
            if edition_to not in reg[key]["paths"]:
                targets = [s for s in reg[key].get("successors", []) if edition_to in reg[s]["paths"]]
                if not targets:
                    unmappable.append({"id": stable, "key": key, "old": old, "cur": cur, "reason": f"{key} has no path in {edition_to}"})
                    continue
            for target in targets:
                new_path = reg[target]["paths"][edition_to]
                new_vals = vn.get(target, [])
                if not new_vals:
                    if target != key:
                        counts["split_successor_absent"] += 1  # e.g. no LoD3 in this building: nothing to distribute to
                        continue
                    insert_needed.append({"id": stable, "key": target, "cur": cur, "reason": f"{new_path} absent in the new building; value replacement cannot add a leaf"})
                    continue
                official = new_vals[0]
                coded = bool(reg[key].get("codelist")) and edition_from != edition_to
                cur_text = cur  # the text to locate in the current building (before any code mapping)
                if coded:
                    # map our code and the previous official code into the new edition's code list;
                    # a code that is not 1:1 in the crosswalk keeps its old codeSpace (no information loss)
                    cur_mapped = X.resolve(crosswalk, key, cur) if cur is not None else None
                    old_mapped = X.resolve(crosswalk, key, old) if old is not None else None
                    if cur_mapped is None:
                        cur_raw = _raw_leaf(cur_member, reg[key]["paths"][edition_from], cur)
                        if cur_raw is not None and (old_mapped == official or old_mapped is None):
                            ops.setdefault(stable, []).append({"path": new_path, "old": official, "new": cur, "new_raw": cur_raw, "from_key": key,
                                                               "code_space": _code_space(product, edition_from, crosswalk, key), "carried": True})
                            carried.append({"id": stable, "key": target, "value": cur, "reason": f"code {cur} is not 1:1 in the {edition_from}->{edition_to} crosswalk; kept with the {edition_from} codeSpace"})
                        else:
                            conflicts.append({"id": stable, "key": target, "old": old, "cur": cur, "new": official, "reason": "unmapped code and the official value changed too"})
                        continue
                    cur, old = cur_mapped, (old_mapped if old_mapped is not None else old)
                if official == cur:
                    absorbed.append({"id": stable, "key": target, "value": cur})
                elif old is not None and official == old:
                    cur_raw = _raw_leaf(cur_member, reg[key]["paths"][edition_from], cur_text)
                    if cur_raw is None or (_unique_leaf(new_member, _tag_localname(new_path), official) or _unique_leaf(new_member, _tag_localname(new_path), A.norm_num(official))) is None:
                        conflicts.append({"id": stable, "key": target, "old": old, "cur": cur, "new": official, "reason": "leaf not unique in the building; manual"})
                        continue
                    # a mapped code is written as the new edition's code; other values keep their raw text
                    ops.setdefault(stable, []).append({"path": new_path, "old": official, "new": cur, "new_raw": (cur if coded and cur != cur_text else cur_raw), "from_key": key})
                else:
                    conflicts.append({"id": stable, "key": target, "old": old, "cur": cur, "new": official, "reason": "both sides changed to different values"})
    lifecycle = {"only_base_current": sorted((set(base) & set(current)) - set(new)), "only_new": sorted(set(new) - set(current)),
                 "current_not_in_base": sorted(set(current) - set(base))}
    return {"shared": len(shared), "counts": dict(counts), "ops": ops, "absorbed": absorbed, "conflicts": conflicts,
            "unmappable": unmappable, "insert_needed": insert_needed, "lifecycle": lifecycle, "carried": carried}


def build_manifest(args: argparse.Namespace) -> tuple[dict, bytes]:
    base_p, cur_p, new_p = Path(args.base), Path(args.current), Path(args.new)
    base_raw, cur_raw, new_raw = base_p.read_bytes(), cur_p.read_bytes(), new_p.read_bytes()
    edition_from = R.detect_edition(cur_raw) or args.edition_from
    edition_to = R.detect_edition(new_raw) or args.edition_to
    if R.detect_edition(base_raw) not in (None, edition_from):
        raise SystemExit("base and current must be the same edition")
    base = load_edition(base_p, args.municipality); current = load_edition(cur_p, args.municipality); new = load_edition(new_p, args.municipality)
    crosswalk = load_crosswalk(edition_from, edition_to, getattr(args, "crosswalk", None), getattr(args, "overrides", None))
    result = three_way(base, current, new, edition_from, edition_to, cur_raw, new_raw, base_raw, crosswalk, args.product)
    gml_ids = {s: b.id for s, b in new.items()}
    product = apply_manifest(new_raw, result["ops"], gml_ids)
    targets = sorted(result["ops"])
    rng = random.Random(args.seed)
    sample = sorted(rng.sample(targets, min(args.sample_size, len(targets)))) if targets else []
    change_list = [{"id": s, **c} for s in targets for c in result["ops"][s]]
    manifest = {
        "schemaVersion": 1, "kind": "carry-forward", "repository": args.repository,
        "scope": {"mesh": args.mesh, "municipality": args.municipality, "edition_from": edition_from, "edition_to": edition_to},
        "plan_issue": args.plan_issue,
        "materials": [_file_material("base", base_p, args.base_uri), _file_material("current", cur_p, args.current_uri), _file_material("new", new_p, args.new_uri)],
        "builder": {"tools_repo": args.tools_repo, "tools_commit": args.tools_commit, "script": "scripts/carry_forward_manifest.py"},
        "invocation": {"command": ["python3", "scripts/carry_forward_manifest.py", "generate", "--mesh", args.mesh, "--municipality", args.municipality,
                                   "--base", base_p.name, "--current", cur_p.name, "--new", new_p.name, "--product", args.product,
                                   "--seed", str(args.seed), "--sample-size", str(args.sample_size)],
                       "parameters": {"registry_sha256": sha256_hex(R.REGISTRY_PATH.read_bytes())}, "environment": _environment()},
        "products": [{"path": args.product, "sha256": sha256_hex(product), "buildings": len(new)}],
        "evidence": {
            "edition_from": edition_from, "edition_to": edition_to, "shared": result["shared"], "counts": result["counts"],
            "targets": targets, "changes": change_list,
            "absorbed": result["absorbed"], "conflicts": result["conflicts"], "unmappable": result["unmappable"],
            "insert_needed": result["insert_needed"], "carried_old_codespace": result["carried"], "lifecycle": result["lifecycle"],
            "crosswalk": {"from": crosswalk.get("from"), "to": crosswalk.get("to"), "lists": len(crosswalk.get("lists", {})),
                          "overrides_sha256": crosswalk.get("overrides_sha256")},
            "summary": {"reapply": len(change_list), "absorbed": len(result["absorbed"]), "conflicts": len(result["conflicts"]),
                        "unmappable": len(result["unmappable"]), "insert_needed": len(result["insert_needed"]),
                        "carried_old_codespace": len(result["carried"])},
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


def commit_message(stable: str, changes: list[dict], edition_from: str, edition_to: str, manifest_path: str, manifest_bytes: bytes) -> str:
    labels = ", ".join(f"{c['from_key']}: {c['old']} → {c['new']}" for c in changes[:3])
    more = f" (+{len(changes) - 3} more)" if len(changes) > 3 else ""
    return (f"Carry forward {stable} changes onto the {edition_to} edition: {labels}{more}\n\n"
            f"Building: {stable}\nCarry-Forward-From: {edition_from}\nSource-To: {edition_to}\n"
            f"Provenance-Manifest: {manifest_ref(manifest_path, manifest_bytes)}\n"
            f"Created-By: carry_forward_manifest.py/carry-forward\n")


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
    print(json.dumps({"output": args.output, "editions": [ev["edition_from"], ev["edition_to"]], "shared": ev["shared"], "counts": ev["counts"],
                      "summary": ev["summary"], "lifecycle": {k: len(v) for k, v in ev["lifecycle"].items()}}, ensure_ascii=False))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    raw = Path(args.input).read_bytes()
    new = load_edition(Path(args.input), manifest["scope"]["municipality"])
    out = apply_manifest(raw, changes_by_building(manifest), {s: b.id for s, b in new.items()})
    Path(args.output).write_bytes(out)
    print(f"applied {len(manifest['evidence']['targets'])} buildings -> {args.output} sha256 {sha256_hex(out)}")
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
    edition_from = manifest["scope"]["edition_from"]
    if manifest["evidence"].get("carried_old_codespace"):
        # carried codes point at codelists/<edition_from>/<file>: keep those lists in the repository first
        source = Path(args.codelists_from) if getattr(args, "codelists_from", None) else None
        dest = repo / "codelists" / edition_from
        if source is None or not source.is_dir():
            print(f"::error::carried codes need the {edition_from} code lists: pass --codelists-from <dir> (they are stored under codelists/{edition_from}/)", file=sys.stderr)
            return 1
        dest.mkdir(parents=True, exist_ok=True)
        for f in sorted(source.glob("*.xml")):
            (dest / f.name).write_bytes(f.read_bytes())
        subprocess.run(["git", "-C", str(repo), "add", "--", f"codelists/{edition_from}", rel_manifest], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-F", "-"],
                       input=(f"Keep the {edition_from} code lists for codes carried with their old codeSpace\n\n"
                              f"Provenance-Manifest: {manifest_ref(rel_manifest, manifest_bytes)}\nCreated-By: carry_forward_manifest.py/carry-forward\n").encode(), check=True)
    current = load_edition(target, manifest["scope"]["municipality"])
    gml_ids = {s: b.id for s, b in current.items()}
    raw = target.read_bytes()
    spans = building_spans(raw)
    for stable in sorted(manifest["evidence"]["targets"], key=lambda s: spans[gml_ids[s]][0], reverse=True):
        start, end = spans[gml_ids[stable]]
        raw = raw[:start] + apply_changes_to_member(raw[start:end], per_building[stable]) + raw[end:]
        target.write_bytes(raw)
        subprocess.run(["git", "-C", str(repo), "-c", "core.looseCompression=1", "add", "--", product, rel_manifest], check=True)
        message = commit_message(stable, per_building[stable], manifest["scope"]["edition_from"], manifest["scope"]["edition_to"], rel_manifest, manifest_bytes)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-F", "-"], input=message.encode(), check=True)
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
        located: dict[str, str] = {}
        for material in committed["materials"]:
            uri = urllib.parse.urlparse(material["uri"])
            members = material.get("members") or []
            if members:
                located[material["name"]] = str(Path(args.materials_dir) / members[0]["path"])
            elif uri.scheme == "file":
                located[material["name"]] = urllib.request.url2pathname(uri.path)
            else:
                located[material["name"]] = str(Path(args.materials_dir) / material["name"])
        args.base, args.current, args.new = located["base"], located["current"], located["new"]
    if not all(getattr(args, k, None) for k in ("base", "current", "new")):
        print("::error::verify needs --base/--current/--new or --materials-dir")
        return 1
    for k in ("repository", "plan_issue"):
        setattr(args, k, committed[k])
    args.mesh = committed["scope"]["mesh"]; args.municipality = committed["scope"]["municipality"]
    args.edition_from = committed["scope"]["edition_from"]; args.edition_to = committed["scope"]["edition_to"]
    args.tools_repo = committed["builder"]["tools_repo"]; args.tools_commit = committed["builder"]["tools_commit"]
    args.product = committed["products"][0]["path"]; args.seed = committed["sample_audit"]["seed"]; args.sample_size = committed["sample_audit"]["size"]
    args.base_uri = args.current_uri = args.new_uri = None
    args.crosswalk = getattr(args, "crosswalk", None)
    args.overrides = getattr(args, "overrides", None)
    regenerated, _product = build_manifest(args)
    a, b = _reproducible_view(committed), _reproducible_view(regenerated)
    if canonical_bytes(a) != canonical_bytes(b):
        for key in a:
            if canonical_bytes(a[key]) != canonical_bytes(b[key]):
                print(f"::error::reproduction mismatch in '{key}'")
        return 1
    print("reproduction: OK (materials, evidence, and products regenerate identically)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--repository", required=True); g.add_argument("--mesh", required=True); g.add_argument("--municipality", required=True)
    g.add_argument("--base", required=True); g.add_argument("--current", required=True); g.add_argument("--new", required=True)
    g.add_argument("--edition-from"); g.add_argument("--edition-to")
    g.add_argument("--base-uri"); g.add_argument("--current-uri"); g.add_argument("--new-uri")
    g.add_argument("--product", required=True); g.add_argument("--tools-repo", default="4dcitygml/tools"); g.add_argument("--tools-commit", required=True)
    g.add_argument("--plan-issue", required=True); g.add_argument("--seed", type=int, default=20260903); g.add_argument("--sample-size", type=int, default=30)
    g.add_argument("--crosswalk", help="code-list crosswalk JSON (default: semantics/codelists/<from>__<to>.json in tools)")
    g.add_argument("--overrides", help="the city's reviewed code rules (semantics/overrides.json in the city repository)")
    g.add_argument("--output", required=True); g.add_argument("--apply-output")
    g.set_defaults(func=cmd_generate)
    a = sub.add_parser("apply"); a.add_argument("--manifest", required=True); a.add_argument("--input", required=True); a.add_argument("--output", required=True); a.set_defaults(func=cmd_apply)
    c = sub.add_parser("commits"); c.add_argument("--repo", required=True); c.add_argument("--manifest", required=True)
    c.add_argument("--codelists-from", help="the old edition's code lists (copied to codelists/<edition_from>/ when codes are carried)")
    c.set_defaults(func=cmd_commits)
    v = sub.add_parser("verify"); v.add_argument("--manifest", required=True); v.add_argument("--base"); v.add_argument("--current"); v.add_argument("--new"); v.add_argument("--materials-dir"); v.add_argument("--crosswalk"); v.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
