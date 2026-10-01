// Radar view.
//
// Radar imagery comes from the Iowa Environmental Mesonet tile service:
//   ridge::USCOMP-N0Q-<stamp>   national mosaic, every 5 minutes
//   ridge::<SITE>-<PROD>-<stamp> single radar Level III products
// Reflectivity tiles are decoded back to dBZ in the browser and repainted
// with the selected palette and clutter cutoff.

import { state, api, IEM, proxyUrl, on, saveSettings, toast, el, debounce } from './core.js';
import { createMap, applyBasemap, setBorders, locationMarker, BASEMAPS } from './basemap.js';
import { PALETTES, paletteLUT, colorIndex, dbzToIndex } from './ramps.js';
import { hazardColor, hazardName, instant, ago, tempRGB, windRGB, tempVal, windVal, windUnit, SPC_COLORS } from './util.js';
import { uiIcon } from './icons.js';

const TILE = (layer, cache = 'c') => `https://mesonet{s}.agron.iastate.edu/${cache}/tile.py/1.0.0/${layer}/{z}/{x}/{y}.png`;

const PRODUCTS = [
  { id: 'refl', name: 'Reflectivity', hint: 'Precipitation intensity', mosaic: true, siteProduct: 'N0B', recolor: true },
  { id: 'vel', name: 'Velocity', hint: 'Green is toward the radar, red is away', siteProduct: 'N0U', needsSite: true, legendImg: `${IEM}/GIS/legends/N0U.gif` },
  { id: 'srv', name: 'Rotation', hint: 'Storm-relative velocity. Tight red and green couplets side by side mark rotation.', siteProduct: 'N0S', needsSite: true, legendImg: `${IEM}/GIS/legends/N0S.gif` },
  { id: 'tops', name: 'Echo tops', hint: 'How high the storms reach', mosaic: true, siteProduct: 'NET', legendImg: `${IEM}/GIS/legends/NET.gif` },
  {
    id: 'rain', name: 'Rainfall', hint: 'Radar-estimated totals (MRMS)', noSite: true,
    options: [['q2-n1p', '1 h'], ['q2-p24h', '24 h'], ['q2-p48h', '48 h'], ['q2-p72h', '72 h']],
  },
  { id: 'future', name: 'Future radar', hint: 'HRRR model forecast, colored by precipitation type', noSite: true },
  { id: 'sat', name: 'Satellite', hint: 'GOES imagery', noSite: true, options: [['13', 'Infrared'], ['02', 'Visible'], ['09', 'Water vapor']] },
  { id: 'wind', name: 'Wind', hint: 'Current model wind from Open-Meteo, not observations', field: 'wind' },
  { id: 'temp', name: 'Temperature', hint: 'Current model temperature from Open-Meteo, not observations', field: 'temp' },
];

const RAIN_LEGEND = { 'q2-n1p': `${IEM}/images/mrms_q3_p1h.png` };
const RAIN_LEGEND_LONG = `${IEM}/images/mrms_q3_p24h.png`;

let map = null;
let root = null;
let ui = {};
let frames = [];
let layers = [];
let index = 0;
let playing = false;
let playTimer = null;
let loading = new Set();
let sites = [];
let sitesLayer = null;
let wwaLayers = { Y: null, A: null, W: null };
let outlookLayer = null;
let fieldLayer = null;
let highlight = null;
let marker = null;
let buildToken = 0;
let lastBuild = 0;
let built = false;

const product = () => PRODUCTS.find((p) => p.id === state.settings.radar_product) || PRODUCTS[0];
const option = (p) => (state.settings.radar_options || {})[p.id] || (p.options ? p.options[0][0] : null);

// ------------------------------------------------------------------ recolor tile layer

function paintTile(canvas, img) {
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  let data;
  try {
    data = ctx.getImageData(0, 0, canvas.width, canvas.height);
  } catch {
    return; // cross-origin tile we cannot read: leave it as drawn
  }
  const d = data.data;
  const lut = paletteLUT(state.settings.radar_palette);
  const minIdx = dbzToIndex(Number(state.settings.radar_min_dbz ?? -32));
  let colored = 0;
  let exactHits = 0;
  for (let i = 0; i < d.length; i += 4) {
    const a = d[i + 3];
    if (!a) continue;
    colored++;
    let idx = colorIndex(d[i], d[i + 1], d[i + 2]);
    if (idx >= 1000) idx -= 1000;
    else if (idx >= 0) exactHits++;
    if (idx < 0 || idx < minIdx) {
      d[i + 3] = 0;
      continue;
    }
    const o = idx * 4;
    d[i] = lut[o];
    d[i + 1] = lut[o + 1];
    d[i + 2] = lut[o + 2];
    d[i + 3] = (lut[o + 3] * a) / 255;
  }
  // If most pixels did not match the table exactly, this is not the imagery we
  // expect. Leave the original colors rather than paint nonsense.
  if (colored > 64 && exactHits / colored < 0.5) return;
  ctx.putImageData(data, 0, 0);
}

