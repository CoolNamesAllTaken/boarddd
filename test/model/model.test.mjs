// boarddd/model: validateBoard against the shared cases (python/tests/test_validate.py runs the same file),
// the generated schema module, and the royalblue54L_feather golden board against boarddd's own KiCad parser.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as model from '../../src/model/index.js';
import { parseKicadFootprint, parseSexpr } from '../../src/footprint/kicad_mod.js';

const { validateBoard, assertBoard, footprintToBoard, SCHEMA, SCHEMA_ID } = model;
const fixture = (rel) => new URL(`../../fixtures/${rel}`, import.meta.url);
const json = (rel) => JSON.parse(readFileSync(fixture(rel), 'utf8'));
const golden = json('royalblue54L_feather/board.json');

/** [["set"|"delete", "/json/pointer", value?], ...] -> a patched copy (fixtures/model/cases.json). */
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

for (const c of json('model/cases.json')) {
  test(`validateBoard: ${c.name}`, () => {
    const doc = 'replace' in c ? c.replace : applyPatch(json('model/minimal.json'), c.patch);
    assert.deepEqual(validateBoard(doc), c.errors);
  });
}

test('assertBoard throws with the errors, returns valid boards', () => {
  assert.throws(() => assertBoard({ name: 1 }), /\/name: expected string/);
  assert.equal(assertBoard(golden), golden);
});

test('schema.js is schema/board.schema.json', () => {
  assert.deepEqual(SCHEMA, json('../schema/board.schema.json'));
  assert.equal(SCHEMA.$id, SCHEMA_ID);
});

test('index.d.ts declares exactly the runtime exports', () => {
  const dts = readFileSync(new URL('../../src/model/index.d.ts', import.meta.url), 'utf8');
  const declared = new Set([...dts.matchAll(/^export (?:declare )?(?:function|const) (\w+)/gm)].map((x) => x[1]));
  assert.deepEqual(Object.keys(model).sort(), [...declared].sort());
});

test('golden royalblue54L_feather is valid', () => {
  assert.deepEqual(validateBoard(golden), []);
});

test('golden footprints match boarddd/footprint parsing the .kicad_pcb (top-side instances)', () => {
  const root = parseSexpr(readFileSync(fixture('royalblue54L_feather/kicad/RoyalBlue54L-Feather.kicad_pcb'), 'utf8'));
  const close = (a, b, what) => assert.ok(Math.abs(a - b) < 1e-5, `${what}: ${a} vs ${b}`);
  const closeAll = (a, b, what) => { assert.equal(a.length, b.length, what); a.forEach((v, i) => close(v, b[i], what)); };
  let checked = 0;
  const seen = new Set();
  for (const node of root.filter((n) => Array.isArray(n) && n[0] === 'footprint')) {
    const fp = parseKicadFootprint(node);
    if (fp.layer !== 'F.Cu' || seen.has(fp.name)) continue;
    seen.add(fp.name);
    const at = node.find((n) => Array.isArray(n) && n[0] === 'at');
    const angle = Number(at[3] ?? 0);
    const g = golden.footprints[fp.name];
    assert.ok(g, fp.name);
    assert.equal(g.pads.length, fp.pads.length, fp.name);
    fp.pads.forEach((p, i) => {
      const q = g.pads[i], what = `${fp.name} pad ${p.number}`;
      assert.deepEqual([q.number, q.type, q.shape, q.layers], [p.number, p.type, p.shape, p.layers], what);
      closeAll(q.at.slice(0, 2), p.at.slice(0, 2), what);
      close(((q.at[2] + angle - p.at[2]) % 360 + 540) % 360 - 180, 0, `${what} angle`);   // kicad_pcb angles are absolute
      closeAll(q.size, p.size, what);
      assert.equal(q.drill?.shape ?? null, p.drill?.shape ?? null, what);
      if (p.drill) { closeAll(q.drill.size, p.drill.size, what); closeAll(q.drill.offset, p.offset, what); }
      for (const k of ['roundrect_rratio', 'chamfer_ratio', 'anchor']) assert.equal(q[k] ?? null, p[k] ?? null, `${what} ${k}`);
      assert.equal(q.primitives?.length ?? 0, p.primitives?.length ?? 0, what);
      (p.primitives || []).forEach((pr, k) => closeAll(q.primitives[k].pts.flat(), pr.pts.flat(), `${what} primitive ${k}`));
    });
    const graphics = fp.graphics.filter((x) => /\.(SilkS|Fab|CrtYd)$/.test(x.layer) && x.pts.length >= 2);
    assert.equal(g.graphics.length, graphics.length, fp.name);
    graphics.forEach((x, i) => {
      assert.deepEqual([g.graphics[i].layer, g.graphics[i].kind, g.graphics[i].closed, g.graphics[i].filled], [x.layer, x.kind, x.closed, x.filled]);
      closeAll(g.graphics[i].pts.flat(), x.pts.flat(), `${fp.name} graphic ${i}`);
    });
    checked += 1;
  }
  const topNames = new Set(golden.components.filter((c) => c.side === 'top').map((c) => c.footprint));
  assert.equal(checked, topNames.size);   // 22 of 27; the 5 bottom-only ones are checked through the drills below
});

test('footprintToBoard puts every golden thru-hole pad on a drill (bottom-side J1 included)', () => {
  const holes = golden.drills.filter((d) => d.function !== 'via')
    .map((d) => ({ plated: d.plated, x: (d.x + (d.x2 ?? d.x)) / 2, y: (d.y + (d.y2 ?? d.y)) / 2 }));
  let n = 0;
  for (const c of golden.components) {
    for (const pad of golden.footprints[c.footprint].pads) {
      if (!['thru_hole', 'np_thru_hole'].includes(pad.type)) continue;
      const [x, y] = footprintToBoard(c, pad.at);
      const d = Math.min(...holes.filter((h) => h.plated === (pad.type === 'thru_hole')).map((h) => Math.hypot(h.x - x, h.y - y)));
      assert.ok(d < 1e-3, `${c.ref} pad ${pad.number}: ${d} mm from the nearest hole`);
      n += 1;
    }
  }
  assert.equal(n, holes.length);
});
