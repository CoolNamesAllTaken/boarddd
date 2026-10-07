// boarddd/impedance/ui: copper on a view2d stage. copperContent draws a boarddd/copper@1 document (for boards
// without Gerbers); createHighlight is an overlay showing the selected nets (the rest dimmed), a route section,
// a position marker and discontinuity rings; attachPicker turns stage pointer events into copper picks (hover:
// net and width; click: the net, or its pair; alt-click: the segment alone).
import { draw } from '../../view2d/content.js';
import { netRoute } from '../route.js';
import { copperHitIndex, pairOf, pairOrder, trackPath } from './pick.js';

const ringPath = (r) => `M${r.map(([x, y]) => `${x},${y}`).join('L')}Z`;

/** Copper colours per layer (top to bottom), CSS. */
export const LAYER_COLORS = ['#c8553d', '#4f8a5b', '#b9a33f', '#3f7cc8', '#8a4fb0', '#3fa8a8', '#a8743f', '#4d6fd1'];
export const layerColor = (copper, layer) => {
  const i = (copper.layers ?? []).indexOf(layer);
  return i === (copper.layers?.length ?? 0) - 1 && i > 0 ? '#4d6fd1' : LAYER_COLORS[Math.max(0, i) % LAYER_COLORS.length];
};

/**
 * A view2d `draw` content of a copper@1 document: `layers` (default all, bottom first so the top is on top),
 * zones translucent, tracks, pads and vias solid, in each layer's colour (`colors`: {layer: css}).
 */
export function copperContent(copper, { layers = null, colors = {}, zoneAlpha = 0.35, alpha = 0.9 } = {}) {
  const order = [...(layers ?? copper.layers ?? [])].reverse();
  return draw((g) => {
    for (const layer of order) {
      const col = colors[layer] ?? layerColor(copper, layer);
      g.fillStyle = col;
      g.strokeStyle = col;
      g.globalAlpha = zoneAlpha;
      for (const z of copper.zones ?? []) {
        if (z.layer !== layer) continue;
        const p = new Path2D();
        for (const f of z.fill) for (const r of [f.outline, ...f.holes]) p.addPath(new Path2D(ringPath(r)));
        g.fill(p, 'evenodd');
      }
      g.globalAlpha = alpha;
      g.lineCap = 'round';
      g.lineJoin = 'round';
      for (const t of copper.tracks ?? []) {
        if (t.layer !== layer) continue;
        const pts = trackPath(t);
        g.lineWidth = t.width;
        g.beginPath();
        pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
        g.stroke();
      }
      for (const p of copper.pads ?? []) {
        if (!p.layers.includes(layer)) continue;
        for (const r of p.polygons ?? []) g.fill(new Path2D(ringPath(r)));
      }
    }
    g.globalAlpha = 1;
    g.fillStyle = '#d8d2c0';
    for (const v of copper.vias ?? []) { g.beginPath(); g.arc(v.at[0], v.at[1], v.diameter / 2, 0, 2 * Math.PI); g.fill(); }
  }, copper.bounds ?? null);
}

/** The points of a net's route between s0 and s1 over the given tracks (a section's stretch), in route order. */
export function routeSlice(route, s0, s1, tracks = null) {
  const want = tracks && tracks.length ? new Set(tracks) : null;
  const out = [];
  for (const e of route) {
    if (e.s1 < s0 - 1e-9 || e.s0 > s1 + 1e-9) continue;
    if (want && !want.has(e.track.id)) continue;
    const a = Math.max(s0, e.s0) - e.s0, b = Math.min(s1, e.s1) - e.s0;
    if (b < a - 1e-9) continue;
    const n = Math.max(1, Math.ceil((b - a) / 0.1));
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const f = a + ((b - a) * i) / n;
      pts.push(e.path.at(e.forward ? f : e.path.len - f).p);
    }
    out.push({ layer: e.track.layer, width: e.track.width, points: pts });
  }
  return out;
}

