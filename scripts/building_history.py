#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Derive the history of one building from a city repository — independent of
commit granularity.

Semantic Operation records community proposals as one commit per building;
official editions arrive as whole-file baselines accepted by reproduction. Both
must be traceable per building. This tool follows a building's stable ID — the
city's own rule in 4dcitygml.json (``building_id``: a code-list attribute, the
``gml:id`` or a generic attribute; scripts/building_identity.py) — and every
earlier ID it had, via identity commits, through the git history of any CityGML
dialect (1.0, 2.0, 3.0). For every commit that touched the building it reports
what changed — in the semantic registry's edition-independent keys where the
data has a registered edition, else in the attribute paths as written — which kind of
operation it was (proposal / identity / source-baseline / scope-extract /
source-update / carry-forward / layout), and — for manifest-backed commits —
the manifest's own classification of that building.

Usage:
    python3 scripts/building_history.py --repo <clone> --id <stable building ID> [--json] [--rev main]
    python3 scripts/building_history.py --repo <clone> --index-out site/history   # static index for every building (Pages)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import analyze_yearly_citygml_mesh as A  # noqa: E402
from scripts import semantic_registry as R  # noqa: E402
from scripts.building_identity import (IdentityRule, building_spans, citymodel_close, member_markers,  # noqa: E402
                                       rule_from_config, stable_id, trailers as parse_trailers)
from scripts.provenance_manifest import parse_manifest_ref  # noqa: E402
from scripts.repo_git import blob as _blob, git as _git  # noqa: E402

BULK_KINDS = {"source-baseline", "scope-extract", "layout", "carry-forward", "schema-update", "schema-migration", "lifecycle"}


def aliases_of(repo: Path, rev: str, stable: str) -> list[str]:
    """Every stable ID the building had, following Building-ID-From/To trailers in both directions."""
    log = _git(repo, "log", rev, "--format=%H%x00%B%x01", "--grep=^Building-ID-From: ", "--grep=^Building-ID-To: ", "--all-match")
    pairs = []
    for entry in log.split("\x01"):
        if "\x00" not in entry:
            continue
        _sha, body = entry.lstrip("\n").split("\x00", 1)
        t = parse_trailers(body)
        for f, to in zip(t.get("Building-ID-From", []), t.get("Building-ID-To", [])):
            pairs.append((f, to))
    ids = {stable}
    changed = True
    while changed:
        changed = False
        for f, to in pairs:
            if (f in ids) != (to in ids):
                ids |= {f, to}
                changed = True
    return sorted(ids)


def city_rule(repo: Path, rev: str) -> IdentityRule:
    """The building ID rule of the city's 4dcitygml.json at rev (the default rule when absent)."""
    raw = _blob(repo, rev, "4dcitygml.json")
    try:
        return rule_from_config(json.loads(raw.decode("utf-8")) if raw else None)
    except ValueError:
        return IdentityRule()


def _members(raw: bytes, rule: IdentityRule) -> dict[str, bytes]:
    """stable ID -> the bytes of its cityObjectMember, in the file's own dialect."""
    return {stable_id(raw[s:e], gml_id, rule): raw[s:e] for gml_id, (s, e) in building_spans(raw).items()}


def _frame(raw: bytes) -> tuple[bytes, bytes]:
    """What surrounds the members: the file up to its first member, and its own closing tag."""
    start = raw.find(member_markers(raw)[0])
    close = citymodel_close(raw)
    return (raw[:start] if start > 0 else raw[:4096]), (raw[close:] if close >= 0 else b"</core:CityModel>")


def _key(path: str, edition: str | None) -> str:
    """The registry key of an attribute path when the data has a registered edition; otherwise
    the path as written (generic attributes keep their names)."""
    if not edition:
        return path
    return R.key_for(path, edition) or R.normalize_path(path)


def _values(member: bytes, edition: str | None, frame: tuple[bytes, bytes]) -> dict[str, str]:
    """Keyed values of one building member (coded values raw, numbers normalized)."""
    from lxml import etree
    from scripts.safe_xml import safe_fromstring
    try:
        root = safe_fromstring(frame[0] + member + frame[1])
    except etree.XMLSyntaxError:
        return {}
    building = next(root.iter(*A.BUILDING_TAGS), None)
    if building is None:
        return {}
    out = {_key(path, edition): value for path, value in sorted(A.extract_attributes(building).items())}
    out["geometry.lod0_fingerprint"] = (A.geometry_fingerprint(A.lod0_geometry(building)) or "-")[:12]
    return out


