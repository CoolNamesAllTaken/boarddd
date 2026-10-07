// boarddd/impedance: golden references, the shared parity cases (python/tests/test_impedance.py runs the same
// file), synthesis, validity flags and monotonicity. Regenerate the cases with node fixtures/impedance/make_cases.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as z from '../../src/impedance/index.js';

const cases = JSON.parse(readFileSync(new URL('../../fixtures/impedance/cases.json', import.meta.url), 'utf8'));
const rel = (a, b) => Math.abs(a - b) / Math.abs(b);

for (const g of cases.golden) {
  test(`golden ${g.id}: ${g.model} ${g.key} vs ${g.source}`, () => {
    const r = z.calculate(g.model, g.args);
    const err = 100 * rel(r[g.key], g.ref);
    assert.ok(err <= g.tol_pct, `${g.key} ${r[g.key]} vs ${g.ref}: ${err.toFixed(3)} % > ${g.tol_pct} %`);
    assert.deepEqual(r.flags, [], 'golden cases are inside the validity range');
  });
}

/** Numbers within 1e-12 relative, everything else equal. */
function close(actual, expected, path = '') {
  if (typeof expected === 'number') {
    assert.equal(typeof actual, 'number', path);
    assert.ok(actual === expected || rel(actual, expected) < 1e-12, `${path}: ${actual} != ${expected}`);
  } else if (Array.isArray(expected)) {
    assert.equal(actual.length, expected.length, `${path} length`);
    expected.forEach((e, i) => close(actual[i], e, `${path}[${i}]`));
  } else if (expected && typeof expected === 'object') {
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort(), path);
    for (const k of Object.keys(expected)) close(actual[k], expected[k], `${path}.${k}`);
  } else assert.equal(actual, expected, path);
}

test('parity cases: the JS results are the ones in cases.json', () => {
  for (const c of cases.parity) close(JSON.parse(JSON.stringify(z.calculate(c.model, c.args))), c.expect, c.id);
});

test('synthesis cases', () => {
  for (const c of cases.synthesis) {
    const r = z.synthesize(c.model, c.params, c.target, c.opts);
    close(r.value, c.expect.value, `${c.model} value`);
    close(JSON.parse(JSON.stringify(r.result)), c.expect.result, `${c.model} result`);
    const key = c.opts.key ?? ('Zdiff' in r.result ? 'Zdiff' : 'Z0');
    assert.ok(rel(r.result[key], c.target) < 1e-6, `${c.model}: ${r.result[key]} for ${c.target}`);
  }
});

test('synthesis: out of reach throws', () => {
  assert.throws(() => z.synthesize('microstrip', { h: 1, er: 4 }, 0.5), /not reachable/);
  assert.throws(() => z.synthesize('microstrip', { h: 1, er: 4 }, 500), /not reachable/);
});

test('validity flags', () => {
  const codes = (r) => r.flags.map((f) => f.code);
  assert.deepEqual(codes(z.microstrip({ w: 0.36, h: 0.2104, t: 0.035, er: 4.4 })), []);
  assert.deepEqual(codes(z.microstrip({ w: 200, h: 1, er: 4 })), ['w/h']);
  assert.deepEqual(codes(z.microstrip({ w: 0.2, h: 0.1, t: 0.05, er: 4 })), ['t/h']);
  assert.deepEqual(codes(z.stripline({ w: 0.1, h1: 0.05, h2: 0.4, t: 0.035, er: 4 })), ['h_max/h_min', 'h_min/t']);
  assert.deepEqual(codes(z.cpwg({ w: 0.15, gap: 0.4, h: 0.1, t: 0.035, er: 4.4 })), ['h/gap']);
  assert.deepEqual(codes(z.coupledMicrostrip({ w: 0.1, s: 0.005, h: 0.1, er: 4.4 })), ['s/h']);
  assert.deepEqual(codes(z.coupledStripline({ w: 0.1, s: 0.02, h1: 0.2, t: 0.035, er: 4 })), ['s/(b-t)']);
  assert.deepEqual(codes(z.coatedMicrostrip({ w: 0.3, h: 0.1, t: 0.035, er: 4.2, c: 0.06, erc: 6 })), ['t/h', 'c/h', 'erc']);
  const f = z.microstrip({ w: 200, h: 1, er: 4 }).flags[0];
  assert.deepEqual(f, { code: 'w/h', value: 200, min: 0.01, max: 100, message: 'w/h = 200 is outside 0.01..100 (Hammerstad-Jensen)' });
});

test('IPC-2141 is labelled a comparison and is the published formula', () => {
  const r = z.ipc2141Microstrip({ w: 3.3, h: 0.794, t: 0.035, er: 4.2 });
  assert.equal(r.comparison, true);
  assert.ok(rel(r.Z0, 21.0765) < 1e-5);   // 30 % below Polar's 30.09: why it is comparison only
  assert.equal('comparison' in z.microstrip({ w: 1, h: 1, er: 4 }), false);
});

test('bad inputs throw', () => {
  assert.throws(() => z.microstrip({ w: -1, h: 1, er: 4 }), RangeError);
  assert.throws(() => z.microstrip({ w: 1, h: 1, er: 0.5 }), RangeError);
  assert.throws(() => z.stripline({ w: 1, h1: 1, t: -0.1, er: 4 }), RangeError);
  assert.throws(() => z.calculate('nope', {}), /unknown model/);
});

