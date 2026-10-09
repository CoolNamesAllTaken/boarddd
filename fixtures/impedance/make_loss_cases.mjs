// Writes fixtures/impedance/loss-cases.json: the loss and frequency cases (impedance phase I9) that
// test/impedance/loss.test.mjs (JS) and python/tests/test_impedance_loss.py both run.
//   golden   published or independently computed references, each with its source and tolerance
//   parity   inputs across every model, section kind and helper, with the JS results; Python must match to 1e-8
// Usage: node fixtures/impedance/make_loss_cases.mjs [--check]   (--check fails if loss-cases.json is stale)
// skrf-loss-refs.json comes from skrf_loss_refs.py (scikit-rf, an independent BSD-3 implementation; numbers only).
import { readFileSync, writeFileSync } from 'node:fs';
import * as z from '../../src/impedance/index.js';

const MIL = 0.0254;
const SOURCES = {
  polar: { about: 'Polar Instruments application note AP8195 (2017, B. Simonovich): Si9000e (BEM) offset stripline 1B1A, FR408HR, Huray/cannonball roughness; loss curves digitised from the published screenshots (about ±0.01 dB/in), Er(f) from its causal-extrapolation plot (±0.001)' },
  polar_params: { about: 'Polar Instruments AP8195: cannonball sphere radius Rz/16.73 and tile area (6r)², worked example values' },
  pozar: { about: 'D. M. Pozar, "Microwave Engineering", 3rd ed., Example 3.5: 50 Ω copper stripline, b = 3.2 mm, εr 2.2, tan δ 0.001, t = 0.01 mm, 10 GHz (α_c from Pozar\'s approximate stripline formula)' },
  rogers: { about: 'J. Coonrod, "Circuit materials and high-frequency losses of PCBs", The PCB Magazine, Feb. 2012 (Rogers): 50 Ω microstrip on RO4350B (Dk 3.66, Df 0.0037), modelled and measured, 10 GHz; dielectric loss only (the copper roughness is not stated)' },
  skrf: { about: 'scikit-rf 2.1.0 media.MLine (BSD-3, an independent implementation of the same papers): Djordjevic-Sarkar ("djordjevicsvensson"), Kirschning-Jansen dispersion from the same static values, and the dielectric attenuation; skrf-loss-refs.json' },
  wide: { about: 'Wheeler\'s rule on a parallel-plate limit: a microstrip 100 h wide in air must approach R = 2 Rs/w (fringing spreads the current: slightly below)' },
};

// AP8195's offset stripline (1B1A), as its Lossless Calculation screen gives it (mils): H1 12 (core, Er 3.65,
// Df 0.0094 @ 10 GHz), H2 11.8 (prepreg holding the trace, Er 3.59, Df 0.0095), W1 11, W2 9.88, T1 1.2;
// conductivity 5.8e7; Huray radius 0.2277 µm, 14 balls on 1.866 µm².
const H1 = 12 * MIL, H2 = 11.8 * MIL, W1 = 11 * MIL, W2 = 9.88 * MIL, T1 = 1.2 * MIL;
const POLAR_SECTION = {
  conductors: [{ y0: -0.035, y1: 0, net: 'gnd' }, { y0: H1 + H2, y1: H1 + H2 + 0.035, net: 'gnd' },
    ...z.sectionFor('stripline', { w: W1, h1: H1, h2: H2 - T1, t: T1, er: 3.6, etch: W1 - W2 }).conductors.filter((c) => c.net === 'sig')],
  dielectrics: [{ y0: 0, y1: H1, er: 3.65, tand: 0.0094, material: 'core' }, { y0: H1, y1: H1 + H2, er: 3.59, tand: 0.0095, material: 'prepreg' }],
};
const POLAR_HURAY = { model: 'huray', radius: 0.2277e-3, count: 14, area: 1.866e-6 };
const POLAR_SOLVE = { level: 0, maxLevel: 1, tol: 1e-9 };
const POLAR_LOSS = { dielectric: { frequency: 1e10 } };
// [GHz, smooth conductor, dielectric, smooth attenuation, conductor with roughness, attenuation with roughness], dB/in
const POLAR_LOSS_DB_IN = [
  [10, 0.166, 0.416, 0.586, 0.324, 0.744],
  [20, 0.232, 0.831, 1.076, 0.578, 1.418],
  [30, 0.289, 1.251, 1.549, 0.814, 2.074],
  [40, 0.332, 1.671, 2.013, 1.041, 2.721],
  [50, 0.372, 2.082, 2.463, 1.256, 3.342],
];
// Polar's causal-extrapolation Er (digitised; points where the two curves and the legend don't overlap)
const POLAR_ER = [['core', 3.65, 0.0094, [[5, 3.6661], [20, 3.6363], [30, 3.6266], [40, 3.6202], [50, 3.6153]]],
  ['prepreg', 3.59, 0.0095, [[1, 3.6395], [2, 3.6242], [5, 3.6048], [20, 3.5758], [30, 3.5669], [40, 3.5605], [50, 3.5556]]]];