/** The point at distance s along a net's route (the first stretch that covers it), or null. */
export function routePoint(route, s, tracks = null) {
  const want = tracks && tracks.length ? new Set(tracks) : null;
  const e = route.find((x) => x.s0 - 1e-9 <= s && s <= x.s1 + 1e-9 && (!want || want.has(x.track.id)))
    ?? route.find((x) => x.s0 - 1e-9 <= s && s <= x.s1 + 1e-9);
  if (!e) return null;
  const f = s - e.s0;
  return { p: e.path.at(e.forward ? f : e.path.len - f).p, layer: e.track.layer };
}

/**
 * An overlay on `stage` for a copper@1 document: `set({ nets, track })` shows the nets' copper (or one track)
 * over a dimmed board, `section(sec)` outlines a route section, `marker(p)` a position, `rings(points)`
 * discontinuities. Colours from CSS variables on the stage element (--bdi-hl, --bdi-hl-section, --bdi-marker).
 */
export function createHighlight(stage, copper, { dim = 0.55 } = {}) {
  let sel = { nets: [], track: null };
  let sec = null;
  let mark = null;
  let rings = [];
  const routes = new Map();
  const routeOf = (net) => { if (!routes.has(net)) routes.set(net, netRoute(copper, net)); return routes.get(net); };
  const overlay = stage.addOverlay({
    space: 'world',
    className: 'bdi-hl',
    draw(g, ctx) {
      g.replaceChildren();
      if (!sel.nets.length && sel.track == null) return;
      const b = stage.bounds ?? { minX: -1e3, maxX: 1e3, minY: -1e3, maxY: 1e3 };
      ctx.svg('rect', { x: b.minX - 50, y: b.minY - 50, width: b.maxX - b.minX + 100, height: b.maxY - b.minY + 100, class: 'bdi-dim', 'fill-opacity': dim }, g);
      const nets = new Set(sel.nets);
      const tracks = sel.track != null ? [copper.tracks[sel.track]] : (copper.tracks ?? []).filter((t) => nets.has(t.net));
      for (const t of tracks) {
        const pts = trackPath(t);
        ctx.svg('polyline', { points: pts.map((p) => p.join(',')).join(' '), 'stroke-width': t.width, class: 'bdi-hl-track', 'data-layer': t.layer }, g);
      }
      if (sel.track == null) {
        for (const p of copper.pads ?? []) if (nets.has(p.net)) for (const r of p.polygons ?? []) ctx.svg('path', { d: ringPath(r), class: 'bdi-hl-pad' }, g);
        for (const v of copper.vias ?? []) if (nets.has(v.net)) ctx.svg('circle', { cx: v.at[0], cy: v.at[1], r: v.diameter / 2, class: 'bdi-hl-via' }, g);
        for (const z of copper.zones ?? []) if (nets.has(z.net)) for (const f of z.fill) ctx.svg('path', { d: [f.outline, ...f.holes].map(ringPath).join(''), class: 'bdi-hl-zone', 'fill-rule': 'evenodd' }, g);
      }
      const px = ctx.mmPerPx();
      if (sec) {
        for (const part of routeSlice(routeOf(sec.net), sec.s0, sec.s1, sec.tracks)) {
          ctx.svg('polyline', { points: part.points.map((p) => p.join(',')).join(' '), 'stroke-width': part.width + 4 * px, class: 'bdi-hl-section' }, g);
        }
      }
      // rings and the marker: screen-sized (view2d world strokes are px)
      for (const r of rings) ctx.svg('circle', { cx: r[0], cy: r[1], r: 9 * px, 'stroke-width': 2, class: 'bdi-ring' }, g);
      if (mark) {
        ctx.svg('circle', { cx: mark[0], cy: mark[1], r: 6 * px, 'stroke-width': 4, class: 'bdi-marker-halo' }, g);
        ctx.svg('circle', { cx: mark[0], cy: mark[1], r: 6 * px, 'stroke-width': 2, class: 'bdi-marker' }, g);
      }
    },
  });
  return {
    get selection() { return { ...sel }; },
    set({ nets = [], track = null } = {}) { sel = { nets, track }; sec = null; mark = null; rings = []; overlay.invalidate(); },
    section(s) { sec = s; overlay.invalidate(); },
    marker(p) { mark = p; overlay.invalidate(); },
    rings(points) { rings = points ?? []; overlay.invalidate(); },
    routeOf,
    /** Redraw (e.g. after a zoom, so screen-sized rings stay the same size). */
    invalidate() { overlay.invalidate(); },
    remove() { overlay.remove(); },
  };
}

