// boarddd/impedance/ui: layerView, the board one copper layer at a time (the layers above ghosted, the ones below
// hidden), a stepper through the stackup (copper and the dielectrics between), and picking nets to control:
//   click a trace     adds its whole net to the selection; click it again drops it (a drag or a long press pans)
//   the panel         per net or pair: its reference plane(s) as a cross-section, breaks in them along the route,
//                     a reference override; for the whole selection: structure, target, tolerance, Z per geometry:
//      ⇄ N12 N13 [B.Cu] ✂ ✕          [▭▭] over In4.Cu (GND) · 0.2 mm  ◫ [auto ▾]   ⚠ crosses a split in In4.Cu
//      [MS ▾]  — [50] Ω  ⇄ [90] Ω ± [10] %
//      F.Cu w 0.2  ≈ 52.3 Ω  50 Ω at w 0.212
//      [✓ Save 3]
// Selecting never moves the view: only a drag, the wheel, a pinch, ⌖ (the selection) and ⤢ (the board) do.
// Works from a copper@1 document alone (plain Gerbers through boarddd.connectivity, or X2/KiCad copper); the
// impedance figures are tier 1 (closed form) on `board.stackup`, the reference planes from analyzeNet along each
// route (with `route`), and an app may give the tier-2 (field solver) figure through `evaluate`.
import { draw } from '../../view2d/content.js';
import { createStage } from '../../view2d/stage.js';
import { parallelPartner } from '../../nets/similar.js';
import { evaluateTarget } from '../stackup.js';
import { createAnalyzer } from './analyzer.js';
import { crossSection } from './crosssection.js';
import { netPicker } from './netpick.js';
import { impedancePanel, STRUCTURES } from './panel.js';
import { copperHitIndex, pairOf, pairOrder, trackPath } from './pick.js';
import { copperContent, createHighlight, layerColor } from './stage.js';
import { IMPEDANCE_UI_CSS } from './style.js';

const GHOST = 0.16;
const CLICK_MS = 300; // a press held longer pans (view2d's click also needs less than 4 px of movement)
const MAX_T2 = 6; // tier-2 figures asked of the app per selection (distinct geometries)
const el = (tag, cls, parent, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  parent?.append(e);
  return e;
};
const f1 = (v) => (v == null || !Number.isFinite(v) ? '–' : v.toFixed(1));
const mm = (v) => (v == null || !Number.isFinite(v) ? '–' : String(+v.toFixed(4)));
const len = (t) => { const p = trackPath(t); let s = 0; for (let i = 1; i < p.length; i++) s += Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]); return s; };

/** The stepper's stops, top to bottom: every copper layer of `copper`, and the dielectrics of `stackup` between. */
export function layerStops(copper, stackup = null) {
  const cu = copper.layers ?? [];
  const rows = (stackup?.layers ?? []).filter((l) => l.kind === 'copper' || l.kind === 'dielectric');
  const stops = [];
  if (rows.some((l) => l.kind === 'copper' && cu.includes(l.layer))) {
    let seen = 0;
    for (const l of rows) {
      if (l.kind === 'copper' && cu.includes(l.layer)) { stops.push({ kind: 'copper', layer: l.layer, name: l.layer, thickness: l.thickness ?? null }); seen++; }
      else if (l.kind === 'dielectric' && seen > 0 && seen < cu.length) {
        const last = stops[stops.length - 1];
        if (last.kind === 'dielectric') { last.thickness = (last.thickness ?? 0) + (l.thickness ?? 0); last.parts.push(l); continue; }
        stops.push({ kind: 'dielectric', name: l.name, thickness: l.thickness ?? null, epsilon_r: l.epsilon_r ?? null,
          material: l.material ?? null, dielectric: l.dielectric ?? null, parts: [l] });
      }
    }
    for (const l of cu) if (!stops.some((s) => s.layer === l)) stops.push({ kind: 'copper', layer: l, name: l, thickness: null });
  } else {
    cu.forEach((l, i) => { if (i) stops.push({ kind: 'dielectric', name: '', thickness: null, epsilon_r: null, parts: [] }); stops.push({ kind: 'copper', layer: l, name: l, thickness: null }); });
  }
  return stops;
}

/** The structure a net's route mostly is (from an impedance@1 analysis), with its layer and geometry. */
export function mainSection(doc) {
  if (!doc) return null;
  const by = new Map();
  for (const s of doc.sections.filter((x) => x.net === doc.nets[0] && x.z)) {
    const key = `${s.layer}|${s.structure}|${s.geometry?.width}|${s.geometry?.gap ?? ''}`;
    const was = by.get(key) ?? { length: 0, s };
    was.length += s.length;
    by.set(key, was);
  }
  return [...by.values()].sort((a, b) => b.length - a.length)[0]?.s ?? null;
}

function boundsOf(copper) {
  const b = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  const add = (x, y, r = 0) => { b.minX = Math.min(b.minX, x - r); b.maxX = Math.max(b.maxX, x + r); b.minY = Math.min(b.minY, y - r); b.maxY = Math.max(b.maxY, y + r); };
  for (const t of copper.tracks ?? []) for (const p of trackPath(t)) add(p[0], p[1], t.width / 2);
  for (const p of copper.pads ?? []) for (const r of p.polygons ?? []) for (const q of r) add(q[0], q[1]);
  for (const z of copper.zones ?? []) for (const f of z.fill) for (const q of f.outline) add(q[0], q[1]);
  for (const v of copper.vias ?? []) add(v.at[0], v.at[1], v.diameter / 2);
  if (!Number.isFinite(b.minX)) return { minX: 0, maxX: 10, minY: 0, maxY: 10 };
  const m = 0.02 * Math.max(b.maxX - b.minX, b.maxY - b.minY);
  return { minX: b.minX - m, maxX: b.maxX + m, minY: b.minY - m, maxY: b.maxY + m };
}

// the target's structure (the stackup's names), and what each of the route's structures is as a target
const STRUCTURE_OPTIONS = [['', 'auto', 'from the stackup and the copper around the trace'], ['microstrip', 'MS', STRUCTURES.microstrip[1]],
  ['stripline', 'SL', STRUCTURES.stripline[1]], ['coplanar_grounded', 'CPWG', STRUCTURES.cpwg[1]], ['coplanar', 'CPW', STRUCTURES.cpw[1]]];
const AS_TARGET = { microstrip: 'microstrip', stripline: 'stripline', offset_stripline: 'stripline', cpwg: 'coplanar_grounded', cpw: 'coplanar' };
const SHORT = { microstrip: 'MS', stripline: 'SL', coplanar_grounded: 'CPWG', coplanar: 'CPW' };


