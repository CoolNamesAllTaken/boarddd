// boarddd/impedance/ui: one route through a selection of nets and pairs ("units"), for the Z-along-the-route plot.
// Units follow each other through the parts between them (a series resistor, AC caps, a common-mode choke: every
// net of the part is in the selection or a supply), each unit's route oriented to continue from the part, and its
// impedance@1 sections placed at their distance along the whole path:
//   J2 ─── USB_D+ (F.Cu) ──◎── (B.Cu) ─── R5 ─── USB_D+_R ─── U1
//   0                                     18.2                 31.4 mm
// Board ↔ path: pathAt (a path position → its section and the board point, the trace's direction there) and
// pathFromBoard (a point on a selected trace → its path position). No DOM: the node tests run it.
import { netRoute } from '../route.js';

const GROUND = /^(?:[ADPS]?GND|VSS|GROUND|EARTH|CHASSIS)(?:[_\-.].*)?$/i;
const SUPPLY = /^(?:[+-]?\d+V\d*|V(?:CC|DD|BUS|BAT|IN|SYS)|[+-]?\d*V\d+)(?:[_\-.].*)?$/i;
const shortName = (n) => String(n).split('/').filter(Boolean).pop() ?? String(n);
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

/** Nets a series part may also have without being a branch: grounds, supplies, solid planes. */
export function supplyNets(copper) {
  const out = new Set((copper.planes ?? []).filter((p) => p.solid).map((p) => p.net));
  for (const n of copper.nets ?? []) if (n && (GROUND.test(shortName(n)) || SUPPLY.test(shortName(n)))) out.add(n);
  return out;
}

/**
 * The parts between units: [{ a, b, refs, pads }] (unit indexes, a < b; `pads` the parts' pads on either unit's
 * nets). A part counts when it has pads on both units and no net outside the selection but supplies.
 */
export function bridges(copper, units) {
  const unitOf = new Map();
  units.forEach((u, i) => { for (const n of u) unitOf.set(n, i); });
  const supplies = supplyNets(copper);
  const byRef = new Map();
  for (const p of copper.pads ?? []) {
    if (!p.ref) continue;
    if (!byRef.has(p.ref)) byRef.set(p.ref, []);
    byRef.get(p.ref).push(p);
  }
  const out = new Map();
  for (const [ref, pads] of byRef) {
    const nets = new Set(pads.map((p) => p.net).filter(Boolean));
    if ([...nets].some((n) => !unitOf.has(n) && !supplies.has(n))) continue;
    const us = [...new Set([...nets].filter((n) => unitOf.has(n)).map((n) => unitOf.get(n)))].sort((x, y) => x - y);
    for (let i = 0; i < us.length; i++) {
      for (let j = i + 1; j < us.length; j++) {
        const k = `${us[i]} ${us[j]}`;
        if (!out.has(k)) out.set(k, { a: us[i], b: us[j], refs: [], pads: [] });
        const e = out.get(k);
        e.refs.push(ref);
        e.pads.push(...pads.filter((p) => unitOf.get(p.net) === us[i] || unitOf.get(p.net) === us[j]));
      }
    }
  }
  return [...out.values()];
}

/**
 * The units in path order: [{ unit (index), via (the bridge to the one before, or null: not connected) }]. Starts at
 * an end (a unit with one neighbour, the first picked of those), follows the parts, and continues from the next
 * end when the chain stops (a `via: null` boundary).
 */
export function chainOrder(units, links) {
  const nb = units.map(() => []);
  for (const l of links) { nb[l.a].push(l); nb[l.b].push(l); }
  const done = new Set();
  const out = [];
  while (done.size < units.length) {
    const left = units.map((_, i) => i).filter((i) => !done.has(i));
    const deg = (i) => nb[i].filter((l) => !done.has(l.a === i ? l.b : l.a)).length;
    let at = left.find((i) => deg(i) === 1) ?? left.find((i) => deg(i) === 0) ?? left[0];
    let via = null;
    for (;;) {
      done.add(at);
      out.push({ unit: at, via });
      const next = nb[at].filter((l) => !done.has(l.a === at ? l.b : l.a)).sort((x, y) => Math.min(x.a, x.b) - Math.min(y.a, y.b))[0];
      if (!next) break;
      via = next;
      at = next.a === at ? next.b : next.a;
    }
  }
  return out;
}

/** A route's two ends: where it starts (s = 0) and where its walk ends (the largest s). */
function routeEnds(route) {
  if (!route.length) return null;
  const first = route.reduce((a, b) => (b.s0 < a.s0 ? b : a));
  const last = route.reduce((a, b) => (b.s1 > a.s1 ? b : a));
  return { start: first.path.at(first.forward ? 0 : first.path.len).p, end: last.path.at(last.forward ? last.path.len : 0).p, length: last.s1 };
}