/**
 * Pointer picking of copper on `stage`: hover shows the net and width in a tooltip, click calls
 * `onSelect({ nets, track, hit })` with the net (and its differential partner unless `pairs: false`), alt-click
 * with the track alone. `layers` limits what can be picked: a list, or a function returning the layers on show
 * (default every copper layer).
 */
export function attachPicker(stage, copper, { board = null, onSelect = () => {}, onHover = null, pairs = true, layers = null, slackPx = 4 } = {}) {
  const index = copperHitIndex(copper);
  const names = copper.nets ?? [];
  // what may be picked: `layers` as a list, or a function returning one (the layers on show); null = all
  const allowed = () => { const l = typeof layers === 'function' ? layers() : layers; return l ? new Set(l) : null; };
  const tip = document.createElement('div');
  tip.className = 'bdi-tip';
  tip.hidden = true;
  stage.root.append(tip);
  const describe = (d) => `${d.net || '(no net)'}${d.width ? ` · ${+d.width.toFixed(4)} mm` : ''} · ${d.layer}`;
  const pickAt = (x, y) => {
    const ok = allowed();
    // nearest first; under the point, the smallest first (a track over a pour wins)
    return index.all(x, y, slackPx * stage.mmPerPx()).find((sh) => !ok || ok.has(sh.data.layer) || sh.data.layers?.some((l) => ok.has(l)))?.data ?? null;
  };
  const offMove = stage.on('move', (e) => {
    const d = pickAt(e.x, e.y);
    onHover?.(d, e);
    if (!d) { tip.hidden = true; return; }
    tip.textContent = describe(d);
    tip.hidden = false;
    const r = stage.root.getBoundingClientRect();
    tip.style.left = `${e.event.clientX - r.left + 12}px`;
    tip.style.top = `${e.event.clientY - r.top + 12}px`;
  });
  const offLeave = stage.on('leave', () => { tip.hidden = true; });
  const offClick = stage.on('click', (e) => {
    const d = pickAt(e.x, e.y);
    if (!d || !d.net) { onSelect({ nets: [], track: null, hit: d }); return; }
    if (e.event.altKey && d.track != null) { onSelect({ nets: [d.net], track: d.track, hit: d }); return; }
    const partner = pairs ? pairOf(d.net, { board, names }) : null;
    onSelect({ nets: partner ? pairOrder(d.net, partner) : [d.net], track: null, hit: d });
  });
  return {
    index,
    /** What is at a world point (board mm). */
    pick: pickAt,
    detach() { offMove(); offLeave(); offClick(); tip.remove(); },
  };
}

/**
 * Board mm of a boarddd/scene pick on the board solid: the hit point in the solid's own frame (its geometry is
 * board mm, as boarddd/board builds it). null for a hit on something else (a part).
 */
export function boardPointFromPick(hit) {
  const obj = hit?.object;
  if (!obj || !hit.point || typeof obj.worldToLocal !== 'function') return null;
  if (obj.userData?.group && !['board', 'mask', 'copper', 'silk'].includes(obj.userData.group)) return null;
  const p = typeof hit.point.clone === 'function' ? hit.point.clone() : { ...hit.point };
  const local = obj.worldToLocal(p);
  return { x: local.x, y: local.y, z: local.z };
}
