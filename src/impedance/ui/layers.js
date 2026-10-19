// boarddd/impedance/ui: layerView, the board one copper layer at a time (the layers above ghosted, the ones below
// hidden), a stepper through the stackup (copper and the dielectrics between), and picking nets to control:
//   click a trace            its segment (width, length, layer)
//   right click / long-press its whole net on every layer, and the control form:
//      N12 ✎   F.Cu In2.Cu         ⇄ N13 ✓ / ⇄ pair with…
//      [MS ▾]  [50] Ω ± [10] %
//      Z 52.3 Ω at w 0.200 mm  ·  → w 0.212 mm for 50 Ω
//      [✓ control impedance]
// Works from a copper@1 document alone (plain Gerbers through boarddd.connectivity, or X2/KiCad copper); the
// impedance figures are tier 1 (closed form) on `board.stackup`, the route along the net from the impedance
// panel's analyzeNet, and an app may give the tier-2 (field solver) figure through `evaluate`.
import { draw } from '../../view2d/content.js';
import { createStage } from '../../view2d/stage.js';
import { parallelPartner } from '../../nets/similar.js';
import { evaluateTarget } from '../stackup.js';
import { impedancePanel, STRUCTURES } from './panel.js';
import { copperHitIndex, pairOf, pairOrder, trackPath } from './pick.js';
import { copperContent, createHighlight, layerColor } from './stage.js';
import { IMPEDANCE_UI_CSS } from './style.js';

const GHOST = 0.16;
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

/**
 * The layer view in `el`. Options: `board` (board@1 with the stackup the figures are computed on), `copper`
 * (copper@1), `route` (createAnalyzer options, e.g. `{ worker: url }`: the net's impedance along its route, from
 * an impedancePanel under the form; false: none), `netInfo` ({net: {auto, pads}}:
 * auto-named nets can be renamed), `rename(net, name)` (async; omit: no ✎), `evaluate(draft)` (async; the app's
 * tier-2 figure: resolves { z, width, gap?, why? }), `editable` (default true: the ✓ button), `defaults`
 * ({ target, tolerance, pair }), `injectCss`.
 * Events: 'layer' (stop), 'segment' ({ track, net, layer, width, length } | null), 'net' ({ nets, layers } | null),
 * 'draft' (the form's value), 'control' (✓: the draft), 'rename' ({ net, name }), 'error'.
 */
