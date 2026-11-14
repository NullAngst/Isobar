// Boot, view routing, location search, refresh loop and the settings drawer.

import { state, api, on, emit, saveSettings, openExternal, toast, el, debounce } from './core.js';
import { uiIcon } from './icons.js';
import { ago, ageText, countdown, instant, temp, haversineKm } from './util.js';
import { renderNow, currentHourIndex } from './now.js';
import { renderForecast } from './hourly.js';
import { initRadar, showRadar, hideRadar, focusGeometry } from './radar.js';
import { needsCartoKey } from './basemap.js';
import { initOutlooks, showOutlooks } from './outlooks.js';
import { renderAlerts, renderDiscussion } from './alerts.js';

const VIEWS = [
  { id: 'now', name: 'Now', icon: 'now' },
  { id: 'forecast', name: 'Forecast', icon: 'hourly' },
  { id: 'radar', name: 'Radar', icon: 'radar', full: true },
  { id: 'outlooks', name: 'Outlooks', icon: 'outlooks', full: true },
  { id: 'alerts', name: 'Alerts', icon: 'alerts' },
  { id: 'discussion', name: 'Discussion', icon: 'discussion' },
];

const THEMES = [
  ['system', 'Match system'], ['dark', 'Dark'], ['light', 'Light'], ['midnight', 'Midnight'],
  ['verdigris', 'Verdigris'], ['kawaii-light', 'Kawaii Light'], ['kawaii-dark', 'Kawaii Dark'],
];
const LIGHT_THEMES = new Set(['light', 'kawaii-light']);

const $ = (id) => document.getElementById(id);
const media = window.matchMedia('(prefers-color-scheme: light)');
const inited = { radar: false, outlooks: false };
let version = '';
let platform = 'desktop';
let hasTray = false;
let loadedAt = 0;
let loadSeq = 0;
let keyNudged = false;
let lastFocus = null;

// Where a position can come from: the Android app, or a real browser in
// --browser mode. Qt WebEngine has no location provider, so it's left out.
const canLocate = () => !!(window.IsobarAndroid && window.IsobarAndroid.requestLocation)
  || (!/QtWebEngine/.test(navigator.userAgent) && 'geolocation' in navigator);

// ------------------------------------------------------------------ theme

function applyTheme() {
  const pref = state.settings.theme;
  const theme = pref === 'system' ? (media.matches ? 'light' : 'dark') : pref;
  document.documentElement.dataset.theme = theme;
  // In the Android app, tint the status and navigation bar areas to match.
  if (window.IsobarAndroid && window.IsobarAndroid.setTheme) {
    const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
    try { window.IsobarAndroid.setTheme(bg, LIGHT_THEMES.has(theme)); } catch { /* bridge missing */ }
  }
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
      class: 'rail-btn', dataset: { view: v.id }, 'aria-label': v.name, title: `${v.name} (${VIEWS.indexOf(v) + 1})`,
      onclick: () => go(v.id),
    }, el('span', { class: 'rail-ic', html: uiIcon(v.icon, 22) }), el('span', { class: 'rail-label' }, v.name),
    v.id === 'alerts' ? el('span', { class: 'badge', id: 'alert-badge', hidden: true }) : null));
  }
  $('rail-settings').replaceChildren(el('span', { class: 'rail-ic', html: uiIcon('settings', 22) }), el('span', { class: 'rail-label' }, 'Settings'));
  $('rail-settings').addEventListener('click', () => openSettings());
}

// Views that were renamed, so old saved settings and links still land somewhere sensible.
const VIEW_ALIASES = { hourly: 'forecast' };

