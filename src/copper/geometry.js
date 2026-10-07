// boarddd/copper: polygon helpers, the twins of python/src/boarddd/copper.py's (unfracture, areas, point in fill,
// the plane summary and the stale-fill check). Pure; no DOM.

/** A pour covering at least this share of the board's area is a solid plane (`Plane.solid`). */
export const PLANE_COVERAGE = 0.5;

/** Shoelace area: > 0 counter-clockwise (board frame, y up). */
export function signedArea(pts) {
  let a = 0;
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const p = pts[i], q = pts[(i + 1) % n];
    a += p[0] * q[1] - q[0] * p[1];
  }
  return a / 2;
}

const key = (p) => `${Math.round(p[0] * 1e6)},${Math.round(p[1] * 1e6)}`;

/** Even-odd ray cast. */
export function pointInRing(p, ring) {
  const [x, y] = p;
  let inside = false;
  let [x1, y1] = ring[ring.length - 1];
  for (const [x2, y2] of ring) {
    if ((y1 > y) !== (y2 > y) && x < x1 + ((y - y1) * (x2 - x1)) / (y2 - y1)) inside = !inside;
    x1 = x2;
    y1 = y2;
  }
  return inside;
}

export function bbox(pts) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) {
    if (x < x0) x0 = x;
    if (y < y0) y0 = y;
    if (x > x1) x1 = x;
    if (y > y1) y1 = y;
  }
  return [x0, y0, x1, y1];
}

const round = (v, d) => {
  const f = 10 ** d;
  const x = Math.round(v * f) / f;
  return x === 0 ? 0 : x;
};

const inBox = (p, b) => b[0] <= p[0] && p[0] <= b[2] && b[1] <= p[1] && p[1] <= b[3];

/**
 * A fractured polygon (KiCad's filled_polygon, a Gerber G36 region: holes joined to the outline by zero-width
 * cuts) as `[{ outline, holes }]`: the cut edge pairs cancel, the rest chains into loops (the original order
 * wins at a vertex with several ways on) and loops nest by containment (even depth = outline, counter-clockwise;
 * odd = hole, clockwise). Points match at 1 nm.
 */
export function unfracture(ring) {
  const pts = [];
  for (const p of ring) if (!pts.length || key(p) !== key(pts[pts.length - 1])) pts.push(p);
  while (pts.length > 1 && key(pts[0]) === key(pts[pts.length - 1])) pts.pop();
  const n = pts.length;
  if (n < 3) return [];
  const keys = pts.map(key);
  const edges = keys.map((k, i) => [k, keys[(i + 1) % n]]);
  const byDir = new Map();
  edges.forEach(([a, b], i) => {
    const k = `${a}>${b}`;
    if (!byDir.has(k)) byDir.set(k, []);
    byDir.get(k).push(i);
  });
  const alive = new Array(n).fill(true);
  edges.forEach(([a, b], i) => {
    if (!alive[i]) return;
    const back = (byDir.get(`${b}>${a}`) || []).find((j) => alive[j]);
    if (back !== undefined) alive[i] = alive[back] = false;
  });
  const outEdges = new Map();
  edges.forEach(([a], i) => {
    if (!alive[i]) return;
    if (!outEdges.has(a)) outEdges.set(a, []);
    outEdges.get(a).push(i);
  });
  const loops = [];
  for (let start = 0; start < n; start++) {
    if (!alive[start]) continue;
    const loop = [];
    let i = start;
    while (alive[i]) {
      alive[i] = false;
      loop.push(pts[i]);
      const v = edges[i][1];
      const next = (i + 1) % n;
      if (alive[next] && edges[next][0] === v) { i = next; continue; }
      const cand = (outEdges.get(v) || []).find((j) => alive[j]);
      if (cand === undefined) break;
      i = cand;
    }
    if (loop.length >= 3 && Math.abs(signedArea(loop)) > 1e-12) loops.push(loop);
  }
  // a stable sort, as Python's
  const sorted = loops.map((lp, i) => [lp, -Math.abs(signedArea(lp)), i]).sort((a, b) => a[1] - b[1] || a[2] - b[2]).map((x) => x[0]);
  const boxes = sorted.map(bbox);
  const parent = [], depth = [];
  sorted.forEach((lp, i) => {
    let p = null;
    const bx = boxes[i];
    for (let j = i - 1; j >= 0; j--) {
      const bj = boxes[j];
      if (bx[0] < bj[0] || bx[1] < bj[1] || bx[2] > bj[2] || bx[3] > bj[3]) continue;
      const mid = [(lp[0][0] + lp[1][0]) / 2, (lp[0][1] + lp[1][1]) / 2];
      if (pointInRing(lp[0], sorted[j]) || pointInRing(mid, sorted[j])) { p = j; break; }
    }
    parent.push(p);
    depth.push(p === null ? 0 : depth[p] + 1);
  });
  const polys = new Map();
  sorted.forEach((lp, i) => {
    const a = signedArea(lp);
    if (depth[i] % 2 === 0) polys.set(i, { outline: a > 0 ? lp : [...lp].reverse(), holes: [] });
    else polys.get(parent[i]).holes.push(a < 0 ? lp : [...lp].reverse());
  });
  return [...polys.values()];
}

