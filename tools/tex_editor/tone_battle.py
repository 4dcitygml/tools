#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Tone finals — compare candidate tones side by side in 3 browser tabs (for team review).

One command does the following:
1. Generate converted textures (variants) for the candidate tones if missing
   (scripts/retone_textures.py; requires Pillow: pip install Pillow)
2. Start one server per "original + candidate" on separate ports (served with
   --textures swapping)
3. Open browser tabs on the same building side by side (tone name shown in the header)

Usage (inside the clone):
    python3 tools/tex_editor/tone_battle.py
    python3 tools/tex_editor/tone_battle.py --means 115,125,135 --mesh 53394632 \\
        --tile 533946321 --bid bldg_84b2b1b5-...

Exit with Ctrl+C (stops all started servers).
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
import urllib.request
from urllib.parse import quote
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent.parent
RETONE = REPO_ROOT / "scripts" / "retone_textures.py"
VARIANTS_DIR = REPO_ROOT / "_local" / "tone_variants"

# Default subject: a high-rise tower (LOD2, many faces, tone differences easy to see)
DEFAULT_TILE = "533946533"
DEFAULT_BID = "bldg_0c1c527d-771c-497d-88b6-34c34ddfde62"
DEFAULT_MESH = "53394653"
BASE_PORT = 8790


def wait_up(port: int, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--means", default=None, help="Y average values to compare (exposure shift mode, comma-separated)")
    p.add_argument("--lifts", default="100,115,130",
                   help="shadow lift amounts to compare (lift dark areas only, comma-separated; default 100,115,130)")
    p.add_argument("--mesh", default=DEFAULT_MESH,
                   help=f"prefix match for appearance folder to generate variants (default {DEFAULT_MESH})")
    p.add_argument("--tile", default=DEFAULT_TILE, help="mesh to open")
    p.add_argument("--bid", default=DEFAULT_BID, help="gml:id of building to open")
    p.add_argument("--no-open", action="store_true", help="do not open browser (for testing)")
    args = p.parse_args()

    # Default is the shadow-lift method (sunlit faces unchanged; only shaded faces lifted)
    if args.means:
        cands = [("mean", float(x)) for x in args.means.split(",")]
    else:
        cands = [("lift", float(x)) for x in args.lifts.split(",")]

    # Clean up child servers reliably on SIGTERM too (Ctrl+C goes via KeyboardInterrupt)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    # 1) Prepare variants (generate if missing)
    def vname(kind, v):
        return f"y{v:.0f}" if kind == "mean" else f"lift{v:.0f}"

    for kind, v in cands:
        vdir = VARIANTS_DIR / vname(kind, v)
        marker = vdir / f".done_{args.mesh}"
        if marker.exists():
            print(f"Variant exists: {vdir} (mesh {args.mesh})")
            continue
        print(f"Generating variant: {vname(kind, v)} (mesh {args.mesh}; may take several minutes)")
        flag = "--mean" if kind == "mean" else "--lift"
        r = subprocess.run(
            [sys.executable, str(RETONE), "--make-variant", str(vdir),
             flag, str(v), "--mesh", args.mesh],
            cwd=str(REPO_ROOT),
        )
        if r.returncode != 0:
            sys.exit("Failed to generate variant (check Pillow: pip install Pillow)")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    # 2) Start servers (original + each candidate)
    plans = [("Original", None)] + [
        (("Y=" if kind == "mean" else "lift=") + f"{v:.0f}", VARIANTS_DIR / vname(kind, v))
        for kind, v in cands
    ]
    procs = []
    urls = []
    try:
        for i, (label, tex) in enumerate(plans):
            port = BASE_PORT + i
            cmd = [sys.executable, str(APP_DIR / "app.py"),
                   "--port", str(port), "--no-browser"]
            if tex is not None:
                cmd += ["--textures", str(tex)]
            procs.append(subprocess.Popen(cmd, cwd=str(REPO_ROOT)))
            if not wait_up(port):
                sys.exit(f"Failed to start server on port {port}")
            url = (f"http://localhost:{port}/?tile={args.tile}&bid={args.bid}"
                   f"&label={quote(label)}")
            urls.append((label, url))
            print(f"  {label}: {url}")

        # 3) Open the tabs
        if not args.no_open:
            for _, url in urls:
                webbrowser.open(url)
                time.sleep(0.8)
        print("\nClick LOD2 → Texture in the 3D panel of each tab to compare.")
        print("Exit with Ctrl+C (stops all servers)")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nExiting")
    finally:
        for pr in procs:
            pr.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