const btn = (cls, parent, text, title) => {
  const b = el('button', cls, parent, text);
  b.type = 'button';
  b.title = title;
  b.setAttribute('aria-label', title);
  return b;
};
// icons as inline SVG: ◫ and ⌖ are missing from many system fonts
const ICONS = {
  target: '<path d="M8 1v4M8 11v4M1 8h4M11 8h4"/><circle cx="8" cy="8" r="4.5"/>',
  plane: '<path d="M2 10.5l6 3 6-3M2 7.5l6 3 6-3-6-3z"/>',
};
const icon = (b, name) => {
  b.textContent = '';
  b.insertAdjacentHTML('beforeend', `<svg class="bdi-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`);
  return b;
};
const pairKey = (a, b) => [a, b].sort().join('\n');
const minOf = (xs) => { const v = (xs ?? []).filter((x) => x != null); return v.length ? Math.min(...v) : null; };
const xy = (p) => `${p[0].toFixed(1)}, ${p[1].toFixed(1)}`;
const refName = (r) => `${r.layer}${r.net ? ` (${r.net})` : ''}`;

/** The nets of a copper@1 document for a net box: [{ name, pads, layers, auto }] (pads and auto from `netInfo` when given). */
export function netEntries(copper, netInfo = {}) {
  const pads = new Map(), layers = new Map();
  const on = (net, l) => { if (!net) return; if (!layers.has(net)) layers.set(net, new Set()); layers.get(net).add(l); };
  for (const p of copper.pads ?? []) { if (p.net) pads.set(p.net, (pads.get(p.net) ?? 0) + 1); for (const l of p.layers ?? []) on(p.net, l); }
  for (const t of copper.tracks ?? []) on(t.net, t.layer);
  for (const z of copper.zones ?? []) on(z.net, z.layer);
  const order = copper.layers ?? [];
  const names = copper.nets?.length ? copper.nets : [...layers.keys()];
  return names.filter(Boolean).map((name) => ({ name, pads: netInfo[name]?.pads ?? pads.get(name) ?? 0,
    layers: order.filter((l) => layers.get(name)?.has(l)), auto: !!netInfo[name]?.auto }));
}

const inRing = (r, x, y) => {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, yi] = r[i], [xj, yj] = r[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
};
/** The net of the zone on `layer` at a point ('' when none): the plane a chosen reference layer has there. */
export function zoneNetAt(copper, layer, [x, y]) {
  for (const z of copper.zones ?? []) {
    if (z.layer !== layer) continue;
    for (const f of z.fill) if (inRing(f.outline, x, y) && !f.holes.some((h) => inRing(h, x, y))) return z.net ?? '';
  }
  return '';
}

/** Where a net is mostly routed: the layer and width with the most track length. */
export function routedOn(copper, net) {
  const by = new Map();
  for (const t of copper.tracks ?? []) if (t.net === net) by.set(`${t.layer}\n${t.width}`, (by.get(`${t.layer}\n${t.width}`) ?? 0) + len(t));
  const best = [...by].sort((a, b) => b[1] - a[1])[0];
  if (!best) return null;
  const [layer, width] = best[0].split('\n');
  return { layer, width: Number(width) };
}

/**
 * The reference planes the stackup alone gives a trace on `layer`: the nearest copper above and below, or `pick`
 * (a copper layer) on its side; [{ side, layer, net: '', h }] with the dielectric (and skipped copper) between.
 */
export function stackupRefs(stackup, layer, pick = null) {
  const ls = stackup?.layers ?? [];
  const i = ls.findIndex((l) => l.kind === 'copper' && (l.layer === layer || l.name === layer));
  if (i < 0) return [];
  const out = [];
  for (const [side, dir] of [['top', -1], ['bottom', 1]]) {
    const want = pick && ls.slice(dir < 0 ? 0 : i + 1, dir < 0 ? i : undefined).some((l) => l.kind === 'copper' && (l.layer ?? l.name) === pick) ? pick : null;
    let h = 0;
    for (let j = i + dir; j >= 0 && j < ls.length; j += dir) {
      const l = ls[j];
      if (l.kind === 'copper' && (!want || (l.layer ?? l.name) === want)) { out.push({ side, layer: l.layer ?? l.name, net: '', h: +h.toFixed(4), skipped: [] }); break; }
      if (l.kind === 'copper' || l.kind === 'dielectric') h += l.thickness ?? 0;
    }
  }
  return out;
}

/** In words: 'over In1.Cu (GND) · 0.21 mm', both planes of a stripline, the coplanar ground and its gap. */
export function referenceText(refs, coplanarGap = null) {
  const d3 = (v) => String(+(+v).toFixed(3));
  const one = (r) => `${refName(r)} · ${d3(r.h)} mm`;
  let s = !refs?.length ? 'no reference plane' : refs.length === 1 ? `${refs[0].side === 'bottom' ? 'over' : 'under'} ${one(refs[0])}`
    : `between ${one(refs[0])} and ${one(refs[1])}`;
  if (coplanarGap != null) s += ` · ground beside it, g ${d3(coplanarGap)} mm`;
  return s;
}

/**
 * Where an analysis's reference breaks along the route: no plane under the trace, a void or split it crosses (the
 * plane skipped), and a reference change (at a via, or along a layer). [{ type, at, layer, text, count }]
 */
export function referenceBreaks(doc) {
  const out = new Map();
  const add = (key, w) => { const was = out.get(key); if (was) was.count++; else out.set(key, { ...w, count: 1 }); };
  const names = (x) => x.refs.map(refName).join(' + ') || 'none';
  doc.sections.forEach((s, i) => {
    const p = doc.sections[i - 1];
    const cont = p && p.net === s.net && Math.abs(p.s1 - s.s0) < 1e-6;
    const fresh = (f) => s.flags.includes(f) && !(cont && p.flags.includes(f));
    if (fresh('no_ref')) add(`no\n${s.layer}`, { type: 'no_ref', at: s.start, layer: s.layer, text: `no reference plane at ${xy(s.start)}` });
    else if (fresh('plane_gap')) {
      const gap = s.refs.flatMap((r) => r.skipped)[0] ?? null;
      add(`gap\n${gap}`, { type: 'plane_gap', at: s.start, layer: s.layer, plane: gap, text: `crosses a split in ${gap ?? 'a plane'} at ${xy(s.start)}` });
    }
    if (cont && s.refs.length && p.refs.length && names(p) !== names(s)) {
      const via = p.layer !== s.layer;
      add(`chg\n${names(p)}\n${names(s)}`, { type: via ? 'via' : 'ref_change', at: s.start, layer: s.layer,
        text: `${via ? 'plane change at a via' : 'reference changes'}: ${names(p)} → ${names(s)} at ${xy(s.start)}` });
    }
  });
  return [...out.values()];
}

/**
 * The layer view in `el`. Options: `board` (board@1 with the stackup the figures are computed on), `copper`
 * (copper@1), `route` (createAnalyzer options, e.g. `{ worker: url }`: each selected net's route is analysed for its
 * reference planes, and the last one's impedance along it shows under the form; false: the stackup alone),
 * `netInfo` ({net: {auto, pads}}: auto-named nets can be renamed), `rename(net, name)` (async; omit: no ✎),
 * `evaluate(item)` (async; the app's tier-2 figure for one geometry: resolves { z, width, gap?, why? }),
 * `editable` (default true: the Save button), `defaults` ({ target: {single, differential}, tolerance, pair }),
 * `region` and `layer` (the view and the stop to start on: an app keeps them across reloads), `injectCss`.
 * Events: 'layer' (stop), 'segment' ({ track, net, layer, width, length }: the trace clicked), 'net' ({ nets, pairs,
 * layers } | null), 'draft' (the form's value), 'control' (Save: the draft), 'rename' ({ net, name }), 'error'.
 */