def _manifest_entry(repo: Path, sha: str, trailers: dict, ids: set[str]) -> dict | None:
    refs = trailers.get("Provenance-Manifest", [])
    if not refs or parse_manifest_ref(refs[0]) is None:
        return None
    path, _digest = parse_manifest_ref(refs[0])
    blob = _blob(repo, sha, path)
    if blob is None:
        return None
    try:
        m = json.loads(blob.decode("utf-8"))
    except ValueError:
        return None
    ev = m.get("evidence", {})
    entry = {"kind": m.get("kind"), "manifest": path}
    for section in ("links", "changes", "absorbed", "conflicts", "unmappable", "insert_needed", "carried_old_codespace", "unlinked"):
        hits = [e for e in ev.get(section, []) if (e.get("id") in ids or e.get("from") in ids or e.get("to") in ids)]
        if hits:
            entry[section] = hits
    return entry


def history(repo: Path, stable: str, rev: str = "HEAD") -> list[dict]:
    ids = set(aliases_of(repo, rev, stable))
    rule = city_rule(repo, rev)
    files = [p for p in _git(repo, "ls-tree", "-r", "--name-only", rev).splitlines() if p.endswith(".gml")]
    log = _git(repo, "log", rev, "--reverse", "--format=%H%x00%ct%x00%s%x00%B%x01", "--", *files)
    rows: list[dict] = []
    prev_values: dict[str, str] | None = None
    prev_id: str | None = None
    for entry in log.split("\x01"):
        if entry.count("\x00") < 3:
            continue
        sha, ts, subject, body = entry.lstrip("\n").split("\x00", 3)
        sha, ts = sha.strip(), ts.strip()
        trailers = parse_trailers(body)
        kind = (trailers.get("Change-Type") or ["proposal"])[-1]
        mentioned = ids & set(trailers.get("Building", []) + trailers.get("Building-Added", []) + trailers.get("Building-Deleted", [])
                              + trailers.get("Building-ID-From", []) + trailers.get("Building-ID-To", []))
        has_building_trailers = any(trailers.get(k) for k in ("Building", "Building-Added", "Building-Deleted", "Building-ID-From", "Building-ID-To"))
        if has_building_trailers and not mentioned and kind not in BULK_KINDS:
            continue  # a per-building commit about another building: no need to read the blobs
        parents = _git(repo, "show", "-s", "--format=%P", sha).split()
        changed_files = _git(repo, "diff", "--name-only", "--no-renames", parents[0] if parents else "4b825dc642cb6eb9a060e54bf8d69288fbee4904", sha, "--", "*.gml").splitlines()
        if not changed_files:
            continue
        member = current_id = None
        for f in changed_files:
            raw = _blob(repo, sha, f)
            if raw is None:
                continue
            current_id, member = next(((k, m) for k, m in _members(raw, rule).items() if k in ids), (None, None))
            if member is not None:
                edition, frame = R.detect_edition(raw), _frame(raw)
                break
        if member is None:
            # deleted in this commit?
            if prev_values is not None and any(_blob(repo, sha, f) is None or not ids & set(_members(_blob(repo, sha, f), rule)) for f in changed_files):
                if kind != "proposal" or mentioned:
                    rows.append({"commit": sha[:12], "time": int(ts), "subject": subject, "kind": kind, "event": "removed", "id": prev_id})
                    prev_values = None
            continue
        values = _values(member, edition, frame)
        diff = {k: (prev_values.get(k) if prev_values else None, v) for k, v in values.items() if prev_values is None or prev_values.get(k) != v}
        if prev_values is not None:
            diff.update({k: (prev_values[k], None) for k in prev_values if k not in values})
        if prev_values is not None and not diff and current_id == prev_id and not mentioned and kind not in ("source-baseline", "scope-extract", "layout"):
            continue  # the file changed but not this building
        if prev_values is not None and not diff and current_id == prev_id and kind in ("source-baseline", "scope-extract", "layout") :
            rows.append({"commit": sha[:12], "time": int(ts), "subject": subject, "kind": kind, "event": "carried unchanged", "id": current_id, "edition": edition})
            prev_id = current_id
            continue
        row = {"commit": sha[:12], "time": int(ts), "subject": subject, "kind": kind, "edition": edition, "id": current_id,
               "event": "first appearance" if prev_values is None else ("id changed" if current_id != prev_id else "changed"),
               "changes": {k: {"old": o, "new": n} for k, (o, n) in diff.items()}}
        if current_id != prev_id and prev_id is not None:
            row["id_from"] = prev_id
        manifest = _manifest_entry(repo, sha, trailers, ids)
        if manifest:
            row["manifest"] = manifest
        rows.append(row)
        prev_values, prev_id = values, current_id
    return rows


