# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Exercise the actual pre-build release guards without publishing anything."""
import os
from pathlib import Path
import re
import subprocess
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseSeriesTest(unittest.TestCase):
    def workflow(self, series):
        return (ROOT / '.github/workflows' / f'release-{series}.yml').read_text()

    def run_guard(self, series, event, tag='', ref='main', ref_type='branch'):
        text = self.workflow(series).split('  validate-release:\n', 1)[1]
        script = text.split('        run: |\n', 1)[1].split('\n\n', 1)[0]
        return subprocess.run(['bash', '-c', textwrap.dedent(script)], capture_output=True,
            env={**os.environ, 'REQUESTED_TAG': tag, 'GITHUB_EVENT_NAME': event,
                 'GITHUB_REF_NAME': ref, 'GITHUB_REF_TYPE': ref_type}).returncode

    def test_each_series_accepts_its_own_tag_and_rejects_other_series(self):
        for series, other in [('hub', 'tools'), ('tools', 'hub')]:
            for event in ['push', 'workflow_dispatch']:
                with self.subTest(series=series, event=event):
                    tag=f'{series}-v1.1.0'
                    self.assertEqual(self.run_guard(series,event,tag,tag,'tag'),0)
                    bad=f'{other}-v1.1.0'
                    self.assertNotEqual(self.run_guard(series,event,bad,bad,'tag'),0)

    def test_preview_is_branch_only_and_malformed_tags_fail(self):
        for series in ['hub','tools']:
            self.assertEqual(self.run_guard(series,'workflow_dispatch'),0)
            self.assertEqual(self.run_guard(series,'pull_request',ref='1/merge'),0)
            self.assertNotEqual(self.run_guard(series,'workflow_dispatch',ref=f'{series}-v1.1.0',ref_type='tag'),0)
            for tag in [f'{series}-vlatest', f'{series}-v1.1.0; echo unexpected', 'main']:
                self.assertNotEqual(self.run_guard(series,'workflow_dispatch',tag),0)

    def test_publish_jobs_and_assets_remain_separate(self):
        for series in ['hub','tools']:
            text=self.workflow(series)
            self.assertIn(f"tags: ['{series}-v*']", text)
            self.assertIn(f"if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/{series}-v')",text)
            self.assertNotIn('--clobber',text)
        hub=self.workflow('hub'); common=self.workflow('tools')
        self.assertNotIn('citygml-tools-${version}-source.zip',hub)
        self.assertNotIn('windows-full.zip',common)
        self.assertEqual(hub.count('needs: validate-release'),2)
        self.assertEqual(common.count('needs: validate-release'),1)
        self.assertIn('--latest=false',common)


if __name__ == '__main__': unittest.main()
