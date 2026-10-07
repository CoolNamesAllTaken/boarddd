// boarddd/copper: Gerber X2 copper layers -> boarddd/copper@1 in the browser (Gerber-only uploads), the twin of
// python/src/boarddd/io/gerber_copper.py (same rules, same output; python/tests/io/test_gerber_copper.py checks
// the two against each other). See that module and docs/copper.md for what becomes what:
//   Conductor (and EtchedComponent) draws with a circular aperture -> tracks (G02/G03 arcs keep their mid point;
//   a full circle is two half arcs),
//   Conductor regions -> zones of kind 'region' (cut-ins removed), EtchedComponent regions -> kind 'shape',
//   ViaPad flashes -> vias (joined across layers; drills from Excellon files when given),
//   pad flashes and pad regions -> pads (one per layer), nets from %TO.N, ref/pin from %TO.P.
// A small tokenizer: the wasm renderer ignores %TO attributes.

import { layerRole } from '../gerber/layers.js';
import { parseExcellon } from '../gerber/drills.js';
import { boardOutline } from '../gerber/outline.js';
import { checkFills, circleHalves, fillArea, planes, signedArea, unfracture } from './geometry.js';

/** Aperture functions of pads (X2 spec 5.6.10), lower case. */
export const PAD_FUNCTIONS = new Set([
  'smdpad', 'componentpad', 'heatsinkpad', 'connectorpad', 'castellatedpad', 'testpad', 'fiducialpad', 'washerpad',
  'otherpad', 'bgapad', 'pressfitpad',
]);
/** Points per quarter turn when circles, rounded corners and region arcs are flattened. */
const SEGMENTS = 8;

const r = (v) => {
  const x = Math.round(v * 1e6) / 1e6;
  return x === 0 ? 0 : x;
};
const rp = (p) => [r(p[0]), r(p[1])];
const ccw = (ring) => (signedArea(ring) > 0 ? ring : [...ring].reverse());

function unescape(s) {
  return s.replace(/\\u([0-9A-Fa-f]{4})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));
}

function splitFields(s) {
  const out = [];
  let cur = '';
  for (let i = 0; i < s.length; i++) {
    if (s[i] === '\\' && s[i + 1] === ',') { cur += ','; i++; continue; }
    if (s[i] === ',') { out.push(cur); cur = ''; } else cur += s[i];
  }
  out.push(cur);
  return out.map(unescape);
}

function* tokens(text) {
  let i = 0;
  const n = text.length;
  while (i < n) {
    const c = text[i];
    if (c === ' ' || c === '\r' || c === '\n' || c === '\t') { i++; continue; }
    if (c === '%') {
      let j = text.indexOf('%', i + 1);
      if (j < 0) j = n;
      const block = text.slice(i + 1, j).replace(/[\r\n]/g, '');
      if (block.startsWith('AM')) yield ['ext', block];
      else for (const cmd of block.split('*')) if (cmd) yield ['ext', cmd];
      i = j + 1;
      continue;
    }
    let j = text.indexOf('*', i);
    if (j < 0) j = n;
    const word = text.slice(i, j).replace(/[\r\n]/g, '').trim();
    if (word) yield ['word', word];
    i = j + 1;
  }
}

// ---------------------------------------------------------------------------------------------------------------
// aperture shapes

function circle(d, cx = 0, cy = 0, segments = SEGMENTS) {
  const n = 4 * segments;
  return Array.from({ length: n }, (_, i) => [cx + (d / 2) * Math.cos((2 * Math.PI * i) / n), cy + (d / 2) * Math.sin((2 * Math.PI * i) / n)]);
}
const rect = (w, h, cx = 0, cy = 0) => [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]];

function obround(w, h, segments = SEGMENTS) {
  const rad = Math.min(w, h) / 2;
  const hx = w / 2 - rad, hy = h / 2 - rad;
  const pts = [];
  [[hx, -hy], [hx, hy], [-hx, hy], [-hx, -hy]].forEach(([cx, cy], k) => {
    const a0 = -Math.PI / 2 + (k * Math.PI) / 2;
    for (let i = 0; i <= segments; i++) {
      const a = a0 + ((Math.PI / 2) * i) / segments;
      pts.push([cx + rad * Math.cos(a), cy + rad * Math.sin(a)]);
    }
  });
  return pts;
}

