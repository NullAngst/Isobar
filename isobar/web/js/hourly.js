// Hourly chart (shared with the Now view) and the Hourly view.

import { state, el } from './core.js';
import { tenDay } from './days.js';
import { wxIcon, arrowIcon } from './icons.js';
import {
  temp, tempVal, tempColor, wind, windVal, precip, compass, wxText,
  wall, hourLabel, dayShort, dayLong, timeLabel,
} from './util.js';

const NS = 'http://www.w3.org/2000/svg';
let gradSeq = 0;

function s(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function smoothPath(points) {
  // Monotone-ish cubic through the points, so the curve never overshoots.
  if (!points.length) return '';
  let d = `M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
  for (let i = 1; i < points.length; i++) {
    const [x0, y0] = points[i - 1];
    const [x1, y1] = points[i];
    const mx = (x0 + x1) / 2;
    d += `C${mx.toFixed(1)} ${y0.toFixed(1)} ${mx.toFixed(1)} ${y1.toFixed(1)} ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  }
  return d;
}

function layoutFor(hours, compact, fit, n) {
  // Default column widths fit each range in a typical desktop window. With
  // `fit` (the space available, in px) the columns stretch or squeeze to fill
  // it, and the label spacing follows the column width so nothing collides.
  let L;
  if (hours <= 24) L = { col: 44, windEvery: compact ? 0 : 2 };
  else if (hours <= 48) L = { col: 22, iconEvery: 3, labelEvery: 3, hourEvery: 3, windEvery: 3 };
  else L = { col: 6, iconEvery: 6, labelEvery: 12, hourEvery: 12, windEvery: 6 };
  if (fit && n > 1) {
    const want = (fit - 36) / (n - 1);
    L.col = Math.min(120, Math.max(compact ? 26 : L.col, want));
  }
  if (hours <= 24) {
    L.iconEvery = L.col >= 36 ? 1 : 2;
    L.labelEvery = L.col >= 34 ? 1 : 2;
    L.hourEvery = L.col >= 42 ? 1 : 2;
    if (L.windEvery) L.windEvery = L.col >= 60 ? 1 : 2;
  }
  return L;
}

