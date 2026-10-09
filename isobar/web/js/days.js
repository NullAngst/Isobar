// The 10-day list with expandable details. Used by the Forecast view.

import { el, esc } from './core.js';
import { wxIcon } from './icons.js';
import {
  temp, tempVal, tempColor, wind, precip, snow, compass, wxText,
  wall, wallNow, dayShort, timeLabel, SPC_COLORS,
} from './util.js';

export function uvText(uv) {
  if (uv === null || uv === undefined) return '';
  if (uv < 3) return 'Low';
  if (uv < 6) return 'Moderate';
  if (uv < 8) return 'High';
  if (uv < 11) return 'Very high';
  return 'Extreme';
}

export function stat(label, value, sub = '', extra = '') {
  return el('div', { class: 'stat' },
    el('div', { class: 'stat-label' }, label),
    el('div', { class: 'stat-value', html: `${esc(value)}${extra}` }),
    sub ? el('div', { class: 'stat-sub' }, sub) : null);
}

function nextDayRisk(days, dayIndex) {
  const d = days && days[dayIndex];
  if (!d || !d.category || d.category.level < 1) return null;
  return el('span', { class: 'mini-risk', style: `--risk:${SPC_COLORS[d.category.label]}`, title: `SPC: ${d.category.name} risk` }, d.category.name);
}

function periodsForDate(periods, dateStr) {
  return (periods || []).filter((p) => p.start && p.start.slice(0, 10) === dateStr);
}

export function tenDay(f, periods, spcDays) {
  const d = f.daily;
  const lows = d.temperature_2m_min.map(tempVal);
  const highs = d.temperature_2m_max.map(tempVal);
  // The last day can come back without values; null would count as 0 in Math.min.
  const known = (list) => list.filter((v) => v !== null && Number.isFinite(v));
  const lo = Math.min(...known(lows), ...known(highs));
  const hi = Math.max(...known(highs), ...known(lows));
  const span = Math.max(1, hi - lo);
  const today = wallNow().toISOString().slice(0, 10);

  const rows = d.time.map((dateStr, i) => {
    const date = wall(dateStr);
    const name = dateStr === today ? 'Today' : dayShort(date);
    const hasRange = lows[i] !== null && highs[i] !== null;
    const left = hasRange ? ((lows[i] - lo) / span) * 100 : 0;
    const width = hasRange ? Math.max(4, ((highs[i] - lows[i]) / span) * 100) : 0;
    const grad = `linear-gradient(90deg, ${tempColor(d.temperature_2m_min[i])}, ${tempColor(d.temperature_2m_max[i])})`;
    const pop = d.precipitation_probability_max[i];
    const details = el('div', { class: 'day-detail', hidden: true });
    const row = el('button', {
      class: 'day', 'aria-expanded': 'false',
      onclick: () => {
        const open = details.hidden;
        details.hidden = !open;
        row.setAttribute('aria-expanded', String(open));
        if (open && !details.childElementCount) fillDetails(details, f, i, periodsForDate(periods, dateStr));
      },
    },
    el('span', { class: 'day-name' }, name),
    el('span', { class: 'day-icon', html: wxIcon(d.weather_code[i], 1, 26) }),
    el('span', { class: 'day-pop' }, pop >= 10 ? `${pop}%` : ''),
    el('span', { class: 'day-lo' }, temp(d.temperature_2m_min[i])),
    el('span', { class: 'day-range' }, el('span', { class: 'day-bar', style: `left:${left}%;width:${width}%;background:${grad}` })),
    el('span', { class: 'day-hi' }, temp(d.temperature_2m_max[i])),
    el('span', { class: 'day-extra' }, nextDayRisk(spcDays, i) || wxText(d.weather_code[i])));
    return [row, details];
  });
  return el('div', { class: 'days' }, ...rows.flat());
}

function fillDetails(box, f, i, periods) {
  const d = f.daily;
  const grid = el('div', { class: 'detail-grid' },
    stat('Feels like', `${temp(d.apparent_temperature_max[i])} / ${temp(d.apparent_temperature_min[i])}`),
    stat('Precipitation', precip(d.precipitation_sum[i]), snow(d.snowfall_sum[i])),
    stat('Wind', wind(d.wind_speed_10m_max[i]), `Gusts ${wind(d.wind_gusts_10m_max[i])}, from ${compass(d.wind_direction_10m_dominant[i])}`),
    stat('UV index', d.uv_index_max[i] === null ? '--' : String(Math.round(d.uv_index_max[i])), uvText(d.uv_index_max[i])),
    stat('Sunrise', timeLabel(wall(d.sunrise[i]))),
    stat('Sunset', timeLabel(wall(d.sunset[i]))));
  box.append(grid);
  for (const p of periods) {
    box.append(el('p', { class: 'nws-text' }, el('strong', {}, `${p.name}. `), p.detail));
  }
}
