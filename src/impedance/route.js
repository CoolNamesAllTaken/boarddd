// boarddd/impedance: a net's route on a real board → cross-sections → impedance per section (impedance phase I6).
// python/src/boarddd/impedance/route.py is the same code (python/tests/test_impedance_route.py checks the two
// against each other); docs/impedance.md "Along a route" describes the method and its thresholds.
//
// analyzeNet(board, copper, net | [p, n], options) walks the net's tracks in order (vias join layers), cuts a
// station every `step` mm, and at each station casts a line across the trace and intersects it with the copper
// (boarddd/copper@1) on the trace's layer (coplanar grounds, the pair partner, other signals) and on the layers
// above and below (the reference planes: solid under the trace and 3h beyond, an edge, a void or split, or
// none). That gives the station's real cross-section in the board@1 stackup, which is classified (microstrip,
// embedded microstrip, stripline, offset stripline, CPW, CPWG; edge-coupled for a pair) and solved (tier 2 by
// default, cached by its quantised geometry). Stations with the same geometry merge into sections; the result
// is a boarddd/impedance@1 document with the sections, their flags, the discontinuities along the route and a
// length-weighted summary against the net class's target.

import { calculate } from './closedform.js';
import { solveCrossSection } from './fieldsolver.js';
import { lineLoss, sectionLoss } from './loss.js';
import { lineFromStackup } from './stackup.js';

export const IMPEDANCE_SCHEMA_ID = 'boarddd/impedance@1';

/** Defaults of analyzeNet's options (mm unless said otherwise). */
export const ROUTE_DEFAULTS = Object.freeze({
  step: 0.25,           // station spacing along the route
  solver: 'field',      // 'field' (tier 2) or 'closedform' (tier 1, field where tier 1 has no model)
  tolerancePct: 10,     // when the target gives none
  window: null,         // half-width of the cut line; default max(1, w/2 + 8 h)
  coplanarWindow: null, // a ground this close to the trace edge makes it coplanar; default max(3 w, 5 h)
  pairWindow: null,     // the partner of a pair this close (edge to edge) and parallel couples; default max(0.5, 4 w)
  parallelDeg: 20,      // largest angle between the pair's tracks that still counts as parallel
  refMargin: 3,         // a reference plane must reach this many h beyond the trace edges ('ref_edge' otherwise)
  neighbours: 'ignore', // 'ground': other nets' copper within the coplanar window is solved as grounded
  noPlane: 'skip',      // no reference plane (via antipads, voids): no Z ('solve': CPW between coplanar grounds)
  fieldOptions: { level: -1, maxLevel: 0, tol: 0.05 },        // cross-sections covering >= shortLength: within 0.5 % of fine solves
  shortFieldOptions: { level: -2, maxLevel: -1, tol: 0.05 },  // the rest (breakouts, via transitions): within ~3 % (tested)
  shortLength: 1,       // mm of route a cross-section must cover to get fieldOptions
  frequency: null,      // Hz: also the loss of every section and of the route there (loss.js); null: no loss
  frequencies: null,    // Hz: the loss and Z sweep per section and route; default LOSS_SWEEP plus `frequency`
  lossOptions: null,    // loss.js options over the stackup's (e.g. { conductor: { roughness: { rq: 0.001 } } })
});

/** The default loss sweep of analyzeNet: 100 MHz to 40 GHz, four points per decade. */
export const LOSS_SWEEP = Object.freeze([1e8, 1.8e8, 3.2e8, 5.6e8, 1e9, 1.8e9, 3.2e9, 5.6e9, 1e10, 1.8e10, 3.2e10, 4e10]);

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
const GROUND_NAME = /^(?:[ADPS]?GND|VSS|GROUND|EARTH|CHASSIS)(?:[_\-.].*)?$/i;
const R6 = (v) => { const x = Math.round(v * 1e6) / 1e6; return x === 0 ? 0 : x; };
/** v to 9 significant digits (loss values span decades). */
const S9 = (v) => Number(v.toPrecision(9));
const q = (v, step) => { const x = Math.round(v / step) * step; return Math.round(x * 1e6) / 1e6 || 0; };
/** v rounded to a geometric grid of ratio 1 + rel (at least `floor`): gaps that differ by less share a solve. */
const qlog = (v, rel, floor) => {
  if (!(v > floor)) return R6(Math.max(v, 0) > floor / 2 ? floor : 0);
  const k = Math.round(Math.log(v / floor) / Math.log(1 + rel));
  return R6(floor * (1 + rel) ** k);
};

// ── geometry ─────────────────────────────────────────────────────────────────────────────────────────────────

/** Centre, radius, start angle and signed sweep of an arc track through start, mid, end. */
function arcOf(t) {
  const [ax, ay] = t.start, [mx, my] = t.mid, [bx, by] = t.end;
  const d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my));
  if (Math.abs(d) < 1e-12) return null;
  const ux = ((ax * ax + ay * ay) * (my - by) + (mx * mx + my * my) * (by - ay) + (bx * bx + by * by) * (ay - my)) / d;
  const uy = ((ax * ax + ay * ay) * (bx - mx) + (mx * mx + my * my) * (ax - bx) + (bx * bx + by * by) * (mx - ax)) / d;
  const r = Math.sqrt((ax - ux) ** 2 + (ay - uy) ** 2);
  const a0 = Math.atan2(ay - uy, ax - ux), am = Math.atan2(my - uy, mx - ux), a1 = Math.atan2(by - uy, bx - ux);
  const ccw = (a, b) => { let x = b - a; while (x < 0) x += 2 * Math.PI; while (x >= 2 * Math.PI) x -= 2 * Math.PI; return x; };
  let sweep = ccw(a0, a1);
  if (ccw(a0, am) > sweep) sweep -= 2 * Math.PI;   // mid is not on the counter-clockwise way: clockwise
  return { c: [ux, uy], r, a0, sweep };
}