const golden = [];
const add = (source, kind, args, key, ref, tol_pct, extra = {}) => golden.push({ id: `${source}-${golden.length}`, source, kind, args, key, ref, tol_pct, ...extra });
for (const [f, c, d, a, cr, ar] of POLAR_LOSS_DB_IN) {
  const args = { section: POLAR_SECTION, solve: POLAR_SOLVE, loss: POLAR_LOSS, f: f * 1e9 };
  const rough = { ...args, loss: { ...POLAR_LOSS, conductor: { roughness: POLAR_HURAY } } };
  add('polar', 'section', args, 'alpha_c_db_per_inch', c, 8);
  add('polar', 'section', args, 'alpha_d_db_per_inch', d, 3);
  add('polar', 'section', args, 'db_per_inch', a, 3);
  add('polar', 'section', rough, 'alpha_c_db_per_inch', cr, 6);
  add('polar', 'section', rough, 'db_per_inch', ar, 3);
}
add('polar', 'section', { section: POLAR_SECTION, solve: POLAR_SOLVE, loss: POLAR_LOSS, f: 1e10 }, 'Z0', 50.22, 1);
for (const [, er, tand, pts] of POLAR_ER) for (const [f, v] of pts) add('polar', 'dielectric', { er, tand, frequency: 1e10, f: f * 1e9 }, 'er', v, 0.1);
// AP8195's worked example: drum Rz 3.175 µm → 0.190 µm, matte 4.443 µm → 0.266 µm, their mean 0.2277 µm → A = 1.866 µm².
add('polar_params', 'cannonball', { rz: 3.175e-3 }, 'radius', 0.190e-3, 0.5);
add('polar_params', 'cannonball', { rz: 4.443e-3 }, 'radius', 0.266e-3, 0.5);
add('polar_params', 'huray_area', { radius: 0.2277e-3 }, 'area', 1.866e-6, 0.5);
// Pozar Example 3.5: W = 2.66 mm for 50 Ω; α_d = 0.155 Np/m, α_c = 0.122 Np/m (his approximate formula).
const POZAR = { model: 'stripline', params: { w: 2.66, h1: 1.595, h2: 1.595, t: 0.01, er: 2.2 }, loss: { dielectric: { tand: 0.001, model: 'constant' } }, f: 1e10 };
add('pozar', 'line', POZAR, 'Z0_lossless', 50, 2);
add('pozar', 'line', POZAR, 'alpha_d', 0.155, 2);
add('pozar', 'line', POZAR, 'alpha_c', 0.122, 10);
// Rogers RO4350B 50 Ω microstrip, ½ oz copper, at 10 GHz: dielectric loss 0.14 / 0.13 / 0.13 dB/in on 20 / 10 / 6.6 mil.
for (const [hm, d] of [[20, 0.14], [10, 0.13], [6.6, 0.13]]) {
  const h = hm * MIL, t = 0.0175, w = z.synthesize('microstrip', { h, t, er: 3.66 }, 50).value;
  add('rogers', 'line', { model: 'microstrip', params: { w, h, t, er: 3.66 }, loss: { dielectric: { tand: 0.0037, frequency: 1e10 } }, f: 1e10 },
    'alpha_d_db_per_inch', d, 10);
}
// scikit-rf: Djordjevic-Sarkar, Kirschning-Jansen dispersion (from skrf's own static values), dielectric attenuation.
const SKRF = JSON.parse(readFileSync(new URL('./skrf-loss-refs.json', import.meta.url), 'utf8'));
for (const row of SKRF.rows) {
  const p = row.line;
  row.frequency.forEach((f, i) => {
    const d = { er: p.er, tand: p.tand, frequency: p.f_ref, f };
    add('skrf', 'dielectric', d, 'er', row.er_f[i], 1e-6);
    add('skrf', 'dielectric', d, 'tand', row.tand_f[i], 1e-6);
    const disp = { w: p.w, h: p.h, er: row.er_f[i], eps_eff: row.eps_eff_static[i], Z0: row.Z0_static[i], f };
    add('skrf', 'dispersion', disp, 'eps_eff', row.eps_eff_f[i], 1e-6);
    add('skrf', 'dispersion', disp, 'Z0', row.Z0_f[i], 1e-6);
    add('skrf', 'line', { model: 'microstrip', params: { w: p.w, h: p.h, t: p.t, er: p.er }, loss: { dielectric: { tand: p.tand, frequency: p.f_ref }, dispersion: false }, f },
      'alpha_d', row.alpha_d[i], 2);
  });
}
for (const w of [20]) {
  add('wide', 'wheeler', { model: 'microstrip', params: { w, h: 0.2, t: 0.035, er: 1 } }, 'R_over_2Rs_per_w', 1, 7);
}

