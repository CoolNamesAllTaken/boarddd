// boarddd/copper: validateCopper against the shared cases (python/tests/test_copper.py runs the same file), the
// generated schema module, the polygon helpers, and copperFromGerbers on the fixtures' Gerber X2 exports against
// the KiCad reader's golden copper.json (python/tests/io/test_gerber_copper.py also checks Python = JS).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import * as copper from '../../src/copper/index.js';

const { validateCopper, assertCopper, copperFromGerbers, unfracture, fillArea, signedArea, inFill, circleHalves, SCHEMA, SCHEMA_ID } = copper;
const fixture = (rel) => new URL(`../../fixtures/${rel}`, import.meta.url);
const json = (rel) => JSON.parse(readFileSync(fixture(rel), 'utf8'));
const fab = (dir) => readdirSync(fixture(dir)).sort().map((name) => ({ name, text: readFileSync(fixture(`${dir}/${name}`), 'utf8') }));

function applyPatch(doc, patch) {
  const out = structuredClone(doc);
  for (const [op, path, value] of patch) {
    const keys = path.split('/').slice(1);
    const last = keys.pop();
    let node = out;
    for (const k of keys) node = node[Array.isArray(node) ? Number(k) : k];
    const key = Array.isArray(node) ? Number(last) : last;
    if (op === 'set') node[key] = value;
    else if (Array.isArray(node)) node.splice(key, 1);
    else delete node[key];
  }
  return out;
}

for (const c of json('copper/cases.json')) {
  test(`validateCopper: ${c.name}`, () => {
    assert.deepEqual(validateCopper(applyPatch(json('copper/minimal.json'), c.patch)), c.errors);
  });
}

test('schema.js is schema/copper.schema.json; the golden is valid', () => {
  assert.deepEqual(SCHEMA, json('../schema/copper.schema.json'));
  assert.equal(SCHEMA.$id, SCHEMA_ID);
  const golden = json('royalblue54L_nfc_antenna/copper.json');
  assert.equal(assertCopper(golden), golden);
  assert.throws(() => assertCopper({ schema: 'boarddd/copper@1' }), /missing required property 'board'/);
});

test('index.d.ts declares exactly the runtime exports', async () => {
  const { default: ts } = await import('typescript');
  const file = new URL('../../src/copper/index.d.ts', import.meta.url).pathname;
  const program = ts.createProgram([file], { noEmit: true, lib: ['lib.es2022.d.ts', 'lib.dom.d.ts'], moduleResolution: ts.ModuleResolutionKind.Bundler, module: ts.ModuleKind.ESNext });
  const checker = program.getTypeChecker();
  const exports = checker.getExportsOfModule(checker.getSymbolAtLocation(program.getSourceFile(file)));
  const declared = exports.filter((s) => (s.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(s) : s).flags & ts.SymbolFlags.Value).map((s) => s.name);
  assert.deepEqual(Object.keys(copper).sort(), declared.sort());
});

test('unfracture: cut-ins become holes', () => {
  const sq = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  const outer = sq(0, 0, 10, 10), hole = sq(2, 2, 4, 4).reverse();
  const ring = [outer[0], ...hole, hole[0], outer[0], ...outer.slice(1)];
  const [p, ...rest] = unfracture(ring);
  assert.equal(rest.length, 0);
  assert.equal(signedArea(p.outline), 100);
  assert.equal(p.holes.length, 1);
  assert.equal(fillArea([p]), 96);
  assert.ok(inFill([1, 1], p) && !inFill([3, 3], p));
  assert.deepEqual(circleHalves([1, 1], [2, 1]), [[[2, 1], [0, 1], [1, 2]], [[0, 1], [2, 1], [1, 0]]]);
});

test('copperFromGerbers: royalblue54L_feather (8 layers, X2 nets, vias, pours)', () => {
  const doc = copperFromGerbers(fab('royalblue54L_feather/fab'), { name: 'RoyalBlue54L-Feather' });
  assert.deepEqual(validateCopper(doc), []);
  assert.deepEqual(doc.layers, ['F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'In5.Cu', 'In6.Cu', 'B.Cu']);
  assert.equal(doc.tracks.length, 943);
  assert.equal(doc.vias.length, 183);
  assert.ok(doc.vias.every((v) => v.span[0] === 'F.Cu' && v.span[1] === 'B.Cu' && v.net));
  assert.equal(doc.pads.length, 991);
  assert.ok(doc.pads.every((p) => p.ref));
  assert.deepEqual(doc.warnings, []);
  const solid = doc.planes.filter((p) => p.solid).map((p) => `${p.layer} ${p.net}`).sort();
  assert.deepEqual(solid, ['B.Cu GND', 'In1.Cu GND', 'In2.Cu VDD', 'In3.Cu GND', 'In4.Cu VDD', 'In5.Cu GND', 'In6.Cu GND']);
  const gnd = doc.zones.filter((z) => z.layer === 'In1.Cu' && z.net === 'GND');
  assert.ok(Math.abs(gnd.reduce((s, z) => s + z.area, 0) - 986.7479) < 1e-3);
});

test('copperFromGerbers: the NFC antenna matches the KiCad golden', () => {
  const golden = json('royalblue54L_nfc_antenna/copper.json');
  const doc = copperFromGerbers(fab('royalblue54L_nfc_antenna/fab'));
  assert.deepEqual(validateCopper(doc), []);
  assert.deepEqual(doc.nets, golden.nets);
  assert.equal(doc.tracks.length, golden.tracks.length);
  const near = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]) < 2e-3;
  let arcs = 0;
  for (const t of golden.tracks) {
    const o = doc.tracks.find((x) => x.layer === t.layer && x.width === t.width && ((near(x.start, t.start) && near(x.end, t.end)) || (near(x.start, t.end) && near(x.end, t.start))));
    assert.ok(o, `track ${t.id}`);
    if (t.mid && o.mid) { assert.ok(near(t.mid, o.mid), `arc ${t.id}`); arcs += 1; }
  }
  assert.equal(arcs, 45); // the other 3 arcs are < 3 um long: kicad-cli writes them as straight draws
  assert.deepEqual(doc.vias.map((v) => [v.at, v.drill]), golden.vias.map((v) => [v.at, 0.711]));
  const area = (zs) => zs.reduce((s, z) => s + z.area, 0);
  assert.ok(Math.abs(area(doc.zones) - area(golden.zones)) < 1e-4);
});
