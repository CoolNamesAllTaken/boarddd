// boarddd/nets: patterns and tracks like a picked one, against fixtures/nets/cases.json (python checks the same file),
// plus the typings and the rules spelled out.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import * as nets from '../../src/nets/index.js';

const fixture = (rel) => new URL(`../../fixtures/${rel}`, import.meta.url);
const CASES = JSON.parse(readFileSync(fixture('nets/cases.json'), 'utf8'));
const CM5 = JSON.parse(gunzipSync(readFileSync(fixture('impedance/route/cm5.json.gz'))));
const PAIR = JSON.parse(readFileSync(fixture('impedance/route/pair.json'), 'utf8'));
const COPPER = { cm5: CM5.copper, pair: PAIR.copper, ...CASES.copper };
const BOARDS = { cm5: CM5.board, pair: PAIR.board, lanes: null };

test('index.d.ts declares exactly the runtime exports', async () => {
  const { default: ts } = await import('typescript');
  const file = new URL('../../src/nets/index.d.ts', import.meta.url).pathname;
  const program = ts.createProgram([file], { noEmit: true, lib: ['lib.es2022.d.ts', 'lib.dom.d.ts'], moduleResolution: ts.ModuleResolutionKind.Bundler, module: ts.ModuleKind.ESNext });
  const checker = program.getTypeChecker();
  const exports = checker.getExportsOfModule(checker.getSymbolAtLocation(program.getSourceFile(file)));
  assert.deepEqual(Object.keys(nets).sort(), exports.map((s) => s.name).filter((n) => !/^[A-Z]/.test(n)).sort());
});

test('cases.json patterns give the stored results', () => {
  for (const c of CASES.patterns) {
    assert.deepEqual(nets.matchNets(c.names, c.patterns, { board: BOARDS[c.board] ?? null, ...c.options }), c.result, c.patterns.join(' '));
  }
});

test('cases.json similar seeds give the stored results', () => {
  for (const c of CASES.similar) assert.deepEqual(nets.similarTracks(COPPER[c.copper], c.seed, { board: BOARDS[c.copper] }), c.result);
});

test('CM5 MINIMA: KiCad patterns give KiCad classes; a picked 100 Ω pair finds the class', () => {
  const names = CM5.board.nets.map((n) => n.name);
  for (const nc of CM5.board.net_classes.filter((c) => c.patterns.length)) {
    assert.deepEqual(nets.matchNets(names, nc.patterns, { caseSensitive: true }).nets.sort(), [...nc.nets].sort(), nc.name);
  }
  const s = nets.similarTracks(CM5.copper, { layer: 'F.Cu', nets: ['/CM5/ETH_PI.TRD0_P', '/CM5/ETH_PI.TRD0_N'] }, { board: CM5.board });
  const klass = CM5.board.net_classes.find((c) => c.name === '100ohm');
  assert.equal(s.gap, 0.25);
  assert.deepEqual(s.pairs.flatMap((p) => p.nets).sort(), [...klass.nets].sort());
});

test('rules', () => {
  assert.deepEqual(nets.matchNets(['/usb/USB_D+', '/usb/USB_D-', 'usb_vbus'], 'USB_D*').pairs, [['/usb/USB_D+', '/usb/USB_D-']]);
  assert.deepEqual(nets.matchNets(['TX_T', 'TX_C', 'RX_T'], ['*_{T,C}']).pairs, [['TX_T', 'TX_C']]);
  assert.deepEqual(nets.matchNets(['TX_T', 'TX_C'], ['*_T/*_C']).pairs, [['TX_T', 'TX_C']]);
  assert.ok(nets.parsePattern('re:(').error && nets.parsePattern('a[').error && nets.parsePattern(' ').error);
  assert.ok(nets.matchesPattern('rf_*', 'RF_OUT') && !nets.matchesPattern('rf_*', 'RF_OUT', { caseSensitive: true }));
  assert.deepEqual(nets.pairsAmong(['D-', 'D+']), [['D+', 'D-']]);
  // lanes: two unnamed pairs at the picked gap, the third (wider gap) left out; single picks keep the width
  const lanes = CASES.copper.lanes;
  const s = nets.similarTracks(lanes, { layer: 'F.Cu', nets: ['NET_A', 'NET_B'] });
  assert.deepEqual(s.pairs.map((p) => p.nets), [['NET_A', 'NET_B'], ['NET_C', 'NET_D']]);
  assert.equal(nets.similarTracks(lanes, { layer: 'F.Cu', nets: ['WIDE'] }).nets.length, 1);
  assert.equal(nets.similarTracks(lanes, { layer: 'F.Cu', nets: ['SOLO'] }).nets.find((n) => n.net === 'GND').plane, true);
});
