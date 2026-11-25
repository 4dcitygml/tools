// Copyright (c) 2026 4dcitygml
// SPDX-License-Identifier: Apache-2.0
// What every screen of the tools shares (V6; before, each page kept its own copy and they
// differed). runtime.page puts it into each screen after the language pack.
'use strict';

const $ = id => document.getElementById(id);

// The English originals when no language pack was injected
window.t = window.t || ((k, d, p) => {
  let s = d;
  if (p) for (const key in p) s = s.split('{' + key + '}').join(p[key]);
  return s;
});

// Text for HTML, in content and in attribute values (D-3a6: one copy left quotes as they were)
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

// A call to the tool's own server: GET, or POST of `body` as JSON. A failed call throws an
// Error whose `data` is the server's answer (null when there was none).
async function api(path, body) {
  const r = await fetch(path, body === undefined ? undefined
    : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const j = await r.json().catch(() => null);
  if (!r.ok || !j || (j.ok === false && j.error)) {
    const e = new Error((j && j.error) || `HTTP ${r.status}`);
    e.data = j;
    throw e;
  }
  return j;
}

// A short note in the page's #toast element
function toast(msg) {
  const el = document.getElementById('toast'); if (!el) return;
  el.textContent = msg; el.style.display = 'block';
  clearTimeout(el._h); el._h = setTimeout(() => el.style.display = 'none', 3500);
}

function ago(iso) {
  if (!iso) return '';
  const sec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (sec < 3600) return t('common.ago_min', '{n} min ago', {n: Math.max(0, Math.floor(sec / 60))});
  if (sec < 86400) return t('common.ago_hour', '{n} hr ago', {n: Math.floor(sec / 3600)});
  return t('common.ago_day', '{n} days ago', {n: Math.floor(sec / 86400)});
}

// The pixel box a face's UVs cover in a w×h atlas (CityGML's UV origin is bottom-left, a canvas's
// top-left): the texture editor paints into it, the 3D view cuts it out
function uvBox(uvs, iw, ih) {
  let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity;
  for (const [u, v] of uvs) {
    if (u < u0) u0 = u;
    if (u > u1) u1 = u;
    if (v < v0) v0 = v;
    if (v > v1) v1 = v;
  }
  return {
    x: Math.round(u0 * iw), y: Math.round((1 - v1) * ih),
    w: Math.max(1, Math.round((u1 - u0) * iw)),
    h: Math.max(1, Math.round((v1 - v0) * ih)),
  };
}

// The desktop icon for this city; the result goes into #shortcutResult
async function makeShortcut() {
  const out = document.getElementById('shortcutResult');
  const show = text => { if (out) out.textContent = ' ' + text; };
  show('…');
  try {
    const r = await api('/api/shortcut', {});
    show(t('common.shortcut_done', 'Created {name}. Drag it to your Desktop or Dock; from now on, open the tools with it.', {name: r.name}));
  } catch (e) {
    show(t('common.shortcut_failed', 'Could not create the icon: {reason}', {reason: e.message}));
  }
}

// Unexpected errors on screen, so they are seen without opening the console
window.addEventListener('error', ev => showErrBanner(ev.message));
window.addEventListener('unhandledrejection', ev => showErrBanner(String(ev.reason)));
function showErrBanner(msg) {
  if (toolStopped) return;   // the stop banner says why every call fails
  banner('errBanner', t('common.err_banner', '⚠ Error: {msg} (click to close)', {msg})).onclick = ev => ev.currentTarget.remove();
}
function banner(id, text) {   // its look: common.css
  let b = $(id);
  if (!b) {
    b = document.createElement('div');
    b.id = id;
    document.body.appendChild(b);
  }
  b.textContent = text;
  return b;
}

// The tool is a program running in a terminal window: when that window is closed, the page
// stays but nothing works. Any answer of the page's own server means it runs; only a failed
// connection means it stopped (no route of its own: the page itself is asked).
let toolStopped = false;
function heartbeat() {
  fetch(location.pathname, {method: 'HEAD', cache: 'no-store'}).then(() => {
    toolStopped = false;
    $('stopBanner')?.remove();
  }, () => {
    toolStopped = true;
    $('errBanner')?.remove();
    banner('stopBanner', t('common.tool_stopped',
      'The tool has stopped. If you closed its terminal window, start it again (the same way as before), then reload this page.'));
  });
}

// An editor opened from the hub links back to it (the hub hands its address over at launch)
function hubBackLink() {
  const hub = window.CITYGML && window.CITYGML.hub;
  if (!hub) return;
  const a = document.createElement('a');
  a.href = hub;
  a.className = 'hubBack';
  a.textContent = t('common.back_to_hub', '← Back to the hub');
  (document.querySelector('header, #header') || document.body).prepend(a);
}

window.addEventListener('DOMContentLoaded', () => {
  hubBackLink();
  setInterval(heartbeat, 5000);
});
