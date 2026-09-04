"""Static checks for every GitHub Actions workflow in tools and the city repositories.

These catch the mistakes that only surface after a push (a workflow file that GitHub
refuses to start, an unpinned action, a missing permission block) without needing
PyYAML: the checks are line-based on purpose so they run in any environment.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS = Path(__file__).resolve().parent.parent
CITY_REPOS = [p for p in [ROOT / "city-template", *sorted(ROOT.glob("sample-*-station"))] if p.is_dir()]

SHA_PIN = re.compile(r"^\s*-?\s*uses:\s*([^\s@#]+)@([0-9a-f]{40})\b")
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)")


def workflows() -> list[Path]:
    files: list[Path] = []
    for repo in [TOOLS, *CITY_REPOS]:
        files.extend(sorted((repo / ".github" / "workflows").glob("*.yml")))
    return files


def job_level_ifs(text: str) -> list[str]:
    """`if:` lines indented exactly one level under `jobs:` (4 spaces) = job conditions."""
    out, in_jobs = [], False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if in_jobs and re.match(r"^\S", line):
            in_jobs = False
        if in_jobs and re.match(r"^    if:", line):
            out.append(line.strip())
    return out


class WorkflowLintTest(unittest.TestCase):
    def test_workflows_exist(self) -> None:
        self.assertGreaterEqual(len(workflows()), 2)

    def test_every_action_is_pinned_to_a_commit_sha(self) -> None:
        # A5: moving tags (v4, main, latest) are not allowed in any workflow.
        bad = []
        for wf in workflows():
            for line in wf.read_text(encoding="utf-8").splitlines():
                m = USES.match(line)
                if m and not m.group(1).startswith("./") and not SHA_PIN.match(line):
                    bad.append(f"{wf.parent.parent.parent.name}/{wf.name}: {line.strip()}")
        self.assertEqual(bad, [], "unpinned actions:\n" + "\n".join(bad))

    def test_no_runner_only_functions_in_job_conditions(self) -> None:
        # hashFiles() is evaluated on the runner and is unavailable in job-level `if:`;
        # GitHub then refuses to start the workflow at all (0 jobs, conclusion failure).
        bad = []
        for wf in workflows():
            for cond in job_level_ifs(wf.read_text(encoding="utf-8")):
                if "hashFiles(" in cond:
                    bad.append(f"{wf.parent.parent.parent.name}/{wf.name}: {cond}")
        self.assertEqual(bad, [], "hashFiles in job-level if:\n" + "\n".join(bad))

    def test_every_workflow_declares_permissions(self) -> None:
        # Least privilege: the token scope must be explicit (top-level or per job).
        bad = []
        for wf in workflows():
            text = wf.read_text(encoding="utf-8")
            if not re.search(r"^\s*permissions:", text, re.M):
                bad.append(f"{wf.parent.parent.parent.name}/{wf.name}")
        self.assertEqual(bad, [], "workflows without a permissions block:\n" + "\n".join(bad))

    def test_no_moving_tools_reference(self) -> None:
        # City workflows fetch shared logic at an immutable commit SHA, never a branch.
        bad = []
        for wf in workflows():
            for line in wf.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*CITYGML_TOOLS_REF:\s*(\S+)", line)
                if m and not re.fullmatch(r"[0-9a-f]{40}", m.group(1)):
                    bad.append(f"{wf.parent.parent.parent.name}/{wf.name}: {line.strip()}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_pull_request_target_checkouts_do_not_keep_credentials(self) -> None:
        # A1: a workflow triggered by pull_request_target must not leave a writable token in
        # a checkout of untrusted code.
        bad = []
        for wf in workflows():
            text = wf.read_text(encoding="utf-8")
            if "pull_request_target" not in text:
                continue
            for m in re.finditer(r"uses:\s*actions/checkout@[0-9a-f]{40}[^\n]*\n((?:\s{8,}[^\n]*\n)*)", text):
                block = m.group(1)
                if "persist-credentials: false" not in block:
                    bad.append(f"{wf.parent.parent.parent.name}/{wf.name}")
                    break
        self.assertEqual(bad, [], "pull_request_target checkout keeps credentials:\n" + "\n".join(bad))

    def test_release_workflows_smoke_test_their_archives(self) -> None:
        # The distribution must be exercised (extracted and imported) before it is uploaded.
        for name in ("release-hub.yml",):
            text = (TOOLS / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertEqual(text.count("Smoke-test the archive"), 2, name)


if __name__ == "__main__":
    unittest.main()
