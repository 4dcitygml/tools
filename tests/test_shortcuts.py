#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""tools/shortcuts.py: desktop launchers embed the city id and the per-user tools
folder only — never the clone's path — so they work wherever they are moved."""
from __future__ import annotations

import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import TempHome, load_app, runtime

sc = load_app("shortcuts", "tools/shortcuts.py")


class TestNames(unittest.TestCase):
    def test_safe_name_keeps_scripts_and_drops_separators(self):
        self.assertEqual(sc.safe_name("東京駅デモ"), "東京駅デモ")
        self.assertEqual(sc.safe_name("Demo: Tokio/Station"), "Demo Tokio Station")
        self.assertEqual(sc.safe_name(""), "CityGML")


class TestLauncherCopy(unittest.TestCase):
    def test_ensure_launcher_copies_from_bundle_once(self):
        with TempHome() as home, tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "program"; bundle.mkdir()
            (bundle / runtime.LAUNCHER_NAME).write_text("#!/bin/bash\necho hi\n")
            with patch.object(runtime, "SHARED_DIR", bundle):
                target = sc.ensure_launcher()
                self.assertEqual(target, home / "Documents" / "citygml-tools" / runtime.LAUNCHER_NAME)
                self.assertTrue(target.is_file())
                target.write_text("customized")
                sc.ensure_launcher()
            self.assertEqual(target.read_text(), "customized")   # a source tree never overwrites

    def test_installed_version_refreshes_a_stale_launcher(self):
        with TempHome() as home, tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "program"; bundle.mkdir()
            (bundle / runtime.LAUNCHER_NAME).write_text("#!/bin/bash\necho new\n")
            target = home / "Documents" / "citygml-tools" / runtime.LAUNCHER_NAME
            target.parent.mkdir(parents=True)
            target.write_text("#!/bin/bash\necho old\n")            # the copy of an earlier version
            with patch.object(runtime, "SHARED_DIR", bundle), patch.dict(os.environ, {"CITYGML_HUB_TAG": "hub-v1.3.0"}):
                self.assertEqual(sc.ensure_launcher(), target)
            self.assertEqual(target.read_text(), "#!/bin/bash\necho new\n")   # kept in line with the installed version


@unittest.skipUnless(sys.platform == "darwin", "macOS bundle")
class TestMacApp(unittest.TestCase):
    def test_bundle_embeds_city_not_clone_path(self):
        with TempHome() as tmp:
            tools = runtime.tools_dir()
            tools.mkdir(parents=True)
            app = sc.create_mac_app(tools, "東京駅デモ", "4dcitygml/sample-tokyo-station", logo=None, sign=False)
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
        with TempHome() as tmp:
            logo = tmp / "logo.png"
            # a minimal valid PNG (1x1) is enough for sips to scale
            logo.write_bytes(bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0300"
                "06fd02fe0a3f0f7c0000000049454e44ae426082"))
            tools = runtime.tools_dir(); tools.mkdir(parents=True)
            app = sc.create_mac_app(tools, "Demo", "o/r", logo=logo, sign=False)
            info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
            self.assertEqual(info.get("CFBundleIconFile"), "icon")
            self.assertTrue((app / "Contents" / "Resources" / "icon.icns").is_file())


if __name__ == "__main__":
    unittest.main()