export function layerView(el0, { board, copper, route = false, netInfo = {}, rename = null, evaluate = null, editable = true,
  defaults = {}, injectCss: inject = true } = {}) {
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

  const root = el('div', 'bdi-lv', el0);
  root.tabIndex = 0;
  root.setAttribute('aria-label', 'Board layers: ↑ ↓ to step through them, right click (or hold) a trace to pick its net');
  const main = el('div', 'bdi-lv-main', root);
  const stepper = el('div', 'bdi-lv-steps', main);
  const up = el('button', 'bdi-lv-step', stepper, '▲');
  up.type = 'button'; up.title = 'the layer above (↑)'; up.setAttribute('aria-label', 'Layer above');
  const list = el('ol', 'bdi-lv-stack', stepper);
  const down = el('button', 'bdi-lv-step', stepper, '▼');
  down.type = 'button'; down.title = 'the layer below (↓)'; down.setAttribute('aria-label', 'Layer below');
  const stageEl = el('div', 'bdi-lv-stage', main);
  const caption = el('div', 'bdi-lv-caption', stageEl);
  const side = el('div', 'bdi-lv-side', root);
  const info = el('div', 'bdi-lv-info', side);
  const form = el('div', 'bdi-ctl', side);
  form.hidden = true;
  const routeBox = route ? el('details', 'bdi-lv-route', side) : null;
  if (routeBox) { el('summary', null, routeBox, 'Z along the route'); routeBox.hidden = true; }

  // ---- the stage: one content per copper layer (scrubbing changes opacities only: no re-render)
  const bounds = copper.bounds ?? boundsOf(copper);
  // the stage takes the board's shape (within the CSS's min / max heights)
  stageEl.style.aspectRatio = `${(bounds.maxX - bounds.minX).toFixed(3)} / ${(bounds.maxY - bounds.minY).toFixed(3)}`;
  const stage = createStage(stageEl, { bounds, injectCss: inject });
  const contents = order.map((l) => copperContent(copper, { layers: [l], zoneAlpha: 0.45, alpha: 1 }));
  const dielectric = draw((g) => {
    g.fillStyle = getComputedStyle(root).getPropertyValue('--bdi-diel') || '#e9ddb8';
    g.globalAlpha = 0.55;
    g.fillRect(bounds.minX, bounds.minY, bounds.maxX - bounds.minX, bounds.maxY - bounds.minY);
  }, bounds);
  // bottom first: a layer above is drawn over the ones below it, ghosted
  stage.setScene([{ layers: [...contents.map((content) => ({ content, opacity: 0 })).reverse(), { content: dielectric, opacity: 0 }] }]);
  const hl = createHighlight(stage, copper, { dim: 0.25 });
  const panel = routeBox ? impedancePanel(routeBox, { board, copper, stage: null, pick: false, group: false, injectCss: false,
    analyzer: typeof route === 'object' ? route : {} }) : null;
  const index = copperHitIndex(copper);

  let at = Math.max(0, stops.findIndex((s) => s.kind === 'copper'));
  let picked = null; // { nets, layers, hit }
  let pairing = false; // the next click picks the partner
  let draft = null;

  function drawStack() {
    list.replaceChildren();
    stops.forEach((s, i) => {
      const li = el('li', `bdi-lv-stop is-${s.kind}${i === at ? ' is-on' : ''}`, list);
      li.dataset.i = String(i);
      if (s.kind === 'copper') {
        li.style.setProperty('--c', layerColor(copper, s.layer));
        li.textContent = s.layer.replace(/\.Cu$/, '');
        li.title = `${s.layer}${s.thickness ? ` · ${mm(s.thickness)} mm copper` : ''}`;
        if (picked?.layers.includes(s.layer)) li.classList.add('has-net');
      } else {
        li.textContent = s.epsilon_r ? `εr ${(+s.epsilon_r).toFixed(2)}` : '';
        li.title = [s.dielectric || 'dielectric', s.material, s.thickness ? `${mm(s.thickness)} mm` : null, s.epsilon_r ? `εr ${s.epsilon_r}` : null].filter(Boolean).join(' · ');
      }
      li.setAttribute('role', 'button');
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

  // ---- picks
  const tip = el('div', 'bdi-tip', stage.root);
  tip.hidden = true;
  stage.on('move', (e) => {
    const d = pickAt(e.x, e.y);
    if (!d) { tip.hidden = true; return; }
    tip.textContent = `${d.net || '(no net)'}${d.width ? ` · ${mm(d.width)} mm` : ''}${pairing ? ' · ⇄ pair with this' : ''}`;
    tip.hidden = false;
    const r = stage.root.getBoundingClientRect();
    tip.style.left = `${e.event.clientX - r.left + 12}px`;
    tip.style.top = `${e.event.clientY - r.top + 12}px`;
  });
  stage.on('leave', () => { tip.hidden = true; });
  stage.on('click', (e) => {
    const d = pickAt(e.x, e.y);
    if (pairing) {
      pairing = false;
      root.dataset.pairing = '';
      if (d?.net && picked && !picked.nets.includes(d.net)) setNets(pairOrder(picked.nets[0], d.net), d);
      return;
    }
    if (!d || d.kind !== 'track') {
      if (!picked) { hl.set({}); info.replaceChildren(); delete root.dataset.picked; emit('segment', null); }
      return;
    }
    const t = copper.tracks[d.track];
    const seg = { track: d.track, net: t.net, layer: t.layer, width: t.width, length: len(t) };
    if (!picked) hl.set({ nets: [t.net], track: d.track });
    if (!picked) root.dataset.picked = 'segment';
    info.replaceChildren();
    const line = el('div', 'bdi-lv-seg', info);
    line.append(el('span', 'bdi-chip', null, t.net || '(no net)'), ` w ${mm(t.width)} mm · ${f1(seg.length)} mm · ${t.layer}`);
    line.title = 'this segment: right click (or hold) to pick its whole net';
    emit('segment', seg);
  });
  stage.on('context', (e) => {
    const d = pickAt(e.x, e.y);
    if (!d?.net) return;
    pairing = false;
    root.dataset.pairing = '';
    const partner = pairOf(d.net, { board, names: copper.nets ?? [] });
    setNets(partner ? pairOrder(d.net, partner) : [d.net], d, !partner);
  });

  function setNets(nets, hit = null, guessPair = false) {
    const layers = layersOf(nets);
    picked = { nets, layers, hit };
    hl.set({ nets });
    hl.focus?.(onLayer());
    let suggestion = null;
    if (guessPair && nets.length === 1 && defaults.pair !== false) {
      const p = parallelPartner(copper, nets[0]);
      if (p) suggestion = p;
    }
    drawStack();
    root.dataset.picked = 'net';
    emit('net', { nets, layers });
    buildForm(suggestion);
    if (panel) { routeBox.hidden = false; panel.select({ nets, hit })?.catch?.(() => {}); }
  }

  // ---- the control form
  let tgtIn, tolIn, structSel, zEl, sugEl, t2El, okBtn;
  function buildForm(suggestion) {
    form.replaceChildren();
    form.hidden = !picked;
    if (!picked) return;
    const { nets, layers } = picked;
    const head = el('div', 'bdi-ctl-head', form);
    const name = el('strong', 'bdi-ctl-net', head, `${nets.length === 2 ? '⇄ ' : ''}${nets.join(' ')}`);
    name.title = nets.map((n) => `${n}${netInfo[n]?.pads != null ? ` · ${netInfo[n].pads} pads` : ''}${netInfo[n]?.auto ? ' · named here (no net name in the files)' : ''}`).join('\n');
    if (rename && nets.length === 1 && netInfo[nets[0]]?.auto) {
      const b = el('button', 'bdi-gbtn bdi-ctl-rename', head, '✎');
      b.type = 'button'; b.title = 'name this net'; b.setAttribute('aria-label', 'Rename net');
      b.addEventListener('click', async () => {
        const v = typeof prompt === 'function' ? prompt('Net name', nets[0]) : null;
        if (!v || v === nets[0]) return;
        try { await rename(nets[0], v.trim()); emit('rename', { net: nets[0], name: v.trim() }); } catch (err) { emit('error', err); }
      });
    }
    for (const l of layers) {
      const c = el('button', `bdi-chip bdi-ctl-layer${l === onLayer() ? ' is-on' : ''}`, head, l);
      c.type = 'button';
      c.title = `on ${l}: show it`;
      c.addEventListener('click', () => { const i = stops.findIndex((s) => s.layer === l); if (i >= 0) { at = i; show(); } });
    }
    const pairRow = el('div', 'bdi-ctl-row', form);
    if (nets.length === 2) {
      pairRow.append(el('span', 'bdi-msym', null, '⇄'), ` pair `);
      const un = el('button', 'bdi-gbtn', pairRow, '✕');
      un.type = 'button'; un.title = 'not a pair: this net alone'; un.setAttribute('aria-label', 'Unpair');
      un.addEventListener('click', () => setNets([nets[0]], picked.hit));
    } else {
      if (suggestion) {
        const s = el('button', 'bdi-gbtn bdi-ctl-partner', pairRow, `⇄ ${suggestion.net} ✓`);
        s.type = 'button';
        s.title = `runs beside it at ${mm(suggestion.gap)} mm for ${f1(suggestion.length)} mm (${Math.round(100 * suggestion.share)} % of its route on ${suggestion.layer}): a differential pair?`;
        s.addEventListener('click', () => setNets(pairOrder(nets[0], suggestion.net), picked.hit));
      }
      const p = el('button', 'bdi-gbtn bdi-ctl-pairwith', pairRow, '⇄ pair with…');
      p.type = 'button'; p.title = 'click the partner trace on the board';
      p.addEventListener('click', () => { pairing = true; root.dataset.pairing = 'on'; stage.root.focus?.(); });
    }
    const row = el('div', 'bdi-ctl-row', form);
    structSel = el('select', 'bdi-override bdi-ctl-structure', row);
    structSel.title = 'structure: auto (from the stackup and the copper around the trace), or set it';
    for (const [v, t, w] of STRUCTURE_OPTIONS) { const o = el('option', null, structSel, t); o.value = v; o.title = w; }
    tgtIn = el('input', 'bdi-pattern bdi-ctl-target', row);
    tgtIn.inputMode = 'decimal'; tgtIn.value = String(defaults.target?.[nets.length === 2 ? 'differential' : 'single'] ?? (nets.length === 2 ? 90 : 50));
    tgtIn.setAttribute('aria-label', 'Target ohms');
    row.append(' Ω ± ');
    tolIn = el('input', 'bdi-pattern bdi-ctl-tol', row);
    tolIn.inputMode = 'decimal'; tolIn.value = String(defaults.tolerance ?? 10); tolIn.setAttribute('aria-label', 'Tolerance percent');
    row.append(' %');
    const out = el('div', 'bdi-ctl-out', form);
    el('span', 'bdi-ctl-tier', out, 'closed form').title = 'tier 1: closed-form formulas, as you type';
    zEl = el('span', 'bdi-z bdi-ctl-z', out, '…');
    sugEl = el('span', 'bdi-ctl-sug', out);
    t2El = el('div', 'bdi-ctl-t2', form);
    t2El.hidden = !evaluate;
    if (editable) {
      okBtn = el('button', 'bdi-gbtn bdi-apply bdi-ctl-ok', form, '✓ control impedance');
      okBtn.type = 'button';
      okBtn.title = 'make it a controlled structure: this net (or pair), its layer and width as routed, the target and tolerance';
      okBtn.addEventListener('click', () => { if (draft) emit('control', { ...draft }); });
    }
    for (const i of [tgtIn, tolIn]) i.addEventListener('input', () => compute());
    structSel.addEventListener('change', () => compute(true));
    compute(true);
  }

  /** The geometry the form computes on: the analysis's main section when there is one, else the picked track. */
  function geometry() {
    const sec = mainSection(panel?.result && sameNets(panel.result.nets, picked.nets) ? panel.result : null);
    if (sec) return { layer: sec.layer, width: sec.geometry?.width ?? null, gap: sec.geometry?.gap ?? null, structure: sec.structure,
      coplanar_gap: (sec.geometry?.coplanar_gap ?? []).filter((g) => g != null).reduce((a, b) => Math.min(a, b), Infinity), from: 'route' };
    const h = picked.hit;
    const t = h?.kind === 'track' ? copper.tracks[h.track] : longestTrack(picked.nets[0]);
    if (!t) return null;
    let gap = null;
    if (picked.nets.length === 2) gap = parallelPartner(copper, picked.nets[0], { layer: t.layer, share: 0 })?.gap ?? null;
    return { layer: t.layer, width: t.width, gap, structure: null, coplanar_gap: Infinity, from: 'track' };
  }
  const longestTrack = (net) => (copper.tracks ?? []).filter((t) => t.net === net).sort((a, b) => len(b) - len(a))[0] ?? null;
  const sameNets = (a, b) => a.length === b.length && a.every((n, i) => n === b[i]);

  let t2Run = 0;
  function compute(asTier2 = false) {
    if (!picked || !zEl) return;
    const g = geometry();
    const target = Number(tgtIn.value), tolerance = Number(tolIn.value);
    const kind = picked.nets.length === 2 ? 'differential' : 'single';
    const forced = structSel.value || null;
    const found = AS_TARGET[g?.structure] ?? null;
    const structure = forced ?? found;
    structSel.options[0].textContent = !forced && found ? `auto: ${SHORT[found]}` : 'auto';
    draft = g ? { nets: [...picked.nets], kind, layer: g.layer, structure, target, tolerance_pct: tolerance,
      width: g.width, gap: kind === 'differential' ? g.gap : null, coplanar_gap: Number.isFinite(g.coplanar_gap) ? g.coplanar_gap : null,
      anchors: anchorsOf(picked) } : null;
    if (!g || !stackup || !(target > 0)) { zEl.textContent = '–'; sugEl.textContent = ''; emit('draft', draft); return; }
    try {
      const coplanar = structure?.startsWith('coplanar');
      const opts = coplanar && draft.coplanar_gap ? { coplanarGap: draft.coplanar_gap } : {};
      const t = { kind, target, tolerance_pct: tolerance, structure: coplanar && !draft.coplanar_gap ? undefined : structure ?? undefined,
        layers: [{ layer: g.layer, width: g.width, gap: draft.gap }] };
      let now, want, approx = '';
      const run = (tt, o) => [evaluateTarget(stackup, tt, o)[0], evaluateTarget(stackup, { ...tt, layers: [{ layer: g.layer, width: null, gap: draft.gap }] }, o)[0]];
      try {
        [now, want] = run(t, opts);
      } catch (err) {
        if (!t.structure) throw err;
        // tier 1 has no model for it (a coplanar pair): the layer's plain structure, the field solver for the rest
        [now, want] = run({ ...t, structure: undefined }, {});
        approx = ` · tier 1 has no ${kind === 'differential' ? 'differential ' : ''}${t.structure.replace('_', ' ')} model: as ${now.model.replace('_', ' ')}`;
      }
      const off = (100 * (now.value - target)) / target;
      const v = Math.abs(off) <= tolerance / 2 ? 'ok' : Math.abs(off) <= tolerance ? 'warn' : 'bad';
      zEl.textContent = `≈ ${f1(now.value)} Ω`;
      zEl.dataset.v = v;
      zEl.title = `${kind === 'differential' ? 'Zdiff' : 'Z0'} at w ${mm(g.width)} mm${draft.gap != null ? `, s ${mm(draft.gap)} mm` : ''} on ${g.layer} (tier 1, ${now.model}) · ${off >= 0 ? '+' : ''}${f1(off)} % of ${target} Ω${approx}`;
      sugEl.textContent = Math.abs(want.width - g.width) > 1e-4 ? `${target} Ω at w ${mm(want.width)} mm` : '✓ w';
      sugEl.title = `the width for ${target} Ω on this stackup (tier 1)${draft.gap != null ? ` at s ${mm(draft.gap)} mm` : ''}; routed: w ${mm(g.width)} mm`;
      draft.tier1 = { z: now.value, model: now.model, width_for_target: want.width };
    } catch (err) {
      zEl.textContent = '✗';
      zEl.title = String(err?.message ?? err);
      sugEl.textContent = '';
    }
    emit('draft', draft);
    if (evaluate && asTier2 !== null) {
      const mine = ++t2Run;
      clearTimeout(compute.timer);
      compute.timer = setTimeout(async () => {
        t2El.replaceChildren(el('span', 'bdi-ctl-tier', null, 'field solver'), ' …');
        try {
          const r = await evaluate({ ...draft });
          if (mine !== t2Run || !r) return;
          t2El.replaceChildren(el('span', 'bdi-ctl-tier', null, 'field solver'), ' ');
          t2El.firstChild.title = 'tier 2: the final figure';
          const z = el('span', 'bdi-ctl-t2z', t2El, r.z != null ? `${f1(r.z)} Ω` : '–');
          z.title = r.why ?? 'field solver (tier 2): the final figure';
          if (r.width != null) t2El.append(el('span', 'bdi-ctl-t2w', null, ` → w ${mm(r.width)}${r.gap != null ? ` s ${mm(r.gap)}` : ''} mm`));
          if (r.note) t2El.append(el('span', 'bdi-s', null, ` · ${r.note}`));
        } catch (err) {
          if (mine === t2Run) { t2El.textContent = ''; emit('error', err); }
        }
      }, 350);
    }
  }

  function anchorsOf(p) {
    const out = [];
    for (const n of p.nets) {
      const pad = (copper.pads ?? []).find((q) => q.net === n);
      const t = pad ? null : longestTrack(n);
      if (pad) out.push({ net: n, layer: pad.layers[0], x: pad.at[0], y: pad.at[1] });
      else if (t) { const q = trackPath(t)[0]; out.push({ net: n, layer: t.layer, x: q[0], y: q[1] }); }
    }
    return out;
  }

  if (panel) panel.on('result', () => compute(true));

  // ---- keys and the stepper
  up.addEventListener('click', () => step(-1));
  down.addEventListener('click', () => step(1));
  list.addEventListener('click', (e) => { const li = e.target.closest('li[data-i]'); if (li) { at = Number(li.dataset.i); show(); } });
  root.addEventListener('keydown', (e) => {
    if (e.target.closest?.('input, select, textarea')) return;
    if (e.key === 'ArrowUp') { e.preventDefault(); step(-1); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); step(1); }
    else if (e.key === 'Escape') { api.clear(); }
  });
  stage.on('view', () => hl.invalidate());
  show();

  const api = {
    root,
    stage,
    stops,
    get index() { return at; },
    /** The stop on show ({kind, layer?, ...}). */
    get stop() { return { ...stops[at], index: at }; },
    get selection() { return picked ? { nets: [...picked.nets], layers: [...picked.layers] } : null; },
    get draft() { return draft ? { ...draft } : null; },
    /** The route analysis panel (impedancePanel), with `route`. */
    get panel() { return panel; },
    /** Show a stop by index, or a copper layer by id. */
    show(i) { const k = typeof i === 'string' ? stops.findIndex((s) => s.layer === i) : i; if (k >= 0 && k < stops.length) { at = k; show(); } },
    step,
    /** Pick nets as a right click would ([net] or a pair). */
    pick(nets, { guessPair = nets.length === 1 } = {}) { setNets(nets, null, guessPair); },
    /** Pick what is under a board point on the layer on show, as a right click there. */
    pickAt(x, y) { const d = pickAt(x, y); if (d?.net) setNets([d.net], d, true); return d; },
    clear() { picked = null; pairing = false; root.dataset.pairing = ''; delete root.dataset.picked; hl.set({}); form.hidden = true; info.replaceChildren(); drawStack(); emit('net', null); },
    /** Set the form's figures (e.g. a row the app already has). */
    setTarget({ target, tolerance } = {}) { if (tgtIn && target != null) tgtIn.value = String(target); if (tolIn && tolerance != null) tolIn.value = String(tolerance); compute(true); },
    on(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); return () => listeners.get(name)?.delete(fn); },
    destroy() { panel?.destroy(); hl.remove(); stage.destroy?.(); root.remove(); },
  };
  return api;
}
