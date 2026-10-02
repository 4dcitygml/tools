# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
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
# The city checkouts next to this one carry the same suffix (tools -> city-template; a development
# copy tools<suffix> -> city-template<suffix>), as tests/test_operator_explanation.py resolves them.
# Without the suffix the city workflows of the development copies were never linted.
SUFFIX = TOOLS.name.removeprefix("tools")
CITY_REPOS = [p for p in [ROOT / f"city-template{SUFFIX}", *sorted(ROOT.glob(f"sample-*-station{SUFFIX}"))]
              if p.is_dir()]
# The trusted side: workflows that run .github/scripts from the default branch with a write token.
TRUSTED_WORKFLOWS = ("pr-comment.yml", "review-report.yml", "pr-base-freshness.yml", "pr-recheck.yml")

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


def trusted_setup_block(text: str) -> str:
    """A trusted workflow's first two steps (checkout, setup-python), without comment lines."""
    lines = [line for line in text.splitlines() if not line.strip().startswith("#")]
    start = next(i for i, line in enumerate(lines) if re.match(r"^\s*- uses: actions/checkout@", line))
    end = next(i for i, line in enumerate(lines) if i > start and "python-version:" in line)
    return "\n".join(lines[start:end + 1])


class WorkflowLintTest(unittest.TestCase):
    def test_workflows_exist(self) -> None:
        # the tools repository ships one release workflow (hub-v1.2: the hub zip is the only
        # distribution); city repositories add theirs when checked out next to it
        self.assertGreaterEqual(len(workflows()), 1)

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

    def test_step_names_and_run_lines_do_not_break_yaml(self) -> None:
        # A plain scalar ends at ": " — a step name like "Build (x: y)" makes GitHub refuse the
        # file. Line-based on purpose (no PyYAML in CI), so it runs everywhere.
        bad = []
        for wf in workflows():
            for line in wf.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*-?\s*(name|run):\s*(?![|>'\"])(.*)$", line)
                if m and ": " in m.group(2):
                    bad.append(f"{wf.parent.parent.parent.name}/{wf.name}: {line.strip()}")
        self.assertEqual(bad, [], "': ' inside a plain scalar:\n" + "\n".join(bad))

    def test_every_tools_workflow_carries_the_copy_switch(self) -> None:
        # A development copy sets CITYGML_CI=off to skip every automatic run; the entry job of
        # each workflow carries the condition, and a manual run is always allowed.
        switch = "if: vars.CITYGML_CI != 'off' || github.event_name == 'workflow_dispatch'"
        for wf in sorted((TOOLS / ".github" / "workflows").glob("*.yml")):
            text = wf.read_text(encoding="utf-8")
            self.assertIn(switch, text, wf.name)
            self.assertIn("workflow_dispatch:", text, wf.name)

    def test_the_windows_build_is_the_only_job_no_windows_skips(self) -> None:
        # CITYGML_CI=no-windows (a development copy) skips the Windows runner, the costliest,
        # and nothing else; a manual run still builds it. Production sets nothing.
        condition = "if: vars.CITYGML_CI != 'no-windows' || github.event_name == 'workflow_dispatch'"
        text = (TOOLS / ".github" / "workflows" / "release-hub.yml").read_text(encoding="utf-8")
        job = text[text.index("\n  windows:\n"):]
        job = job.split("\n    steps:", 1)[0]
        self.assertIn(condition, job)
        self.assertIn("runs-on: windows-latest", job)
        for wf in sorted((TOOLS / ".github" / "workflows").glob("*.yml")):
            self.assertEqual(wf.read_text(encoding="utf-8").count("no-windows'"), 1 if wf.name == "release-hub.yml" else 0, wf.name)
            if wf.name != "release-hub.yml":
                self.assertNotIn("runs-on: windows", wf.read_text(encoding="utf-8"), wf.name)

    def test_trusted_workflows_share_one_setup_block(self) -> None:
        # S7 (maintainer, 2026-09-30): no composite action and no merged workflow; instead the
        # four trusted workflows of every city start with the same two steps, so none of them can
        # drift to checking out PR code, keeping a token or running another Python. The pins move
        # together: the blocks must equal each other, whatever version they pin.
        blocks, missing = {}, []
        for repo in CITY_REPOS:
            for name in TRUSTED_WORKFLOWS:
                wf = repo / ".github" / "workflows" / name
                if wf.is_file():
                    blocks[f"{repo.name}/{name}"] = trusted_setup_block(wf.read_text(encoding="utf-8"))
                else:
                    missing.append(f"{repo.name}/{name}")
        if not CITY_REPOS:
            self.skipTest("no city repository checked out next to tools")
        self.assertEqual(missing, [], "trusted workflows missing")
        first = next(iter(blocks.values()))
        for line in ("ref: ${{ github.event.repository.default_branch }}", "persist-credentials: false",
                     "sparse-checkout: .github/scripts", "- uses: actions/setup-python@", "python-version: '3.12'"):
            self.assertIn(line, first)
        differ = [name for name, block in blocks.items() if block != first]
        self.assertEqual(differ, [], f"setup block differs from {next(iter(blocks))}:\n" + "\n".join(differ))

    def test_release_workflows_smoke_test_their_archives(self) -> None:
        # The distribution must be exercised (extracted and imported) before it is uploaded:
        # both build jobs run scripts/verify_bundle.py (the Windows job twice, once under the
        # bundled python.exe), and the same script runs in tests/test_bundle.py.
        text = (TOOLS / ".github" / "workflows" / "release-hub.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("run: python3 scripts/verify_bundle.py") + text.count("run: python scripts/verify_bundle.py"), 2)
        self.assertIn("verify_bundle.py $zip --flavor windows --git-smoke", text)


if __name__ == "__main__":
    unittest.main()
