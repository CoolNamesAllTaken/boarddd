// boarddd/impedance loss and frequency (loss.js): the golden references and the shared parity cases
// (python/tests/test_impedance_loss.py runs the same file), plus physics checks: limits, passivity, causality of
// the dielectric, S-parameter properties, tier 1 against tier 2, the stackup and analyzeNet. Regenerate the cases
// with node fixtures/impedance/make_loss_cases.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as z from '../../src/impedance/index.js';
import { evaluate } from '../../fixtures/impedance/loss_eval.mjs';

const load = (name) => JSON.parse(readFileSync(new URL(`../../fixtures/impedance/${name}`, import.meta.url), 'utf8'));
const cases = load('loss-cases.json');
const rel = (a, b) => Math.abs(a - b) / Math.abs(b);

function close(actual, expected, path = '', tol = 1e-9) {
  if (typeof expected === 'number') {
    assert.equal(typeof actual, 'number', path);
    assert.ok(actual === expected || rel(actual, expected) < tol || Math.abs(actual - expected) < 1e-12, `${path}: ${actual} != ${expected}`);
  } else if (Array.isArray(expected)) {
    assert.equal(actual.length, expected.length, `${path} length`);
    expected.forEach((e, i) => close(actual[i], e, `${path}[${i}]`, tol));
  } else if (expected && typeof expected === 'object') {
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort(), path);
    for (const k of Object.keys(expected)) close(actual[k], expected[k], `${path}.${k}`, tol);
  } else assert.equal(actual, expected, path);
}

// ── golden ───────────────────────────────────────────────────────────────────────────────────────────────────

for (const g of cases.golden) {
  test(`loss golden ${g.id}: ${g.kind} ${g.key} vs ${g.source}`, () => {
    const v = evaluate(g), err = 100 * rel(v, g.ref);
    assert.ok(err <= g.tol_pct, `${g.key} ${v} vs ${g.ref}: ${err.toFixed(3)} % > ${g.tol_pct} %`);
  });
}

// ── parity: the JS results in loss-cases.json are this code's (Python checks itself against them) ─────────────

test('loss parity: helpers', () => {
  for (const c of cases.parity.dielectric) close(c.fs.map((f) => z.dielectricAt(f, c.m)), c.expect, 'dielectric');
  for (const c of cases.parity.roughness) close(c.fs.map((f) => z.roughnessFactor(f, c.r)), c.expect, 'roughness');
  for (const c of cases.parity.dispersion) close(c.fs.map((f) => z.microstripDispersion(c.p, f)), c.expect, 'dispersion');
  for (const c of cases.parity.coupled_dispersion) close(c.fs.map((f) => z.coupledMicrostripDispersion(c.p, f)), c.expect, 'coupled');
});

test('loss parity: tier-1 lines and Touchstone', () => {
  for (const c of cases.parity.lines) close(JSON.parse(JSON.stringify(z.lineLoss(c.model, c.params, c.fs, c.opts))), c.expect, c.model);
  for (const c of cases.parity.touchstone) assert.equal(z.touchstone(cases.parity.lines[c.line].expect, c.length, { z0: c.z0 }), c.expect);
});

test('loss parity: field sections', () => {
  for (const c of cases.parity.sections) {
    const r = z.solveCrossSection(c.section, { ...c.solve, loss: true });
    close(r.loss, c.partials, `${c.id} partials`);
    close(JSON.parse(JSON.stringify(z.sectionLoss(r, c.fs, c.opts))), c.expect, c.id);
  }
});

// ── physics ──────────────────────────────────────────────────────────────────────────────────────────────────

