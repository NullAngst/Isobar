import { state } from './core.js';

// ------------------------------------------------------------------ units

const us = () => state.settings.units === 'us';

export const toF = (c) => c * 9 / 5 + 32;

export function tempVal(c) {
  if (c === null || c === undefined) return null;
  return us() ? toF(c) : c;
}

export function temp(c, withUnit = false) {
  const v = tempVal(c);
  if (v === null) return '--';
  return `${Math.round(v)}°${withUnit ? (us() ? 'F' : 'C') : ''}`;
}

export function windVal(kmh) {
  if (kmh === null || kmh === undefined) return null;
  return us() ? kmh * 0.621371 : kmh;
}

export function wind(kmh) {
  const v = windVal(kmh);
  return v === null ? '--' : `${Math.round(v)} ${us() ? 'mph' : 'km/h'}`;
}

export const windUnit = () => (us() ? 'mph' : 'km/h');

export function precip(mm, zeroDash = false) {
  if (mm === null || mm === undefined) return '--';
  if (zeroDash && mm <= 0) return '';
  return us() ? `${(mm / 25.4).toFixed(2)} in` : `${mm.toFixed(1)} mm`;
}

export function snow(cm) {
  if (!cm) return '';
  return us() ? `${(cm / 2.54).toFixed(1)} in snow` : `${cm.toFixed(1)} cm snow`;
}

export function pressure(hpa) {
  if (hpa === null || hpa === undefined) return '--';
  return us() ? `${(hpa * 0.02953).toFixed(2)} inHg` : `${Math.round(hpa)} hPa`;
}

export function distance(m) {
  if (m === null || m === undefined) return '--';
  if (us()) {
    const mi = m / 1609.34;
    return mi >= 10 ? '10+ mi' : `${mi.toFixed(1)} mi`;
  }
  const km = m / 1000;
  return km >= 16 ? '16+ km' : `${km.toFixed(1)} km`;
}

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
export function compass(deg) {
  if (deg === null || deg === undefined) return '';
  return COMPASS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
}

// ------------------------------------------------------------------ time
// Open-Meteo returns wall-clock strings in the location's zone ("2026-09-30T14:00").
// They are parsed into Date objects whose UTC fields hold that wall time and
// always formatted with timeZone: 'UTC'. Real instants (ISO with offset) are
// formatted in the location's IANA zone instead.

export function wall(str) {
  const [d, t = '00:00'] = str.split('T');
  const [y, mo, da] = d.split('-').map(Number);
  const [h, mi] = t.split(':').map(Number);
  return new Date(Date.UTC(y, mo - 1, da, h, mi));
}

export function wallNow() {
  return new Date(Date.now() + state.offset * 1000);
}

const fmtCache = new Map();
function fmt(options) {
  const key = JSON.stringify(options);
  if (!fmtCache.has(key)) fmtCache.set(key, new Intl.DateTimeFormat('en-US', options));
  return fmtCache.get(key);
}

const h12 = () => state.settings.clock !== '24';

