// Active alerts for the selected place, SPC mesoscale discussions over it,
// and the office's Area Forecast Discussion or Hazardous Weather Outlook.

import { state, api, el, emit, openExternal, saveSettings } from './core.js';
import { uiIcon } from './icons.js';
import { instant, ago, eventColor, MCD_COLOR } from './util.js';

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

function mcdCard(m) {
  return el('article', { class: 'alert', style: `--hz:${MCD_COLOR}` },
    el('div', { class: 'alert-head alert-head-static' },
      el('span', { class: 'alert-event' }, m.name),
      el('span', { class: 'alert-when' }, m.expires ? `Until ${instant(m.expires, true)}` : '')),
    el('div', { class: 'alert-body' },
      el('p', {}, 'The Storm Prediction Center is watching this area closely. '
        + 'These discussions often come before a watch, or explain why one is not needed.'),
      m.issued ? el('p', { class: 'muted small' }, `Issued ${instant(m.issued, true)} (${ago(m.issued)})`) : null,
      el('div', { class: 'row gap' },
        el('button', { class: 'btn', onclick: () => openExternal(m.link) }, 'Read it on spc.noaa.gov ', el('span', { html: uiIcon('external', 14) })),
        m.geometry ? el('button', { class: 'btn', onclick: () => emit('focus', m.geometry) }, 'Show on radar') : null)));
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
  const mcds = data.mcds || [];
  if (!alerts.length && !mcds.length) {
    root.append(el('div', { class: 'empty' },
      el('h2', {}, 'No active alerts'),
      el('p', {}, `Nothing in effect for ${state.loc.name}.`)));
    return;
  }
  if (alerts.length === 1) open.add(alerts[0].id);
  if (alerts.length) root.append(el('div', { class: 'alerts' }, ...alerts.map(alertCard)));
  if (mcds.length) {
    root.append(el('h2', { class: 'section-head' }, 'From the Storm Prediction Center'),
      el('div', { class: 'alerts' }, ...mcds.map(mcdCard)));
  }
}

// ------------------------------------------------------------------ discussion

// One entry per office and product, so flipping between the two tabs doesn't refetch.
const texts = new Map();

const PRODUCTS = {
  afd: { code: 'AFD', title: 'Forecast discussion', none: 'The NWS did not return one.' },
  hwo: {
    code: 'HWO', title: 'Hazardous weather outlook',
    none: 'This office has no current Hazardous Weather Outlook. Some only issue one when there is something to say.',
  },
};

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

function discussionHead(root, which) {
  const tabs = el('div', { class: 'seg', role: 'group', 'aria-label': 'Product' },
    ...Object.entries(PRODUCTS).map(([key, p]) => el('button', {
      'aria-pressed': String(key === which),
      onclick: () => { if (key !== which) { saveSettings({ discussion_product: key }); renderDiscussion(root); } },
    }, key === 'afd' ? 'Discussion' : 'Hazards')));
  const head = el('div', { class: 'view-head' }, el('div', { class: 'row', style: 'gap:16px;flex-wrap:wrap' }, el('h1', {}, PRODUCTS[which].title), tabs));
  root.replaceChildren(head);
  return head;
}

export async function renderDiscussion(root) {
  const which = PRODUCTS[state.settings.discussion_product] ? state.settings.discussion_product : 'afd';
  const product = PRODUCTS[which];
  discussionHead(root, which);
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
  const key = `${office}:${product.code}`;
  let entry = texts.get(key);
  if (!entry || Date.now() - entry.at > 10 * 60 * 1000) {
    root.append(el('div', { class: 'empty' }, el('p', {}, 'Loading')));
    try {
      const res = await api('/api/discussion', { office, product: product.code });
      entry = { at: Date.now(), data: res.discussion, error: null };
    } catch (err) {
      entry = { at: 0, data: null, error: err.message };
    }
    texts.set(key, entry);
    // The view, the place or the tab may have changed while this loaded.
    const nowWhich = state.settings.discussion_product;
    if (state.view !== 'discussion' || !state.data || !state.data.nws || state.data.nws.office !== office || (nowWhich || 'afd') !== which) return;
    discussionHead(root, which);
  }
  if (!entry.data) {
    root.append(el('div', { class: 'empty' }, el('h2', {}, `${product.title} unavailable`), el('p', {}, entry.error || product.none)));
    return;
  }
  const doc = entry.data;

  root.querySelector('.view-head').append(el('button', { class: 'link', onclick: () => openExternal(doc.link) },
    'Open on weather.gov ', el('span', { html: uiIcon('external', 14) })));

  root.append(el('p', { class: 'muted' }, `NWS ${office}, issued ${instant(doc.issued, true)} (${ago(doc.issued)})`));
  const blocks = afdBlocks(doc.text);
  const article = el('article', { class: 'afd' });
  if (!blocks.length) {
    article.append(el('pre', { class: 'afd-pre' }, doc.text || ''));
  }
  for (const b of blocks) {
    if (!b.chunks.length) continue;
    article.append(el('h2', {}, b.title), ...b.chunks.map(chunkNode));
  }
  root.append(article);
}
