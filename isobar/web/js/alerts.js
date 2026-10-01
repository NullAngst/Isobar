// Active alerts for the selected place, and the NWS Area Forecast Discussion.

import { state, api, el, emit, openExternal } from './core.js';
import { uiIcon } from './icons.js';
import { instant, ago, eventColor } from './util.js';

// ------------------------------------------------------------------ alerts

const open = new Set();

function paragraphs(text) {
  return String(text || '')
    .replace(/\r/g, '')
    .split(/\n\s*\n/)
    .map((p) => p.replace(/\n/g, ' ').replace(/\s+/g, ' ').trim())
    .filter(Boolean);
}

function alertCard(a) {
  const isOpen = open.has(a.id);
  const body = el('div', { class: 'alert-body', hidden: !isOpen });
  if (a.headline) body.append(el('p', { class: 'alert-headline' }, a.headline));
  for (const p of paragraphs(a.description)) body.append(el('p', {}, p));
  if (a.instruction) {
    body.append(el('h3', {}, 'What to do'));
    for (const p of paragraphs(a.instruction)) body.append(el('p', {}, p));
  }
  if (a.area) body.append(el('p', { class: 'muted small' }, `Areas: ${a.area}`));
  body.append(el('div', { class: 'row gap' },
    a.geometry
      ? el('button', { class: 'btn', onclick: () => emit('focus', a.geometry) }, 'Show on radar')
      : el('button', { class: 'btn', onclick: () => emit('go', 'radar') }, 'Open radar'),
    el('span', { class: 'muted small' }, a.sender ? `Issued by ${a.sender}` : '')));

  const head = el('button', {
    class: 'alert-head', 'aria-expanded': String(isOpen),
    onclick: () => {
      const now = body.hidden;
      body.hidden = !now;
      head.setAttribute('aria-expanded', String(now));
      if (now) open.add(a.id); else open.delete(a.id);
    },
  },
  el('span', { class: 'alert-event' }, a.event),
  el('span', { class: 'alert-when' }, alertWhen(a)),
  el('span', { class: 'alert-chev', html: uiIcon('chevron', 18) }));

  return el('article', { class: 'alert', style: `--hz:${eventColor(a.event)}` }, head, body);
}

function alertWhen(a) {
  const now = Date.now();
  const onset = a.onset ? new Date(a.onset).getTime() : 0;
  const parts = [];
  if (onset > now + 5 * 60000) parts.push(`From ${instant(a.onset, true)}`);
  if (a.expires) parts.push(`until ${instant(a.expires, true)}`);
  const text = parts.join(' ');
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
}

export function renderAlerts(root) {
  root.replaceChildren(el('div', { class: 'view-head' }, el('h1', {}, 'Alerts')));
  const data = state.data;
  if (!state.loc) {
    root.append(el('div', { class: 'empty' }, el('h2', {}, 'Pick a location')));
    return;
  }
  if (!data) {
    root.append(el('div', { class: 'empty' }, el('p', {}, 'Loading')));
    return;
  }
  if (!data.nws) {
    root.append(el('div', { class: 'empty' },
      el('h2', {}, 'No alert coverage here'),
      el('p', {}, 'Alerts come from the US National Weather Service, so they only cover the United States and its territories.')));
    return;
  }
  const alerts = data.alerts || [];
  if (!alerts.length) {
    root.append(el('div', { class: 'empty' },
      el('h2', {}, 'No active alerts'),
      el('p', {}, `Nothing in effect for ${state.loc.name}.`)));
    return;
  }
  if (alerts.length === 1) open.add(alerts[0].id);
  root.append(el('div', { class: 'alerts' }, ...alerts.map(alertCard)));
}

// ------------------------------------------------------------------ discussion

let afd = { office: null, at: 0, data: null, error: null };