const RecolorLayer = L.GridLayer.extend({
  initialize(url, options) {
    this._url = url;
    L.GridLayer.prototype.initialize.call(this, options);
  },
  tileUrl(coords) {
    const subs = this.options.subdomains;
    return L.Util.template(this._url, { s: subs[Math.abs(coords.x + coords.y) % subs.length], x: coords.x, y: coords.y, z: coords.z });
  },
  createTile(coords, done) {
    const size = this.getTileSize();
    const canvas = document.createElement('canvas');
    canvas.width = size.x;
    canvas.height = size.y;
    const img = new Image();
    img.crossOrigin = 'anonymous';
    const url = this.tileUrl(coords);
    let proxied = false;
    img.onload = () => {
      paintTile(canvas, img);
      done(null, canvas);
    };
    img.onerror = () => {
      if (!proxied) {
        proxied = true;
        img.src = proxyUrl(url);
      } else {
        done(null, canvas);
      }
    };
    img.src = url;
    return canvas;
  },
});

// ------------------------------------------------------------------ model field layer (wind / temperature)

const FieldLayer = L.Layer.extend({
  onAdd(m) {
    this._map = m;
    this._canvas = L.DomUtil.create('canvas', 'field-canvas');
    m.getPane('field').appendChild(this._canvas);
    m.on('moveend resize', this._reset, this);
    m.on('zoomstart', this._hide, this);
    this._reset();
  },
  onRemove(m) {
    m.off('moveend resize', this._reset, this);
    m.off('zoomstart', this._hide, this);
    this._canvas.remove();
  },
  setData(grid, kind) {
    this._grid = grid;
    this._kind = kind;
    this._draw();
  },
  _hide() {
    this._canvas.style.visibility = 'hidden';
  },
  _reset() {
    const size = this._map.getSize();
    L.DomUtil.setPosition(this._canvas, this._map.containerPointToLayerPoint([0, 0]));
    this._canvas.width = size.x;
    this._canvas.height = size.y;
    this._canvas.style.visibility = 'visible';
    this._draw();
  },
  _sample(lat, lon, arr) {
    const g = this._grid;
    const ny = g.lats.length;
    const nx = g.lons.length;
    const fi = (lat - g.lats[0]) / (g.lats[ny - 1] - g.lats[0]) * (ny - 1);
    const fj = (lon - g.lons[0]) / (g.lons[nx - 1] - g.lons[0]) * (nx - 1);
    if (!(fi >= 0 && fj >= 0 && fi <= ny - 1 && fj <= nx - 1)) return null;
    const i0 = Math.min(Math.floor(fi), ny - 2);
    const j0 = Math.min(Math.floor(fj), nx - 2);
    const ti = fi - i0;
    const tj = fj - j0;
    const at = (i, j) => arr[i * nx + j];
    const v00 = at(i0, j0), v01 = at(i0, j0 + 1), v10 = at(i0 + 1, j0), v11 = at(i0 + 1, j0 + 1);
    if ([v00, v01, v10, v11].some((v) => v === null || v === undefined)) return null;
    return (v00 * (1 - tj) + v01 * tj) * (1 - ti) + (v10 * (1 - tj) + v11 * tj) * ti;
  },
  _draw() {
    const g = this._grid;
    const ctx = this._canvas.getContext('2d');
    ctx.clearRect(0, 0, this._canvas.width, this._canvas.height);
    if (!g || !g.lats || g.lats.length < 2 || g.lons.length < 2) return;
    const m = this._map;
    const step = 6;
    const w = Math.ceil(this._canvas.width / step);
    const h = Math.ceil(this._canvas.height / step);
    const small = document.createElement('canvas');
    small.width = w;
    small.height = h;
    const sctx = small.getContext('2d');
    const img = sctx.createImageData(w, h);
    const values = this._kind === 'temp' ? g.temp : g.speed;
    const toRGB = this._kind === 'temp' ? tempRGB : windRGB;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const ll = m.containerPointToLatLng([x * step, y * step]);
        let lon = ll.lng;
        while (lon < g.lons[0] - 180) lon += 360;
        while (lon > g.lons[0] + 180) lon -= 360;
        const v = this._sample(ll.lat, lon, values);
        if (v === null) continue;
        const [r, gg, b] = toRGB(v);
        const o = (y * w + x) * 4;
        img.data[o] = r;
        img.data[o + 1] = gg;
        img.data[o + 2] = b;
        img.data[o + 3] = 150;
      }
    }
    sctx.putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(small, 0, 0, w * step, h * step);

    // Values and arrows at grid nodes, skipping nodes that would crowd.
    ctx.font = '600 12px Plex, system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const nx = g.lons.length;
    let lastPts = [];
    g.lats.forEach((lat, i) => {
      g.lons.forEach((lon, j) => {
        const k = i * nx + j;
        const p = m.latLngToContainerPoint([lat, lon]);
        if (p.x < 10 || p.y < 10 || p.x > this._canvas.width - 10 || p.y > this._canvas.height - 10) return;
        if (lastPts.some((q) => Math.abs(q.x - p.x) < 54 && Math.abs(q.y - p.y) < 40)) return;
        lastPts.push(p);
        if (this._kind === 'temp') {
          const t = g.temp[k];
          if (t === null || t === undefined) return;
          label(ctx, `${Math.round(tempVal(t))}°`, p.x, p.y);
        } else {
          const s = g.speed[k];
          const dir = g.dir[k];
          if (s === null || dir === null || s === undefined) return;
          arrow(ctx, p.x, p.y - 8, dir, s);
          label(ctx, `${Math.round(windVal(s))}`, p.x, p.y + 12);
        }
      });
    });
  },
});

