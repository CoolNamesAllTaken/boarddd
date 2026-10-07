// boarddd/impedance tier 2, the field solver: golden references, the shared cases (python/tests/
// test_impedance_field.py runs the same file), the sweep fixture, symmetry, matrices, input errors and the
// stackup integration. Regenerate the cases with node fixtures/impedance/make_cases.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as z from '../../src/impedance/index.js';
import { SWEEP_OPTS, fenceFor } from '../../fixtures/impedance/make_field_sweep.mjs';

const load = (name) => JSON.parse(readFileSync(new URL(`../../fixtures/impedance/${name}`, import.meta.url), 'utf8'));
const cases = load('field-cases.json'), tier1 = load('cases.json');
const sweep = load('field-sweep.json'), qs = load('qs-sweep.json');
const rel = (a, b) => Math.abs(a - b) / Math.abs(b);
const lean = (r) => { const o = JSON.parse(JSON.stringify(r)); delete o.ms; return o; };

/** Numbers within `tol` relative, everything else equal. */
function close(actual, expected, path = '', tol = 1e-9) {
  if (typeof expected === 'number') {
    assert.equal(typeof actual, 'number', path);
    assert.ok(actual === expected || rel(actual, expected) < tol, `${path}: ${actual} != ${expected}`);
  } else if (Array.isArray(expected)) {
    assert.equal(actual.length, expected.length, `${path} length`);
    expected.forEach((e, i) => close(actual[i], e, `${path}[${i}]`, tol));
  } else if (expected && typeof expected === 'object') {
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort(), path);
    for (const k of Object.keys(expected)) close(actual[k], expected[k], `${path}.${k}`, tol);
  } else assert.equal(actual, expected, path);
}

for (const g of cases.golden) {
  test(`field golden ${g.id}: ${g.model} ${g.key} vs ${g.source}`, () => {
    const r = z.fieldCalculate(g.model, g.args);
    const err = 100 * rel(r[g.key], g.ref);
    assert.ok(err <= g.tol_pct, `${g.key} ${r[g.key]} vs ${g.ref}: ${err.toFixed(3)} % > ${g.tol_pct} %`);
    // Where the answer is exact, the error estimate covers the actual error.
    if (g.source === 'cohn') assert.ok(err <= r.error_pct, `error ${err.toFixed(3)} % > estimate ${r.error_pct.toFixed(3)} %`);
  });
}

test('field cases: the JS results are the ones in field-cases.json', () => {
  for (const c of cases.sections) {
    const r = c.section ? z.solveCrossSection(c.section, c.opts) : z.fieldCalculate(c.model, c.args, c.opts);
    close(lean(r), c.expect, c.id);
  }
});

test('field stackup lines and targets', () => {
  for (const c of cases.stackup_lines) {
    const line = z.lineFromStackup(tier1.stackups[c.stackup], c.layer, c.opts);
    close(JSON.parse(JSON.stringify(line)), c.expect, `${c.stackup} ${c.layer}`);
    close(lean(z.solveCrossSection(line.section)), c.result, `${c.stackup} ${c.layer} result`);
  }
  for (const c of cases.stackup_targets) {
    const rows = z.evaluateTarget(tier1.stackups[c.stackup], c.target, { solver: 'field' });
    close(rows.map((r) => ({ ...JSON.parse(JSON.stringify(r)), result: lean(r.result) })), c.expect, 'rows', 1e-8);
    for (const r of rows) if (r.synthesized) assert.ok(rel(r.value, r.target) < 1e-3, `${r.layer}: ${r.value}`);
  }
});

test('field-sweep.json: a sample of rows recomputes', () => {
  for (const { row: [model, args, ref] } of cases.sweep_sample) {
    const p = model === 'cpwg' ? { ...args, fence: fenceFor(args) } : args;
    const r = z.fieldCalculate(model, p, SWEEP_OPTS);
    for (const [k, v] of Object.entries(ref)) assert.ok(Math.abs(r[k] - v) <= 6e-5, `${model} ${JSON.stringify(args)} ${k}: ${r[k]} vs ${v}`);
  }
});

test('field-sweep.json vs qs-sweep.json (the GPL reference it replaced): same rows, within -3.5 .. +0.5 %', () => {
  assert.equal(sweep.rows.length, qs.rows.length);
  sweep.rows.forEach(([model, args, ref], i) => {
    const [m2, a2, r2] = qs.rows[i];
    assert.equal(model, m2);
    assert.deepEqual(args, a2);
    for (const k of Object.keys(ref)) {
      const d = (100 * (r2[k] - ref[k])) / ref[k];
      assert.ok(d > -3.5 && d < 0.5, `${model} ${JSON.stringify(args)} ${k}: qs ${r2[k]} vs ours ${ref[k]} (${d.toFixed(2)} %)`);
    }
  });
});

test('mirror symmetry gives the full-domain answer', () => {
  for (const [model, p] of [['coupled_microstrip', { w: 0.15, s: 0.2, h: 0.2, t: 0.035, er: 4.4 }], ['coated_microstrip', { w: 0.3, h: 0.2, t: 0.035, er: 4.4, c: 0.02, erc: 3.5 }]]) {
    const a = z.fieldCalculate(model, p), b = z.fieldCalculate(model, p, { symmetry: false });
    assert.notEqual(a.symmetry, 'none');
    assert.equal(b.symmetry, 'none');
    const k = 'Z0' in a ? 'Z0' : 'Zdiff';
    assert.ok(rel(a[k], b[k]) < 2e-3, `${model}: ${a[k]} vs ${b[k]}`);
    if (k === 'Zdiff') assert.ok(rel(a.Zcommon, b.Zcommon) < 2e-3);
  }
});

