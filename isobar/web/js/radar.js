// Radar view.
//
// Radar imagery comes from the Iowa Environmental Mesonet tile service:
//   ridge::USCOMP-N0Q-<stamp>   national mosaic, every 5 minutes
//   ridge::<SITE>-<PROD>-<stamp> single radar Level III products
// Reflectivity tiles are decoded back to dBZ in the browser and repainted
// with the selected palette and clutter cutoff.

import { state, api, IEM, proxyUrl, on, saveSettings, toast, el, debounce, esc, openExternal } from './core.js';
import { createMap, applyBasemap, setBorders, locationMarker, BASEMAPS } from './basemap.js';
import { PALETTES, paletteLUT, colorIndex, dbzToIndex, DBZ_MIN, DBZ_STEP } from './ramps.js';
import {
  hazardColor, hazardName, instant, ago, tempRGB, windRGB, tempVal, windVal, windUnit, SPC_COLORS, MCD_COLOR,
  haversineKm, bearingDeg, compass, distanceKm,
} from './util.js';
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
  { id: 'future', name: 'Future radar', hint: 'HRRR model forecast, colored by precipitation type. Times are when each frame is valid.', noSite: true },
  { id: 'sat', name: 'Satellite', hint: 'GOES East loop from NOAA NESDIS', noSite: true, options: [['13', 'Infrared'], ['02', 'GeoColor'], ['09', 'Water vapor']] },
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
// True while the newest frame is on screen by the user's choice (the default).
// Then every refresh jumps to the newest scan. Stepping back, dragging the
// slider off the end, or pausing on an older frame turns it off; returning to
// the newest frame turns it back on.
let followLatest = true;
let playTimer = null;
let loading = new Set();
// Frames load one after another instead of all at once: the one on screen
// first, then the rest from the oldest. PRELOAD_PARALLEL frames load at a time.
const PRELOAD_PARALLEL = 2;
let pending = [];
// How long play waits for a frame that's still loading before moving on.
const PLAY_WAIT_MS = 2500;
let waitedSince = 0;
let sites = [];
let sitesLayer = null;
let wwaLayers = { Y: null, A: null, W: null };
let outlookLayer = null;
let mcdLayer = null;
let reportsLayer = null;
let attrsLayer = null;
let attrsAt = 0;
let tropicalLayer = null;
let tropicalAt = 0;
let fieldLayer = null;
let highlight = null;
let highlightTimer = null;
// Each overlay counts its refreshes. A response that comes back after a newer
// refresh started (or after the overlay was switched off) is thrown away, so a
// slow answer can never put back a layer the user just turned off.
const seqs = { wwa: 0, outlook: 0, mcd: 0, reports: 0, attrs: 0, tropical: 0 };
// What each overlay is doing, for the chips by the timeline: '' (fine),
// 'loading' or 'error'. A failed overlay retries on its own after RETRY_MS.
const overlayState = {};
const retryTimers = {};
const RETRY_MS = 30000;
let mcdAt = 0;
let reports = { hours: 0, at: 0, features: [] };
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
      // Kept for the pointer readout, which reads the original colors back.
      canvas._src = img;
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
      el('p', { class: 'hint' }, 'Pick a radar dot on the map to see just that radar.'));
    return;
  }
  const chosen = state.settings.radar_site || 'mosaic';
  const auto = chosen === 'auto' || (p.needsSite && chosen === 'mosaic');
  box.append(el('div', { class: 'site-now' },
    el('span', { class: 'site-dot' }), el('span', {}, siteName(active), auto ? el('span', { class: 'muted' }, ', nearest') : null)));
  if (p.mosaic) {
    box.append(el('button', { class: 'link small', onclick: goMosaic }, 'Back to all radars'));
  } else {
    box.append(el('p', { class: 'hint' }, `${p.name} comes from one radar at a time. Pick another dot to switch.`));
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
      mk.bindTooltip(esc(label), { permanent: true, direction: 'right', offset: [8, 0], className: 'site-label' });
    } else {
      mk.bindTooltip(esc(`${label}${x.name ? ` ${x.name}` : ''}`), { direction: 'top', offset: [0, -6] });
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
    toast('No radar near this location. Pick a radar dot on the map.');
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
      out.push({ url: TILE(`nexrad-eet-m${String(m).padStart(2, '0')}m`, 'cache'), time: new Date(Date.now() - m * 60000).toISOString(), approx: true, live: true });
    }
    out.push({ url: TILE('nexrad-eet', 'cache'), time: new Date().toISOString(), approx: true, live: true });
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
    if (!out.length) out.push({ url: TILE(`ridge::${site}-${prod}-0`, 'cache'), time: null, recolor: !!p.recolor, live: true });
    return out;
  }

  if (p.id === 'rain') {
    out.push({ url: TILE(option(p), 'cache'), time: null, label: `Last ${p.options.find((o) => o[0] === option(p))[1]}`, live: true });
    return out;
  }

  if (p.id === 'future') {
    // Naming the run in the URL gives each frame a real valid time, and lets
    // the frames be reused until the next run lands.
    let run = null;
    try {
      run = (await api('/api/hrrr')).run;
    } catch (err) {
      console.warn('hrrr run', err);
    }
    for (let h = 0; h <= 18; h++) {
      const minutes = String(h * 60).padStart(4, '0');
      if (run) {
        const time = new Date(new Date(run.iso).getTime() + h * 3600e3).toISOString();
        out.push({ url: TILE(`hrrr::REFP-F${minutes}-${run.stamp}`, 'cache'), time, label: `+${h} h`, future: true });
      } else {
        out.push({ url: TILE(`hrrr::REFP-F${minutes}-0`, 'cache'), time: null, label: h === 0 ? 'Model start' : `+${h} h`, live: true });
      }
    }
    return out;
  }

  if (p.id === 'sat') {
    const band = option(p);
    try {
      const info = await api('/api/sat', { band });
      if (info && info.end) {
        // GOES East scans the US every 5 minutes; 10 minute steps keep the loop light.
        const step = 10 * 60000;
        const n = Math.max(2, Math.min(18, Math.round(Math.min(minutes, 180) / 10)));
        const last = Math.floor(info.end / step) * step;
        for (let i = n - 1; i >= 0; i--) {
          const t = last - i * step;
          out.push({ image: true, base: info.url, t, time: new Date(t).toISOString() });
        }
        return out;
      }
    } catch (err) {
      console.warn('satellite loop unavailable, showing the latest image', err);
    }
    const lon = state.loc ? state.loc.lon : -95;
    const bird = lon < -110 ? 'west' : 'east';
    out.push({ url: TILE(`goes_${bird}_conus_ch${band}`, 'cache'), time: null, label: `GOES ${bird === 'east' ? 'East' : 'West'}, latest`, live: true });
    return out;
  }
  return out;
}

