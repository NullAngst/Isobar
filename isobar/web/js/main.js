// Boot, view routing, location search, refresh loop and the settings drawer.

import { state, api, on, emit, saveSettings, openExternal, toast, el, debounce } from './core.js';
import { uiIcon } from './icons.js';
import { ago } from './util.js';
import { renderNow, currentHourIndex } from './now.js';
import { renderHourly } from './hourly.js';
import { initRadar, showRadar, hideRadar, focusGeometry } from './radar.js';
import { needsCartoKey } from './basemap.js';
import { initOutlooks, showOutlooks } from './outlooks.js';
import { renderAlerts, renderDiscussion } from './alerts.js';

const VIEWS = [
  { id: 'now', name: 'Now', icon: 'now' },
  { id: 'hourly', name: 'Hourly', icon: 'hourly' },
  { id: 'radar', name: 'Radar', icon: 'radar', full: true },
  { id: 'outlooks', name: 'Outlooks', icon: 'outlooks', full: true },
  { id: 'alerts', name: 'Alerts', icon: 'alerts' },
  { id: 'discussion', name: 'Discussion', icon: 'discussion' },
];

const $ = (id) => document.getElementById(id);
const media = window.matchMedia('(prefers-color-scheme: light)');
const inited = { radar: false, outlooks: false };
let version = '';
let loadedAt = 0;
let loadSeq = 0;
let keyNudged = false;

// ------------------------------------------------------------------ theme

function applyTheme() {
  const pref = state.settings.theme;
  const theme = pref === 'system' ? (media.matches ? 'light' : 'dark') : pref;
  document.documentElement.dataset.theme = theme;
}

media.addEventListener('change', () => {
  if (state.settings.theme === 'system') {
    applyTheme();
    emit('settings', { theme: 'system' });
  }
});

// ------------------------------------------------------------------ routing

function buildRail() {
  const top = $('rail-views');
  for (const v of VIEWS) {
    top.append(el('button', {
      class: 'rail-btn', dataset: { view: v.id }, 'aria-label': v.name,
      onclick: () => go(v.id),
    }, el('span', { class: 'rail-ic', html: uiIcon(v.icon, 22) }), el('span', { class: 'rail-label' }, v.name),
    v.id === 'alerts' ? el('span', { class: 'badge', id: 'alert-badge', hidden: true }) : null));
  }
  $('rail-settings').replaceChildren(el('span', { class: 'rail-ic', html: uiIcon('settings', 22) }), el('span', { class: 'rail-label' }, 'Settings'));
  $('rail-settings').addEventListener('click', () => openSettings());
}

function go(view) {
  if (!VIEWS.some((v) => v.id === view)) view = 'now';
  const previous = state.view;
  state.view = view;
  document.querySelectorAll('.rail-btn').forEach((b) => b.setAttribute('aria-current', b.dataset.view === view ? 'page' : 'false'));
  document.querySelectorAll('.view').forEach((v) => { v.hidden = v.id !== `view-${view}`; });
  $('views').dataset.full = String(!!VIEWS.find((v) => v.id === view).full);
  if (previous === 'radar' && view !== 'radar') hideRadar();
  render(view);
}

function render(view = state.view) {
  const root = $(`view-${view}`);
  if (view === 'now') renderNow(root);
  if (view === 'hourly') renderHourly(root, state.data && state.data.forecast ? currentHourIndex(state.data.forecast) : 0);
  if (view === 'alerts') renderAlerts(root);
  if (view === 'discussion') renderDiscussion(root);
  if (view === 'radar') {
    if (!inited.radar) { initRadar(root); inited.radar = true; }
    showRadar();
  }
  if (view === 'outlooks') {
    if (!inited.outlooks) { initOutlooks(root); inited.outlooks = true; }
    showOutlooks();
  }
  if ((view === 'radar' || view === 'outlooks') && !keyNudged && needsCartoKey()) {
    keyNudged = true;
    toast('The street maps need a free CARTO key. Add one in Settings, under Maps.');
  }
}

on('go', (view) => go(view));
on('focus', (geometry) => {
  go('radar');
  setTimeout(() => focusGeometry(geometry), 120);
});

// ------------------------------------------------------------------ location and data

function setLocation(loc) {
  state.loc = { name: loc.name, lat: Number(loc.lat), lon: Number(loc.lon) };
  state.data = null;
  state.spc = null;
  saveSettings({ location: state.loc });
  renderTop();
  emit('location', state.loc);
  render();
  loadData();
}

