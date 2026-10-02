// SPC convective outlooks, days 1 to 8, on their own map.

import { state, api, on, el, openExternal, toast } from './core.js';
import { createMap, applyBasemap, setBorders, locationMarker } from './basemap.js';
import { wallNow, dayShort, dateShort, instant, SPC_COLORS } from './util.js';
import { uiIcon } from './icons.js';

const KINDS = {
  cat: 'Categorical',
  torn: 'Tornado',
  wind: 'Wind',
  hail: 'Hail',
  prob: 'Any severe',
};

const CAT_NAMES = {
  TSTM: 'General thunderstorms', MRGL: 'Marginal', SLGT: 'Slight', ENH: 'Enhanced', MDT: 'Moderate', HIGH: 'High',
};
const CAT_LEVEL = { TSTM: 0, MRGL: 1, SLGT: 2, ENH: 3, MDT: 4, HIGH: 5 };

// Fallbacks for files that leave out fill colors.
const PROB_COLORS = {
  torn: { '0.02': '#008b00', '0.05': '#8b4726', '0.10': '#ffc800', '0.15': '#ff0000', '0.30': '#ff00ff', '0.45': '#912cee', '0.60': '#104e8b' },
  other: { '0.05': '#8b4726', '0.15': '#ffc800', '0.30': '#ff0000', '0.45': '#ff00ff', '0.60': '#912cee' },
  ext: { '0.15': '#f2d147', '0.30': '#e5864e' },
};

let map = null;
let root = null;
let ui = {};
let layer = null;
let hatch = null;
let marker = null;
let day = 1;
let kind = 'cat';
const cache = new Map();

const kindsFor = (d) => (d <= 2 ? ['cat', 'torn', 'wind', 'hail'] : d === 3 ? ['cat', 'prob'] : ['prob']);

function dayDate(d) {
  // Each outlook day starts at 12Z, which is morning of that date in US zones.
  const info = state.spc && state.spc[d - 1];
  if (d > 1 && info && info.valid) {
    const t = new Date(info.valid);
    if (!Number.isNaN(t.getTime())) return new Date(t.getTime() + state.offset * 1000);
  }
  return new Date(wallNow().getTime() + (d - 1) * 86400000);
}

function labelText(label) {
  const up = label.toUpperCase();
  if (CAT_NAMES[up]) return CAT_NAMES[up];
  if (up.startsWith('SIGN') || up.startsWith('CIG')) return up === 'CIG2' || up === 'CIG3' ? `Intensity level ${up.slice(3)}` : 'Significant';
  const n = Number(label);
  if (Number.isFinite(n)) return `${Math.round(n * 100)}%`;
  return label;
}

function colorFor(props, k) {
  if (props.fill) return props.fill;
  const up = props.label.toUpperCase();
  if (SPC_COLORS[up]) return SPC_COLORS[up];
  const table = day >= 4 || k === 'prob' ? PROB_COLORS.ext : k === 'torn' ? PROB_COLORS.torn : PROB_COLORS.other;
  return table[Number(props.label).toFixed(2)] || '#9aa6b2';
}