const nearestPad = (pads, p) => Math.min(...pads.map((q) => dist(q.at, p)), Infinity);

/** The part whose pad (on one of `nets`) is nearest `p`, within `max` mm: its ref, or null. */
function partAt(copper, nets, p, max = 1.5) {
  let best = null, bd = max;
  for (const q of copper.pads ?? []) {
    if (!q.ref || !nets.includes(q.net)) continue;
    const d = dist(q.at, p);
    if (d < bd) { bd = d; best = q.ref; }
  }
  return best;
}

/**
 * One path through `units` ([[net] | [p, n]], the selection) from their analyses (`docs`: impedance@1 per unit,
 * null while one runs). Options: `targets` (per unit: { value, tolerance_pct } | null). Returns
 * { length, units: [{ nets, kind, key, s0, s1, reversed, target, doc }], sections: [{ s0, s1, z, unit, section }],
 *   marks: [{ type: 'part' | 'gap' | 'end' | discontinuity type, s, label, refs?, unit }] }.
 */
export function routePath(copper, units, docs = [], { targets = [] } = {}) {
  const links = bridges(copper, units);
  const order = chainOrder(units, links);
  const routes = units.map((u) => netRoute(copper, u[0]));
  const out = { length: 0, units: [], sections: [], marks: [] };
  let s = 0;
  order.forEach(({ unit: i, via }, k) => {
    const u = units[i];
    const ends = routeEnds(routes[i]) ?? { start: [0, 0], end: [0, 0], length: 0 };
    // continue from the part before; the first unit faces the part after it
    let reversed = false;
    if (via) reversed = nearestPad(via.pads.filter((p) => u.includes(p.net)), ends.end) < nearestPad(via.pads.filter((p) => u.includes(p.net)), ends.start);
    else {
      const after = order[k + 1]?.via;
      if (after) reversed = nearestPad(after.pads.filter((p) => u.includes(p.net)), ends.start) < nearestPad(after.pads.filter((p) => u.includes(p.net)), ends.end);
    }
    const L = ends.length;
    const doc = docs[i] ?? null;
    const key = doc?.summary?.key ?? (u.length === 2 ? 'Zdiff' : 'Z0');
    const at = (x) => s + (reversed ? L - x : x);
    if (k === 0 || !via) {
      if (k > 0) out.marks.push({ type: 'gap', s, label: '⋯', unit: i });
      const ref = partAt(copper, u, reversed ? ends.end : ends.start);
      if (ref) out.marks.push({ type: 'end', s, label: ref, refs: [ref], unit: i });
    } else out.marks.push({ type: 'part', s, label: via.refs.join(' '), refs: [...via.refs], unit: i });
    out.units.push({ nets: [...u], kind: u.length === 2 ? 'differential' : 'single', key, s0: s, s1: s + L, reversed, length: L,
      start: reversed ? ends.end : ends.start, end: reversed ? ends.start : ends.end, target: targets[i] ?? doc?.target ?? null, doc, index: i });
    if (doc) {
      const secs = doc.sections.filter((x) => x.net === u[0]).map((x) => {
        const a = at(x.s0), b = at(x.s1);
        const z = x.z ? (key === 'Zdiff' ? x.z.Zdiff : x.z.Z0) ?? null : null;
        return { s0: Math.min(a, b), s1: Math.max(a, b), z, unit: i, section: x };
      });
      out.sections.push(...secs.sort((a, b) => a.s0 - b.s0));
      for (const d of doc.discontinuities ?? []) {
        if (d.net !== u[0] || d.type === 'width_change' || d.type === 'uncoupled' || d.type === 'ref_edge') continue;
        // a detail reads 'from → to' the way analyzeNet walked: the path's way when the unit is turned round
        const detail = reversed && /→/.test(d.detail ?? '') ? d.detail.split(/\s*→\s*/).reverse().join(' → ') : d.detail ?? '';
        out.marks.push({ type: d.type, s: at(d.s), label: d.type === 'via' ? viaLabel(detail) : '', detail, layer: d.layer, unit: i });
      }
    }
    s += L;
    const after = order[k + 1];
    if (!after || !after.via) {
      const ref = partAt(copper, u, reversed ? ends.start : ends.end);
      if (ref) out.marks.push({ type: 'end', s, label: ref, refs: [ref], unit: i });
    }
  });
  out.length = s;
  out.marks.sort((a, b) => a.s - b.s);
  out.routes = routes;
  return out;
}

/** 'F.Cu → In2.Cu' → 'F→In2'. */
const viaLabel = (detail) => String(detail ?? '').replace(/\.Cu/g, '').replace(/\s*→\s*/, '→');