def _file_state(raw: bytes, rule: IdentityRule) -> tuple[dict[str, dict[str, str]], str | None]:
    """stable ID -> keyed values for every building of a file (one parse)."""
    edition = R.detect_edition(raw)
    stable_of = {gml_id: stable_id(raw[s:e], gml_id, rule) for gml_id, (s, e) in building_spans(raw).items()}
    state: dict[str, dict[str, str]] = {}
    for gml_id, building in _load_from_bytes(raw).items():
        if gml_id not in stable_of:
            continue
        values = {_key(path, edition): value for path, value in sorted(building.attrs.items())}
        values["geometry.lod0_fingerprint"] = (building.fingerprint_1mm or "-")[:12]
        state[stable_of[gml_id]] = values
    return state, edition


def _load_from_bytes(raw: bytes) -> dict:
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".gml", delete=False) as tf:
        tf.write(raw)
        name = tf.name
    try:
        return A.load_buildings(Path(name))
    finally:
        os.unlink(name)


def build_index(repo: Path, rev: str = "HEAD") -> dict[str, list[dict]]:
    """Every building's timeline in one pass over the history (files parsed once per commit)."""
    rule = city_rule(repo, rev)
    files = [p for p in _git(repo, "ls-tree", "-r", "--name-only", rev).splitlines() if p.endswith(".gml")]
    log = _git(repo, "log", rev, "--reverse", "--format=%H%x00%ct%x00%s%x00%B%x01", "--", *files)
    state: dict[str, dict[str, str]] = {}          # current values per stable ID
    events: dict[str, list[dict]] = {}
    file_states: dict[str, dict[str, dict[str, str]]] = {}   # path -> stable ID -> values (at the last seen commit)
    for entry in log.split("\x01"):
        if entry.count("\x00") < 3:
            continue
        sha, ts, subject, body = entry.lstrip("\n").split("\x00", 3)
        sha, ts = sha.strip(), ts.strip()
        trailers = parse_trailers(body)
        kind = (trailers.get("Change-Type") or ["proposal"])[-1]
        parents = _git(repo, "show", "-s", "--format=%P", sha).split()
        parent = parents[0] if parents else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        changed_files = _git(repo, "diff", "--name-only", "--no-renames", parent, sha, "--", "*.gml").splitlines()
        if not changed_files:
            continue
        id_from = (trailers.get("Building-ID-From") or [None])[0]
        id_to = (trailers.get("Building-ID-To") or [None])[0]
        manifest_cache: dict | None = None
        seen_now: set[str] = set()
        mentioned = set(trailers.get("Building", []) + trailers.get("Building-Added", []) + trailers.get("Building-Deleted", []))
        if id_from: mentioned.add(id_from)
        if id_to: mentioned.add(id_to)
        per_building = bool(mentioned) and kind not in BULK_KINDS
        for f in changed_files:
            raw = _blob(repo, sha, f)
            if per_building and raw is not None and f in file_states:
                # a one-building commit: parse only the mentioned members, patch the cached file state
                frame = _frame(raw)
                edition = R.detect_edition(raw)
                new_state = dict(file_states[f])
                present: set[str] = set()
                for stable_m, member in _members(raw, rule).items():
                    if stable_m in mentioned:
                        new_state[stable_m] = _values(member, edition, frame)
                        present.add(stable_m)
                for gone in mentioned - present:
                    new_state.pop(gone, None)
                old_state = file_states[f]
                # restrict the comparison below to the mentioned buildings
                old_state = {k: v for k, v in old_state.items() if k in mentioned}
                iter_state = {k: v for k, v in new_state.items() if k in mentioned}
                file_states[f] = new_state
            else:
                new_state, edition = _file_state(raw, rule) if raw is not None else ({}, None)
                old_state = file_states.get(f, {})
                file_states[f] = new_state
                iter_state = new_state
            for stable, values in iter_state.items():
                seen_now.add(stable)
                prev_id = id_from if (id_to == stable and id_from) else stable
                prev = state.get(prev_id)
                diff = {k: (prev.get(k) if prev else None, v) for k, v in values.items() if prev is None or prev.get(k) != v}
                if prev is not None:
                    diff.update({k: (prev[k], None) for k in prev if k not in values})
                if prev is not None and not diff and prev_id == stable:
                    if stable not in old_state and kind in ("layout",):
                        events.setdefault(stable, []).append({"commit": sha[:12], "time": int(ts), "subject": subject, "kind": kind, "event": "moved (layout)", "id": stable, "edition": edition, "file": f})
                    continue
                event = "first appearance" if prev is None else ("id changed" if prev_id != stable else "changed")
                row = {"commit": sha[:12], "time": int(ts), "subject": subject, "kind": kind, "edition": edition, "id": stable, "event": event, "file": f,
                       "changes": {k: {"old": o, "new": n} for k, (o, n) in diff.items()}}
                if prev_id != stable:
                    row["id_from"] = prev_id
                    state.pop(prev_id, None)
                    events.setdefault(stable, []).extend(events.pop(prev_id, []))
                if trailers.get("Provenance-Manifest"):
                    if manifest_cache is None:
                        manifest_cache = _manifest_blob(repo, sha, trailers) or {}
                    entry_m = _manifest_slice(manifest_cache, {stable, prev_id})
                    if entry_m:
                        row["manifest"] = entry_m
                state[stable] = values
                events.setdefault(stable, []).append(row)
            for stable in set(old_state) - set(iter_state):
                if stable in seen_now or (id_from == stable and id_to):
                    continue  # moved to another file, or renamed (handled above)
                events.setdefault(stable, []).append({"commit": sha[:12], "time": int(ts), "subject": subject, "kind": kind, "event": "removed", "id": stable, "file": f})
                state.pop(stable, None)
    return events


