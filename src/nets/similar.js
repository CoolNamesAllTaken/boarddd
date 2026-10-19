// boarddd/nets: tracks like a picked one. Every net with tracks of the same width on the same layer, or, for a pair,
// every pair with that width and the same gap. The same rules as python's boarddd.nets; see docs/nets.md.
import { pairsAmong } from './patterns.js';

/** Width tolerance, mm: the larger of 1 µm and 0.5 %. */
export const widthTolerance = (w) => Math.max(0.001, 0.005 * w);
/** Gap tolerance, mm: the larger of 2 µm and 2 %. */
export const gapTolerance = (g) => Math.max(0.002, 0.02 * g);

/** A track's straight pieces: the segment, or an arc as its two chords through `mid`. */
function pieces(t) {
  return t.mid ? [[t.start, t.mid], [t.mid, t.end]] : [[t.start, t.end]];
}

const len = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);

function trackLength(t) {
  return pieces(t).reduce((s, [a, b]) => s + len(a, b), 0);
}

/** Tracks of `nets` on `layer` (all widths, or those within tolerance of `width`). */
function tracksOf(copper, layer, nets, width = null) {
  const want = new Set(nets);
  return (copper.tracks ?? []).filter((t) => t.layer === layer && want.has(t.net)
    && (width == null || Math.abs(t.width - width) <= widthTolerance(width)));
}

/**
 * The edge-to-edge gap between two nets' tracks on a layer: where their pieces run parallel (within 10°) and
 * overlap, closer than max(0.5 mm, 4 w) centre to centre. `{ gap, length }` (mm; the gap most of the coupled
 * length has, to 1 µm), or null when they never run side by side.
 */
export function pairGap(copper, a, b, { layer, width = null } = {}) {
  const A = tracksOf(copper, layer, [a], width), B = tracksOf(copper, layer, [b], width);
  const buckets = new Map();
  for (const ta of A) {
    for (const [p0, p1] of pieces(ta)) {
      const la = len(p0, p1);
      if (la < 1e-6) continue;
      const ux = (p1[0] - p0[0]) / la, uy = (p1[1] - p0[1]) / la;
      for (const tb of B) {
        const w = (ta.width + tb.width) / 2;
        const window = Math.max(0.5, 4 * w);
        for (const [q0, q1] of pieces(tb)) {
          const lb = len(q0, q1);
          if (lb < 1e-6) continue;
          if (Math.abs(((q1[0] - q0[0]) * ux + (q1[1] - q0[1]) * uy) / lb) < 0.985) continue;
          const s0 = (q0[0] - p0[0]) * ux + (q0[1] - p0[1]) * uy;
          const s1 = (q1[0] - p0[0]) * ux + (q1[1] - p0[1]) * uy;
          const overlap = Math.min(la, Math.max(s0, s1)) - Math.max(0, Math.min(s0, s1));
          if (overlap <= 1e-6) continue;
          const d0 = (q0[1] - p0[1]) * ux - (q0[0] - p0[0]) * uy;
          const d1 = (q1[1] - p0[1]) * ux - (q1[0] - p0[0]) * uy;
          const d = Math.abs((d0 + d1) / 2);
          if (d > window) continue;
          const gap = d - w;
          if (gap <= 0) continue;
          const key = Math.round(gap * 1000);
          const was = buckets.get(key) ?? { length: 0, sum: 0 };
          was.length += overlap;
          was.sum += gap * overlap;
          buckets.set(key, was);
        }
      }
    }
  }
  if (!buckets.size) return null;
  let bestKey = null;
  for (const [k, v] of buckets) {
    const best = bestKey == null ? null : buckets.get(bestKey);
    if (!best || v.length > best.length + 1e-9 || (Math.abs(v.length - best.length) <= 1e-9 && k < bestKey)) bestKey = k;
  }
  const best = buckets.get(bestKey);
  const total = [...buckets.values()].reduce((s, v) => s + v.length, 0);
  return { gap: round(best.sum / best.length, 4), length: round(total, 3) };
}

const round = (v, n) => Math.round(v * 10 ** n) / 10 ** n;

/**
 * Tracks like the picked one. `seed`: `{ layer, width, nets }`, `nets` one net (single) or a pair [p, n]; `width`
 * defaults to the seed net's longest width on the layer. Returns `{ kind, layer, width, gap, nets, pairs }`:
 *   nets   [{ net, length, tracks, pair, plane }] every net with tracks of that width on that layer, longest first
 *          (`pair`: its partner also matched; `plane`: a solid-plane net, which a caller leaves out by default);
 *   pairs  for a pair seed, [{ nets: [p, n], gap, length }] the pairs at that width whose gap is the seed's (within
 *          gapTolerance), the seed first; `gap` is the seed's.
 * Pairs come from board@1 Net.pair or the naming rule, else from the geometry (a net's parallel neighbour).
 */