function titleCase(text) {
  // ".SHORT TERM /THROUGH TONIGHT/" reads better as "Short term (through tonight)".
  const lower = text.toLowerCase().trim().replace(/\s*\/([^/]+)\/?\s*$/, ' ($1)').replace(/\b(\d+)z\b/g, '$1Z');
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

function looksTabular(lines) {
  return lines.filter((l) => /\S\s{3,}\S/.test(l.trim())).length >= 2;
}

function afdBlocks(text) {
  // AFDs are 66-column text. Section headers look like ".SHORT TERM...".
  const lines = String(text || '').replace(/\r/g, '').split('\n');
  const sections = [];
  let current = null;
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    const m = line.match(/^\.([A-Z][A-Z0-9 /&()'-]+?)\.\.\.\s*(.*)$/);
    if (m) {
      current = { title: titleCase(m[1]), lines: m[2] ? [m[2]] : [] };
      sections.push(current);
      continue;
    }
    if (line.trim() === '&&' || line.trim() === '$$') {
      current = null;
      continue;
    }
    if (!current) continue;
    current.lines.push(line);
  }
  return sections.map((sec) => {
    const chunks = sec.lines.join('\n').split(/\n\s*\n/).map((c) => c.replace(/^\n+|\n+$/g, '')).filter((c) => c.trim());
    return { title: sec.title, chunks };
  });
}

function chunkNode(chunk) {
  const lines = chunk.split('\n');
  if (looksTabular(lines)) return el('pre', { class: 'afd-pre' }, chunk);
  if (lines.every((l) => /^\s*([-*]|\d+\.)\s/.test(l) || /^\s{2,}\S/.test(l))) {
    // Bullet list: join wrapped continuation lines onto their bullet.
    const items = [];
    for (const l of lines) {
      if (/^\s*([-*]|\d+\.)\s/.test(l)) items.push(l.replace(/^\s*([-*]|\d+\.)\s+/, ''));
      else if (items.length) items[items.length - 1] += ` ${l.trim()}`;
    }
    return el('ul', {}, ...items.map((t) => el('li', {}, t)));
  }
  return el('p', {}, lines.map((l) => l.trim()).join(' '));
}

export async function renderDiscussion(root) {
  root.replaceChildren(el('div', { class: 'view-head' }, el('h1', {}, 'Forecast discussion')));
  const data = state.data;
  if (!state.loc) {
    root.append(el('div', { class: 'empty' }, el('h2', {}, 'Pick a location')));
    return;
  }
  if (!data) {
    root.append(el('div', { class: 'empty' }, el('p', {}, 'Loading')));
    return;
  }
  if (!data.nws || !data.nws.office) {
    root.append(el('div', { class: 'empty' },
      el('h2', {}, 'No discussion here'),
      el('p', {}, 'Forecast discussions are written by US National Weather Service offices.')));
    return;
  }
  const office = data.nws.office;
  if (afd.office !== office || Date.now() - afd.at > 10 * 60 * 1000) {
    root.append(el('div', { class: 'empty' }, el('p', {}, 'Loading')));
    try {
      const res = await api('/api/discussion', { office });
      afd = { office, at: Date.now(), data: res.discussion, error: null };
    } catch (err) {
      afd = { office, at: 0, data: null, error: err.message };
    }
    if (state.view !== 'discussion') return;
    root.replaceChildren(el('div', { class: 'view-head' }, el('h1', {}, 'Forecast discussion')));
  }
  if (!afd.data) {
    root.append(el('div', { class: 'empty' }, el('h2', {}, 'Discussion unavailable'), el('p', {}, afd.error || 'The NWS did not return one.')));
    return;
  }

  const head = root.querySelector('.view-head');
  head.append(el('button', { class: 'link', onclick: () => openExternal(afd.data.link) },
    'Open on weather.gov ', el('span', { html: uiIcon('external', 14) })));

  root.append(el('p', { class: 'muted' }, `NWS ${office}, issued ${instant(afd.data.issued, true)} (${ago(afd.data.issued)})`));
  const blocks = afdBlocks(afd.data.text);
  const article = el('article', { class: 'afd' });
  if (!blocks.length) {
    article.append(el('pre', { class: 'afd-pre' }, afd.data.text || ''));
  }
  for (const b of blocks) {
    if (!b.chunks.length) continue;
    article.append(el('h2', {}, b.title), ...b.chunks.map(chunkNode));
  }
  root.append(article);
}