/** A track as a path: length and point/direction at a distance along it. */
function pathOf(t) {
  const arc = t.mid ? arcOf(t) : null;
  if (arc) {
    const len = Math.abs(arc.sweep) * arc.r, sg = Math.sign(arc.sweep);
    return {
      len,
      at(s) {
        const a = arc.a0 + (arc.sweep * s) / len;
        return { p: [arc.c[0] + arc.r * Math.cos(a), arc.c[1] + arc.r * Math.sin(a)], d: [-Math.sin(a) * sg, Math.cos(a) * sg] };
      },
      pieces() {   // capsules for the obstacle index: 8 per quarter turn
        const n = Math.max(1, Math.ceil((Math.abs(arc.sweep) / (Math.PI / 2)) * 8));
        const pts = Array.from({ length: n + 1 }, (_, i) => this.at((len * i) / n).p);
        return pts.slice(1).map((b, i) => [pts[i], b]);
      },
    };
  }
  const [ax, ay] = t.start, [bx, by] = t.end;
  const len = Math.sqrt((bx - ax) ** 2 + (by - ay) ** 2);
  const d = len > 0 ? [(bx - ax) / len, (by - ay) / len] : [1, 0];
  return { len, at: (s) => ({ p: [ax + d[0] * s, ay + d[1] * s], d }), pieces: () => [[t.start, t.end]] };
}

const bboxOf = (pts) => {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) { if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y; }
  return [x0, y0, x1, y1];
};

/**
 * The copper of one layer, indexed on a 1 mm grid: capsules (tracks), discs (vias) and polygons with holes
 * (pads, zone fills), each with its net.
 */
function layerIndex(copper, layer) {
  const items = [];
  for (const t of copper.tracks ?? []) {
    if (t.layer !== layer) continue;
    for (const [a, b] of pathOf(t).pieces()) {
      items.push({ type: 'capsule', a, b, r: t.width / 2, net: t.net, kind: 'track', id: t.id ?? null, width: t.width,
        box: [Math.min(a[0], b[0]) - t.width / 2, Math.min(a[1], b[1]) - t.width / 2, Math.max(a[0], b[0]) + t.width / 2, Math.max(a[1], b[1]) + t.width / 2] });
    }
  }
  const order = new Map((copper.layers ?? []).map((l, i) => [l, i]));
  for (const v of copper.vias ?? []) {
    const lo = order.get(v.span[0]), hi = order.get(v.span[1]), at = order.get(layer);
    if (at == null || lo == null || hi == null || at < lo || at > hi) continue;
    if (v.pad_layers && !v.pad_layers.includes(layer)) continue;
    const dia = v.padstack?.find((p) => p.layer === layer)?.diameter ?? v.diameter;
    const r = dia / 2;
    items.push({ type: 'disc', c: v.at, r, net: v.net, kind: 'via', box: [v.at[0] - r, v.at[1] - r, v.at[0] + r, v.at[1] + r] });
  }
  for (const p of copper.pads ?? []) {
    if (!p.layers.includes(layer)) continue;
    for (const ring of p.polygons ?? []) {
      if (ring.length >= 3) items.push({ type: 'poly', rings: [ring], net: p.net, kind: 'pad', box: bboxOf(ring) });
    }
  }
  for (const z of copper.zones ?? []) {
    if (z.layer !== layer) continue;
    for (const f of z.fill) {
      items.push({ type: 'poly', rings: [f.outline, ...f.holes], net: z.net, kind: z.kind === 'teardrop' ? 'teardrop' : 'zone', box: bboxOf(f.outline) });
    }
  }
  const grid = new Map();
  items.forEach((it, i) => {
    for (let gx = Math.floor(it.box[0]); gx <= Math.floor(it.box[2]); gx++) {
      for (let gy = Math.floor(it.box[1]); gy <= Math.floor(it.box[3]); gy++) {
        const k = `${gx},${gy}`;
        if (!grid.has(k)) grid.set(k, []);
        grid.get(k).push(i);
      }
    }
  });
  return { items, grid };
}

/** The items of an index whose box meets the segment p0..p1 (grid cells along its box). */
function query(idx, p0, p1) {
  const x0 = Math.floor(Math.min(p0[0], p1[0])), x1 = Math.floor(Math.max(p0[0], p1[0]));
  const y0 = Math.floor(Math.min(p0[1], p1[1])), y1 = Math.floor(Math.max(p0[1], p1[1]));
  const seen = new Set();
  for (let gx = x0; gx <= x1; gx++) for (let gy = y0; gy <= y1; gy++) for (const i of idx.grid.get(`${gx},${gy}`) ?? []) seen.add(i);
  return [...seen].sort((a, b) => a - b).map((i) => idx.items[i]);
}

/**
 * Where an item crosses the cut line through p along n (u = distance along n), as [lo, hi] intervals; the
 * station frame is a = along the trace (d), u = across (n).
 */
function crossings(it, p, d, n) {
  const loc = (q) => [(q[0] - p[0]) * d[0] + (q[1] - p[1]) * d[1], (q[0] - p[0]) * n[0] + (q[1] - p[1]) * n[1]];
  const disc = (c, r) => {
    const [a, u] = loc(c);
    if (Math.abs(a) > r) return null;
    const h = Math.sqrt(r * r - a * a);
    return [u - h, u + h];
  };
  const ringCuts = (ring) => {
    const out = [];
    let [a1, u1] = loc(ring[ring.length - 1]);
    for (const qq of ring) {
      const [a2, u2] = loc(qq);
      if ((a1 > 0) !== (a2 > 0)) out.push(u1 + ((0 - a1) * (u2 - u1)) / (a2 - a1));
      a1 = a2; u1 = u2;
    }
    return out;
  };
  if (it.type === 'disc') { const iv = disc(it.c, it.r); return iv ? [iv] : []; }
  if (it.type === 'capsule') {
    const parts = [disc(it.a, it.r), disc(it.b, it.r)].filter(Boolean);
    const dx = it.b[0] - it.a[0], dy = it.b[1] - it.a[1], L = Math.sqrt(dx * dx + dy * dy);
    if (L > 0) {
      const m = [(-dy / L) * it.r, (dx / L) * it.r];
      const quad = [[it.a[0] + m[0], it.a[1] + m[1]], [it.b[0] + m[0], it.b[1] + m[1]], [it.b[0] - m[0], it.b[1] - m[1]], [it.a[0] - m[0], it.a[1] - m[1]]];
      const c = ringCuts(quad).sort((x, y) => x - y);
      if (c.length >= 2) parts.push([c[0], c[c.length - 1]]);
    }
    if (!parts.length) return [];
    return [[Math.min(...parts.map((x) => x[0])), Math.max(...parts.map((x) => x[1]))]];
  }
  const cuts = it.rings.flatMap(ringCuts).sort((x, y) => x - y);
  const out = [];
  for (let i = 0; i + 1 < cuts.length; i += 2) out.push([cuts[i], cuts[i + 1]]);
  return out;
}

