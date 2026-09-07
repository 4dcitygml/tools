# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The distribution zip is built and verified by scripts/build_bundle.py and
scripts/verify_bundle.py — the same two scripts the release workflow calls. Running them
here against the source tree means a rename in the code that would break the release is
caught by pytest, not by a red release job."""
from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.support import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-hub.yml"


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_bundle = load("build_bundle")
verify_bundle = load("verify_bundle")


def stub_portable(folder: Path) -> Path:
    """Stand-ins for the pinned MinGit and PythonPortable the workflow downloads."""
    for rel in ("PortableGit/cmd/git.exe", "PortableGit/LICENSE.txt", "PythonPortable/python.exe",
                "PythonPortable/LICENSE.txt", "PythonPortable/python314._pth"):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text("stub", encoding="utf-8")
    return folder


class TestBuildAndVerify(unittest.TestCase):
    def test_macos_zip_from_the_source_tree_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = build_bundle.build(REPO_ROOT, "macos", "v9.9.9", tmp)
            self.assertEqual(zip_path.name, "citygml-hub-v9.9.9-macos.zip")
            digest = verify_bundle.verify(zip_path, "macos")
            self.assertEqual(len(digest), 64)
            names = set(zipfile.ZipFile(zip_path).namelist())
            self.assertTrue(names.issuperset(build_bundle.required("macos")))
            self.assertNotIn(f"{build_bundle.LIB}/start-windows.bat", names)          # Windows-only launcher
            self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names))
            self.assertFalse(any(n.startswith(f"{build_bundle.ROOT}/") and n.count("/") == 1 for n in names))  # only program/ at the top

    def test_windows_zip_with_stub_runtimes_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            portable = stub_portable(Path(tmp) / "dist")
            zip_path = build_bundle.build(REPO_ROOT, "windows", "dev", tmp, portable_dir=portable)
            self.assertEqual(zip_path.name, "citygml-hub-dev-windows-full.zip")
            verify_bundle.verify(zip_path, "windows")
            names = set(zipfile.ZipFile(zip_path).namelist())
            for n in (f"{build_bundle.LIB}/PortableGit/cmd/git.exe", f"{build_bundle.LIB}/PythonPortable/python.exe",
                      f"{build_bundle.LIB}/start-windows.bat"):
                self.assertIn(n, names)

    def test_windows_flavor_needs_the_portable_runtimes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                build_bundle.build(REPO_ROOT, "windows", "dev", tmp)

    def test_verify_rejects_a_zip_missing_a_required_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = build_bundle.build(REPO_ROOT, "macos", "dev", tmp)
            broken = Path(tmp) / "broken.zip"
            with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(broken, "w") as dst:
                for info in src.infolist():
                    if info.filename != f"{build_bundle.LIB}/runtime.py":
                        dst.writestr(info, src.read(info))
            with self.assertRaisesRegex(RuntimeError, "runtime.py"):
                verify_bundle.verify(broken, "macos")

    def test_version_comes_from_the_release_tag_only(self):
        self.assertEqual(build_bundle.version_from_tag("hub-v1.3.0"), "v1.3.0")
        for other in ("", "refs/heads/main", "tools-v1.2.2", "hub-v1.3"):
            self.assertEqual(build_bundle.version_from_tag(other), "dev")


class TestManifest(unittest.TestCase):
    def test_every_manifest_source_exists_and_the_required_names_come_from_it(self):
        for flavor in ("macos", "windows"):
            entries = build_bundle.manifest(REPO_ROOT, flavor)
            for src, arc, mode in entries:
                self.assertTrue(src.is_file(), src)
                self.assertTrue(arc.startswith(f"{build_bundle.LIB}/"), arc)
            archive_names = {arc for _, arc, _ in entries}
            from_tree = [n for n in build_bundle.required(flavor) if "PortableGit" not in n and "PythonPortable" not in n]
            self.assertTrue(set(from_tree).issubset(archive_names), set(from_tree) - archive_names)
        self.assertEqual(next(m for s, a, m in build_bundle.manifest(REPO_ROOT, "macos") if a.endswith("/citygml.sh")), 0o755)

    def test_every_hub_screen_and_every_pack_travels(self):
        archive_names = {arc for _, arc, _ in build_bundle.manifest(REPO_ROOT, "macos")}
        for page in (REPO_ROOT / "tools" / "hub").glob("*.html"):
            self.assertIn(f"{build_bundle.LIB}/{page.name}", archive_names)
        for rel in ("attr_editor/setup.html", "attr_editor/viewer.html", "i18n/catalogs/hub/de.json",
                    "themes/theme_loader.py", "runtime.py", "accounts.py"):
            self.assertIn(f"{build_bundle.LIB}/{rel}", archive_names)

    def test_sparse_checkout_of_both_jobs_covers_the_manifest(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        blocks = re.findall(r"sparse-checkout: \|\n((?:\s+\S+\n)+)", text)
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            listed = [ln.strip() for ln in block.splitlines() if ln.strip()]
            for src, _, _ in build_bundle.manifest(REPO_ROOT, "windows"):
                rel = src.relative_to(REPO_ROOT).as_posix()
                covered = "/" not in rel or any(rel == entry or rel.startswith(entry.rstrip("/") + "/")
                                                or (entry.count("/") == 1 and rel.startswith(entry.split("/")[0] + "/")
                                                    and rel.count("/") == 1)   # cone mode: files in a listed dir's parent
                                                for entry in listed)
                self.assertTrue(covered, f"{rel} is not in the sparse-checkout: {listed}")
            for script in ("scripts/build_bundle.py", "scripts/verify_bundle.py"):
                self.assertIn(script, listed)


class TestWorkflowUsesTheScripts(unittest.TestCase):
    def test_no_inline_zip_logic_remains(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("zipfile", text)
        self.assertNotIn("required = [", text)
        self.assertEqual(text.count("scripts/build_bundle.py --flavor windows"), 1)
        self.assertEqual(text.count("scripts/build_bundle.py --flavor macos"), 1)
        self.assertEqual(text.count("citygml-hub-*-windows-full.zip --flavor windows"), 1)
        self.assertEqual(text.count("citygml-hub-*-macos.zip --flavor macos"), 1)
        self.assertIn("--git-smoke", text)                   # the bundled python.exe runs the verifier too

    def test_release_inputs_stay_pinned(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for marker in ("MINGIT_URL:", "MINGIT_SHA256:", "PYEMBED_URL:", "PYEMBED_SHA256:", "Fetch-Verified"):
            self.assertIn(marker, text)
        self.assertNotIn("releases/latest", text)
        self.assertNotIn("pip install pyinstaller", text)
        self.assertIn("needs: [windows, macos-zip]", text)


if __name__ == "__main__":
    unittest.main()