function label(ctx, text, x, y) {
  ctx.lineWidth = 3;
  ctx.strokeStyle = 'rgba(10,14,20,0.75)';
  ctx.strokeText(text, x, y);
  ctx.fillStyle = '#ffffff';
  ctx.fillText(text, x, y);
}

function arrow(ctx, x, y, fromDeg, kmh) {
  const len = 9 + Math.min(14, kmh / 4);
  const rad = ((fromDeg + 180) % 360) * Math.PI / 180;
  const dx = Math.sin(rad);
  const dy = -Math.cos(rad);
  const x1 = x - dx * len / 2, y1 = y - dy * len / 2;
  const x2 = x + dx * len / 2, y2 = y + dy * len / 2;
  ctx.lineCap = 'round';
  ctx.lineWidth = 4;
  ctx.strokeStyle = 'rgba(10,14,20,0.6)';
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#ffffff';
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  const hx = -dy, hy = dx;
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - dx * 5 + hx * 3.5, y2 - dy * 5 + hy * 3.5);
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - dx * 5 - hx * 3.5, y2 - dy * 5 - hy * 3.5);
  ctx.stroke();
}

// ------------------------------------------------------------------ sites

function nearestSite() {
  const fromNws = state.data && state.data.nws && state.data.nws.radar;
  if (fromNws) return fromNws;
  if (!state.loc || !sites.length) return null;
  let best = null;
  let bestD = Infinity;
  for (const s of sites) {
    const d = (s.lat - state.loc.lat) ** 2 + ((s.lon - state.loc.lon) * Math.cos(state.loc.lat * Math.PI / 180)) ** 2;
    if (d < bestD) { bestD = d; best = s.id; }
  }
  return bestD < 25 ? best : null;
}

function activeSite() {
  const p = product();
  if (p.noSite || p.field) return 'mosaic';
  let s = state.settings.radar_site || 'mosaic';
  if (s === 'auto') s = nearestSite() || 'mosaic';
  if (p.needsSite && s === 'mosaic') s = nearestSite();
  return s;
}

async function loadSites() {
  try {
    const res = await api('/api/radar/sites');
    sites = res.sites || [];
  } catch {
    sites = [];
  }
  renderSiteInfo();
  renderSitesLayer();
}

function siteById(id) {
  return sites.find((x) => x.id === id) || null;
}

function siteName(id) {
  const x = siteById(id);
  if (!x) return id ? `K${id}` : '';
  return `${x.icao || x.id}${x.name ? ` ${x.name}` : ''}`;
}

function goMosaic() {
  saveSettings({ radar_site: 'mosaic' });
  rebuild();
}

function renderSiteInfo() {
  // Shows which radar is on screen. Picking one happens by clicking its dot.
  const box = ui.site;
  if (!box) return;
  const p = product();
  box.replaceChildren();
  box.hidden = !!(p.noSite || p.field);
  if (box.hidden) return;
  const active = activeSite();
  if (!active || active === 'mosaic') {
    box.append(
      el('div', { class: 'site-now' }, p.needsSite ? 'No radar picked' : 'All radars'),
      el('p', { class: 'hint' }, 'Click a radar dot on the map to see just that radar.'));
    return;
  }
  const chosen = state.settings.radar_site || 'mosaic';
  const auto = chosen === 'auto' || (p.needsSite && chosen === 'mosaic');
  box.append(el('div', { class: 'site-now' },
    el('span', { class: 'site-dot' }), el('span', {}, siteName(active), auto ? el('span', { class: 'muted' }, ', nearest') : null)));
  if (p.mosaic) {
    box.append(el('button', { class: 'link small', onclick: goMosaic }, 'Back to all radars'));
  } else {
    box.append(el('p', { class: 'hint' }, `${p.name} comes from one radar at a time. Click another dot to switch.`));
  }
}