/** Overlapping or touching intervals merged, sorted. */
function union(ivs) {
  const s = [...ivs].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const out = [];
  for (const [lo, hi] of s) {
    if (out.length && lo <= out[out.length - 1][1] + 1e-6) out[out.length - 1][1] = Math.max(out[out.length - 1][1], hi);
    else out.push([lo, hi]);
  }
  return out;
}

// ── the route ────────────────────────────────────────────────────────────────────────────────────────────────

/**
 * A net's tracks in route order: depth first from the end with the lowest (x, y) (a branch continues from its
 * node), each track oriented the way it is walked, with `s0` its distance from the start and `run` the index
 * of the unbroken stretch it belongs to (a new run after every jump back to a branch point).
 */
export function netRoute(copper, net) {
  const tracks = (copper.tracks ?? []).map((t, i) => ({ t, i })).filter(({ t }) => t.net === net);
  const key = (p) => `${Math.round(p[0] * 1000)},${Math.round(p[1] * 1000)}`;
  const at = new Map(), pos = new Map();
  for (const e of tracks) {
    for (const end of [e.t.start, e.t.end]) {
      const k = key(end);
      if (!at.has(k)) { at.set(k, []); pos.set(k, end); }
      at.get(k).push(e);
    }
  }
  const visited = new Set();
  const out = [];
  let run = -1, sBase = 0;
  const nodes = [...at.keys()].sort((a, b) => {
    const da = at.get(a).length === 1 ? 0 : 1, db = at.get(b).length === 1 ? 0 : 1;
    const pa = pos.get(a), pb = pos.get(b);
    return da - db || pa[0] - pb[0] || pa[1] - pb[1];
  });
  for (const startNode of nodes) {
    if (at.get(startNode).every((e) => visited.has(e.i))) continue;
    // depth first; a stack of [node, s] to continue from
    const stack = [[startNode, sBase]];
    let last = null;
    while (stack.length) {
      const [node, s] = stack.pop();
      const next = at.get(node).filter((e) => !visited.has(e.i)).sort((a, b) => a.i - b.i);
      if (!next.length) continue;
      const e = next[0];
      visited.add(e.i);
      if (next.length > 1) stack.push([node, s]);
      const forward = key(e.t.start) === node;
      if (last !== node) run += 1;
      const path = pathOf(e.t);
      out.push({ track: e.t, index: e.i, forward, s0: s, s1: s + path.len, run, path });
      const other = key(forward ? e.t.end : e.t.start);
      last = other;
      stack.push([other, s + path.len]);
      sBase = Math.max(sBase, s + path.len);
    }
  }
  return out;
}

// ── stackup ──────────────────────────────────────────────────────────────────────────────────────────────────

function stackInfo(stackup) {
  const layers = stackup?.layers ?? [];
  const copper = [];
  layers.forEach((l, i) => { if (l.kind === 'copper') copper.push({ name: l.layer ?? l.name, index: i, t: l.thickness ?? 0.035 }); });
  /** Dielectric (and voided copper) distance between copper a and b, edge to edge. */
  const between = (a, b) => {
    const [i, j] = [copper[a].index, copper[b].index].sort((x, y) => x - y);
    let h = 0;
    for (let k = i + 1; k < j; k++) h += layers[k].kind === 'dielectric' || layers[k].kind === 'copper' ? layers[k].thickness ?? 0 : 0;
    return h;
  };
  return { copper, between, order: new Map(copper.map((c, i) => [c.name, i])) };
}

// ── one station ──────────────────────────────────────────────────────────────────────────────────────────────

/**
 * The environment of the trace at p (direction d) on a layer: the same-layer copper across the cut, the
 * partner of a pair, and the reference planes above and below. Returns the layout for the field solver
 * (relative x, the trace centred at 0), its quantised key and the classification.
 */