test('Djordjevic-Sarkar: the reference point, causality (Kramers-Kronig slope), monotonic Dk', () => {
  const m = { er: 4.2, tand: 0.02, frequency: 1e9 };
  const at = (f) => z.dielectricAt(f, m);
  close(at(1e9).er, 4.2, 'er0', 1e-12);
  close(at(1e9).tand, 0.02, 'tand0', 1e-12);
  // Dk falls with frequency; mid-band the slope per decade is -(2/π) ln 10 ε'' (the KK pair of a flat loss).
  const fs = [1e4, 1e6, 1e8, 1e9, 1e10, 1e11];
  for (let i = 1; i < fs.length; i++) assert.ok(at(fs[i]).er < at(fs[i - 1]).er);
  const slope = at(1e10).er - at(1e9).er, kk = -(2 / Math.PI) * Math.LN10 * 4.2 * 0.02;
  assert.ok(rel(slope, kk) < 0.05, `${slope} vs ${kk}`);
  // Nearly flat loss tangent across the band.
  assert.ok(Math.abs(at(1e7).tand - 0.02) < 0.002 && Math.abs(at(1e11).tand - 0.02) < 0.002);
  assert.deepEqual(z.dielectricAt(5e9, { ...m, model: 'constant' }), { er: 4.2, tand: 0.02 });
  assert.throws(() => z.dielectricAt(1e9, { er: 0.5 }), RangeError);
  assert.throws(() => z.dielectricAt(1e9, { er: 4, tand: 0.01, model: 'debye' }), RangeError);
});

test('roughness: limits of Hammerstad and Huray, cannonball parameters', () => {
  assert.equal(z.roughnessFactor(1e9, null), 1);
  const h = (f) => z.roughnessFactor(f, { rq: 0.001 });
  assert.ok(h(1e6) < 1.01 && h(1e12) > 1.99 && h(1e12) <= 2);
  const c = z.cannonball(0.005), hu = (f) => z.roughnessFactor(f, { rz: 0.005 });
  assert.ok(hu(1e6) < 1.05 && hu(1e14) > 1 + 1.5 * c.ratio * 0.95);
  close(c.ratio, (14 * 4 * Math.PI) / 36, 'ratio');
  for (let f = 1e7; f < 1e11; f *= 3) assert.ok(hu(f * 3) > hu(f));
  assert.throws(() => z.roughnessFactor(1e9, { model: 'huray' }), RangeError);
});

test('skin depth and surface resistance of copper', () => {
  close(z.skinDepth(1e9), 2.0897e-6, 'δ(1 GHz)', 1e-3);
  close(z.surfaceResistance(1e10), 0.0261, 'Rs(10 GHz)', 2e-3);
});

test('tier 1: DC limit, loss grows with frequency, passive S-parameters, reciprocity', () => {
  const p = { w: 0.2, h: 0.2, t: 0.035, er: 4.2 };
  const fs = [1e3, 1e6, 1e8, 1e9, 1e10, 4e10];
  const r = z.lineLoss('microstrip', p, fs, { dielectric: { tand: 0.02 } });
  close(r.R[0], 1 / (5.8e7 * 0.2e-3 * 0.035e-3), 'Rdc', 1e-3);
  for (let i = 1; i < fs.length; i++) assert.ok(r.alpha[i] > r.alpha[i - 1] && r.R[i] >= r.R[i - 1]);
  // The matched-line split α ≈ α_c + α_d holds for a low-loss line.
  assert.ok(rel(r.alpha[4], r.alpha_c[4] + r.alpha_d[4]) < 0.01);
  const S = z.sParameters(r, 50, { z0: r.Z0[4] });
  for (const M of S) {
    const p2 = (c) => c[0] * c[0] + c[1] * c[1];
    assert.ok(p2(M[0][0]) + p2(M[1][0]) <= 1 + 1e-12);
    close(M[0][1], M[1][0], 'S12 = S21');
  }
  // |S21| of a long matched-ish line: the dB/mm figure times the length (within the mismatch).
  const db = -20 * Math.log10(Math.hypot(...S[4][1][0]));
  assert.ok(Math.abs(db - r.db_per_mm[4] * 50) < 0.1, `${db} vs ${r.db_per_mm[4] * 50}`);
  assert.throws(() => z.lineLoss('microstrip', { ...p, t: 0 }, [1e9]), RangeError);
  assert.throws(() => z.lineLoss('ipc2141_microstrip', p, [1e9]), RangeError);
  assert.throws(() => z.lineLoss('microstrip', p, [0]), RangeError);
});

test('tier 1: Kirschning-Jansen dispersion raises εeff and Z0 of a microstrip with frequency', () => {
  const p = { w: 1.5, h: 0.794, t: 0.035, er: 4.2 };
  const on = z.lineLoss('microstrip', p, [1e8, 2e10], { dielectric: { model: 'constant' } });
  const off = z.lineLoss('microstrip', p, [1e8, 2e10], { dielectric: { model: 'constant' }, dispersion: false });
  assert.ok(on.eps_eff[1] > off.eps_eff[1] * 1.03);
  close(on.eps_eff[0], off.eps_eff[0], 'low f', 1e-3);
});

