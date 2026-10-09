import { state, el, emit } from './core.js';
import { wxIcon, arrowIcon, uiIcon } from './icons.js';
import {
  temp, wind, precip, pressure, distance, compass, wxText,
  wall, wallNow, timeLabel, instant, ago, eventColor, SPC_COLORS, MCD_COLOR,
} from './util.js';
import { hourlyChart, contentWidth } from './hourly.js';
import { stat, uvText } from './days.js';

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

function alertBanner(alerts, mcds) {
  alerts = alerts || [];
  mcds = mcds || [];
  if (!alerts.length && !mcds.length) return null;
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
  // An SPC mesoscale discussion over the place often comes an hour or so before a watch.
  for (const m of mcds.slice(0, 2)) {
    items.push(el('button', { class: 'banner', style: `--hz:${MCD_COLOR}`, onclick: () => emit('go', 'alerts') },
      el('span', { class: 'banner-bar' }),
      el('span', { class: 'banner-event' }, m.name),
      el('span', { class: 'banner-until' }, 'from the Storm Prediction Center'),
      el('span', { class: 'banner-go', html: uiIcon('next', 16) })));
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

  const banner = alertBanner(data.alerts, data.mcds);

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
    stat('Humidity', pct(c.relative_humidity_2m), `Dew point ${temp(h.dew_point_2m[k])}`),
    stat('Pressure', pressure(c.pressure_msl), pressureTrend(h, k)),
    stat('Visibility', distance(h.visibility[k])),
    stat('UV index', h.uv_index[k] === null ? '--' : String(Math.round(h.uv_index[k])), uvText(h.uv_index[k])),
    stat('Cloud cover', pct(c.cloud_cover)),
    stat('Air quality', String(aqi), aqiLabel),
    stat('Rain today', precip(precipToday), `${f.daily.precipitation_probability_max[todayIdx] ?? 0}% chance`),
    stat('Sunrise', timeLabel(wall(f.daily.sunrise[todayIdx]))),
    stat('Sunset', timeLabel(wall(f.daily.sunset[todayIdx]))));

  // Now covers right now and the next 12 hours. The 10-day and longer hourly
  // outlook live on the Forecast page.
  const next = el('section', { class: 'block block-next' },
    el('div', { class: 'block-head' },
      el('h2', {}, 'Next 12 hours'),
      el('button', { class: 'link', onclick: () => emit('go', 'forecast') }, 'Full forecast')),
    hourlyChart(f, k, 12, { compact: true, fit: contentWidth(root) }));

  root.append(...[banner, hero, next, stats].filter(Boolean));
}

function pct(v) {
  return v === null || v === undefined ? '--' : `${Math.round(v)}%`;
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