function renderSitesLayer() {
  if (sitesLayer) {
    map.removeLayer(sitesLayer);
    sitesLayer = null;
  }
  if (!sites.length) return;
  const p = product();
  const active = p.noSite || p.field ? null : activeSite();
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#3d8bfd';
  sitesLayer = L.layerGroup(sites.map((x) => {
    const on = x.id === active;
    const mk = L.circleMarker([x.lat, x.lon], {
      pane: 'top', radius: on ? 7 : 5, weight: on ? 4 : 1.5, bubblingMouseEvents: false,
      color: on ? accent : '#ffffff', opacity: on ? 1 : 0.85,
      fillColor: on ? '#ffffff' : '#5b6b7d', fillOpacity: on ? 1 : 0.9,
      className: on ? 'site-marker site-active' : 'site-marker',
    });
    const label = x.icao || x.id;
    if (on) {
      mk.bindTooltip(label, { permanent: true, direction: 'right', offset: [8, 0], className: 'site-label' });
    } else {
      mk.bindTooltip(`${label}${x.name ? ` ${x.name}` : ''}`, { direction: 'top', offset: [0, -6] });
    }
    mk.on('click', () => {
      if (on) {
        // Clicking the radar you're already on goes back to the mosaic when the product has one.
        if (p.mosaic) goMosaic();
        return;
      }
      if (p.noSite || p.field) saveSettings({ radar_product: 'refl' });
      saveSettings({ radar_site: x.id });
      rebuild();
    });
    return mk;
  })).addTo(map);
}

// ------------------------------------------------------------------ frames

