import { state, el, esc, emit } from './core.js';
import { wxIcon, arrowIcon, uiIcon } from './icons.js';
import {
  temp, tempVal, tempColor, wind, precip, snow, pressure, distance, compass, wxText,
  wall, wallNow, dayShort, timeLabel, instant, ago, eventColor, SPC_COLORS,
} from './util.js';
import { hourlyChart } from './hourly.js';

function currentHourIndex(f) {
  const now = wallNow();
  const times = f.hourly.time;
  let k = 0;
  for (let i = 0; i < times.length; i++) {
    if (wall(times[i]) <= now) k = i;
    else break;
  }
  return k;
}

function aqiText(aqi) {
  if (aqi === null || aqi === undefined) return ['--', ''];
  const v = Math.round(aqi);
  if (v <= 50) return [v, 'Good'];
  if (v <= 100) return [v, 'Moderate'];
  if (v <= 150) return [v, 'Unhealthy for sensitive groups'];
  if (v <= 200) return [v, 'Unhealthy'];
  if (v <= 300) return [v, 'Very unhealthy'];
  return [v, 'Hazardous'];
}

function uvText(uv) {
  if (uv === null || uv === undefined) return '';
  if (uv < 3) return 'Low';
  if (uv < 6) return 'Moderate';
  if (uv < 8) return 'High';
  if (uv < 11) return 'Very high';
  return 'Extreme';
}

function stat(label, value, sub = '', extra = '') {
  return el('div', { class: 'stat' },
    el('div', { class: 'stat-label' }, label),
    el('div', { class: 'stat-value', html: `${esc(value)}${extra}` }),
    sub ? el('div', { class: 'stat-sub' }, sub) : null);
}

function alertBanner(alerts) {
  if (!alerts || !alerts.length) return null;
  const items = alerts.slice(0, 3).map((a) => el('button', {
    class: 'banner', style: `--hz:${eventColor(a.event)}`,
    onclick: () => emit('go', 'alerts'),
  },
  el('span', { class: 'banner-bar' }),
  el('span', { class: 'banner-event' }, a.event),
  a.expires ? el('span', { class: 'banner-until' }, `until ${instant(a.expires, true)}`) : null,
  el('span', { class: 'banner-go', html: uiIcon('next', 16) })));
  if (alerts.length > 3) {
    items.push(el('button', { class: 'banner banner-more', onclick: () => emit('go', 'alerts') }, `${alerts.length - 3} more alerts`));
  }
  return el('div', { class: 'banners' }, ...items);
}

function riskChip(days) {
  if (!days || !days.length) return null;
  const d1 = days[0];
  const cat = d1.category;
  const color = cat ? SPC_COLORS[cat.label] : null;
  const text = cat
    ? (cat.level === 0 ? 'General thunderstorms today' : `${cat.name} severe risk today, level ${cat.level} of 5`)
    : 'No severe storms expected today';
  return el('button', {
    class: `risk ${cat ? 'risk-on' : ''}`, style: color ? `--risk:${color}` : '',
    onclick: () => emit('go', 'outlooks'),
  }, el('span', { class: 'risk-dot' }), text);
}

function nextDayRisk(days, dayIndex) {
  const d = days && days[dayIndex];
  if (!d || !d.category || d.category.level < 1) return null;
  return el('span', { class: 'mini-risk', style: `--risk:${SPC_COLORS[d.category.label]}`, title: `SPC: ${d.category.name} risk` }, d.category.name);
}

function periodsForDate(periods, dateStr) {
  return (periods || []).filter((p) => p.start && p.start.slice(0, 10) === dateStr);
}