// The satellite loop is one image per frame covering the view plus a margin.
// Panning inside the margin keeps the images; leaving it or zooming fetches new ones.
let satBounds = null;
let satZoom = null;

function satUrl(frame) {
  const b = satBounds;
  const size = map.getSize().multiplyBy(1.6);
  const w = Math.min(2048, Math.round(size.x));
  const h = Math.min(2048, Math.round(size.y));
  const params = new URLSearchParams({
    bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(3)).join(','),
    bboxSR: '4326', imageSR: '3857', size: `${w},${h}`, format: 'png', f: 'image',
    time: `${frame.t - 5 * 60000},${frame.t + 5 * 60000}`,
  });
  return `${frame.base}?${params}`;
}

function satNeedsNewView() {
  if (!satBounds) return true;
  return map.getZoom() !== satZoom || !satBounds.contains(map.getBounds());
}

function clearFrames() {
  for (const layer of layers) if (layer._onMap) map.removeLayer(layer);
  layers = [];
  frames = [];
  pending = [];
  loading.clear();
  updateLoading();
}

function frameKey(frame) {
  return frame.image ? `sat:${frame.base}:${frame.t}` : frame.url;
}

function makeLayer(frame) {
  let layer;
  if (frame.image) {
    layer = L.imageOverlay(satUrl(frame), satBounds, { pane: 'radar', opacity: 0, className: 'radar-tiles', interactive: false });
    layer.on('load error', () => { layer._ready = true; loading.delete(layer); updateLoading(); pump(); });
  } else {
    const options = {
      pane: 'radar', opacity: 0, subdomains: '123', maxZoom: 19, maxNativeZoom: 12,
      updateWhenZooming: false, keepBuffer: 1, className: 'radar-tiles', crossOrigin: 'anonymous',
    };
    layer = frame.recolor ? new RecolorLayer(frame.url, options) : L.tileLayer(frame.url, options);
    layer.on('loading', () => { layer._ready = false; loading.add(layer); updateLoading(); });
    layer.on('load', () => { layer._ready = true; loading.delete(layer); updateLoading(); pump(); });
  }
  layer._key = frameKey(frame);
  layer._onMap = false;
  layer._ready = false;
  return layer;
}

function putOnMap(layer) {
  if (layer._onMap) return;
  layer._onMap = true;
  layer._ready = false;
  loading.add(layer);
  updateLoading();
  layer.addTo(map);
}

function takeOffMap(layer) {
  if (!layer._onMap) return;
  layer._onMap = false;
  layer._ready = false;
  loading.delete(layer);
  map.removeLayer(layer);
}

// Queue every frame that isn't on the map yet: the one showing first, then
// oldest to newest, which is the order the loop plays in.
function schedulePreload() {
  const order = [index, ...frames.map((_, i) => i).filter((i) => i !== index)];
  pending = order.filter((i) => layers[i] && !layers[i]._onMap);
  pump();
}

function pump() {
  let busy = layers.filter((l) => l._onMap && !l._ready).length;
  while (busy < PRELOAD_PARALLEL && pending.length) {
    const layer = layers[pending.shift()];
    if (!layer || layer._onMap) continue;
    putOnMap(layer);
    busy++;
  }
  updateLoading();
}

// Zooming would make every loaded frame fetch a full set of new tiles at
// once. Instead the other frames come off the map and load again in turn.
function onZoomStart() {
  layers.forEach((layer, k) => { if (k !== index) takeOffMap(layer); });
  pending = [];
}

function onZoomEnd() {
  if (frames.length && frames[0].image) return; // the satellite loop rebuilds on moveend
  schedulePreload();
}

async function rebuild({ keepIndex = false, force = false } = {}) {
  if (!map) return;
  // A fresh build (new product, radar or place) always starts on the newest frame.
  if (!keepIndex) followLatest = true;
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
  if (next.length && next[0].image && (force || satNeedsNewView())) {
    satBounds = map.getBounds().pad(0.3);
    satZoom = map.getZoom();
    force = true;
  }

  // Frames already loaded are kept, so a refresh only fetches the new scan.
  // Frames whose URL always means "latest" are rebuilt, since the same URL
  // now holds a different picture.
  const old = new Map();
  for (const layer of layers) old.set(layer._key, layer);
  const nextLayers = next.map((frame) => {
    const key = frameKey(frame);
    const reuse = !force && !frame.live && old.get(key);
    if (reuse) {
      old.delete(key);
      return reuse;
    }
    return makeLayer(frame);
  });
  for (const layer of old.values()) takeOffMap(layer);
  frames = next;
  layers = nextLayers;
  pending = [];

  index = frames.length - 1;
  if (prevTime && !followLatest) {
    // Stay on the frame the user picked. If it has aged out of the loop,
    // land on the oldest frame, which is the closest one still there.
    const k = frames.findIndex((f) => f.time === prevTime);
    index = k >= 0 ? k : 0;
  }
  if (p.id === 'future' && !(prevTime && !followLatest)) {
    // Start on the frame closest to now, since the run is an hour or two old.
    const now = Date.now();
    const k = frames.findIndex((f) => f.time && new Date(f.time).getTime() >= now - 30 * 60000);
    index = k >= 0 ? k : 0;
  }
  show(index);
  schedulePreload();
  renderTimeline();
  lastBuild = Date.now();
  built = true;
  if (wasPlaying && frames.length > 1) play();
}

