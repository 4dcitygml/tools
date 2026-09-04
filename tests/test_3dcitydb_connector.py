#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import os
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lxml import etree

TOOLS_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = TOOLS_ROOT / "connectors" / "3dcitydb" / "connector.py"
SPEC = importlib.util.spec_from_file_location("citydb_sync_connector", MODULE_PATH)
assert SPEC and SPEC.loader
connector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = connector
SPEC.loader.exec_module(connector)


class ConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        # Keep the test independent of the developer's gh login: an empty gh config directory
        # makes `gh auth status` answer "not logged in" at once, with no keychain or network.
        self._gh_env = {k: os.environ.get(k) for k in ("GH_CONFIG_DIR", "GH_TOKEN", "GITHUB_TOKEN")}
        os.environ["GH_CONFIG_DIR"] = str(self.repo / ".gh-config")
        os.environ.pop("GH_TOKEN", None)
        os.environ.pop("GITHUB_TOKEN", None)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, check=True)
        self.base = TOOLS_ROOT / "tests" / "fixtures" / "base.gml"
        self.head = TOOLS_ROOT / "tests" / "fixtures" / "prA_attr.gml"
        (self.repo / "city.gml").write_bytes(self.base.read_bytes())
        subprocess.run(["git", "add", "city.gml"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=self.repo, check=True)

    def tearDown(self) -> None:
        for key, value in self._gh_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def config(self, export: Path) -> object:
        return connector.SyncConfig.load(
            self.repo,
            citygml=Path("city.gml"),
            export_file=export,
        )

    def test_noop_plan_has_no_changes(self) -> None:
        plan = connector.plan_sync(self.config(self.base))
        self.assertEqual(plan.classification, "none")
        self.assertTrue(plan.verified)
        self.assertEqual(plan.output, plan.base)

    def test_attribute_plan_is_single_and_minimal(self) -> None:
        plan = connector.plan_sync(self.config(self.head))
        self.assertEqual(plan.classification, "single")
        self.assertTrue(plan.verified)
        self.assertEqual(len(plan.modified), 1)
        self.assertEqual(plan.methods[plan.modified[0]], "leaf")
        self.assertNotEqual(plan.output, plan.base)

    def test_dirty_repository_fails_closed(self) -> None:
        (self.repo / "notes.txt").write_text("untracked", encoding="utf-8")
        with self.assertRaises(connector.SyncError):
            connector.plan_sync(self.config(self.base))

    def test_stale_local_base_fails_closed(self) -> None:
        old_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, text=True, capture_output=True
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", old_head], cwd=self.repo, check=True
        )
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "local only"], cwd=self.repo, check=True
        )
        with self.assertRaises(connector.SyncError):
            connector.plan_sync(self.config(self.base))

    def test_citygml_must_be_inside_repository(self) -> None:
        with self.assertRaises(connector.SyncError):
            connector.SyncConfig.load(self.repo, citygml=self.base, export_file=self.base)

    def test_pr_body_prefills_public_fields_and_anchor(self) -> None:
        plan = connector.plan_sync(self.config(self.head))
        building_id = plan.modified[0]
        proposal = connector.ProposalInput(
            building_id=building_id,
            reason="現地確認による修正",
            source="2026年度調査",
            public_author="citydatawalker",
        )
        body = connector.render_pr_body(plan, proposal)
        self.assertIn("<!--sec:reason-->", body)
        self.assertIn(building_id, body)
        self.assertIn("citydatawalker", body)
        self.assertIn("2026年度調査", body)

    def test_normalize_default_core_namespace(self) -> None:
        raw = b'<CityModel xmlns="http://www.opengis.net/citygml/2.0"><cityObjectMember></cityObjectMember></CityModel>'
        normalized = connector.normalize_citydb_export(raw)
        self.assertIn(b"<core:CityModel", normalized)
        self.assertIn(b"<core:cityObjectMember>", normalized)
        self.assertIn(b"</core:CityModel>", normalized)

    def test_restores_encoded_uro_attributes(self) -> None:
        raw = b'''<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
          xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
          xmlns:gen="http://www.opengis.net/citygml/generics/2.0"
          xmlns:gml="http://www.opengis.net/gml"><core:cityObjectMember>
          <bldg:Building gml:id="b1"><gen:genericAttributeSet name="uro:buildingDataQualityAttribute#0">
          <gen:stringAttribute name="lodType"><gen:value>2</gen:value></gen:stringAttribute>
          <gen:stringAttribute name="lodType@codeSpace"><gen:value>codes.xml</gen:value></gen:stringAttribute>
          </gen:genericAttributeSet></bldg:Building></core:cityObjectMember></core:CityModel>'''
        output, count = connector.restore_generic_uro(
            raw, uro_namespace="https://www.geospatial.jp/iur/uro/3.0"
        )
        self.assertEqual(count, 1)
        self.assertIn(b"buildingDataQualityAttribute", output)
        self.assertIn(b'codeSpace="codes.xml"', output)
        self.assertNotIn(b"genericAttributeSet", output)

    def test_uro3_round_trip_is_semantically_identical(self) -> None:
        stylesheet = etree.parse(
            str(TOOLS_ROOT / "connectors" / "3dcitydb" / "xslt" / "uro_to_generic.xsl")
        )
        transformed = etree.XSLT(stylesheet)(etree.parse(io.BytesIO(self.base.read_bytes())))
        restored, count = connector.restore_generic_uro(
            bytes(transformed), uro_namespace=connector.detect_uro_namespace(self.base.read_bytes())
        )
        self.assertGreater(count, 0)
        diff = connector.diff_sources(self.base.read_bytes(), restored, "base", "roundtrip")
        self.assertEqual(diff["summary"]["modified"], 0)
        self.assertEqual(diff["summary"]["added"], 0)
        self.assertEqual(diff["summary"]["deleted"], 0)

    def test_geometry_proposal_is_not_published(self) -> None:
        geometry = TOOLS_ROOT / "tests" / "fixtures" / "prB_geom.gml"
        plan = connector.plan_sync(self.config(geometry))
        proposal = connector.ProposalInput(
            building_id=plan.modified[0],
            reason="geometry correction",
            source="survey",
            public_author="citydatawalker",
        )
        with self.assertRaises(connector.SyncError):
            connector.create_proposal(self.config(geometry), plan, proposal)

    def test_selected_building_output_excludes_other_changes(self) -> None:
        namespaces = (
            'xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
            'xmlns:gml="http://www.opengis.net/gml"'
        )
        def model(a: str, b: str) -> bytes:
            return (
                f'<core:CityModel {namespaces}>'
                '<core:cityObjectMember><bldg:Building gml:id="A">'
                f'<bldg:storeysAboveGround>{a}</bldg:storeysAboveGround>'
                '</bldg:Building></core:cityObjectMember>'
                '<core:cityObjectMember><bldg:Building gml:id="B">'
                f'<bldg:storeysAboveGround>{b}</bldg:storeysAboveGround>'
                '</bldg:Building></core:cityObjectMember></core:CityModel>'
            ).encode()

        base = model("3", "3")
        result = connector.reconstruct(base, model("5", "6"))
        plan = connector.SyncPlan(
            base_sha="test",
            original_branch="main",
            classification=result.classification,
            verified=result.verified,
            modified=result.modified,
            added=result.added,
            deleted=result.deleted,
            renamed=result.renamed,
            methods=result.methods,
            warnings=result.warnings,
            changes=connector.diff_sources(base, result.output, "base", "head")["buildings"],
            versioning={},
            output=result.output,
            base=base,
        )
        selected = connector.output_for_building(plan, "A")
        diff = connector.diff_sources(base, selected, "base", "selected")
        self.assertEqual([change["id"] for change in diff["buildings"]], ["A"])

    def test_prepare_fetches_official_and_detects_same_building_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as remote_temp, tempfile.TemporaryDirectory() as checkout_temp:
            remote = Path(remote_temp) / "official.git"
            subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
            subprocess.run(["git", "remote", "add", "origin", remote], cwd=self.repo, check=True)
            subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=self.repo, check=True)

            plan = connector.plan_sync(self.config(self.head))
            building_id = plan.modified[0]
            ready = connector.check_pr_readiness(self.config(self.head), plan, building_id)
            self.assertTrue(ready.ready)

            official = Path(checkout_temp) / "official"
            subprocess.run(["git", "clone", "-q", "-b", "main", remote, official], check=True)
            subprocess.run(["git", "config", "user.name", "Official"], cwd=official, check=True)
            subprocess.run(
                ["git", "config", "user.email", "official@example.invalid"],
                cwd=official,
                check=True,
            )
            (official / "city.gml").write_bytes(
                (TOOLS_ROOT / "tests" / "fixtures" / "prB_geom.gml").read_bytes()
            )
            subprocess.run(["git", "add", "city.gml"], cwd=official, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "official update"], cwd=official, check=True)
            subprocess.run(["git", "push", "-q", "origin", "main"], cwd=official, check=True)

            conflict = connector.check_pr_readiness(self.config(self.head), plan, building_id)
            self.assertFalse(conflict.ready)
            self.assertTrue(conflict.conflict)
            self.assertIn(building_id, conflict.official_changed_buildings)

    def test_ui_documents_versioning_field_mapping(self) -> None:
        html = (TOOLS_ROOT / "connectors" / "3dcitydb" / "web" / "index.html").read_text()
        self.assertIn("FEATURE.updating_person", html)
        self.assertIn("FEATURE.reason_for_update", html)
        self.assertIn("FEATURE.lineage", html)
        self.assertIn("Open a PR to the official repository", html)
        javascript = (TOOLS_ROOT / "connectors" / "3dcitydb" / "web" / "app.js").read_text()
        self.assertIn("/api/prepare", javascript)


if __name__ == "__main__":
    unittest.main()
