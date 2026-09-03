# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The bulk-submission provenance manifest: the published schema, the example,
the validator in scripts/provenance_manifest.py, and the policy document stay
consistent with each other."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO_ROOT / "schemas/provenance/bulk-manifest.schema.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((REPO_ROOT / "docs/examples/bulk-manifest.example.json").read_text(encoding="utf-8"))
DOC = (REPO_ROOT / "docs/bulk-submission-provenance.md").read_text(encoding="utf-8")


from scripts.provenance_manifest import validate as _validate, parse_manifest_ref, manifest_ref


def check(schema: dict, value) -> list[str]:
    return _validate(value, schema)


class BulkManifestTest(unittest.TestCase):
    def test_example_conforms_to_schema(self) -> None:
        self.assertEqual(check(SCHEMA, EXAMPLE), [])

    def test_checker_rejects_broken_manifests(self) -> None:
        broken = json.loads(json.dumps(EXAMPLE))
        broken["builder"]["tools_commit"] = "main"  # moving ref: never allowed
        broken["evidence"]["links"][0]["tier"] = "D"  # only A/B/C link tiers
        del broken["plan_issue"]
        errors = check(SCHEMA, broken)
        self.assertTrue(any("tools_commit" in e for e in errors), errors)
        self.assertTrue(any("tier" in e for e in errors), errors)
        self.assertTrue(any("plan_issue" in e for e in errors), errors)

    def test_schema_covers_every_bulk_kind_named_in_the_policy(self) -> None:
        kinds = set(SCHEMA["properties"]["kind"]["enum"])
        for kind in ("source-baseline", "scope-extract", "source-update", "carry-forward",
                     "identity-baseline", "identity-correction", "schema-update", "schema-migration", "layout"):
            self.assertIn(kind, kinds)
            self.assertIn(f"`{kind}`", DOC)

    def test_policy_names_the_machine_contract(self) -> None:
        for token in ("Provenance-Manifest:", "Building-ID-From:", "Building-ID-To:",
                      "Identity-Evidence:", "Created-By:", "Plan-Issue", "Manifest-SHA256",
                      "materials", "builder", "invocation", "products", "evidence", "sample_audit",
                      "continuous", "mixed", "renumbered"):
            self.assertIn(token, DOC, token)
        for key in ("materials", "builder", "invocation", "products", "evidence", "sample_audit"):
            self.assertIn(key, SCHEMA["required"])
        self.assertIn("identity-baseline", DOC)
        self.assertIn("Documentation never substitutes for an unimplemented gate", DOC)
        self.assertIn("pilot pending", DOC)  # gates exist; the PR types unlock after the private pilot

    def test_manifest_ref_round_trip(self) -> None:
        ref = manifest_ref("provenance/identity-baseline/53394611-2020-2025.json", b"{}")
        parsed = parse_manifest_ref(ref)
        self.assertEqual(parsed[0], "provenance/identity-baseline/53394611-2020-2025.json")
        self.assertEqual(len(parsed[1]), 64)
        self.assertIsNone(parse_manifest_ref("provenance/x.json@md5:abc"))


if __name__ == "__main__":
    unittest.main()