export function hourLabel(date) {
  if (!h12()) return fmt({ hour: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(date);
  return fmt({ hour: 'numeric', hour12: true, timeZone: 'UTC' }).format(date).replace(' ', '').toLowerCase();
}

export function timeLabel(date) {
  return fmt({ hour: 'numeric', minute: '2-digit', hour12: h12(), timeZone: 'UTC' }).format(date);
}

export function dayShort(date) {
  return fmt({ weekday: 'short', timeZone: 'UTC' }).format(date);
}

export function dayLong(date) {
  return fmt({ weekday: 'long', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(date);
}

export function dateShort(date) {
  return fmt({ month: 'short', day: 'numeric', timeZone: 'UTC' }).format(date);
}

export function instant(iso, withDay = false) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const options = { hour: 'numeric', minute: '2-digit', hour12: h12(), timeZone: state.tz };
  if (withDay) Object.assign(options, { weekday: 'short', month: 'short', day: 'numeric' });
  try {
    return fmt(options).format(date);
  } catch {
    return fmt({ ...options, timeZone: 'UTC' }).format(date) + ' UTC';
  }
}

export function ago(iso) {
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return '';
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  return `${h} h ${min % 60} min ago`;
}

// ------------------------------------------------------------------ colors

function lerpStops(stops, value) {
  if (value <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    if (value <= stops[i][0]) {
      const [v0, c0] = stops[i - 1];
      const [v1, c1] = stops[i];
      const t = (value - v0) / (v1 - v0);
      return c0.map((c, k) => Math.round(c + (c1[k] - c) * t));
    }
  }
  return stops[stops.length - 1][1];
}

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

// Thermal scale keyed in Fahrenheit.
const TEMP_STOPS = [
  [-20, '#d4c4ff'], [0, '#9a82ef'], [15, '#5d72e6'], [32, '#4aa8e8'], [45, '#45c9b4'],
  [55, '#86cf62'], [65, '#d3cf47'], [75, '#f0ad3a'], [85, '#ee7a31'], [95, '#de452e'],
  [105, '#b4234d'], [115, '#7c1442'],
].map(([v, c]) => [v, hex(c)]);

export function tempRGB(c) {
  return lerpStops(TEMP_STOPS, toF(c));
}

export function tempColor(c) {
  const [r, g, b] = tempRGB(c);
  return `rgb(${r},${g},${b})`;
}

// Wind scale keyed in mph.
const WIND_STOPS = [
  [0, '#4f7fbf'], [8, '#46b5b0'], [16, '#8bcf58'], [25, '#e8c83c'], [35, '#ee8a30'],
  [50, '#dc3f2c'], [70, '#b02a9e'], [100, '#f2e6ff'],
].map(([v, c]) => [v, hex(c)]);

export function windRGB(kmh) {
  return lerpStops(WIND_STOPS, kmh * 0.621371);
}

// ------------------------------------------------------------------ weather codes

const WX = {
  0: 'Clear', 1: 'Mostly clear', 2: 'Partly cloudy', 3: 'Cloudy',
  45: 'Fog', 48: 'Freezing fog',
  51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
  56: 'Freezing drizzle', 57: 'Freezing drizzle',
  61: 'Light rain', 63: 'Rain', 65: 'Heavy rain',
  66: 'Freezing rain', 67: 'Heavy freezing rain',
  71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains',
  80: 'Showers', 81: 'Showers', 82: 'Heavy showers',
  85: 'Snow showers', 86: 'Heavy snow showers',
  95: 'Thunderstorms', 96: 'Thunderstorms with hail', 99: 'Severe thunderstorms with hail',
};

export function wxText(code, isDay = 1) {
  if ((code === 0 || code === 1) && !isDay) return code === 0 ? 'Clear' : 'Mostly clear';
  if (code === 0 && isDay) return 'Sunny';
  return WX[code] ?? 'Unknown';
}

// ------------------------------------------------------------------ hazards
// NWS map colors by VTEC phenomenon.significance, plus names for the legend/popups.

export const HAZARDS = {
  'TO.W': ['Tornado Warning', '#ff2a2a'],
  'SV.W': ['Severe Thunderstorm Warning', '#ffa500'],
  'EW.W': ['Extreme Wind Warning', '#ff8c00'],
  'FF.W': ['Flash Flood Warning', '#cc2a55'],
  'SQ.W': ['Snow Squall Warning', '#c71585'],
  'MA.W': ['Special Marine Warning', '#ffa500'],
  'FA.W': ['Flood Warning', '#00e05a'],
  'FL.W': ['Flood Warning', '#00e05a'],
  'WS.W': ['Winter Storm Warning', '#ff69b4'],
  'BZ.W': ['Blizzard Warning', '#ff4500'],
  'IS.W': ['Ice Storm Warning', '#b02aa8'],
  'LE.W': ['Lake Effect Snow Warning', '#008b8b'],
  'HW.W': ['High Wind Warning', '#daa520'],
  'XH.W': ['Extreme Heat Warning', '#c71585'],
  'EH.W': ['Excessive Heat Warning', '#c71585'],
  'EC.W': ['Extreme Cold Warning', '#3b5bff'],
  'HU.W': ['Hurricane Warning', '#dc143c'],
  'TR.W': ['Tropical Storm Warning', '#d0342c'],
  'SS.W': ['Storm Surge Warning', '#b524f7'],
  'FW.W': ['Red Flag Warning', '#ff1493'],
  'FZ.W': ['Freeze Warning', '#6a5acd'],
  'DS.W': ['Dust Storm Warning', '#ffe4c4'],
  'GL.W': ['Gale Warning', '#dda0dd'],
  'SR.W': ['Storm Warning', '#9400d3'],
  'TO.A': ['Tornado Watch', '#ffee00'],
  'SV.A': ['Severe Thunderstorm Watch', '#e8789e'],
  'FF.A': ['Flash Flood Watch', '#2e8b57'],
  'FA.A': ['Flood Watch', '#2e8b57'],
  'WS.A': ['Winter Storm Watch', '#4682b4'],
  'HU.A': ['Hurricane Watch', '#ff00ff'],
  'TR.A': ['Tropical Storm Watch', '#f08080'],
  'SS.A': ['Storm Surge Watch', '#db7ff7'],
  'FW.A': ['Fire Weather Watch', '#ffdead'],
  'HW.A': ['High Wind Watch', '#b8860b'],
  'FZ.A': ['Freeze Watch', '#00ced1'],
  'XH.A': ['Extreme Heat Watch', '#a01a50'],
  'EC.A': ['Extreme Cold Watch', '#5f9ea0'],
  'WW.Y': ['Winter Weather Advisory', '#7b68ee'],
  'WI.Y': ['Wind Advisory', '#d2b48c'],
  'FG.Y': ['Dense Fog Advisory', '#8c99a6'],
  'HT.Y': ['Heat Advisory', '#ff7f50'],
  'FA.Y': ['Flood Advisory', '#00ff7f'],
  'FL.Y': ['Flood Advisory', '#00ff7f'],
  'CF.Y': ['Coastal Flood Advisory', '#7cfc00'],
  'SC.Y': ['Small Craft Advisory', '#d8bfd8'],
  'FR.Y': ['Frost Advisory', '#6495ed'],
  'DU.Y': ['Blowing Dust Advisory', '#bdb76b'],
  'ZF.Y': ['Freezing Fog Advisory', '#008080'],
  'CW.Y': ['Cold Weather Advisory', '#afeeee'],
  'AQ.Y': ['Air Quality Alert', '#9a9a9a'],
  'LE.Y': ['Lake Effect Snow Advisory', '#48d1cc'],
  'SU.Y': ['High Surf Advisory', '#ba55d3'],
  'RP.S': ['Rip Current Statement', '#40e0d0'],
};

const SIG_FALLBACK = { W: '#ef4b4b', A: '#e8d23a', Y: '#8f9aa8', S: '#9fb4c8' };

export function hazardColor(phenom, sig) {
  return (HAZARDS[`${phenom}.${sig}`] || [null, SIG_FALLBACK[sig] || '#9aa6b2'])[1];
}

export function hazardName(phenom, sig, fallback) {
  return (HAZARDS[`${phenom}.${sig}`] || [fallback || `${phenom}.${sig}`])[0];
}

const BY_NAME = new Map(Object.values(HAZARDS).map(([name, color]) => [name.toLowerCase(), color]));

export function eventColor(event) {
  const key = (event || '').toLowerCase();
  if (BY_NAME.has(key)) return BY_NAME.get(key);
  if (key.endsWith('warning')) return SIG_FALLBACK.W;
  if (key.endsWith('watch')) return SIG_FALLBACK.A;
  if (key.endsWith('advisory')) return SIG_FALLBACK.Y;
  return SIG_FALLBACK.S;
}

// SPC categorical colors, used when the GeoJSON leaves them out.
export const SPC_COLORS = {
  TSTM: '#c1e9c1', MRGL: '#66a366', SLGT: '#f6f67f', ENH: '#e6c27f', MDT: '#e67f7f', HIGH: '#ff7fff',
};