function go(view) {
  view = VIEW_ALIASES[view] || view;
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
  if (view === 'forecast') renderForecast(root, state.data && state.data.forecast ? currentHourIndex(state.data.forecast) : 0);
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

// A redraw caused by new data, not by the user moving around. The scrolling
// views keep their scroll position; the discussion only redraws if the
// office changed, since its text comes from its own request.
let lastOffice = null;

function refreshView(view = state.view) {
  const root = $(`view-${view}`);
  if (view === 'discussion') {
    const office = state.data && state.data.nws ? state.data.nws.office : null;
    if (office === lastOffice && root.childElementCount) return;
    lastOffice = office;
    renderDiscussion(root);
    return;
  }
  if (view === 'now' || view === 'forecast' || view === 'alerts') {
    const scroll = root.scrollTop;
    render(view);
    root.scrollTop = scroll;
    return;
  }
  render(view);
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
  lastOffice = null;
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
    const first = !state.data;
    state.data = data;
    if (data.forecast) {
      state.tz = data.forecast.timezone || (data.nws && data.nws.tz) || 'UTC';
      state.offset = data.forecast.utc_offset_seconds || 0;
    }
    loadedAt = Date.now();
    updateAlertUi();
    emit('data', data);
    if (first) render(); else refreshView();
    renderTop();
    if (data.us) loadSpc(seq);
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
    if (['now', 'outlooks', 'forecast'].includes(state.view)) refreshView();
  } catch (err) {
    console.warn('spc point', err);
  }
}

async function refreshAlerts() {
  if (!state.loc || !state.data || !state.data.us) return;
  // A place change while this is in flight must not paint the old place's alerts onto the new one.
  const seq = loadSeq;
  const data = state.data;
  try {
    const res = await api('/api/alerts', { lat: state.loc.lat, lon: state.loc.lon });
    if (seq !== loadSeq || data !== state.data) return;
    const key = (d) => [...(d.alerts || []).map((a) => a.id), ...(d.mcds || []).map((m) => m.number), d.alerts_ok,
      Math.floor(((d.ages && d.ages.alerts) || 0) / 300)].join();
    const before = key(data);
    // A failed fetch keeps the last list on screen; the health note says it's old.
    if (res.ok) data.alerts = res.alerts || [];
    data.alerts_ok = res.ok;
    data.ages = { ...(data.ages || {}), alerts: res.age };
    data.mcds = res.mcds || [];
    updateAlertUi();
    if (before !== key(data) && ['now', 'alerts'].includes(state.view)) refreshView();
  } catch (err) {
    console.warn('alerts', err);
    data.alerts_ok = false;
    updateAlertUi();
  }
}

// ------------------------------------------------------------------ alert badge, warning strip, title and tray

const isWarning = (a) => /warning$/i.test(a.event || '') || a.tier === 'critical';

function updateAlertUi() {
  updateBadge();
  renderStrip();
  reportStatus();
}

function updateBadge() {
  const badge = $('alert-badge');
  const alerts = (state.data && state.data.alerts) || [];
  const count = alerts.length + ((state.data && state.data.mcds) || []).length;
  badge.hidden = !count;
  badge.textContent = String(count);
  badge.classList.toggle('badge-warn', alerts.some(isWarning));
  badge.classList.toggle('badge-critical', alerts.some((a) => a.tier === 'critical'));
}

// A strip under the top bar on every view, radar included, while a warning
// covers the selected place.
function renderStrip() {
  const strip = $('strip');
  const warnings = ((state.data && state.data.alerts) || []).filter(isWarning);
  if (!warnings.length) { strip.hidden = true; return; }
  const top = warnings[0];
  const name = (top.flags || []).find((f) => f.endsWith('Emergency')) || top.event;
  const soon = countdown(top.expires);
  strip.className = `strip${top.tier === 'critical' ? ' strip-critical' : ''}`;
  strip.style.setProperty('--hz', top.tier === 'critical' ? '#ff2a2a' : 'var(--warn)');
  // replaceChildren writes a null as the text "null", so the gaps are filtered out.
  strip.replaceChildren(...[
    el('span', { html: uiIcon('alerts', 16) }),
    el('strong', {}, name),
    top.expires ? el('span', { class: 'strip-until' }, `until ${instant(top.expires, true)}`) : null,
    soon ? el('span', { class: 'strip-soon', dataset: { until: top.expires } }, soon) : null,
    warnings.length > 1 ? el('span', { class: 'strip-more' }, `+${warnings.length - 1} more`) : null,
    el('span', { class: 'strip-go', html: uiIcon('next', 16) }),
  ].filter(Boolean));
  strip.hidden = false;
}

