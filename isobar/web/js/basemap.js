// Base maps shared by the radar and outlook maps. Labels sit in their own pane
// above the weather so city names stay readable through heavy echoes.

import { state, IEM } from './core.js';

const CARTO = 'https://{s}.basemaps.cartocdn.com';
const CARTO_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services';

export const BASEMAPS = {
  dark: { name: 'Dark', base: `${CARTO}/dark_nolabels/{z}/{x}/{y}{r}.png`, labels: `${CARTO}/dark_only_labels/{z}/{x}/{y}{r}.png`, attr: CARTO_ATTR },
  light: { name: 'Light', base: `${CARTO}/light_nolabels/{z}/{x}/{y}{r}.png`, labels: `${CARTO}/light_only_labels/{z}/{x}/{y}{r}.png`, attr: CARTO_ATTR },
  streets: { name: 'Streets', base: `${CARTO}/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png`, labels: `${CARTO}/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png`, attr: CARTO_ATTR },
  satellite: {
    name: 'Satellite',
    base: `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`,
    labels: `${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`,
    attr: 'Imagery &copy; Esri, Maxar, Earthstar Geographics',
  },
};

export function resolveBasemap() {
  const pick = state.settings.basemap;
  if (pick && pick !== 'auto' && BASEMAPS[pick]) return pick;
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

export function createMap(container, options = {}) {
  const map = L.map(container, {
    zoomControl: false,
    attributionControl: true,
    worldCopyJump: true,
    minZoom: 3,
    maxZoom: 12,
    zoomSnap: 0.5,
    preferCanvas: false,
    ...options,
  });
  map.attributionControl.setPrefix('');
  L.control.zoom({ position: 'bottomright' }).addTo(map);

  map.createPane('outlook').style.zIndex = 320;
  map.createPane('radar').style.zIndex = 350;
  map.createPane('field').style.zIndex = 360;
  map.createPane('borders').style.zIndex = 380;
  const labels = map.createPane('labels');
  labels.style.zIndex = 450;
  labels.style.pointerEvents = 'none';
  map.createPane('top').style.zIndex = 620;

  const holder = { base: null, labels: null, borders: null };
  map._isobarBase = holder;
  applyBasemap(map);
  return map;
}

export function applyBasemap(map) {
  const holder = map._isobarBase;
  const def = BASEMAPS[resolveBasemap()];
  if (holder.base) map.removeLayer(holder.base);
  if (holder.labels) map.removeLayer(holder.labels);
  holder.base = L.tileLayer(def.base, { attribution: def.attr, subdomains: 'abcd', maxZoom: 19, detectRetina: false }).addTo(map);
  holder.labels = L.tileLayer(def.labels, { pane: 'labels', subdomains: 'abcd', maxZoom: 19 }).addTo(map);
  map.getContainer().dataset.basemap = resolveBasemap();
}

export function setBorders(map, on) {
  const holder = map._isobarBase;
  if (on && !holder.borders) {
    holder.borders = L.tileLayer(`${IEM}/c/tile.py/1.0.0/uscounties/{z}/{x}/{y}.png`, {
      pane: 'borders', opacity: 0.55, maxZoom: 19, className: 'county-tiles',
    }).addTo(map);
  } else if (!on && holder.borders) {
    map.removeLayer(holder.borders);
    holder.borders = null;
  }
}

export function locationMarker(latlng) {
  return L.marker(latlng, {
    pane: 'top',
    interactive: false,
    keyboard: false,
    icon: L.divIcon({ className: 'loc-marker', html: '<span></span>', iconSize: [18, 18], iconAnchor: [9, 9] }),
  });
}
