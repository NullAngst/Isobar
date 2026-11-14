// Radar color tables.
//
// IEM_N0Q is the color table IEM uses for its N0Q/N0B reflectivity imagery:
// entry i (0-based) is {-32 + 0.5 * i} dBZ. Source: pyIEM
// (src/pyiem/data/ramps/composite_n0q.txt, MIT license, Iowa State University).
// Tiles are decoded back to dBZ by matching pixel colors against this table,
// then painted with whichever palette is selected.

export const IEM_N0Q = new Uint8Array([133,113,143,133,114,143,134,115,141,135,117,139,135,118,139,136,119,137,137,121,135,137,122,135,138,123,133,139,125,132,139,126,132,140,127,130,141,129,128,141,130,128,142,131,126,143,132,124,143,133,124,144,135,123,145,136,121,145,137,121,146,139,119,147,141,117,150,145,83,152,148,87,155,151,91,157,154,96,160,157,100,163,160,104,165,163,109,168,166,113,170,169,118,173,172,122,176,175,126,178,178,131,183,184,140,186,187,144,189,190,148,191,193,153,194,196,157,196,199,162,199,202,166,202,205,170,204,208,175,210,212,180,207,210,180,201,204,180,198,201,180,195,199,180,192,196,180,189,193,180,185,190,180,182,187,180,179,185,180,176,182,180,173,179,180,170,176,180,164,171,180,160,168,180,157,165,180,154,162,180,151,160,180,148,157,180,145,154,180,148,155,181,144,152,180,140,149,179,136,146,178,128,140,176,124,137,175,120,134,174,116,131,172,112,128,171,108,125,170,103,121,169,99,118,168,95,115,167,91,112,166,87,109,164,79,103,162,75,100,161,71,97,160,67,94,159,65,91,158,67,97,162,69,104,166,72,111,170,74,118,174,77,125,178,79,132,182,81,139,187,86,153,195,89,159,199,91,166,203,94,173,207,96,180,212,98,187,216,101,194,220,103,201,224,106,208,228,111,214,232,104,214,215,89,214,179,82,214,162,75,214,144,67,214,126,60,214,109,53,214,91,17,213,24,17,209,23,16,205,23,16,200,22,16,196,22,15,188,21,15,183,20,14,179,20,14,175,19,14,171,19,13,166,18,13,162,18,13,158,17,12,153,17,12,149,16,12,145,16,11,136,15,11,132,14,10,128,14,10,124,13,10,119,13,9,115,12,9,111,12,9,107,11,8,102,11,8,98,10,9,94,9,50,115,8,70,125,8,91,136,7,111,146,7,132,157,6,152,168,6,173,178,5,193,189,5,214,199,4,234,210,4,255,226,0,255,216,0,255,211,0,255,206,0,255,201,0,255,196,0,255,192,0,255,187,0,255,182,0,255,177,0,255,172,0,255,167,0,255,162,0,255,153,0,255,148,0,255,143,0,255,138,0,255,133,0,255,128,0,255,0,0,248,0,0,241,0,0,234,0,0,227,0,0,213,0,0,205,0,0,198,0,0,191,0,0,184,0,0,177,0,0,170,0,0,163,0,0,155,0,0,148,0,0,141,0,0,127,0,0,120,0,0,113,0,0,255,255,255,255,245,255,255,234,255,255,223,255,255,212,255,255,201,255,255,190,255,255,179,255,255,157,255,255,146,255,255,117,255,252,107,253,249,96,250,246,86,247,243,75,244,240,64,241,237,54,239,234,43,236,231,32,233,225,11,227,178,0,255,172,0,252,164,0,247,155,0,244,147,0,239,136,0,234,131,0,232,121,0,226,114,0,221,105,0,219,5,236,240,5,235,240,5,234,240,5,221,224,5,220,224,5,219,224,5,205,208,5,204,208,4,189,192,4,188,192,4,187,192,4,174,176,4,173,176,4,158,160,4,157,160,4,156,160,3,142,144,3,141,144,3,140,144,3,126,128,3,125,128,3,111,112,3,110,112,3,109,112,2,95,96,2,94,96,2,79,80,2,78,80,2,77,80,2,63,64,2,62,64,2,61,64,1,48,48,1,47,48,1,32,32,1,31,32,1,30,32,58,103,181,58,102,181,58,101,181,58,100,181,58,99,181,58,98,181]);
export const DBZ_MIN = -32;
export const DBZ_STEP = 0.5;
export const N0Q_COUNT = IEM_N0Q.length / 3;