function tenDay(f, periods, spcDays) {
  const d = f.daily;
  const lows = d.temperature_2m_min.map(tempVal);
  const highs = d.temperature_2m_max.map(tempVal);
  const lo = Math.min(...lows);
  const hi = Math.max(...highs);
  const span = Math.max(1, hi - lo);
  const today = wallNow().toISOString().slice(0, 10);

  const rows = d.time.map((dateStr, i) => {
    const date = wall(dateStr);
    const name = dateStr === today ? 'Today' : dayShort(date);
    const left = ((lows[i] - lo) / span) * 100;
    const width = Math.max(4, ((highs[i] - lows[i]) / span) * 100);
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

export function renderNow(root) {
  root.replaceChildren();
  const data = state.data;
  if (!state.loc) {
    root.append(el('div', { class: 'empty' },
      el('h2', {}, 'Pick a location'),
      el('p', {}, 'Search for a city, a ZIP code, or coordinates like 34.58, -83.33.'),
      el('button', { class: 'btn', onclick: () => document.getElementById('search').focus() }, 'Search')));
    return;
  }
  if (!data || !data.forecast) {
    root.append(el('div', { class: 'empty' },
      el('h2', {}, data ? 'Forecast unavailable' : 'Loading forecast'),
      el('p', {}, data ? (data.errors || []).join('. ') || 'Open-Meteo did not answer. Try refresh.' : '')));
    return;
  }

  const f = data.forecast;
  const c = f.current;
  const h = f.hourly;
  const k = currentHourIndex(f);
  const todayIdx = 0;

  const banner = alertBanner(data.alerts);

  const obs = data.obs && data.obs.time && (Date.now() - new Date(data.obs.time)) < 3 * 3600e3
    ? el('p', { class: 'obs' }, `Observed ${temp(data.obs.temp_c)} at ${data.obs.station}, ${ago(data.obs.time)}`)
    : null;

  const nwsLine = data.periods && data.periods.length
    ? el('p', { class: 'nws-line' }, el('strong', {}, `${data.periods[0].name}: `), data.periods[0].detail)
    : null;

  const hero = el('section', { class: 'hero' },
    el('div', { class: 'hero-main' },
      el('div', { class: 'hero-icon', html: wxIcon(c.weather_code, c.is_day, 64) }),
      el('div', { class: 'hero-temp' }, temp(c.temperature_2m)),
      el('div', { class: 'hero-side' },
        el('div', { class: 'hero-cond' }, wxText(c.weather_code, c.is_day)),
        el('div', { class: 'hero-feels' }, `Feels like ${temp(c.apparent_temperature)}`),
        el('div', { class: 'hero-hilo' }, `High ${temp(f.daily.temperature_2m_max[todayIdx])}, low ${temp(f.daily.temperature_2m_min[todayIdx])}`))),
    el('div', { class: 'hero-aside' }, riskChip(state.spc), obs),
    nwsLine);

  const [aqi, aqiLabel] = aqiText(data.air && data.air.us_aqi);
  const precipToday = f.daily.precipitation_sum[todayIdx];
  const stats = el('section', { class: 'stats' },
    stat('Wind', wind(c.wind_speed_10m), `Gusts ${wind(c.wind_gusts_10m)}, from ${compass(c.wind_direction_10m)}`, ` <span class="stat-arrow">${arrowIcon(c.wind_direction_10m, 18)}</span>`),
    stat('Humidity', `${Math.round(c.relative_humidity_2m)}%`, `Dew point ${temp(h.dew_point_2m[k])}`),
    stat('Pressure', pressure(c.pressure_msl), pressureTrend(h, k)),
    stat('Visibility', distance(h.visibility[k])),
    stat('UV index', h.uv_index[k] === null ? '--' : String(Math.round(h.uv_index[k])), uvText(h.uv_index[k])),
    stat('Cloud cover', `${Math.round(c.cloud_cover)}%`),
    stat('Air quality', String(aqi), aqiLabel),
    stat('Rain today', precip(precipToday), `${f.daily.precipitation_probability_max[todayIdx] ?? 0}% chance`),
    stat('Sunrise', timeLabel(wall(f.daily.sunrise[todayIdx]))),
    stat('Sunset', timeLabel(wall(f.daily.sunset[todayIdx]))));

  const next = el('section', { class: 'block' },
    el('div', { class: 'block-head' },
      el('h2', {}, 'Next 24 hours'),
      el('button', { class: 'link', onclick: () => emit('go', 'hourly') }, 'Hourly detail')),
    hourlyChart(f, k, 24, { compact: true }));

  const days = el('section', { class: 'block' },
    el('div', { class: 'block-head' }, el('h2', {}, '10 days')),
    tenDay(f, data.periods, state.spc));

  root.append(...[banner, hero, stats, next, days].filter(Boolean));
}

function pressureTrend(h, k) {
  const p = h.pressure_msl;
  if (!p || k < 3 || p[k] === null || p[k - 3] === null) return '';
  const diff = p[k] - p[k - 3];
  if (diff > 1) return 'Rising';
  if (diff < -1) return 'Falling';
  return 'Steady';
}

export { currentHourIndex };