async function loadData() {
  if (!state.loc) return;
  const seq = ++loadSeq;
  const { lat, lon } = state.loc;
  $('refresh').classList.add('spin');
  try {
    const data = await api('/api/weather', { lat, lon });
    if (seq !== loadSeq) return;
    state.data = data;
    if (data.forecast) {
      state.tz = data.forecast.timezone || (data.nws && data.nws.tz) || 'UTC';
      state.offset = data.forecast.utc_offset_seconds || 0;
    }
    loadedAt = Date.now();
    updateBadge();
    emit('data', data);
    render();
    renderTop();
    if (data.nws) loadSpc(seq);
  } catch (err) {
    if (seq === loadSeq) toast(`Could not load weather: ${err.message}`);
  } finally {
    if (seq === loadSeq) $('refresh').classList.remove('spin');
  }
}

async function loadSpc(seq) {
  try {
    const res = await api('/api/spc/point', { lat: state.loc.lat, lon: state.loc.lon });
    if (seq !== loadSeq) return;
    state.spc = res.days;
    emit('spc', res.days);
    if (['now', 'outlooks'].includes(state.view)) render();
  } catch (err) {
    console.warn('spc point', err);
  }
}

async function refreshAlerts() {
  if (!state.loc || !state.data || !state.data.nws) return;
  try {
    const res = await api('/api/alerts', { lat: state.loc.lat, lon: state.loc.lon });
    const before = (state.data.alerts || []).map((a) => a.id).join();
    state.data.alerts = res.alerts;
    updateBadge();
    if (before !== res.alerts.map((a) => a.id).join() && ['now', 'alerts'].includes(state.view)) render();
  } catch (err) {
    console.warn('alerts', err);
  }
}

function updateBadge() {
  const badge = $('alert-badge');
  const alerts = (state.data && state.data.alerts) || [];
  badge.hidden = !alerts.length;
  badge.textContent = String(alerts.length);
  badge.classList.toggle('badge-warn', alerts.some((a) => /warning$/i.test(a.event || '')));
}

// ------------------------------------------------------------------ top bar

function isSaved() {
  return state.loc && state.settings.saved.some((s) => Math.abs(s.lat - state.loc.lat) < 1e-3 && Math.abs(s.lon - state.loc.lon) < 1e-3);
}

function renderTop() {
  $('place').textContent = state.loc ? state.loc.name : 'No location';
  const star = $('star');
  star.hidden = !state.loc;
  star.setAttribute('aria-pressed', String(!!isSaved()));
  star.title = isSaved() ? 'Remove from saved places' : 'Save this place';
  $('updated').textContent = loadedAt ? `Updated ${ago(new Date(loadedAt).toISOString())}` : '';
}

function toggleSaved() {
  if (!state.loc) return;
  const saved = state.settings.saved.slice();
  if (isSaved()) {
    saveSettings({ saved: saved.filter((s) => !(Math.abs(s.lat - state.loc.lat) < 1e-3 && Math.abs(s.lon - state.loc.lon) < 1e-3)) });
  } else {
    saved.push({ ...state.loc });
    saveSettings({ saved });
  }
  renderTop();
}

// ------------------------------------------------------------------ search

let results = [];
let active = -1;

function showResults(list, heading) {
  results = list;
  active = list.length ? 0 : -1;
  const box = $('results');
  box.replaceChildren();
  if (heading) box.append(el('div', { class: 'results-head' }, heading));
  list.forEach((r, i) => {
    box.append(el('button', {
      class: 'result', role: 'option', 'aria-selected': String(i === active), dataset: { i: String(i) },
      onmousedown: (e) => { e.preventDefault(); pick(i); },
    }, el('span', { class: 'result-ic', html: uiIcon(r.saved ? 'star' : 'pin', 16) }), el('span', {}, r.name),
    r.detail ? el('span', { class: 'result-detail' }, r.detail) : null));
  });
  box.hidden = !box.childElementCount;
}

function savedList() {
  return state.settings.saved.map((s) => ({ ...s, saved: true }));
}

const search = debounce(async (q) => {
  if (!q.trim()) { showResults(savedList(), state.settings.saved.length ? 'Saved places' : ''); return; }
  try {
    const res = await api('/api/geocode', { q });
    if ($('search').value !== q) return;
    showResults(res.results || [], res.results && res.results.length ? '' : 'No matches');
    if (!res.results || !res.results.length) $('results').hidden = false;
  } catch (err) {
    showResults([], `Search failed: ${err.message}`);
    $('results').hidden = false;
  }
}, 300);

function pick(i) {
  const r = results[i];
  if (!r) return;
  $('search').value = '';
  $('results').hidden = true;
  $('search').blur();
  setLocation(r);
}