// Palettes are stops of [dBZ, '#rrggbb', alpha]. stepped: true holds each
// color until the next stop instead of blending.
export const PALETTES = {
  default: { name: 'IEM default', source: true },
  classic: {
    name: 'NWS classic', stepped: true,
    stops: [
      [5, '#04e9e7', 1], [10, '#019ff4', 1], [15, '#0300f4', 1], [20, '#02fd02', 1],
      [25, '#01c501', 1], [30, '#008e00', 1], [35, '#fdf802', 1], [40, '#e5bc00', 1],
      [45, '#fd9500', 1], [50, '#fd0000', 1], [55, '#d40000', 1], [60, '#bc0000', 1],
      [65, '#f800fd', 1], [70, '#9854c6', 1], [75, '#fdfdfd', 1],
    ],
  },
  smooth: {
    name: 'Smooth',
    stops: [
      [0, '#7fb4d6', 0.25], [10, '#5aa0e0', 0.65], [18, '#3fbf6a', 0.9], [28, '#13852f', 1],
      [35, '#efe03c', 1], [43, '#f39a22', 1], [50, '#e2342b', 1], [58, '#99131d', 1],
      [64, '#e04fd0', 1], [70, '#8a3fd1', 1], [76, '#ffffff', 1],
    ],
  },
  soft: {
    name: 'Soft',
    stops: [
      [0, '#6c8fc7', 0.2], [12, '#5a7fd6', 0.6], [22, '#4f63d8', 0.85], [32, '#7a52cc', 1],
      [40, '#c4458a', 1], [48, '#ee7a3c', 1], [56, '#f6cf45', 1], [66, '#fff6c8', 1], [74, '#ffffff', 1],
    ],
  },
  contrast: {
    name: 'Color-blind safe',
    stops: [
      [0, '#3b2a6b', 0.3], [10, '#3d4d9a', 0.75], [20, '#2f7ea8', 1], [30, '#29a98b', 1],
      [40, '#83cc4f', 1], [48, '#f2e43a', 1], [55, '#ff9a1f', 1], [62, '#ff3d5c', 1], [70, '#ffffff', 1],
    ],
  },
  mono: {
    name: 'Grayscale',
    stops: [[0, '#5a6470', 0.3], [20, '#9aa3ad', 0.8], [40, '#d9dde2', 1], [55, '#ffffff', 1], [60, '#ff4a4a', 1], [70, '#ff9cf0', 1]],
  },
};

const hexRGB = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

// Lookup table: N0Q index (0..254) -> RGBA for a palette. Index -1 handled by caller.
const lutCache = new Map();
export function paletteLUT(key) {
  if (lutCache.has(key)) return lutCache.get(key);
  const pal = PALETTES[key] || PALETTES.default;
  const lut = new Uint8ClampedArray(N0Q_COUNT * 4);
  for (let i = 0; i < N0Q_COUNT; i++) {
    const dbz = DBZ_MIN + DBZ_STEP * i;
    let rgba;
    if (pal.source) {
      rgba = [IEM_N0Q[i * 3], IEM_N0Q[i * 3 + 1], IEM_N0Q[i * 3 + 2], 255];
    } else {
      rgba = sample(pal, dbz);
    }
    lut.set(rgba, i * 4);
  }
  lutCache.set(key, lut);
  return lut;
}

function sample(pal, dbz) {
  const stops = pal.stops;
  if (dbz < stops[0][0]) return [0, 0, 0, 0];
  for (let i = stops.length - 1; i >= 0; i--) {
    if (dbz >= stops[i][0]) {
      const [v0, c0, a0] = stops[i];
      const c = hexRGB(c0);
      if (pal.stepped || i === stops.length - 1) return [...c, Math.round(a0 * 255)];
      const [v1, c1h, a1] = stops[i + 1];
      const c1 = hexRGB(c1h);
      const t = (dbz - v0) / (v1 - v0);
      return [
        Math.round(c[0] + (c1[0] - c[0]) * t),
        Math.round(c[1] + (c1[1] - c[1]) * t),
        Math.round(c[2] + (c1[2] - c[2]) * t),
        Math.round((a0 + (a1 - a0) * t) * 255),
      ];
    }
  }
  return [0, 0, 0, 0];
}

// Exact color -> index map, plus a nearest-color fallback cache for pixels
// that were blended or color-managed on the way in.
const exact = new Map();
for (let i = 0; i < N0Q_COUNT; i++) {
  exact.set((IEM_N0Q[i * 3] << 16) | (IEM_N0Q[i * 3 + 1] << 8) | IEM_N0Q[i * 3 + 2], i);
}
const near = new Map();

// Returns the table index for a pixel color: i for an exact match,
// 1000 + i for a nearest-color match, -1 when the color is not radar data.
export function colorIndex(r, g, b) {
  const key = (r << 16) | (g << 8) | b;
  const hit = exact.get(key);
  if (hit !== undefined) return hit;
  let cached = near.get(key);
  if (cached === undefined) {
    let best = -1;
    let bestD = 24 * 24 * 3;  // anything farther than this is not radar data
    for (let i = 0; i < N0Q_COUNT; i++) {
      const dr = IEM_N0Q[i * 3] - r;
      const dg = IEM_N0Q[i * 3 + 1] - g;
      const db = IEM_N0Q[i * 3 + 2] - b;
      const d = dr * dr + dg * dg + db * db;
      if (d < bestD) { bestD = d; best = i; }
    }
    cached = best < 0 ? -1 : 1000 + best;
    if (near.size > 50000) near.clear();
    near.set(key, cached);
  }
  return cached;
}

export const dbzToIndex = (dbz) => Math.max(0, Math.min(N0Q_COUNT - 1, Math.round((dbz - DBZ_MIN) / DBZ_STEP)));
