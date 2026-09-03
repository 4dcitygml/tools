# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Entry points must not crash when the console cannot encode their output.

Regression for the first hub release build: on Windows the redirected stdout of
the embeddable Python uses the legacy code page, and `app.py --help` raised
UnicodeEncodeError on the arrow characters in the module docstring.
"""
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ["tools/hub/app.py", "tools/attr_editor/app.py", "tools/tex_editor/app.py"]


class ConsoleEncodingTest(unittest.TestCase):
    def test_help_survives_narrow_code_page(self):
        for rel in APPS:
            with self.subTest(app=rel):
                env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
                proc = subprocess.run([sys.executable, "-S", str(ROOT / rel), "--help"],
                                      capture_output=True, env=env, timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace")[-500:])
                self.assertNotIn(b"UnicodeEncodeError", proc.stderr)


if __name__ == "__main__":
    unittest.main()