function station(ctx, st) {
  const { o, stack, index, groundNets } = ctx;
  const li = stack.order.get(st.layer);
  const n = [-st.d[1], st.d[0]];
  const w = st.width;
  // the nearest dielectric height decides the windows
  const hNear = Math.min(...[li - 1, li + 1].filter((j) => j >= 0 && j < stack.copper.length).map((j) => stack.between(li, j)), Infinity);
  const h0 = Number.isFinite(hNear) ? hNear : 0.2;
  const R = o.window ?? Math.max(1, w / 2 + 8 * h0);
  const cop = o.coplanarWindow ?? Math.max(3 * w, 5 * h0);
  const pairWin = o.pairWindow ?? Math.max(0.5, 4 * w);
  const p0 = [st.p[0] - n[0] * R, st.p[1] - n[1] * R], p1 = [st.p[0] + n[0] * R, st.p[1] + n[1] * R];
  const flags = [];
  const hits = [];
  for (const it of query(index(st.layer), p0, p1)) {
    if (it.net === st.net) continue;
    for (const [lo, hi] of crossings(it, st.p, st.d, n)) {
      if (hi < -R || lo > R) continue;
      hits.push({ lo: Math.max(lo, -R), hi: Math.min(hi, R), it });
    }
  }
  // the partner of a pair: its nearest parallel track
  let partner = null;
  if (st.partner) {
    const cos = Math.cos((o.parallelDeg * Math.PI) / 180);
    for (const h of hits) {
      if (h.it.net !== st.partner || h.it.kind !== 'track') continue;
      const dx = h.it.b[0] - h.it.a[0], dy = h.it.b[1] - h.it.a[1], L = Math.sqrt(dx * dx + dy * dy);
      if (!(L > 0) || Math.abs((dx * st.d[0] + dy * st.d[1]) / L) < cos) continue;
      const side = h.lo + h.hi > 0 ? 1 : -1;
      const gap = side > 0 ? h.lo - w / 2 : -w / 2 - h.hi;
      if (gap < 0 || gap > pairWin) continue;
      if (!partner || gap < partner.gap) partner = { side, gap, width: h.it.width };
    }
  }
  // the structure's copper, relative x
  const traces = [{ x0: -w / 2, x1: w / 2, net: partner ? 'p' : 'sig' }];
  if (partner) {
    const g = qlog(partner.gap, 0.02, 0.01), wn = q(partner.width, 0.001);
    traces.push(partner.side > 0 ? { x0: R6(w / 2 + g), x1: R6(w / 2 + g + wn), net: 'n' } : { x0: R6(-w / 2 - g - wn), x1: R6(-w / 2 - g), net: 'n' });
  }
  const left = Math.min(...traces.map((t) => t.x0)), right = Math.max(...traces.map((t) => t.x1));
  // coplanar grounds and neighbours on each side
  const isGround = (it) => it.kind === 'zone' || groundNets.has(it.net);
  const grounds = [];
  const coplanar = [null, null];
  for (const side of [-1, 1]) {
    const edge = side > 0 ? right : left;
    const beyond = hits.filter((h) => h.it.net !== (partner ? st.partner : null) && (side > 0 ? h.hi > edge : h.lo < edge));
    let nearG = null, nearS = null;
    for (const h of beyond) {
      const dist = side > 0 ? h.lo - edge : edge - h.hi;
      if (dist < 0) { if (!flags.includes('overlap')) flags.push('overlap'); continue; }
      if (isGround(h.it)) { if (!nearG || dist < nearG.dist) nearG = { dist, h }; }
      else if (!nearS || dist < nearS.dist) nearS = { dist, h };
    }
    if (nearS && nearS.dist <= cop && (!nearG || nearS.dist < nearG.dist)) {
      if (!flags.includes('neighbour')) flags.push('neighbour');
      if (o.neighbours === 'ground') nearG = nearS;
    }
    if (nearG && nearG.dist <= cop) {
      // the ground's extent: the copper of ground-like items contiguous from its near edge, to the window
      const ivs = union(beyond.filter((h) => isGround(h.it) || h === nearG.h).map((h) => [h.lo, h.hi]));
      const g0 = side > 0 ? edge + nearG.dist : edge - nearG.dist;
      const run = ivs.find(([lo, hi]) => lo <= g0 + 1e-6 && hi >= g0 - 1e-6) ?? [g0, g0];
      const gap = qlog(nearG.dist, 0.1, 0.005);
      const near = R6(side > 0 ? edge + gap : edge - gap);
      const far = side > 0 ? run[1] : run[0];
      // a ground at least max(2 h, 2 w, 0.2 mm) wide is solved as unbounded; a narrower strip as it is (to 0.1 mm)
      const wide = Math.abs(far) >= R - 1e-6 || Math.abs(far - near) >= Math.max(2 * h0, 2 * w, 0.2);
      const farQ = wide ? null : R6(side > 0 ? Math.max(near + 0.1, q(far, 0.1)) : Math.min(near - 0.1, q(far, 0.1)));
      grounds.push(side > 0 ? { x0: near, x1: farQ } : { x0: farQ, x1: near });
      coplanar[side > 0 ? 1 : 0] = gap;
    }
  }
  // reference planes: the first copper above / below that covers the trace and its margin
  const refs = { top: null, bottom: null };
  const planes = { top: null, bottom: null };
  for (const [side, step] of [['top', -1], ['bottom', 1]]) {
    const skipped = [];
    for (let j = li + step; j >= 0 && j < stack.copper.length; j += step) {
      const name = stack.copper[j].name;
      const h = stack.between(li, j);
      const zs = query(index(name), p0, p1).filter((it) => it.kind === 'zone');
      const cov = union(zs.flatMap((it) => crossings(it, st.p, st.d, n).map(([lo, hi]) => [lo, hi, it.net])).map(([lo, hi]) => [Math.max(lo, -R), Math.min(hi, R)]).filter(([lo, hi]) => hi > lo));
      const under = cov.find(([lo, hi]) => lo <= left + 1e-6 && hi >= right - 1e-6);
      if (!under) {
        const touched = cov.some(([lo, hi]) => hi > left && lo < right);
        const plane = ctx.planeLayers.has(name);
        if (touched || plane) { if (!flags.includes('plane_gap')) flags.push('plane_gap'); }
        skipped.push(name);
        continue;
      }
      // the net of the plane: the zone that covers the trace centre
      const zNet = zs.find((it) => crossings(it, st.p, st.d, n).some(([lo, hi]) => lo <= (left + right) / 2 && hi >= (left + right) / 2))?.net ?? '';
      const margin = o.refMargin * h;
      const x0 = under[0] <= -R + 1e-6 ? null : R6(q(under[0], 0.1));
      const x1 = under[1] >= R - 1e-6 ? null : R6(q(under[1], 0.1));
      if ((x0 != null && x0 > left - margin) || (x1 != null && x1 < right + margin)) {
        if (!flags.includes('ref_edge')) flags.push('ref_edge');
        planes[side] = { x0, x1 };
      }
      refs[side] = { side, layer: name, net: zNet, h: R6(h), extent: [x0, x1], skipped };
      break;
    }
  }
  const structure = classify(stack, li, refs, grounds.length > 0);
  if (!refs.top && !refs.bottom) flags.push('no_ref');
  const layout = { traces: traces.map((t) => ({ x0: R6(t.x0), x1: R6(t.x1), net: t.net })), grounds, planes };
  return { structure, partner, coplanar, refs, flags, layout, kind: partner ? 'differential' : 'single' };
}