async function fetchOutlook(d, k) {
  const key = `${d}:${k}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < 10 * 60 * 1000) return hit.data;
  const data = await api('/api/spc', { day: d, kind: k });
  cache.set(key, { at: Date.now(), data });
  return data;
}

async function draw() {
  const d = day;
  const k = kind;
  ui.status.textContent = 'Loading outlook';
  let o;
  try {
    o = await fetchOutlook(d, k);
  } catch (err) {
    ui.status.textContent = '';
    toast(`Outlook not available: ${err.message}`);
    return;
  }
  if (d !== day || k !== kind) return;
  if (layer) map.removeLayer(layer);
  if (hatch) map.removeLayer(hatch);

  layer = L.geoJSON({ type: 'FeatureCollection', features: o.features }, {
    pane: 'outlook',
    style: (f) => {
      const color = colorFor(f.properties, k);
      return { color: f.properties.stroke || color, weight: 1.4, opacity: 0.95, fillColor: color, fillOpacity: 0.42 };
    },
    onEachFeature: (f, lyr) => lyr.bindTooltip(labelText(f.properties.label), { sticky: true, className: 'iso-tip' }),
  }).addTo(map);

  hatch = o.intensity && o.intensity.length
    ? L.geoJSON({ type: 'FeatureCollection', features: o.intensity }, {
      pane: 'outlook',
      interactive: false,
      style: () => ({ color: '#111', weight: 1, opacity: 0.8, dashArray: '4 3', fillColor: 'url(#spc-hatch)', fillOpacity: 1 }),
    }).addTo(map)
    : null;

  ui.status.textContent = o.features.length
    ? `Valid ${instant(o.valid, true)} to ${instant(o.expire, true)}`
    : 'No areas outlined for this day';
  renderLegend(o, k);
  renderSummary();
}

function renderLegend(o, k) {
  const box = ui.legend;
  box.replaceChildren();
  const seen = new Map();
  for (const f of o.features) {
    if (!seen.has(f.properties.label)) seen.set(f.properties.label, colorFor(f.properties, k));
  }
  const entries = [...seen.entries()].sort((a, b) => sortKey(a[0]) - sortKey(b[0]));
  for (const [label, color] of entries) {
    box.append(el('div', { class: 'key-row' }, el('span', { class: 'swatch', style: `background:${color}` }), labelText(label)));
  }
  if (o.intensity && o.intensity.length) {
    box.append(el('div', { class: 'key-row' }, el('span', { class: 'swatch swatch-hatch' }), 'Significant or higher intensity'));
  }
  box.hidden = !box.childElementCount;
}

function sortKey(label) {
  const up = label.toUpperCase();
  if (up in CAT_LEVEL) return CAT_LEVEL[up];
  return Number(label) || 0;
}

function renderTabs() {
  ui.days.replaceChildren(...Array.from({ length: 8 }, (_, i) => {
    const d = i + 1;
    const date = dayDate(d);
    const info = state.spc && state.spc[i];
    const cat = info && info.category;
    const prob = info && info.probs && info.probs.prob;
    let dot = null;
    if (cat && cat.level >= 1) dot = SPC_COLORS[cat.label];
    else if (d >= 4 && prob && prob.pct >= 15) dot = PROB_COLORS.ext[prob.pct >= 30 ? '0.30' : '0.15'];
    return el('button', {
      class: 'day-tab', 'aria-pressed': String(d === day), title: `Day ${d}`,
      onclick: () => { day = d; if (!kindsFor(d).includes(kind)) kind = kindsFor(d)[0]; renderTabs(); draw(); },
    },
    el('span', { class: 'day-tab-name' }, d === 1 ? 'Today' : dayShort(date)),
    el('span', { class: 'day-tab-date' }, dateShort(date)),
    dot ? el('span', { class: 'day-tab-dot', style: `background:${dot}` }) : el('span', { class: 'day-tab-dot none' }));
  }));
  ui.kinds.replaceChildren(...kindsFor(day).map((k) => el('button', {
    'aria-pressed': String(k === kind),
    onclick: () => { kind = k; renderTabs(); draw(); },
  }, KINDS[k])));
  ui.kinds.hidden = kindsFor(day).length < 2;
}

function headLabel() {
  if (!ui.headText) return;
  const name = day === 1 ? 'Today' : dayShort(dayDate(day));
  const info = state.spc && state.spc[day - 1];
  const cat = info && info.category;
  ui.headText.textContent = cat
    ? `${name}: ${cat.level === 0 ? 'thunderstorms' : `${cat.name.toLowerCase()} risk`}`
    : `${name}: SPC outlook`;
}

function renderSummary() {
  headLabel();
  const box = ui.summary;
  box.replaceChildren();
  if (!state.loc) {
    box.append(el('p', { class: 'muted' }, 'Pick a location to see its risk.'));
    return;
  }
  if (state.data && !state.data.nws) {
    box.append(el('p', { class: 'muted' }, 'SPC outlooks cover the United States only.'));
    return;
  }
  const info = state.spc && state.spc[day - 1];
  if (!info) {
    box.append(el('p', { class: 'muted' }, 'Checking your location'));
    return;
  }
  const cat = info.category;
  const head = cat
    ? (cat.level === 0 ? 'General thunderstorms' : `${cat.name} risk, level ${cat.level} of 5`)
    : (day <= 3 ? 'No severe risk outlined' : '');
  if (head) {
    box.append(el('div', { class: 'sum-head', style: cat ? `--risk:${SPC_COLORS[cat.label]}` : '' },
      el('span', { class: 'risk-dot' }), head));
  }
  const rows = [];
  for (const k of ['torn', 'wind', 'hail', 'prob']) {
    const p = info.probs && info.probs[k];
    if (!p) continue;
    const name = k === 'prob' ? (day >= 4 ? 'Severe storms' : 'Any severe') : KINDS[k];
    const text = p.pct ? `${p.pct}%${p.intensity ? ', significant' : ''}` : 'Under 2%';
    if (k === 'prob' && day >= 4 && !p.pct) {
      rows.push(el('div', { class: 'sum-row' }, el('span', {}, name), el('span', {}, 'Below 15%')));
    } else {
      rows.push(el('div', { class: 'sum-row' }, el('span', {}, name), el('span', {}, text)));
    }
  }
  if (rows.length) box.append(el('div', { class: 'sum-rows' }, ...rows));
  box.append(el('p', { class: 'muted small' }, 'Chance of severe weather within 25 miles of your location.'));
}

function spcLink() {
  if (day <= 3) return `https://www.spc.noaa.gov/products/outlook/day${day}otlk.html`;
  return 'https://www.spc.noaa.gov/products/exper/day4-8/';
}