/** Filled area of `[{ outline, holes }]`, mm^2. */
export function fillArea(fill) {
  let a = 0;
  for (const p of fill) {
    a += Math.abs(signedArea(p.outline));
    for (const h of p.holes) a -= Math.abs(signedArea(h));
  }
  return a;
}

/**
 * A full circle as two half arcs `[start, end, mid]`: start -> opposite with its mid a quarter turn counter-clockwise
 * from start, then back (tracks have no full circles), as python's `boarddd.copper.circle_halves`.
 */
export function circleHalves(c, start) {
  const dx = start[0] - c[0], dy = start[1] - c[1];
  const o = [round(c[0] - dx, 6), round(c[1] - dy, 6)];
  const m1 = [round(c[0] - dy, 6), round(c[1] + dx, 6)];
  const m2 = [round(c[0] + dy, 6), round(c[1] - dx, 6)];
  const s = [round(start[0], 6), round(start[1], 6)];
  return [[s, o, m1], [o, s, m2]];
}

/** Whether `p` is inside a FillPolygon (in its outline, not in a hole). */
export function inFill(p, poly) {
  return pointInRing(p, poly.outline) && !poly.holes.some((h) => inBox(p, bbox(h)) && pointInRing(p, h));
}

/** Pour area per (layer, net), teardrops and no-net copper excluded; by `layers` order, then largest first. */
export function planes(zones, boardArea, layers = []) {
  const acc = new Map();
  for (const z of zones) {
    if (z.kind === 'teardrop' || !z.net) continue;
    const k = JSON.stringify([z.layer, z.net]);
    acc.set(k, (acc.get(k) ?? 0) + z.area);
  }
  const rank = new Map(layers.map((l, i) => [l, i]));
  return [...acc.entries()]
    .map(([k, area]) => [JSON.parse(k), area])
    .sort((a, b) => (rank.get(a[0][0]) ?? rank.size) - (rank.get(b[0][0]) ?? rank.size) || b[1] - a[1])
    .map(([[layer, net], area]) => {
      const coverage = boardArea ? round(Math.min(area / boardArea, 1), 4) : null;
      return { layer, net, area: round(area, 4), coverage, solid: Boolean(coverage && coverage >= PLANE_COVERAGE) };
    });
}

/**
 * Mark pours (and Gerber regions) whose fill holds copper of another net (a track end or middle, a via or pad
 * centre on its layer) as `stale`, with a warning each; returns how many. Teardrops and copper drawings are not
 * checked. As python's `boarddd.copper.check_fills`.
 */
export function checkFills(cu) {
  const probes = new Map();
  const add = (layer, p, net, what) => {
    if (!probes.has(layer)) probes.set(layer, []);
    probes.get(layer).push([p, net, what]);
  };
  for (const t of cu.tracks) {
    const m = t.mid ?? [(t.start[0] + t.end[0]) / 2, (t.start[1] + t.end[1]) / 2];
    for (const p of [t.start, t.end, m]) add(t.layer, p, t.net, 'track');
  }
  const order = new Map(cu.layers.map((l, i) => [l, i]));
  for (const v of cu.vias) {
    const a = order.get(v.span[0]) ?? 0, b = order.get(v.span[1]) ?? cu.layers.length - 1;
    for (const l of v.pad_layers ?? cu.layers.slice(a, b + 1)) add(l, v.at, v.net, 'via');
  }
  for (const pd of cu.pads) for (const l of pd.layers) add(l, pd.at, pd.net, pd.ref ? `pad ${pd.ref}.${pd.number}` : 'pad');
  let stale = 0;
  for (const z of cu.zones) {
    if (!z.fill.length || (z.kind !== 'pour' && z.kind !== 'region')) continue; // drawings and teardrops are not filled around copper
    let hit = null;
    for (const poly of z.fill) {
      const box = bbox(poly.outline);
      const holes = poly.holes.map((h) => [bbox(h), h]);
      hit = (probes.get(z.layer) || []).find(
        ([p, net]) => net !== z.net && inBox(p, box) && pointInRing(p, poly.outline) && !holes.some(([hb, h]) => inBox(p, hb) && pointInRing(p, h)),
      );
      if (hit) break;
    }
    z.stale = Boolean(hit);
    if (hit) {
      stale += 1;
      const [p, net, what] = hit;
      const label = z.name ? `zone '${z.name}'` : 'zone';
      cu.warnings.push(
        `${label} (${z.net || 'no net'}) on ${z.layer}: fill is stale: ${what} of net '${net || '(none)'}' ` +
          `at (${p[0].toFixed(3)}, ${p[1].toFixed(3)}) is inside it; refill the zones`,
      );
    }
  }
  return stale;
}
