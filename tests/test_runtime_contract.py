#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""docs/client-runtime-contract.md (runtime-v1) stays in sync with the code that
implements it: the environment/CLI channel, the shared settings file, the
folder and zip layout, the discovery endpoints and the update endpoints. A future
core module is held to the same document."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = (REPO_ROOT / "docs" / "client-runtime-contract.md").read_text(encoding="utf-8")


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hub = load("rt_hub", "tools/hub/app.py")
attr = load("rt_attr", "tools/attr_editor/app.py")


class TestDocumentedChannels(unittest.TestCase):
    def test_environment_and_flags(self):
        for token in ("`CITYGML_UPSTREAM=<owner/repo>`", "`CITYGML_HUB_TAG=<hub-vX.Y.Z>`", "`--repo <path>`",
                      "`--port <n>`", "`--no-browser`", "`CITYGML_LANG`"):
            self.assertIn(token, DOC, token)
        launcher = (REPO_ROOT / "install" / "citygml.sh").read_text(encoding="utf-8")
        for var in ("CITYGML_UPSTREAM", "CITYGML_HUB_TAG"):
            self.assertIn(var, launcher)
        app = (REPO_ROOT / "tools" / "hub" / "app.py").read_text(encoding="utf-8")
        for var in ("CITYGML_UPSTREAM", "CITYGML_HUB_TAG"):
            self.assertIn(var, app)

    def test_files_ports_and_endpoints(self):
        for token in ("~/.citygml_attr_editor.json", "citygml-hub/<hub-vX.Y.Z>/program/hub.py",
                      "`8760`", "`8765`", "`8766`", "`GET /api/status`", "`GET /api/repo`",
                      "`GET /api/update`", "`POST /api/update/fetch`", "`POST /api/shortcut`",
                      "citygml-hub-<ver>-macos.zip", "citygml-hub-<ver>-windows-full.zip", "`min_hub`"):
            self.assertIn(token, DOC, token)
        self.assertEqual(hub.DEFAULT_PORT, 8760)
        self.assertEqual({t["port"] for t in hub.TOOLS.values()}, {8765, 8766})
        self.assertEqual(hub.CONFIG_PATH.name, ".citygml_attr_editor.json")
        self.assertEqual(attr.CONFIG_PATH.name, ".citygml_attr_editor.json")
        self.assertEqual(hub.platform_asset_name("hub-v1.2.0").split("-")[:3], ["citygml", "hub", "v1.2.0"])

    def test_runtime_symbols_exist(self):
        for name in ("city_key", "clone_city", "requested_city", "remember_city_clone", "saved_clone_for",
                     "free_port", "running_hub_for", "running_hub_tag", "hub_install_root",
                     "latest_hub_release", "install_release", "min_hub_of", "create_city_shortcut",
                     "git_sync_module", "shortcuts_module", "classification_module"):
            self.assertTrue(callable(getattr(hub, name, None)), name)


class TestSharedSettingsFile(unittest.TestCase):
    """Writers merge; keys they do not own survive — across the hub and the editors."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "config.json"
        self._saved = (hub.CONFIG_PATH, attr.CONFIG_PATH)
        hub.CONFIG_PATH = path
        attr.CONFIG_PATH = path

    def tearDown(self):
        hub.CONFIG_PATH, attr.CONFIG_PATH = self._saved
        self._tmp.cleanup()

    def test_editor_save_keeps_the_hubs_city_registry(self):
        hub.remember_city_clone("4dcitygml/sample-tokyo-station", "/x/tokyo")
        attr.save_config({"repo": "/x/tokyo", "lang": "ja"})          # what the editor writes after its own setup
        cfg = hub.load_config()
        self.assertEqual(cfg["cities"]["4dcitygml/sample-tokyo-station"]["repo"], "/x/tokyo")
        self.assertEqual(cfg["lang"], "ja")
        hub.save_config({"repo": "/x/munich"})
        self.assertEqual(attr.load_config()["lang"], "ja")           # and the hub keeps the editor's key

    def test_documented_example_round_trips(self):
        example = json.loads(DOC[DOC.index("```json") + 7:DOC.index("```", DOC.index("```json") + 7)]
                             .replace("/…/", "/tmp/").replace("2026-09-06T…", "2026-09-06T00:00:00+0900"))
        self.assertEqual(set(example), {"repo", "lang", "cities"})
        self.assertEqual(set(next(iter(example["cities"].values()))), {"repo", "last_used"})


class TestStatusShape(unittest.TestCase):
    def test_status_carries_the_discovery_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "4dcitygml.json").write_text(json.dumps({"repo": "o/r", "data_dirs": ["d"]}))
            (root / "d").mkdir(); (root / "d" / "a.gml").write_text("<x/>")
            with patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.2.0"}):
                status = hub.Hub(root).status()
        for key in ("repo", "branch", "nwo", "hubTag", "sync", "runtime"):
            self.assertIn(key, status)
        self.assertEqual(Path(status["repo"]).resolve(), root.resolve())
        self.assertEqual(status["hubTag"], "hub-v1.2.0")


if __name__ == "__main__":
    unittest.main()