let lastStatus = '';

function reportStatus() {
  if (platform !== 'desktop') return;
  const d = state.data;
  const alerts = (d && d.alerts) || [];
  const mcds = (d && d.mcds) || [];
  const now = d && d.forecast && d.forecast.current ? temp(d.forecast.current.temperature_2m) : '';
  const place = state.loc ? state.loc.name : '';
  const warnings = alerts.filter(isWarning);
  let level = 'none';
  if (alerts.some((a) => a.tier === 'critical')) level = 'critical';
  else if (warnings.length) level = 'warning';
  else if (alerts.some((a) => /watch$/i.test(a.event || '')) || mcds.length) level = 'watch';
  const head = [place, now].filter(Boolean).join(', ');
  const title = `Isobar${head ? `: ${head}` : ''}${warnings.length ? `, ${warnings[0].event}` : ''}`;
  const lines = [head || 'Isobar'];
  for (const a of alerts.slice(0, 4)) lines.push(`${a.event}${a.expires ? ` until ${instant(a.expires)}` : ''}`);
  if (alerts.length > 4) lines.push(`and ${alerts.length - 4} more`);
  if (!alerts.length && d && d.us) lines.push('No active alerts');
  const status = { title, tooltip: lines.join('\n'), level };
  const key = JSON.stringify(status);
  if (key === lastStatus) return;
  lastStatus = key;
  api('/api/status', {}, { method: 'POST', body: key }).catch(() => { lastStatus = ''; });
}

// Rewrites every "expires in N min" on screen.
function tickCountdowns() {
  document.querySelectorAll('[data-until]').forEach((node) => {
    const text = countdown(node.dataset.until);
    node.textContent = text;
    node.hidden = !text;
  });
}

// ------------------------------------------------------------------ top bar

function isSaved() {
  return state.loc && state.settings.saved.some((s) => Math.abs(s.lat - state.loc.lat) < 1e-3 && Math.abs(s.lon - state.loc.lon) < 1e-3);
}

const FORECAST_STALE_S = 30 * 60;

function renderTop() {
  $('place').textContent = state.loc ? state.loc.name : 'No location';
  const star = $('star');
  star.hidden = !state.loc;
  star.setAttribute('aria-pressed', String(!!isSaved()));
  star.title = isSaved() ? 'Remove from saved places' : 'Save this place';
  // "Updated" is when the forecast was last confirmed by Open-Meteo, not when
  // the page last asked, so an old copy standing in shows as old.
  const updated = $('updated');
  const age = state.data && state.data.ages && state.data.ages.forecast;
  const seconds = age ? Date.now() / 1000 - age : null;
  updated.classList.toggle('stale', seconds !== null && seconds > FORECAST_STALE_S);
  if (seconds !== null && seconds > FORECAST_STALE_S) {
    updated.textContent = `Forecast from ${ageText(seconds)} ago`;
    updated.title = 'Open-Meteo is not answering, so this is the last forecast Isobar got.';
  } else {
    updated.textContent = loadedAt ? `Updated ${ago(new Date(loadedAt).toISOString())}` : '';
    updated.title = '';
  }
  reportStatus();
}

function toggleSaved() {
  if (!state.loc) return;
  const saved = state.settings.saved.slice();
  if (isSaved()) {
    saveSettings({ saved: saved.filter((s) => !(Math.abs(s.lat - state.loc.lat) < 1e-3 && Math.abs(s.lon - state.loc.lon) < 1e-3)) });
  } else {
    saved.push({ ...state.loc, notify: false });
    saveSettings({ saved });
  }
  renderTop();
}

// ------------------------------------------------------------------ device location

let locateWaiter = null;

// Called by the Android app with a fix, or with an error message.
function onLocation(lat, lon, error) {
  const waiter = locateWaiter;
  locateWaiter = null;
  if (!waiter) return;
  if (error || !Number.isFinite(lat) || !Number.isFinite(lon)) waiter.reject(new Error(error || 'No location'));
  else waiter.resolve({ lat, lon });
}

