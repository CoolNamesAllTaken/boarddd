// boarddd/impedance/ui: routeProfile, Z along the whole route of a selection (path.js), one plot under the board:
//   J2            ◎ F→B               R5                      U1      parts and vias marked, labeled at the top
//   ──────────────────┐   ┌─────────────────────────────────      Z per section, coloured by verdict; the band
//   ░░░░░░░░░░░░░░░░░░└───┘░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░      is each unit's target ± tolerance
//   ▀▀▀▀▀▀▀▀ F.Cu ▀▀▀▀▀▀▀▀▄▄▄▄ B.Cu ▄▄▄▄▀▀▀▀ F.Cu ▀▀▀▀▀▀▀▀▀▀▀▀      the layer under the trace
//   0        10        20        30 mm
// Hover, tap or ←/→ moves a cursor (tooltip: Z, width, layer, reference plane, gap) and emits 'hover' with the
// board point; show(s) puts the cursor where the board's pointer is. Each unit is analysed once (cached).
import { createAnalyzer } from './analyzer.js';
import { verdict, STRUCTURES, DISCONTINUITIES } from './panel.js';
import { pathAt, pathFromBoard, pathNetName, routePath } from './path.js';
import { layerColor } from './stage.js';

const NS = 'http://www.w3.org/2000/svg';
const el = (tag, cls, parent, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  parent?.append(e);
  return e;
};
const svg = (tag, attrs = {}, parent = null, text = null) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, String(v));
  if (text != null) e.textContent = text;
  parent?.append(e);
  return e;
};
const f1 = (v) => (v == null || !Number.isFinite(v) ? '–' : v.toFixed(1));
const mm = (v) => (v == null || !Number.isFinite(v) ? '–' : String(+(+v).toFixed(3)));
const MARK = { ok: '✓', warn: '⚠', bad: '✗', none: '' };
const SYMBOL = { part: '▮', end: '▮', gap: '⋯', ...Object.fromEntries(Object.entries(DISCONTINUITIES).map(([k, v]) => [k, v[0]])) };

/** Round tick steps (1, 2, 5 × 10^n) for `span` over about `n` ticks. */
export function niceTicks(lo, hi, n = 5) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((k) => k * mag).find((k) => k >= raw) ?? 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

/** 'over In1.Cu (GND) · h 0.2 mm' from an impedance@1 section's refs. */
function planesText(sec) {
  const r = sec?.refs ?? [];
  const one = (x) => `${x.layer}${x.net ? ` (${x.net})` : ''}`;
  if (!r.length) return 'no reference plane';
  if (r.length === 1) return `${r[0].side === 'bottom' ? 'over' : 'under'} ${one(r[0])} · h ${mm(r[0].h)} mm`;
  return `between ${one(r[0])} and ${one(r[1])}`;
}

/** 'pad J3.1', 'teardrop' or 'zone': the same-net copper that is the conductor at a section (analyzeNet), or ''. */
export function copperText(sec) {
  const c = sec?.geometry?.copper;
  if (!c) return '';
  if (c.kind !== 'pad') return c.kind;
  return c.ref ? `pad ${c.ref}${c.number ? `.${c.number}` : ''}` : 'pad';
}

/**
 * The stretches of a path whose conductor is a pad, teardrop or zone (consecutive sections on the same copper):
 * [{ s0, s1, kind, label ('J3.1' for a pad), text (copperText), width (the widest), track (the track's width) }].
 */
export function copperSpans(path) {
  const out = [];
  for (const x of path?.sections ?? []) {
    const text = copperText(x.section);
    const prev = out[out.length - 1];
    if (!text) continue;
    const g = x.section.geometry;
    if (prev && prev.text === text && prev.unit === x.unit && Math.abs(prev.s1 - x.s0) < 1e-6) {
      prev.s1 = x.s1; prev.width = Math.max(prev.width, g.width);
      continue;
    }
    const c = g.copper;
    out.push({ s0: x.s0, s1: x.s1, unit: x.unit, kind: c.kind, text, width: g.width, track: g.track_width ?? null,
      label: c.kind === 'pad' && c.ref ? `${c.ref}${c.number ? `.${c.number}` : ''}` : '' });
  }
  return out;
}