test('matrices: symmetric, diagonally dominant, L = μ0ε0 C0⁻¹, homogeneous K = εr K0', () => {
  const sec = cases.sections.find((c) => c.id === 'three_signals').section;
  const r = z.solveCrossSection(sec);
  const { C, C0, L } = r.matrices;
  assert.deepEqual(r.signals, ['s0', 's1', 's2']);
  for (let i = 0; i < 3; i++) {
    assert.ok(C[i][i] > 0 && C0[i][i] > 0);
    for (let j = 0; j < 3; j++) {
      assert.ok(rel(C[i][j], C[j][i]) < 1e-12 || Math.abs(C[i][j] - C[j][i]) < 1e-25);
      if (i !== j) assert.ok(C[i][j] < 0 && C[i][i] > -C[i][j]);
    }
  }
  // L C0 = μ0 ε0 I = I / c²
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) {
    const v = L[i].reduce((s, x, k) => s + x * C0[k][j], 0) * 299792458 ** 2;
    assert.ok(Math.abs(v - (i === j ? 1 : 0)) < 1e-9, `(LC0)[${i}][${j}] = ${v}`);
  }
  const s = z.fieldCalculate('stripline', { w: 0.1, h1: 0.2, t: 0.018, er: 3.7 });
  assert.ok(rel(s.eps_eff, 3.7) < 1e-12);
  assert.ok(rel(s.Z0, z.fieldCalculate('stripline', { w: 0.1, h1: 0.2, t: 0.018, er: 1 }).Z0 / Math.sqrt(3.7)) < 1e-12);
});

test('asymmetric pair: modes from the matrices', () => {
  const r = z.solveCrossSection(cases.sections.find((c) => c.id === 'asymmetric_pair').section);
  assert.equal(r.symmetry, 'none');
  assert.ok(Math.abs(r.Zdiff - 2 * r.Zodd) < 1e-12 && Math.abs(r.Zeven - 2 * r.Zcommon) < 1e-12);
  assert.ok(r.Zdiff > 50 && r.Zdiff < 200 && r.Zcommon > 10 && r.Zcommon < 100);
});

test('tolerance drives refinement', () => {
  const p = { w: 0.15, h: 0.2, t: 0.035, er: 4.4 };
  const loose = z.fieldCalculate('microstrip', p, { tol: 0.05 }), tight = z.fieldCalculate('microstrip', p, { tol: 0.001 });
  assert.equal(loose.levels.length, 2);
  assert.ok(tight.levels.length > 2 && tight.error.Z0 <= 0.001);
  assert.ok(rel(loose.Z0, tight.Z0) < 2e-3);
});

test('invalid sections throw', () => {
  const ok = { conductors: [{ y0: -1, y1: 0, net: 'gnd' }, { x0: 0, x1: 1, y0: 1, y1: 1.1, net: 'a' }] };
  assert.equal(typeof z.solveCrossSection(ok).Z0, 'number');
  for (const [sec, re] of [
    [{ conductors: [{ y0: -1, y1: 0, net: 'gnd' }] }, /no signal/],
    [{ conductors: [{ x0: 0, x1: 1, y0: 1, y1: 1.1, net: 'a' }] }, /no ground/],
    [{ conductors: [{ y0: -1, y1: 0, net: 'gnd' }, { y0: 1, y1: 1.1, net: 'a' }] }, /bounded/],
    [{ conductors: [{ y0: 0, y1: -1, net: 'gnd' }, { x0: 0, x1: 1, y0: 1, y1: 1.1, net: 'a' }] }, /negative size/],
    [{ ...ok, dielectrics: [{ y0: 0, y1: 1, er: 0.5 }] }, /er must be/],
    [{ conductors: [{ y0: -1, y1: 0 }] }, /no net/],
  ]) assert.throws(() => z.solveCrossSection(sec), re);
  assert.throws(() => z.fieldCalculate('microstrip', { w: 0, h: 1, er: 4 }), /w must be/);
  assert.throws(() => z.fieldCalculate('ipc2141_microstrip', { w: 1, h: 1, er: 4 }), /no builder/);
  assert.throws(() => z.lineFromStackup(tier1.stackups.synthetic, 'F.Cu', { width: 0.1, solver: 'magic' }), /unknown solver/);
});

test('field mode handles what tier 1 cannot: differential coplanar, coplanar on an inner layer', () => {
  const st = tier1.stackups.royalblue;
  assert.throws(() => z.lineFromStackup(st, 'F.Cu', { width: 0.2, kind: 'differential', gap: 0.15, structure: 'coplanar_grounded', coplanarGap: 0.2 }), /tier 1 has no/);
  const line = z.lineFromStackup(st, 'F.Cu', { width: 0.2, kind: 'differential', gap: 0.15, structure: 'coplanar_grounded', coplanarGap: 0.2, solver: 'field' });
  assert.equal(line.model, null);
  const r = z.solveCrossSection(line.section);
  assert.ok(r.Zdiff > 50 && r.Zdiff < 150);
  // The closed-form result is unchanged by the option.
  const a = z.lineFromStackup(st, 'In1.Cu', { width: 0.1 }), b = z.lineFromStackup(st, 'In1.Cu', { width: 0.1, solver: 'field' });
  assert.deepEqual(a, { model: b.model, structure: b.structure, params: b.params, warnings: b.warnings });
});