/**
 * The structure of a station: by its reference planes (none, one, two), coplanar grounds and whether its layer
 * is outer; two planes more than 10 % apart in height make an offset stripline.
 */
function classify(stack, li, refs, coplanar) {
  const outer = li === 0 || li === stack.copper.length - 1;
  const n = (refs.top ? 1 : 0) + (refs.bottom ? 1 : 0);
  if (n === 0) return coplanar ? 'cpw' : 'none';
  if (n === 1) return coplanar ? 'cpwg' : outer ? 'microstrip' : 'embedded_microstrip';
  if (coplanar) return 'cpwg';
  const h1 = refs.top.h, h2 = refs.bottom.h;
  return Math.abs(h1 - h2) / Math.max(h1, h2) > 0.1 ? 'offset_stripline' : 'stripline';
}

/** Apply a user override ({structure?, refTop?, refBottom?, coplanar?}) to a station's environment. */
function overridden(env, ov, stack, layer) {
  if (!ov) return env;
  const e = structuredClone(env);
  e.flags.push('override');
  const drop = (side) => { e.refs[side] = null; e.layout.planes[side] = null; };
  if (ov.refTop === false) drop('top');
  if (ov.refBottom === false) drop('bottom');
  if (ov.coplanar === false || ['microstrip', 'embedded_microstrip', 'stripline', 'offset_stripline'].includes(ov.structure)) {
    e.layout.grounds = []; e.coplanar = [null, null];
  }
  if (ov.structure === 'cpw') { drop('top'); drop('bottom'); }
  for (const [side, name] of [['top', ov.refTop], ['bottom', ov.refBottom]]) {
    if (typeof name === 'string' && name !== e.refs[side]?.layer) {
      const li = stack.order.get(layer), j = stack.order.get(name);
      if (j == null) throw new RangeError(`override: no copper layer ${name}`);
      e.refs[side] = { side, layer: name, net: '', h: R6(stack.between(li, j)), extent: [null, null], skipped: [] };
      e.layout.planes[side] = null;
    }
  }
  e.structure = ov.structure ?? classify(stack, stack.order.get(layer), e.refs, e.layout.grounds.length > 0);
  return e;
}

// ── solving ──────────────────────────────────────────────────────────────────────────────────────────────────

/** A layout seen from the other side (x → -x): the same impedance. */
function mirrored(layout) {
  const ext = (e) => (e ? { x0: e.x1 == null ? null : R6(-e.x1), x1: e.x0 == null ? null : R6(-e.x0) } : null);
  return {
    traces: layout.traces.map((t) => ({ x0: R6(-t.x1), x1: R6(-t.x0), net: t.net })).sort((a, b) => a.x0 - b.x0),
    grounds: layout.grounds.map(ext).sort((a, b) => (a.x0 ?? -Infinity) - (b.x0 ?? -Infinity)),
    planes: { top: ext(layout.planes.top), bottom: ext(layout.planes.bottom) },
  };
}

/** The layout the solver sees: of the two mirror images, the one whose JSON sorts first (one solve for both). */
function canonical(layout) {
  const a = { ...layout, traces: [...layout.traces].sort((x, y) => x.x0 - y.x0) }, b = mirrored(layout);
  // the trace analysed stays the reference: mirror it back to x = 0 only matters for keys, not for Z
  return JSON.stringify(b) < JSON.stringify(a) ? b : a;
}

/** The geometry key of an environment: what the solver sees, quantised and mirror-canonical. */
function geometryKey(layer, env, solver) {
  return JSON.stringify([layer, solver, env.structure, env.refs.top?.layer ?? null, env.refs.bottom?.layer ?? null, canonical(env.layout)]);
}

/** Solve one environment: { Z0 | Zdiff, Zcommon, ..., error_pct, solver } or null (no reference). */
function solve(ctx, layer, env, width, fieldOptions) {
  if (env.structure === 'none' || (env.structure === 'cpw' && ctx.o.noPlane !== 'solve')) return null;
  const pair = env.kind === 'differential';
  const refTop = env.refs.top?.layer ?? false, refBottom = env.refs.bottom?.layer ?? false;
  const inner2 = env.refs.top && env.refs.bottom;
  const base = { kind: 'single', width, refTop, refBottom, structure: inner2 ? 'stripline' : 'microstrip' };
  const loss = ctx.o.frequency != null;
  if (ctx.o.solver === 'closedform') {
    const t1 = tier1(ctx, layer, env, width);
    if (t1) return t1;
  }
  const line = lineFromStackup(ctx.board.stackup, layer, { ...base, solver: 'field', layout: canonical(env.layout), loss });
  const r = solveCrossSection(line.section, { ...fieldOptions, loss });
  const z = pair
    ? { Z0: null, Zdiff: R6(r.Zdiff), Zcommon: R6(r.Zcommon), Zodd: R6(r.Zodd), Zeven: R6(r.Zeven), eps_eff: null }
    : { Z0: R6(r.Z0), Zdiff: null, Zcommon: null, Zodd: null, Zeven: null, eps_eff: R6(r.eps_eff) };
  return { ...z, error_pct: R6(r.error_pct), solver: 'field', model: null, warnings: line.warnings,
    ...(loss && { lossSource: { result: r, options: line.loss } }) };
}