function bindSearch() {
  const input = $('search');
  input.addEventListener('input', () => search(input.value));
  input.addEventListener('focus', () => search(input.value));
  input.addEventListener('blur', () => setTimeout(() => { $('results').hidden = true; }, 120));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!results.length) return;
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + results.length) % results.length;
      $('results').querySelectorAll('.result').forEach((b) => b.setAttribute('aria-selected', String(Number(b.dataset.i) === active)));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (active >= 0) pick(active);
    } else if (e.key === 'Escape') {
      input.blur();
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && !e.target.closest('input, select, textarea')) {
      e.preventDefault();
      input.focus();
    }
  });
  $('place').addEventListener('click', () => input.focus());
  $('star').addEventListener('click', toggleSaved);
  $('refresh').addEventListener('click', () => loadData());
}

// ------------------------------------------------------------------ settings drawer

function seg(label, key, options, after) {
  const group = el('div', { class: 'seg', role: 'group', 'aria-label': label });
  const paint = () => group.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === String(state.settings[key]))));
  for (const [value, text] of options) {
    group.append(el('button', {
      dataset: { v: String(value) },
      onclick: () => { saveSettings({ [key]: value }); paint(); if (after) after(value); },
    }, text));
  }
  paint();
  return el('div', { class: 'set-row' }, el('span', { class: 'set-label' }, label), group);
}

function select(label, key, options, numeric = false) {
  const sel = el('select', { onchange: (e) => saveSettings({ [key]: numeric ? Number(e.target.value) : e.target.value }) },
    ...options.map(([v, t]) => el('option', { value: String(v) }, t)));
  sel.value = String(state.settings[key]);
  return el('label', { class: 'set-row' }, el('span', { class: 'set-label' }, label), sel);
}

function mapKeySection(s) {
  const input = el('input', {
    type: 'text', class: 'text-input', value: s.carto_key || '', placeholder: 'Paste your CARTO key',
    spellcheck: 'false', autocomplete: 'off', 'aria-label': 'CARTO API key',
  });
  const saveKey = () => {
    const value = input.value.trim();
    if (value === String(state.settings.carto_key || '')) return;
    saveSettings({ carto_key: value });
    toast(value ? 'Map key saved. The maps reload with it now.' : 'Map key removed.');
  };
  input.addEventListener('change', saveKey);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { saveKey(); input.blur(); } });
  return [
    select('Map style', 'basemap', [['auto', 'Match theme'], ['dark', 'Dark'], ['light', 'Light'], ['streets', 'Streets'], ['satellite', 'Satellite']]),
    el('label', { class: 'set-block' }, el('span', { class: 'set-label' }, 'CARTO API key'), input),
    el('p', { class: 'muted small' },
      'The dark, light and street maps come from CARTO, which needs a free key. No account, they email it right back. ',
      'Satellite uses Esri and works without one. '),
    el('button', { class: 'link small', onclick: () => openExternal('https://carto.com/basemaps/apikey/') }, 'Get a free CARTO key ', el('span', { html: uiIcon('external', 14) })),
    cacheRow(),
  ];
}

const TILE_CACHE = 'isobar-tiles-v1';

function cacheRow() {
  const row = el('div', { class: 'set-row' });
  if (!('serviceWorker' in navigator) || !window.caches) {
    row.append(el('span', { class: 'muted small' }, 'Map tile cache is not available in this browser.'));
    return row;
  }
  const size = el('span', { class: 'set-label' }, 'Map cache');
  const paint = async () => {
    try {
      const cache = await caches.open(TILE_CACHE);
      const n = (await cache.keys()).length;
      const est = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate() : null;
      const mb = est && est.usage ? Math.round(est.usage / 1048576) : null;
      size.textContent = `Map cache: ${n.toLocaleString()} tiles${mb !== null ? `, about ${mb} MB` : ''}`;
    } catch {
      size.textContent = 'Map cache';
    }
  };
  const clear = el('button', {
    class: 'btn',
    onclick: async () => {
      await caches.delete(TILE_CACHE);
      toast('Map cache cleared. Tiles download fresh from here on.');
      paint();
    },
  }, 'Clear');
  row.append(size, clear);
  paint();
  return row;
}

