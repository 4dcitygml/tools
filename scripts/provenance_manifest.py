#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Bulk-submission provenance manifest: loading, structural validation against
schemas/provenance/bulk-manifest.schema.json, and the trailer reference format.

The validator is a small dependency-free subset of JSON Schema (required,
type, enum, const, pattern, minItems, minimum/maximum, additionalProperties,
$ref into $defs) — enough for the manifest schema, identical in CI and locally.

Trailer reference format (exchange contract A7):
    Provenance-Manifest: provenance/identity-baseline/53394651-2020-2025.json@sha256:<hex>
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

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