/** A solved cross-section's loss over the frequencies (loss.js): sweep columns, or null without a source. */
function lossOfSolve(z, fs, extra) {
  const src = z?.lossSource;
  if (!src) return null;
  const opts = { ...src.options, ...extra };
  const r = src.result ? sectionLoss(src.result, fs, opts) : lineLoss(src.model, src.params, fs, opts);
  const pair = r.key === 'Zdiff';
  return { frequency: fs, z: pair ? r.Zdiff : r.Z0, db_per_mm: r.db_per_mm,
    db_per_mm_c: (pair ? r.odd : r).alpha_c.map((a) => (a * 20) / Math.LN10 / 1000),
    db_per_mm_d: (pair ? r.odd : r).alpha_d.map((a) => (a * 20) / Math.LN10 / 1000) };
}

/** Tier 1 where it has a model (infinite planes, symmetric coplanar gap); null to fall back to the field solver. */
function tier1(ctx, layer, env, width) {
  const pair = env.kind === 'differential';
  const st = { microstrip: 'microstrip', stripline: 'stripline', offset_stripline: 'stripline', cpwg: 'coplanar_grounded', cpw: 'coplanar' }[env.structure];
  if (!st || (pair && st.startsWith('coplanar'))) return null;
  if (st === 'coplanar_grounded' && env.refs.top && env.refs.bottom) return null;   // coplanar stripline
  const gaps = env.coplanar.filter((g) => g != null);
  const nt = env.layout.traces.find((t) => t.net === 'n');
  let line;
  try {
    line = lineFromStackup(ctx.board.stackup, layer, {
      width, kind: env.kind, structure: st, refTop: env.refs.top?.layer ?? undefined, refBottom: env.refs.bottom?.layer ?? undefined,
      loss: ctx.o.frequency != null,
      ...(pair && { gap: nt.x0 > 0 ? nt.x0 - width / 2 : -width / 2 - nt.x1 }), ...(gaps.length && { coplanarGap: Math.min(...gaps) }),
    });
  } catch { return null; }
  const r = calculate(line.model, line.params);
  const z = pair
    ? { Z0: null, Zdiff: R6(r.Zdiff), Zcommon: R6(r.Zcommon), Zodd: R6(r.Zodd), Zeven: R6(r.Zeven), eps_eff: null }
    : { Z0: R6(r.Z0), Zdiff: null, Zcommon: null, Zodd: null, Zeven: null, eps_eff: R6(r.eps_eff) };
  const loss = ctx.o.frequency != null && line.params.t > 0;
  return { ...z, error_pct: null, solver: 'closedform', model: line.model, warnings: line.warnings,
    ...(loss && { lossSource: { model: line.model, params: line.params, options: line.loss } }) };
}

// ── the analysis ─────────────────────────────────────────────────────────────────────────────────────────────

/** The net class target of a net: { value, key, tolerance_pct, source, net_class } or null. */
function targetOf(board, net, kind, o, warnings) {
  const key = kind === 'differential' ? 'Zdiff' : 'Z0';
  if (o.target != null) {
    const t = typeof o.target === 'number' ? { value: o.target } : o.target;
    return { value: t.value, key, tolerance_pct: t.tolerance_pct ?? o.tolerancePct, source: 'option', net_class: null };
  }
  const cls = (board.nets ?? []).find((x) => x.name === net)?.net_class;
  const nc = (board.net_classes ?? []).find((c) => c.name === cls);
  const imp = nc?.impedance;
  if (!imp) return null;
  if ((imp.kind === 'differential') !== (kind === 'differential')) {
    warnings.push(`net class ${nc.name}: its ${imp.target} Ω target is ${imp.kind}; applied to the ${kind === 'differential' ? 'pair' : 'net'} as ${key}`);
  }
  return { value: imp.target, key, tolerance_pct: imp.tolerance_pct ?? o.tolerancePct, source: 'net_class', net_class: nc.name };
}

/**
 * Impedance along a net's route: a boarddd/impedance@1 document (see docs/impedance.md "Along a route").
 * @param {object} board  boarddd/board@1 (its stackup; nets and net classes for the target)
 * @param {object} copper  boarddd/copper@1 of the same board
 * @param {string|string[]} net  a net, or [p, n] for a differential pair
 * @param {object} [options]  see ROUTE_DEFAULTS; plus `target` (Ω or {value, tolerance_pct}), `groundNets`
 *   (names to treat as ground besides zones, solid planes and GND-like names) and `overrides`
 *   ({net: {...}, tracks: {id: {...}}} with structure, refTop, refBottom (layer or false), coplanar: false), and
 *   `cache` (a Map shared between calls: solved cross-sections by geometry key, e.g. for every net of a board),
 *   and `onProgress({phase: 'route' | 'solve', done, total})` (JS only; the UI's progress bar). With `frequency`
 *   (Hz) every section with a Z also gets `loss` (dB/mm and dB there, conductor and dielectric parts, and the
 *   Z and dB/mm sweep over `frequencies`) and the summary the route's loss (loss.js; the stackup's Er/Df,
 *   roughness and conductivity, `lossOptions` over them)
 */
