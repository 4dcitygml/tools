#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Bulk-submission provenance manifest: loading, structural validation against
schemas/provenance/bulk-manifest.schema.json, the trailer reference format, and
the command skeleton the manifest scripts share (generate / commits / verify).

The validator is a small dependency-free subset of JSON Schema (required,
type, enum, const, pattern, minItems, minimum/maximum, additionalProperties,
$ref into $defs) — enough for the manifest schema, identical in CI and locally.

Trailer reference format (exchange contract A7):
    Provenance-Manifest: provenance/identity-baseline/53394651-2020-2025.json@sha256:<hex>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "provenance" / "bulk-manifest.schema.json"
MANIFEST_REF_RE = re.compile(r"^(?P<path>[^@\s]+)@sha256:(?P<sha>[0-9a-f]{64})$")


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve(schema: dict, node: dict) -> dict:
    if "$ref" in node:
        return schema["$defs"][node["$ref"].split("/")[-1]]
    return node


def validate(manifest: object, schema: dict | None = None) -> list[str]:
    """Return a list of violations (empty when the manifest conforms)."""
    schema = schema or load_schema()

    def check(node: dict, value, path: str) -> list[str]:
        node = _resolve(schema, node)
        errors: list[str] = []
        if "const" in node and value != node["const"]:
            errors.append(f"{path}: expected {node['const']!r}")
        if "enum" in node and value not in node["enum"]:
            errors.append(f"{path}: {value!r} not in {node['enum']}")
        kind = node.get("type")
        if kind == "object":
            if not isinstance(value, dict):
                return [f"{path}: object expected"]
            for key in node.get("required", []):
                if key not in value:
                    errors.append(f"{path}: missing required {key}")
            props = node.get("properties", {})
            for key, sub in value.items():
                if key in props:
                    errors += check(props[key], sub, f"{path}.{key}")
                elif node.get("additionalProperties") is False:
                    errors.append(f"{path}: unexpected property {key}")
                elif isinstance(node.get("additionalProperties"), dict):
                    errors += check(node["additionalProperties"], sub, f"{path}.{key}")
        elif kind == "array":
            if not isinstance(value, list):
                return [f"{path}: array expected"]
            if len(value) < node.get("minItems", 0):
                errors.append(f"{path}: at least {node['minItems']} item(s) required")
            for index, item in enumerate(value):
                errors += check(node["items"], item, f"{path}[{index}]")
        elif kind == "string":
            if not isinstance(value, str):
                return [f"{path}: string expected"]
            if "pattern" in node and not re.search(node["pattern"], value):
                errors.append(f"{path}: {value!r} does not match {node['pattern']}")
        elif kind == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                return [f"{path}: integer expected"]
            if "minimum" in node and value < node["minimum"]:
                errors.append(f"{path}: below minimum {node['minimum']}")
        elif kind == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return [f"{path}: number expected"]
            if "minimum" in node and value < node["minimum"]:
                errors.append(f"{path}: below minimum {node['minimum']}")
            if "maximum" in node and value > node["maximum"]:
                errors.append(f"{path}: above maximum {node['maximum']}")
        return errors

    return check(schema, manifest, "$")


def canonical_bytes(obj: object) -> bytes:
    """Deterministic JSON serialization (sorted keys, no insignificant whitespace)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_ref(path: str, data: bytes) -> str:
    """The value of the Provenance-Manifest trailer for a manifest file's bytes."""
    return f"{path}@sha256:{sha256_hex(data)}"


def parse_manifest_ref(value: str) -> tuple[str, str] | None:
    match = MANIFEST_REF_RE.match(value.strip())
    if match is None:
        return None
    return match.group("path"), match.group("sha")


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The command skeleton shared by identity_manifest.py, source_update_manifest.py
# and carry_forward_manifest.py. Each script keeps what differs: how the manifest
# is built, how one change is applied, and the commit message.
# --------------------------------------------------------------------------
def reproducible_view(manifest: dict) -> dict:
    """The parts of a manifest that a re-run must reproduce exactly
    (generation time, invocation and material URIs are not among them)."""
    return {"kind": manifest["kind"], "scope": manifest["scope"], "products": manifest["products"],
            "evidence": manifest["evidence"],
            "materials": [{k: m[k] for k in ("name", "sha256", "bytes")} for m in manifest["materials"]]}


