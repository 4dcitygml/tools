#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""identity-baseline: unify historical ``uro:buildingID`` values to the current
edition — manifest generation, application, per-building commits, and the
reproduction check used by CI.

Principle (docs/bulk-submission-provenance.md): an identical buildingID is
never sufficient evidence on its own. Every link carries geometric evidence
and each edition boundary is classified by its *ID regime*:

- ``continuous``  — nearly every shared ID is the same building (share ≥ 95 %)
- ``mixed``       — most are, some are not
- ``renumbered``  — shared IDs are coincidences (share < 50 %); the boundary is
                    matched by geometry only and same-ID gets no credit at all

Links are composed as a chain across boundaries; a chain that meets a weak or
missing link (tier C/D) is cut and the original ID is kept (unlinked, with the
reason recorded).

Subcommands:
    generate  build provenance/<kind>/<mesh>-<from>-<to>.json from edition GML files
    apply     rewrite a baseline GML with the manifest's links (byte-preserving)
    commits   create one git commit per link in a city repository
    verify    regenerate from the same editions and compare (CI reproduction gate)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import random
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import analyze_yearly_citygml_mesh as A  # noqa: E402
from scripts.provenance_manifest import canonical_bytes, manifest_ref, sha256_hex, validate  # noqa: E402

BUILDING_ID_RE_TEMPLATE = rb"<(?:\w+:)?buildingID(?:\s[^>]*)?>%s</(?:\w+:)?buildingID>"
_INDEX_RE = re.compile(r"\[\d+\]$")


@dataclass
class Thresholds:
    consistent_iou: float = 0.5          # same-ID pair counts as "same building" above this ...
    consistent_centroid_m: float = 2.0   # ... and within this centroid distance
    regime_continuous_min_share: float = 0.95
    regime_renumbered_max_share: float = 0.5
    minimum_iou: float = 0.5             # geometry matching (analyze_yearly)
    search_padding_m: float = 2.0
    tier_a_iou: float = 0.9
    tier_a_centroid_m: float = 1.0
    tier_b_area_ratio_min: float = 0.67
    tier_b_area_ratio_max: float = 1.5
    tier_b_competitor_iou_max: float = 0.2
    id_change_tier_b_min_iou: float = 0.9  # different IDs in a non-renumbered regime need this much overlap for tier B


def building_id_of(building: A.Building) -> str | None:
    for key, value in building.attrs.items():
        last = _INDEX_RE.sub("", key.split("/")[-1])
        if last == "buildingID" and value:
            return value.strip()
    return None


def load_edition(path: Path, municipality: str) -> dict[str, A.Building]:
    """Buildings of one edition keyed by uro:buildingID (municipality-filtered)."""
    result: dict[str, A.Building] = {}
    duplicates: set[str] = set()
    for building in A.load_buildings(path).values():
        if municipality and building.city_code != municipality:
            continue
        stable = building_id_of(building)
        if not stable:
            continue
        if stable in result:
            duplicates.add(stable)
        result[stable] = building
    if duplicates:
        raise SystemExit(f"{path}: duplicated uro:buildingID within one edition: {sorted(duplicates)[:5]}")
    return result


def _metrics(old: A.Building, new: A.Building) -> tuple[float, float, float, float]:
    iou, hausdorff, centroid = A.geometry_metrics(old, new)
    area_ratio = (new.geom.area / old.geom.area) if (old.geom is not None and new.geom is not None and old.geom.area) else 0.0
    return (iou or 0.0), (hausdorff or 0.0), (centroid or 0.0), area_ratio


class _Competitors:
    """Spatial index over one edition's footprints: best IoU of a geometry with
    any *other* building (the strongest competing candidate)."""

    def __init__(self, pool: dict[str, A.Building]):
        from shapely.strtree import STRtree
        self.ids = [stable for stable, b in pool.items() if b.geom is not None]
        self.geoms = [pool[stable].geom for stable in self.ids]
        self.tree = STRtree(self.geoms) if self.geoms else None

    def best(self, new: A.Building, exclude: str) -> float:
        if new.geom is None or self.tree is None:
            return 0.0
        best = 0.0
        for index in self.tree.query(new.geom):
            stable = self.ids[int(index)]
            if stable == exclude:
                continue
            old_geom = self.geoms[int(index)]
            inter = old_geom.intersection(new.geom).area
            if inter <= 0:
                continue
            union = old_geom.union(new.geom).area
            if union:
                best = max(best, inter / union)
        return best