def _manifest_blob(repo: Path, sha: str, trailers: dict) -> dict | None:
    ref = parse_manifest_ref(trailers["Provenance-Manifest"][0])
    if ref is None:
        return None
    blob = _blob(repo, sha, ref[0])
    if blob is None:
        return None
    try:
        m = json.loads(blob.decode("utf-8"))
    except ValueError:
        return None
    m["__path"] = ref[0]
    return m


def _manifest_slice(m: dict, ids: set[str]) -> dict | None:
    if not m:
        return None
    ev = m.get("evidence", {})
    entry = {"kind": m.get("kind"), "manifest": m.get("__path")}
    hit = False
    for section in ("links", "changes", "absorbed", "conflicts", "unmappable", "insert_needed", "carried_old_codespace", "unlinked"):
        rows = [e for e in ev.get(section, []) if (e.get("id") in ids or e.get("from") in ids or e.get("to") in ids)]
        if rows:
            entry[section] = rows
            hit = True
    return entry if hit else None


_INDEX_HTML = """<!doctype html>
<meta charset="utf-8"><title>Building history</title>
<style>body{font:14px/1.5 system-ui,sans-serif;margin:1.5rem;max-width:60rem}input{font:inherit;padding:.3rem .5rem;width:22rem}
.ev{border-left:3px solid #999;padding:.2rem .8rem;margin:.6rem 0}.k{font-weight:600}.chg{font-family:ui-monospace,monospace;font-size:12px;color:#333}
.kind-proposal{border-color:#2a6}.kind-identity-baseline,.kind-identity-correction{border-color:#c80}.kind-source-baseline,.kind-scope-extract{border-color:#57a}
.kind-source-update,.kind-carry-forward{border-color:#a5c}small{color:#666}</style>
<h1>Building history</h1>
<p><input id="q" placeholder="Building ID"> <button id="go">Show</button> <small id="meta"></small></p>
<div id="out"></div>
<script>
const out=document.getElementById('out'),meta=document.getElementById('meta');
fetch('index.json').then(r=>r.json()).then(i=>{meta.textContent=`${i.buildings} buildings · ${i.events} events · generated ${i.generated_at} · ${i.rev}`;});
async function show(id){out.innerHTML='';if(!id)return;const r=await fetch('buildings/'+encodeURIComponent(id)+'.json');
if(!r.ok){out.textContent='No history for '+id;return;}const h=await r.json();
const head=document.createElement('h2');head.textContent=h.id+(h.aliases&&h.aliases.length>1?'  (formerly '+h.aliases.filter(a=>a!==h.id).join(', ')+')':'');out.appendChild(head);
for(const e of h.events){const d=document.createElement('div');d.className='ev kind-'+e.kind;
const when=new Date(e.time*1000).toISOString().slice(0,10);
d.innerHTML=`<div><span class="k">${e.kind}</span> — ${e.event}${e.id_from?' ('+e.id_from+' → '+e.id+')':''} <small>${when} · ${e.commit}${e.edition?' · '+e.edition:''}</small></div><div>${e.subject}</div>`;
const ch=e.changes||{};const keys=Object.keys(ch);if(keys.length){const ul=document.createElement('div');ul.className='chg';
ul.textContent=keys.slice(0,20).map(k=>`${k}: ${ch[k].old??'∅'} → ${ch[k].new??'∅'}`).join('\n')+(keys.length>20?`\n… ${keys.length-20} more`:'');ul.style.whiteSpace='pre';d.appendChild(ul);}
if(e.manifest){const m=document.createElement('div');m.className='chg';m.textContent='manifest '+e.manifest.manifest+' ('+e.manifest.kind+'): '+Object.entries(e.manifest).filter(([k,v])=>Array.isArray(v)).map(([k,v])=>k+'='+v.length).join(', ');d.appendChild(m);}
out.appendChild(d);}}
document.getElementById('go').onclick=()=>show(document.getElementById('q').value.trim());
document.getElementById('q').onkeydown=e=>{if(e.key==='Enter')show(e.target.value.trim());};
if(location.hash.length>1)show(decodeURIComponent(location.hash.slice(1)));
</script>
"""


