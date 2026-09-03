# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Headless E2E for the dashboard (dev use; needs selenium + Chrome. Not run in CI).

Verifies page rendering and absence of JS errors in both the disconnected
(CITYGML_HUB_NO_GH=1) and connected states.
Run: python3 tools/hub/e2e_dashboard.py"""
import subprocess, sys, time, os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def browser():
    o = Options()
    o.add_argument("--headless=new"); o.add_argument("--window-size=1200,900")
    return webdriver.Chrome(options=o)

def start_hub(port, no_gh):
    env = dict(os.environ)
    if no_gh: env["CITYGML_HUB_NO_GH"] = "1"
    p = subprocess.Popen([sys.executable, "tools/hub/app.py", "--repo", ".",
                          "--port", str(port), "--no-browser"], cwd=REPO, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    return p

def js_errors(d):
    return [e for e in d.get_log("browser") if e["level"] == "SEVERE" and "favicon" not in e["message"]]

fail = 0
def check(name, cond):
    global fail
    print(("PASS" if cond else "FAIL"), name)
    if not cond: fail += 1

# --- State 1: disconnected (NO_GH) ---
hub = start_hub(8801, no_gh=True)
d = browser()
try:
    d.get("http://localhost:8801/"); time.sleep(3)
    body = d.find_element(By.TAG_NAME, "body").text
    # Expected strings = en catalog values (SOURCE_LANG=en; headless Chrome negotiates en).
    check("Disconnected: GitHub disconnected display", "not connected" in body.lower())
    check("Disconnected: connect button present", len(d.find_elements(By.ID, "ghConnect")) == 1)
    check("Disconnected: gh auth login notice gone", "gh auth login" not in body)
    check("Disconnected: list shows connection prompt", 'use "Connect to GitHub"' in body)
    check("Disconnected: no JS errors", not js_errors(d))
finally:
    d.quit(); hub.terminate()

# --- State 2: connected (gh token stashed; real GraphQL) ---
hub = start_hub(8802, no_gh=False)
d = browser()
try:
    d.get("http://localhost:8802/"); time.sleep(4)
    body = d.find_element(By.TAG_NAME, "body").text
    _u = os.environ.get("CITYGML_E2E_USER");
    check(f"Connected: @{_u} display", f"@{_u}" in body) if _u else None
    check("Connected: connected status display", "Connected (@" in body)
    check("Connected: badge render", "Maintainer class" in body or "Merged PRs" in body)
    check("Connected: PR row render", len(d.find_elements(By.CSS_SELECTOR, "#prs .row")) > 0)
    check("Connected: Issue row render", len(d.find_elements(By.CSS_SELECTOR, "#issues .row")) > 0)
    check("Connected: no warning box", d.find_element(By.ID, "ghWarn").text.strip() == "")
    check("Connected: no JS errors", not js_errors(d))
finally:
    d.quit(); hub.terminate()

sys.exit(1 if fail else 0)
