#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Numerical regression tests for the texture editor's camera-alignment math.

The production functions are extracted from ``tex_editor/index.html`` and run
unchanged. The test uses Node.js when available; macOS development machines can
instead use the system JavaScript for Automation runtime without adding a dependency.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "tools" / "tex_editor" / "index.html"


def _production_camera_math() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    start_marker = "function gaussSolve(M, rhs) {"
    end_marker = "// ---- UI ----"
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


REGRESSION_JS = r"""
let camGeom = [];

function maxPointError(actual, expected) {
  let worst = 0;
  for (let i = 0; i < actual.length; i++) {
    worst = Math.max(worst, Math.hypot(
      actual[i][0] - expected[i][0], actual[i][1] - expected[i][1]));
  }
  return worst;
}

function runRegression() {
  // Non-coplanar synthetic control points projected by a known 3x4 camera.
  const trueP = [
    [1.2, 0.08, -0.04, 320],
    [-0.03, 1.1, 0.06, 240],
    [0.001, -0.002, 0.01, 1],
  ];
  const points3 = [
    [-3, -2, 2], [4, -2, 3], [-2, 5, 4], [3, 4, 7],
    [-5, 1, 8], [5, 2, 5], [1, -5, 6], [-4, -4, 9],
    [2, 1, 11], [6, -3, 10], [-1, 6, 12], [4, 5, 14],
  ];
  const pairs = points3.map(p3 => ({ p3, p2: camProject(trueP, p3).slice(0, 2) }));
  const fittedP = dltSolve(pairs);
  if (!fittedP) throw new Error('DLT returned null for valid non-coplanar points');
  const dltError = camReprojError(fittedP, pairs);

  // An overdetermined homography fit (six points, four required).
  const src = [[0, 0], [100, 0], [100, 80], [0, 80], [35, 25], [70, 55]];
  const knownH = ([X, Y]) => {
    const d = 0.0012 * X - 0.0007 * Y + 1;
    return [(1.15 * X + 0.12 * Y + 18) / d,
            (-0.08 * X + 1.08 * Y + 27) / d];
  };
  const dst = src.map(knownH);
  const fittedH = homographyLSQ(src, dst);
  if (!fittedH) throw new Error('Homography returned null for valid points');
  const homographyError = maxPointError(src.map(p => fittedH(p[0], p[1])), dst);

  // Two faces with identical image footprints: the nearer face (w=2) must win
  // over the farther face (w=4), independent of traversal order.
  const zCamera = [
    [80, 0, 50, 0],
    [0, 80, 50, 0],
    [0, 0, 1, 0],
  ];
  camGeom = [
    { ptsm: [[-2, -2, 4], [2, -2, 4], [2, 2, 4], [-2, 2, 4]] },
    { ptsm: [[-1, -1, 2], [1, -1, 2], [1, 1, 2], [-1, 1, 2]] },
  ];
  const z = buildZBuffer(zCamera, 100, 100);
  const zCenter = z.zbuf[50 * z.zw + 50];

  return { dltError, homographyError, zCenter };
}
"""


def _run_javascript(source: str) -> dict[str, float]:
    node = shutil.which("node")
    if node:
        command = [node, "-e", source + "\nconsole.log(JSON.stringify(runRegression()));"]
    elif sys.platform == "darwin" and shutil.which("osascript"):
        command = [
            "osascript", "-l", "JavaScript", "-e",
            source + "\nJSON.stringify(runRegression());",
        ]
    else:
        raise unittest.SkipTest("Node.js or macOS JavaScript runtime is required")
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"JavaScript runtime returned no result: {result.stderr}")
    return json.loads(lines[-1])


class TestTextureCameraMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = _run_javascript(_production_camera_math() + REGRESSION_JS)

    def test_normalized_dlt_reprojects_synthetic_camera(self):
        self.assertLess(self.result["dltError"], 1e-8)

    def test_overdetermined_homography_reprojects_control_points(self):
        self.assertLess(self.result["homographyError"], 1e-8)

    def test_z_buffer_keeps_nearest_face(self):
        self.assertAlmostEqual(self.result["zCenter"], 0.5, places=6)


if __name__ == "__main__":
    unittest.main()
