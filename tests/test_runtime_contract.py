#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""docs/client-runtime-contract.md (runtime-v1) on the main line: the shared
modules and launchers it names exist, the channels it documents are the ones the
launcher uses, and the shared settings file is merged (never replaced) by the editor.
The full hub-side checks live with hub-v1.2.0 (tests/test_runtime_contract.py there)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = (REPO_ROOT / "docs" / "client-runtime-contract.md").read_text(encoding="utf-8")

_spec = importlib.util.spec_from_file_location("rt_attr_main", REPO_ROOT / "tools" / "attr_editor" / "app.py")
attr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attr)


class TestContractArtifacts(unittest.TestCase):
    def test_shared_modules_and_launchers_exist(self):
        for rel in ("tools/git_sync.py", "tools/shortcuts.py", "scripts/pr_classification.py",
                    "install/citygml.sh", "install/citygml.ps1"):
            self.assertTrue((REPO_ROOT / rel).is_file(), rel)
            self.assertIn(Path(rel).name, DOC, rel)

    def test_launcher_uses_the_documented_channel(self):
        sh = (REPO_ROOT / "install" / "citygml.sh").read_text(encoding="utf-8")
        ps = (REPO_ROOT / "install" / "citygml.ps1").read_text(encoding="utf-8")
        for var in ("CITYGML_UPSTREAM", "CITYGML_HUB_TAG"):
            self.assertIn(var, sh); self.assertIn(var, ps)
            self.assertIn(f"`{var}=", DOC)
        for name in ("citygml-hub-<ver>-macos.zip", "citygml-hub-<ver>-windows-full.zip", "citygml-hub/<hub-vX.Y.Z>/program/hub.py"):
            self.assertIn(name, DOC)
        self.assertIn("-macos.zip", sh); self.assertIn("-windows-full.zip", ps)

    def test_editor_settings_write_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = attr.CONFIG_PATH
            attr.CONFIG_PATH = Path(tmp) / "config.json"
            try:
                attr.CONFIG_PATH.write_text(json.dumps({"cities": {"o/r": {"repo": "/x"}}, "lang": "de"}))
                attr.save_config({"repo": "/x"})
                cfg = json.loads(attr.CONFIG_PATH.read_text())
            finally:
                attr.CONFIG_PATH = saved
        self.assertEqual(cfg["cities"]["o/r"]["repo"], "/x")
        self.assertEqual(cfg["lang"], "de")
        self.assertEqual(cfg["repo"], "/x")


if __name__ == "__main__":
    unittest.main()
