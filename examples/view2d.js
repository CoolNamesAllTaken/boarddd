// boarddd/view2d demo: the app chrome (buttons, sliders, status line, URL) is this file's; the stage,
// compare modes, measure, overlays and picking are boarddd/view2d.
import * as v2 from 'boarddd/view2d/index.js';
import * as gerber from 'boarddd/gerber/index.js';

const $ = (id) => document.getElementById(id);
const text = (url) => fetch(url).then((r) => { if (!r.ok) throw new Error(`${r.status} ${url}`); return r.text(); });
const load = (dir, names) => Promise.all(names.map(async (name) => ({ name, source: await text(`${dir}/${name}`) })));

const PIC = ['B_Mask.gbr', 'bottom_layer.gbr', 'B_Silkscreen.gbr', 'Edge_Cuts.gbr', 'F_Mask.gbr', 'F_Silkscreen.gbr', 'NPTH.drl', 'PTH.drl', 'top_layer.gbr']
  .map((f) => `pic_programmer-${f}`);
const RB = ['B_Cu', 'B_Mask', 'B_Paste', 'B_Silkscreen', 'Edge_Cuts', 'F_Cu', 'F_Mask', 'F_Paste', 'F_Silkscreen', 'In1_Cu', 'In2_Cu', 'In3_Cu', 'In4_Cu', 'In5_Cu', 'In6_Cu']
  .map((f) => `RoyalBlue54L-Feather-${f}.gbr`).concat(['RoyalBlue54L-Feather-NPTH.drl', 'RoyalBlue54L-Feather-PTH.drl']);
const SHEET = { minX: 120, maxX: 200, minY: -60, maxY: -5 }; // sheet mm 120,5..200,60 (y negated: world y is up)
const VIEWS = [['top', 'Top'], ['bottom', 'Bottom'], ['layers', 'Layers']];
const MODES = { side: 'Side by side', diff: 'Diff', onion: 'Onion', swipe: 'Swipe', base: 'Base', head: 'Head' };
const PALETTE = { pic: { mask: 'green', finish: 'hasl' }, rb: { mask: 'blue', finish: 'enig' } };

const [picBase, picHead, rb, pos, sheetBase, sheetHead, renderer] = await Promise.all([
  load('../test/fixtures/pic_programmer/base', PIC),
  load('../test/fixtures/pic_programmer/head', PIC),
  load('../fixtures/royalblue54L_feather/fab', RB),
  text('../fixtures/royalblue54L_feather/fab/pos.csv'),
  text('../test/fixtures/pic_programmer_sch/base.svg'),
  text('../test/fixtures/pic_programmer_sch/head.svg'),
  gerber.createGerberRenderer(document.createElement('canvas')),
]);

