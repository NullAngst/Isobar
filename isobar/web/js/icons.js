// Line icons drawn on a 24x24 grid. Weather icons get a small color accent
// on the element that carries the meaning (sun, drops, bolt); clouds stay neutral.

const SUN = '#f0b23c';
const MOON = '#c9d4e2';
const RAIN = '#59a7e6';
const SNOW = '#cfe4f6';
const BOLT = '#f3c63c';

const CLOUD = 'M6.5 18h11a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.6 11.3 3.4 3.4 0 0 0 6.5 18z';

function svg(inner, size, extra = '') {
  return `<svg class="ic" ${extra} width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}

function sun(cx = 12, cy = 12, r = 4.2, ray = 2.6) {
  let rays = '';
  for (let i = 0; i < 8; i++) {
    const a = (i * Math.PI) / 4;
    const x1 = cx + Math.cos(a) * (r + 1.6);
    const y1 = cy + Math.sin(a) * (r + 1.6);
    const x2 = cx + Math.cos(a) * (r + 1.6 + ray);
    const y2 = cy + Math.sin(a) * (r + 1.6 + ray);
    rays += `M${x1.toFixed(2)} ${y1.toFixed(2)}L${x2.toFixed(2)} ${y2.toFixed(2)}`;
  }
  return `<g stroke="${SUN}"><circle cx="${cx}" cy="${cy}" r="${r}"/><path d="${rays}"/></g>`;
}

function moon(dx = 0, dy = 0, s = 1) {
  return `<path stroke="${MOON}" transform="translate(${dx} ${dy}) scale(${s})" d="M15.5 3.6a8.2 8.2 0 1 0 4.9 13.9A7 7 0 0 1 15.5 3.6z"/>`;
}

function cloud(dy = 0) {
  return `<path class="ic-cloud" transform="translate(0 ${dy})" d="${CLOUD}"/>`;
}

function drops(color, pattern) {
  return `<path stroke="${color}" d="${pattern}"/>`;
}

const RAIN_LINES = 'M8.5 18.6l-1 2.6M12.5 18.6l-1 2.6M16.5 18.6l-1 2.6';
const HEAVY_LINES = 'M7.5 18.4l-1.3 3.3M11 18.4l-1.3 3.3M14.5 18.4l-1.3 3.3M18 18.4l-1.3 3.3';
const DRIZZLE = 'M8.2 19.4v.1M12 20.6v.1M15.8 19.4v.1';
const FLAKES = 'M8 19.2v.1M12 21v.1M16 19.2v.1M10 21.8v.1M14 21.8v.1';

function partly(isDay) {
  const back = isDay ? sun(8.2, 7.6, 2.9, 1.7) : moon(0, -1, 0.62);
  return back + `<path class="ic-cloud ic-fill" transform="translate(1.6 1.4) scale(0.92)" d="${CLOUD}"/>`;
}

export function wxIcon(code, isDay = 1, size = 28) {
  const day = !!isDay;
  let inner;
  switch (code) {
    case 0:
    case 1:
      inner = day ? sun() : moon();
      break;
    case 2:
      inner = partly(day);
      break;
    case 3:
      inner = cloud(0);
      break;
    case 45:
    case 48:
      inner = '<path d="M4 9.5h16M3 13.5h18M5 17.5h14"/>';
      break;
    case 51:
    case 53:
    case 55:
      inner = cloud(-3) + drops(RAIN, DRIZZLE);
      break;
    case 56:
    case 57:
    case 66:
    case 67:
      inner = cloud(-3) + drops(RAIN, 'M8.5 18.6l-1 2.6M15.5 18.6l-1 2.6') + drops(SNOW, 'M12 20.2v.1');
      break;
    case 61:
    case 63:
      inner = cloud(-3) + drops(RAIN, RAIN_LINES);
      break;
    case 65:
    case 82:
      inner = cloud(-3) + drops(RAIN, HEAVY_LINES);
      break;
    case 80:
    case 81:
      inner = (day ? sun(7.6, 6.4, 2.4, 1.4) : moon(0, -2, 0.56)) +
        `<path class="ic-cloud ic-fill" transform="translate(1.4 -2.2) scale(0.92)" d="${CLOUD}"/>` + drops(RAIN, RAIN_LINES);
      break;
    case 71:
    case 73:
    case 75:
    case 77:
    case 85:
    case 86:
      inner = cloud(-3) + drops(SNOW, FLAKES);
      break;
    case 95:
    case 96:
    case 99:
      inner = cloud(-3) + `<path stroke="${BOLT}" d="M12.8 15.6l-2.6 3.6h3.2l-2.2 3.4"/>`;
      break;
    default:
      inner = cloud(0);
  }
  return svg(inner, size, `data-code="${code}"`);
}

const UI = {
  now: '<path d="M10 14.5V5a2 2 0 1 1 4 0v9.5a4 4 0 1 1-4 0z"/><path d="M12 9v7"/>',
  hourly: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  radar: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><path d="M12 12l6-6"/><circle cx="12" cy="12" r=".6"/>',
  outlooks: '<path d="M3.5 18.5c3-1.5 5.5-6 8.5-6s5.5 4.5 8.5 6"/><path d="M6.5 18.5c2-1 3.5-3.5 5.5-3.5s3.5 2.5 5.5 3.5"/><path d="M12 4.5v4"/>',
  alerts: '<path d="M12 4l9 15.5H3z"/><path d="M12 10v4.2M12 17v.1"/>',
  discussion: '<path d="M5 6h14M5 10h14M5 14h9M5 18h11"/>',
  settings: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  search: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
  star: '<path d="M12 4l2.4 5 5.4.7-4 3.8 1 5.4-4.8-2.6-4.8 2.6 1-5.4-4-3.8 5.4-.7z"/>',
  refresh: '<path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4.5V9h-4.5"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  pause: '<path d="M8.5 5.5v13M15.5 5.5v13"/>',
  prev: '<path d="M15 6l-6 6 6 6"/>',
  next: '<path d="M9 6l6 6-6 6"/>',
  layers: '<path d="M12 4l8.5 4.5L12 13 3.5 8.5z"/><path d="M3.5 12.5L12 17l8.5-4.5"/><path d="M3.5 16.5L12 21l8.5-4.5"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  locate: '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>',
  chevron: '<path d="M7 10l5 5 5-5"/>',
  external: '<path d="M14 5h5v5M19 5l-8 8M17 14v5H5V7h5"/>',
  pin: '<path d="M12 21s-6.5-6.2-6.5-11A6.5 6.5 0 0 1 18.5 10c0 4.8-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
};

export function uiIcon(name, size = 20) {
  return svg(UI[name] || '', size);
}

export function arrowIcon(deg, size = 16) {
  // Wind direction is where the wind comes from; the arrow points where it goes.
  return `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><g transform="rotate(${(deg + 180) % 360} 12 12)"><path d="M12 4v16M7 9l5-5 5 5"/></g></svg>`;
}