test('t = 0 is exact (Cohn) and thickness is continuous', () => {
  const zs = (t) => z.stripline({ w: 0.15, h1: 0.2, t, er: 4.1 }).Z0;
  assert.ok(rel(zs(1e-9), zs(0)) < 1e-6);
  const zc = (t) => z.coupledStripline({ w: 0.15, s: 0.1, h1: 0.2, t, er: 4.1 }).Zdiff;
  assert.ok(rel(zc(1e-9), zc(0)) < 1e-5);
  const zm = (t) => z.coupledMicrostrip({ w: 0.15, s: 0.1, h: 0.2, t, er: 4.1 }).Zdiff;
  assert.ok(rel(zm(1e-9), zm(0)) < 1e-5);
  const zg = (t) => z.cpwg({ w: 0.3, gap: 0.15, h: 0.3, t, er: 4.5 }).Z0;
  assert.ok(rel(zg(1e-9), zg(0)) < 1e-3);
  // offset -> symmetric is continuous
  const zo = (h2) => z.stripline({ w: 0.15, h1: 0.2, h2, t: 0.018, er: 4.1 }).Z0;
  assert.ok(rel(zo(0.2 * (1 + 1e-9)), zo(0.2)) < 1e-6);
});

test('impedance falls monotonically with width (synthesis relies on it)', () => {
  const sweeps = {
    microstrip: { h: 0.2, t: 0.035, er: 4.4 },
    coated_microstrip: { h: 0.2, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 },
    stripline: { h1: 0.1, h2: 0.4, t: 0.035, er: 4 },
    cpw: { gap: 0.15, h: 0.5, t: 0.035, er: 4.4 },
    cpwg: { gap: 0.15, h: 0.3, t: 0.035, er: 4.4 },
    coupled_microstrip: { s: 0.15, h: 0.2, t: 0.035, er: 4.4 },
    coupled_stripline: { s: 0.15, h1: 0.1, h2: 0.25, t: 0.035, er: 4 },
  };
  for (const [model, p] of Object.entries(sweeps)) {
    let prev = Infinity;
    for (let w = 0.005; w < 20; w *= 1.05) {
      const r = z.calculate(model, { ...p, w });
      const v = r.Zdiff ?? r.Z0;
      assert.ok(v < prev, `${model} at w = ${w}: ${v} >= ${prev}`);
      prev = v;
    }
  }
});

test('elliptic integrals', () => {
  assert.ok(rel(z.ellipticK(0), Math.PI / 2) < 1e-15);
  assert.ok(rel(z.ellipticK(Math.SQRT1_2), 1.8540746773013719) < 1e-14);
  assert.ok(rel(z.ellipticRatio(Math.SQRT1_2), 1) < 1e-15);
});

test('accuracy envelope against the quasi-static field-solver sweep (fixtures/impedance/qs-sweep.json)', () => {
  const sweep = JSON.parse(readFileSync(new URL('../../fixtures/impedance/qs-sweep.json', import.meta.url), 'utf8'));
  const errs = {};
  for (const [model, args, ref] of sweep.rows) {
    const r = z.calculate(model, args);
    if (r.flags.length || args.t < 0.018) continue;   // real copper, inside the validity range
    for (const [k, v] of Object.entries(ref)) (errs[model] ??= []).push((100 * (r[k] - v)) / v);
  }
  assert.deepEqual(Object.keys(errs).sort(), ['coated_microstrip', 'coupled_microstrip', 'coupled_stripline', 'cpwg', 'microstrip', 'stripline']);
  for (const [model, e] of Object.entries(errs)) {
    const max = Math.max(...e.map(Math.abs)), rms = Math.sqrt(e.reduce((s, x) => s + x * x, 0) / e.length);
    assert.ok(max < 2.5 && rms < 1, `${model}: max ${max.toFixed(2)} %, rms ${rms.toFixed(2)} % over ${e.length}`);
  }
});

test('stackup lines and ImpedanceTarget evaluation (shared cases)', () => {
  for (const c of cases.stackup_lines) close(JSON.parse(JSON.stringify(z.lineFromStackup(cases.stackups[c.stackup], c.layer, c.opts))), c.expect, `${c.stackup} ${c.layer}`);
  for (const c of cases.stackup_targets) {
    const rows = z.evaluateTarget(cases.stackups[c.stackup], c.target);
    close(JSON.parse(JSON.stringify(rows)), c.expect, `${c.stackup} ${c.target.kind} ${c.target.target}`);
    for (const r of rows.filter((x) => x.synthesized)) assert.ok(Math.abs(r.deviation_pct) < 1e-6);
  }
});

test('stackup: structure and model mapping, refusals', () => {
  assert.equal(z.modelFor('microstrip', 'single'), 'microstrip');
  assert.equal(z.modelFor('microstrip', 'single', { coated: true }), 'coated_microstrip');
  assert.equal(z.modelFor('stripline', 'differential'), 'coupled_stripline');
  assert.equal(z.modelFor('coplanar_grounded', 'single'), 'cpwg');
  assert.equal(z.modelFor('coplanar', 'differential'), null);
  const s = cases.stackups.royalblue;
  assert.throws(() => z.lineFromStackup(s, 'F.Cu', { width: 0.1, structure: 'stripline' }), /outer layer/);
  assert.throws(() => z.lineFromStackup(s, 'In1.Cu', { width: 0.1, structure: 'microstrip' }), /inner layer/);
  assert.throws(() => z.lineFromStackup(s, 'F.Cu', { width: 0.1, kind: 'differential', gap: 0.1, structure: 'coplanar_grounded', coplanarGap: 0.1 }), /no differential coplanar_grounded/);
  assert.throws(() => z.lineFromStackup(s, 'In9.Cu', { width: 0.1 }), /no copper layer/);
  assert.throws(() => z.lineFromStackup(s, 'In2.Cu', { width: 0.1, refTop: 'B.Cu' }), /not found above/);
});