const boundsOf = (...lists) => gerber.unionBounds(lists.map((l) => gerber.padBounds(gerber.gerberExtents(l.find((f) => /Edge_Cuts/.test(f.name)).source), 2)));
const BOARDS = {
  pic: { base: picBase, head: picHead, bounds: boundsOf(picBase, picHead) },
  rb: { base: null, head: rb, bounds: boundsOf(rb) },
};
const placements = pos.trim().split(/\r?\n/).slice(1).map((l) => {
  const [ref, , , x, y, rot, side] = l.split(',').map((s) => s.replace(/"/g, ''));
  return { ref, x: +x, y: +y, rot: +rot, side };
});

// --- state: in the URL hash, so a reload or a shared link opens the same view
const hash = new URLSearchParams(location.hash.slice(1));
const state = { board: hash.get('b') || 'pic', view: hash.get('v') || 'top', layer: hash.get('l') || '', marks: hash.get('m') !== '0', ...v2.parseViewState(hash) };
let stage = null;
let cmp = null;
let regions = [];
let picked = null;
let overlays = [];
const cache = new Map(); // same key -> same content object, so tiles are reused across switches
const memo = (key, make) => { if (!cache.has(key)) cache.set(key, make()); return cache.get(key); };

function writeHash() {
  const p = new URLSearchParams({ b: state.board, v: state.view, ...(state.layer ? { l: state.layer } : {}), ...(state.marks ? {} : { m: '0' }),
    ...v2.formatViewState(cmp ? cmp.getState() : { region: stage?.getRegion() }) });
  history.replaceState(null, '', `#${p}`);
}
let hashTimer = 0;
const writeSoon = () => { clearTimeout(hashTimer); hashTimer = setTimeout(writeHash, 250); };

function sideContent(side) {
  const b = BOARDS[state.board];
  const files = b[side];
  if (!files) return null;
  const f = files.find((x) => x.name === state.layer);
  if (state.layer) return f ? memo(`${state.board}|${side}|${state.layer}`, () => v2.layers([{ ...f, color: v2.layerStack([f])[0].color, alpha: 1 }])) : null;
  if (state.view === 'layers') return memo(`${state.board}|${side}|layers`, () => v2.layers(v2.layerStack(files)));
  return memo(`${state.board}|${side}|${state.view}`, () => v2.face(v2.faceBoard(files, state.view), { side: state.view, palette: PALETTE[state.board] }));
}

function diffContent() {
  const b = BOARDS[state.board];
  const pick = (files) => {
    if (state.layer) return files.find((x) => x.name === state.layer) || null;
    const side = state.view === 'bottom' ? 'bottom' : 'top';
    return v2.layerStack(files).find((l) => l.role === 'copper' && l.side === side) || null;
  };
  const one = (f) => (f ? { source: f.source, name: f.name } : null);
  return memo(`${state.board}|diff|${state.view}|${state.layer}`, () => v2.diff(one(pick(b.base)), one(pick(b.head)), { regions: true }));
}

function build() {
  const region = stage?.getRegion() ?? state.region ?? null;
  stage?.destroy();
  cmp?.destroy();
  cmp = null;
  regions = [];
  picked = null;
  overlays = [];
  const sheet = state.board === 'sch';
  const b = BOARDS[state.board];
  stage = v2.createStage($('stage'), { renderer, bounds: sheet ? SHEET : b.bounds, flip: !sheet && state.view === 'bottom', background: sheet ? '#f5f4ef' : '#1c1f24' });
  stage.on('move', (e) => { $('xy').textContent = `x ${e.x.toFixed(3)}  y ${e.y.toFixed(3)} mm`; });
  stage.on('leave', () => { $('xy').textContent = ''; });
  stage.on('measure', (e) => { $('meas').textContent = v2.measureText(e.result); });
  stage.on('view', writeSoon);
  stage.on('render', (e) => {
    if (!e.info.regions) return;
    regions = e.info.regions;
    $('note').textContent = `${regions.length} changed area${regions.length === 1 ? '' : 's'}`;
    marks.invalidate();
  });
  // change boxes in mm (world space: drawn once, moved with the view)
  const marks = stage.addOverlay({ space: 'world', draw: (g, ctx) => {
    if (!state.marks) return;
    for (const r of regions) ctx.svg('rect', { x: r.minX - 0.5, y: r.minY - 0.5, width: r.maxX - r.minX + 1, height: r.maxY - r.minY + 1, class: 'change' }, g);
  } });
  overlays.push(marks);
  // placement ticks: constant size on screen, redrawn per view (gentoo's viewer markers)
  if (state.board === 'rb') {
    overlays.push(stage.addOverlay({ space: 'screen', className: 'tick', draw: (g, ctx) => {
      if (!state.marks || ctx.view.s < 4) return;
      const side = state.view === 'bottom' ? 'bottom' : 'top';
      for (const p of placements) {
        if (state.view !== 'layers' && p.side !== side) continue;
        const [x, y] = ctx.toScreen(p.x, p.y);
        const a = ((ctx.flip ? 180 - p.rot : p.rot) + 90) * Math.PI / 180;
        ctx.svg('circle', { cx: x, cy: y, r: 5 }, g);
        ctx.svg('line', { x1: x, y1: y, x2: x + Math.cos(a) * 11, y2: y - Math.sin(a) * 11 }, g);
      }
    } }));
  }
  // picking: the holes of the board on show
  if (!sheet) {
    const files = b.head;
    const holes = files.filter((f) => /\.drl$/.test(f.name)).flatMap((f) => gerber.parseExcellon(f.source));
    const index = v2.createHitIndex(holes.map((h) => (h.x2 == null ? v2.circleShape(h.x, h.y, h.diameter, h) : v2.segmentShape(h.x, h.y, h.x2, h.y2, h.diameter, h))));
    const ring = stage.addOverlay({ space: 'screen', draw: (g, ctx) => {
      if (!picked) return;
      const [x, y] = ctx.toScreen(picked.x, picked.y);
      ctx.svg('circle', { cx: x, cy: y, r: picked.diameter / 2 * ctx.view.s + 4, class: 'picked' }, g);
    } });
    stage.on('click', (e) => {
      if (stage.tool !== 'pan') return;
      picked = index.at(e.x, e.y, 4 * stage.mmPerPx())?.data ?? null;
      $('pick').textContent = picked ? `${picked.plated ? 'PTH' : 'NPTH'} Ø${picked.diameter} mm at ${picked.x.toFixed(3)}, ${picked.y.toFixed(3)}` : '';
      ring.invalidate();
    });
  }
  if (state.board === 'rb') {
    stage.setScene([{ label: state.layer || state.view, layers: [{ content: sideContent('head') }] }]);
  } else if (sheet) {
    cmp = v2.createCompare(stage, { base: v2.image(sheetBase, SHEET), head: v2.image(sheetHead, SHEET), mode: state.mode, opacity: state.opacity, swipe: state.swipe, onChange: writeSoon });
  } else {
    const under = state.view === 'layers' || state.layer ? null : sideContent('head');
    cmp = v2.createCompare(stage, { base: sideContent('base'), head: sideContent('head'), diff: diffContent(), underlay: under, mode: state.mode, opacity: state.opacity, swipe: state.swipe, onChange: writeSoon });
  }
  stage.setTool($('measure').getAttribute('aria-pressed') === 'true' ? 'measure' : 'pan');
  stage.setRegion(region);
  chrome();
  writeHash();
}

// --- chrome
function buttons(el, items, current, onPick) {
  el.replaceChildren(...items.map(([k, label]) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.setAttribute('aria-pressed', String(k === current));
    b.onclick = () => onPick(k);
    return b;
  }));
}

function chrome() {
  const sheet = state.board === 'sch';
  $('views').hidden = sheet;
  $('layer').hidden = sheet;
  buttons($('views'), VIEWS, state.layer ? null : state.view, (v) => { state.view = v; state.layer = ''; build(); });
  const files = BOARDS[state.board]?.head || [];
  $('layer').replaceChildren(new Option('board view', ''), ...v2.layerStack(files).reverse().map((l) => new Option(l.name.replace(/^.*?-(?=[A-Z]|top|bottom)/, ''), l.name)));
  $('layer').value = state.layer;
  $('modes').hidden = !cmp;
  if (cmp) buttons($('modes'), cmp.modes.map((m) => [m, MODES[m]]), cmp.mode, (m) => { cmp.setMode(m); state.mode = m; chrome(); writeHash(); });
  const slider = cmp && (cmp.mode === 'onion' || cmp.mode === 'swipe');
  $('opl').hidden = !slider;
  if (slider) {
    $('opl').firstChild.textContent = cmp.mode === 'onion' ? 'head opacity ' : 'swipe ';
    $('opacity').value = cmp.mode === 'onion' ? cmp.opacity : cmp.swipe;
  }
  $('ticks').setAttribute('aria-pressed', String(state.marks));
  if (!cmp) $('note').textContent = '';
}

$('board').value = state.board;
$('board').onchange = () => { state.board = $('board').value; state.layer = ''; state.region = null; stage?.setRegion(null); build(); };
$('layer').onchange = () => { state.layer = $('layer').value; build(); };
$('opacity').oninput = () => {
  const v = +$('opacity').value;
  if (cmp.mode === 'onion') { cmp.setOpacity(v); state.opacity = v; } else { cmp.setSwipe(v); state.swipe = v; }
  writeSoon();
};
$('measure').onclick = () => {
  const on = stage.tool !== 'measure';
  stage.setTool(on ? 'measure' : 'pan');
  $('measure').setAttribute('aria-pressed', String(on));
  if (!on) $('meas').textContent = '';
};
$('ticks').onclick = () => { state.marks = !state.marks; for (const o of overlays) o.invalidate(); chrome(); writeHash(); };
$('fit').onclick = () => stage.fit();
build();
window.demo = { get stage() { return stage; }, get compare() { return cmp; } };