def _competitor_iou(new: A.Building, old_pool: dict[str, A.Building], exclude: str, index: "_Competitors | None" = None) -> float:
    return (index or _Competitors(old_pool)).best(new, exclude)


def _consistent(iou: float, centroid: float, thr: Thresholds) -> bool:
    return iou >= thr.consistent_iou and centroid <= thr.consistent_centroid_m


def boundary_links(old: dict[str, A.Building], new: dict[str, A.Building],
                   label_old: str, label_new: str, thr: Thresholds) -> tuple[dict, list[dict], list[dict]]:
    """Classify one edition boundary. Returns (stats, links, unlinked)."""
    shared = sorted(set(old) & set(new))
    consistent_pairs: list[str] = []
    inconsistent_pairs: list[str] = []
    for stable in shared:
        iou, _h, centroid, _a = _metrics(old[stable], new[stable])
        (consistent_pairs if _consistent(iou, centroid, thr) else inconsistent_pairs).append(stable)
    share = (len(consistent_pairs) / len(shared)) if shared else 0.0
    if not shared:
        # no ID survived the boundary at all: nothing to give same-ID credit to
        regime = "renumbered" if (old and new) else "continuous"
    elif share >= thr.regime_continuous_min_share:
        regime = "continuous"
    elif share < thr.regime_renumbered_max_share:
        regime = "renumbered"
    else:
        regime = "mixed"

    links: list[dict] = []
    unlinked: list[dict] = []
    pool_old = dict(old)
    pool_new = dict(new)
    competitors = _Competitors(old)
    if regime != "renumbered":
        for stable in consistent_pairs:
            iou, hausdorff, centroid, area_ratio = _metrics(old[stable], new[stable])
            competitor = competitors.best(new[stable], stable)
            tier = "A" if (iou >= thr.tier_a_iou and centroid <= thr.tier_a_centroid_m) else "B"
            if tier == "B" and not (thr.tier_b_area_ratio_min <= area_ratio <= thr.tier_b_area_ratio_max
                                    and competitor < thr.tier_b_competitor_iou_max):
                tier = "C"
            links.append(_link(stable, stable, tier, "same_id", iou, centroid, hausdorff, area_ratio, competitor, [label_old, label_new]))
            pool_old.pop(stable, None)
            pool_new.pop(stable, None)

    # Geometry-only matching for the rest. Keys are prefixed so analyze_yearly's
    # same-key step can never fire on a coincidental ID.
    keyed_old = {f"o:{k}": v for k, v in pool_old.items()}
    keyed_new = {f"n:{k}": v for k, v in pool_new.items()}
    cmp = A.compare(keyed_old, keyed_new, thr.minimum_iou, thr.search_padding_m)
    split_old = {i for d in cmp["split_candidate_details"] for i in [d["old_id"]]}
    merge_old = {i for d in cmp["merge_candidate_details"] for i in d["old_ids"]}
    matched_old: set[str] = set()
    matched_new: set[str] = set()
    for m in cmp["matches"]:
        stable_old, stable_new = m["old_id"][2:], m["new_id"][2:]
        o, n = pool_old[stable_old], pool_new[stable_new]
        iou, hausdorff, centroid, area_ratio = _metrics(o, n)
        competitor = competitors.best(n, stable_old)
        method = m["method"]
        if method == "lod0_fingerprint_1mm" or (iou >= thr.tier_a_iou and centroid <= thr.tier_a_centroid_m):
            tier = "A"
        elif (_consistent(iou, centroid, thr) and thr.tier_b_area_ratio_min <= area_ratio <= thr.tier_b_area_ratio_max
              and competitor < thr.tier_b_competitor_iou_max):
            tier = "B"
        else:
            tier = "C"
        if m["old_id"] in split_old or m["old_id"] in merge_old:
            tier = "C"
        if regime != "renumbered" and stable_old != stable_new and iou < thr.id_change_tier_b_min_iou:
            tier = "C"  # a different ID in a regime that keeps IDs = rebuild candidate
        links.append(_link(stable_old, stable_new, tier, method, iou, centroid, hausdorff, area_ratio, competitor, [label_old, label_new]))
        matched_old.add(stable_old)
        matched_new.add(stable_new)

    for stable in sorted(pool_old):
        if stable in matched_old:
            continue
        if stable in inconsistent_pairs:
            iou, _h, centroid, _a = _metrics(old[stable], new[stable])
            unlinked.append({"id": stable, "tier": "C",
                             "reason": f"{label_old}->{label_new}: same buildingID but IoU {iou:.3f} / centroid {centroid:.1f} m ({regime} regime); no geometric match"})
        elif f"o:{stable}" in split_old or f"o:{stable}" in merge_old:
            unlinked.append({"id": stable, "tier": "C", "reason": f"{label_old}->{label_new}: split/merge candidate"})
        else:
            unlinked.append({"id": stable, "tier": "D", "reason": f"{label_old}->{label_new}: no geometric match"})

    stats = {
        "from": label_old, "to": label_new, "id_regime": regime,
        "shared_ids": len(shared), "shared_consistent": len(consistent_pairs),
        "geometry_matches": len(links), "id_persisted": sum(1 for l in links if l["from"] == l["to"]),
        "unmatched_old": len(pool_old) - len(matched_old), "unmatched_new": len(pool_new) - len(matched_new),
    }
    return stats, links, unlinked


