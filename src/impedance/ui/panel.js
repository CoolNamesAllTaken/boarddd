// boarddd/impedance/ui: impedancePanel, the click-a-trace panel. Framework-free; words go in tooltips, the panel
// shows symbols and numbers:
//   ⇄ D+ D-   101.2 Ω   ✓ 100 ±10 %   [auto ▾]   F.Cu B.Cu
//   ▁▁▁▁▂▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁   (Z along the route; hover: marker on the stage, cross-section below)
//   [cross-section]
//   ◎ 4.5  ⊘ 4.5  ⇹ 0.0 …  (discontinuities; click: zoom there)
// Colours and sizes are CSS variables (IMPEDANCE_UI_CSS); a host restyles them on .bdi-panel or an ancestor.
import { createAnalyzer } from './analyzer.js';
import { crossSection } from './crosssection.js';
import { pairOf, pairOrder } from './pick.js';
import { attachPicker, createHighlight, routePoint } from './stage.js';
import { IMPEDANCE_UI_CSS } from './style.js';

const NS = 'http://www.w3.org/2000/svg';
const el = (tag, cls, parent, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  parent?.append(e);
  return e;
};
const svg = (tag, attrs = {}, parent = null) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, String(v));
  parent?.append(e);
  return e;
};
const f1 = (v) => (v == null ? '–' : v.toFixed(1));

/** Short names and words for structures. */
export const STRUCTURES = {
  microstrip: ['MS', 'microstrip: one reference plane, air (and mask) above'],
  embedded_microstrip: ['EMS', 'embedded microstrip: an inner layer with one reference plane'],
  stripline: ['SL', 'stripline: reference planes above and below'],
  offset_stripline: ['SL↕', 'offset stripline: planes above and below at different heights'],
  cpwg: ['CPWG', 'grounded coplanar: coplanar ground beside the trace and a plane'],
  cpw: ['CPW', 'coplanar, no plane under it'],
  none: ['—', 'no reference plane: no impedance'],
};
/** Symbols and words for discontinuities. */
export const DISCONTINUITIES = {
  via: ['◎', 'via: the route changes layer'],
  ref_change: ['⇅', 'the reference plane changes'],
  plane_gap: ['⊘', 'a plane split or void under the trace'],
  width_change: ['⇔', 'the width changes'],
  uncoupled: ['⇹', 'the pair splits (breakout): two single lines'],
  no_ref: ['✗', 'no reference plane: no impedance here'],
  ref_edge: ['⚠', 'a reference plane ends near the trace'],
};
const OVERRIDES = [
  ['', 'auto', 'classified per section from the copper'],
  ['microstrip', 'MS', 'force microstrip (outer) / no coplanar ground'],
  ['stripline', 'SL', 'force stripline (inner) / no coplanar ground'],
  ['cpwg', 'CPWG', 'label as grounded coplanar (coplanar copper as found)'],
  ['cpw', 'CPW', 'coplanar without a plane (planes ignored)'],
];

/** ✓ within tolerance, ⚠ within it but near the limit (≥ 80 %), ✗ outside or no Z. */
export function verdict(z, target) {
  if (z == null) return 'bad';
  if (!target) return 'none';
  const dev = Math.abs(z - target.value) / target.value * 100;
  return dev > target.tolerance_pct ? 'bad' : dev > 0.8 * target.tolerance_pct ? 'warn' : 'ok';
}
const MARK = { ok: '✓', warn: '⚠', bad: '✗', none: '' };

let cssDone = false;
function injectCss(doc) {
  if (cssDone || !doc?.head) return;
  const s = doc.createElement('style');
  s.textContent = IMPEDANCE_UI_CSS;
  doc.head.append(s);
  cssDone = true;
}

/**
 * The impedance panel in `el` for a board@1 + copper@1 pair. With `stage` (a view2d stage showing the board) it
 * attaches the picker (click a trace: its net or pair; alt-click: one segment) and the highlight overlay, and
 * syncs hovers both ways. Options: `analyzer` (createAnalyzer options, or an analyzer), `options` (analyzeNet
 * options), `pick: false` (select() only), `layers` (what clicks may pick: a list or a function returning the
 * layers on show), `injectCss: false`. Events: 'select', 'result', 'hover', 'error'.
 */