function renderSettings() {
  const body = $('settings-body');
  body.replaceChildren();
  const s = state.settings;

  const savedBox = el('div', { class: 'saved-list' });
  const paintSaved = () => {
    savedBox.replaceChildren();
    if (!s.saved.length) savedBox.append(el('p', { class: 'muted' }, 'Star a place in the top bar to keep it here.'));
    s.saved.forEach((p, i) => savedBox.append(el('div', { class: 'saved-item' },
      el('button', { class: 'link', onclick: () => { closeSettings(); setLocation(p); } }, p.name),
      el('button', {
        class: 'icon-btn small', 'aria-label': `Remove ${p.name}`, html: uiIcon('close', 16),
        onclick: () => { saveSettings({ saved: s.saved.filter((_, k) => k !== i) }); paintSaved(); renderTop(); },
      }))));
  };
  paintSaved();

  const tray = el('label', { class: 'set-row switch' },
    el('input', { type: 'checkbox', checked: !!s.tray_on_close, onchange: (e) => saveSettings({ tray_on_close: e.target.checked }) }),
    el('span', {}, 'Keep running in the tray when the window closes'));

  const link = (text, url) => el('button', { class: 'link', onclick: () => openExternal(url) }, text);

  body.append(
    el('h3', {}, 'Units'),
    seg('Units', 'units', [['us', 'US'], ['metric', 'Metric']], () => render()),
    seg('Clock', 'clock', [['12', '12 hour'], ['24', '24 hour']], () => render()),
    el('h3', {}, 'Look'),
    seg('Theme', 'theme', [['system', 'System'], ['dark', 'Dark'], ['light', 'Light'], ['midnight', 'Midnight']]),
    select('Open on', 'start_view', VIEWS.map((v) => [v.id, v.name])),
    el('h3', {}, 'Maps'),
    ...mapKeySection(s),
    el('h3', {}, 'Radar'),
    select('Loop length', 'radar_loop_minutes', [[30, '30 minutes'], [60, '1 hour'], [90, '90 minutes'], [120, '2 hours'], [180, '3 hours']], true),
    select('Loop speed', 'radar_speed_ms', [[700, 'Slow'], [450, 'Normal'], [250, 'Fast']], true),
    el('h3', {}, 'Alerts'),
    seg('Notify me', 'notify', [['off', 'Off'], ['warnings', 'Warnings'], ['all', 'All alerts']]),
    el('p', { class: 'muted small' }, 'Desktop notifications for your current place, checked every 2 minutes while Isobar runs.'),
    tray,
    el('h3', {}, 'Saved places'),
    savedBox,
    el('h3', {}, 'About'),
    el('p', { class: 'small' }, `Isobar ${version}. Free software under the GPL-3.0.`),
    el('p', { class: 'small muted' },
      'Forecast and air quality from Open-Meteo. Alerts, observations, forecast text and discussions from the National Weather Service. ',
      'Radar, satellite and HRRR imagery from the Iowa Environmental Mesonet. Outlooks from the Storm Prediction Center. ',
      'Maps from CARTO, OpenStreetMap and Esri.'),
    el('div', { class: 'row gap' },
      link('Source code', 'https://github.com/NullAngst/Isobar'),
      link('Open-Meteo', 'https://open-meteo.com/'),
      link('IEM', 'https://mesonet.agron.iastate.edu/'),
      link('SPC', 'https://www.spc.noaa.gov/')),
  );
}

function openSettings() {
  renderSettings();
  $('settings').hidden = false;
  $('scrim').hidden = false;
  $('rail-settings').setAttribute('aria-expanded', 'true');
  $('settings').querySelector('button').focus();
}

function closeSettings() {
  $('settings').hidden = true;
  $('scrim').hidden = true;
  $('rail-settings').setAttribute('aria-expanded', 'false');
}

on('settings', (patch) => {
  if ('theme' in patch) applyTheme();
  if ('units' in patch || 'clock' in patch) renderTop();
});

// ------------------------------------------------------------------ boot

async function boot() {
  try {
    state.settings = await api('/api/settings');
  } catch (err) {
    document.body.textContent = `Isobar could not start: ${err.message}`;
    return;
  }
  try { version = (await api('/api/version')).version; } catch { version = ''; }

  applyTheme();
  buildRail();
  bindSearch();
  $('settings-close').innerHTML = uiIcon('close', 20);
  $('settings-close').addEventListener('click', closeSettings);
  $('scrim').addEventListener('click', closeSettings);
  $('star').innerHTML = uiIcon('star', 18);
  $('refresh').innerHTML = uiIcon('refresh', 18);
  $('search-ic').innerHTML = uiIcon('search', 18);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('settings').hidden) closeSettings(); });

  window.isobar = { go };

  // Tile cache (web/sw.js). If it fails to register, maps still work, they just
  // lean on the regular browser cache.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch((err) => console.warn('tile cache unavailable', err));
  }

  if (state.settings.location) {
    state.loc = state.settings.location;
    renderTop();
    go(state.settings.start_view || 'now');
    emit('location', state.loc);
    loadData();
  } else {
    renderTop();
    go('now');
    setTimeout(() => $('search').focus(), 200);
  }

  setInterval(() => { if (!document.hidden) loadData(); }, 10 * 60 * 1000);
  setInterval(() => { if (!document.hidden) refreshAlerts(); }, 2 * 60 * 1000);
  setInterval(renderTop, 30 * 1000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - loadedAt > 10 * 60 * 1000) loadData();
  });
}

boot();