def _link(from_id, to_id, tier, method, iou, centroid, hausdorff, area_ratio, competitor, chain) -> dict:
    return {"from": from_id, "to": to_id, "tier": tier, "method": method,
            "iou": round(iou, 4), "centroid_m": round(centroid, 2), "hausdorff_m": round(hausdorff, 2),
            "area_ratio": round(area_ratio, 3), "competitor_iou": round(competitor, 4), "chain": list(chain)}


_TIER_ORDER = {"A": 0, "B": 1, "C": 2}


def compose_chain(per_boundary: list[tuple[dict, list[dict], list[dict]]], labels: list[str]) -> tuple[list[dict], list[dict], int]:
    """Follow A/B links from the first edition to the last; cut at C/D."""
    step_maps = [{l["from"]: l for l in links} for _s, links, _u in per_boundary]
    first_ids = sorted(step_maps[0].keys() | {u["id"] for u in per_boundary[0][2]})
    links: list[dict] = []
    unlinked: list[dict] = []
    unchanged = 0
    for start in first_ids:
        current = start
        weakest: dict | None = None
        worst_tier = "A"
        cut: tuple[str, str] | None = None  # (tier, reason)
        for index, step in enumerate(step_maps):
            link = step.get(current)
            if link is None:
                entry = next((u for u in per_boundary[index][2] if u["id"] == current), None)
                cut = (entry["tier"], entry["reason"]) if entry else ("D", f"{labels[index]}->{labels[index + 1]}: no link")
                break
            if link["tier"] == "C":
                cut = ("C", f"{labels[index]}->{labels[index + 1]}: tier C ({link['method']}, IoU {link['iou']}, "
                            f"centroid {link['centroid_m']} m, {link['from']} -> {link['to']}) — needs human review")
                break
            if weakest is None or link["iou"] < weakest["iou"]:
                weakest = link
            if _TIER_ORDER[link["tier"]] > _TIER_ORDER[worst_tier]:
                worst_tier = link["tier"]
            current = link["to"]
        if cut is not None:
            unlinked.append({"id": start, "tier": cut[0], "reason": cut[1]})
            continue
        if current == start:
            unchanged += 1
            continue
        assert weakest is not None
        links.append({**weakest, "from": start, "to": current, "tier": worst_tier, "chain": list(labels)})
    return links, unlinked, unchanged


_ANY_BUILDING_ID_RE = re.compile(rb"(<(?:\w+:)?buildingID(?:\s[^>]*)?>)([^<]+)(</(?:\w+:)?buildingID>)")


def apply_links(raw: bytes, links: list[dict]) -> bytes:
    """Simultaneous, byte-preserving substitution of every link's buildingID.

    Each From must occur exactly once in the input; the result must not contain
    duplicated IDs (a To that equals a From of another link is fine — both are
    replaced in the same pass — but a To colliding with an untouched building is
    an error)."""
    mapping = {l["from"].encode(): l["to"].encode() for l in links}
    seen: dict[bytes, int] = {}
    for m in _ANY_BUILDING_ID_RE.finditer(raw):
        seen[m.group(2)] = seen.get(m.group(2), 0) + 1
    for src in mapping:
        if seen.get(src, 0) != 1:
            raise SystemExit(f"buildingID {src.decode()} occurs {seen.get(src, 0)} times (expected exactly once)")
    out = _ANY_BUILDING_ID_RE.sub(lambda m: m.group(1) + mapping.get(m.group(2), m.group(2)) + m.group(3), raw)
    final: dict[bytes, int] = {}
    for m in _ANY_BUILDING_ID_RE.finditer(out):
        final[m.group(2)] = final.get(m.group(2), 0) + 1
    dup = sorted(k.decode() for k, n in final.items() if n > 1)
    if dup:
        raise SystemExit(f"applying the links would duplicate buildingID(s): {dup[:5]}")
    return out


