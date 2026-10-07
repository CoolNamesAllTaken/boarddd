// boarddd/impedance analyzeNet: impedance along a route on a real board (python/tests/test_impedance_route.py runs
// the same boards and checks Python = JS). The synthetic boards (fixtures/impedance/route/make_route_boards.py)
// against tier 1; royalblue54L_feather's USB pair for the real-board path, the cache and the solver accuracy.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import * as z from '../../src/impedance/index.js';

const { analyzeNet, netRoute, validateImpedance, lineFromStackup, calculate } = z;
const load = (name) => {
  const raw = readFileSync(new URL(`../../fixtures/impedance/route/${name}`, import.meta.url));
  return JSON.parse((name.endsWith('.gz') ? gunzipSync(raw) : raw).toString('utf8'));
};
const rel = (a, b) => Math.abs(a - b) / Math.abs(b);
const tier1 = (stackup, layer, o) => { const l = lineFromStackup(stackup, layer, o); return calculate(l.model, l.params); };
const run = (data, net, o) => { const d = analyzeNet(data.board, data.copper, net, o); assert.deepEqual(validateImpedance(d), []); return d; };

test('index.d.ts declares exactly the runtime exports', async () => {
  const { default: ts } = await import('typescript');
  const file = new URL('../../src/impedance/index.d.ts', import.meta.url).pathname;
  const program = ts.createProgram([file], { noEmit: true, lib: ['lib.es2022.d.ts', 'lib.dom.d.ts'], moduleResolution: ts.ModuleResolutionKind.Bundler, module: ts.ModuleKind.ESNext });
  const checker = program.getTypeChecker();
  const exports = checker.getExportsOfModule(checker.getSymbolAtLocation(program.getSourceFile(file)));
  const declared = exports.filter((s) => (s.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(s) : s).flags & ts.SymbolFlags.Value).map((s) => s.name);
  assert.deepEqual(Object.keys(z).sort(), declared.sort());
});

test('a microstrip across a plane split takes the next plane, and none when there is none', () => {
  const data = load('split.json');
  const d = run(data, 'SIG');
  const [a, gap, b] = d.sections;
  assert.deepEqual(d.sections.map((s) => [s.s0, s.s1]), [[0, 14], [14, 16], [16, 30]]);
  assert.deepEqual([a.refs[0].layer, gap.refs[0].layer, b.refs[0].layer], ['In1.Cu', 'In2.Cu', 'In1.Cu']);
  assert.deepEqual(gap.refs[0].skipped, ['In1.Cu']);
  assert.deepEqual(gap.flags, ['plane_gap']);
  assert.ok(rel(a.z.Z0, tier1(data.board.stackup, 'F.Cu', { width: 0.35 }).Z0) < 0.015);
  assert.ok(rel(gap.z.Z0, tier1(data.board.stackup, 'F.Cu', { width: 0.35, refBottom: 'In2.Cu' }).Z0) < 0.03);
  assert.deepEqual(d.discontinuities.map((x) => x.type), ['ref_change', 'plane_gap', 'ref_change']);
  assert.equal(d.summary.out_of_tolerance_length, 2);
  const bare = run(data, 'BARE');
  assert.equal(bare.sections[1].z, null);
  assert.deepEqual(bare.sections[1].flags, ['no_ref', 'plane_gap']);
});

test('cpwg gap, offset stripline, embedded microstrip', () => {
  const cp = load('cpwg.json');
  const [narrow, wide] = run(cp, 'RF').sections;
  assert.deepEqual([narrow.structure, wide.structure], ['cpwg', 'cpwg']);
  for (const s of [narrow, wide]) {
    const ref = tier1(cp.board.stackup, 'F.Cu', { width: 0.3, structure: 'coplanar_grounded', coplanarGap: s.geometry.coplanar_gap[0] }).Z0;
    assert.ok(rel(s.z.Z0, ref) < 0.03, `${s.z.Z0} vs ${ref}`);
  }
  assert.ok(narrow.z.Z0 < wide.z.Z0);
  const inner = load('inner.json');
  const [strip, emb] = run(inner, 'DATA').sections;
  assert.deepEqual([strip.structure, emb.structure], ['offset_stripline', 'embedded_microstrip']);
  assert.ok(rel(strip.z.Z0, tier1(inner.board.stackup, 'In1.Cu', { width: 0.15 }).Z0) < 0.02);
});

