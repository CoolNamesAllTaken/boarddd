// boarddd/impedance/ui, the parts without a DOM: copper picking, pairs, route slices, the cross-section layout,
// the gbrjob stackup (boarddd/model) and analyzeNet's progress. The browser parts: impedance-ui.spec.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as ui from '../../src/impedance/ui/index.js';
import { analyzeNet, netRoute } from '../../src/impedance/index.js';
import { stackupFromJob } from '../../src/model/index.js';

const load = (rel) => JSON.parse(readFileSync(new URL(`../../fixtures/${rel}`, import.meta.url), 'utf8'));

test('index.d.ts declares exactly the runtime exports', async () => {
  const { default: ts } = await import('typescript');
  const file = new URL('../../src/impedance/ui/index.d.ts', import.meta.url).pathname;
  const program = ts.createProgram([file], { noEmit: true, lib: ['lib.es2022.d.ts', 'lib.dom.d.ts'], moduleResolution: ts.ModuleResolutionKind.Bundler, module: ts.ModuleKind.ESNext });
  const checker = program.getTypeChecker();
  const exports = checker.getExportsOfModule(checker.getSymbolAtLocation(program.getSourceFile(file)));
  const declared = exports.filter((s) => (s.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(s) : s).flags & ts.SymbolFlags.Value).map((s) => s.name);
  assert.deepEqual(Object.keys(ui).sort(), declared.sort());
});

test('copperHitIndex picks tracks over the plane, and the pair', () => {
  const { copper, board } = load('impedance/route/pair.json');
  const index = ui.copperHitIndex(copper);
  // the P run is y = 0.2 (w 0.25), N at y = -0.2; the GND plane is on In1.Cu under both
  assert.equal(index.at(10, 0.2).data.net, 'USB_P');
  assert.equal(index.at(10, 0.2).data.kind, 'track');
  assert.equal(index.at(10, -0.2).data.width, 0.25);
  assert.equal(index.at(10, 0).data.kind, 'zone'); // between the lines: only the plane below
  const top = ui.copperHitIndex(copper, { layers: ['F.Cu'] });
  assert.equal(top.at(10, 0), null);
  assert.equal(ui.pairOf('USB_P', { board }), null); // the synthetic board@1 sets no Net.pair
  assert.equal(ui.pairOf('USB_P', { names: copper.nets }), 'USB_N');
  assert.equal(ui.pairOf('/D+', { names: ['/D+', '/D-'] }), '/D-');
  assert.equal(ui.pairOf('GND', { names: copper.nets }), null);
  assert.deepEqual(ui.pairOrder('USB_N', 'USB_P'), ['USB_P', 'USB_N']);
});

test('routeSlice / routePoint follow the route', () => {
  const { copper } = load('impedance/route/pair.json');
  const route = netRoute(copper, 'USB_P');
  const p = ui.routePoint(route, 10);
  assert.ok(Math.abs(p.p[1] - 0.2) < 1e-9 && p.layer === 'F.Cu');
  const parts = ui.routeSlice(route, 5, 8, ['p-run']);
  assert.equal(parts.length, 1);
  const pts = parts[0].points;
  assert.ok(Math.abs(Math.hypot(pts.at(-1)[0] - pts[0][0], pts.at(-1)[1] - pts[0][1]) - 3) < 1e-9);
  assert.equal(ui.trackPath({ start: [0, 0], end: [1, 0] }).length, 2);
  const arc = ui.arcPoints({ start: [1, 0], mid: [0, 1], end: [-1, 0] });
  assert.ok(arc.every(([x, y]) => Math.abs(Math.hypot(x, y) - 1) < 1e-9 && y >= -1e-9));
});

test('sectionLayout rebuilds what analyzeNet solved', () => {
  const data = load('impedance/route/cpwg.json');
  const doc = analyzeNet(data.board, data.copper, 'RF');
  const lay = ui.sectionLayout(doc.sections[0]);
  assert.deepEqual(lay.traces, [{ x0: -0.15, x1: 0.15, net: 'sig' }]);
  const g = doc.sections[0].geometry.coplanar_gap;
  assert.deepEqual(lay.grounds, [{ x0: null, x1: -0.15 - g[0] }, { x0: 0.15 + g[1], x1: null }]);
  const pair = load('impedance/route/pair.json');
  const sec = analyzeNet(pair.board, pair.copper, ['USB_P', 'USB_N']).sections.find((s) => s.kind === 'differential');
  const pl = ui.sectionLayout(sec);
  assert.equal(pl.traces.length, 2);
  assert.ok(Math.abs(pl.traces[1].x0 - pl.traces[0].x1 - sec.geometry.gap) < 1e-12);
  assert.equal(ui.verdict(95, { value: 90, tolerance_pct: 10 }), 'ok');
  assert.equal(ui.verdict(98, { value: 90, tolerance_pct: 10 }), 'warn');
  assert.equal(ui.verdict(100, { value: 90, tolerance_pct: 10 }), 'bad');
  assert.equal(ui.verdict(null, null), 'bad');
});

