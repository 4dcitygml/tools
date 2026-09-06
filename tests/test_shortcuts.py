#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""tools/shortcuts.py: desktop launchers embed the city id and the per-user tools
folder only — never the clone's path — so they work wherever they are moved."""
from __future__ import annotations

import importlib.util
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("shortcuts", REPO_ROOT / "tools" / "shortcuts.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


class TestNames(unittest.TestCase):
    def test_safe_name_keeps_scripts_and_drops_separators(self):
        self.assertEqual(sc.safe_name("東京駅デモ"), "東京駅デモ")
        self.assertEqual(sc.safe_name("Demo: Tokio/Station"), "Demo Tokio Station")
        self.assertEqual(sc.safe_name(""), "CityGML")


class TestLauncherCopy(unittest.TestCase):
    def test_ensure_launcher_copies_from_bundle_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = tmp / "program"; bundle.mkdir()
            name = "citygml.ps1" if sys.platform.startswith("win") else "citygml.sh"
            (bundle / name).write_text("#!/bin/bash\necho hi\n")
            tools = tmp / "tools"
            target = sc.ensure_launcher(tools, bundle)
            self.assertEqual(target, tools / name)
            self.assertTrue(target.is_file())
            target.write_text("customized")
            sc.ensure_launcher(tools, bundle)
            self.assertEqual(target.read_text(), "customized")   # an existing launcher is left alone


@unittest.skipUnless(sys.platform == "darwin", "macOS bundle")
class TestMacApp(unittest.TestCase):
    def test_bundle_embeds_city_not_clone_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with patch.object(sc.Path, "home", return_value=tmp):
                tools = tmp / "Documents" / "citygml-tools"
                tools.mkdir(parents=True)
                app = sc.create_mac_app(tools, "東京駅デモ", "4dcitygml/sample-tokyo-station", tools, logo=None, sign=False)
            self.assertEqual(app.name, "東京駅デモ.app")
            script = (app / "Contents" / "MacOS" / "launch").read_text()
            self.assertIn("4dcitygml/sample-tokyo-station", script)
            self.assertIn('"$HOME/Documents/citygml-tools/citygml.sh"', script)   # resolved at run time
            self.assertNotIn(str(tmp), script)                                      # no absolute machine path
            info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
            self.assertEqual(info["CFBundleExecutable"], "launch")
            self.assertEqual(info["CFBundleName"], "東京駅デモ")
            self.assertNotIn("CFBundleIconFile", info)                              # no logo given → no icon, still valid

    def test_bundle_gets_an_icon_from_a_png_logo(self):
        if not (sc.shutil.which("sips") and sc.shutil.which("iconutil")):
            self.skipTest("sips/iconutil missing")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            logo = tmp / "logo.png"
            # a minimal valid PNG (1x1) is enough for sips to scale
            logo.write_bytes(bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0300"
                "06fd02fe0a3f0f7c0000000049454e44ae426082"))
            with patch.object(sc.Path, "home", return_value=tmp):
                tools = tmp / "Documents" / "citygml-tools"; tools.mkdir(parents=True)
                app = sc.create_mac_app(tools, "Demo", "o/r", tools, logo=logo, sign=False)
            info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
            self.assertEqual(info.get("CFBundleIconFile"), "icon")
            self.assertTrue((app / "Contents" / "Resources" / "icon.icns").is_file())


if __name__ == "__main__":
    unittest.main()
