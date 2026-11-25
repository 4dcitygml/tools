#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The screens' shared behaviour, run in Node against a small DOM stand-in: the stop banner when
the tool's terminal was closed and the link back to the hub (#57), and a setup screen that is
written only where it changed, so a click is never lost to the 2-second poll (#58)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMON = (REPO_ROOT / "tools" / "common.js").read_text(encoding="utf-8")
SETUP = re.search(r"<script>\n(.*?)</script>", (REPO_ROOT / "tools" / "hub" / "setup.html").read_text(encoding="utf-8"),
                  re.S)[1]

DOM = """
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
const els = {}, writes = {};
function element(id) {
  let html = '';
  return {id, style: {}, children: [],
    get innerHTML() { return html; },
    set innerHTML(v) { writes[this.id] = (writes[this.id] || 0) + 1; html = v; },
    prepend(c) { this.children.unshift(c); },
    remove() { delete els[this.id]; }};
}
globalThis.document = {
  getElementById: id => els[id] || null,
  createElement: () => element(''),
  querySelector: () => els.header || null,
  body: {appendChild: c => { els[c.id] = c; }, prepend: c => { els.body = c; }},
  addEventListener: () => {}, visibilityState: 'visible', title: '',
};
globalThis.location = {pathname: '/', replace: () => {}};
const answers = {};
let online = true;
globalThis.fetch = async path => { if (!online) throw new TypeError('Failed to fetch'); return answers[path] || {ok: true, status: 200, json: async () => ({ok: true})}; };
const reply = body => ({ok: true, status: 200, json: async () => body});
const settle = () => new Promise(r => setTimeout(r, 20));
"""


def run(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("Node.js is required")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestStopBannerAndHubLink(unittest.TestCase):
    def test_a_stopped_tool_is_said_once_and_cleared_when_it_answers(self):
        out = run(DOM + COMMON + """
(async () => {
  const r = {};
  online = false; heartbeat(); await settle();
  r.stopped = els.stopBanner && els.stopBanner.innerHTML === '' && !!els.stopBanner.textContent;
  r.text = els.stopBanner.textContent;
  showErrBanner('Failed to fetch');               // every call fails now: no second, misleading banner
  r.errBanner = !!els.errBanner;
  online = true; heartbeat(); await settle();
  r.cleared = !els.stopBanner;
  console.log(JSON.stringify(r));
})();
""")
        self.assertTrue(out["stopped"])
        self.assertIn("If you closed its terminal window", out["text"])
        self.assertFalse(out["errBanner"])
        self.assertTrue(out["cleared"])

    def test_an_editor_links_back_only_when_the_hub_launched_it(self):
        out = run(DOM + COMMON + """
els.header = element('header');
hubBackLink();
const none = els.header.children.length;
window.CITYGML = {hub: 'http://localhost:8762/'};    // a hub on another port than 8760
hubBackLink();
console.log(JSON.stringify({none, href: els.header.children[0].href, text: els.header.children[0].textContent}));
""")
        self.assertEqual(out["none"], 0)
        self.assertEqual(out["href"], "http://localhost:8762/")
        self.assertEqual(out["text"], "← Back to the hub")


STATUS = {"ok": True, "clientId": True, "login": None, "chosen": False, "accounts": ["alice"], "machine": None,
          "legacy": None, "clone": {}, "fork": None, "forkChecked": True, "access": None, "mode": "setup"}


class TestTheSetupScreen(unittest.TestCase):
    def test_an_unchanged_state_writes_nothing(self):
        # #58: every poll replaced the whole screen, so a press and its release could land on two
        # different buttons and the click was lost
        out = run(DOM + COMMON + """
for (const id of ['prog', 'screen', 'nav']) els[id] = element(id);
window.CITYGML = {UPSTREAM: 'https://github.com/4dcitygml/sample-munich-station', DEFAULT_DEST: '/d', MODE: 'setup', START: 'cmd'};
answers['/api/setup/status'] = reply(%s);
""" % json.dumps(STATUS) + SETUP + """
(async () => {
  await settle();
  const first = {...writes};
  await poll(); await poll();
  const same = {...writes};
  answers['/api/setup/status'] = reply({...%s, accounts: ['alice', 'bob']});
  await poll();
  const changed = {...writes};
  online = false; await poll();
  const offline = {...writes};
  console.log(JSON.stringify({first, same, changed, offline, screen: els.screen.innerHTML}));
  process.exit(0);
})();
""" % json.dumps(STATUS))
        self.assertEqual(out["first"], {"prog": 1, "screen": 1, "nav": 1})
        self.assertEqual(out["same"], out["first"])                        # nothing replaced, the buttons stay
        self.assertEqual(out["changed"]["screen"], 2)                      # a new saved account: written again
        self.assertEqual(out["changed"]["nav"], 1)
        self.assertEqual(out["offline"], out["changed"])                   # no answer: the screen stays as it was
        self.assertIn("https://github.com/signup", out["screen"])         # a new person can make an account
        self.assertNotIn('class="ghost" id="go"', out["screen"])           # the new sign-in looks like a button


if __name__ == "__main__":
    unittest.main()
