// boarddd/impedance/ui: an SVG cross-section of a boarddd/impedance@1 section, rebuilt from its geometry on the
// board@1 stackup the same way analyzeNet built it for the field solver (lineFromStackup with a layout):
// dielectrics with their εr, copper (traces, coplanar grounds, reference planes), the solder mask. Dimensions
// show on hover (w, s, g, h); every shape has a tooltip with its words.
import { lineFromStackup } from '../stackup.js';

const NS = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs = {}, parent = null, text = null) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, String(v));
  if (text != null) e.textContent = text;
  parent?.append(e);
  return e;
};
const mm = (v) => `${+v.toFixed(3)} mm`;

/** The layout analyzeNet solved for a section (traces centred on the analysed one; pair partner on its side). */
export function sectionLayout(section) {
  const g = section.geometry;
  const w = g.width;
  const traces = [{ x0: -w / 2, x1: w / 2, net: section.kind === 'differential' ? 'p' : 'sig' }];
  if (section.kind === 'differential' && g.gap != null) traces.push({ x0: w / 2 + g.gap, x1: w / 2 + g.gap + (g.partner_width ?? w), net: 'n' });
  const left = traces[0].x0, right = traces[traces.length - 1].x1;
  const grounds = [];
  const [gl, gr] = g.coplanar_gap ?? [null, null];
  if (gl != null) grounds.push({ x0: null, x1: left - gl });
  if (gr != null) grounds.push({ x0: right + gr, x1: null });
  const planes = { top: null, bottom: null };
  for (const r of section.refs ?? []) {
    const [x0, x1] = r.extent ?? [null, null];
    if (x0 != null || x1 != null) planes[r.side] = { x0, x1 };
  }
  return { traces, grounds, planes };
}

/** The field-solver section (conductors, dielectrics) of an impedance@1 section; null when it has no reference. */
export function sectionGeometry(board, section) {
  const top = section.refs?.find((r) => r.side === 'top')?.layer ?? false;
  const bottom = section.refs?.find((r) => r.side === 'bottom')?.layer ?? false;
  if (top === false && bottom === false && !(section.geometry.coplanar_gap ?? []).some((x) => x != null)) return null;
  const layout = sectionLayout(section);
  const line = lineFromStackup(board.stackup, section.layer, {
    width: section.geometry.width, kind: 'single', refTop: top, refBottom: bottom,
    structure: top && bottom ? 'stripline' : 'microstrip', solver: 'field', layout,
  });
  return { ...line.section, layout };
}

/**
 * Draw `section` (an impedance@1 section) into `el` as an SVG cross-section on `board`'s stackup. Returns the
 * <svg> (or a <div> saying why there is none). Options: `width`/`height` (CSS px, default the element's size or
 * 320 x 160), `margin` (mm shown beyond the copper, default max(3 h, 0.3)).
 */