/**
 * The plot in `el`. Options: `board`, `copper`, `analyzer` (createAnalyzer options, or an analyzer), `options`
 * (analyzeNet options for every unit). select(units, { targets, options }) analyses each unit ([net] | [p, n]; with
 * `options[i]` its own analyzeNet options, e.g. a reference override) and draws the path; setTargets() redraws.
 * Events: 'hover' ({ s, at } | null: at is pathAt's, with the board point and direction), 'path', 'error'.
 */
export function routeProfile(el0, { board, copper, analyzer = {}, options = {} } = {}) {
  const an = typeof analyzer?.analyze === 'function' ? analyzer : createAnalyzer({ board, copper, ...analyzer });
  const own = an !== analyzer;
  const listeners = new Map();
  const emit = (n, v) => { for (const fn of listeners.get(n) ?? []) fn(v); };
  const root = el('div', 'bdi-rp', el0);
  const head = el('div', 'bdi-rp-head', root);
  el('strong', 'bdi-rp-title', head, 'Z along the route');
  const sumEl = el('span', 'bdi-rp-sum', head);
  const progEl = el('span', 'bdi-rp-prog', head);
  const chart = el('div', 'bdi-rp-chart', root);
  chart.tabIndex = 0;
  chart.setAttribute('role', 'slider');
  chart.setAttribute('aria-label', 'Position along the route: ← → move the cursor, Shift for bigger steps');
  chart.setAttribute('aria-valuemin', '0');
  const tip = el('div', 'bdi-rp-tip', root);
  tip.hidden = true;
  root.dataset.state = 'empty';

  let units = [], targets = [], perUnit = [];
  let docs = [];
  let path = null;
  let run = 0;
  let cursorS = null;
  let geo = null; // the last drawing's scales
  const cache = new Map();
  const cacheKey = (u, o) => `${u.join('\n')}|${JSON.stringify(o ?? {})}`;

  function rebuild() {
    path = units.length ? routePath(copper, units, docs, { targets }) : null;
    draw();
    emit('path', path);
  }

  function summary() {
    if (!path) return '';
    const withZ = path.sections.filter((x) => x.z != null);
    const zs = withZ.map((x) => x.z);
    let inBand = 0, total = 0;
    for (const x of path.sections) {
      const t = path.units.find((u) => u.index === x.unit)?.target;
      total += x.s1 - x.s0;
      if (x.z != null && t && verdict(x.z, { tolerance_pct: 10, ...t }) !== 'bad') inBand += x.s1 - x.s0;
    }
    const parts = [`${mm(+path.length.toFixed(1))} mm`];
    if (zs.length) parts.push(`${f1(Math.min(...zs))}–${f1(Math.max(...zs))} Ω`);
    if (total > 0 && path.units.some((u) => u.target)) parts.push(`${Math.round((100 * inBand) / total)} % in band`);
    return parts.join(' · ');
  }

  function draw() {
    chart.replaceChildren();
    geo = null;
    sumEl.textContent = summary();
    if (!path || !(path.length > 0)) return;
    const W = Math.max(chart.clientWidth || 600, 200), H = Math.max(chart.clientHeight || 240, 160);
    const L = 44, R = 10, T = 22, B = 34;
    const zs = path.sections.map((x) => x.z).filter((v) => v != null);
    let lo = Infinity, hi = -Infinity;
    for (const v of zs) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    for (const u of path.units) {
      const t = u.target;
      if (t) { const tol = t.tolerance_pct ?? 10; lo = Math.min(lo, t.value * (1 - tol / 100)); hi = Math.max(hi, t.value * (1 + tol / 100)); }
    }
    if (!Number.isFinite(lo)) { lo = 0; hi = 100; }
    const pad = (hi - lo) * 0.1 || 2;
    lo -= pad; hi += pad;
    const X = (s) => L + ((W - L - R) * s) / path.length;
    const Y = (z) => T + (H - T - B) * (1 - (Math.min(Math.max(z, lo), hi) - lo) / (hi - lo));
    const g = svg('svg', { class: 'bdi-chart bdi-rp-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}` }, chart);
    const yb = H - B; // the plot's bottom
    // axes
    for (const v of niceTicks(lo, hi, Math.max(2, Math.floor((H - T - B) / 40)))) {
      svg('line', { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: 'bdi-rp-grid' }, g);
      svg('text', { x: L - 4, y: Y(v) + 3, class: 'bdi-rp-ax', 'text-anchor': 'end' }, g, String(+v.toFixed(1)));
    }
    svg('text', { x: 4, y: T - 8, class: 'bdi-rp-ax' }, g, 'Ω');
    const xt = niceTicks(0, path.length, Math.max(2, Math.floor((W - L - R) / 70)));
    xt.forEach((v, i) => {
      svg('line', { x1: X(v), x2: X(v), y1: yb + 10, y2: yb + 14, class: 'bdi-rp-tick' }, g);
      svg('text', { x: X(v), y: H - 6, class: 'bdi-rp-ax', 'text-anchor': i === 0 ? 'start' : 'middle' }, g, String(+v.toFixed(2)));
    });
    svg('text', { x: W - R, y: H - 6, class: 'bdi-rp-ax', 'text-anchor': 'end' }, g, 'mm');
    // each unit's target band, its net name
    for (const u of path.units) {
      const t = u.target;
      if (t) {
        const tol = t.tolerance_pct ?? 10;
        const y0 = Y(t.value * (1 + tol / 100)), y1 = Y(t.value * (1 - tol / 100));
        svg('rect', { x: X(u.s0), width: Math.max(1, X(u.s1) - X(u.s0)), y: y0, height: y1 - y0, class: 'bdi-band' }, g);
        svg('line', { x1: X(u.s0), x2: X(u.s1), y1: Y(t.value), y2: Y(t.value), class: 'bdi-target' }, g);
      }
      const name = `${u.nets.length === 2 ? '⇄ ' : ''}${u.nets.map(pathNetName).join(' ')}`;
      const w = X(u.s1) - X(u.s0);
      if (w > 48) {
        const tx = svg('text', { x: X(u.s0) + 4, y: yb - 5, class: 'bdi-rp-net' }, g, name);
        if (name.length * 6 > w - 8) tx.textContent = `${name.slice(0, Math.max(3, Math.floor((w - 8) / 6) - 1))}…`;
      }
    }
    // where the conductor is a pad, teardrop or zone of the net (wider than the track): shaded, pads named
    for (const c of copperSpans(path)) {
      const x0 = X(c.s0), w = Math.max(1, X(c.s1) - x0);
      const r = svg('rect', { x: x0, width: w, y: T, height: yb - T, class: `bdi-rp-cu is-${c.kind}` }, g);
      const words = `${c.text}: the conductor is its copper, up to ${mm(c.width)} mm wide${c.track != null ? ` (track ${mm(c.track)} mm)` : ''}`;
      svg('title', {}, r, words);
      if (c.label && c.label.length * 6 + 4 <= w) svg('title', {}, svg('text', { x: x0 + w / 2, y: yb - 16, class: 'bdi-rp-culabel', 'text-anchor': 'middle' }, g, c.label), words);
    }
    // the layer under the trace
    for (const x of path.sections) {
      const r = svg('rect', { x: X(x.s0), width: Math.max(1, X(x.s1) - X(x.s0)), y: yb + 2, height: 6, class: 'bdi-rp-layer', fill: layerColor(copper, x.section.layer) }, g);
      svg('title', {}, r, x.section.layer);
    }
    // Z: a step line per unit, each section coloured by its verdict
    for (const u of path.units) {
      const secs = path.sections.filter((x) => x.unit === u.index);
      const pts = [];
      for (const x of secs) if (x.z != null) pts.push(`${X(x.s0).toFixed(1)},${Y(x.z).toFixed(1)}`, `${X(x.s1).toFixed(1)},${Y(x.z).toFixed(1)}`);
      if (pts.length) svg('polyline', { points: pts.join(' '), class: 'bdi-rp-step' }, g);
      for (const x of secs) {
        if (x.z == null) { svg('rect', { x: X(x.s0), width: Math.max(1, X(x.s1) - X(x.s0)), y: yb - 3, height: 3, class: 'bdi-noz' }, g); continue; }
        const v = u.target ? verdict(x.z, { tolerance_pct: 10, ...u.target }) : 'none';
        svg('line', { x1: X(x.s0), x2: Math.max(X(x.s1), X(x.s0) + 1), y1: Y(x.z), y2: Y(x.z), class: `bdi-seg bdi-${v}` }, g);
      }
    }
    // parts, vias, reference changes: a line each, labeled at the top where there is room
    let lastLabel = -Infinity;
    for (const m of path.marks) {
      const x = X(m.s);
      const big = m.type === 'part' || m.type === 'end' || m.type === 'gap';
      svg('line', { x1: x, x2: x, y1: T - 4, y2: yb, class: `bdi-rp-mark is-${m.type}` }, g);
      const label = big ? m.label : SYMBOL[m.type] ?? '•';
      const wide = label.length * 6.5 + 6;
      if (x - lastLabel < (big ? wide : 12) && !big) continue;
      const anchor = x < L + wide / 2 ? 'start' : x > W - R - wide / 2 ? 'end' : 'middle';
      const t = svg('text', { x, y: T - 8, class: `bdi-rp-mlabel is-${m.type}`, 'text-anchor': anchor }, g, label);
      const words = m.type === 'part' ? `${m.refs.join(', ')}: the route goes through ${m.refs.length > 1 ? 'them' : 'it'}`
        : m.type === 'end' ? `${m.label}: the route ends here` : m.type === 'gap' ? 'not connected: the next line starts here'
          : `${DISCONTINUITIES[m.type]?.[1] ?? m.type}${m.detail ? ` (${m.detail})` : ''}`;
      svg('title', {}, t, `${words} · ${mm(m.s)} mm`);
      lastLabel = x;
    }
    const cursor = svg('line', { y1: T - 4, y2: yb + 8, class: 'bdi-cursor bdi-rp-cursor', visibility: 'hidden' }, g);
    const dot = svg('circle', { r: 4, class: 'bdi-rp-dot', visibility: 'hidden' }, g);
    const hit = svg('rect', { x: L, y: 0, width: W - L - R, height: H, class: 'bdi-hit' }, g);
    geo = { W, H, L, R, T, B, X, Y, cursor, dot, g };
    chart.setAttribute('aria-valuemax', String(+path.length.toFixed(2)));
    const sOf = (ev) => {
      const r = g.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      return Math.min(path.length, Math.max(0, ((px - L) / (W - L - R)) * path.length));
    };
    let touch = false;
    hit.addEventListener('pointerdown', (ev) => { touch = ev.pointerType !== 'mouse'; set(sOf(ev), true); });
    hit.addEventListener('pointermove', (ev) => { if (ev.pointerType === 'mouse' || ev.buttons) set(sOf(ev), true); });
    hit.addEventListener('pointerleave', (ev) => { if (!touch && ev.pointerType === 'mouse') set(null, true); });
    if (cursorS != null) set(cursorS, false);
  }

  /** The cursor at s (null: none): line, dot, tooltip; `tell` emits 'hover' (the board shows it). */
  function set(s, tell) {
    cursorS = s;
    const at = s != null && path ? pathAt(path, s) : null;
    if (!geo || !at) {
      tip.hidden = true;
      if (geo) { geo.cursor.setAttribute('visibility', 'hidden'); geo.dot.setAttribute('visibility', 'hidden'); }
      chart.removeAttribute('aria-valuenow');
      if (tell) emit('hover', null);
      return null;
    }
    const x = geo.X(at.s);
    geo.cursor.setAttribute('x1', x); geo.cursor.setAttribute('x2', x); geo.cursor.setAttribute('visibility', 'visible');
    if (at.z != null) { geo.dot.setAttribute('cx', x); geo.dot.setAttribute('cy', geo.Y(at.z)); geo.dot.setAttribute('visibility', 'visible'); }
    else geo.dot.setAttribute('visibility', 'hidden');
    const lines = tipLines(at);
    tip.replaceChildren(...lines.map((l, i) => el('div', i ? 'bdi-rp-tl' : 'bdi-rp-tz', null, l)));
    tip.hidden = false;
    const cw = chart.clientWidth || geo.W;
    const left = (x / geo.W) * cw;
    tip.style.left = `${Math.min(Math.max(0, left + 10), Math.max(0, cw - 230))}px`;
    tip.style.top = `${chart.offsetTop + 4}px`;
    if (left + 10 > cw - 230) tip.style.left = `${Math.max(0, left - 240)}px`;
    chart.setAttribute('aria-valuenow', String(+at.s.toFixed(2)));
    chart.setAttribute('aria-valuetext', lines.join(', '));
    if (tell) emit('hover', { s: at.s, at });
    return at;
  }

  function tipLines(at) {
    const sec = at.section, u = at.unit;
    const t = u.target;
    const v = at.z != null && t ? verdict(at.z, { tolerance_pct: 10, ...t }) : null;
    const key = u.key === 'Zdiff' ? 'Zdiff' : 'Z0';
    const out = [`${at.z == null ? 'no Z' : `${f1(at.z)} Ω`}${v ? ` ${MARK[v]}` : ''} · ${mm(+at.s.toFixed(2))} mm${at.z != null ? ` (${key})` : ''}`];
    if (!sec) return [...out, u.nets.join(' ')];
    const g = sec.geometry ?? {};
    out.push(`${u.nets.map(pathNetName).join(' ')} · ${sec.layer} · ${STRUCTURES[sec.structure]?.[0] ?? sec.structure}`);
    const cu = copperText(sec);
    out.push(`w ${mm(g.width)} mm${cu ? ` (${cu})` : ''}${g.gap != null ? ` · gap ${mm(g.gap)} mm` : ''}${(g.coplanar_gap ?? []).some((x) => x != null) ? ` · ground ${mm(Math.min(...g.coplanar_gap.filter((x) => x != null)))} mm` : ''}`);
    out.push(planesText(sec));
    return out;
  }

  async function analyseAll(myRun) {
    for (let i = 0; i < units.length; i++) {
      if (myRun !== run) return;
      const k = cacheKey(units[i], perUnit[i]);
      if (cache.has(k)) { docs[i] = cache.get(k); continue; }
      progEl.textContent = units.length > 1 ? `${i + 1}/${units.length} …` : '…';
      try {
        const u = units[i];
        const doc = await an.analyze(u.length === 2 ? u : u[0], { ...options, ...(perUnit[i] ?? {}) });
        if (myRun !== run) return;
        cache.set(k, doc);
        docs[i] = doc;
      } catch (err) {
        if (err?.message === 'superseded' || myRun !== run) return;
        docs[i] = null;
        emit('error', err);
      }
      rebuild();
    }
    if (myRun !== run) return;
    progEl.textContent = '';
    root.dataset.state = path ? 'ready' : 'empty';
    rebuild();
  }

  const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => { if (path) draw(); }) : null;
  ro?.observe(chart);

  chart.addEventListener('keydown', (e) => {
    if (!path) return;
    const step = (e.shiftKey ? 0.1 : 0.01) * path.length;
    const s = cursorS ?? 0;
    let next = null;
    if (e.key === 'ArrowRight') next = Math.min(path.length, s + step);
    else if (e.key === 'ArrowLeft') next = Math.max(0, s - step);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = path.length;
    else if (e.key === 'Escape' && cursorS != null) { e.stopPropagation(); set(null, true); return; }
    else return;
    e.preventDefault();
    set(next, true);
  });
  chart.addEventListener('focus', () => { if (path && cursorS == null) set(0, true); });

  const api = {
    root,
    analyzer: an,
    get path() { return path; },
    get docs() { return [...docs]; },
    get cursor() { return cursorS; },
    get state() { return root.dataset.state; },
    /** Analyse and draw `units` ([[net] | [p, n]]): resolves when every one is in. */
    select(list, { targets: t = [], options: o = [] } = {}) {
      units = (list ?? []).map((u) => [...u]);
      targets = t; perUnit = o;
      docs = units.map((u, i) => cache.get(cacheKey(u, perUnit[i])) ?? null);
      cursorS = null;
      tip.hidden = true;
      const myRun = ++run;
      root.dataset.state = !units.length ? 'empty' : docs.every(Boolean) ? 'ready' : 'busy';
      rebuild();
      if (!units.length || docs.every(Boolean)) { progEl.textContent = ''; return Promise.resolve(path); }
      return analyseAll(myRun).then(() => path);
    },
    /** New targets per unit (no re-run). */
    setTargets(t) { targets = t ?? []; if (path) rebuild(); },
    /** Put the cursor at path position s (null: none) without emitting; returns pathAt's. */
    show(s) { return set(s, false); },
    /** A point on a selected trace → its path position, the cursor there: pathFromBoard's, or null. */
    showBoard(net, p, track = null) {
      const hit = path ? pathFromBoard(path, copper, net, p, track) : null;
      const at = set(hit ? hit.s : null, false);
      return hit && at ? { ...hit, at } : null;
    },
    on(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); return () => listeners.get(name)?.delete(fn); },
    destroy() { run++; ro?.disconnect(); if (own) an.destroy?.(); root.remove(); },
  };
  return api;
}
