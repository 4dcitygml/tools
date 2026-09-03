// Copyright (c) 2026 4dcitygml
// SPDX-License-Identifier: Apache-2.0
let csrf = '';
let plan = null;
let prReady = false;

const el = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function api(path, body) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function showError(error) {
  el('error').textContent = error ? error.message || String(error) : '';
}

async function loadStatus() {
  try {
    const data = await api('/api/status');
    csrf = data.csrf;
    const s = data.status;
    prReady = Boolean(s.github_authenticated && s.remote);
    el('status').className = '';
    el('status').innerHTML = `
      <dl>
        <dt>Repository</dt><dd>${esc(s.repository)}</dd>
        <dt>Branch</dt><dd>${esc(s.branch || '—')}</dd>
        <dt>GitHub CLI</dt><dd>${s.github_authenticated ? 'Connected' : 'Not connected'}</dd>
        <dt>Official repository</dt><dd>${esc(s.remote || 'Not configured')}</dd>
        <dt>CityGML</dt><dd>${esc(s.citygml)}</dd>
        <dt>Source</dt><dd>${s.export_mode === 'file' ? 'Test export file' : 'citydb-tool'}</dd>
      </dl>`;
    el('sync').disabled = !(s.repository_ok && s.clean && s.citygml_ok && s.messages.length === 0);
    if (s.messages.length) showError(new Error(s.messages.join(' ')));
  } catch (error) { showError(error); }
}

el('sync').addEventListener('click', async () => {
  showError(null);
  el('sync').disabled = true;
  el('sync').textContent = 'Checking…';
  try {
    plan = await api('/api/sync', {csrf});
    el('result').hidden = false;
    el('proposal').hidden = true;
    el('selection').hidden = true;
    el('changes').hidden = false;
    el('prResult').innerHTML = '';
    const count = plan.modified.length + plan.added.length + plan.deleted.length + plan.renamed.length;
    el('summary').innerHTML = count === 0
      ? '<p class="ok">Up to date. No semantic differences were found.</p>'
      : `<p><strong>${count} building(s)</strong> have candidate changes. Choose an eligible change to prepare its pull request.</p>`;
    el('changes').innerHTML = plan.changes.map((change, index) => {
      const attrs = (change.attribute_diffs || []).map(d =>
        `<li><code>${esc(d.path)}</code>: <del>${esc(d.old)}</del> → <ins>${esc(d.new)}</ins></li>`).join('');
      const eligible = change.status === 'modified' && !change.geometry_changed;
      const selector = eligible
        ? `<button class="select-pr" data-building-id="${esc(change.id)}">Prepare PR</button>`
        : '<button disabled>Manual review required</button>';
      return `<article><div class="change-heading"><h3>${esc(change.id)} <small>${esc(change.status)}</small></h3>${selector}</div><ul>${attrs}</ul>${change.geometry_changed ? '<p>Geometry changed</p>' : ''}</article>`;
    }).join('');
    document.querySelectorAll('.select-pr').forEach(button => {
      button.addEventListener('click', () => prepareBuilding(button));
    });
  } catch (error) { showError(error); }
  finally { el('sync').disabled = false; el('sync').textContent = 'Sync'; }
});

async function prepareBuilding(button) {
  showError(null);
  const id = button.dataset.buildingId;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = 'Checking official repository…';
  try {
    const readiness = await api('/api/prepare', {csrf, buildingId: id});
    if (!readiness.ready) throw new Error(readiness.message);
    selectBuilding(id, readiness);
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

function selectBuilding(id, readiness) {
  const metadata = plan.versioning[id] || {};
  el('changes').hidden = true;
  el('selection').hidden = false;
  el('selectedBuilding').textContent = `Selected for PR: ${id} · official ${readiness.remote_sha.slice(0, 12)}`;
  el('proposal').hidden = false;
  el('proposal').dataset.buildingId = id;
  el('author').value = metadata.updating_person || '';
  el('reason').value = metadata.reason_for_update || '';
  el('source').value = metadata.lineage || '';
  el('notes').value = '';
  el('versioning').textContent = Object.keys(metadata).length
    ? 'Available 3DCityDB FEATURE versioning values have been filled in. Review them before publishing.'
    : 'No FEATURE versioning values were found. The gray text in each field shows which value would be filled automatically.';
  el('createPr').disabled = !prReady;
  if (!prReady) {
    el('versioning').textContent += ' This test repository has no official origin, so PR creation is disabled.';
  }
  el('proposal').scrollIntoView({behavior: 'smooth', block: 'start'});
}

el('changeSelection').addEventListener('click', () => {
  el('selection').hidden = true;
  el('changes').hidden = false;
  el('proposal').hidden = true;
  el('changes').scrollIntoView({behavior: 'smooth', block: 'start'});
});

el('createPr').addEventListener('click', async () => {
  showError(null);
  el('createPr').disabled = true;
  el('createPr').textContent = 'Creating pull request…';
  try {
    const data = await api('/api/propose', {
      csrf,
      buildingId: el('proposal').dataset.buildingId,
      publicAuthor: el('author').value,
      reason: el('reason').value,
      source: el('source').value,
      notes: el('notes').value,
    });
    el('prResult').innerHTML = `<p class="ok">Pull request created: <a href="${esc(data.url)}" target="_blank" rel="noopener">${esc(data.url)}</a></p>`;
  } catch (error) { showError(error); }
  finally { el('createPr').disabled = !prReady; el('createPr').textContent = 'Create pull request'; }
});

loadStatus();
