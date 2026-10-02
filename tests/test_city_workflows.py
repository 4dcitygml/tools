# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Contract between tools/ci and the thin wrapper workflows shipped in city-template
(mirrored into sample cities when they adopt that tools version).

Runs only when the sibling repositories are checked out next to tools/ (the
public layout: <root>/tools, <root>/city-template, <root>/sample-*-station).
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT.parent
SIBLING_SUFFIX = REPO_ROOT.name.removeprefix("tools")   # sibling checkouts share this checkout's suffix
TEMPLATE = ROOT / ("city-template" + SIBLING_SUFFIX)
SAMPLES = sorted(ROOT.glob("sample-*-station" + SIBLING_SUFFIX))


@unittest.skipUnless(TEMPLATE.is_dir() and SAMPLES, "sibling city repositories not checked out")
class CityWorkflowContractTest(unittest.TestCase):
    def _wf(self, repo: Path, name: str) -> str:
        return (repo / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_tools_repository_defaults_to_4dcitygml_for_federated_city_repos(self) -> None:
        """A city repository hosted outside the 4dcitygml organization (a
        municipality's own org) must still fetch the shared CI logic; deriving the
        owner from the city repository would point at a non-existent <org>/tools."""
        wf = self._wf(TEMPLATE, "pr-analysis.yml")
        self.assertNotIn("github.repository_owner", wf)
        self.assertIn("CITYGML_TOOLS_REPO: ${{ vars.CITYGML_TOOLS_REPO || '4dcitygml/tools' }}", wf)

    def test_posting_workflow_truncates_instead_of_failing_on_long_comments(self) -> None:
        # The posting logic is one script the thin workflow calls; its limits are the contract.
        wf = self._wf(TEMPLATE, "pr-comment.yml")
        self.assertIn("python .github/scripts/post_comments.py", wf)
        import importlib.util, sys
        from unittest.mock import patch
        scripts = TEMPLATE / ".github" / "scripts"
        spec = importlib.util.spec_from_file_location("post_comments", scripts / "post_comments.py")
        post = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(scripts), *sys.path]):   # it imports its siblings by name
            spec.loader.exec_module(post)
        self.assertEqual(post.COMMENT_LIMIT, 60000)          # GitHub rejects bodies over 65,536 characters
        self.assertEqual(post.ARTIFACT_LIMIT, 4194304)       # absurd sizes are still rejected
        self.assertIn("Truncated to fit the GitHub comment size limit", post.TRUNCATED)
        body = post.truncated(b"x" * (post.COMMENT_LIMIT + 10))
        self.assertLessEqual(len(body), post.COMMENT_LIMIT + len(post.TRUNCATED.encode()))
        self.assertTrue(body.endswith(post.TRUNCATED.encode()))


def tracked(repo: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True).stdout
    return {name for name in out.decode("utf-8").split("\0") if name}


def template_differences(template: Path, city: Path, allowed: dict) -> list[str]:
    """Every file of a practice city that breaks .github/practice/allowed-differences.json of the template."""
    meta = json.loads((city / "4dcitygml.json").read_text(encoding="utf-8"))
    ours, theirs = tracked(template), tracked(city)
    expected = {name: template / name for name in ours}
    expected.update({name: template / source for name, source in allowed.get("activated", {}).items()})
    pr_template = template / ".github/PULL_REQUEST_TEMPLATE" / f"{meta.get('lang', 'en')}.md"
    if pr_template.is_file():
        expected[".github/PULL_REQUEST_TEMPLATE.md"] = pr_template
    free = set(allowed.get("replaced", {})) - {".github/PULL_REQUEST_TEMPLATE.md"}
    added = set(allowed.get("added", []))
    lang = str(meta.get("lang") or "en").split("-")[0].lower()
    if lang != "en":   # e.g. docs/<lang>/getting-started.md, in the city's working language only
        added |= {path.replace("<lang>", lang) for path in allowed.get("added_in_city_language", [])}
    data = tuple(f"{d.rstrip('/')}/" for d in [*(meta.get("data_dirs") or []), "provenance"])
    problems = [f"missing: {name}" for name in sorted(set(expected) - theirs)]
    for name in sorted(theirs):
        if name in free or name in added or name.startswith(data):
            continue
        if name not in expected:
            problems.append(f"not in the template: {name}")
        elif (city / name).read_bytes() != expected[name].read_bytes():
            problems.append(f"differs from {expected[name].relative_to(template)}: {name}")
    return problems


ALLOWED = TEMPLATE / ".github" / "practice" / "allowed-differences.json"   # CI configuration (A11)


@unittest.skipUnless(ALLOWED.is_file() and SAMPLES, "template without .github/practice/allowed-differences.json")
class PracticeCityTemplateTest(unittest.TestCase):
    # S15: a practice city is the template plus its data (rebuilt from the template in v1.5.0).
    def test_practice_cities_carry_the_template(self) -> None:
        allowed = json.loads(ALLOWED.read_text(encoding="utf-8"))
        problems = {city.name: template_differences(TEMPLATE, city, allowed) for city in SAMPLES}
        self.assertEqual({name: p for name, p in problems.items() if p}, {})

    def test_the_rule_itself(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            template, city = Path(t) / "template", Path(t) / "city"
            files = {"a.txt": "a", ".github/PULL_REQUEST_TEMPLATE.md": "en", ".github/PULL_REQUEST_TEMPLATE/de.md": "de",
                     ".github/practice/manual-reset.yml": "reset", "README.md": "template"}
            for root, extra in ((template, {}),
                                (city, {".github/PULL_REQUEST_TEMPLATE.md": "de", "README.md": "city",
                                        "docs/de/getting-started.md": "Erste Schritte",
                                        ".github/workflows/manual-reset.yml": "reset", "lod2/x.gml": "x",
                                        "4dcitygml.json": json.dumps({"lang": "de", "data_dirs": ["lod2"]})})):
                for name, text in {**files, **extra}.items():
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    (root / name).write_text(text, encoding="utf-8")
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            allowed = {"replaced": {"README.md": "", ".github/PULL_REQUEST_TEMPLATE.md": ""}, "added": ["4dcitygml.json"],
                       "added_in_city_language": ["docs/<lang>/getting-started.md"],
                       "activated": {".github/workflows/manual-reset.yml": ".github/practice/manual-reset.yml"}}
            self.assertEqual(template_differences(template, city, allowed), [])
            (city / "a.txt").write_text("changed", encoding="utf-8")
            (city / "extra.txt").write_text("", encoding="utf-8")
            (city / ".github/PULL_REQUEST_TEMPLATE.md").write_text("en", encoding="utf-8")
            subprocess.run(["git", "-C", str(city), "add", "-A"], check=True)
            self.assertEqual(template_differences(template, city, allowed),
                             ["differs from .github/PULL_REQUEST_TEMPLATE/de.md: .github/PULL_REQUEST_TEMPLATE.md",
                              "differs from a.txt: a.txt", "not in the template: extra.txt"])


if __name__ == "__main__":
    unittest.main()
