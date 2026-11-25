// Copyright (c) 2026 4dcitygml
// SPDX-License-Identifier: Apache-2.0
// What the attribute and the texture editor share (T0; before, each page kept its own copy): the
// city's map with its tiles and buildings, and the page address that names the tile and the
// building. attr_editor/app.py puts it into both editors' screens (Handler.page_transform). It works on
// the globals both pages keep under the same names: map, tiles, currentTile, selected,
// footprintLayers, loadTile and selectBuilding.
'use strict';

const FOOTPRINT = { color: '#555', weight: 1, fillColor: '#4fc3f7', fillOpacity: 0.35 };
const FOOTPRINT_SELECTED = { color: '#d97706', weight: 2.5, fillColor: '#fbbf24', fillOpacity: 0.55 };

// The city's map (its centre, zoom and base layer from the server; a world view without them)
function editorMap() {
  const cfg = window.CITYGML.map;
  const m = L.map('map').setView(cfg.center || [20, 0], cfg.zoom || 2);
  const plan = cfg.basemaps.plan;
  L.tileLayer(plan.url, { maxZoom: plan.maxZoom, attribution: plan.attribution }).addTo(m);
  return m;
}

// The tiles as blue squares (label(tile) is the tooltip); a click opens the tile
function drawTiles(label) {
  const layer = L.layerGroup().addTo(map);
  for (const tile of tiles) {
    if (!tile.bounds) continue;
    const rect = L.rectangle([[tile.bounds[0], tile.bounds[1]], [tile.bounds[2], tile.bounds[3]]],
      { color: '#0969da', weight: 1, fillOpacity: 0.04 }).addTo(layer);
    rect.bindTooltip(label(tile), { sticky: true });
    rect.on('click', () => loadTile(tile.code));
  }
  return layer;
}

// The open tile's buildings as footprints (a click selects one); the map moves to the tile
function drawFootprints() {
  footprintLayers.forEach(l => map.removeLayer(l));
  footprintLayers.clear();
  for (const b of currentTile.buildings) {
    if (!b.footprint || b.footprint.length < 3) continue;
    const poly = L.polygon(b.footprint, FOOTPRINT).addTo(map);
    poly.on('click', () => selectBuilding(b));
    poly.bindTooltip(b.buildingID || b.gid, { sticky: true });
    footprintLayers.set(b.gid, poly);
  }
  const b0 = currentTile.bounds;
  if (b0) map.fitBounds([[b0[0], b0[1]], [b0[2], b0[3]]]);
}

function highlightFootprint(gid) {
  footprintLayers.forEach((poly, g) => poly.setStyle(g === gid ? FOOTPRINT_SELECTED : FOOTPRINT));
}

// The address names the open tile and building (and a tone label), so a reload comes back to them
function updateUrl() {
  const q = new URLSearchParams();
  if (currentTile) q.set('tile', currentTile.code);
  if (selected) q.set('bid', selected.gid);
  if (window._toneLabel) q.set('label', window._toneLabel);
  history.replaceState(null, '', '?' + q.toString());
}

// At start: the tile and building the address names, else the whole city in view
async function openFromUrl() {
  const q = new URLSearchParams(location.search);
  if (q.get('tile')) {
    await loadTile(q.get('tile'));
    if (q.get('bid') && currentTile) {
      const b = currentTile.buildings.find(x => x.gid === q.get('bid'));
      if (b) selectBuilding(b, true);
    }
  } else if (tiles.length) {
    const bs = tiles.map(tile => tile.bounds).filter(Boolean);
    if (bs.length) map.fitBounds(bs.map(b => [[b[0], b[1]], [b[2], b[3]]]).flat());
  }
}

// What a send will do, from /api/submission: [label, HTML] rows (account, author, copy, city,
// then the page's own rows, then how the proposal opens); each editor lays them out its own way
function submissionRows(s, own) {
  const idn = s.identity || {};
  return [
    [t('common.confirm_account', 'GitHub account'), s.account ? `<b>@${esc(s.account)}</b>`
      : `<span style="color:var(--danger)">${t('common.confirm_no_account', 'none connected for this city')}</span>`],
    [t('common.confirm_identity', 'Name recorded with the change'), (idn.name || idn.email)
      ? `${esc(idn.name)} &lt;${esc(idn.email)}&gt;` : t('common.confirm_identity_unset', '(not set — choose the account in the hub first)')],
    [t('common.confirm_origin', 'Uploaded to (your own copy)'), s.origin ? esc(s.origin) : '—'],
    [t('common.confirm_upstream', 'Proposed to (the city)'), esc(s.upstream || '')],
    ...own,
    [t('common.confirm_proposal', 'Proposal'), s.autoPr ? t('common.confirm_pr_auto', 'opened automatically after the upload')
      : t('common.confirm_pr_manual', 'GitHub opens for the final step (one green button)')],
  ];
}