function stampUTC(ms) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}${p(d.getUTCHours())}${p(d.getUTCMinutes())}`;
}

async function buildFrames() {
  const p = product();
  const minutes = Number(state.settings.radar_loop_minutes) || 60;
  const site = activeSite();
  const out = [];

  if (p.field) return out;

  if (p.needsSite && !site) {
    toast('No radar near this location. Click a radar dot on the map to pick one.');
    return out;
  }

  if (p.id === 'refl' && site === 'mosaic') {
    const step = 5 * 60000;
    const last = Math.floor((Date.now() - 7 * 60000) / step) * step;
    const n = Math.max(2, Math.round(minutes / 5));
    for (let i = n - 1; i >= 0; i--) {
      const t = last - i * step;
      out.push({ url: TILE(`ridge::USCOMP-N0Q-${stampUTC(t)}`), time: new Date(t).toISOString(), recolor: true });
    }
    return out;
  }

  if (p.id === 'tops' && site === 'mosaic') {
    for (let m = 55; m >= 5; m -= 5) {
      out.push({ url: TILE(`nexrad-eet-m${String(m).padStart(2, '0')}m`, 'cache'), time: new Date(Date.now() - m * 60000).toISOString(), approx: true });
    }
    out.push({ url: TILE('nexrad-eet', 'cache'), time: new Date().toISOString(), approx: true });
    return out;
  }

  if (p.siteProduct) {
    const prod = p.siteProduct;
    try {
      const res = await api('/api/radar/frames', { site, product: prod, minutes });
      for (const f of res.frames || []) {
        out.push({ url: TILE(`ridge::${site}-${prod}-${f.stamp}`), time: f.iso, recolor: !!p.recolor });
      }
    } catch (err) {
      console.warn('frame list failed', err);
    }
    if (!out.length) out.push({ url: TILE(`ridge::${site}-${prod}-0`, 'cache'), time: null, recolor: !!p.recolor });
    return out;
  }

  if (p.id === 'rain') {
    out.push({ url: TILE(option(p), 'cache'), time: null, label: `Last ${p.options.find((o) => o[0] === option(p))[1]}` });
    return out;
  }

  if (p.id === 'future') {
    for (let h = 0; h <= 18; h++) {
      out.push({ url: TILE(`hrrr::REFP-F${String(h * 60).padStart(4, '0')}-0`, 'cache'), time: null, label: h === 0 ? 'Model start' : `+${h} h` });
    }
    return out;
  }

  if (p.id === 'sat') {
    const lon = state.loc ? state.loc.lon : -95;
    const bird = lon < -110 ? 'west' : 'east';
    out.push({ url: TILE(`goes_${bird}_conus_ch${option(p)}`, 'cache'), time: null, label: `GOES ${bird === 'east' ? 'East' : 'West'}` });
    return out;
  }
  return out;
}

function clearFrames() {
  for (const layer of layers) map.removeLayer(layer);
  layers = [];
  frames = [];
  loading.clear();
  updateLoading();
}

function makeLayer(frame) {
  const options = {
    pane: 'radar', opacity: 0, subdomains: '123', maxZoom: 19, maxNativeZoom: 12,
    updateWhenZooming: false, keepBuffer: 1, className: 'radar-tiles', crossOrigin: 'anonymous',
  };
  const layer = frame.recolor ? new RecolorLayer(frame.url, options) : L.tileLayer(frame.url, options);
  layer.on('loading', () => { loading.add(layer); updateLoading(); });
  layer.on('load', () => { loading.delete(layer); updateLoading(); });
  return layer;
}

async function rebuild({ keepIndex = false } = {}) {
  if (!map) return;
  const token = ++buildToken;
  const wasPlaying = playing;
  stop();
  const p = product();
  renderProductPanel();
  renderLegend();
  renderSitesLayer();

  if (p.field) {
    clearFrames();
    renderTimeline();
    ensureField();
    return;
  }
  removeField();

  const next = await buildFrames();
  if (token !== buildToken) return;
  const prevTime = keepIndex && frames[index] ? frames[index].time : null;
  clearFrames();
  frames = next;
  layers = frames.map((f) => makeLayer(f).addTo(map));
  index = frames.length - 1;
  if (prevTime) {
    const k = frames.findIndex((f) => f.time === prevTime);
    if (k >= 0) index = k;
  }
  if (p.id === 'future') index = 0;
  show(index);
  renderTimeline();
  lastBuild = Date.now();
  built = true;
  if (wasPlaying && frames.length > 1) play();
}

function show(i) {
  if (!layers.length) return;
  const opacity = Number(state.settings.radar_opacity) || 0.85;
  layers.forEach((layer, k) => layer.setOpacity(k === i ? opacity : 0));
  index = i;
  const f = frames[i];
  if (ui.label) {
    let text = f.label || '';
    if (f.time) {
      text = instant(f.time);
      if (f.approx) text = `about ${text}`;
      if (i === frames.length - 1) text += `, ${ago(f.time)}`;
    } else if (!text) {
      text = 'Latest scan';
    }
    ui.label.textContent = text;
  }
  if (ui.slider) ui.slider.value = String(i);
}

function play() {
  if (frames.length < 2) return;
  playing = true;
  ui.play.innerHTML = uiIcon('pause', 18);
  ui.play.setAttribute('aria-label', 'Pause');
  const tick = () => {
    if (!playing) return;
    const nextIdx = (index + 1) % frames.length;
    show(nextIdx);
    const speed = Number(state.settings.radar_speed_ms) || 450;
    playTimer = setTimeout(tick, nextIdx === frames.length - 1 ? speed * 3.5 : speed);
  };
  playTimer = setTimeout(tick, 120);
}

function stop() {
  playing = false;
  clearTimeout(playTimer);
  if (ui.play) {
    ui.play.innerHTML = uiIcon('play', 18);
    ui.play.setAttribute('aria-label', 'Play');
  }
}

function step(delta) {
  stop();
  if (frames.length) show((index + delta + frames.length) % frames.length);
}

function updateLoading() {
  if (ui.spinner) ui.spinner.hidden = loading.size === 0;
}

// ------------------------------------------------------------------ fields

async function ensureField() {
  const p = product();
  if (!fieldLayer) fieldLayer = new FieldLayer().addTo(map);
  const b = map.getBounds().pad(0.15);
  const size = map.getSize();
  const nx = Math.max(5, Math.min(10, Math.round(size.x / 140)));
  const ny = Math.max(4, Math.min(8, Math.round(size.y / 140)));
  try {
    const grid = await api('/api/grid', {
      s: b.getSouth().toFixed(2), w: b.getWest().toFixed(2), n: b.getNorth().toFixed(2), e: b.getEast().toFixed(2), nx, ny,
    });
    if (product().field === p.field && fieldLayer) {
      fieldLayer.setData(grid, p.field);
      if (ui.label) ui.label.textContent = grid.time ? `Model time ${instant(`${grid.time}Z`)}` : 'Current model analysis';
    }
  } catch (err) {
    toast(`Could not load ${p.name.toLowerCase()} data: ${err.message}`);
  }
}

function removeField() {
  if (fieldLayer) {
    map.removeLayer(fieldLayer);
    fieldLayer = null;
  }
}

// ------------------------------------------------------------------ warnings, watches, advisories

const WWA_ORDER = { Y: 0, A: 1, W: 2 };
const TOP_PRIORITY = { TO: 5, EW: 4, SV: 3, FF: 3 };

async function refreshWWA() {
  if (!map) return;
  const want = {
    W: state.settings.radar_layers.warnings,
    A: state.settings.radar_layers.watches,
    Y: state.settings.radar_layers.advisories,
  };
  const b = map.getBounds().pad(0.1);
  const z = map.getZoom();
  for (const sig of ['Y', 'A', 'W']) {
    if (!want[sig]) {
      if (wwaLayers[sig]) { map.removeLayer(wwaLayers[sig]); wwaLayers[sig] = null; }
      continue;
    }
    try {
      const fc = await api('/api/wwa', {
        sig, z: Math.round(z),
        s: b.getSouth().toFixed(2), w: b.getWest().toFixed(2), n: b.getNorth().toFixed(2), e: b.getEast().toFixed(2),
      });
      fc.features.sort((a, c) => (TOP_PRIORITY[a.properties.phenom] || 0) - (TOP_PRIORITY[c.properties.phenom] || 0));
      const layer = L.geoJSON(fc, {
        style: (f) => wwaStyle(f.properties),
        onEachFeature: (f, lyr) => lyr.bindPopup(() => wwaPopup(f.properties), { className: 'iso-popup', maxWidth: 300 }),
      });
      if (wwaLayers[sig]) map.removeLayer(wwaLayers[sig]);
      wwaLayers[sig] = layer.addTo(map);
    } catch (err) {
      console.warn('wwa', sig, err);
    }
  }
  for (const sig of ['Y', 'A', 'W']) if (wwaLayers[sig]) wwaLayers[sig].bringToFront();
}

function wwaStyle(p) {
  const color = hazardColor(p.phenom, p.sig);
  if (p.sig === 'W') return { color, weight: p.phenom === 'TO' ? 3.2 : 2.4, fillColor: color, fillOpacity: 0.12, opacity: 1 };
  if (p.sig === 'A') return { color, weight: 2, dashArray: '7 5', fillColor: color, fillOpacity: 0.07, opacity: 0.95 };
  return { color, weight: 1, fillColor: color, fillOpacity: 0.14, opacity: 0.8 };
}

function wwaPopup(p) {
  const name = hazardName(p.phenom, p.sig, p.event);
  const lines = [`<strong style="--c:${hazardColor(p.phenom, p.sig)}" class="pop-title">${name}</strong>`];
  if (p.expires) lines.push(`<span>Until ${instant(p.expires, true)}</span>`);
  if (p.wfo) lines.push(`<span>NWS ${String(p.wfo).replace(/^K/, '')}</span>`);
  return `<div class="pop">${lines.join('')}</div>`;
}

async function refreshOutlook() {
  if (outlookLayer) { map.removeLayer(outlookLayer); outlookLayer = null; }
  if (!state.settings.radar_layers.outlook) return;
  try {
    const o = await api('/api/spc', { day: 1, kind: 'cat' });
    outlookLayer = L.geoJSON({ type: 'FeatureCollection', features: o.features }, {
      pane: 'outlook',
      interactive: false,
      style: (f) => ({
        color: f.properties.stroke || SPC_COLORS[f.properties.label] || '#888',
        weight: 1.2, fillColor: f.properties.fill || SPC_COLORS[f.properties.label] || '#888', fillOpacity: 0.28,
      }),
    }).addTo(map);
  } catch (err) {
    console.warn('outlook overlay', err);
  }
}

// ------------------------------------------------------------------ panels

function renderProductPanel() {
  const p = product();
  ui.products.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.id === p.id)));
  ui.hint.textContent = p.hint || '';
  ui.options.replaceChildren();
  if (p.options) {
    const current = option(p);
    for (const [value, text] of p.options) {
      ui.options.append(el('button', {
        class: 'chip', 'aria-pressed': String(value === current),
        onclick: () => {
          saveSettings({ radar_options: { [p.id]: value } });
          rebuild();
        },
      }, text));
    }
  }
  ui.options.hidden = !p.options;
  renderSiteInfo();
}

function renderLegend() {
  const p = product();
  const box = ui.legend;
  box.replaceChildren();
  box.hidden = false;
  if (p.id === 'refl') {
    box.append(gradientLegend('dBZ', [-10, 75], (v) => {
      const lut = paletteLUT(state.settings.radar_palette);
      const idx = dbzToIndex(v);
      if (v < Number(state.settings.radar_min_dbz ?? -32)) return [0, 0, 0, 0];
      return [lut[idx * 4], lut[idx * 4 + 1], lut[idx * 4 + 2], lut[idx * 4 + 3]];
    }, [0, 20, 35, 50, 65]));
  } else if (p.field === 'temp') {
    const us = state.settings.units === 'us';
    box.append(gradientLegend(us ? '°F' : '°C', [-25, 45], (c) => [...tempRGB(c), 220], us ? [-17.8, 0, 15.6, 32.2] : [-20, 0, 20, 40], (c) => Math.round(tempVal(c))));
  } else if (p.field === 'wind') {
    box.append(gradientLegend(windUnit(), [0, 120], (k) => [...windRGB(k), 220], [0, 32, 64, 96], (k) => Math.round(windVal(k))));
  } else if (p.id === 'rain') {
    box.append(el('img', { src: option(p) === 'q2-n1p' ? RAIN_LEGEND['q2-n1p'] : RAIN_LEGEND_LONG, alt: 'Rainfall legend', class: 'legend-img' }));
  } else if (p.legendImg) {
    box.append(el('img', { src: p.legendImg, alt: `${p.name} legend`, class: 'legend-img' }));
  } else if (p.id === 'future') {
    box.append(el('div', { class: 'legend-note' }, 'Green to red: rain. Blue: snow. Pink: mix. Purple: freezing rain.'));
  } else {
    box.hidden = true;
  }
}

function gradientLegend(unit, [lo, hi], colorAt, ticks, fmtTick = (v) => v) {
  const canvas = el('canvas', { width: 220, height: 10, class: 'legend-bar' });
  const ctx = canvas.getContext('2d');
  for (let x = 0; x < 220; x++) {
    const v = lo + (hi - lo) * (x / 219);
    const [r, g, b, a] = colorAt(v);
    ctx.fillStyle = `rgba(${r},${g},${b},${(a ?? 255) / 255})`;
    ctx.fillRect(x, 0, 1, 10);
  }
  const tickRow = el('div', { class: 'legend-ticks' });
  for (const t of ticks) {
    tickRow.append(el('span', { style: `left:${((t - lo) / (hi - lo)) * 100}%` }, String(fmtTick(t))));
  }
  return el('div', { class: 'legend-grad' }, el('div', { class: 'legend-unit' }, unit), canvas, tickRow);
}

function renderTimeline() {
  const p = product();
  const n = frames.length;
  ui.slider.max = String(Math.max(0, n - 1));
  ui.slider.value = String(index);
  const animated = n > 1;
  ui.play.disabled = !animated;
  ui.prev.disabled = !animated;
  ui.next.disabled = !animated;
  ui.slider.disabled = !animated;
  if (p.field) ui.label.textContent = 'Current model analysis';
}

function buildLayersPanel() {
  const s = state.settings;
  const toggle = (key, text) => el('label', { class: 'switch' },
    el('input', {
      type: 'checkbox', checked: !!s.radar_layers[key],
      onchange: (e) => {
        saveSettings({ radar_layers: { [key]: e.target.checked } });
        if (['warnings', 'watches', 'advisories'].includes(key)) refreshWWA();
        if (key === 'outlook') refreshOutlook();
        if (key === 'counties') setBorders(map, e.target.checked);
      },
    }),
    el('span', {}, text));

  const basemap = el('select', {
    onchange: (e) => { saveSettings({ basemap: e.target.value }); },
  }, el('option', { value: 'auto' }, 'Match theme'), ...Object.entries(BASEMAPS).map(([k, v]) => el('option', { value: k }, v.name)));
  basemap.value = s.basemap || 'auto';
  ui.basemap = basemap;

  const palette = el('select', {
    onchange: (e) => { saveSettings({ radar_palette: e.target.value }); repaint(); },
  }, ...Object.entries(PALETTES).map(([k, v]) => el('option', { value: k }, v.name)));
  palette.value = s.radar_palette in PALETTES ? s.radar_palette : 'default';

  const opacity = el('input', {
    type: 'range', min: '0.3', max: '1', step: '0.05', value: String(s.radar_opacity),
    oninput: (e) => { saveSettings({ radar_opacity: Number(e.target.value) }); show(index); },
  });

  const minDbz = el('input', {
    type: 'range', min: '-30', max: '30', step: '5', value: String(s.radar_min_dbz),
    oninput: (e) => {
      minLabel.textContent = `${e.target.value} dBZ`;
    },
    onchange: (e) => { saveSettings({ radar_min_dbz: Number(e.target.value) }); repaint(); },
  });
  const minLabel = el('span', { class: 'muted' }, `${s.radar_min_dbz} dBZ`);

  return el('div', { class: 'layers-body' },
    el('h3', {}, 'Overlays'),
    toggle('warnings', 'Warnings'),
    toggle('watches', 'Watches'),
    toggle('advisories', 'Advisories'),
    toggle('outlook', 'SPC day 1 outlook'),
    toggle('counties', 'County lines'),
    el('h3', {}, 'Display'),
    el('label', { class: 'field' }, el('span', {}, 'Map'), basemap),
    el('label', { class: 'field' }, el('span', {}, 'Reflectivity colors'), palette),
    el('label', { class: 'field' }, el('span', {}, 'Radar opacity'), opacity),
    el('label', { class: 'field' }, el('span', {}, 'Hide echoes below'), el('div', { class: 'row' }, minDbz, minLabel)),
  );
}

function repaint() {
  // Palette or cutoff changed: redraw reflectivity tiles from the browser cache.
  if (product().recolor) rebuild({ keepIndex: true });
  renderLegend();
}

// ------------------------------------------------------------------ public

export function initRadar(container) {
  root = container;
  const mapEl = el('div', { class: 'radar-map', id: 'radar-map' });

  ui.products = el('div', { class: 'product-list', role: 'group', 'aria-label': 'Radar product' },
    ...PRODUCTS.map((p) => el('button', {
      class: 'product', dataset: { id: p.id }, 'aria-pressed': 'false',
      onclick: () => {
        saveSettings({ radar_product: p.id });
        if (p.needsSite && (state.settings.radar_site === 'mosaic')) {
          const near = nearestSite();
          if (near) toast(`Using ${siteName(near)}, the nearest radar. Click any dot to switch.`);
        }
        rebuild();
      },
    }, p.name)));
  ui.hint = el('p', { class: 'hint' });
  ui.options = el('div', { class: 'chips' });
  ui.site = el('div', { class: 'site-box' });

  const panel = el('div', { class: 'float panel-products' },
    ui.products, ui.options, ui.hint, ui.site);

  const layersBody = buildLayersPanel();
  const layersPanel = el('div', { class: 'float panel-layers', hidden: true }, layersBody);
  const layersBtn = el('button', {
    class: 'float icon-btn btn-layers', 'aria-label': 'Layers and display', title: 'Layers and display',
    html: uiIcon('layers', 20),
    onclick: () => { layersPanel.hidden = !layersPanel.hidden; layersBtn.setAttribute('aria-expanded', String(!layersPanel.hidden)); },
  });
  const homeBtn = el('button', {
    class: 'float icon-btn btn-home', 'aria-label': 'Center on location', title: 'Center on location',
    html: uiIcon('locate', 20),
    onclick: () => { if (state.loc) map.setView([state.loc.lat, state.loc.lon], 8); },
  });

  ui.play = el('button', { class: 'icon-btn', 'aria-label': 'Play', html: uiIcon('play', 18), onclick: () => (playing ? stop() : play()) });
  ui.prev = el('button', { class: 'icon-btn', 'aria-label': 'Previous frame', html: uiIcon('prev', 18), onclick: () => step(-1) });
  ui.next = el('button', { class: 'icon-btn', 'aria-label': 'Next frame', html: uiIcon('next', 18), onclick: () => step(1) });
  ui.slider = el('input', { type: 'range', min: '0', max: '0', value: '0', 'aria-label': 'Frame', oninput: (e) => { stop(); show(Number(e.target.value)); } });
  ui.label = el('span', { class: 'frame-label' });
  ui.spinner = el('span', { class: 'spinner', hidden: true, 'aria-label': 'Loading' });
  const timeline = el('div', { class: 'float timeline' }, ui.prev, ui.play, ui.next, ui.slider, ui.label, ui.spinner);
  ui.legend = el('div', { class: 'float legend' });

  root.append(mapEl, panel, layersBtn, homeBtn, layersPanel, timeline, ui.legend);

  map = createMap(mapEl);
  const start = state.loc ? [state.loc.lat, state.loc.lon] : [38.5, -96];
  map.setView(start, state.loc ? 7 : 4);
  setBorders(map, state.settings.radar_layers.counties);

  map.on('moveend', debounce(() => {
    refreshWWA();
    if (product().field) ensureField();
  }, 450));

  on('location', () => {
    if (!map) return;
    if (marker) map.removeLayer(marker);
    marker = locationMarker([state.loc.lat, state.loc.lon]).addTo(map);
    map.setView([state.loc.lat, state.loc.lon], Math.max(map.getZoom(), 7));
    renderSiteInfo();
    if (state.view === 'radar') rebuild();
    else built = false;
  });
  on('data', () => {
    renderSiteInfo();
    if (state.settings.radar_site === 'auto' || product().needsSite) {
      if (state.view === 'radar') rebuild({ keepIndex: true });
      else built = false;
    }
  });
  on('settings', (patch) => {
    if ('basemap' in patch || 'theme' in patch || 'carto_key' in patch) applyBasemap(map);
    if ('basemap' in patch && ui.basemap) ui.basemap.value = state.settings.basemap || 'auto';
    if ('theme' in patch) renderSitesLayer();
    if ('units' in patch && product().field) { renderLegend(); if (fieldLayer) fieldLayer._draw(); }
  });

  document.addEventListener('keydown', (e) => {
    if (state.view !== 'radar' || e.target.closest('input, select, textarea')) return;
    if (e.key === ' ') { e.preventDefault(); playing ? stop() : play(); }
    if (e.key === 'ArrowLeft') step(-1);
    if (e.key === 'ArrowRight') step(1);
  });

  setInterval(() => {
    if (state.view !== 'radar' || document.hidden) return;
    if (Date.now() - lastBuild > 120000 && !product().field) rebuild({ keepIndex: !playing });
    refreshWWA();
  }, 60000);

  if (state.loc) marker = locationMarker([state.loc.lat, state.loc.lon]).addTo(map);
  loadSites();
}

export function showRadar() {
  if (!map) return;
  setTimeout(() => {
    map.invalidateSize();
    if (!built || Date.now() - lastBuild > 120000) rebuild({ keepIndex: true });
    refreshWWA();
    refreshOutlook();
  }, 30);
}

export function hideRadar() {
  stop();
}

export function focusGeometry(geometry) {
  if (!map || !geometry) return;
  if (highlight) map.removeLayer(highlight);
  highlight = L.geoJSON(geometry, {
    pane: 'top', interactive: false,
    style: { color: '#ffffff', weight: 3, fill: false, dashArray: '2 6', className: 'focus-outline' },
  }).addTo(map);
  setTimeout(() => map.fitBounds(highlight.getBounds().pad(0.6), { maxZoom: 10 }), 60);
  setTimeout(() => { if (highlight) { map.removeLayer(highlight); highlight = null; } }, 30000);
}