def order_links(links: list[dict]) -> tuple[list[dict], list[dict]]:
    """Order for one-commit-per-link application: a link may only be applied
    once its To is free, i.e. after the link that consumes that ID as a From.
    Returns (ordered, cyclic) — cyclic links (mutual ID reuse) cannot be applied
    one at a time and are excluded (they need a human decision)."""
    remaining = {l["from"]: l for l in links}
    ordered: list[dict] = []
    while remaining:
        ready = sorted(f for f, l in remaining.items() if l["to"] not in remaining or l["to"] == f)
        if not ready:
            break
        for f in ready:
            ordered.append(remaining.pop(f))
    return ordered, sorted(remaining.values(), key=lambda l: l["from"])


def _file_material(label: str, path: Path, uri: str | None) -> dict:
    """Material entry for one edition file. ``uri`` may name the official ZIP
    with the member path after '#' (``https://.../x.zip#pkg/udx/bldg/m.gml``):
    then the archive is the material and the file is its member, so CI can
    re-fetch exactly that member by HTTP Range (fetch_materials.py)."""
    data = path.read_bytes()
    digest, size = sha256_hex(data), len(data)
    acquired = _dt.date.today().isoformat()
    if uri and "#" in uri and not uri.startswith("file:"):
        archive, member = uri.split("#", 1)
        entry = {"name": label, "uri": archive, "sha256": digest, "bytes": size, "acquired": acquired,
                 "members": [{"path": member, "sha256": digest, "bytes": size}]}
        # archive-level digest/size are unknown offline; the member digest is what CI verifies
        entry["sha256"] = digest
        return entry
    return {"name": label, "uri": uri or path.resolve().as_uri(), "sha256": digest, "bytes": size, "acquired": acquired}