function devicePosition() {
  return new Promise((resolve, reject) => {
    if (window.IsobarAndroid && window.IsobarAndroid.requestLocation) {
      locateWaiter = { resolve, reject };
      setTimeout(() => { if (locateWaiter && locateWaiter.resolve === resolve) onLocation(NaN, NaN, 'Timed out'); }, 30000);
      try { window.IsobarAndroid.requestLocation(); } catch (err) { onLocation(NaN, NaN, err.message); }
      return;
    }
    if (!('geolocation' in navigator)) { reject(new Error('Location is not available here')); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      (err) => reject(new Error(err.message || 'Location denied')),
      { timeout: 20000, maximumAge: 5 * 60000 });
  });
}

async function placeFromPosition({ lat, lon }) {
  let name = `${lat.toFixed(3)}, ${lon.toFixed(3)}`;
  try {
    const res = await api('/api/reverse', { lat, lon });
    if (res && res.name) name = res.name;
  } catch { /* coordinates as the name */ }
  return { name, lat, lon };
}

async function useMyLocation() {
  toast('Finding your location');
  try {
    setLocation(await placeFromPosition(await devicePosition()));
  } catch (err) {
    toast(`Could not get your location: ${err.message}`);
  }
}

// "Follow my location": when the app comes to the front, move the place to
// where the phone is if it has moved more than a few km.
async function followLocation() {
  if (!state.settings.follow_location || !canLocate()) return;
  try {
    const pos = await devicePosition();
    if (state.loc && haversineKm(state.loc.lat, state.loc.lon, pos.lat, pos.lon) < 3) return;
    setLocation(await placeFromPosition(pos));
  } catch (err) {
    console.warn('follow location', err);
  }
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
    }, el('span', { class: 'result-ic', html: uiIcon(r.locate ? 'locate' : r.saved ? 'star' : 'pin', 16) }), el('span', {}, r.name),
    r.detail ? el('span', { class: 'result-detail' }, r.detail) : null));
  });
  box.hidden = !box.childElementCount;
}

function savedList() {
  const out = canLocate() ? [{ name: 'Use my location', locate: true }] : [];
  return out.concat(state.settings.saved.map((s) => ({ ...s, saved: true })));
}

const search = debounce(async (q) => {
  if (!q.trim()) { showResults(savedList(), state.settings.saved.length ? 'Saved places' : ''); return; }
  if (q.trim().length < 2) return;
  try {
    const res = await api('/api/geocode', { q });
    if ($('search').value !== q) return;
    showResults(res.results || [], res.results && res.results.length ? '' : 'No matches');
    if (!res.results || !res.results.length) $('results').hidden = false;
  } catch (err) {
    showResults([], `Search failed: ${err.message}`);
    $('results').hidden = false;
  }
}, 400);

async function pick(i) {
  const r = results[i];
  if (!r) return;
  $('search').value = '';
  $('results').hidden = true;
  $('search').blur();
  if (r.locate) { useMyLocation(); return; }
  if (r.reverse) {
    // Typed coordinates get a place name once, when picked, not per keystroke.
    try {
      const res = await api('/api/reverse', { lat: r.lat, lon: r.lon });
      if (res && res.name) r.name = res.name;
    } catch { /* keep the coordinates as the name */ }
  }
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
    if (e.target.closest('input, select, textarea') || e.ctrlKey || e.metaKey || e.altKey) return;
    if (!$('keys').hidden || !$('settings').hidden) return;
    if (e.key === '?') {
      e.preventDefault();
      openKeys();
      return;
    }
    if (e.key === '/') {
      e.preventDefault();
      input.focus();
      return;
    }
    if (e.key === 'r' && !e.shiftKey) {
      loadData();
      return;
    }
    // 1 to 6 jump straight to a view, in rail order.
    const n = Number(e.key);
    if (Number.isInteger(n) && n >= 1 && n <= VIEWS.length) {
      e.preventDefault();
      go(VIEWS[n - 1].id);
    }
  });
  $('place').addEventListener('click', () => input.focus());
  $('star').addEventListener('click', toggleSaved);
  $('refresh').addEventListener('click', () => loadData());
  $('strip').addEventListener('click', () => go('alerts'));
}

// ------------------------------------------------------------------ dialogs: focus handling