export function analyzeNet(board, copper, net, options = {}) {
  const t0 = now();
  const o = { ...ROUTE_DEFAULTS, ...options, fieldOptions: { ...ROUTE_DEFAULTS.fieldOptions, ...options.fieldOptions },
    shortFieldOptions: { ...ROUTE_DEFAULTS.shortFieldOptions, ...options.shortFieldOptions } };
  const nets = Array.isArray(net) ? net : [net];
  if (nets.length < 1 || nets.length > 2) throw new RangeError('analyzeNet takes a net or a [p, n] pair');
  const kind = nets.length === 2 ? 'differential' : 'single';
  const stack = stackInfo(board.stackup);
  if (!stack.copper.length) throw new RangeError('the board has no stackup copper layers');
  const warnings = [];
  const indexes = new Map();
  const planeLayers = new Set((copper.planes ?? []).filter((p) => p.solid).map((p) => p.layer));
  const groundNets = new Set([...(copper.planes ?? []).filter((p) => p.solid).map((p) => p.net),
    ...(copper.nets ?? []).filter((x) => GROUND_NAME.test(x)), ...(o.groundNets ?? [])]);
  for (const x of nets) groundNets.delete(x);
  const ctx = { o, board, stack, groundNets, planeLayers, index: (l) => { if (!indexes.has(l)) indexes.set(l, layerIndex(copper, l)); return indexes.get(l); } };
  const cache = o.cache instanceof Map ? o.cache : new Map();
  let solves = 0, hits = 0, stations = 0;
  const sections = [];
  const routes = nets.map((x) => netRoute(copper, x));
  if (!routes[0].length) throw new RangeError(`net ${nets[0]} has no tracks`);
  const progress = typeof o.onProgress === 'function' ? o.onProgress : null;
  const totalLen = routes.reduce((t, r) => t + r.reduce((u, e) => u + e.path.len, 0), 0) || 1;
  let doneLen = 0;
  nets.forEach((x, k) => {
    for (const e of routes[k]) {
      doneLen += e.path.len;
      progress?.({ phase: 'route', done: doneLen, total: totalLen });
      if (!stack.order.has(e.track.layer)) { warnings.push(`${e.track.layer} is not in the stackup: track skipped`); continue; }
      const nb = Math.max(1, Math.ceil(e.path.len / o.step - 1e-9));
      const ov = o.overrides?.tracks?.[e.track.id] ?? o.overrides?.net ?? null;
      for (let b = 0; b < nb; b++) {
        const f0 = (e.path.len * b) / nb, f1 = (e.path.len * (b + 1)) / nb, fm = (f0 + f1) / 2;
        const g = (f) => (e.forward ? f : e.path.len - f);
        const mid = e.path.at(g(fm));
        const d = e.forward ? mid.d : [-mid.d[0], -mid.d[1]];
        const st = { net: x, partner: kind === 'differential' ? nets[1 - k] : null, layer: e.track.layer, width: e.track.width, p: mid.p, d };
        stations += 1;
        let env = station(ctx, st);
        if (k === 1 && env.partner) continue;        // the coupled stretches are the first net's sections
        if (kind === 'differential' && !env.partner) env.flags.push('uncoupled');
        env = overridden(env, ov, stack, st.layer);
        const key = geometryKey(st.layer, env, o.solver);
        const prev = sections[sections.length - 1];
        const s0 = R6(e.s0 + f0), s1 = R6(e.s0 + f1);
        const pA = e.path.at(g(f0)).p, pB = e.path.at(g(f1)).p;
        if (prev && prev._key === key && prev.net === x && prev._run === e.run && Math.abs(prev.s1 - s0) < 1e-6 && prev.geometry.width === e.track.width) {
          prev.s1 = s1; prev.end = [R6(pB[0]), R6(pB[1])]; prev.length = R6(prev.s1 - prev.s0);
          if (e.track.id && !prev.tracks.includes(e.track.id)) prev.tracks.push(e.track.id);
          continue;
        }
        const tr = env.layout.traces;
        const nt = tr.find((t) => t.net === 'n');
        const gL = env.coplanar[0], gR = env.coplanar[1];
        sections.push({
          _key: key, _run: e.run, _env: env,
          net: x, start: [R6(pA[0]), R6(pA[1])], end: [R6(pB[0]), R6(pB[1])], s0, s1, length: R6(s1 - s0),
          layer: st.layer, structure: env.structure, kind: env.kind,
          geometry: {
            width: e.track.width, thickness: stack.copper[stack.order.get(st.layer)].t,
            partner_width: nt ? R6(nt.x1 - nt.x0) : null,
            gap: nt ? R6(nt.x0 > 0 ? nt.x0 - e.track.width / 2 : -e.track.width / 2 - nt.x1) : null,
            coplanar_gap: [gL, gR],
            h_top: env.refs.top?.h ?? null, h_bottom: env.refs.bottom?.h ?? null,
          },
          z: null,
          refs: [env.refs.top, env.refs.bottom].filter(Boolean).map((r) => ({ side: r.side, layer: r.layer, net: r.net, h: r.h, extent: r.extent, skipped: r.skipped })),
          flags: [...new Set(env.flags)].sort(),
          tracks: e.track.id ? [e.track.id] : [],
          loss: null,
        });
      }
    }
  });
  const tSolve = now();
  // how much route each cross-section covers decides its accuracy (fieldOptions or shortFieldOptions)
  const covers = new Map();
  for (const sec of sections) covers.set(sec._key, (covers.get(sec._key) ?? 0) + sec.length);
  const wantLoss = o.frequency != null;
  if (wantLoss && !(o.frequency > 0)) throw new RangeError(`frequency must be > 0 Hz (got ${o.frequency})`);
  const fs = wantLoss ? [...new Set([...(o.frequencies ?? LOSS_SWEEP), o.frequency])].sort((a, b) => a - b) : null;
  const fi = wantLoss ? fs.indexOf(o.frequency) : -1;
  const sweeps = new Map();
  sections.forEach((sec, i) => {
    progress?.({ phase: 'solve', done: i, total: sections.length });
    const fine = covers.get(sec._key) >= o.shortLength - 1e-9;
    const had = cache.get(sec._key);
    if (!had || (fine && !had.fine) || (wantLoss && had.z && !had.z.lossSource)) {
      solves += 1;
      let z = null;
      try { z = solve(ctx, sec.layer, sec._env, sec.geometry.width, fine ? o.fieldOptions : o.shortFieldOptions); }
      catch (err) { z = null; sec.flags.push('solver_error'); warnings.push(`${sec.net} at s=${sec.s0}: ${err.message}`); }
      cache.set(sec._key, { z, fine });
    } else hits += 1;
    const z = cache.get(sec._key).z;
    if (z) {
      for (const w of z.warnings ?? []) if (!warnings.includes(w)) warnings.push(w);
      sec.z = { Z0: z.Z0, Zdiff: z.Zdiff, Zcommon: z.Zcommon, Zodd: z.Zodd, Zeven: z.Zeven, eps_eff: z.eps_eff, error_pct: z.error_pct, solver: z.solver, model: z.model };
      if (kind === 'differential' && sec.kind === 'single') sec.z.Zdiff = R6(2 * z.Z0);   // uncoupled: two single lines
      if (wantLoss) {
        if (!sweeps.has(sec._key)) {
          let sw = null;
          try { sw = lossOfSolve(z, fs, o.lossOptions ?? {}); } catch (err) { warnings.push(`${sec.net} at s=${sec.s0}: loss: ${err.message}`); }
          sweeps.set(sec._key, sw);
        }
        const sw = sweeps.get(sec._key);
        if (sw) {
          // An uncoupled stretch of a pair: two independent lines, Zdiff = 2 Z0 and the same loss per line.
          const zk = kind === 'differential' && sec.kind === 'single' ? sw.z.map((v) => 2 * v) : sw.z;
          sec.loss = { frequency: o.frequency, z: S9(zk[fi]), db_per_mm: S9(sw.db_per_mm[fi]), db: S9(sw.db_per_mm[fi] * sec.length),
            db_per_mm_conductor: S9(sw.db_per_mm_c[fi]), db_per_mm_dielectric: S9(sw.db_per_mm_d[fi]),
            sweep: { frequency: fs, z: zk.map(S9), db_per_mm: sw.db_per_mm.map(S9) } };
        }
      }
    }
  });
  progress?.({ phase: 'solve', done: sections.length, total: sections.length });
  const target = targetOf(board, nets[0], kind, o, warnings);
  const summary = summarise(sections.filter((s) => s.net === nets[0]), target, kind);
  if (wantLoss) summary.loss = lossSummary(sections.filter((s) => s.net === nets[0]), o.frequency, fs);
  const discontinuities = discontinuitiesOf(sections);
  for (const s of sections) { delete s._key; delete s._run; delete s._env; }
  const solveMs = now() - tSolve;
  const ms = now() - t0;
  return {
    schema: IMPEDANCE_SCHEMA_ID, board: board.name, nets, kind, solver: o.solver, target, sections, summary, discontinuities,
    options: { step: o.step, solver: o.solver, tolerance_pct: o.tolerancePct, window: o.window, coplanar_window: o.coplanarWindow,
      pair_window: o.pairWindow, parallel_deg: o.parallelDeg, ref_margin: o.refMargin, neighbours: o.neighbours, no_plane: o.noPlane,
      short_length: o.shortLength, frequency: o.frequency ?? null },
    warnings,
    timing: { ms: Math.round(ms * 10) / 10, solve_ms: Math.round(solveMs * 10) / 10, stations, solves, cache_hits: hits },
  };
}