// ── parity ────────────────────────────────────────────────────────────────────────────────────────────────
const FS = [1e7, 1e8, 1e9, 3.2e9, 1e10, 2.5e10, 5e10];
const LINES = [
  ['microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4 }, { dielectric: { tand: 0.02 }, conductor: { roughness: { rq: 0.001 } } }],
  ['microstrip', { w: 0.15, h: 0.1, t: 0.018, er: 3.66 }, { dielectric: { tand: 0.0037, frequency: 1e10 }, dispersion: false }],
  ['coated_microstrip', { w: 0.3, h: 0.2, t: 0.035, er: 4.2, c: 0.015, erc: 3.6 }, { dielectric: { tand: 0.02 }, mask: { tand: 0.025 }, conductor: { roughness: { model: 'cannonball', rz: 0.005 } } }],
  ['stripline', { w: 0.15, h1: 0.2, h2: 0.25, t: 0.018, er: 4.1 }, { dielectric: { tand: 0.01, model: 'constant' }, signal: { roughness: { rq: 0.0005 } }, ground: { conductivity: 4e7 } }],
  ['cpw', { w: 0.3, gap: 0.15, h: 0.3, t: 0.035, er: 4.5 }, { dielectric: { tand: 0.02, f_low: 1e4, f_high: 1e11 } }],
  ['cpwg', { w: 0.3, gap: 0.15, h: 0.3, t: 0.035, er: 4.5, c: 0.02, erc: 3.5 }, { dielectric: { tand: 0.02 }, mask: { tand: 0.03 } }],
  ['coupled_microstrip', { w: 0.15, s: 0.15, h: 0.2104, t: 0.035, er: 4.4 }, { dielectric: { tand: 0.02 }, conductor: { roughness: { model: 'huray', radius: 0.0005, ratio: 2 } } }],
  ['coupled_microstrip', { w: 0.15, s: 0.15, h: 0.2104, t: 0.035, er: 4.4, c: 0.02, erc: 3.8 }, { dielectric: { tand: 0.015, frequency: 1e10 } }],
  ['coupled_stripline', { w: 0.1, s: 0.15, h1: 0.2, h2: 0.3, t: 0.018, er: 4.1 }, { dielectric: { tand: 0.01 } }],
];
const SECTIONS = [
  ['polar-offset-stripline', POLAR_SECTION, { level: -1, maxLevel: 0 }, { ...POLAR_LOSS, conductor: { roughness: POLAR_HURAY } }],
  ['masked-microstrip', { conductors: [{ y0: -0.035, y1: 0, net: 'gnd', metal: 'In1.Cu' }, { x0: -0.15, x1: 0.15, y0: 0.2, y1: 0.235, net: 'sig', metal: 'F.Cu' }],
    dielectrics: [{ y0: 0, y1: 0.2, er: 4.4, tand: 0.02, material: 'core' }, { y0: 0.2, y1: 0.215, er: 3.5, tand: 0.025, material: 'mask' },
      { x0: -0.165, x1: 0.165, y0: 0.2, y1: 0.25, er: 3.5, tand: 0.025, material: 'mask' }] }, {},
  { materials: { core: { frequency: 1e10 }, mask: { model: 'constant' } }, metals: { 'F.Cu': { roughness: { rq: 0.0008 } }, 'In1.Cu': { roughness: { rz: 0.003 }, conductivity: 5e7 } } }],
  ['pair', z.sectionFor('coupled_microstrip', { w: 0.15, s: 0.15, h: 0.2104, t: 0.035, er: 4.4, c: 0.02, erc: 3.8 }), { level: -1, maxLevel: 0 }, { dielectric: { frequency: 1e9 } }],
  ['asymmetric-pair', { conductors: [{ y0: -0.035, y1: 0, net: 'gnd' }, { x0: -0.25, x1: -0.1, y0: 0.2, y1: 0.235, net: 'p' }, { x0: 0.05, x1: 0.15, y0: 0.2, y1: 0.235, net: 'n' }],
    dielectrics: [{ y0: 0, y1: 0.2, er: 4.4, tand: 0.02 }] }, { level: -1, maxLevel: 0 }, {}],
  ['cpwg', z.sectionFor('cpwg', { w: 0.3, gap: 0.15, h: 0.3, t: 0.035, er: 4.5 }), { level: -1, maxLevel: 0 }, { conductor: { roughness: { rq: 0.001 } } }],
];
const parity = {
  dielectric: [[{ er: 4.4, tand: 0.02 }, FS], [{ er: 3.0, tand: 0.001, frequency: 1e10, f_low: 1e4, f_high: 1e11 }, FS], [{ er: 3.5, tand: 0.01, model: 'constant' }, FS]]
    .map(([m, fs]) => ({ m, fs, expect: fs.map((f) => z.dielectricAt(f, m)) })),
  roughness: [{ rq: 0.001 }, { model: 'hammerstad', rq: 0.0003 }, { rz: 0.004 }, { model: 'huray', radius: 0.0004, ratio: 3, matte: 1.2 }, { model: 'huray', radius: 0.0003, count: 10, area: 4e-6 }, null, { model: 'none' }]
    .map((r) => ({ r, fs: FS, expect: FS.map((f) => z.roughnessFactor(f, r)) })),
  dispersion: [[{ w: 0.36, h: 0.2104, er: 4.4, eps_eff: 3.2, Z0: 51 }], [{ w: 1.5, h: 0.794, er: 4.2, eps_eff: 3.2, Z0: 50 }]]
    .map(([p]) => ({ p, fs: FS, expect: FS.map((f) => z.microstripDispersion(p, f)) })),
  coupled_dispersion: [{ w: 0.15, s: 0.15, h: 0.2104, er: 4.4, Zeven: 75, Zodd: 45, eps_eff_even: 3.4, eps_eff_odd: 2.9, single: { Z0: 63, eps_eff: 3.15 } },
    { w: 0.3, s: 0.6, h: 0.2, er: 3.5, Zeven: 60, Zodd: 50, eps_eff_even: 2.9, eps_eff_odd: 2.6, single: { Z0: 55, eps_eff: 2.75 } }]
    .map((p) => ({ p, fs: FS, expect: FS.map((f) => z.coupledMicrostripDispersion(p, f)) })),
  lines: LINES.map(([model, params, opts]) => ({ model, params, opts, fs: FS, expect: z.lineLoss(model, params, FS, opts) })),
  sections: SECTIONS.map(([id, section, solve, opts]) => {
    const r = z.solveCrossSection(section, { ...solve, loss: true });
    return { id, section, solve, opts, fs: FS, partials: r.loss, expect: z.sectionLoss(r, FS, opts) };
  }),
};
parity.touchstone = [[0, 100, 50], [6, 50.8, 50], [8, 25, 42.5]].map(([i, length, z0]) => ({ line: i, length, z0,
  expect: z.touchstone(parity.lines[i].expect, length, { z0 }) }));

const out = { about: 'Shared boarddd/impedance loss cases (generated by make_loss_cases.mjs; lengths in mm, Hz). See docs/impedance.md "Loss and frequency".',
  sources: SOURCES, golden, parity };
const text = `${JSON.stringify(out, null, 1)}\n`;
const path = new URL('./loss-cases.json', import.meta.url);
if (process.argv.includes('--check')) {
  let old = '';
  try { old = readFileSync(path, 'utf8'); } catch { /* missing */ }
  if (old !== text) { console.error('fixtures/impedance/loss-cases.json is stale: run node fixtures/impedance/make_loss_cases.mjs'); process.exit(1); }
} else writeFileSync(path, text);
