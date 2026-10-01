// Isobar map tile cache.
//
// Runs as a service worker, so it sits between the maps and the tile servers.
// Every tile is stored on disk with its own expiry. Until that expiry passes,
// zooming or panning back to a tile is served from disk with no download.
// After it passes, the next request fetches a fresh copy and replaces it.
//
// How long a tile stays good depends on what it is:
//   timestamped radar scans and mosaics   24 hours (a past scan never changes)
//   county and state lines                30 days
//   base maps (CARTO, Esri)               7 days
//   legends                               7 days
//   HRRR future radar                     20 minutes (new model run every hour)
//   "latest" products                     4 minutes (echo tops, rainfall, satellite,
//                                          single-site fallback)
//
// If the network is down, an expired tile is still served rather than a blank one.

const CACHE = 'isobar-tiles-v1';
const MAX_ENTRIES = 6000;
const TRIM_EVERY = 200;
const KEEP_EXPIRED = 60 * 60 * 1000; // expired tiles hang around an hour for offline use

const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

let puts = 0;
let trimming = false;

function ttlFor(href) {
  let u;
  try { u = new URL(href); } catch { return 0; }
  const host = u.hostname;

  if (/^mesonet\d?\.agron\.iastate\.edu$/.test(host)) {
    const marker = '/tile.py/1.0.0/';
    const at = u.pathname.indexOf(marker);
    if (at >= 0) {
      const layer = decodeURIComponent(u.pathname.slice(at + marker.length).split('/')[0]);
      if (/^(uscounties|usstates)/.test(layer)) return 30 * DAY;
      if (/\d{12}$/.test(layer)) return DAY;
      if (layer.startsWith('hrrr::')) return 20 * MIN;
      return 4 * MIN;
    }
    if (u.pathname.startsWith('/GIS/legends/') || u.pathname.startsWith('/images/')) return 7 * DAY;
    return 0;
  }
  if (host.endsWith('basemaps.cartocdn.com')) return 7 * DAY;
  if (host.endsWith('arcgisonline.com')) return 7 * DAY;
  return 0;
}

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith('isobar-tiles-') && name !== CACHE) await caches.delete(name);
    }
    await self.clients.claim();
    sweep();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const ttl = ttlFor(req.url);
  if (!ttl) return; // not a tile: leave it alone
  event.respondWith(serve(req, ttl));
});

self.addEventListener('message', (event) => {
  if (event.data === 'sweep') sweep();
});

function expiresOf(res) {
  return Number(res.headers.get('x-isobar-expires')) || 0;
}

async function serve(req, ttl) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req.url);
  const now = Date.now();
  if (hit && expiresOf(hit) > now) return hit;

  let res;
  try {
    res = await fetch(req);
  } catch (err) {
    if (hit) return hit;
    throw err;
  }
  // Opaque responses (no CORS) can't be inspected and Chrome bills each one
  // as megabytes of quota, so those are passed through uncached.
  if (res.ok && res.type !== 'opaque') {
    try {
      const body = await res.clone().blob();
      const headers = new Headers(res.headers);
      headers.set('x-isobar-expires', String(now + ttl));
      await cache.put(req.url, new Response(body, { status: res.status, statusText: res.statusText, headers }));
      if (++puts % TRIM_EVERY === 0) trim(cache);
    } catch (err) {
      // Disk full or quota hit: still show the tile, just don't keep it.
    }
  } else if (!res.ok && hit) {
    return hit;
  }
  return res;
}

async function trim(cache) {
  // Oldest writes first, since cache.put moves an entry to the end.
  if (trimming) return;
  trimming = true;
  try {
    const keys = await cache.keys();
    const excess = keys.length - MAX_ENTRIES;
    for (let i = 0; i < excess; i++) await cache.delete(keys[i]);
  } finally {
    trimming = false;
  }
}

async function sweep() {
  // Drops tiles that expired over an hour ago. Runs at startup and on request.
  if (trimming) return;
  trimming = true;
  try {
    const cache = await caches.open(CACHE);
    const cutoff = Date.now() - KEEP_EXPIRED;
    for (const req of await cache.keys()) {
      const res = await cache.match(req);
      if (!res || expiresOf(res) < cutoff) await cache.delete(req);
    }
  } finally {
    trimming = false;
  }
  await trim(await caches.open(CACHE));
}