test('pairs: 4-port symmetry, differential loss is the odd mode, coupled modes from both tiers', () => {
  const p = { w: 0.15, s: 0.15, h: 0.2104, t: 0.035, er: 4.4 };
  const fs = [1e9, 1e10];
  const t1 = z.lineLoss('coupled_microstrip', p, fs, { dielectric: { tand: 0.02 }, dispersion: false });
  const t2 = z.sectionLoss(z.solveCrossSection(z.sectionFor('coupled_microstrip', p), { loss: true }), fs, { dielectric: { tand: 0.02 } });
  assert.equal(t1.key, 'Zdiff');
  assert.deepEqual(t1.db_per_mm, t1.odd.db_per_mm);
  for (let i = 0; i < fs.length; i++) {
    assert.ok(rel(t1.Zdiff[i], t2.Zdiff[i]) < 0.02, `Zdiff ${t1.Zdiff[i]} vs ${t2.Zdiff[i]}`);
    assert.ok(rel(t1.odd.alpha_c[i], t2.odd.alpha_c[i]) < 0.06, `odd α_c ${t1.odd.alpha_c[i]} vs ${t2.odd.alpha_c[i]}`);
    assert.ok(rel(t1.even.alpha_c[i], t2.even.alpha_c[i]) < 0.06, `even α_c ${t1.even.alpha_c[i]} vs ${t2.even.alpha_c[i]}`);
  }
  const S = z.sParameters(t1, 30);
  assert.equal(S[0].length, 4);
  close(S[1][0][1], S[1][2][3], 'S12 = S34');
  close(S[1][0][2], S[1][2][0], 'S13 = S31');
  const ts = z.touchstone(t1, 30).split('\n');
  assert.match(ts[1], /^# Hz S RI R 50$/);
  assert.equal(ts.length, 2 + 4 * fs.length + 1);
});

test('tier 2 against tier 1 on every single-line model (conductor and dielectric loss)', () => {
  const fs = [1e9, 1e10];
  for (const [model, p] of [['microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4 }], ['stripline', { w: 0.15, h1: 0.2, h2: 0.25, t: 0.018, er: 4.1 }],
    ['cpwg', { w: 0.3, gap: 0.15, h: 0.3, t: 0.035, er: 4.5 }], ['coated_microstrip', { w: 0.3, h: 0.2, t: 0.035, er: 4.2, c: 0.015, erc: 3.6 }]]) {
    const o = { dielectric: { tand: 0.02 }, mask: { tand: 0.02 }, dispersion: false };
    const t1 = z.lineLoss(model, p, fs, o);
    const sec = z.sectionFor(model, p);
    sec.dielectrics = sec.dielectrics.map((d) => ({ ...d, tand: 0.02 }));
    const t2 = z.sectionLoss(z.solveCrossSection(sec, { loss: true }), fs, o);
    for (let i = 0; i < fs.length; i++) {
      assert.ok(rel(t1.alpha_c[i], t2.alpha_c[i]) < 0.05, `${model} α_c ${t1.alpha_c[i]} vs ${t2.alpha_c[i]}`);
      assert.ok(rel(t1.alpha_d[i], t2.alpha_d[i]) < 0.03, `${model} α_d ${t1.alpha_d[i]} vs ${t2.alpha_d[i]}`);
    }
  }
});

test('the field solver: dielectric parts add up to K; dK0/dn converges; loss options on sections', () => {
  const sec = { conductors: [{ y0: -0.035, y1: 0, net: 'gnd' }, { x0: -0.15, x1: 0.15, y0: 0.2, y1: 0.235, net: 'sig' }],
    dielectrics: [{ y0: 0, y1: 0.2, er: 4.4, tand: 0.02 }, { y0: 0.2, y1: 0.25, er: 3.3, tand: 0.03 }] };
  const levels = [-1, 0, 1, 2].map((level) => z.solveCrossSection(sec, { level, maxLevel: level + 1, tol: 1e-9, loss: true }));
  for (const r of levels) {
    const sum = r.loss.parts.reduce((a, P) => a + P[0][0], 0);
    close(sum, r.matrices.C[0][0], 'Σ parts = C', 1e-12);
  }
  const g = levels.map((r) => r.loss.dK0.reduce((a, M) => a + M[0][0], 0));
  // Successive grids change ∂K0/∂n by less each time (and by < 1 % at the default levels).
  assert.ok(Math.abs(g[3] - g[2]) < Math.abs(g[1] - g[0]) && rel(g[2], g[3]) < 0.01, g.join(' '));
  assert.throws(() => z.sectionLoss(z.solveCrossSection(sec), [1e9]), RangeError);
  assert.throws(() => z.solveCrossSection({ ...sec, dielectrics: [{ y0: 0, y1: 0.2, er: 4, tand: -1 }] }), RangeError);
});

test('stackup: loss options from board@1 fields; tier 1 and tier 2 agree', () => {
  const st = load('cases.json').stackups.royalblue;
  const layers = st.layers.map((l) => (l.kind === 'copper' ? { ...l, roughness_rz: 0.004 } : l.kind === 'dielectric' ? { ...l, loss_tangent: 0.015, frequency: 1e10 } : l));
  const line = z.lineFromStackup({ ...st, layers }, 'F.Cu', { width: 0.3, solver: 'field', loss: true });
  assert.deepEqual(line.loss.signal.roughness, { rz: 0.004 });
  assert.equal(line.loss.dielectric.frequency, 1e10);
  assert.ok(line.section.conductors.every((c) => c.metal) && line.section.dielectrics.every((d) => d.tand != null && d.material));
  const fs = [1e9, 1e10];
  const t1 = z.lineLoss(line.model, line.params, fs, line.loss), t2 = z.sectionLoss(z.solveCrossSection(line.section, { loss: true }), fs, line.loss);
  for (let i = 0; i < fs.length; i++) assert.ok(rel(t1.db_per_mm[i], t2.db_per_mm[i]) < 0.04, `${t1.db_per_mm[i]} vs ${t2.db_per_mm[i]}`);
  assert.deepEqual(z.roughnessOf({ roughness_model: 'none' }), { model: 'none' });
  assert.equal(z.roughnessOf({ name: 'x' }), null);
});

test('analyzeNet with a frequency: per-section loss, route summary, cache reuse, validity', () => {
  const raw = readFileSync(new URL('../../fixtures/impedance/route/pair.json', import.meta.url));
  const { board, copper } = JSON.parse(raw);
  const cache = new Map();
  const plain = z.analyzeNet(board, copper, ['USB_P', 'USB_N'], { cache });
  assert.equal(plain.summary.loss, null);
  assert.ok(plain.sections.every((s) => s.loss === null));
  const doc = z.analyzeNet(board, copper, ['USB_P', 'USB_N'], { cache, frequency: 5e9 });
  assert.deepEqual(z.validateImpedance(doc), []);
  const L = doc.summary.loss;
  assert.ok(L.db > 0 && L.length > 0 && L.sweep.frequency.includes(5e9));
  const withLoss = doc.sections.filter((s) => s.net === 'USB_P' && s.loss);
  close(L.db, withLoss.reduce((a, s) => a + s.loss.db, 0), 'route dB', 1e-6);
  for (const s of withLoss) {
    close(s.loss.z, s.kind === 'differential' ? s.z.Zdiff : s.z.Zdiff ?? s.z.Z0, `Z at f (${s.kind})`, 0.03);
    assert.ok(s.loss.db_per_mm_conductor > 0 && s.loss.db_per_mm_dielectric > 0);
  }
  // A second run at another frequency reuses the solves (the loss partials are cached with them).
  const again = z.analyzeNet(board, copper, ['USB_P', 'USB_N'], { cache, frequency: 1e10 });
  assert.equal(again.timing.solves, 0);
  assert.ok(again.summary.loss.db > L.db);
  // tier 1
  const t1 = z.analyzeNet(board, copper, ['USB_P', 'USB_N'], { frequency: 5e9, solver: 'closedform' });
  assert.ok(rel(t1.summary.loss.db, L.db) < 0.1, `${t1.summary.loss.db} vs ${L.db}`);
  assert.throws(() => z.analyzeNet(board, copper, 'USB_P', { frequency: -1 }), RangeError);
});