export function impedancePanel(el0, { board, copper, stage = null, analyzer = {}, options = {}, pick = true, layers = null, injectCss: inject = true } = {}) {
  if (inject) injectCss(el0.ownerDocument);
  const an = typeof analyzer?.analyze === 'function' ? analyzer : createAnalyzer({ board, copper, ...analyzer });
  const listeners = new Map();
  const emit = (n, v) => { for (const fn of listeners.get(n) ?? []) fn(v); };
  const root = el('div', 'bdi-panel', el0);
  const head = el('div', 'bdi-head', root);
  const netEl = el('span', 'bdi-nets', head);
  const zEl = el('span', 'bdi-z', head);
  const vEl = el('span', 'bdi-verdict', head);
  const sel = el('select', 'bdi-override', head);
  sel.title = 'structure: auto, or an override for the whole net (re-runs)';
  for (const [v, t, w] of OVERRIDES) { const o = el('option', null, sel, t); o.value = v; o.title = w; }
  const layersEl = el('span', 'bdi-layers', head);
  const prog = el('div', 'bdi-progress', root);
  const bar = el('div', 'bdi-bar', prog);
  const chart = el('div', 'bdi-profile', root);
  const xsEl = el('div', 'bdi-xsbox', root);
  const discEl = el('ol', 'bdi-disc', root);
  const empty = el('div', 'bdi-empty', root, '⊕');
  empty.title = 'click a trace on the board: its net (and its pair); alt-click: one segment';

  let selection = { nets: [], track: null };
  let result = null;
  let override = '';
  let runs = 0;
  let current = null; // the section shown in the cross-section
  const hl = stage ? createHighlight(stage, copper) : null;
  const picker = stage && pick ? attachPicker(stage, copper, { board, layers, onSelect: (s) => api.select(s) }) : null;
  root.dataset.state = 'empty';

  function busy(on, p = null) {
    root.dataset.state = on ? 'busy' : result ? 'ready' : selection.nets.length ? 'error' : 'empty';
    bar.style.width = p ? `${Math.round(100 * (p.phase === 'route' ? 0.2 * p.done / p.total : 0.2 + 0.8 * p.done / Math.max(p.total, 1)))}%` : on ? '5%' : '0';
  }

  function key(doc) { return doc.summary.key; }
  function zOf(s, k) { return s.z ? (k === 'Zdiff' ? s.z.Zdiff : s.z.Z0) : null; }

  function showHeader(doc) {
    const pair = doc.kind === 'differential';
    netEl.textContent = `${pair ? '⇄ ' : ''}${doc.nets.join(' ')}`;
    netEl.title = `${pair ? 'differential pair' : 'net'}: ${doc.nets.join(', ')}${selection.track != null ? ' (one segment)' : ''}`;
    const k = key(doc);
    const z = doc.summary.z_weighted;
    zEl.textContent = `${f1(z)} Ω`;
    const zc = doc.sections.filter((s) => s.z?.Zcommon != null);
    const zcm = zc.length ? zc.reduce((t, s) => t + s.z.Zcommon * s.length, 0) / zc.reduce((t, s) => t + s.length, 0) : null;
    zEl.title = `${k === 'Zdiff' ? 'Zdiff' : 'Z0'}, length-weighted over ${f1(doc.summary.length_with_z)} of ${f1(doc.summary.length)} mm`
      + ` · range ${f1(doc.summary.z_min)}–${f1(doc.summary.z_max)} Ω${zcm != null ? ` · Zcommon ${f1(zcm)} Ω` : ''}`;
    const t = doc.target;
    const v = verdict(z, t);
    vEl.dataset.v = v;
    vEl.textContent = t ? `${MARK[v]} ${+t.value.toFixed(1)} ±${+t.tolerance_pct.toFixed(1)} %` : '';
    vEl.title = t ? `target ${t.value} Ω ±${t.tolerance_pct} % (${t.source === 'net_class' ? `net class ${t.net_class}` : 'set'}) · deviation ${z != null ? ((100 * (z - t.value)) / t.value).toFixed(1) : '–'} % · ${f1(doc.summary.out_of_tolerance_pct)} % of the route outside or without Z` : 'no target (net class without one)';
    layersEl.replaceChildren();
    for (const l of [...new Set(doc.sections.map((s) => s.layer))]) { const c = el('span', 'bdi-chip', layersEl, l); c.title = `routed on ${l}`; }
  }

  function showProfile(doc) {
    chart.replaceChildren();
    const k = key(doc);
    const secs = doc.sections.filter((s) => s.net === doc.nets[0]);
    if (!secs.length) return;
    const W = chart.clientWidth || 320, H = chart.clientHeight || 64, P = 4;
    const s1 = Math.max(...secs.map((s) => s.s1));
    const zs = secs.map((s) => zOf(s, k)).filter((v) => v != null);
    const t = doc.target;
    let lo = Math.min(...zs, t ? t.value * (1 - t.tolerance_pct / 100) : Infinity);
    let hi = Math.max(...zs, t ? t.value * (1 + t.tolerance_pct / 100) : -Infinity);
    if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
    const pad = (hi - lo) * 0.08 || 1;
    lo -= pad; hi += pad;
    const X = (s) => P + ((W - 2 * P) * s) / s1, Y = (z) => P + (H - 2 * P) * (1 - (Math.min(Math.max(z, lo), hi) - lo) / (hi - lo));
    const root2 = svg('svg', { class: 'bdi-chart', width: W, height: H, viewBox: `0 0 ${W} ${H}` }, chart);
    if (t) {
      svg('rect', { x: P, width: W - 2 * P, y: Y(t.value * (1 + t.tolerance_pct / 100)), height: Y(t.value * (1 - t.tolerance_pct / 100)) - Y(t.value * (1 + t.tolerance_pct / 100)), class: 'bdi-band' }, root2);
      svg('line', { x1: P, x2: W - P, y1: Y(t.value), y2: Y(t.value), class: 'bdi-target' }, root2);
    }
    for (const s of secs) {
      const z = zOf(s, k);
      if (z == null) { svg('rect', { x: X(s.s0), width: Math.max(1, X(s.s1) - X(s.s0)), y: H - P - 3, height: 3, class: 'bdi-noz' }, root2); continue; }
      svg('line', { x1: X(s.s0), x2: Math.max(X(s.s1), X(s.s0) + 1), y1: Y(z), y2: Y(z), class: `bdi-seg bdi-${verdict(z, t)}` }, root2);
    }
    for (const d of doc.discontinuities) if (d.type === 'via' && d.net === doc.nets[0]) svg('line', { x1: X(d.s), x2: X(d.s), y1: P, y2: H - P, class: 'bdi-via' }, root2);
    const cursor = svg('line', { y1: 0, y2: H, class: 'bdi-cursor', visibility: 'hidden' }, root2);
    const hit = svg('rect', { x: 0, y: 0, width: W, height: H, class: 'bdi-hit' }, root2);
    const title = svg('title', {}, hit);
    const sectionAt = (s) => secs.find((x) => x.s0 - 1e-9 <= s && s <= x.s1 + 1e-9) ?? null;
    const move = (ev) => {
      const r = root2.getBoundingClientRect();
      const s = Math.min(s1, Math.max(0, ((ev.clientX - r.left - P) / (r.width - 2 * P)) * s1));
      const sec = sectionAt(s);
      cursor.setAttribute('x1', X(s)); cursor.setAttribute('x2', X(s)); cursor.setAttribute('visibility', 'visible');
      const z = sec ? zOf(sec, k) : null;
      title.textContent = sec ? `${s.toFixed(2)} mm · ${z == null ? 'no Z' : `${z.toFixed(1)} Ω`} · ${STRUCTURES[sec.structure]?.[0] ?? sec.structure}${sec.kind === 'differential' ? ' ⇄' : ''} · ${sec.layer}${sec.flags.length ? ` · ${sec.flags.join(', ')}` : ''}` : '';
      api.hover(s, sec);
    };
    hit.addEventListener('pointermove', move);
    hit.addEventListener('pointerleave', () => { cursor.setAttribute('visibility', 'hidden'); api.hover(null, null); });
    hit.addEventListener('click', (ev) => { move(ev); if (current) zoomTo(current.start, current.end); });
  }

  function showDiscontinuities(doc) {
    discEl.replaceChildren();
    // one chip per place: events within 0.1 mm on the same net merge (a via is often a reference change too)
    const groups = [];
    const order = (d) => doc.nets.indexOf(d.net);
    for (const d of [...doc.discontinuities].sort((a, b) => order(a) - order(b) || a.s - b.s)) {
      const g = groups.find((x) => x.net === d.net && Math.abs(x.s - d.s) < 0.1);
      if (g) { if (!g.items.some((x) => x.type === d.type)) g.items.push(d); } else groups.push({ net: d.net, s: d.s, at: d.at, items: [d] });
    }
    groups.forEach((g, i) => {
      const li = el('li', `bdi-d ${g.items.map((d) => `bdi-d-${d.type}`).join(' ')}`, discEl);
      li.dataset.i = String(i);
      for (const d of g.items) el('span', 'bdi-sym', li, (DISCONTINUITIES[d.type] ?? ['•'])[0]);
      el('span', 'bdi-s', li, g.s.toFixed(1));
      li.title = `${g.items.map((d) => `${(DISCONTINUITIES[d.type] ?? [null, d.type])[1]}${d.detail ? ` (${d.detail})` : ''}`).join('\n')}\n${g.s.toFixed(2)} mm along ${g.net} · ${g.items[0].layer}`;
      li.tabIndex = 0;
      const go = () => { zoomTo(g.at, g.at, 1.5); hl?.rings([g.at]); emit('focus', g); };
      li.addEventListener('click', go);
      li.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    });
  }

  function zoomTo(a, b, pad = 0.5) {
    if (!stage) return;
    stage.zoomTo({ minX: Math.min(a[0], b[0]) - pad, maxX: Math.max(a[0], b[0]) + pad, minY: Math.min(a[1], b[1]) - pad, maxY: Math.max(a[1], b[1]) + pad }, { minMm: 2 * pad });
    hl?.invalidate();
  }

  function showSection(sec) {
    current = sec;
    if (!result) return;
    crossSection(xsEl, sec, { board });
  }

  async function run() {
    if (!selection.nets.length) return;
    const myRun = ++runs;
    result = null;
    busy(true);
    const opts = { ...options };
    if (override) {
      opts.overrides = { net: { structure: override } };
      if (override === 'cpw') opts.noPlane = 'solve';
    }
    try {
      const nets = selection.nets.length === 2 ? selection.nets : selection.nets[0];
      let doc = await an.analyze(nets, opts, (p) => { if (myRun === runs) busy(true, p); });
      if (myRun !== runs) return;
      if (selection.track != null) doc = onlyTrack(doc, copper.tracks[selection.track]?.id);
      result = doc;
      busy(false);
      showHeader(doc);
      showProfile(doc);
      showDiscontinuities(doc);
      const main = [...doc.sections].filter((s) => s.z).sort((a, b) => b.length - a.length)[0] ?? doc.sections[0] ?? null;
      showSection(main);
      emit('result', doc);
    } catch (err) {
      if (err?.message === 'superseded' || myRun !== runs) return;
      result = null;
      busy(false);
      netEl.textContent = selection.nets.join(' ');
      zEl.textContent = '✗';
      zEl.title = String(err?.message ?? err);
      emit('error', err);
    }
  }

  sel.addEventListener('change', () => { override = sel.value; root.dataset.override = override; run(); });

  const api = {
    root,
    analyzer: an,
    get selection() { return { ...selection }; },
    get result() { return result; },
    get runs() { return runs; },
    get section() { return current; },
    /** Select nets ({ nets: [net] | [p, n], track? }; a string or an array also works) and analyse them. */
    select(s) {
      const v = typeof s === 'string' ? { nets: [s] } : Array.isArray(s) ? { nets: s } : s ?? { nets: [] };
      selection = { nets: v.nets ?? [], track: v.track ?? null };
      hl?.set(selection);
      emit('select', { ...selection, hit: v.hit ?? null });
      // while it runs: the nets, the previous numbers cleared
      netEl.textContent = `${selection.nets.length === 2 ? '⇄ ' : ''}${selection.nets.join(' ')}`;
      for (const e of [zEl, vEl, layersEl, chart, xsEl, discEl]) e.replaceChildren();
      if (!selection.nets.length) {
        result = null; runs++; busy(false);
        for (const e of [netEl, zEl, vEl, layersEl, chart, xsEl, discEl]) e.replaceChildren();
        root.dataset.state = 'empty';
        return Promise.resolve(null);
      }
      return run();
    },
    /** Re-run with a structure override ('' = auto). */
    setOverride(structure) { sel.value = structure ?? ''; override = sel.value; root.dataset.override = override; return run(); },
    /** Show the route position s (mm, first net) and its section: marker on the stage, cross-section. */
    hover(s, sec = null) {
      if (!result) return;
      const net = result.nets[0];
      const secs = result.sections.filter((x) => x.net === net);
      const at = s == null ? null : sec ?? secs.find((x) => x.s0 - 1e-9 <= s && s <= x.s1 + 1e-9) ?? null;
      if (hl) {
        const p = s == null ? null : routePoint(hl.routeOf(net), s, at?.tracks);
        hl.marker(p?.p ?? null);
        hl.section(at);
      }
      if (at && at !== current) showSection(at);
      emit('hover', { s, section: at });
    },
    on(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); return () => listeners.get(name)?.delete(fn); },
    /** Select what is under a board point (board mm), as a click there would: e.g. from a 3D pick (boardPointFromPick). */
    selectAt(x, y, { alt = false } = {}) {
      const d = picker?.pick(x, y);
      if (!d || !d.net) return api.select({ nets: [] });
      if (alt && d.track != null) return api.select({ nets: [d.net], track: d.track, hit: d });
      return api.select({ nets: [d.net], hit: d, ...pairFrom(d) });
    },
    destroy() { picker?.detach(); hl?.remove(); an.destroy?.(); root.remove(); },
  };
  function pairFrom(d) {
    const partner = pairOf(d.net, { board, names: copper.nets ?? [] });
    return partner ? { nets: pairOrder(d.net, partner) } : {};
  }
  if (stage) stage.on('view', () => hl?.invalidate());
  return api;
}

/** An analysis cut down to one track's sections (alt-click: one segment), summary recomputed over them. */
function onlyTrack(doc, id) {
  if (id == null) return doc;
  const sections = doc.sections.filter((s) => s.tracks.includes(id));
  const k = doc.summary.key;
  const withZ = sections.filter((s) => s.z && (k === 'Zdiff' ? s.z.Zdiff : s.z.Z0) != null);
  const len = sections.reduce((t, s) => t + s.length, 0), lz = withZ.reduce((t, s) => t + s.length, 0);
  const zv = (s) => (k === 'Zdiff' ? s.z.Zdiff : s.z.Z0);
  const summary = { ...doc.summary, length: len, length_with_z: lz,
    z_weighted: lz > 0 ? withZ.reduce((t, s) => t + zv(s) * s.length, 0) / lz : null,
    z_min: withZ.length ? Math.min(...withZ.map(zv)) : null, z_max: withZ.length ? Math.max(...withZ.map(zv)) : null };
  return { ...doc, sections, summary, discontinuities: doc.discontinuities.filter((d) => sections.some((s) => d.s >= s.s0 - 1e-6 && d.s <= s.s1 + 1e-6 && d.net === s.net)) };
}