export function layerView(el0, { board, copper, route = false, netInfo = {}, rename = null, evaluate = null, editable = true,
  defaults = {}, region = null, layer: startLayer = null, injectCss: inject = true } = {}) {
  const doc = el0.ownerDocument;
  if (inject && doc?.head && !doc.getElementById('bdiCss')) {
    const s = doc.createElement('style');
    s.id = 'bdiCss';
    s.textContent = IMPEDANCE_UI_CSS;
    doc.head.append(s);
  }
  const listeners = new Map();
  const emit = (n, v) => { for (const fn of listeners.get(n) ?? []) fn(v); };
  const stackup = board?.stackup ?? null;
  const stops = layerStops(copper, stackup);
  const order = copper.layers ?? [];
  const names = copper.nets ?? [];
  let destroyed = false;

  const root = el('div', 'bdi-lv', el0);
  root.tabIndex = 0;
  root.setAttribute('aria-label', 'Board layers: ↑ ↓ step through them, click traces to pick their nets, Esc clears, Delete drops the last');
  const main = el('div', 'bdi-lv-main', root);
  const stepper = el('div', 'bdi-lv-steps', main);
  const up = btn('bdi-lv-step', stepper, '▲', 'Layer above (↑)');
  const list = el('ol', 'bdi-lv-stack', stepper);
  const down = btn('bdi-lv-step', stepper, '▼', 'Layer below (↓)');
  const stageEl = el('div', 'bdi-lv-stage', main);
  const caption = el('div', 'bdi-lv-caption', stageEl);
  const tools = el('div', 'bdi-lv-tools', stageEl);
  const countEl = el('span', 'bdi-lv-count', tools);
  const clearBtn = btn('bdi-gbtn bdi-lv-clear', tools, 'Clear', 'Clear the selection (Esc)');
  const zoomSelBtn = icon(btn('bdi-gbtn bdi-lv-zoomsel', tools, '', 'Zoom to the selection'), 'target');
  const fitBtn = btn('bdi-gbtn bdi-lv-fit', tools, '⤢', 'The whole board');
  // the side always takes its room, so a pick never resizes the stage (a resize would move the view)
  const side = el('div', 'bdi-lv-side', root);
  const finderEl = el('div', 'bdi-lv-find', side);
  const hint = el('div', 'bdi-lv-hint', side, 'Click traces to pick their nets · again to drop one · drag to pan');
  const unitsEl = el('div', 'bdi-lv-units', side);
  const form = el('div', 'bdi-ctl', side);
  form.hidden = true;
  const routeBox = route ? el('details', 'bdi-lv-route', side) : null;
  if (routeBox) { el('summary', null, routeBox, 'Z along the route'); routeBox.hidden = true; }

  // ---- the stage: one content per copper layer (scrubbing changes opacities only: no re-render)
  const bounds = copper.bounds ?? boundsOf(copper);
  // the stage takes the board's shape (within the CSS's min / max heights)
  stageEl.style.aspectRatio = `${(bounds.maxX - bounds.minX).toFixed(3)} / ${(bounds.maxY - bounds.minY).toFixed(3)}`;
  const stage = createStage(stageEl, { bounds, injectCss: inject, clickMaxMs: CLICK_MS, dblclickFit: false, region });
  const contents = order.map((l) => copperContent(copper, { layers: [l], zoneAlpha: 0.45, alpha: 1 }));
  const dielectric = draw((g) => {
    g.fillStyle = getComputedStyle(root).getPropertyValue('--bdi-diel') || '#e9ddb8';
    g.globalAlpha = 0.55;
    g.fillRect(bounds.minX, bounds.minY, bounds.maxX - bounds.minX, bounds.maxY - bounds.minY);
  }, bounds);
  // bottom first: a layer above is drawn over the ones below it, ghosted
  stage.setScene([{ layers: [...contents.map((content) => ({ content, opacity: 0 })).reverse(), { content: dielectric, opacity: 0 }] }]);
  const hl = createHighlight(stage, copper, { dim: 0.25 });
  const routeOpts = typeof route === 'object' ? route : {};
  const panel = routeBox ? impedancePanel(routeBox, { board, copper, stage: null, pick: false, group: false, injectCss: false, analyzer: routeOpts }) : null;
  // the reference planes along each selected route: tier 1 is enough to find them
  const refAn = route ? createAnalyzer({ board, copper, ...routeOpts }) : null;
  const index = copperHitIndex(copper);

  let at = startLayer ? stops.findIndex((s) => s.layer === startLayer) : -1;
  if (at < 0) at = Math.max(0, stops.findIndex((s) => s.kind === 'copper'));
  let sel = []; // nets, in the order they were added
  let pairs = []; // [p, n] differential pairs within the selection
  const hits = new Map(); // net -> what was clicked on it
  const split = new Set(); // pairs the user took apart: not paired again by themselves
  const partners = new Map(); // net -> { net, why, info } | null
  const refsBy = new Map(); // refKey -> { state: 'busy' | 'ready' | 'error', doc?, error? }
  const chosen = new Map(); // unit key -> a reference layer the user picked
  let tintKey = null; // the unit whose reference planes are tinted on the board
  let draft = null;

  const unitKey = (u) => u.join(' ');
  const refKey = (u) => `${unitKey(u)}@${chosen.get(unitKey(u)) ?? ''}`;
  const units = () => {
    const out = [], done = new Set();
    for (const n of sel) {
      if (done.has(n)) continue;
      const u = pairs.find((p) => p.includes(n)) ?? [n];
      for (const x of u) done.add(x);
      out.push(u);
    }
    return out;
  };

  function drawStack() {
    list.replaceChildren();
    const on = new Set(layersOf(sel));
    stops.forEach((s, i) => {
      const li = el('li', `bdi-lv-stop is-${s.kind}${i === at ? ' is-on' : ''}`, list);
      li.dataset.i = String(i);
      if (s.kind === 'copper') {
        li.style.setProperty('--c', layerColor(copper, s.layer));
        li.textContent = s.layer.replace(/\.Cu$/, '');
        li.title = `${s.layer}${s.thickness ? ` · ${mm(s.thickness)} mm copper` : ''}`;
        if (on.has(s.layer)) li.classList.add('has-net');
      } else {
        li.textContent = s.epsilon_r ? `εr ${(+s.epsilon_r).toFixed(2)}` : '';
        li.title = [s.dielectric || 'dielectric', s.material, s.thickness ? `${mm(s.thickness)} mm` : null, s.epsilon_r ? `εr ${s.epsilon_r}` : null].filter(Boolean).join(' · ');
      }
      if (s.kind === 'copper') li.setAttribute('role', 'button'); // a dielectric is a step (▲ ▼, ↑ ↓), not a tap target
      li.setAttribute('aria-current', i === at ? 'true' : 'false');
    });
    up.disabled = at <= 0;
    down.disabled = at >= stops.length - 1;
  }

  function show() {
    const s = stops[at];
    const k = s.kind === 'copper' ? order.indexOf(s.layer) : stops.slice(0, at).filter((x) => x.kind === 'copper').length - 1;
    order.forEach((l, i) => {
      const slot = order.length - 1 - i; // the scene is bottom first
      const o = s.kind === 'copper' ? (i < k ? GHOST : i === k ? 1 : 0) : (i <= k ? GHOST : 0);
      stage.setLayer(0, slot, { opacity: o });
    });
    stage.setLayer(0, order.length, { opacity: s.kind === 'dielectric' ? 1 : 0 });
    caption.textContent = s.kind === 'copper' ? s.layer : (s.epsilon_r ? `${s.dielectric || 'dielectric'} · εr ${(+s.epsilon_r).toFixed(2)}${s.thickness ? ` · ${mm(s.thickness)} mm` : ''}` : 'dielectric');
    hl.focus?.(s.kind === 'copper' ? s.layer : null);
    drawStack();
    for (const c of unitsEl.querySelectorAll('.bdi-ctl-layer')) c.classList.toggle('is-on', c.textContent === s.layer);
    emit('layer', { ...s, index: at });
  }

  function step(d) {
    const i = Math.min(stops.length - 1, Math.max(0, at + d));
    if (i !== at) { at = i; show(); }
  }

  const onLayer = () => (stops[at].kind === 'copper' ? stops[at].layer : null);
  const pickAt = (x, y) => {
    const l = onLayer();
    if (!l) return null;
    return index.all(x, y, 4 * stage.mmPerPx()).find((sh) => sh.data.layer === l || sh.data.layers?.includes(l))?.data ?? null;
  };
  const layersOf = (nets) => order.filter((l) => (copper.tracks ?? []).some((t) => t.layer === l && nets.includes(t.net))
    || (copper.pads ?? []).some((p) => p.layers.includes(l) && nets.includes(p.net))
    || (copper.zones ?? []).some((z) => z.layer === l && nets.includes(z.net)));

  // ---- the selection
  function partnerOf(net) {
    if (!partners.has(net)) {
      const byName = pairOf(net, { board, names });
      const beside = !byName && defaults.pair !== false ? parallelPartner(copper, net) : null;
      partners.set(net, byName ? { net: byName, why: 'name' } : beside ? { net: beside.net, why: 'copper', info: beside } : null);
    }
    return partners.get(net);
  }
  const loose = (n) => sel.includes(n) && !pairs.some((p) => p.includes(n));
  /** A net added next to its partner (by name, or running beside it) makes a pair, unless the user split them. */
  function autoPair(net) {
    const p = partnerOf(net)?.net;
    const mate = p && p !== net && loose(p) ? p : sel.find((n) => n !== net && loose(n) && partnerOf(n)?.net === net) ?? null;
    if (mate && loose(net) && !split.has(pairKey(net, mate))) pairs.push(pairOrder(net, mate));
  }
  function add(net, hit = null) {
    if (!net || sel.includes(net)) return;
    sel.push(net);
    if (hit) hits.set(net, hit);
    autoPair(net);
  }
  function remove(net) {
    sel = sel.filter((n) => n !== net);
    hits.delete(net);
    pairs = pairs.filter((p) => !p.includes(net));
  }

  function changed() {
    const layers = layersOf(sel);
    hl.set({ nets: [...sel] });
    hl.focus?.(onLayer());
    if (tintKey && !units().some((u) => unitKey(u) === tintKey)) tintKey = null;
    applyTint();
    drawStack();
    const n = sel.length;
    countEl.textContent = n ? `${n} net${n === 1 ? '' : 's'}` : '';
    countEl.hidden = !n;
    clearBtn.hidden = !n;
    zoomSelBtn.disabled = !n;
    if (n) root.dataset.picked = 'net'; else delete root.dataset.picked;
    emit('net', n ? { nets: [...sel], pairs: pairs.map((p) => [...p]), layers } : null);
    buildUnits();
    compute(true);
    runRefs();
    if (panel) {
      const us = units();
      const last = us[us.length - 1];
      routeBox.hidden = !last;
      if (last) panel.select({ nets: last, hit: hits.get(last[0]) ?? null, target: routeTarget(last) })?.catch?.(() => {});
    }
  }

  // ---- reference planes
  function overrideFor(layer, pick) {
    const i = order.indexOf(layer), j = order.indexOf(pick);
    return i < 0 || j < 0 || i === j ? null : j < i ? { refTop: pick } : { refBottom: pick };
  }

  let refsBusy = false;
  async function runRefs() {
    if (!refAn || refsBusy) return;
    refsBusy = true;
    try {
      for (;;) {
        const u = units().find((x) => !refsBy.has(refKey(x)));
        if (!u || destroyed) break;
        const key = refKey(u);
        refsBy.set(key, { state: 'busy' });
        const pick = chosen.get(unitKey(u));
        const g = routedOn(copper, u[0]);
        const ov = pick && g ? overrideFor(g.layer, pick) : null;
        try {
          const d = await refAn.analyze(u.length === 2 ? u : u[0], { solver: 'closedform', step: 0.5, ...(ov && { overrides: { net: ov } }) });
          refsBy.set(key, { state: 'ready', doc: d });
        } catch (err) {
          refsBy.set(key, { state: 'error', error: String(err?.message ?? err) });
        }
        if (destroyed) break;
        const box = [...unitsEl.querySelectorAll('.bdi-lv-unit')].find((b) => b.dataset.nets === unitKey(u));
        if (box) fillRef(box, u);
        if (tintKey === unitKey(u)) applyTint();
        compute(true);
      }
    } finally { refsBusy = false; }
  }

  /** A unit's reference: from its route's main section when analysed, else the stackup's nearest copper. */
  function refInfo(u) {
    const r = refsBy.get(refKey(u));
    const sec = r?.doc ? mainSection(r.doc) : null;
    if (sec) return { refs: sec.refs.map((x) => (x.net ? x : { ...x, net: zoneNetAt(copper, x.layer, sec.start) })), coplanar_gap: minOf(sec.geometry.coplanar_gap), section: sec, breaks: referenceBreaks(r.doc), from: 'route', state: 'ready' };
    const g = routedOn(copper, u[0]);
    return { refs: g ? stackupRefs(stackup, g.layer, chosen.get(unitKey(u)) || null) : [], coplanar_gap: null, section: null, breaks: [],
      from: 'stackup', state: r?.state ?? (refAn ? 'busy' : null), error: r?.error ?? null };
  }
  const planesOf = (u) => refInfo(u).refs.map((r) => ({ layer: r.layer, net: r.net || null }));
  function applyTint(u = null) {
    const t = u ?? (tintKey ? units().find((x) => unitKey(x) === tintKey) : null);
    hl.planes(t ? planesOf(t) : []);
  }

  function fillRef(box, u) {
    const info = refInfo(u);
    const ref = box.querySelector('.bdi-lv-ref');
    const xs = ref.querySelector('.bdi-lv-xs');
    if (info.section) crossSection(xs, info.section, { board, width: 150, height: 64 });
    else xs.replaceChildren(el('span', 'bdi-lv-xs-wait', null, info.state === 'busy' ? '…' : ''));
    xs.title = info.section ? 'Cross-section of the line where most of it runs; the reference planes highlighted' : info.error ?? '';
    const txt = ref.querySelector('.bdi-lv-reftext');
    txt.textContent = referenceText(info.refs, info.coplanar_gap);
    txt.title = info.from === 'route' ? 'Found under the trace along its route' : info.state === 'busy' ? 'The nearest copper in the stackup: reading the route…' : 'The nearest copper in the stackup';
    txt.dataset.from = info.from;
    const pickSel = ref.querySelector('.bdi-lv-refsel');
    const layer = info.section?.layer ?? routedOn(copper, u[0])?.layer;
    const auto = refsBy.get(`${unitKey(u)}@`)?.doc;
    const found = auto ? mainSection(auto)?.refs.map((r) => r.layer).join(' + ') : '';
    pickSel.replaceChildren();
    const o0 = el('option', null, pickSel, found ? `auto: ${found}` : 'auto');
    o0.value = '';
    for (const l of order) if (l !== layer) { const o = el('option', null, pickSel, l); o.value = l; }
    pickSel.value = chosen.get(unitKey(u)) ?? '';
    const warn = box.querySelector('.bdi-lv-warns');
    warn.replaceChildren();
    for (const w of info.breaks.slice(0, 3)) {
      const row = el('div', `bdi-lv-warn is-${w.type}`, warn, `⚠ ${w.text}${w.count > 1 ? ` (×${w.count})` : ''}`);
      const go = btn('bdi-gbtn bdi-lv-show', row, 'show', 'Show the spot on the board');
      go.addEventListener('click', () => showSpot(u, w));
    }
    if (info.breaks.length > 3) el('div', 'bdi-lv-warn', warn, `+${info.breaks.length - 3}`).title = info.breaks.slice(3).map((w) => w.text).join('\n');
  }

  /** A break in the reference: its layer on show, the planes tinted, the spot ringed; the view pans only if it is off screen. */
  function showSpot(u, w) {
    const i = stops.findIndex((s) => s.layer === w.layer);
    if (i >= 0 && i !== at) { at = i; show(); }
    tintKey = unitKey(u);
    for (const b of unitsEl.querySelectorAll('.bdi-lv-tint')) b.setAttribute('aria-pressed', String(b.closest('.bdi-lv-unit')?.dataset.nets === tintKey));
    applyTint();
    hl.rings([w.at]);
    const [px, py] = stage.toScreen(w.at[0], w.at[1]);
    const pane = stage.panes[0]?.el;
    if (pane && (px < 0 || py < 0 || px > pane.clientWidth || py > pane.clientHeight)) stage.setView({ ...stage.getView(), cx: w.at[0], cy: w.at[1] });
  }

  function unitRow(u) {
    const key = unitKey(u);
    const box = el('div', 'bdi-lv-unit');
    box.dataset.nets = key;
    const head = el('div', 'bdi-ctl-head', box);
    const name = el('strong', 'bdi-ctl-net', head, `${u.length === 2 ? '⇄ ' : ''}${u.join(' ')}`);
    name.title = u.map((n) => `${n}${netInfo[n]?.pads != null ? ` · ${netInfo[n].pads} pads` : ''}${netInfo[n]?.auto ? ' · named here (no net name in the files)' : ''}`).join('\n');
    if (rename && u.length === 1 && netInfo[u[0]]?.auto) {
      const b = btn('bdi-gbtn bdi-ctl-rename', head, '✎', 'Rename net');
      b.addEventListener('click', async () => {
        const v = typeof prompt === 'function' ? prompt('Net name', u[0]) : null;
        if (!v || v.trim() === u[0]) return;
        try { await rename(u[0], v.trim()); emit('rename', { net: u[0], name: v.trim() }); } catch (err) { emit('error', err); }
      });
    }
    const g = routedOn(copper, u[0]);
    if (g) {
      const c = btn(`bdi-chip bdi-ctl-layer${g.layer === onLayer() ? ' is-on' : ''}`, head, g.layer, `Mostly on ${g.layer}: show it`);
      c.addEventListener('click', () => { const i = stops.findIndex((s) => s.layer === g.layer); if (i >= 0) { at = i; show(); } });
    }
    if (u.length === 2) {
      const b = btn('bdi-gbtn bdi-ctl-unpair', head, '✂', 'Not a pair: two nets');
      b.addEventListener('click', () => { split.add(pairKey(u[0], u[1])); pairs = pairs.filter((p) => p !== pairs.find((q) => unitKey(q) === key)); changed(); });
    } else {
      const p = partnerOf(u[0]);
      const w = btn('bdi-gbtn bdi-ctl-pairwith', head, '⇄…', `Pair ${u[0]} with a net: type its name`);
      w.addEventListener('click', () => {
        if (box.querySelector('.bdi-np')) { box.querySelector('.bdi-np').remove(); return; }
        const np = netPicker(head, { source: allNets(), first: () => (p ? [p.net] : []), exclude: () => [u[0], ...sel.filter((n) => !loose(n))],
          placeholder: 'pair with…', label: `Pair ${u[0]} with`, keepOpen: false,
          onPick: (n) => { if (!sel.includes(n)) sel.push(n); split.delete(pairKey(u[0], n)); if (loose(u[0]) && loose(n)) pairs.push(pairOrder(u[0], n)); changed(); } });
        np.focus();
      });
      if (p && !sel.includes(p.net)) {
        const b = btn('bdi-gbtn bdi-ctl-partner', head, `⇄ +${p.net}`, p.why === 'name' ? `Its pair by name: add ${p.net}`
          : `Runs beside it at ${mm(p.info.gap)} mm for ${f1(p.info.length)} mm (${Math.round(100 * p.info.share)} % of its route on ${p.info.layer}): add it as its pair`);
        b.addEventListener('click', () => { split.delete(pairKey(u[0], p.net)); add(p.net); changed(); });
      }
    }
    const x = btn('bdi-gbtn bdi-ctl-drop', head, '✕', `Drop ${u.join(' ')}`);
    x.addEventListener('click', () => { for (const n of u) remove(n); changed(); });
    const ref = el('div', 'bdi-lv-ref', box);
    const xs = el('div', 'bdi-lv-xs', ref);
    el('span', 'bdi-lv-reftext', ref);
    const tint = icon(btn('bdi-gbtn bdi-lv-tint', ref, '', 'Tint its reference plane on the board'), 'plane');
    tint.setAttribute('aria-pressed', String(tintKey === key));
    tint.addEventListener('click', () => {
      tintKey = tintKey === key ? null : key;
      for (const b of unitsEl.querySelectorAll('.bdi-lv-tint')) b.setAttribute('aria-pressed', String(b.closest('.bdi-lv-unit')?.dataset.nets === tintKey));
      applyTint();
    });
    for (const t of [xs, tint]) {
      t.addEventListener('pointerenter', () => applyTint(u));
      t.addEventListener('pointerleave', () => applyTint());
    }
    const pickSel = el('select', 'bdi-override bdi-lv-refsel', ref);
    pickSel.title = 'Reference plane: auto (found under the trace), or pick the layer';
    pickSel.setAttribute('aria-label', 'Reference plane');
    pickSel.addEventListener('change', () => {
      if (pickSel.value) chosen.set(key, pickSel.value); else chosen.delete(key);
      fillRef(box, u);
      if (tintKey === key) applyTint();
      compute(true);
      runRefs();
    });
    el('div', 'bdi-lv-warns', box);
    fillRef(box, u);
    return box;
  }

  function buildUnits() {
    unitsEl.replaceChildren();
    const us = units();
    hint.hidden = us.length > 0;
    form.hidden = !us.length;
    for (const u of us) unitsEl.append(unitRow(u));
    // two loose nets the user means as a pair: the last two picked
    const free = sel.filter(loose);
    if (free.length >= 2) {
      const [a, b] = free.slice(-2);
      const p = btn('bdi-gbtn bdi-ctl-pairwith', unitsEl, `⇄ ${a} ${b}`, `Pair ${a} and ${b}: a differential pair`);
      p.addEventListener('click', () => { split.delete(pairKey(a, b)); pairs.push(pairOrder(a, b)); changed(); });
    }
  }

  let entries = null;
  const allNets = () => (entries ??= netEntries(copper, netInfo));
  netPicker(finderEl, { source: allNets(), onPick: (n) => { add(n); changed(); }, exclude: () => sel,
    placeholder: '+ net by name', label: 'Add a net by name' });

  // ---- picks
  const tip = el('div', 'bdi-tip', stage.root);
  tip.hidden = true;
  stage.on('move', (e) => {
    const d = pickAt(e.x, e.y);
    if (!d) { tip.hidden = true; return; }
    tip.textContent = `${d.net || '(no net)'}${d.width ? ` · ${mm(d.width)} mm` : ''}${d.net ? (sel.includes(d.net) ? ' · click: drop' : ' · click: add') : ''}`;
    tip.hidden = false;
    const r = stage.root.getBoundingClientRect();
    tip.style.left = `${e.event.clientX - r.left + 12}px`;
    tip.style.top = `${e.event.clientY - r.top + 12}px`;
  });
  stage.on('leave', () => { tip.hidden = true; });
  // a click (short, still: view2d's clickMaxMs) toggles the net under it; drags and long presses only pan
  stage.on('click', (e) => {
    const d = pickAt(e.x, e.y);
    if (!d?.net) return;
    if (d.kind === 'track') {
      const t = copper.tracks[d.track];
      emit('segment', { track: d.track, net: t.net, layer: t.layer, width: t.width, length: len(t) });
    }
    if (sel.includes(d.net)) remove(d.net); else add(d.net, d);
    changed();
  });

  // ---- the form: one for the whole selection
  const row = el('div', 'bdi-ctl-row', form);
  const structSel = el('select', 'bdi-override bdi-ctl-structure', row);
  structSel.title = 'Structure: auto (from the stackup and the copper around each trace), or set it';
  structSel.setAttribute('aria-label', 'Structure');
  for (const [v, t, w] of STRUCTURE_OPTIONS) { const o = el('option', null, structSel, t); o.value = v; o.title = w; }
  const singleBox = el('span', 'bdi-ctl-single', row);
  singleBox.append('— ');
  const tgtIn = el('input', 'bdi-pattern bdi-ctl-target', singleBox);
  tgtIn.inputMode = 'decimal'; tgtIn.value = String(defaults.target?.single ?? 50); tgtIn.setAttribute('aria-label', 'Target ohms');
  singleBox.append(' Ω');
  const diffBox = el('span', 'bdi-ctl-diff', row);
  diffBox.append('⇄ ');
  const diffIn = el('input', 'bdi-pattern bdi-ctl-target-diff', diffBox);
  diffIn.inputMode = 'decimal'; diffIn.value = String(defaults.target?.differential ?? 90); diffIn.setAttribute('aria-label', 'Differential target ohms');
  diffBox.append(' Ω');
  row.append(' ± ');
  const tolIn = el('input', 'bdi-pattern bdi-ctl-tol', row);
  tolIn.inputMode = 'decimal'; tolIn.value = String(defaults.tolerance ?? 10); tolIn.setAttribute('aria-label', 'Tolerance percent');
  row.append(' %');
  const outEl = el('div', 'bdi-ctl-out', form);
  let okBtn = null;
  if (editable) {
    okBtn = btn('bdi-gbtn bdi-apply bdi-ctl-ok', form, '✓ Save', 'Make them controlled: one target per net or pair, at its layer and width as routed, with this target and structure');
    okBtn.addEventListener('click', () => { if (draft?.items.length) emit('control', structuredClone(draft)); });
  }
  for (const i of [tgtIn, diffIn, tolIn]) i.addEventListener('input', () => compute());
  structSel.addEventListener('change', () => compute(true));

  /** The geometry an item is computed on: its route's main section when analysed, else its tracks. */
  function geometryOf(u) {
    const info = refInfo(u);
    const sec = info.section;
    const pairGap = (layer) => (u.length === 2 ? parallelPartner(copper, u[0], { layer, share: 0 })?.gap ?? null : null);
    if (sec) return { layer: sec.layer, width: sec.geometry.width, gap: u.length === 2 ? sec.geometry.gap ?? pairGap(sec.layer) : null,
      structure: sec.structure, coplanar_gap: info.coplanar_gap, refs: info.refs };
    const g = routedOn(copper, u[0]);
    return g ? { layer: g.layer, width: g.width, gap: pairGap(g.layer), structure: null, coplanar_gap: null, refs: info.refs } : null;
  }

  function referenceOf(refs, picked) {
    const side = (s) => { const r = refs.find((x) => x.side === s); return r ? { layer: r.layer, net: r.net || '', h: r.h } : null; };
    return { top: side('top'), bottom: side('bottom'), chosen: !!picked };
  }

  function tier1(it) {
    if (!stackup || !(it.target > 0) || it.width == null) return null;
    const coplanar = it.structure?.startsWith('coplanar');
    const opts = coplanar && it.coplanar_gap ? { coplanarGap: it.coplanar_gap } : {};
    const lay = { layer: it.layer, width: it.width, gap: it.gap, ref_top: it.reference.top?.layer, ref_bottom: it.reference.bottom?.layer };
    const t = { kind: it.kind, target: it.target, tolerance_pct: it.tolerance_pct, structure: coplanar && !it.coplanar_gap ? undefined : it.structure ?? undefined, layers: [lay] };
    const run = (tt, o) => [evaluateTarget(stackup, tt, o)[0], evaluateTarget(stackup, { ...tt, layers: [{ ...tt.layers[0], width: null }] }, o)[0]];
    try {
      let now, want, approx = '';
      try {
        [now, want] = run(t, opts);
      } catch (err) {
        // tier 1 has no model for it (a coplanar pair, an embedded microstrip): the layer's plain structure
        [now, want] = run({ ...t, structure: undefined, layers: [{ layer: it.layer, width: it.width, gap: it.gap }] }, {});
        approx = ` · tier 1 has no model for this one: as ${now.model.replace('_', ' ')}`;
      }
      const off = (100 * (now.value - it.target)) / it.target;
      const v = Math.abs(off) <= it.tolerance_pct / 2 ? 'ok' : Math.abs(off) <= it.tolerance_pct ? 'warn' : 'bad';
      return { z: now.value, model: now.model, width_for_target: want.width, off, v, approx };
    } catch (err) {
      return { error: String(err?.message ?? err) };
    }
  }

  let t2Run = 0;
  /** The form's target for a unit, which the route below is held to. */
  function routeTarget(u) {
    const value = Number(u.length === 2 ? diffIn.value : tgtIn.value);
    return value > 0 ? { value, tolerance_pct: Number(tolIn.value) > 0 ? Number(tolIn.value) : undefined } : null;
  }

  function compute(asTier2 = false) {
    const us = units();
    const forced = structSel.value || null;
    const targetS = Number(tgtIn.value), targetD = Number(diffIn.value), tolerance = Number(tolIn.value);
    singleBox.hidden = !us.some((u) => u.length === 1);
    diffBox.hidden = !us.some((u) => u.length === 2);
    const items = [];
    const found = new Set();
    for (const u of us) {
      const g = geometryOf(u);
      if (!g) continue;
      const kind = u.length === 2 ? 'differential' : 'single';
      const auto = AS_TARGET[g.structure] ?? null;
      found.add(auto);
      items.push({ nets: [...u], kind, layer: g.layer, structure: forced ?? auto, target: kind === 'differential' ? targetD : targetS,
        tolerance_pct: tolerance, width: g.width, gap: kind === 'differential' ? g.gap : null, coplanar_gap: g.coplanar_gap,
        anchors: anchorsOf(u), reference: referenceOf(g.refs, chosen.get(unitKey(u))) });
    }
    const one = found.size === 1 ? [...found][0] : null;
    structSel.options[0].textContent = !forced && one ? `auto: ${SHORT[one]}` : 'auto';
    // one figure per distinct geometry: different widths (or layers, or planes) give different Z
    const geos = new Map();
    for (const it of items) {
      const k = JSON.stringify([it.kind, it.layer, it.width, it.gap, it.structure, it.coplanar_gap, it.reference.top?.layer, it.reference.bottom?.layer]);
      if (!geos.has(k)) geos.set(k, { ...it, items: [] });
      geos.get(k).items.push(it);
    }
    outEl.replaceChildren();
    const rows = [];
    for (const g of geos.values()) {
      g.tier1 = tier1(g);
      for (const it of g.items) if (g.tier1?.z != null) it.tier1 = { z: g.tier1.z, model: g.tier1.model, width_for_target: g.tier1.width_for_target };
      const r = el('div', 'bdi-ctl-geo', outEl);
      r.title = g.items.map((it) => it.nets.join(' ')).join(', ');
      el('span', 'bdi-chip bdi-ctl-where', r, `${g.kind === 'differential' ? '⇄ ' : ''}${g.layer} w ${mm(g.width)}${g.gap != null ? ` s ${mm(g.gap)}` : ''}${g.items.length > 1 ? ` ×${g.items.length}` : ''}`);
      const z = el('span', 'bdi-z bdi-ctl-z', r, g.tier1?.z != null ? `≈ ${f1(g.tier1.z)} Ω` : g.tier1?.error ? '✗' : '–');
      if (g.tier1?.z != null) {
        z.dataset.v = g.tier1.v;
        z.title = `${g.kind === 'differential' ? 'Zdiff' : 'Z0'} on ${g.layer} (tier 1, ${g.tier1.model}) · ${g.tier1.off >= 0 ? '+' : ''}${f1(g.tier1.off)} % of ${g.target} Ω${g.tier1.approx}`;
        el('span', 'bdi-ctl-sug', r, Math.abs(g.tier1.width_for_target - g.width) > 1e-4 ? `${g.target} Ω at w ${mm(g.tier1.width_for_target)} mm` : '✓ w')
          .title = `The width for ${g.target} Ω on this stackup (tier 1)${g.gap != null ? ` at s ${mm(g.gap)} mm` : ''}; routed: w ${mm(g.width)} mm`;
      } else if (g.tier1?.error) z.title = g.tier1.error;
      g.t2 = el('span', 'bdi-ctl-t2', r);
      rows.push(g);
    }
    const figs = rows.filter((g) => g.tier1?.z != null);
    if (figs.length > 1) {
      const worst = figs.reduce((a, b) => (Math.abs(b.tier1.off) > Math.abs(a.tier1.off) ? b : a));
      const zs = figs.map((g) => g.tier1.z);
      const s = el('div', 'bdi-ctl-range', outEl, `≈ ${f1(Math.min(...zs))}–${f1(Math.max(...zs))} Ω · worst ${f1(worst.tier1.z)} Ω (${worst.tier1.off >= 0 ? '+' : ''}${f1(worst.tier1.off)} %)`);
      s.dataset.v = worst.tier1.v;
      s.title = 'Tier 1 across the selection: the range, and the figure furthest from its target';
      outEl.prepend(s);
    }
    if (rows.length) outEl.prepend(Object.assign(el('div', 'bdi-ctl-tier', null, 'closed form'), { title: 'Tier 1: closed-form formulas, as you type' }));
    if (okBtn) {
      okBtn.disabled = !items.length;
      okBtn.textContent = `✓ Save${items.length > 1 ? ` ${items.length}` : ''}`;
    }
    draft = items.length ? { ...(items.length === 1 ? items[0] : {}), nets: [...sel], pairs: pairs.map((p) => [...p]), structure: forced,
      target: targetS, target_differential: targetD, tolerance_pct: tolerance, items } : null;
    if (draft && items.length === 1) draft.target = items[0].target;
    emit('draft', draft);
    if (panel && !routeBox.hidden) {
      const lastU = us[us.length - 1];
      clearTimeout(compute.routeTimer);
      if (lastU) compute.routeTimer = setTimeout(() => panel.setTarget(routeTarget(lastU))?.catch?.(() => {}), 400);
    }
    if (evaluate && asTier2 !== null && rows.length) {
      const mine = ++t2Run;
      clearTimeout(compute.timer);
      compute.timer = setTimeout(() => {
        for (const g of rows.slice(0, MAX_T2)) {
          const it = { ...g.items[0], width: g.width };
          g.t2.textContent = ' …';
          Promise.resolve(evaluate(structuredClone(it))).then((r) => {
            if (mine !== t2Run || !r) return;
            g.t2.replaceChildren(' ', Object.assign(el('span', 'bdi-ctl-t2z', null, r.z != null ? `${f1(r.z)} Ω` : '–'), { title: r.why ?? 'Field solver (tier 2): the final figure' }));
            if (r.width != null) g.t2.append(` → w ${mm(r.width)}${r.gap != null ? ` s ${mm(r.gap)}` : ''} mm`);
            if (r.note) g.t2.title = r.note;
          }).catch((err) => { if (mine === t2Run) { g.t2.textContent = ''; emit('error', err); } });
        }
      }, 350);
    }
  }

  function anchorsOf(nets) {
    const out = [];
    for (const n of nets) {
      const hit = hits.get(n);
      const pad = (copper.pads ?? []).find((q) => q.net === n);
      const t = hit?.kind === 'track' ? copper.tracks[hit.track] : pad ? null : (copper.tracks ?? []).filter((x) => x.net === n).sort((a, b) => len(b) - len(a))[0];
      if (t) { const q = trackPath(t)[0]; out.push({ net: n, layer: t.layer, x: q[0], y: q[1] }); }
      else if (pad) out.push({ net: n, layer: pad.layers[0], x: pad.at[0], y: pad.at[1] });
    }
    return out;
  }

  function selectionBounds() {
    const b = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
    const put = (x, y, r = 0) => { b.minX = Math.min(b.minX, x - r); b.maxX = Math.max(b.maxX, x + r); b.minY = Math.min(b.minY, y - r); b.maxY = Math.max(b.maxY, y + r); };
    const nets = new Set(sel);
    for (const t of copper.tracks ?? []) if (nets.has(t.net)) for (const p of trackPath(t)) put(p[0], p[1], t.width / 2);
    for (const p of copper.pads ?? []) if (nets.has(p.net)) for (const r of p.polygons ?? []) for (const q of r) put(q[0], q[1]);
    if (!Number.isFinite(b.minX)) for (const z of copper.zones ?? []) if (nets.has(z.net)) for (const f of z.fill) for (const q of f.outline) put(q[0], q[1]);
    return Number.isFinite(b.minX) ? b : null;
  }
  const zoomToSelection = () => { const b = selectionBounds(); if (b) stage.zoomTo(b, { minMm: 2 }); };

  if (panel) panel.on('result', () => compute(true));

  // ---- keys, the stepper, the tools
  up.addEventListener('click', () => step(-1));
  down.addEventListener('click', () => step(1));
  list.addEventListener('click', (e) => { const li = e.target.closest('li[data-i]'); if (li) { at = Number(li.dataset.i); show(); } });
  clearBtn.addEventListener('click', () => api.clear());
  zoomSelBtn.addEventListener('click', zoomToSelection);
  fitBtn.addEventListener('click', () => stage.fit());
  root.addEventListener('keydown', (e) => {
    if (e.target.closest?.('input, select, textarea')) return;
    if (e.key === 'ArrowUp') { e.preventDefault(); step(-1); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); step(1); }
    else if (e.key === 'Escape') api.clear();
    else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); api.removeLast(); }
  });
  // rings and markers are screen-sized: redraw on a zoom only (an invalidate asks for a frame, which emits 'view')
  let drawnAt = null;
  stage.on('view', ({ view: v }) => { if (v.s !== drawnAt) { drawnAt = v.s; hl.invalidate(); } });
  show();
  changed();

  const api = {
    root,
    stage,
    stops,
    get index() { return at; },
    /** The stop on show ({kind, layer?, ...}). */
    get stop() { return { ...stops[at], index: at }; },
    get selection() { return sel.length ? { nets: [...sel], pairs: pairs.map((p) => [...p]), layers: layersOf(sel) } : null; },
    get draft() { return draft ? structuredClone(draft) : null; },
    /** The route analysis panel (impedancePanel), with `route`. */
    get panel() { return panel; },
    /** What an app keeps across reloads: the region on show (null: fitted) and the copper layer. */
    get state() { return { region: stage.getRegion(), layer: stops[at].layer ?? null }; },
    /** Show a stop by index, or a copper layer by id. */
    show(i) { const k = typeof i === 'string' ? stops.findIndex((s) => s.layer === i) : i; if (k >= 0 && k < stops.length) { at = k; show(); } },
    step,
    /** Select exactly these nets (two: as a pair unless `pair: false`). */
    pick(nets, { pair = nets.length === 2 } = {}) {
      sel = []; pairs = []; hits.clear();
      for (const n of nets) add(n);
      if (pair && nets.length === 2 && !pairs.length) pairs.push(pairOrder(nets[0], nets[1]));
      if (!pair) pairs = [];
      changed();
    },
    add(net) { add(net); changed(); },
    remove(net) { remove(net); changed(); },
    toggle(net) { if (sel.includes(net)) remove(net); else add(net); changed(); },
    removeLast() { if (sel.length) { remove(sel[sel.length - 1]); changed(); } },
    /** The net under a board point on the layer on show (what a click there would toggle), or null. */
    netAt(x, y) { return pickAt(x, y)?.net || null; },
    /** Toggle what is under a board point on the layer on show, as a click there. */
    pickAt(x, y) { const d = pickAt(x, y); if (d?.net) { if (sel.includes(d.net)) remove(d.net); else add(d.net, d); changed(); } return d; },
    /** Pair two selected nets, or take a pair apart. */
    pair(a, b) { if (loose(a) && loose(b) && a !== b) { split.delete(pairKey(a, b)); pairs.push(pairOrder(a, b)); changed(); } },
    unpair(a) { const p = pairs.find((q) => q.includes(a)); if (p) { split.add(pairKey(p[0], p[1])); pairs = pairs.filter((q) => q !== p); changed(); } },
    /** A net's (or pair's) reference planes as shown: { refs, coplanar_gap, breaks, from: 'route' | 'stackup', state }. */
    reference(nets) { const u = units().find((x) => x.includes(nets[0] ?? nets)); return u ? refInfo(u) : null; },
    /** Pick the reference layer of a selected net or pair ('' or null: as found). */
    setReference(nets, layer) {
      const u = units().find((x) => x.includes(nets[0] ?? nets));
      if (!u) return;
      if (layer) chosen.set(unitKey(u), layer); else chosen.delete(unitKey(u));
      changed();
    },
    /** Tint a selected net's reference planes on the board (null: none). */
    tint(nets) { const u = nets ? units().find((x) => x.includes(nets[0] ?? nets)) : null; tintKey = u ? unitKey(u) : null; buildUnits(); applyTint(); },
    zoomToSelection,
    fit() { stage.fit(); },
    clear() { sel = []; pairs = []; hits.clear(); split.clear(); tintKey = null; changed(); },
    /** Set the form's figures (e.g. a row the app already has). */
    setTarget({ target, differential, tolerance } = {}) {
      if (target != null) tgtIn.value = String(target);
      if (differential != null) diffIn.value = String(differential);
      if (tolerance != null) tolIn.value = String(tolerance);
      compute(true);
    },
    on(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); return () => listeners.get(name)?.delete(fn); },
    destroy() { destroyed = true; clearTimeout(compute.timer); clearTimeout(compute.routeTimer); refAn?.destroy(); panel?.destroy(); hl.remove(); stage.destroy?.(); root.remove(); },
  };
  return api;
}