function show(i) {
  if (!layers.length) return;
  const opacity = Number(state.settings.radar_opacity) || 0.85;
  if (layers[i]) putOnMap(layers[i]);
  layers.forEach((layer, k) => { if (layer._onMap) layer.setOpacity(k === i ? opacity : 0); });
  index = i;
  const f = frames[i];
  if (ui.label) {
    let text = f.label || '';
    let stale = false;
    if (f.time) {
      text = instant(f.time);
      if (f.approx) text = `about ${text}`;
      if (f.future) text = `${text} (${f.label})`;
      if (i === frames.length - 1 && !f.future) {
        text += `, ${ago(f.time)}`;
        // The newest scan should be minutes old. Much older means the feed stalled.
        stale = Date.now() - new Date(f.time).getTime() > 15 * 60000;
      }
    } else if (!text) {
      text = 'Latest scan';
    }
    ui.label.textContent = text;
    ui.label.classList.toggle('stale', stale);
    ui.label.title = stale ? 'The newest frame is more than 15 minutes old. The radar feed may be behind.' : '';
  }
  if (ui.slider) ui.slider.value = String(i);
}

function play() {
  if (frames.length < 2) return;
  playing = true;
  ui.play.innerHTML = uiIcon('pause', 18);
  ui.play.setAttribute('aria-label', 'Pause');
  waitedSince = 0;
  const tick = () => {
    if (!playing) return;
    const nextIdx = (index + 1) % frames.length;
    const layer = layers[nextIdx];
    // Hold on this frame while the next one is still loading, rather than
    // flashing a half-drawn map, but not forever.
    if (layer && !layer._ready) {
      if (!layer._onMap) putOnMap(layer);
      if (!waitedSince) waitedSince = Date.now();
      if (Date.now() - waitedSince < PLAY_WAIT_MS) {
        playTimer = setTimeout(tick, 150);
        return;
      }
    }
    waitedSince = 0;
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

function atLatest() {
  return frames.length > 0 && index === frames.length - 1;
}

// Every way a user picks a frame or pauses goes through here, so the
// follow-the-newest-scan choice always matches what they did last.
function userStop() {
  stop();
  followLatest = atLatest();
}

function step(delta) {
  stop();
  if (frames.length) show((index + delta + frames.length) % frames.length);
  followLatest = atLatest();
}

function updateLoading() {
  if (!ui.spinner) return;
  const busy = loading.size > 0 || pending.length > 0;
  ui.spinner.hidden = !busy;
  if (busy && layers.length > 1) {
    const ready = layers.filter((l) => l._ready).length;
    ui.spinner.title = `Loading frames: ${ready} of ${layers.length} ready`;
  } else {
    ui.spinner.title = 'Loading';
  }
}

// ------------------------------------------------------------------ fields

// The last model grid fetched. The server pads the area it asks Open-Meteo for,
// so as long as the view stays inside it at a similar zoom, pans and small
// zooms reuse it instead of costing another batch of Open-Meteo calls.
let lastGrid = null;

function gridCovers(grid, bounds, zoom) {
  if (!grid || Math.abs(grid.zoom - zoom) > 1 || Date.now() - grid.at > 30 * 60 * 1000) return false;
  const { lats, lons } = grid.data;
  if (!lats || !lons || lats.length < 2 || lons.length < 2) return false;
  return bounds.getSouth() >= lats[0] && bounds.getNorth() <= lats[lats.length - 1]
    && bounds.getWest() >= lons[0] && bounds.getEast() <= lons[lons.length - 1];
}

async function ensureField() {
  const p = product();
  if (!fieldLayer) fieldLayer = new FieldLayer().addTo(map);
  const b = map.getBounds();
  const zoom = map.getZoom();
  const size = map.getSize();
  const nx = Math.max(5, Math.min(9, Math.round(size.x / 140)));
  const ny = Math.max(4, Math.min(7, Math.round(size.y / 140)));
  try {
    const grid = gridCovers(lastGrid, b, zoom) ? lastGrid.data : await api('/api/grid', {
      s: b.getSouth().toFixed(2), w: b.getWest().toFixed(2), n: b.getNorth().toFixed(2), e: b.getEast().toFixed(2), nx, ny,
    });
    if (!lastGrid || lastGrid.data !== grid) lastGrid = { data: grid, zoom, at: Date.now() };
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

const TOP_PRIORITY = { TO: 5, EW: 4, SV: 3, FF: 3 };

// ------------------------------------------------------------------ overlay status

const OVERLAY_NAMES = {
  warnings: 'Warnings', watches: 'Watches', advisories: 'Advisories', mcd: 'Discussions',
  reports: 'Reports', outlook: 'Day 1 outlook', attrs: 'Storm cells', tropical: 'Tropical',
};

// Records what an overlay is doing and redraws the chips. A failure schedules
// one retry; a later success clears it.
function setOverlay(key, status, retry) {
  overlayState[key] = status;
  clearTimeout(retryTimers[key]);
  if (status === 'error' && retry) retryTimers[key] = setTimeout(retry, RETRY_MS);
  renderChips();
}

function renderChips() {
  if (!ui.chips) return;
  const layersOn = state.settings.radar_layers;
  const keys = Object.keys(OVERLAY_NAMES).filter((k) => layersOn[k]);
  ui.chips.replaceChildren(...keys.map((k) => {
    const st = overlayState[k] || '';
    return el('button', {
      class: `ov-chip${st ? ` ov-${st}` : ''}`,
      title: st === 'error' ? `${OVERLAY_NAMES[k]} did not load. Retrying in 30 seconds.` : st === 'loading' ? `Loading ${OVERLAY_NAMES[k].toLowerCase()}` : `${OVERLAY_NAMES[k]} on. Open layers.`,
      onclick: () => ui.openLayers(),
    }, OVERLAY_NAMES[k], st === 'error' ? ', failed' : '');
  }));
  ui.chips.hidden = !keys.length;
  const failed = keys.some((k) => overlayState[k] === 'error');
  if (ui.layersBtn) ui.layersBtn.classList.toggle('has-error', failed);
}

// ------------------------------------------------------------------ warnings, watches, advisories

async function refreshWWA() {
  if (!map) return;
  const seq = ++seqs.wwa;
  const keyOf = { W: 'warnings', A: 'watches', Y: 'advisories' };
  const want = {
    W: state.settings.radar_layers.warnings,
    A: state.settings.radar_layers.watches,
    Y: state.settings.radar_layers.advisories,
  };
  const b = map.getBounds().pad(0.1);
  const z = map.getZoom();
  for (const sig of ['Y', 'A', 'W']) {
    const key = keyOf[sig];
    if (!want[sig]) {
      if (wwaLayers[sig]) { map.removeLayer(wwaLayers[sig]); wwaLayers[sig] = null; }
      setOverlay(key, '');
      continue;
    }
    if (!wwaLayers[sig]) setOverlay(key, 'loading');
    try {
      const fc = await api('/api/wwa', {
        sig, z: Math.round(z),
        s: b.getSouth().toFixed(2), w: b.getWest().toFixed(2), n: b.getNorth().toFixed(2), e: b.getEast().toFixed(2),
      });
      if (seq !== seqs.wwa) return;
      fc.features.sort((a, c) => (TOP_PRIORITY[a.properties.phenom] || 0) - (TOP_PRIORITY[c.properties.phenom] || 0));
      const label = sig === 'W' && state.settings.radar_layers.labels && z >= 6;
      const layer = L.geoJSON(fc, {
        style: (f) => wwaStyle(f.properties),
        onEachFeature: label ? (f, lyr) => lyr.bindTooltip(esc(f.properties.phenom || '?'), {
          permanent: true, direction: 'center', className: 'wwa-label', interactive: false, opacity: 1,
        }) : undefined,
      });
      if (wwaLayers[sig]) map.removeLayer(wwaLayers[sig]);
      wwaLayers[sig] = layer.addTo(map);
      setOverlay(key, '');
    } catch (err) {
      console.warn('wwa', sig, err);
      if (seq === seqs.wwa) setOverlay(key, 'error', refreshWWA);
    }
  }
  for (const sig of ['Y', 'A', 'W']) if (wwaLayers[sig]) wwaLayers[sig].bringToFront();
  if (mcdLayer) mcdLayer.bringToFront();
}

function wwaStyle(p) {
  const color = hazardColor(p.phenom, p.sig);
  if (p.sig === 'W') return { color, weight: p.phenom === 'TO' ? 3.2 : 2.4, fillColor: color, fillOpacity: 0.12, opacity: 1 };
  if (p.sig === 'A') return { color, weight: 2, dashArray: '7 5', fillColor: color, fillOpacity: 0.07, opacity: 0.95 };
  return { color, weight: 1, fillColor: color, fillOpacity: 0.14, opacity: 0.8 };
}

// Popup sections are built as DOM nodes, so nothing from NOAA is ever parsed as HTML.
function wwaSection(p) {
  return el('div', { class: 'pop' },
    el('strong', { class: 'pop-title', style: `--c:${hazardColor(p.phenom, p.sig)}` }, hazardName(p.phenom, p.sig, p.event)),
    p.expires ? el('span', {}, `Until ${instant(p.expires, true)}`) : null,
    p.wfo ? el('span', {}, `NWS ${String(p.wfo).replace(/^K/, '')}`) : null);
}

function mcdSection(p) {
  return el('div', { class: 'pop' },
    el('strong', { class: 'pop-title', style: `--c:${MCD_COLOR}` }, p.name),
    p.expires ? el('span', {}, `Until ${instant(p.expires, true)}`) : null,
    p.issued ? el('span', {}, `Issued ${instant(p.issued, true)}`) : null,
    el('span', {}, popupLink('Read it on spc.noaa.gov', p.link)));
}

// ------------------------------------------------------------------ clicking stacked polygons
//
// Leaflet only hands a click to the topmost shape, so a mesoscale discussion
// drawn over a warning would hide the warning. Instead, a click anywhere on the
// map tests the point against every warning, watch, advisory and discussion,
// and one popup lists all of them, most urgent first.

function inRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function inPolygon(x, y, rings) {
  // The first ring is the outline, the rest are holes.
  if (!rings.length || !inRing(x, y, rings[0])) return false;
  return !rings.slice(1).some((hole) => inRing(x, y, hole));
}

function inGeometry(latlng, g) {
  if (!g) return false;
  const x = latlng.lng;
  const y = latlng.lat;
  if (g.type === 'Polygon') return inPolygon(x, y, g.coordinates);
  if (g.type === 'MultiPolygon') return g.coordinates.some((rings) => inPolygon(x, y, rings));
  if (g.type === 'GeometryCollection') return g.geometries.some((sub) => inGeometry(latlng, sub));
  return false;
}

function hitsIn(group, latlng) {
  const out = [];
  if (!group) return out;
  group.eachLayer((lyr) => {
    if (!lyr.feature || !lyr.getBounds || !lyr.getBounds().contains(latlng)) return;
    if (inGeometry(latlng, lyr.feature.geometry)) out.push(lyr.feature.properties || {});
  });
  return out;
}

const wwaRank = (p) => TOP_PRIORITY[p.phenom] || 0;

function onMapClick(e) {
  const sections = [];
  const seen = new Set();
  const addWwa = (list) => {
    list.sort((a, c) => wwaRank(c) - wwaRank(a));
    for (const p of list) {
      // Watches come as one shape per county, so the same watch can match twice.
      const key = `${p.phenom}.${p.sig}.${p.event}.${p.wfo}.${p.issued}.${p.expires}`;
      if (seen.has(key)) continue;
      seen.add(key);
      sections.push(wwaSection(p));
    }
  };
  addWwa(hitsIn(wwaLayers.W, e.latlng));
  addWwa(hitsIn(wwaLayers.A, e.latlng));
  for (const p of hitsIn(mcdLayer, e.latlng)) sections.push(mcdSection(p));
  addWwa(hitsIn(wwaLayers.Y, e.latlng));
  if (tropicalLayer) {
    for (const p of hitsIn(tropicalLayer, e.latlng)) sections.push(tropicalSection(p));
  }
  if (!sections.length) return;
  const body = el('div', { class: sections.length > 1 ? 'pop-stack' : '' }, ...sections);
  L.popup({ className: 'iso-popup', maxWidth: 320 }).setLatLng(e.latlng).setContent(body).openOn(map);
}

async function refreshOutlook() {
  const seq = ++seqs.outlook;
  if (outlookLayer) { map.removeLayer(outlookLayer); outlookLayer = null; }
  if (!state.settings.radar_layers.outlook) { setOverlay('outlook', ''); return; }
  setOverlay('outlook', 'loading');
  try {
    const o = await api('/api/spc', { day: 1, kind: 'cat' });
    if (seq !== seqs.outlook) return;
    setOverlay('outlook', '');
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
    if (seq === seqs.outlook) setOverlay('outlook', 'error', refreshOutlook);
  }
}

// ------------------------------------------------------------------ SPC mesoscale discussions

function popupLink(text, url) {
  return el('button', { class: 'link small', onclick: () => openExternal(url) }, text, ' ', el('span', { html: uiIcon('external', 12) }));
}

async function refreshMcd() {
  const seq = ++seqs.mcd;
  if (!state.settings.radar_layers.mcd) {
    if (mcdLayer) { map.removeLayer(mcdLayer); mcdLayer = null; }
    setOverlay('mcd', '');
    return;
  }
  if (!mcdLayer) setOverlay('mcd', 'loading');
  try {
    const fc = await api('/api/spc/mcd');
    if (seq !== seqs.mcd) return;
    setOverlay('mcd', '');
    if (mcdLayer) map.removeLayer(mcdLayer);
    mcdAt = Date.now();
    mcdLayer = L.geoJSON(fc, {
      // No popup here: onMapClick finds this and anything under it.
      style: { color: MCD_COLOR, weight: 2.4, dashArray: '2 5', fillColor: MCD_COLOR, fillOpacity: 0.08, opacity: 1 },
    }).addTo(map);
  } catch (err) {
    console.warn('mcd overlay', err);
    if (seq === seqs.mcd) setOverlay('mcd', 'error', refreshMcd);
  }
}

// ------------------------------------------------------------------ local storm reports

const REPORT_HOURS = ['3', '6', '12', '24', '48'];
const MAX_REPORT_MARKERS = 1500;
const REPORT_KINDS = [
  // [test on the report text, legend name, color]
  [/TORNADO|FUNNEL|WATERSPOUT/, 'Tornado', '#ff2a2a'],
  [/HAIL/, 'Hail', '#22c55e'],
  [/WND|WIND|DOWNBURST/, 'Wind', '#3d8bfd'],
  [/FLOOD/, 'Flood', '#14b8a6'],
  [/SNOW|SLEET|ICE|BLIZZARD|FREEZ/, 'Winter', '#c084fc'],
  [/.*/, 'Other', '#9aa6b2'],
];

function reportKind(text) {
  const up = String(text || '').toUpperCase();
  return REPORT_KINDS.find(([re]) => re.test(up));
}

function reportHours() {
  const h = (state.settings.radar_options || {}).reports;
  return REPORT_HOURS.includes(h) ? h : '12';
}

function titleWords(text) {
  return String(text || '').toLowerCase().replace(/\btstm\b/g, 'thunderstorm').replace(/\bwnd\b/g, 'wind')
    .replace(/\bgst\b/g, 'gust').replace(/\bdmg\b/g, 'damage').replace(/^./, (c) => c.toUpperCase());
}

function reportPopup(p) {
  const [, , color] = reportKind(p.text);
  const size = p.magnitude ? `${p.magnitude}${p.unit ? ` ${p.unit.toLowerCase()}` : ''}` : '';
  const place = [p.city, p.county ? `${p.county} County` : '', p.state].filter(Boolean).join(', ');
  return el('div', { class: 'pop' },
    el('strong', { class: 'pop-title', style: `--c:${color}` }, `${titleWords(p.text) || 'Report'}${size ? `, ${size}` : ''}`),
    place ? el('span', {}, place) : null,
    p.valid ? el('span', {}, `${instant(p.valid, true)} (${ago(p.valid)})`) : null,
    p.source ? el('span', {}, `Reported by ${p.source.toLowerCase()}`) : null,
    p.remark ? el('span', {}, p.remark) : null);
}

function drawReports() {
  if (reportsLayer) { map.removeLayer(reportsLayer); reportsLayer = null; }
  if (!state.settings.radar_layers.reports || !reports.features.length) return;
  // Only what's on screen gets a marker, newest first, so a busy day stays responsive.
  const b = map.getBounds().pad(0.2);
  const visible = reports.features
    .filter((f) => b.contains([f.geometry.coordinates[1], f.geometry.coordinates[0]]))
    .sort((a, c) => String(c.properties.valid).localeCompare(String(a.properties.valid)))
    .slice(0, MAX_REPORT_MARKERS)
    .reverse(); // oldest drawn first, so the newest sit on top
  reportsLayer = L.layerGroup(visible.map((f) => {
    const [, , color] = reportKind(f.properties.text);
    const tornado = color === REPORT_KINDS[0][2];
    return L.circleMarker([f.geometry.coordinates[1], f.geometry.coordinates[0]], {
      pane: 'reports', radius: tornado ? 6.5 : 4.5, weight: 1.2, color: '#0a0e14', opacity: 0.9,
      fillColor: color, fillOpacity: 0.95, bubblingMouseEvents: false,
    }).bindPopup(() => reportPopup(f.properties), { className: 'iso-popup', maxWidth: 300 });
  })).addTo(map);
}

async function refreshReports({ force = false } = {}) {
  const seq = ++seqs.reports;
  if (!state.settings.radar_layers.reports) { setOverlay('reports', ''); drawReports(); return; }
  const hours = Number(reportHours());
  if (force || reports.hours !== hours || Date.now() - reports.at > 5 * 60000) {
    if (!reports.features.length) setOverlay('reports', 'loading');
    try {
      const fc = await api('/api/reports', { hours });
      if (seq !== seqs.reports) return;
      reports = { hours, at: Date.now(), features: fc.features || [] };
      setOverlay('reports', '');
    } catch (err) {
      console.warn('storm reports', err);
      if (seq !== seqs.reports) return;
      setOverlay('reports', 'error', () => refreshReports({ force: true }));
    }
  }
  drawReports();
}

// ------------------------------------------------------------------ NEXRAD storm cells

// Arrow length: how far the cell moves in 30 minutes at its tracked speed.
const TRACK_MINUTES = 30;

function attrIcon(p) {
  const kind = p.tvs ? 'tvs' : p.meso ? 'meso' : 'hail';
  return L.divIcon({ className: `cell-marker cell-${kind}`, html: '<span></span>', iconSize: [16, 16], iconAnchor: [8, 8] });
}

function attrPopup(p) {
  const lines = [];
  if (p.tvs) lines.push(`Tornado vortex signature (${p.tvs.toLowerCase()})`);
  if (p.meso) lines.push(`Mesocyclone, strength ${p.meso.toLowerCase()}`);
  if (p.posh || p.poh) lines.push(`Hail: ${Math.round(p.poh)}% chance, ${Math.round(p.posh)}% chance of severe hail`);
  if (p.size) lines.push(state.settings.units === 'us' ? `Largest hail about ${p.size.toFixed(2)} in` : `Largest hail about ${Math.round(p.size * 25.4)} mm`);
  if (p.dbz) lines.push(`Peak ${Math.round(p.dbz)} dBZ${p.top ? `, tops ${state.settings.units === 'us' ? `${Math.round(p.top)}k ft` : `${(p.top * 0.3048).toFixed(1)} km`}` : ''}`);
  if (p.sknt && p.drct !== null) {
    const speed = state.settings.units === 'us' ? `${Math.round(p.sknt * 1.15078)} mph` : `${Math.round(p.sknt * 1.852)} km/h`;
    lines.push(`Moving ${compass((p.drct + 180) % 360)} at ${speed}`);
  }
  const color = p.tvs ? '#ff2a2a' : p.meso ? '#f5c518' : '#22c55e';
  return el('div', { class: 'pop' },
    el('strong', { class: 'pop-title', style: `--c:${color}` }, `Storm cell ${p.id} (K${p.radar.replace(/^K/, '')})`),
    ...lines.map((t) => el('span', {}, t)),
    p.valid ? el('span', {}, `As of ${instant(p.valid)}`) : null);
}

async function refreshAttrs() {
  const seq = ++seqs.attrs;
  if (!state.settings.radar_layers.attrs) {
    if (attrsLayer) { map.removeLayer(attrsLayer); attrsLayer = null; }
    setOverlay('attrs', '');
    return;
  }
  if (!attrsLayer) setOverlay('attrs', 'loading');
  try {
    const fc = await api('/api/attrs');
    if (seq !== seqs.attrs) return;
    attrsAt = Date.now();
    const items = [];
    for (const f of fc.features || []) {
      const [lon, lat] = f.geometry.coordinates;
      const p = f.properties;
      items.push(L.marker([lat, lon], { icon: attrIcon(p), pane: 'reports', bubblingMouseEvents: false, keyboard: false })
        .bindPopup(() => attrPopup(p), { className: 'iso-popup', maxWidth: 300 }));
      if (p.sknt && p.drct !== null && p.drct !== undefined) {
        // Storm motion is given as the direction it comes from, like wind.
        const km = p.sknt * 1.852 * (TRACK_MINUTES / 60);
        const toward = ((p.drct + 180) % 360) * Math.PI / 180;
        const dLat = (km / 111.2) * Math.cos(toward);
        const dLon = (km / (111.2 * Math.cos(lat * Math.PI / 180))) * Math.sin(toward);
        items.push(L.polyline([[lat, lon], [lat + dLat, lon + dLon]], {
          pane: 'reports', color: '#ffffff', weight: 2, opacity: 0.85, dashArray: '4 4', interactive: false,
        }));
      }
    }
    if (attrsLayer) map.removeLayer(attrsLayer);
    attrsLayer = L.layerGroup(items).addTo(map);
    setOverlay('attrs', '');
  } catch (err) {
    console.warn('storm attributes', err);
    if (seq === seqs.attrs) setOverlay('attrs', 'error', refreshAttrs);
  }
}

// ------------------------------------------------------------------ tropical

function tropicalSection(p) {
  const kind = { HU: 'Hurricane', TS: 'Tropical storm', TD: 'Tropical depression', STS: 'Subtropical storm', STD: 'Subtropical depression', PTC: 'Potential tropical cyclone' }[p.kind] || 'Tropical cyclone';
  return el('div', { class: 'pop' },
    el('strong', { class: 'pop-title', style: '--c:#f5f5f5' }, `${kind}${p.name ? ` ${p.name}` : ''}`),
    el('span', {}, `Forecast cone${p.advisory ? `, advisory ${p.advisory}` : ''}`),
    el('span', {}, popupLink('Read it on nhc.noaa.gov', 'https://www.nhc.noaa.gov/')));
}

async function refreshTropical() {
  const seq = ++seqs.tropical;
  if (!state.settings.radar_layers.tropical) {
    if (tropicalLayer) { map.removeLayer(tropicalLayer); tropicalLayer = null; }
    setOverlay('tropical', '');
    return;
  }
  if (!tropicalLayer) setOverlay('tropical', 'loading');
  try {
    const data = await api('/api/tropical');
    if (seq !== seqs.tropical) return;
    tropicalAt = Date.now();
    if (tropicalLayer) map.removeLayer(tropicalLayer);
    // Cones answer the map click (see onMapClick); tracks are drawn on top.
    tropicalLayer = L.geoJSON(data.cones, {
      pane: 'outlook',
      style: { color: '#ffffff', weight: 1.6, dashArray: '6 4', fillColor: '#ffffff', fillOpacity: 0.12, opacity: 0.9 },
    });
    L.geoJSON(data.tracks, {
      pane: 'outlook', interactive: false,
      style: { color: '#ffffff', weight: 2, opacity: 0.9 },
    }).eachLayer((l) => tropicalLayer.addLayer(l));
    tropicalLayer.addTo(map);
    setOverlay('tropical', '');
  } catch (err) {
    console.warn('tropical', err);
    if (seq === seqs.tropical) setOverlay('tropical', 'error', refreshTropical);
  }
}

// Everything drawn over the radar, refreshed on its own clock. Watches and
// warnings ask the server every time (it caches for two minutes); the rest
// only when their copy here has aged out.
function refreshOverlays() {
  refreshWWA();
  if (Date.now() - mcdAt > 2 * 60000 || !mcdLayer) refreshMcd();
  if (state.settings.radar_layers.reports && Date.now() - reports.at > 5 * 60000) refreshReports();
  if (state.settings.radar_layers.attrs && Date.now() - attrsAt > 2 * 60000) refreshAttrs();
  if (state.settings.radar_layers.tropical && Date.now() - tropicalAt > 10 * 60000) refreshTropical();
}

// ------------------------------------------------------------------ panels

const narrow = () => window.matchMedia('(max-width: 640px)').matches;

function setPanelOpen(open) {
  if (!ui.panel) return;
  ui.panel.classList.toggle('open', open);
  ui.panelHead.setAttribute('aria-expanded', String(open));
}

function renderProductPanel() {
  const p = product();
  if (ui.panelHead) {
    ui.panelHead.replaceChildren(el('span', {}, p.name), el('span', { class: 'panel-chev', html: uiIcon('chevron', 16) }));
    ui.panelHead.setAttribute('aria-expanded', String(ui.panel.classList.contains('open')));
  }
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
  } else if (p.id === 'sat') {
    const notes = {
      13: 'Infrared: brighter and colder cloud tops are taller storms. Works day and night.',
      '02': 'GeoColor: true color by day, infrared clouds over city lights at night.',
      '09': 'Water vapor in the lower and middle air. Dark is dry, bright is moist.',
    };
    box.append(el('div', { class: 'legend-note' }, notes[option(p)] || 'GOES East satellite.', ' GOES East via NOAA NESDIS.'));
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
        if (key === 'mcd') refreshMcd();
        if (key === 'reports') { reportsBox.hidden = !e.target.checked; refreshReports(); }
        if (key === 'attrs') refreshAttrs();
        if (key === 'tropical') refreshTropical();
        if (key === 'labels') refreshWWA();
        if (key === 'counties') setBorders(map, e.target.checked);
        renderChips();
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

  const reportsHours = el('select', {
    'aria-label': 'Storm reports from the last',
    onchange: (e) => { saveSettings({ radar_options: { reports: e.target.value } }); refreshReports(); },
  }, ...REPORT_HOURS.map((h) => el('option', { value: h }, `Last ${h} hours`)));
  reportsHours.value = reportHours();
  const reportsBox = el('div', { hidden: !s.radar_layers.reports },
    el('label', { class: 'field' }, reportsHours),
    el('div', { class: 'report-key' }, ...REPORT_KINDS.map(([, name, color]) => el('span', { style: `--c:${color}` }, el('i'), name))));

  return el('div', { class: 'layers-body' },
    el('h3', {}, 'Overlays'),
    toggle('warnings', 'Warnings'),
    toggle('watches', 'Watches'),
    toggle('advisories', 'Advisories'),
    toggle('mcd', 'SPC mesoscale discussions'),
    toggle('reports', 'Storm reports'),
    reportsBox,
    toggle('attrs', 'Storm cells (TVS, mesocyclone, hail)'),
    el('div', { class: 'report-key' },
      el('span', { class: 'cell-key cell-tvs' }, el('i'), 'Tornado vortex'),
      el('span', { class: 'cell-key cell-meso' }, el('i'), 'Mesocyclone'),
      el('span', { class: 'cell-key cell-hail' }, el('i'), 'Hail')),
    toggle('tropical', 'Hurricane cones (NHC)'),
    toggle('outlook', 'SPC day 1 outlook'),
    toggle('labels', 'Label warnings (TO, SV, FF...)'),
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
  if (product().recolor) rebuild({ keepIndex: true, force: true });
  renderLegend();
}

// ------------------------------------------------------------------ pointer readout

// Under the mouse: the reflectivity there (read back from the original tile
// colors, so the palette and cutoff don't change it) and how far and which
// way it is from your place.
const probe = document.createElement('canvas');
probe.width = 1;
probe.height = 1;
const probeCtx = probe.getContext('2d', { willReadFrequently: true });

function dbzAt(latlng) {
  const layer = layers[index];
  const frame = frames[index];
  if (!layer || !frame || !frame.recolor || !layer._tiles) return undefined;
  const z = layer._tileZoom;
  if (z === undefined) return undefined;
  const size = layer.getTileSize();
  const pt = map.project(latlng, z);
  const x = Math.floor(pt.x / size.x);
  const y = Math.floor(pt.y / size.y);
  const tile = layer._tiles[`${x}:${y}:${z}`];
  const img = tile && tile.el && tile.el._src;
  if (!img || !img.naturalWidth) return undefined;
  const sx = Math.floor(((pt.x - x * size.x) / size.x) * img.naturalWidth);
  const sy = Math.floor(((pt.y - y * size.y) / size.y) * img.naturalHeight);
  try {
    probeCtx.clearRect(0, 0, 1, 1);
    probeCtx.drawImage(img, sx, sy, 1, 1, 0, 0, 1, 1);
    const [r, g, b, a] = probeCtx.getImageData(0, 0, 1, 1).data;
    if (!a) return null;
    let idx = colorIndex(r, g, b);
    if (idx >= 1000) idx -= 1000;
    return idx < 0 ? null : DBZ_MIN + DBZ_STEP * idx;
  } catch {
    return undefined; // a tile we can't read
  }
}

function updateReadout(latlng) {
  if (!ui.readout) return;
  const parts = [];
  const dbz = dbzAt(latlng);
  if (dbz !== undefined) parts.push(dbz === null ? 'No echo' : `${Math.round(dbz)} dBZ`);
  if (state.loc) {
    const km = haversineKm(state.loc.lat, state.loc.lon, latlng.lat, latlng.lng);
    const dir = compass(bearingDeg(state.loc.lat, state.loc.lon, latlng.lat, latlng.lng));
    parts.push(km < 0.5 ? 'at your place' : `${distanceKm(km)} ${dir} of your place`);
  }
  ui.readout.textContent = parts.join(', ');
  ui.readout.hidden = !parts.length;
}

// ------------------------------------------------------------------ public

export function initRadar(container) {
  root = container;
  const mapEl = el('div', { class: 'radar-map', id: 'radar-map' });

  ui.products = el('div', { class: 'product-list', role: 'group', 'aria-label': 'Radar product' },
    ...PRODUCTS.map((p) => el('button', {
      class: 'product', dataset: { id: p.id }, 'aria-pressed': 'false',
      onclick: () => {
        // On phones the panel covers the map, so it folds away after a pick.
        if (ui.panel && narrow()) setPanelOpen(false);
        saveSettings({ radar_product: p.id });
        if (p.needsSite && (state.settings.radar_site === 'mosaic')) {
          const near = nearestSite();
          if (near) toast(`Using ${siteName(near)}, the nearest radar. Pick any dot to switch.`);
        }
        rebuild();
      },
    }, p.name)));
  ui.hint = el('p', { class: 'hint' });
  ui.options = el('div', { class: 'chips' });
  ui.site = el('div', { class: 'site-box' });

  // The panel folds down to this one button. On phones it always starts
  // folded; on wider screens it remembers how you left it.
  ui.panelHead = el('button', {
    class: 'panel-head', 'aria-expanded': 'false', title: 'Show or hide radar products',
    onclick: () => {
      const open = !ui.panel.classList.contains('open');
      setPanelOpen(open);
      if (!narrow()) saveSettings({ radar_panel_open: open });
    },
  });
  const panel = el('div', { class: 'float panel-products collapsible fold' },
    ui.panelHead, ui.products, ui.options, ui.hint, ui.site);
  ui.panel = panel;
  setPanelOpen(!narrow() && state.settings.radar_panel_open !== false);

  const layersBody = buildLayersPanel();
  const layersPanel = el('div', { class: 'float panel-layers', hidden: true }, layersBody);
  const layersBtn = el('button', {
    class: 'float icon-btn btn-layers', 'aria-label': 'Layers and display', title: 'Layers and display',
    html: uiIcon('layers', 20),
    onclick: () => { layersPanel.hidden = !layersPanel.hidden; layersBtn.setAttribute('aria-expanded', String(!layersPanel.hidden)); },
  });
  ui.layersBtn = layersBtn;
  ui.openLayers = () => { layersPanel.hidden = false; layersBtn.setAttribute('aria-expanded', 'true'); };
  ui.chips = el('div', { class: 'float ov-chips', role: 'status', 'aria-label': 'Overlays' });
  ui.readout = el('div', { class: 'float readout', hidden: true });
  const homeBtn = el('button', {
    class: 'float icon-btn btn-home', 'aria-label': 'Center on location', title: 'Center on location',
    html: uiIcon('locate', 20),
    onclick: () => { if (state.loc) map.setView([state.loc.lat, state.loc.lon], 8); },
  });

  ui.play = el('button', { class: 'icon-btn', 'aria-label': 'Play', html: uiIcon('play', 18), onclick: () => (playing ? userStop() : play()) });
  ui.prev = el('button', { class: 'icon-btn', 'aria-label': 'Previous frame', html: uiIcon('prev', 18), onclick: () => step(-1) });
  ui.next = el('button', { class: 'icon-btn', 'aria-label': 'Next frame', html: uiIcon('next', 18), onclick: () => step(1) });
  ui.slider = el('input', { type: 'range', min: '0', max: '0', value: '0', 'aria-label': 'Frame', oninput: (e) => { stop(); show(Number(e.target.value)); followLatest = atLatest(); } });
  ui.label = el('span', { class: 'frame-label' });
  ui.spinner = el('span', { class: 'spinner', hidden: true, 'aria-label': 'Loading' });
  const timeline = el('div', { class: 'float timeline' }, ui.prev, ui.play, ui.next, ui.slider, ui.label, ui.spinner);
  ui.legend = el('div', { class: 'float legend' });

  root.append(mapEl, panel, layersBtn, homeBtn, layersPanel, ui.chips, timeline, ui.legend, ui.readout);

  map = createMap(mapEl);
  const start = state.loc ? [state.loc.lat, state.loc.lon] : [38.5, -96];
  map.setView(start, state.loc ? 7 : 4);
  setBorders(map, state.settings.radar_layers.counties);

  map.createPane('reports').style.zIndex = 610;

  map.on('click', onMapClick);

  map.on('moveend', debounce(() => {
    refreshWWA();
    drawReports();
    if (product().field) ensureField();
    if (product().id === 'sat' && frames.length && frames[0].image && satNeedsNewView()) rebuild({ keepIndex: true, force: true });
  }, 450));
  map.on('zoomstart', onZoomStart);
  map.on('zoomend', onZoomEnd);
  // Readout under the pointer. Touch screens have no hover, so it stays off there.
  if (window.matchMedia('(hover: hover)').matches) {
    let pendingMove = null;
    map.on('mousemove', (e) => {
      if (pendingMove) return;
      pendingMove = requestAnimationFrame(() => { pendingMove = null; updateReadout(e.latlng); });
    });
    map.on('mouseout', () => { ui.readout.hidden = true; });
  }

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
    if ('radar_layers' in patch) renderChips();
  });

  document.addEventListener('keydown', (e) => {
    if (state.view !== 'radar' || e.target.closest('input, select, textarea')) return;
    if (e.key === ' ') { e.preventDefault(); if (playing) userStop(); else play(); }
    if (e.key === 'ArrowLeft') step(-1);
    if (e.key === 'ArrowRight') step(1);
  });

  setInterval(() => {
    if (state.view !== 'radar' || document.hidden) return;
    if (Date.now() - lastBuild > refreshAfter() && !product().field) rebuild({ keepIndex: !playing });
    refreshOverlays();
  }, 60000);

  if (state.loc) marker = locationMarker([state.loc.lat, state.loc.lon]).addTo(map);
  loadSites();
}

// How often a product's frames are rebuilt while it's on screen. Timestamped
// frames only add the newest scan, so checking every two minutes is cheap.
// The echo tops loop is twelve "N minutes ago" layers whose pictures all
// shift together, so every rebuild re-downloads the whole loop; that one
// refreshes every ten minutes. Model runs and satellite change more slowly.
function refreshAfter() {
  const p = product();
  const site = activeSite();
  if (p.id === 'tops' && site === 'mosaic') return 10 * 60000;
  if (p.id === 'future') return 30 * 60000;
  if (p.id === 'rain') return 5 * 60000;
  if (p.id === 'sat') return 10 * 60000;
  return 2 * 60000;
}

export function showRadar() {
  if (!map) return;
  setTimeout(() => {
    map.invalidateSize();
    if (!built || Date.now() - lastBuild > refreshAfter()) rebuild({ keepIndex: true });
    refreshOverlays();
    refreshOutlook();
    renderChips();
  }, 30);
}

export function hideRadar() {
  stop();
}

export function focusGeometry(geometry) {
  if (!map || !geometry) return;
  if (highlight) map.removeLayer(highlight);
  clearTimeout(highlightTimer);
  const shape = L.geoJSON(geometry, {
    pane: 'top', interactive: false,
    style: { color: '#ffffff', weight: 3, fill: false, dashArray: '2 6', className: 'focus-outline' },
  });
  // An empty or malformed geometry has no bounds, and fitBounds would throw on it.
  const bounds = shape.getBounds();
  if (!bounds.isValid()) return;
  highlight = shape.addTo(map);
  setTimeout(() => map.fitBounds(bounds.pad(0.6), { maxZoom: 10 }), 60);
  highlightTimer = setTimeout(() => { if (highlight === shape) { map.removeLayer(shape); highlight = null; } }, 30000);
}