def build_manifest(args: argparse.Namespace, thr: Thresholds) -> tuple[dict, bytes]:
    labels = [label for label, _p in args.edition]
    editions = [load_edition(Path(p), args.municipality) for _l, p in args.edition]
    per_boundary = [boundary_links(editions[i], editions[i + 1], labels[i], labels[i + 1], thr) for i in range(len(editions) - 1)]
    links, unlinked, unchanged = compose_chain(per_boundary, labels)
    # A To must not collide with an ID that stays in the file: a past-only
    # building keeps its original ID (design §3.3), and in a renumbered ID space
    # that number may belong to a different building in the current edition.
    # Such links are deferred to a human (tier C) and re-attempted after the
    # retained building has been removed by the annual source update.
    # Deferring a link keeps its From in the file, which can in turn block another
    # link's To: iterate to a fixed point so the applied set is collision-free.
    while True:
        retained = set(editions[0]) - {l["from"] for l in links}
        colliding = [l for l in links if l["to"] in retained]
        if not colliding:
            break
        links = [l for l in links if l["to"] not in retained]
        for link in colliding:
            unlinked.append({"id": link["from"], "tier": "C",
                             "reason": (f"target buildingID {link['to']} is still held by a retained {labels[0]} building; "
                                        f"apply after that building is removed or relinked (IoU {link['iou']}, {link['method']})")})
    ordered, cyclic = order_links(links)
    for link in cyclic:
        unlinked.append({"id": link["from"], "tier": "C",
                         "reason": f"cyclic buildingID reuse: {link['from']} -> {link['to']} cannot be applied one commit at a time; needs a human decision"})
    links = ordered
    unlinked.sort(key=lambda u: u["id"])
    product_source = Path(args.product_source or args.edition[0][1])
    product_bytes = apply_links(product_source.read_bytes(), links)
    uris = dict(args.edition_uri or [])
    rng = random.Random(args.seed)
    link_targets = sorted(l["to"] for l in links)
    sample = sorted(rng.sample(link_targets, min(args.sample_size, len(link_targets)))) if link_targets else []
    manifest = {
        "schemaVersion": 1,
        "kind": args.kind,
        "repository": args.repository,
        "scope": {"mesh": args.mesh, "municipality": args.municipality, "edition_from": labels[0], "edition_to": labels[-1]},
        "plan_issue": args.plan_issue,
        "materials": [_file_material(label, Path(p), uris.get(label)) for label, p in args.edition],
        "builder": {"tools_repo": args.tools_repo, "tools_commit": args.tools_commit, "script": "scripts/identity_manifest.py"},
        "invocation": {
            "command": ["python3", "scripts/identity_manifest.py", "generate", "--mesh", args.mesh, "--municipality", args.municipality]
                       + [x for label, p in args.edition for x in ("--edition", f"{label}={Path(p).name}")]
                       + ["--product", args.product, "--seed", str(args.seed), "--sample-size", str(args.sample_size)],
            "parameters": asdict(thr),
            "environment": _environment(),
        },
        "products": [{"path": args.product, "sha256": sha256_hex(product_bytes), "buildings": len(editions[0])}],
        "evidence": {
            "boundaries": [s for s, _l, _u in per_boundary],
            "thresholds": asdict(thr),
            "links": links,
            "unlinked": unlinked,
            "unchanged": unchanged,
            "per_boundary_links": {f"{s['from']}->{s['to']}": {"A": sum(l['tier'] == 'A' for l in ls), "B": sum(l['tier'] == 'B' for l in ls), "C": sum(l['tier'] == 'C' for l in ls), "unlinked": len(u)} for s, ls, u in per_boundary},
        },
        "sample_audit": {"seed": args.seed, "size": args.sample_size, "ids": sample, "reviewed_by": "", "result": "pending", "notes": ""},
        "generated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    return manifest, product_bytes


def _environment() -> dict:
    env = {"python": sys.version.split()[0]}
    for module in ("lxml", "shapely"):
        try:
            env[module] = __import__(module).__version__
        except Exception:  # pragma: no cover
            pass
    return env


def _reproducible_view(manifest: dict) -> dict:
    """The parts of a manifest that a re-run must reproduce exactly."""
    ev = dict(manifest["evidence"])
    return {"kind": manifest["kind"], "scope": manifest["scope"], "products": manifest["products"],
            "evidence": ev, "materials": [{k: m[k] for k in ("name", "sha256", "bytes")} for m in manifest["materials"]]}


def commit_message(link: dict, manifest_path: str, manifest_bytes: bytes, kind: str, edition_to: str) -> str:
    evidence = (f"tier={link['tier']};method={link['method']};iou={link['iou']};centroid_m={link['centroid_m']};"
                f"hausdorff_m={link['hausdorff_m']};area_ratio={link['area_ratio']};chain={'>'.join(link['chain'])}")
    return (f"Unify buildingID {link['from']} -> {link['to']} ({edition_to} edition)\n\n"
            f"Change-Type: {kind}\n"
            f"Building-ID-From: {link['from']}\n"
            f"Building-ID-To: {link['to']}\n"
            f"Identity-Evidence: {evidence}\n"
            f"Provenance-Manifest: {manifest_ref(manifest_path, manifest_bytes)}\n"
            f"Created-By: identity_manifest.py/{kind}\n")


def cmd_generate(args: argparse.Namespace) -> int:
    thr = Thresholds()
    manifest, product = build_manifest(args, thr)
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
    print(json.dumps({"output": args.output, "links": len(ev["links"]), "unlinked": len(ev["unlinked"]), "unchanged": ev["unchanged"],
                      "boundaries": [(b["from"], b["to"], b["id_regime"]) for b in ev["boundaries"]],
                      "tiers": {t: sum(l["tier"] == t for l in ev["links"]) for t in "AB"}}, ensure_ascii=False))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out = apply_links(Path(args.input).read_bytes(), manifest["evidence"]["links"])
    Path(args.output).write_bytes(out)
    print(f"applied {len(manifest['evidence']['links'])} links -> {args.output} sha256 {sha256_hex(out)}")
    return 0


def cmd_commits(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    manifest_path = Path(args.manifest)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    rel_manifest = manifest_path.resolve().relative_to(repo.resolve()).as_posix()
    product = manifest["products"][0]["path"]
    target = repo / product
    links = manifest["evidence"]["links"]  # manifest order = safe one-at-a-time order (order_links)
    for link in links:
        target.write_bytes(apply_links(target.read_bytes(), [link]))
        subprocess.run(["git", "-C", str(repo), "-c", "core.looseCompression=1", "add", "--", product, rel_manifest], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-F", "-"],
                       input=commit_message(link, rel_manifest, manifest_bytes, manifest["kind"], manifest["scope"]["edition_to"]).encode(),
                       check=True)
    # hundreds of commits each store a full (multi-MB) blob: repack now so the clone stays small before push
    subprocess.run(["git", "-C", str(repo), "gc", "-q"], check=False)
    final = sha256_hex(target.read_bytes())
    if final != manifest["products"][0]["sha256"]:
        print(f"::error::product digest after applying all links {final} != manifest {manifest['products'][0]['sha256']}", file=sys.stderr)
        return 1
    print(f"{len(links)} commits created; product digest matches the manifest")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    committed = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    errors = validate(committed)
    if errors:
        print("::error::manifest schema: " + "; ".join(errors[:5]))
        return 1
    if getattr(args, "materials_dir", None):
        base = Path(args.materials_dir)
        args.edition = []
        for material in committed["materials"]:
            members = material.get("members") or []
            uri = urllib.parse.urlparse(material["uri"])
            if members:
                local = base / members[0]["path"]
            elif uri.scheme == "file":
                local = Path(urllib.request.url2pathname(uri.path))
            else:
                local = base / material["name"]
            args.edition.append((material["name"], str(local)))
    if not args.edition:
        print("::error::verify needs --edition LABEL=PATH (oldest first) or --materials-dir")
        return 1
    thr = Thresholds(**committed["evidence"]["thresholds"])
    args.kind = committed["kind"]; args.repository = committed["repository"]
    args.mesh = committed["scope"]["mesh"]; args.municipality = committed["scope"]["municipality"]
    args.plan_issue = committed["plan_issue"]; args.tools_repo = committed["builder"]["tools_repo"]
    args.tools_commit = committed["builder"]["tools_commit"]; args.product = committed["products"][0]["path"]
    args.seed = committed["sample_audit"]["seed"]; args.sample_size = committed["sample_audit"]["size"]
    args.edition_uri = []
    regenerated, _product = build_manifest(args, thr)
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
        raise argparse.ArgumentTypeError("--edition LABEL=PATH")
    return label, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="build the identity manifest from edition GML files (oldest first)")
    g.add_argument("--kind", default="identity-baseline", choices=["identity-baseline", "identity-correction"])
    g.add_argument("--repository", required=True, help="owner/name of the city repository")
    g.add_argument("--mesh", required=True)
    g.add_argument("--municipality", required=True)
    g.add_argument("--edition", type=_edition, action="append", required=True, help="LABEL=PATH, oldest first, repeat")
    g.add_argument("--edition-uri", type=_edition, action="append", help="LABEL=URI of the official archive member (for CI re-fetch)")
    g.add_argument("--product", required=True, help="repository-relative path of the baseline GML that receives the IDs")
    g.add_argument("--product-source", help="local file holding the current bytes of --product (default: first edition file)")
    g.add_argument("--tools-repo", default="4dcitygml/tools")
    g.add_argument("--tools-commit", required=True, help="immutable commit SHA of the tools used")
    g.add_argument("--plan-issue", required=True)
    g.add_argument("--seed", type=int, default=20260902)
    g.add_argument("--sample-size", type=int, default=30)
    g.add_argument("--output", required=True)
    g.add_argument("--apply-output", help="also write the product GML with all links applied")
    g.set_defaults(func=cmd_generate)

    a = sub.add_parser("apply", help="apply the manifest's links to a GML file")
    a.add_argument("--manifest", required=True); a.add_argument("--input", required=True); a.add_argument("--output", required=True)
    a.set_defaults(func=cmd_apply)

    c = sub.add_parser("commits", help="one commit per link with the contract trailers")
    c.add_argument("--repo", required=True); c.add_argument("--manifest", required=True)
    c.set_defaults(func=cmd_commits)

    v = sub.add_parser("verify", help="regenerate from editions and compare with the committed manifest")
    v.add_argument("--manifest", required=True)
    v.add_argument("--edition", type=_edition, action="append", help="LABEL=PATH, oldest first (or use --materials-dir)")
    v.add_argument("--materials-dir", help="directory filled by fetch_materials.py (editions mapped from the manifest)")
    v.add_argument("--product-source", help="local file with the pre-identity bytes of the product (parent commit)")
    v.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