def changes_by_building(manifest: dict) -> dict[str, list[dict]]:
    """evidence.changes grouped by the building's stable id (the id itself dropped from each change)."""
    out: dict[str, list[dict]] = {}
    for c in manifest["evidence"]["changes"]:
        out.setdefault(c["id"], []).append({k: v for k, v in c.items() if k != "id"})
    return out


def write_generated(manifest: dict, output: str, product: bytes, apply_output: "str | None") -> int:
    """generate: validate, write the manifest (and the product when asked). 2 on schema violations."""
    errors = validate(manifest)
    if errors:
        print("manifest does not conform to the schema:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 2
    data = (json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(data)
    if apply_output:
        Path(apply_output).write_bytes(product)
    return 0


def committed_manifest(repo: Path, manifest_path: Path) -> tuple[dict, bytes, str]:
    """(manifest, its exact bytes, repository-relative path) of a manifest inside the clone."""
    manifest_bytes = manifest_path.read_bytes()
    rel_manifest = manifest_path.resolve().relative_to(repo.resolve()).as_posix()
    return json.loads(manifest_bytes.decode("utf-8")), manifest_bytes, rel_manifest


def commit_series(repo: Path, product: str, rel_manifest: str, steps: Iterable[tuple[bytes, str]],
                  expected_sha256: str, unit: str) -> int:
    """commits: one commit per step (the product bytes after that step, the commit message).

    The product and the manifest are staged together each time. Afterwards the clone is
    repacked and the product digest must equal the manifest's; `unit` names the steps
    ("links", "changes") in that error."""
    target = repo / product
    count = 0
    for raw, message in steps:
        target.write_bytes(raw)
        subprocess.run(["git", "-C", str(repo), "-c", "core.looseCompression=1", "add", "--", product, rel_manifest], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-F", "-"], input=message.encode(), check=True)
        count += 1
    # hundreds of commits each store a full (multi-MB) blob: repack now so the clone stays small before push
    subprocess.run(["git", "-C", str(repo), "gc", "-q"], check=False)
    final = sha256_hex(target.read_bytes())
    if final != expected_sha256:
        print(f"::error::product digest after applying all {unit} {final} != manifest {expected_sha256}", file=sys.stderr)
        return 1
    print(f"{count} commits created; product digest matches the manifest")
    return 0


def schema_errors(committed: dict) -> int:
    """verify, step 1: 1 (and the first violations on stdout) when the committed manifest breaks the schema."""
    errors = validate(committed)
    if errors:
        print("::error::manifest schema: " + "; ".join(errors[:5]))
        return 1
    return 0


def locate_materials(committed: dict, materials_dir: str) -> dict[str, str]:
    """verify, step 2: local path of every material, in manifest order, as fetch_materials.py
    lays them out under materials_dir (archive members by member path, file: URIs as they are)."""
    base = Path(materials_dir)
    located: dict[str, str] = {}
    for material in committed["materials"]:
        uri = urllib.parse.urlparse(material["uri"])
        members = material.get("members") or []
        if members:
            located[material["name"]] = str(base / members[0]["path"])
        elif uri.scheme == "file":
            located[material["name"]] = urllib.request.url2pathname(uri.path)
        else:
            located[material["name"]] = str(base / material["name"])
    return located


def compare_reproduction(committed: dict, regenerated: dict) -> int:
    """verify, last step: 0 when the regenerated manifest reproduces the committed one."""
    a, b = reproducible_view(committed), reproducible_view(regenerated)
    if canonical_bytes(a) != canonical_bytes(b):
        for key in a:
            if canonical_bytes(a[key]) != canonical_bytes(b[key]):
                print(f"::error::reproduction mismatch in '{key}'")
        return 1
    print("reproduction: OK (materials, evidence, and products regenerate identically)")
    return 0


def edition_arg(value: str) -> tuple[str, str]:
    """argparse type for LABEL=PATH."""
    label, _sep, path = value.partition("=")
    if not path:
        raise argparse.ArgumentTypeError("LABEL=PATH expected")
    return label, path