export function hourlyChart(f, startIdx, hours, { compact = false, fit = 0 } = {}) {
  const h = f.hourly;
  const end = Math.min(h.time.length, startIdx + hours);
  const idx = [];
  for (let i = startIdx; i < end; i++) idx.push(i);
  const wrap = el('div', { class: `hchart ${compact ? 'hchart-compact' : ''}` });
  if (idx.length < 2) return wrap;

  const L = layoutFor(hours, compact, fit, idx.length);
  const padX = 18;
  const width = padX * 2 + (idx.length - 1) * L.col;
  const rows = {
    icon: 4,
    tempTop: 52,
    tempBot: compact ? 122 : 142,
  };
  rows.popTop = rows.tempBot + 22;
  rows.popBot = rows.popTop + 34;
  rows.wind = L.windEvery ? rows.popBot + 22 : rows.popBot;
  rows.hour = rows.wind + (L.windEvery ? 46 : 18);
  const height = rows.hour + 8;

  const x = (k) => padX + k * L.col;
  const temps = idx.map((i) => tempVal(h.temperature_2m[i]));
  const feels = idx.map((i) => tempVal(h.apparent_temperature[i]));
  const all = temps.concat(feels).filter((v) => v !== null);
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  if (hi - lo < 6) { const mid = (hi + lo) / 2; lo = mid - 3; hi = mid + 3; }
  const y = (v) => rows.tempBot - ((v - lo) / (hi - lo)) * (rows.tempBot - rows.tempTop);

  const svg = s('svg', { class: 'hchart-svg', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Hourly temperature and precipitation' });

  // Night bands.
  let nightStart = null;
  idx.forEach((i, k) => {
    const night = h.is_day[i] === 0;
    if (night && nightStart === null) nightStart = k;
    if ((!night || k === idx.length - 1) && nightStart !== null) {
      const stop = night ? k : k - 1;
      svg.append(s('rect', {
        class: 'hc-night', x: Math.max(0, x(nightStart) - L.col / 2), y: 0,
        width: Math.max(L.col, (stop - nightStart + 1) * L.col), height: rows.hour - 6,
      }));
      nightStart = null;
    }
  });

  // Day boundaries.
  idx.forEach((i, k) => {
    if (k > 0 && h.time[i].endsWith('T00:00')) {
      svg.append(s('line', { class: 'hc-day', x1: x(k), x2: x(k), y1: rows.tempTop - 22, y2: rows.hour - 6 }));
      svg.append(s('text', { class: 'hc-dayname', x: x(k) + 4, y: rows.tempTop - 12 }, dayShort(wall(h.time[i]))));
    }
  });

  // Temperature gradient, keyed to the actual values on screen.
  const gid = `tg${++gradSeq}`;
  const grad = s('linearGradient', { id: gid, gradientUnits: 'userSpaceOnUse', x1: 0, x2: 0, y1: rows.tempBot, y2: rows.tempTop });
  const loC = state.settings.units === 'us' ? (lo - 32) * 5 / 9 : lo;
  const hiC = state.settings.units === 'us' ? (hi - 32) * 5 / 9 : hi;
  for (let t = 0; t <= 4; t++) {
    grad.append(s('stop', { offset: `${t * 25}%`, 'stop-color': tempColor(loC + (hiC - loC) * (t / 4)) }));
  }
  const fade = s('linearGradient', { id: `${gid}f`, x1: 0, x2: 0, y1: 0, y2: 1 });
  fade.append(s('stop', { offset: '0%', 'stop-color': 'white', 'stop-opacity': 0.22 }));
  fade.append(s('stop', { offset: '100%', 'stop-color': 'white', 'stop-opacity': 0 }));
  const mask = s('mask', { id: `${gid}m` });
  mask.append(s('rect', { x: 0, y: rows.tempTop - 10, width, height: rows.tempBot - rows.tempTop + 20, fill: `url(#${gid}f)` }));
  const defs = s('defs');
  defs.append(grad, fade, mask);
  svg.append(defs);

  const tPts = temps.map((v, k) => [x(k), y(v)]);
  const fPts = feels.map((v, k) => [x(k), y(v)]);
  const tPath = smoothPath(tPts);
  svg.append(s('path', {
    d: `${tPath}L${x(idx.length - 1)} ${rows.tempBot + 8}L${x(0)} ${rows.tempBot + 8}Z`,
    fill: `url(#${gid})`, mask: `url(#${gid}m)`,
  }));
  svg.append(s('path', { class: 'hc-feels', d: smoothPath(fPts) }));
  svg.append(s('path', { class: 'hc-temp', d: tPath, stroke: `url(#${gid})` }));

  // Icons, temperature labels.
  idx.forEach((i, k) => {
    if (k % L.iconEvery === 0) {
      const g = s('g', { transform: `translate(${x(k) - 12} ${rows.icon})` });
      g.innerHTML = wxIcon(h.weather_code[i], h.is_day[i], 24);
      svg.append(g);
    }
    if (k % L.labelEvery === 0) {
      svg.append(s('text', { class: 'hc-tlabel', x: x(k), y: tPts[k][1] - 9 }, temp(h.temperature_2m[i])));
    }
  });

  // Precipitation chance bars and amounts.
  svg.append(s('line', { class: 'hc-base', x1: padX - 6, x2: width - padX + 6, y1: rows.popBot, y2: rows.popBot }));
  const barW = Math.max(3, Math.min(14, L.col * 0.55));
  idx.forEach((i, k) => {
    const pop = h.precipitation_probability[i] ?? 0;
    if (pop > 0) {
      const bh = (pop / 100) * (rows.popBot - rows.popTop);
      const snowy = (h.snowfall[i] ?? 0) > 0;
      svg.append(s('rect', {
        class: snowy ? 'hc-pop hc-pop-snow' : 'hc-pop', x: x(k) - barW / 2, y: rows.popBot - bh, width: barW, height: bh, rx: Math.min(2, barW / 2),
        opacity: (0.35 + 0.65 * Math.min(1, (h.precipitation[i] ?? 0) / 4)).toFixed(2),
      }));
    }
    if (k % L.labelEvery === 0 && pop >= 20 && L.col * L.labelEvery >= 28) {
      svg.append(s('text', { class: 'hc-poplabel', x: x(k), y: rows.popTop - 5 }, `${pop}%`));
    }
  });

  // Wind.
  if (L.windEvery) {
    idx.forEach((i, k) => {
      if (k % L.windEvery !== 0) return;
      const g = s('g', { class: 'hc-wind', transform: `translate(${x(k) - 7} ${rows.wind - 4})` });
      g.innerHTML = arrowIcon(h.wind_direction_10m[i], 14);
      svg.append(g);
      svg.append(s('text', { class: 'hc-windlabel', x: x(k), y: rows.wind + 24 }, String(Math.round(windVal(h.wind_speed_10m[i])))));
    });
  }

  // Hour labels.
  idx.forEach((i, k) => {
    const date = wall(h.time[i]);
    const every = L.hourEvery;
    if (k % every !== 0) return;
    const label = k === 0 ? 'Now' : (hours > 48 && date.getUTCHours() === 0 ? dayShort(date) : hourLabel(date));
    svg.append(s('text', { class: 'hc-hour', x: x(k), y: rows.hour }, label));
  });

  // Hover readout.
  const guide = s('line', { class: 'hc-guide', y1: rows.tempTop - 24, y2: rows.hour - 6, visibility: 'hidden' });
  const dot = s('circle', { class: 'hc-dot', r: 4, visibility: 'hidden' });
  svg.append(guide, dot);
  const tip = el('div', { class: 'hc-tip', hidden: true });
  const hit = s('rect', { x: 0, y: 0, width, height, fill: 'transparent' });
  svg.append(hit);

  const move = (evt) => {
    const box = svg.getBoundingClientRect();
    const px = ((evt.clientX - box.left) / box.width) * width;
    const k = Math.max(0, Math.min(idx.length - 1, Math.round((px - padX) / L.col)));
    const i = idx[k];
    guide.setAttribute('x1', x(k));
    guide.setAttribute('x2', x(k));
    guide.setAttribute('visibility', 'visible');
    dot.setAttribute('cx', x(k));
    dot.setAttribute('cy', tPts[k][1]);
    dot.setAttribute('fill', tempColor(h.temperature_2m[i]));
    dot.setAttribute('visibility', 'visible');
    const amount = h.precipitation[i] > 0 ? `, ${precip(h.precipitation[i])}` : '';
    tip.replaceChildren(
      el('div', { class: 'hc-tip-time' }, `${dayShort(wall(h.time[i]))} ${timeLabel(wall(h.time[i]))}`),
      el('div', { class: 'hc-tip-main' }, `${temp(h.temperature_2m[i])} ${wxText(h.weather_code[i], h.is_day[i])}`),
      el('div', {}, `Feels like ${temp(h.apparent_temperature[i])}`),
      el('div', {}, `Precip ${h.precipitation_probability[i] ?? 0}%${amount}`),
      el('div', {}, `Wind ${wind(h.wind_speed_10m[i])} ${compass(h.wind_direction_10m[i])}`));
    tip.hidden = false;
    // The tip lives inside the scrolling wrapper, so content coordinates work.
    const left = x(k);
    const flip = left + 180 > wrap.scrollLeft + wrap.clientWidth;
    tip.style.left = `${flip ? left - 168 : left + 14}px`;
  };
  hit.addEventListener('pointermove', move);
  hit.addEventListener('pointerdown', move);
  hit.addEventListener('pointerleave', () => {
    tip.hidden = true;
    guide.setAttribute('visibility', 'hidden');
    dot.setAttribute('visibility', 'hidden');
  });

  wrap.append(svg, tip);
  if (!compact) {
    wrap.append(el('div', { class: 'hc-key' },
      el('span', { class: 'key-temp' }, 'Temperature'),
      el('span', { class: 'key-feels' }, 'Feels like'),
      el('span', { class: 'key-pop' }, 'Precip chance'),
      L.windEvery ? el('span', {}, `Wind, ${state.settings.units === 'us' ? 'mph' : 'km/h'}`) : null));
  }
  return wrap;
}

// ------------------------------------------------------------------ view

const RANGES = [['24', '24 hours', 24], ['48', '48 hours', 48], ['168', '7 days', 168]];
let range = '24';

function hourTable(f, start, hours) {
  const h = f.hourly;
  const end = Math.min(h.time.length, start + hours);
  const body = el('tbody');
  let lastDay = null;
  for (let i = start; i < end; i++) {
    const date = wall(h.time[i]);
    const dayKey = h.time[i].slice(0, 10);
    if (dayKey !== lastDay) {
      lastDay = dayKey;
      body.append(el('tr', { class: 'tr-day' }, el('th', { colspan: 9, scope: 'colgroup' }, dayLong(date))));
    }
    const pop = h.precipitation_probability[i] ?? 0;
    body.append(el('tr', {},
      el('td', { class: 'td-time' }, i === start ? 'Now' : hourLabel(date)),
      el('td', { class: 'td-icon', html: wxIcon(h.weather_code[i], h.is_day[i], 22) }),
      el('td', { class: 'td-cond' }, wxText(h.weather_code[i], h.is_day[i])),
      el('td', { class: 'td-num td-temp', style: `--t:${tempColor(h.temperature_2m[i])}` }, temp(h.temperature_2m[i])),
      el('td', { class: 'td-num muted' }, temp(h.apparent_temperature[i])),
      el('td', { class: 'td-num' }, pop ? `${pop}%` : ''),
      el('td', { class: 'td-num' }, precip(h.precipitation[i], true)),
      el('td', { class: 'td-wind' },
        el('span', { class: 'td-arrow', html: arrowIcon(h.wind_direction_10m[i], 14) }),
        `${wind(h.wind_speed_10m[i])}`,
        h.wind_gusts_10m[i] - h.wind_speed_10m[i] > 15 ? el('span', { class: 'muted' }, ` gusts ${Math.round(windVal(h.wind_gusts_10m[i]))}`) : null),
      el('td', { class: 'td-num' }, `${Math.round(h.relative_humidity_2m[i])}%`)));
  }
  return el('div', { class: 'table-wrap' }, el('table', { class: 'hours' },
    el('thead', {}, el('tr', {},
      el('th', { scope: 'col' }, 'Time'), el('th', { scope: 'col' }), el('th', { scope: 'col' }, 'Conditions'),
      el('th', { scope: 'col', class: 'td-num' }, 'Temp'), el('th', { scope: 'col', class: 'td-num' }, 'Feels'),
      el('th', { scope: 'col', class: 'td-num' }, 'Precip'), el('th', { scope: 'col', class: 'td-num' }, 'Amount'),
      el('th', { scope: 'col' }, 'Wind'), el('th', { scope: 'col', class: 'td-num' }, 'Humidity'))),
    body));
}

// Width available for a chart inside a scrolling view, so it can fill the row.
export function contentWidth(root) {
  const cs = getComputedStyle(root);
  const w = root.clientWidth - parseFloat(cs.paddingLeft || 0) - parseFloat(cs.paddingRight || 0);
  return Math.max(280, Math.min(1080, w || 1080));
}

// The Forecast view: the hourly chart, the 10-day list, then the hour-by-hour table.
// The range buttons redraw the chart and table in place so the page doesn't jump.
export function renderForecast(root, startIdx) {
  root.replaceChildren();
  const data = state.data;
  if (!data || !data.forecast) {
    root.append(el('div', { class: 'empty' }, el('h2', {}, state.loc ? 'Loading forecast' : 'Pick a location')));
    return;
  }
  const f = data.forecast;
  const chartBox = el('div');
  const tableBox = el('div');
  const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Range' });
  const draw = () => {
    const hours = RANGES.find((r) => r[0] === range)[2];
    seg.replaceChildren(...RANGES.map(([key, text]) => el('button', {
      'aria-pressed': String(key === range),
      onclick: () => { range = key; draw(); },
    }, text)));
    chartBox.replaceChildren(hourlyChart(f, startIdx, hours, { fit: contentWidth(root) }));
    tableBox.replaceChildren(hourTable(f, startIdx, hours));
  };

  root.append(
    el('div', { class: 'view-head' }, el('h1', {}, 'Forecast')),
    el('section', { class: 'block block-first' },
      el('div', { class: 'block-head' }, el('h2', {}, 'Hourly'), seg),
      chartBox),
    el('section', { class: 'block' },
      el('div', { class: 'block-head' }, el('h2', {}, '10 days')),
      tenDay(f, data.periods, state.spc)),
    el('section', { class: 'block' },
      el('div', { class: 'block-head' }, el('h2', {}, 'Hour by hour')),
      tableBox));
  draw();
}