def write_index(repo: Path, out: Path, rev: str = "HEAD") -> dict:
    import datetime as dt
    events = build_index(repo, rev)
    (out / "buildings").mkdir(parents=True, exist_ok=True)
    total = 0
    for stable, rows in events.items():
        rows.sort(key=lambda r: r["time"])
        aliases = sorted({r.get("id_from") for r in rows if r.get("id_from")} | {stable})
        (out / "buildings" / f"{stable}.json").write_text(json.dumps({"id": stable, "aliases": aliases, "events": rows}, ensure_ascii=False), encoding="utf-8")
        total += len(rows)
    summary = {"buildings": len(events), "events": total, "rev": _git(repo, "rev-parse", "--short", rev).strip(),
               "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
    (out / "index.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    (out / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    return summary


def render(rows: list[dict], stable: str) -> str:
    import datetime as dt
    lines = [f"# History of building {stable}", ""]
    for r in rows:
        when = dt.datetime.fromtimestamp(r["time"], dt.timezone.utc).strftime("%Y-%m-%d")
        head = f"- {when} `{r['commit']}` **{r['kind']}** — {r['event']}"
        if r.get("id_from"):
            head += f" ({r['id_from']} → {r['id']})"
        if r.get("edition"):
            head += f" [{r['edition']}]"
        lines.append(head + f": {r['subject']}")
        for k, c in list(r.get("changes", {}).items())[:12]:
            lines.append(f"    - {k}: {c['old']!r} → {c['new']!r}")
        if len(r.get("changes", {})) > 12:
            lines.append(f"    - … {len(r['changes']) - 12} more")
        if r.get("manifest"):
            m = r["manifest"]
            sections = {k: len(v) for k, v in m.items() if isinstance(v, list)}
            lines.append(f"    - manifest {m['manifest']} ({m['kind']}): " + ", ".join(f"{k}={v}" for k, v in sections.items()))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--id", help="the building's stable ID under the city's building_id rule (current or any earlier value)")
    parser.add_argument("--rev", default="HEAD")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--index-out", type=Path, help="build the static history index (index.html, index.json, buildings/<id>.json) for ALL buildings into this directory")
    args = parser.parse_args(argv)
    if args.index_out:
        summary = write_index(args.repo, args.index_out, args.rev)
        print(json.dumps({"out": str(args.index_out), **summary}))
        return 0
    rows = history(args.repo, args.id, args.rev)
    if args.json:
        print(json.dumps({"id": args.id, "aliases": aliases_of(args.repo, args.rev, args.id), "events": rows}, ensure_ascii=False, indent=1))
    else:
        sys.stdout.write(render(rows, args.id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