test('stackupFromJob: the royalblue gbrjob, 8 copper layers with their ids', () => {
  const st = stackupFromJob(readFileSync(new URL('../../fixtures/royalblue54L_feather/fab/RoyalBlue54L-Feather-job.gbrjob', import.meta.url), 'utf8'));
  const cu = st.layers.filter((l) => l.kind === 'copper');
  assert.deepEqual(cu.map((l) => l.layer), ['F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'In5.Cu', 'In6.Cu', 'B.Cu']);
  assert.equal(st.copper_layers, 8);
  assert.equal(st.thickness, 1.6);
  assert.equal(st.finish, 'ENIG');
  assert.deepEqual(st.layers.filter((l) => l.kind === 'dielectric').map((l) => l.thickness), [0.1, 0.3, 0.1, 0.3, 0.1, 0.3, 0.1]);
  assert.equal(st.mask_color.top, 'Blue');
  assert.deepEqual(stackupFromJob('not json').layers, []);
});

test('analyzeNet reports progress: the route, then each section solved', () => {
  const data = load('impedance/route/split.json');
  const seen = [];
  analyzeNet(data.board, data.copper, 'SIG', { onProgress: (p) => seen.push(p) });
  const route = seen.filter((p) => p.phase === 'route'), solve = seen.filter((p) => p.phase === 'solve');
  assert.ok(route.length && solve.length);
  assert.ok(seen.indexOf(route.at(-1)) < seen.indexOf(solve[0]));
  assert.deepEqual(solve.at(-1), { phase: 'solve', done: 3, total: 3 });
});

test('layerStops: copper top to bottom with the dielectrics between (sublayers merged); none without a stackup', () => {
  const { board, copper } = load('impedance/route/inner.json');
  const stops = ui.layerStops(copper, board.stackup);
  assert.deepEqual(stops.map((s) => s.kind === 'copper' ? s.layer : 'd'), copper.layers.flatMap((l, i) => (i ? ['d', l] : [l])));
  const d = stops.find((s) => s.kind === 'dielectric');
  assert.ok(d.thickness > 0 && d.epsilon_r > 1);
  assert.deepEqual(ui.layerStops(copper, null).map((s) => s.kind), stops.map((s) => s.kind));
});

test('mainSection: the structure most of the route is', () => {
  const { board, copper } = load('impedance/route/inner.json');
  const net = copper.nets.find((n) => n && n !== 'GND');
  const doc = analyzeNet(board, copper, net);
  const s = ui.mainSection(doc);
  const longest = Math.max(...doc.sections.filter((x) => x.z).map((x) => x.length));
  assert.ok(s.length >= longest - 1e-9 || doc.sections.filter((x) => x.structure === s.structure).reduce((t, x) => t + x.length, 0) >= longest - 1e-9);
  assert.equal(ui.mainSection(null), null);
});

test('fuzzyNets: a name part first, then anywhere, then the letters in order; a partner on top', () => {
  const names = ['/1090_RF_IN', 'XRFOO', 'GND', '/USB_D+', '/USB_D-', 'N12', 'N120', 'REF_CLK'];
  assert.deepEqual(ui.fuzzyNets(names, 'rf').slice(0, 2), ['/1090_RF_IN', 'XRFOO']);
  assert.equal(ui.fuzzyNets(names, 'n12')[0], 'N12');
  assert.deepEqual(ui.fuzzyNets(names, 'usbd').sort(), ['/USB_D+', '/USB_D-']);
  assert.deepEqual(ui.fuzzyNets(names, 'zz'), []);
  assert.equal(ui.fuzzyNets(names, '', { first: ['/USB_D-'] })[0], '/USB_D-');
  assert.equal(ui.netScore('/1090_RF_IN', 'rf'), 1);
  assert.equal(ui.netScore('REF_CLK', 'rf'), 5); // r, a gap, f: the letters in order
});

test('reference planes: the split board crosses a 2 mm gap in In1.Cu (In2.Cu under it); words, stackup fallback', () => {
  const { board, copper } = load('impedance/route/split.json');
  const doc = analyzeNet(board, copper, 'SIG', { solver: 'closedform', step: 0.5 });
  const breaks = ui.referenceBreaks(doc);
  assert.deepEqual(breaks.map((b) => b.type), ['plane_gap', 'ref_change', 'ref_change']);
  assert.equal(breaks[0].plane, 'In1.Cu');
  assert.equal(breaks[0].text, 'crosses a split in In1.Cu at 14.0, 2.0');
  assert.equal(breaks[1].text, 'reference changes: In1.Cu (GND) → In2.Cu (+3V3) at 14.0, 2.0');
  assert.equal(breaks[2].text, 'reference changes: In2.Cu (+3V3) → In1.Cu (GND) at 16.0, 2.0');
  assert.equal(ui.referenceText([{ side: 'bottom', layer: 'In1.Cu', net: 'GND', h: 0.2 }]), 'over In1.Cu (GND) · 0.2 mm');
  assert.equal(ui.referenceText([{ side: 'top', layer: 'F.Cu', net: '', h: 0.2 }, { side: 'bottom', layer: 'In2.Cu', net: 'GND', h: 1 }], 0.15),
    'between F.Cu · 0.2 mm and In2.Cu (GND) · 1 mm · ground beside it, g 0.15 mm');
  assert.deepEqual(ui.stackupRefs(board.stackup, 'F.Cu').map((r) => [r.side, r.layer, r.h]), [['bottom', 'In1.Cu', 0.2]]);
  assert.deepEqual(ui.stackupRefs(board.stackup, 'F.Cu', 'In2.Cu').map((r) => [r.layer, r.h]), [['In2.Cu', 1.235]]);
  assert.deepEqual(ui.routedOn(copper, 'SIG'), { layer: 'F.Cu', width: 0.35 });
  const e = ui.netEntries(copper).find((n) => n.name === 'SIG');
  assert.deepEqual(e.layers, ['F.Cu']);
  // an override to a layer on the other side of some of a net's stations is left as found there
  const ov = analyzeNet(board, copper, 'SIG', { solver: 'closedform', step: 0.5, overrides: { net: { refTop: 'In2.Cu' } } });
  assert.ok(ov.sections.every((s) => s.refs.every((r) => r.side === 'bottom')));
});
