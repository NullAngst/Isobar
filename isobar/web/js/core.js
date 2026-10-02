// Shared state, API access and a tiny event bus.

// The session token arrives in the URL the app opens. It's moved into
// sessionStorage and taken out of the address bar right away, so it doesn't
// sit in browser history (in --browser mode) and survives a page reload.
const TOKEN = (() => {
  const params = new URLSearchParams(location.search);
  let token = params.get('t');
  try {
    if (token) sessionStorage.setItem('isobar-token', token);
    else token = sessionStorage.getItem('isobar-token');
  } catch { /* storage blocked: keep the URL token for this load */ }
  if (params.has('t')) {
    params.delete('t');
    const rest = params.toString();
    history.replaceState(null, '', `${location.pathname}${rest ? `?${rest}` : ''}${location.hash}`);
  }
  return token || '';
})();

export const IEM = 'https://mesonet.agron.iastate.edu';

export const state = {
  settings: null,
  loc: null,          // { name, lat, lon }
  data: null,         // /api/weather bundle
  spc: null,          // /api/spc/point days
  view: 'now',
  tz: 'UTC',          // IANA zone of the selected location
  offset: 0,          // UTC offset of the selected location, seconds
};

const handlers = {};

export function on(event, fn) {
  (handlers[event] ||= []).push(fn);
}

export function emit(event, payload) {
  for (const fn of handlers[event] || []) {
    try { fn(payload); } catch (err) { console.error(err); }
  }
}

export async function api(path, params = {}, options = {}) {
  const url = new URL(path, location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) url.searchParams.set(key, value);
  }
  const resp = await fetch(url, {
    ...options,
    headers: { 'X-Isobar-Token': TOKEN, 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  let body = null;
  try { body = await resp.json(); } catch { body = null; }
  if (!resp.ok) throw new Error((body && body.error) || `HTTP ${resp.status}`);
  return body;
}

export function proxyUrl(url) {
  return `/proxy/tile?t=${encodeURIComponent(TOKEN)}&u=${encodeURIComponent(url)}`;
}

let saveTimer = null;
let pending = {};

export function saveSettings(patch) {
  // Merge locally right away, write to disk shortly after.
  deepMerge(state.settings, patch);
  pending = deepMerge(pending, patch);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    const body = pending;
    pending = {};
    try {
      await api('/api/settings', {}, { method: 'POST', body: JSON.stringify(body) });
    } catch (err) {
      toast(`Settings not saved: ${err.message}`);
    }
  }, 300);
  emit('settings', patch);
}

function deepMerge(target, patch) {
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === 'object' && !Array.isArray(value) && target[key] && typeof target[key] === 'object') {
      deepMerge(target[key], value);
    } else {
      target[key] = value;
    }
  }
  return target;
}

export function openExternal(url) {
  api('/api/open', {}, { method: 'POST', body: JSON.stringify({ url }) }).catch(() => window.open(url, '_blank'));
}

let toastTimer = null;
export function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function esc(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