export function crossSection(el, section, { board, width = null, height = null, margin = null } = {}) {
  el.replaceChildren();
  let geo = null;
  try { geo = section ? sectionGeometry(board, section) : null; } catch { geo = null; }
  if (!geo) {
    const d = document.createElement('div');
    d.className = 'bdi-xs-none';
    d.textContent = section ? '✗' : '';
    d.title = section ? 'no reference plane: no cross-section (a via transition or a plane void)' : '';
    el.append(d);
    return d;
  }
  const W = width ?? (el.clientWidth || 320), H = height ?? (el.clientHeight || 160);
  const { conductors, dielectrics, layout } = geo;
  const ys = [...conductors, ...dielectrics].flatMap((r) => [r.y0, r.y1]);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const tr = layout.traces;
  const left = Math.min(...tr.map((t) => t.x0)), right = Math.max(...tr.map((t) => t.x1));
  const hs = (section.refs ?? []).map((r) => r.h);
  const m = margin ?? Math.max(3 * Math.max(...hs, 0.1), 0.3);
  let x0 = left - m, x1 = right + m;
  // keep the trace readable: at most 4:1 the other way
  const sx = W / (x1 - x0), sy = H / (y1 - y0);
  const s = Math.min(sx, sy);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  x0 = cx - W / s / 2; x1 = cx + W / s / 2; y0 = cy - H / s / 2; y1 = cy + H / s / 2;
  const root = svg('svg', { class: 'bdi-xs', viewBox: `${x0} ${-y1} ${x1 - x0} ${y1 - y0}`, width: W, height: H, preserveAspectRatio: 'xMidYMid meet' }, el);
  const g = svg('g', { transform: 'scale(1,-1)' }, root);
  const clip = (r) => ({ x: Math.max(r.x0 ?? x0, x0), w: Math.min(r.x1 ?? x1, x1) - Math.max(r.x0 ?? x0, x0) });
  const isMask = (d) => conductors.length && d.er < 4 && d.y1 - d.y0 < 0.06;
  // dielectrics (later ones win: draw in order)
  const bands = [];
  for (const d of dielectrics) {
    const { x, w } = clip(d);
    if (w <= 0) continue;
    const mask = isMask(d);
    const rect = svg('rect', { x, y: d.y0, width: w, height: d.y1 - d.y0, class: mask ? 'bdi-xs-mask' : 'bdi-xs-diel' }, g);
    svg('title', {}, rect, `${mask ? 'solder mask' : 'dielectric'} · εr ${+d.er.toFixed(3)} · ${mm(d.y1 - d.y0)}`);
    if (!mask && d.x0 == null && d.x1 == null) bands.push(d);
  }
  // copper
  const label = { sig: 'trace', p: 'trace (P)', n: 'partner (N)' };
  const slabY = conductors.find((c) => c.net !== 'gnd')?.y0 ?? 0;
  const refAt = (c) => (section.refs ?? []).find((r) => r.side === (c.y0 < slabY ? 'bottom' : 'top'));
  for (const c of conductors) {
    const { x, w } = clip(c);
    if (w <= 0) continue;
    const plane = c.net === 'gnd' && Math.abs(c.y0 - slabY) > 1e-9; // not in the trace's own layer
    const rect = svg('rect', { x, y: c.y0, width: w, height: Math.max(c.y1 - c.y0, (y1 - y0) / 200), class: c.net === 'gnd' ? (plane ? 'bdi-xs-plane' : 'bdi-xs-gnd') : 'bdi-xs-cu' }, g);
    const ref = plane ? refAt(c) : null;
    const edge = ref && (ref.extent?.[0] != null || ref.extent?.[1] != null) ? ' · ends near the trace' : '';
    svg('title', {}, rect, c.net === 'gnd'
      ? plane ? `reference plane ${ref?.layer ?? ''} ${ref?.net ?? ''} · h ${ref ? mm(ref.h) : '?'}${edge}` : 'coplanar ground'
      : `${label[c.net] ?? c.net} · ${mm(c.x1 - c.x0)} × ${mm(c.y1 - c.y0)}`);
  }
  // εr labels on the right of each full-width band
  const fs = (y1 - y0) / 14;
  for (const d of bands) {
    if (d.y1 - d.y0 < fs * 0.9) continue;
    svg('text', { x: x1 - fs * 0.3, y: -(d.y0 + d.y1) / 2 + fs * 0.35, 'font-size': fs, 'text-anchor': 'end', class: 'bdi-xs-er', transform: 'scale(1,-1)' }, g, `εr ${+d.er.toFixed(2)}`);
  }
  // dimensions, shown on hover
  const dims = svg('g', { class: 'bdi-xs-dims' }, root);
  const slab = conductors.find((c) => c.net !== 'gnd');
  const ty = slab ? -slab.y1 - fs * 0.6 : -y1 + fs;
  const dim = (a, b, y, text) => {
    svg('line', { x1: a, x2: b, y1: y, y2: y, class: 'bdi-xs-dim' }, dims);
    svg('text', { x: (a + b) / 2, y: y - fs * 0.25, 'font-size': fs * 0.8, 'text-anchor': 'middle', class: 'bdi-xs-dimtext' }, dims, text);
  };
  dim(tr[0].x0, tr[0].x1, ty, `w ${+section.geometry.width.toFixed(3)}`);
  if (tr.length > 1) dim(tr[0].x1, tr[1].x0, ty - fs, `s ${+section.geometry.gap.toFixed(3)}`);
  for (const gr of layout.grounds) {
    const a = gr.x1 != null ? gr.x1 : right, b = gr.x1 != null ? left : gr.x0;
    if (b > a) dim(a, b, ty, `g ${+(b - a).toFixed(3)}`);
  }
  for (const r of section.refs ?? []) {
    if (!slab) break;
    const x = right + Math.min(m / 2, 0.15);
    const ya = r.side === 'bottom' ? slab.y0 : slab.y1, yb = r.side === 'bottom' ? slab.y0 - r.h : slab.y1 + r.h;
    svg('line', { x1: x, x2: x, y1: -ya, y2: -yb, class: 'bdi-xs-dim' }, dims);
    svg('text', { x: x + fs * 0.3, y: -(ya + yb) / 2 + fs * 0.3, 'font-size': fs * 0.8, class: 'bdi-xs-dimtext' }, dims, `h ${+r.h.toFixed(3)} ${r.layer}`);
  }
  return root;
}