// Zoom that fits the lower 48 in the current window.
const usZoom = () => (window.innerWidth < 640 ? 3 : 4);

export function initOutlooks(container) {
  root = container;
  const mapEl = el('div', { class: 'radar-map' });
  ui.days = el('div', { class: 'day-tabs', role: 'group', 'aria-label': 'Outlook day' });
  ui.kinds = el('div', { class: 'seg', role: 'group', 'aria-label': 'Outlook type' });
  ui.status = el('p', { class: 'hint' });
  ui.summary = el('div', { class: 'sum' });
  ui.legend = el('div', { class: 'float legend legend-keys' });

  ui.headText = el('span', {}, 'SPC outlooks');
  const head = el('button', {
    class: 'panel-head', 'aria-expanded': 'false',
    onclick: () => head.setAttribute('aria-expanded', String(panel.classList.toggle('open'))),
  }, ui.headText, el('span', { class: 'panel-chev', html: uiIcon('chevron', 16) }));
  const panel = el('div', { class: 'float panel-products panel-outlook collapsible' },
    head,
    el('h2', { class: 'panel-title' }, 'SPC outlooks'),
    ui.days, ui.kinds, ui.status, ui.summary,
    el('button', { class: 'link', onclick: () => openExternal(spcLink()) },
      'Read the SPC discussion ', el('span', { html: uiIcon('external', 14) })));
  const homeBtn = el('button', {
    class: 'float icon-btn btn-home', 'aria-label': 'Show the whole country', title: 'Show the whole country',
    html: uiIcon('locate', 20),
    onclick: () => map.setView([38.5, -96], usZoom()),
  });

  root.append(mapEl, panel, homeBtn, ui.legend);
  map = createMap(mapEl, { maxZoom: 9 });
  map.setView([38.5, -96], usZoom());
  setBorders(map, false);
  if (state.loc) marker = locationMarker([state.loc.lat, state.loc.lon]).addTo(map);

  on('location', () => {
    if (!map) return;
    if (marker) map.removeLayer(marker);
    marker = locationMarker([state.loc.lat, state.loc.lon]).addTo(map);
    renderSummary();
    renderTabs();
  });
  on('spc', () => { renderTabs(); renderSummary(); });
  on('settings', (patch) => {
    if ('basemap' in patch || 'theme' in patch || 'carto_key' in patch) applyBasemap(map);
    if ('clock' in patch) draw();
  });
  renderTabs();
}

export function showOutlooks() {
  if (!map) return;
  setTimeout(() => {
    map.invalidateSize();
    renderTabs();
    renderSummary();
    draw();
  }, 30);
}