/** Length-weighted summary of the sections' impedance against the target. */
function summarise(secs, target, kind) {
  const key = kind === 'differential' ? 'Zdiff' : 'Z0';
  let length = 0, withZ = 0, sum = 0, out = 0, min = null, max = null;
  for (const s of secs) {
    length += s.length;
    const v = s.z?.[key];
    if (v == null) continue;
    withZ += s.length; sum += v * s.length;
    min = min == null ? v : Math.min(min, v); max = max == null ? v : Math.max(max, v);
    if (target && Math.abs(v - target.value) > (target.value * target.tolerance_pct) / 100) out += s.length;
  }
  return {
    key, length: R6(length), length_with_z: R6(withZ), z_weighted: withZ > 0 ? R6(sum / withZ) : null, z_min: min, z_max: max,
    out_of_tolerance_length: target ? R6(out) : null,
    out_of_tolerance_pct: target && length > 0 ? R6((100 * (out + (length - withZ))) / length) : null,
    within: target ? out === 0 && withZ === length : null,
    loss: null,
  };
}

/**
 * The route's loss at the frequency and over the sweep: the sum over the sections that have one (sections with
 * no Z have no loss and are left out: `length` says how much of the route is counted).
 */
function lossSummary(secs, f, fs) {
  let length = 0, db = 0, dbc = 0, dbd = 0;
  const sweep = fs.map(() => 0);
  for (const s of secs) {
    if (!s.loss) continue;
    length += s.length; db += s.loss.db; dbc += s.loss.db_per_mm_conductor * s.length; dbd += s.loss.db_per_mm_dielectric * s.length;
    s.loss.sweep.db_per_mm.forEach((v, k) => { sweep[k] += v * s.length; });
  }
  return { frequency: f, length: R6(length), db: S9(db), db_per_mm: length > 0 ? S9(db / length) : null,
    db_conductor: S9(dbc), db_dielectric: S9(dbd), sweep: { frequency: fs, db: sweep.map(S9) } };
}

/** Where the route changes: vias, reference changes, plane gaps, width changes, coupling, no reference. */
function discontinuitiesOf(sections) {
  const out = [];
  const add = (type, s, detail) => out.push({ type, at: s.start, s: s.s0, layer: s.layer, net: s.net, detail });
  sections.forEach((s, i) => {
    const p = sections[i - 1];
    const cont = p && p.net === s.net && Math.abs(p.s1 - s.s0) < 1e-6 && p.end[0] === s.start[0] && p.end[1] === s.start[1];
    const refsOf = (x) => x.refs.map((r) => `${r.side}:${r.layer}:${r.net}`).join(' ');
    if (cont && p.layer !== s.layer) add('via', s, `${p.layer} → ${s.layer}`);
    else if (cont && refsOf(p) !== refsOf(s)) add('ref_change', s, `${refsOf(p) || 'none'} → ${refsOf(s) || 'none'}`);
    if (cont && p.geometry.width !== s.geometry.width) add('width_change', s, `${p.geometry.width} → ${s.geometry.width} mm`);
    for (const f of ['plane_gap', 'no_ref', 'uncoupled', 'ref_edge']) {
      if (s.flags.includes(f) && !(cont && p.flags.includes(f))) add(f, s, s.refs.map((r) => r.skipped.length ? `skipped ${r.skipped.join(', ')}` : '').filter(Boolean).join('; '));
    }
  });
  return out;
}
