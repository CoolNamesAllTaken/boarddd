// boarddd/impedance/ui: picking copper on a view2d stage. A view2d hit index (createHitIndex) over a
// boarddd/copper@1 document's tracks, arcs, pads, vias and zones, each shape carrying {kind, net, layer, width,
// track}; and the differential partner of a net (board@1 Net.pair, else KiCad's naming rule).
import { createHitIndex, segmentShape, circleShape, polygonShape } from '../../view2d/hit.js';

/** Points along an arc track (start, mid, end), every ~5°. */
export function arcPoints(t) {
  const [ax, ay] = t.start, [mx, my] = t.mid, [bx, by] = t.end;
  const d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my));
  if (Math.abs(d) < 1e-12) return [t.start, t.end];
  const ux = ((ax * ax + ay * ay) * (my - by) + (mx * mx + my * my) * (by - ay) + (bx * bx + by * by) * (ay - my)) / d;
  const uy = ((ax * ax + ay * ay) * (bx - mx) + (mx * mx + my * my) * (ax - bx) + (bx * bx + by * by) * (mx - ax)) / d;
  const r = Math.hypot(ax - ux, ay - uy);
  const a0 = Math.atan2(ay - uy, ax - ux), am = Math.atan2(my - uy, mx - ux), a1 = Math.atan2(by - uy, bx - ux);
  const ccw = (a, b) => ((b - a) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
  let sweep = ccw(a0, a1);
  if (ccw(a0, am) > sweep) sweep -= 2 * Math.PI;
  const n = Math.max(2, Math.ceil(Math.abs(sweep) / (Math.PI / 36)));
  return Array.from({ length: n + 1 }, (_, i) => [ux + r * Math.cos(a0 + (sweep * i) / n), uy + r * Math.sin(a0 + (sweep * i) / n)]);
}

/** A track's centre line: [start, end], or an arc's points. */
export const trackPath = (t) => (t.mid ? arcPoints(t) : [t.start, t.end]);

/**
 * A view2d hit index over the copper of `layers` (default every copper layer). Each shape's `data` is
 * `{ kind: 'track' | 'pad' | 'via' | 'zone', net, layer, width, track }` (`track`: the copper@1 track index).
 * Zones count as background: a track or pad over a pour wins (view2d's smallest-shape rule).
 */
export function copperHitIndex(copper, { layers = null } = {}) {
  const want = layers ? new Set(layers) : null;
  const on = (l) => !want || want.has(l);
  const shapes = [];
  (copper.tracks ?? []).forEach((t, i) => {
    if (!on(t.layer)) return;
    const pts = trackPath(t);
    for (let k = 0; k + 1 < pts.length; k++) {
      shapes.push(segmentShape(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1], t.width, { kind: 'track', net: t.net, layer: t.layer, width: t.width, track: i }));
    }
  });
  const order = new Map((copper.layers ?? []).map((l, i) => [l, i]));
  for (const v of copper.vias ?? []) {
    const lo = order.get(v.span[0]), hi = order.get(v.span[1]);
    const vl = (copper.layers ?? []).slice(lo, hi + 1).filter(on);
    if (vl.length) shapes.push(circleShape(v.at[0], v.at[1], v.diameter, { kind: 'via', net: v.net, layer: vl[0], layers: vl, width: v.diameter, track: null }));
  }
  for (const p of copper.pads ?? []) {
    const pl = p.layers.filter(on);
    if (!pl.length) continue;
    for (const ring of p.polygons ?? []) {
      if (ring.length >= 3) shapes.push(polygonShape(ring, { kind: 'pad', net: p.net, layer: pl[0], layers: pl, width: null, track: null, ref: p.ref, number: p.number }));
    }
  }
  for (const z of copper.zones ?? []) {
    if (!on(z.layer)) continue;
    for (const f of z.fill) {
      // the outline only (holes are where other copper is: a pick there finds that copper first)
      shapes.push(polygonShape(f.outline, { kind: 'zone', net: z.net, layer: z.layer, width: null, track: null }));
    }
  }
  return createHitIndex(shapes);
}

const PAIR = /^(.*?)(\+|-|_P|_N|P|N)$/;
const PARTNER = { '+': '-', '-': '+', _P: '_N', _N: '_P', P: 'N', N: 'P' };

/**
 * The differential partner of a net: board@1 `Net.pair` when the board has nets, else KiCad's rule (the same
 * name with a '+'/'-', '_P'/'_N' or 'P'/'N' suffix swapped) among `names`. null when it has none.
 */
export function pairOf(net, { board = null, names = [] } = {}) {
  const fromBoard = board?.nets?.find((n) => n.name === net)?.pair;
  if (fromBoard) return fromBoard;
  const m = PAIR.exec(net);
  if (!m || !m[1]) return null;
  const partner = m[1] + PARTNER[m[2]];
  return names.includes(partner) ? partner : null;
}

/** [p, n] in the order KiCad pairs them (the '+' / 'P' net first). */
export function pairOrder(a, b) {
  const pos = (n) => /(\+|_P|P)$/.test(n);
  return pos(a) || !pos(b) ? [a, b] : [b, a];
}