function rot(p, deg) {
  if (!deg) return [p[0], p[1]];
  const a = (deg * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
  return [p[0] * c - p[1] * s, p[0] * s + p[1] * c];
}

function hull(points) {
  const seen = new Set();
  const pts = [];
  for (const p of points) {
    const k = `${p[0]},${p[1]}`;
    if (!seen.has(k)) { seen.add(k); pts.push(p); }
  }
  pts.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [], upper = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  for (const p of [...pts].reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}

/** A macro expression (`$1+$1`, `0.5`, `$2x2`, parentheses): four operators over numbers and `$n`. */
export function evaluate(expr, args) {
  const s = expr.replace(/\s+/g, '').replace(/[xX]/g, '*');
  let i = 0;
  const num = () => {
    if (s[i] === '(') { i++; const v = sum(); i++; return v; }
    if (s[i] === '-') { i++; return -num(); }
    if (s[i] === '+') { i++; return num(); }
    if (s[i] === '$') {
      const m = /^\$(\d+)/.exec(s.slice(i));
      if (!m) return 0;
      i += m[0].length;
      const k = Number(m[1]) - 1;
      return k >= 0 && k < args.length ? args[k] : 0;
    }
    const m = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(s.slice(i));
    if (!m) { i = s.length; return 0; }
    i += m[0].length;
    return Number(m[0]);
  };
  const product = () => {
    let v = num();
    while (s[i] === '*' || s[i] === '/') {
      const op = s[i++];
      const w = num();
      v = op === '*' ? v * w : w ? v / w : 0;
    }
    return v;
  };
  const sum = () => {
    let v = product();
    while (s[i] === '+' || s[i] === '-') {
      const op = s[i++];
      const w = product();
      v = op === '+' ? v + w : v - w;
    }
    return v;
  };
  return s ? sum() : 0;
}

function macroLoops(body, args0, segments) {
  const args = [...args0];
  const loops = [];
  for (let stmt of body) {
    stmt = stmt.trim();
    if (!stmt || stmt.startsWith('0')) continue;
    if (stmt.startsWith('$') && stmt.includes('=')) {
      const [name, expr] = [stmt.slice(0, stmt.indexOf('=')), stmt.slice(stmt.indexOf('=') + 1)];
      const k = Number(name.slice(1));
      while (args.length < k) args.push(0);
      args[k - 1] = evaluate(expr, args);
      continue;
    }
    const v = stmt.split(',').map((x) => evaluate(x, args));
    const code = Math.trunc(v[0]);
    const p = v.slice(1);
    let loop, angle;
    if (code === 1 && p.length >= 4) {
      loop = circle(p[1], p[2], p[3], segments);
      angle = p.length > 4 ? p[4] : 0;
    } else if ((code === 20 || code === 2) && p.length >= 7) {
      const [, w, sx, sy, ex, ey] = p;
      const dx = ex - sx, dy = ey - sy;
      const ln = Math.hypot(dx, dy) || 1;
      const nx = (-dy / ln) * (w / 2), ny = (dx / ln) * (w / 2);
      loop = [[sx + nx, sy + ny], [sx - nx, sy - ny], [ex - nx, ey - ny], [ex + nx, ey + ny]];
      angle = p[6];
    } else if (code === 21 && p.length >= 6) {
      loop = rect(p[1], p[2], p[3], p[4]);
      angle = p[5];
    } else if (code === 22 && p.length >= 6) {
      loop = rect(p[1], p[2], p[3] + p[1] / 2, p[4] + p[2] / 2);
      angle = p[5];
    } else if (code === 4 && p.length >= 2) {
      const n = Math.trunc(p[1]);
      const coords = p.slice(2, 2 + 2 * (n + 1));
      loop = Array.from({ length: n }, (_, k) => [coords[2 * k], coords[2 * k + 1]]);
      angle = p.length > 2 + 2 * (n + 1) ? p[2 + 2 * (n + 1)] : 0;
    } else if (code === 5 && p.length >= 5) {
      const n = Math.trunc(p[1]), cx = p[2], cy = p[3], d = p[4];
      loop = Array.from({ length: n }, (_, k) => [cx + (d / 2) * Math.cos((2 * Math.PI * k) / n), cy + (d / 2) * Math.sin((2 * Math.PI * k) / n)]);
      angle = p.length > 5 ? p[5] : 0;
    } else if (code === 7 && p.length >= 6) {
      loops.push(circle(p[2], p[0], p[1], segments).map((q) => rot(q, p[5])));
      continue;
    } else continue;
    if (p[0] === 0) continue;
    loops.push(loop.map((q) => rot(q, angle)));
  }
  return loops;
}

function aperture(template, params, macros, scale, fn, segments) {
  let loops = [];
  let diameter = null;
  if (template === 'C') {
    const d = params.length ? params[0] : 0;
    loops = [circle(d, 0, 0, segments)];
    diameter = d * scale;
  } else if (template === 'R') loops = [rect(params[0], params.length > 1 ? params[1] : params[0])];
  else if (template === 'O') loops = [obround(params[0], params.length > 1 ? params[1] : params[0], segments)];
  else if (template === 'P') {
    const d = params[0], n = params.length > 1 ? Math.trunc(params[1]) : 3, a = params.length > 2 ? params[2] : 0;
    loops = [Array.from({ length: n }, (_, k) => rot([(d / 2) * Math.cos((2 * Math.PI * k) / n), (d / 2) * Math.sin((2 * Math.PI * k) / n)], a))];
  } else if (macros.has(template)) {
    loops = macroLoops(macros.get(template), params, segments);
    if (template === 'RoundRect' && loops.length > 1) loops = [hull(loops.flat())];
  }
  loops = loops.filter((lp) => lp.length >= 3).map((lp) => lp.map((q) => [q[0] * scale, q[1] * scale]));
  const pts = loops.flat();
  const size = pts.length
    ? [Math.max(...pts.map((q) => q[0])) - Math.min(...pts.map((q) => q[0])), Math.max(...pts.map((q) => q[1])) - Math.min(...pts.map((q) => q[1]))]
    : [0, 0];
  return { template, loops, diameter, size, function: fn };
}

// ---------------------------------------------------------------------------------------------------------------
// arcs

function arcSweep(start, end, center, clockwise) {
  const a0 = Math.atan2(start[1] - center[1], start[0] - center[0]);
  const a1 = Math.atan2(end[1] - center[1], end[0] - center[0]);
  let sweep = a1 - a0;
  const same = Math.hypot(start[0] - end[0], start[1] - end[1]) < 1e-9;
  if (clockwise) {
    while (sweep >= 0) sweep -= 2 * Math.PI;
    if (sweep < -2 * Math.PI + 1e-12) sweep += 2 * Math.PI;
    if (same) sweep = -2 * Math.PI;
  } else {
    while (sweep <= 0) sweep += 2 * Math.PI;
    if (sweep > 2 * Math.PI - 1e-12) sweep -= 2 * Math.PI;
    if (same) sweep = 2 * Math.PI;
  }
  return [a0, sweep];
}

function arcPoints(start, end, center, clockwise, segments = SEGMENTS) {
  const [a0, sweep] = arcSweep(start, end, center, clockwise);
  const rad = Math.hypot(start[0] - center[0], start[1] - center[1]);
  const n = Math.max(1, Math.ceil((Math.abs(sweep) / (Math.PI / 2)) * segments));
  const pts = [];
  for (let i = 1; i < n; i++) pts.push([center[0] + rad * Math.cos(a0 + (sweep * i) / n), center[1] + rad * Math.sin(a0 + (sweep * i) / n)]);
  pts.push(end);
  return pts;
}

function singleQuadrantCenter(start, end, i, j, clockwise) {
  let best = null;
  for (const sx of [1, -1]) {
    for (const sy of [1, -1]) {
      const c = [start[0] + sx * i, start[1] + sy * j];
      const err = Math.abs(Math.hypot(start[0] - c[0], start[1] - c[1]) - Math.hypot(end[0] - c[0], end[1] - c[1]));
      const [, sweep] = arcSweep(start, end, c, clockwise);
      if (Math.abs(sweep) <= Math.PI / 2 + 1e-6 && (best === null || err < best[0])) best = [err, c];
    }
  }
  return best ? best[1] : [start[0] + i, start[1] + j];
}

// ---------------------------------------------------------------------------------------------------------------
// one layer

const WORD = /^((?:G\d+)*)((?:[XYIJ][+-]?\d+)*)(?:D(\d+))?$/;
const SHAPE = { C: 'circle', R: 'rect', O: 'oval', P: 'polygon' };

/**
 * One copper Gerber's objects: `{ layer, tracks, pads, zones, viaRings: [[at, diameter, net]], skipped: {reason: n},
 * generator }`, board frame (Gerber's own), mm.
 */
export function parseCopperLayer(text, layer, { segments = SEGMENTS } = {}) {
  const out = { layer, tracks: [], pads: [], zones: [], viaRings: [], skipped: {}, generator: null };
  const skip = (what) => { out.skipped[what] = (out.skipped[what] ?? 0) + 1; };
  let scaleUnits = 1;
  let xdec = 6, ydec = 6, xint = 3, yint = 3, trailing = false;
  const apertures = new Map();
  const macros = new Map();
  const ta = new Map();
  const to = new Map();
  let current = null;
  let interp = 1;
  let multi = true;
  let region = null;
  let contour = [];
  let regionAttrs = ['', new Map()];
  let x = 0, y = 0;
  let dark = true;

  const coord = (raw, dec, intd) => {
    if (trailing) {
      const neg = raw.startsWith('-');
      const v = Number(raw.replace(/^[+-]/, '').padEnd(intd + dec, '0')) / 10 ** dec;
      return (neg ? -v : v) * scaleUnits;
    }
    return (Number(raw) / 10 ** dec) * scaleUnits;
  };
  const net = () => to.get('N')?.[0] ?? '';
  const closeContour = () => {
    if (region !== null && contour.length >= 3) region.push(contour);
    contour = [];
  };
  const finishRegion = () => {
    const [fn, attrs] = regionAttrs;
    const f = fn.split(',')[0].toLowerCase();
    const loops = region ?? [];
    const n = attrs.get('N')?.[0] ?? '';
    if (!loops.length) return;
    if (!dark) skip('clear-polarity objects');
    else if (f === 'conductor' || f === '' || f === 'etchedcomponent') {
      const fill = loops.flatMap((lp) => unfracture(lp.map(rp)));
      if (fill.length) out.zones.push({ layer, net: n, kind: f === 'etchedcomponent' ? 'shape' : 'region', fill, area: r(fillArea(fill)) });
    } else if (PAD_FUNCTIONS.has(f)) {
      const pts = loops.flat();
      const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
      const pv = attrs.get('P') ?? [];
      out.pads.push({
        ref: pv.length ? pv[0] || null : null,
        number: pv.length > 1 ? pv[1] : '',
        net: n,
        layers: [layer],
        at: rp([(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2]),
        shape: 'polygon',
        size: rp([Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)]),
        polygons: loops.map((lp) => ccw(lp.map(rp))),
        function: fn,
      });
    } else skip(`${fn || 'unlabelled'} regions`);
  };

  for (const [kind, cmd] of tokens(String(text))) {
    if (kind === 'ext') {
      const head = cmd.slice(0, 2);
      if (head === 'FS') {
        const m = /^FS([LT]?)([AI]?)X(\d)(\d)Y(\d)(\d)/.exec(cmd);
        if (m) {
          trailing = m[1] === 'T';
          [xint, xdec, yint, ydec] = [Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6])];
        }
      } else if (head === 'MO') scaleUnits = cmd.slice(2, 4) === 'IN' ? 25.4 : 1;
      else if (head === 'AM') {
        const star = cmd.indexOf('*');
        const name = star < 0 ? cmd.slice(2) : cmd.slice(2, star);
        macros.set(name, star < 0 ? [] : cmd.slice(star + 1).split('*').filter((s) => s.trim()));
      } else if (head === 'AD') {
        const m = /^ADD(\d+)([^,]*)(?:,(.*))?$/.exec(cmd);
        if (m) {
          const params = (m[3] ?? '').split('X').filter((v) => v.trim()).map(Number);
          apertures.set(Number(m[1]), aperture(m[2], params, macros, scaleUnits, ta.get('AperFunction') ?? '', segments));
        }
      } else if (head === 'TA') {
        const body = cmd.slice(3), comma = body.indexOf(',');
        ta.set(comma < 0 ? body : body.slice(0, comma), comma < 0 ? '' : body.slice(comma + 1));
      } else if (head === 'TO') {
        const body = cmd.slice(3), comma = body.indexOf(',');
        const name = comma < 0 ? body : body.slice(0, comma), val = comma < 0 ? '' : body.slice(comma + 1);
        // X2: 'N/C' is a pad on no net (a single-pad net); '' no net either
        to.set(name, name === 'N' ? [val === 'N/C' ? '' : unescape(val)] : splitFields(val));
      } else if (head === 'TD') {
        const name = cmd.slice(2).replace(/^\./, '');
        if (!name) { ta.clear(); to.clear(); } else { ta.delete(name); to.delete(name); }
      } else if (head === 'TF') {
        if (cmd.startsWith('TF.GenerationSoftware,')) out.generator = splitFields(cmd.slice(cmd.indexOf(',') + 1)).join(' ');
      } else if (head === 'LP') dark = cmd.slice(2, 3) !== 'C';
      else if (head === 'SR' && cmd !== 'SR' && cmd !== 'SRX1Y1I0J0') skip('step-and-repeat blocks (%SR, not supported)');
      else if ((head === 'LM' || head === 'LR' || head === 'LS') && !['LMN', 'LR0', 'LS1'].includes(cmd)) skip(`%${head} transforms (not supported)`);
      continue;
    }
    if (cmd.startsWith('G04') || cmd === 'M02' || cmd === 'M00' || cmd === 'M01') continue;
    const m = WORD.exec(cmd);
    if (!m) continue;
    for (const g of m[1].match(/G\d+/g) ?? []) {
      const code = Number(g.slice(1));
      if (code === 1 || code === 2 || code === 3) interp = code;
      else if (code === 74) multi = false;
      else if (code === 75) multi = true;
      else if (code === 36) {
        region = [];
        contour = [];
        regionAttrs = [ta.get('AperFunction') ?? '', new Map(to)];
      } else if (code === 37) {
        closeContour();
        finishRegion();
        region = null;
      }
    }
    let d = m[3] ? Number(m[3]) : null;
    if (d !== null && d >= 10) { current = apertures.get(d) ?? null; continue; }
    const coords = {};
    for (const c of m[2].matchAll(/([XYIJ])([+-]?\d+)/g)) coords[c[1]] = c[2];
    if (d === null && !Object.keys(coords).length) continue;
    const x0 = x, y0 = y;
    if ('X' in coords) x = coord(coords.X, xdec, xint);
    if ('Y' in coords) y = coord(coords.Y, ydec, yint);
    const i = 'I' in coords ? coord(coords.I, xdec, xint) : 0;
    const j = 'J' in coords ? coord(coords.J, ydec, yint) : 0;
    if (d === null) d = 1;
    const start = [x0, y0], end = [x, y];
    const centre = () => (multi ? [x0 + i, y0 + j] : singleQuadrantCenter(start, end, Math.abs(i), Math.abs(j), interp === 2));
    if (region !== null) {
      if (d === 2) { closeContour(); contour = [end]; }
      else if (d === 1) {
        if (!contour.length) contour = [start];
        if (interp === 1) contour.push(end);
        else contour.push(...arcPoints(start, end, centre(), interp === 2, segments));
      }
      continue;
    }
    if (d === 2 || current === null) continue;
    const fn = current.function;
    const f = fn.split(',')[0].toLowerCase();
    if (!dark) { skip('clear-polarity objects'); continue; }
    if (d === 1) {
      // EtchedComponent: copper a footprint draws
      if (f !== 'conductor' && f !== '' && f !== 'etchedcomponent') { skip(`${fn} draws`); continue; }
      if (current.diameter === null) { skip('draws with a non-circular aperture'); continue; }
      const width = r(current.diameter);
      if (interp === 1) {
        out.tracks.push({ layer, net: net(), width, start: rp(start), end: rp(end), mid: null, id: null });
        continue;
      }
      const c = centre();
      if (Math.hypot(start[0] - end[0], start[1] - end[1]) < 1e-9) {
        // a full circle: two half arcs, as circleHalves
        for (const [s, e, mid] of circleHalves(c, start)) out.tracks.push({ layer, net: net(), width, start: s, end: e, mid, id: null });
        continue;
      }
      const [a0, sweep] = arcSweep(start, end, c, interp === 2);
      const rad = Math.hypot(start[0] - c[0], start[1] - c[1]);
      const mid = rp([c[0] + rad * Math.cos(a0 + sweep / 2), c[1] + rad * Math.sin(a0 + sweep / 2)]);
      out.tracks.push({ layer, net: net(), width, start: rp(start), end: rp(end), mid, id: null });
    } else if (d === 3) {
      if (f === 'viapad') out.viaRings.push([rp(end), r(current.size[0]), net()]);
      else if (PAD_FUNCTIONS.has(f) || f === '') {
        const pv = to.get('P') ?? [];
        out.pads.push({
          ref: pv.length ? pv[0] || null : null,
          number: pv.length > 1 ? pv[1] : '',
          net: net(),
          layers: [layer],
          at: rp(end),
          shape: SHAPE[current.template] ?? current.template,
          size: rp(current.size),
          polygons: current.loops.map((lp) => ccw(lp.map((q) => rp([x + q[0], y + q[1]])))),
          function: fn || null,
        });
      } else skip(`${fn} flashes`);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------------------------------------------
// a set of files

const textOf = (f) => (typeof f.text === 'string' ? f.text : typeof f.content === 'string' ? f.content : new TextDecoder().decode(f.data ?? f.content));

function copperId(role, count) {
  if (role.side === 'top' || role.index === 1) return 'F.Cu';
  if (role.side === 'bottom' || (count && role.index === count)) return 'B.Cu';
  return `In${role.index - 1}.Cu`;
}

/**
 * boarddd/copper@1 from fab files in the browser: `files` is `[{ name, text }]` (or `content`/`data`); copper
 * layers are found by their X2 `%TF.FileFunction` (or KiCad/Protel names), drills (Excellon) give via drills and
 * an Edge.Cuts/profile Gerber gives the board area for `planes`. Options: `name` (the board name), `checkFills`
 * (default true), `segments`.
 */
export function copperFromGerbers(files, { name = 'board', checkFills: check = true, segments = SEGMENTS } = {}) {
  const list = Array.isArray(files) ? files : Object.entries(files).map(([n, text]) => ({ name: n, text }));
  const copper = [];
  const drills = [];
  let outlineText = null;
  for (const f of list) {
    const text = textOf(f);
    const role = layerRole(f.name, text);
    if (role.role === 'copper') copper.push({ file: f, text, role });
    else if (role.role === 'drill') drills.push({ text, plated: role.plated });
    else if (role.role === 'outline' && outlineText === null) outlineText = text;
  }
  const count = Math.max(0, ...copper.map((c) => c.role.index ?? 0), copper.length);
  for (const c of copper) {
    c.id = copperId(c.role, count);
    c.order = c.id === 'F.Cu' ? 0 : c.id === 'B.Cu' ? 1e6 : Number(c.id.slice(2, -3));
  }
  copper.sort((a, b) => a.order - b.order);
  const warnings = [];
  const doc = {
    schema: 'boarddd/copper@1',
    board: name,
    source: {
      kind: 'gerber',
      files: list.filter((f) => copper.some((c) => c.file === f) || layerRole(f.name, textOf(f)).role === 'drill').map((f) => ({ path: f.name, role: copper.some((c) => c.file === f) ? 'copper' : 'drill', side: null, sha256: null })),
      generator: null,
      reader: 'boarddd/copper copperFromGerbers',
      created: null,
      commit: null,
      ref: null,
    },
    units: 'mm',
    frame: 'board',
    layers: copper.map((c) => c.id),
    nets: [],
    tracks: [],
    vias: [],
    zones: [],
    keepouts: [],
    pads: [],
    net_ties: [],
    planes: [],
    warnings,
    meta: {},
  };
  const rings = new Map();
  const skipped = {};
  let noNet = 0;
  for (const c of copper) {
    const lc = parseCopperLayer(c.text, c.id, { segments });
    doc.source.generator ??= lc.generator;
    doc.tracks.push(...lc.tracks);
    doc.pads.push(...lc.pads);
    doc.zones.push(...lc.zones);
    for (const [at, dia, n] of lc.viaRings) {
      const k = `${at[0]},${at[1]}`;
      if (!rings.has(k)) rings.set(k, [at, []]);
      rings.get(k)[1].push([c.id, dia, n]);
    }
    for (const [what, n] of Object.entries(lc.skipped)) skipped[what] = (skipped[what] ?? 0) + n;
    if (!c.text.includes('%TO.N') && (lc.tracks.length || lc.zones.length)) noNet += 1;
  }
  const named = new Map(doc.pads.filter((p) => p.ref !== null).map((p) => [JSON.stringify([p.at, p.net]), [p.ref, p.number]]));
  for (const p of doc.pads) {
    const k = JSON.stringify([p.at, p.net]);
    if (p.ref === null && named.has(k)) [p.ref, p.number] = named.get(k);
  }
  const order = new Map(doc.layers.map((l, i) => [l, i]));
  const holes = drills.flatMap((d) => parseExcellon(d.text, { plated: d.plated ?? true })).filter((h) => h.plated && h.x2 == null);
  const byXY = new Map(holes.map((h) => [`${h.x.toFixed(3)},${h.y.toFixed(3)}`, h]));
  for (const [at, found] of rings.values()) {
    found.sort((a, b) => order.get(a[0]) - order.get(b[0]));
    const span = [found[0][0], found[found.length - 1][0]];
    const inside = doc.layers.slice(order.get(span[0]), order.get(span[1]) + 1);
    const dias = new Map(found.map(([l, d]) => [l, d]));
    const hole = byXY.get(`${at[0].toFixed(3)},${at[1].toFixed(3)}`);
    const outer = [doc.layers[0], doc.layers[doc.layers.length - 1]];
    doc.vias.push({
      at,
      net: found.map((f) => f[2]).find((n) => n) ?? '',
      diameter: Math.max(...dias.values()),
      drill: hole ? r(hole.diameter) : 0,
      span,
      type: span[0] === outer[0] && span[1] === outer[1] ? 'through' : span.some((l) => outer.includes(l)) ? 'blind' : 'buried',
      pad_layers: dias.size < inside.length ? inside.filter((l) => dias.has(l)) : null,
      padstack: new Set(dias.values()).size > 1 ? [...dias].map(([l, d]) => ({ layer: l, diameter: d })) : null,
      id: null,
    });
  }
  if (!holes.length && doc.vias.length) warnings.push('no drill files: via drills are 0');
  if (noNet) warnings.push(`${noNet} copper layer(s) have no %TO.N net attributes (not Gerber X2): their copper is on no net`);
  for (const what of Object.keys(skipped).sort()) warnings.push(`${skipped[what]} ${what} on copper layers skipped (not tracks, pads, vias or filled copper)`);
  const nets = new Set();
  for (const item of [...doc.pads, ...doc.tracks, ...doc.vias, ...doc.zones]) nets.add(item.net);
  doc.nets = [...(nets.has('') ? [''] : []), ...[...nets].filter((n) => n)];
  for (const z of doc.zones) Object.assign(z, { outline: null, priority: null, name: null, filled: true, stale: null, id: null });
  for (const p of doc.pads) Object.assign(p, { rotation: 0, drill: null, type: null }, { function: p.function ?? null });
  let area = null;
  if (outlineText) {
    const o = boardOutline(outlineText);
    if (o) area = Math.abs(signedArea(o.outer)) - o.holes.reduce((s, h) => s + Math.abs(signedArea(h)), 0);
  }
  doc.planes = planes(doc.zones, area, doc.layers);
  if (check) checkFills(doc);
  return doc;
}