/** A route's point and direction at s ([x, y], unit vector, the walk's way), on `tracks` (ids) when given. */
export function routeAt(route, s, tracks = null) {
  const want = tracks?.length ? new Set(tracks) : null;
  const on = (x) => x.s0 - 1e-9 <= s && s <= x.s1 + 1e-9;
  const e = route.find((x) => on(x) && (!want || want.has(x.track.id))) ?? route.find(on);
  if (!e) return null;
  const f = s - e.s0;
  const q = e.path.at(e.forward ? f : e.path.len - f);
  return { p: q.p, d: e.forward ? q.d : [-q.d[0], -q.d[1]], layer: e.track.layer, width: e.track.width, track: e.index };
}

/** The path's section at a position: { section, unit, z } (the nearest one when s is in a gap without one). */
export function sectionAt(path, s) {
  let best = null, bd = Infinity;
  for (const x of path.sections) {
    const d = s < x.s0 ? x.s0 - s : s > x.s1 ? s - x.s1 : 0;
    if (d < bd) { bd = d; best = x; if (d === 0) break; }
  }
  return best;
}

/** What is at path position s: { s, unit, local (s on the unit's own route), p, d, layer, width, section, z }. */
export function pathAt(path, s) {
  const x = Math.min(path.length, Math.max(0, s));
  const u = path.units.find((v) => x >= v.s0 - 1e-9 && x <= v.s1 + 1e-9) ?? path.units[path.units.length - 1];
  if (!u) return null;
  const local = u.reversed ? u.s1 - x : x - u.s0;
  const sec = sectionAt({ sections: path.sections.filter((v) => v.unit === u.index) }, x);
  const at = routeAt(path.routes[u.index], local, sec?.section?.tracks);
  return { s: x, unit: u, local, p: at?.p ?? null, d: at?.d ?? null, layer: at?.layer ?? null, width: at?.width ?? null,
    section: sec?.section ?? null, z: sec?.z ?? null };
}

/** The nearest point of a track (a route entry's path) to p: { f (along the path), p, d, dist }. */
function nearestOn(e, p) {
  const n = Math.max(1, Math.ceil(e.path.len / 0.05));
  if (!e.track.mid) {
    const a = e.path.at(0), b = e.path.at(e.path.len);
    const dx = b.p[0] - a.p[0], dy = b.p[1] - a.p[1], L2 = dx * dx + dy * dy;
    const f = L2 > 0 ? Math.min(1, Math.max(0, ((p[0] - a.p[0]) * dx + (p[1] - a.p[1]) * dy) / L2)) * e.path.len : 0;
    const q = e.path.at(f);
    return { f, p: q.p, d: q.d, dist: dist(q.p, p) };
  }
  let best = null;
  for (let i = 0; i <= n; i++) {
    const f = (e.path.len * i) / n, q = e.path.at(f), dd = dist(q.p, p);
    if (!best || dd < best.dist) best = { f, p: q.p, d: q.d, dist: dd };
  }
  return best;
}

/**
 * The path position of a point on one of the selection's traces: `net` and `track` (the copper@1 track index under
 * the pointer, when known). A pair's second net maps to the nearest point of the first net's route (the sections are
 * the first net's). Returns { s, unit, p (on the trace), d (its direction), width } or null when the net is not on
 * the path.
 */
export function pathFromBoard(path, copper, net, p, track = null) {
  const u = path.units.find((v) => v.nets.includes(net));
  if (!u) return null;
  const route = path.routes[u.index];
  let local = null, on = null;
  const own = track != null && copper.tracks?.[track]?.net === net ? copper.tracks[track] : null;
  if (net === u.nets[0]) {
    const cands = own ? route.filter((e) => e.index === track) : route;
    let best = null;
    for (const e of cands) { const q = nearestOn(e, p); if (!best || q.dist < best.q.dist) best = { e, q }; }
    if (best) {
      local = best.e.s0 + (best.e.forward ? best.q.f : best.e.path.len - best.q.f);
      on = { p: best.q.p, d: best.e.forward ? best.q.d : [-best.q.d[0], -best.q.d[1]], width: best.e.track.width };
    }
  } else {
    let best = null;
    for (const e of route) { const q = nearestOn(e, p); if (!best || q.dist < best.q.dist) best = { e, q }; }
    if (best) local = best.e.s0 + (best.e.forward ? best.q.f : best.e.path.len - best.q.f);
    if (own) {
      path.partners ??= new Map();
      if (!path.partners.has(net)) path.partners.set(net, netRoute(copper, net));
      const e = path.partners.get(net).find((x) => x.index === track);
      if (e) { const q = nearestOn(e, p); on = { p: q.p, d: q.d, width: own.width }; }
    }
    if (!on && best) on = { p: best.q.p, d: best.q.d, width: best.e.track.width };
  }
  if (local == null) return null;
  const s = u.reversed ? u.s1 - local : u.s0 + local;
  return { s, unit: u, local, ...on };
}

export { shortName as pathNetName };