export function similarTracks(copper, seed, { board = null } = {}) {
  const layer = seed.layer;
  const seedNets = seed.nets ?? [];
  let width = seed.width ?? null;
  if (width == null) {
    const byWidth = new Map();
    for (const t of tracksOf(copper, layer, seedNets.slice(0, 1))) byWidth.set(t.width, (byWidth.get(t.width) ?? 0) + trackLength(t));
    width = [...byWidth].sort((x, y) => y[1] - x[1] || x[0] - y[0])[0]?.[0] ?? null;
  }
  const out = { kind: seedNets.length === 2 ? 'differential' : 'single', layer, width, gap: null, nets: [], pairs: [] };
  if (width == null) return out;
  const tol = widthTolerance(width);
  const acc = new Map();
  for (const t of copper.tracks ?? []) {
    if (t.layer !== layer || !t.net || Math.abs(t.width - width) > tol) continue;
    const was = acc.get(t.net) ?? { net: t.net, length: 0, tracks: 0 };
    was.length += trackLength(t);
    was.tracks += 1;
    acc.set(t.net, was);
  }
  const planes = new Set((copper.planes ?? []).filter((p) => p.solid).map((p) => p.net));
  const names = [...acc.keys()];
  const partner = new Map();
  for (const [p, n] of pairsAmong(names, { board })) { partner.set(p, n); partner.set(n, p); }
  out.nets = [...acc.values()]
    .map((v) => ({ net: v.net, length: round(v.length, 3), tracks: v.tracks, pair: partner.get(v.net) ?? null, plane: planes.has(v.net) }))
    .sort((x, y) => y.length - x.length || (x.net < y.net ? -1 : x.net > y.net ? 1 : 0));
  if (out.kind === 'single') return out;

  const g0 = pairGap(copper, seedNets[0], seedNets[1], { layer, width });
  if (!g0) return out;
  out.gap = g0.gap;
  const gtol = gapTolerance(g0.gap);
  const candidates = [];
  const used = new Set();
  const seedKey = [...seedNets].sort().join('\u0000');
  const consider = (p, n) => {
    const g = pairGap(copper, p, n, { layer, width });
    if (!g || Math.abs(g.gap - g0.gap) > gtol) return false;
    candidates.push({ nets: [p, n], gap: g.gap, length: g.length });
    used.add(p).add(n);
    return true;
  };
  for (const [p, n] of pairsAmong(names, { board })) consider(p, n);
  // nets with no named partner: the other unnamed net they run beside at the gap
  const lone = out.nets.map((v) => v.net).filter((n) => !used.has(n) && !partner.has(n) && !planes.has(n));
  for (let i = 0; i < lone.length; i++) {
    if (used.has(lone[i])) continue;
    for (let j = i + 1; j < lone.length; j++) {
      if (used.has(lone[j])) continue;
      if (consider(lone[i], lone[j])) break;
    }
  }
  candidates.sort((x, y) => (([...y.nets].sort().join('\u0000') === seedKey) - ([...x.nets].sort().join('\u0000') === seedKey))
    || y.length - x.length || (x.nets[0] < y.nets[0] ? -1 : x.nets[0] > y.nets[0] ? 1 : 0));
  out.pairs = candidates;
  return out;
}

/**
 * The net that runs beside `net` as a pair partner would: parallel at one gap (pairGap) for at least `share` of its
 * routed length, on `layer` or (default) summed over every layer it is routed on. For boards whose nets carry no
 * names to pair them by (plain Gerbers: N1, N2...). `{ net, gap, length, share, layer }` (`gap` and `layer`: where
 * they run together longest), or null.
 */
export function parallelPartner(copper, net, { layer = null, share = 0.5, exclude = [] } = {}) {
  const mine = (copper.tracks ?? []).filter((t) => t.net === net && (layer == null || t.layer === layer));
  const routed = mine.reduce((s, t) => s + trackLength(t), 0);
  if (routed <= 0) return null;
  const layers = [...new Set(mine.map((t) => t.layer))];
  const skip = new Set([net, ...exclude]);
  const planes = new Set((copper.planes ?? []).filter((p) => p.solid).map((p) => p.net));
  const near = new Map(); // layer -> candidate nets with a track within the pair window of ours
  for (const l of layers) {
    const own = mine.filter((t) => t.layer === l);
    const reach = Math.max(...own.map((t) => Math.max(0.5, 4 * t.width)));
    const box = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
    for (const t of own) for (const [p, q] of pieces(t)) for (const [x, y] of [p, q]) {
      box.minX = Math.min(box.minX, x); box.maxX = Math.max(box.maxX, x); box.minY = Math.min(box.minY, y); box.maxY = Math.max(box.maxY, y);
    }
    const found = new Set();
    for (const t of copper.tracks ?? []) {
      if (t.layer !== l || !t.net || skip.has(t.net) || planes.has(t.net)) continue;
      const [x0, y0] = t.start, [x1, y1] = t.end;
      if (Math.max(x0, x1) < box.minX - reach || Math.min(x0, x1) > box.maxX + reach
        || Math.max(y0, y1) < box.minY - reach || Math.min(y0, y1) > box.maxY + reach) continue;
      found.add(t.net);
    }
    near.set(l, found);
  }
  const by = new Map(); // other net -> { length, best: { layer, gap, length } }
  for (const [l, nets] of near) {
    for (const other of nets) {
      const g = pairGap(copper, net, other, { layer: l });
      if (!g) continue;
      const was = by.get(other) ?? { length: 0, best: null };
      was.length += g.length;
      if (!was.best || g.length > was.best.length) was.best = { layer: l, gap: g.gap, length: g.length };
      by.set(other, was);
    }
  }
  let best = null;
  for (const [other, v] of by) {
    const s = v.length / routed;
    if (s >= share && (!best || v.length > best.length)) best = { net: other, gap: v.best.gap, length: round(v.length, 3), share: round(s, 3), layer: v.best.layer };
  }
  return best;
}