test('a pair breaking out: coupled in the middle, two single lines at the ends', () => {
  const data = load('pair.json');
  const d = run(data, ['USB_P', 'USB_N']);
  assert.deepEqual(d.sections.map((s) => `${s.net}:${s.kind}`), ['USB_P:single', 'USB_P:differential', 'USB_P:single', 'USB_N:single', 'USB_N:single']);
  const ref = tier1(data.board.stackup, 'F.Cu', { width: 0.25, kind: 'differential', gap: 0.15 }).Zdiff;
  assert.ok(rel(d.sections[1].z.Zdiff, ref) < 0.01);
  assert.equal(d.target.key, 'Zdiff');
  assert.deepEqual(netRoute(data.copper, 'USB_P').map((e) => e.track.id), ['p-in', 'p-run', 'p-out']);
});

test('overrides, tier 1, the shared cache', () => {
  const data = load('cpwg.json');
  const forced = run(data, 'RF', { overrides: { net: { structure: 'microstrip' } } });
  assert.deepEqual(forced.sections.map((s) => [s.structure, s.flags]), [['microstrip', ['override']]]);
  const t1 = run(load('split.json'), 'SIG', { solver: 'closedform' });
  assert.equal(t1.sections[0].z.model, 'coated_microstrip');
  const cache = new Map();
  const pair = load('pair.json');
  const first = run(pair, ['USB_P', 'USB_N'], { cache });
  const again = run(pair, ['USB_P', 'USB_N'], { cache });
  assert.ok(first.timing.solves > 0);
  assert.equal(again.timing.solves, 0);
  assert.deepEqual(again.sections, first.sections);
});

test('royalblue54L_feather USB pair: real copper, timing, accuracy against finer solves', (t) => {
  const data = load('royalblue-usb.json.gz');
  const d = run(data, ['/Debugger/D+', '/Debugger/D-']);
  t.diagnostic(`analyzeNet: ${d.timing.ms} ms (${d.timing.solves} cross-sections solved in ${d.timing.solve_ms} ms, ${d.timing.stations} stations)`);
  assert.ok(Math.abs(d.summary.length - 22.55) < 0.01);
  assert.ok(d.discontinuities.some((x) => x.type === 'via' && x.detail === 'B.Cu → F.Cu'));
  const main = d.sections.filter((s) => s.kind === 'differential').sort((a, b) => b.length - a.length)[0];
  assert.ok(main.z.Zdiff > 105 && main.z.Zdiff < 115, String(main.z.Zdiff));
  // one grid level finer everywhere: the long cross-sections move < 1 %, the short ones < 4 %, the summary < 0.6 %
  const fine = analyzeNet(data.board, data.copper, ['/Debugger/D+', '/Debugger/D-'],
    { fieldOptions: { level: 0, maxLevel: 1 }, shortFieldOptions: { level: 0, maxLevel: 1 } });
  const cover = new Map();
  for (const s of d.sections) { const k = JSON.stringify([s.layer, s.structure, s.geometry, s.refs]); cover.set(k, (cover.get(k) ?? 0) + s.length); }
  d.sections.forEach((s, i) => {
    if (!s.z) return;
    const k = s.kind === 'differential' ? 'Zdiff' : 'Z0';
    const long = cover.get(JSON.stringify([s.layer, s.structure, s.geometry, s.refs])) >= 1;
    assert.ok(rel(s.z[k], fine.sections[i].z[k]) < (long ? 0.01 : 0.04), `${s.s0} ${s.structure} ${s.z[k]} vs ${fine.sections[i].z[k]}`);
  });
  assert.ok(rel(d.summary.z_weighted, fine.summary.z_weighted) < 0.006);
  // a second analysis sharing the cache solves nothing
  const cache = new Map();
  run(data, ['/Debugger/D+', '/Debugger/D-'], { cache });
  const again = run(data, ['/Debugger/D+', '/Debugger/D-'], { cache });
  assert.equal(again.timing.solves, 0);
  t.diagnostic(`again with the cache: ${again.timing.ms} ms`);
});