// Tab and Shift+Tab stay inside an open drawer or dialog.
function trapFocus(container, e) {
  if (e.key !== 'Tab') return;
  const items = [...container.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((n) => !n.disabled && n.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

// ------------------------------------------------------------------ keyboard shortcuts

const SHORTCUTS = [
  ['1 to 6', 'Now, Forecast, Radar, Outlooks, Alerts, Discussion'],
  ['/', 'Search for a place'],
  ['r', 'Refresh'],
  ['?', 'This list'],
  ['Esc', 'Close a panel, a dialog or the search'],
  ['Space', 'Radar: play or pause the loop'],
  ['Left, Right', 'Radar: step one frame'],
  ['+ and -', 'Maps: zoom in and out'],
  ['Arrow keys', 'Maps: pan, when the map has focus'],
];

function openKeys() {
  lastFocus = document.activeElement;
  const box = $('keys');
  const close = el('button', { class: 'icon-btn', 'aria-label': 'Close', html: uiIcon('close', 18), onclick: closeKeys });
  box.replaceChildren(el('div', { class: 'keys-card' },
    el('div', { class: 'drawer-head keys-head' }, el('h2', { id: 'keys-title' }, 'Keyboard shortcuts'), close),
    el('dl', { class: 'keys-list' }, ...SHORTCUTS.flatMap(([k, what]) => [el('dt', {}, el('kbd', {}, k)), el('dd', {}, what)]))));
  box.hidden = false;
  close.focus();
}

function closeKeys() {
  $('keys').hidden = true;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
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

function select(label, key, options, numeric = false, after = null) {
  const sel = el('select', {
    onchange: (e) => {
      const value = numeric ? Number(e.target.value) : e.target.value;
      saveSettings({ [key]: value });
      if (after) after(value);
    },
  }, ...options.map(([v, t]) => el('option', { value: String(v) }, t)));
  sel.value = String(state.settings[key]);
  return el('label', { class: 'set-row' }, el('span', { class: 'set-label' }, label), sel);
}

function checkbox(key, text, after = null) {
  return el('label', { class: 'set-row switch' },
    el('input', {
      type: 'checkbox', checked: !!state.settings[key],
      onchange: (e) => { saveSettings({ [key]: e.target.checked }); if (after) after(e.target.checked); },
    }),
    el('span', {}, text));
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
    select('Map style', 'basemap', [['satellite', 'Satellite'], ['auto', 'Match theme'], ['dark', 'Dark'], ['light', 'Light'], ['streets', 'Streets']]),
    el('label', { class: 'set-block' }, el('span', { class: 'set-label' }, 'CARTO API key'), input),
    el('p', { class: 'muted small' },
      'Satellite is the default and needs nothing. The dark, light, street and match-theme maps come from CARTO, which needs a free key. No account, they email it right back. '),
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

function hourOptions(withOff) {
  const h24 = state.settings.clock === '24';
  const list = Array.from({ length: 24 }, (_, h) => [h, h24 ? `${String(h).padStart(2, '0')}:00` : `${h % 12 || 12} ${h < 12 ? 'am' : 'pm'}`]);
  return withOff ? [[-1, 'Off'], ...list] : list;
}

function alertsSection() {
  const android = platform === 'android';
  const quietEnd = select('Until', 'quiet_end', hourOptions(false), true);
  quietEnd.hidden = state.settings.quiet_start < 0;
  const quietStart = select('Quiet hours', 'quiet_start', hourOptions(true), true, (v) => { quietEnd.hidden = v < 0; });
  const out = [
    el('h3', {}, 'Alerts'),
    seg('Notify me', 'notify', [['off', 'Off'], ['warnings', 'Warnings'], ['watches', 'Watches too'], ['all', 'All alerts']]),
    el('p', { class: 'muted small' },
      android
        ? 'Notifications for your place and any saved place with its bell on, checked about every 15 minutes in the background. '
        : 'Notifications for your place and any saved place with its bell on, checked every 2 minutes while Isobar runs. ',
      '"Watches too" adds watches and SPC mesoscale discussions. Updates to an alert you already got stay quiet unless it gets worse.'),
    quietStart,
    quietEnd,
    el('p', { class: 'muted small' }, 'During quiet hours only tornado and extreme wind warnings, emergencies and PDS warnings come through.'),
    checkbox('critical_sound', android
      ? 'Tornado warnings and emergencies use the loud, high-priority channel'
      : 'Play a sound for tornado warnings and emergencies, and keep them on screen'),
  ];
  if (android) {
    if (canLocate()) {
      out.push(checkbox('follow_location', 'Follow my location: move to where the phone is each time Isobar opens', (on) => { if (on) followLocation(); }));
    }
    if (window.IsobarAndroid && window.IsobarAndroid.openNotificationSettings) {
      out.push(el('button', { class: 'link small', onclick: () => window.IsobarAndroid.openNotificationSettings() }, 'Android notification settings'));
    }
  } else {
    if (hasTray) out.push(checkbox('tray_on_close', 'Keep running in the tray when the window closes'));
    out.push(checkbox('autostart', 'Start at login, hidden in the tray'));
  }
  return out;
}

function renderSettings() {
  const body = $('settings-body');
  body.replaceChildren();
  const s = state.settings;

  const savedBox = el('div', { class: 'saved-list' });
  const paintSaved = () => {
    savedBox.replaceChildren();
    if (!s.saved.length) savedBox.append(el('p', { class: 'muted' }, 'Star a place in the top bar to keep it here.'));
    s.saved.forEach((p, i) => {
      const bell = el('button', {
        class: `icon-btn small bell${p.notify ? ' on' : ''}`, 'aria-pressed': String(!!p.notify),
        'aria-label': `Notify me about alerts for ${p.name}`, title: p.notify ? 'Notifications on for this place' : 'Notifications off for this place',
        html: uiIcon('bell', 16),
        onclick: () => {
          saveSettings({ saved: s.saved.map((q, k) => (k === i ? { ...q, notify: !q.notify } : q)) });
          paintSaved();
        },
      });
      savedBox.append(el('div', { class: 'saved-item' },
        el('button', { class: 'link', onclick: () => { closeSettings(); setLocation(p); } }, p.name),
        el('span', { class: 'row' }, bell, el('button', {
          class: 'icon-btn small', 'aria-label': `Remove ${p.name}`, html: uiIcon('close', 16),
          onclick: () => { saveSettings({ saved: s.saved.filter((_, k) => k !== i) }); paintSaved(); renderTop(); },
        }))));
    });
  };
  paintSaved();

  const link = (text, url) => el('button', { class: 'link', onclick: () => openExternal(url) }, text);

  body.append(
    el('h3', {}, 'Units'),
    seg('Units', 'units', [['us', 'US'], ['metric', 'Metric']], () => refreshView()),
    seg('Clock', 'clock', [['12', '12 hour'], ['24', '24 hour']], () => { refreshView(); renderSettings(); }),
    el('h3', {}, 'Look'),
    select('Theme', 'theme', THEMES),
    select('Open on', 'start_view', VIEWS.map((v) => [v.id, v.name])),
    el('h3', {}, 'Maps'),
    ...mapKeySection(s),
    el('h3', {}, 'Radar'),
    select('Loop length', 'radar_loop_minutes', [[30, '30 minutes'], [60, '1 hour'], [90, '90 minutes'], [120, '2 hours'], [180, '3 hours']], true),
    select('Loop speed', 'radar_speed_ms', [[700, 'Slow'], [450, 'Normal'], [250, 'Fast']], true),
    ...alertsSection(),
    el('h3', {}, 'Saved places'),
    savedBox,
    el('p', { class: 'muted small' }, 'The bell turns on notifications for that place too.'),
    el('h3', {}, 'About'),
    el('p', { class: 'small' }, `Isobar ${version}. Free software under the GPL-3.0.`),
    el('p', { class: 'small muted' },
      'Forecast and air quality from Open-Meteo. Alerts, observations, forecast text and discussions from the National Weather Service. ',
      'Radar, storm attributes and HRRR imagery from the Iowa Environmental Mesonet. Satellite loops from NOAA NESDIS. ',
      'Outlooks from the Storm Prediction Center, tropical cones from the National Hurricane Center. ',
      'Maps from CARTO, OpenStreetMap and Esri.'),
    el('div', { class: 'row gap' },
      el('button', { class: 'link', onclick: () => { closeSettings(); openKeys(); } }, 'Keyboard shortcuts'),
      link('Source code', 'https://github.com/NullAngst/Isobar'),
      link('Open-Meteo', 'https://open-meteo.com/'),
      link('IEM', 'https://mesonet.agron.iastate.edu/'),
      link('SPC', 'https://www.spc.noaa.gov/')),
  );
}

function openSettings() {
  lastFocus = document.activeElement;
  renderSettings();
  $('settings').hidden = false;
  $('scrim').hidden = false;
  $('rail-settings').setAttribute('aria-expanded', 'true');
  $('settings').querySelector('button').focus();
}

function closeSettings() {
  if ($('settings').hidden) return;
  $('settings').hidden = true;
  $('scrim').hidden = true;
  $('rail-settings').setAttribute('aria-expanded', 'false');
  if (lastFocus && lastFocus.focus && document.contains(lastFocus)) lastFocus.focus();
}

on('settings', (patch) => {
  if ('theme' in patch) applyTheme();
  if ('units' in patch || 'clock' in patch) renderTop();
  if (('units' in patch || 'clock' in patch) && !$('strip').hidden) renderStrip();
  if ('autostart' in patch && !$('settings').hidden) renderSettings();
});

// ------------------------------------------------------------------ boot

async function boot() {
  try {
    state.settings = await api('/api/settings');
    if (state.settings.start_view === 'hourly') state.settings.start_view = 'forecast';
  } catch (err) {
    document.body.textContent = `Isobar could not start: ${err.message}`;
    return;
  }
  try {
    const v = await api('/api/version');
    version = v.version;
    platform = v.platform || 'desktop';
    hasTray = !!v.tray;
  } catch { version = ''; }
  document.documentElement.dataset.platform = platform;

  applyTheme();
  buildRail();
  bindSearch();
  $('settings-close').innerHTML = uiIcon('close', 20);
  $('settings-close').addEventListener('click', closeSettings);
  $('scrim').addEventListener('click', closeSettings);
  $('star').innerHTML = uiIcon('star', 18);
  $('refresh').innerHTML = uiIcon('refresh', 18);
  $('search-ic').innerHTML = uiIcon('search', 18);
  $('settings').addEventListener('keydown', (e) => trapFocus($('settings'), e));
  $('keys').addEventListener('keydown', (e) => trapFocus($('keys'), e));
  $('keys').addEventListener('click', (e) => { if (e.target === $('keys')) closeKeys(); });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('keys').hidden) closeKeys();
    else if (!$('settings').hidden) closeSettings();
  });

  window.isobar = {
    go,
    onLocation,
    back() {
      if (!$('keys').hidden) { closeKeys(); return true; }
      if (!$('settings').hidden) { closeSettings(); return true; }
      if (!$('results').hidden) { $('results').hidden = true; $('search').blur(); return true; }
      const open = document.querySelector('.collapsible.open');
      if (open) { open.classList.remove('open'); return true; }
      if (state.view !== 'now') { go('now'); return true; }
      return false;
    },
    resumed() {
      if (Date.now() - loadedAt > 5 * 60 * 1000) loadData();
      followLocation();
    },
  };

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
  followLocation();

  setInterval(() => { if (!document.hidden) loadData(); }, 10 * 60 * 1000);
  // Alerts keep refreshing while the window is hidden in the tray, so the
  // title, tray tooltip and tray icon stay current.
  setInterval(() => { if (!document.hidden || platform === 'desktop') refreshAlerts(); }, 2 * 60 * 1000);
  setInterval(() => { renderTop(); tickCountdowns(); }, 30 * 1000);
  let lastWidth = window.innerWidth;
  window.addEventListener('resize', debounce(() => {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    if (state.view === 'now' || state.view === 'forecast') refreshView();
  }, 250));
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - loadedAt > 10 * 60 * 1000) loadData();
  });
}

boot();
