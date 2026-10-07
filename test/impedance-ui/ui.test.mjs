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
