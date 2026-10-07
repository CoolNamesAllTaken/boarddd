// Writes fixtures/impedance/cases.json (tier 1) and field-cases.json (tier 2, the field solver): the shared
// impedance cases that test/impedance (JS) and python/tests/test_impedance*.py both run.
//   golden     published or independently computed reference values, each with its source and tolerance
//   parity     inputs spanning every model, flagged and not, with the JS results; Python must match them to 1e-9
//   synthesis  width/spacing solves with their JS results
// field-cases.json:
//   golden     the field solver against exact and published references, with tolerances
//   sections   generic cross-sections (asymmetric pairs, three signals, finite grounds, layered dielectrics)
//              and tier-1 models through fieldCalculate, with the JS results; Python must match them to 1e-9
//   stackup_lines, stackup_targets   lineFromStackup / evaluateTarget with solver: 'field'
//   sweep_sample   rows of field-sweep.json the tests recompute
// Usage: node fixtures/impedance/make_cases.mjs [--check]   (--check fails if either file is stale)
import { readFileSync, writeFileSync } from 'node:fs';
import * as z from '../../src/impedance/index.js';
import { fieldCalculate, solveCrossSection } from '../../src/impedance/fieldsolver.js';
import { sweepRow } from './make_field_sweep.mjs';

const SOURCES = {
  polar: { tol_pct: 1, about: 'Polar Instruments Si9000 (BEM field solver), published microstrip cases: IPC 1999 paper, https://www.polarinstruments.com/support/cits/IPC1999.pdf' },
  hfss: { tol_pct: 2, about: 'Ansys HFSS, 1 GHz full-wave with loss, as published in Ttl/js_2d_fields tests/test_vs_ref.js (loss raises Z about 0.5-1 % over a quasi-static answer)' },
  cohn: { tol_pct: 1e-9, about: 'Cohn 1954/1955 exact conformal maps (t = 0), evaluated with scipy.special.ellipk (an independent elliptic-integral implementation)' },
  skrf: { tol_pct: 0.01, about: 'scikit-rf 2.1.0 media.MLine (hammerstadjensen, no dispersion) and media.CPW (Ghione, t = 0), 1 MHz, lossless: an independent BSD-3 implementation of the same papers' },
  field: { tol_pct: 2, about: "boarddd's own quasi-static field solver (src/impedance/fieldsolver.js, MIT): the field-sweep.json value where the sweep has the geometry, else solved on the sweep's grid (levels 2/3); cpwg with the sweep's via fence" },
};

// [source, model, args (mm), key, reference]; 'field' rows take their reference from boarddd's field solver below
const REFS = [
  ['polar', 'microstrip', { w: 3.3, h: 0.794, t: 0.035, er: 4.2 }, 'Z0', 30.09],
  ['polar', 'microstrip', { w: 1.5, h: 0.794, t: 0.035, er: 4.2 }, 'Z0', 50.63],
  ['polar', 'microstrip', { w: 0.45, h: 0.794, t: 0.035, er: 4.2 }, 'Z0', 89.63],
  ['hfss', 'microstrip', { w: 3, h: 1.6, t: 0.035, er: 4.5 }, 'Z0', 49.8],
  ['hfss', 'stripline', { w: 0.15, h1: 0.2, h2: 0.2, t: 0.035, er: 4.1 }, 'Z0', 50.611],
  ['hfss', 'cpwg', { w: 0.3, gap: 0.15, h: 0.3, t: 0.035, er: 4.5 }, 'Z0', 55.465],
  ['hfss', 'coupled_microstrip', { w: 3, s: 1, h: 1.6, t: 0.035, er: 4.5 }, 'Zodd', 40.23],
  ['hfss', 'coupled_microstrip', { w: 3, s: 1, h: 1.6, t: 0.035, er: 4.5 }, 'Zeven', 57.65],
  ['hfss', 'coupled_stripline', { w: 0.15, s: 0.1, h1: 0.2, h2: 0.2, t: 0.035, er: 4.1 }, 'Zodd', 37.6],
  ['hfss', 'coupled_stripline', { w: 0.15, s: 0.1, h1: 0.2, h2: 0.2, t: 0.035, er: 4.1 }, 'Zeven', 61.36],
  ["skrf", "microstrip", {"w":3.3,"h":0.794,"t":0.035,"er":4.2}, "Z0", 30.11216039270318],
  ["skrf", "microstrip", {"w":3.3,"h":0.794,"t":0.035,"er":4.2}, "eps_eff", 3.39863436015296],
  ["skrf", "microstrip", {"w":1.5,"h":0.794,"t":0.035,"er":4.2}, "Z0", 50.63289591193422],
  ["skrf", "microstrip", {"w":1.5,"h":0.794,"t":0.035,"er":4.2}, "eps_eff", 3.1512481840837694],
  ["skrf", "microstrip", {"w":0.45,"h":0.794,"t":0.035,"er":4.2}, "Z0", 89.71415793109226],
  ["skrf", "microstrip", {"w":0.45,"h":0.794,"t":0.035,"er":4.2}, "eps_eff", 2.867489398820376],
  ["skrf", "microstrip", {"w":0.36,"h":0.2104,"t":0.035,"er":4.4}, "Z0", 50.92204804373111],
  ["skrf", "microstrip", {"w":0.36,"h":0.2104,"t":0.035,"er":4.4}, "eps_eff", 3.1763273735478417],
  ["skrf", "microstrip", {"w":0.1,"h":0.1,"t":0,"er":3}, "Z0", 83.71029483726397],
  ["skrf", "microstrip", {"w":0.1,"h":0.1,"t":0,"er":3}, "eps_eff", 2.280869219254949],
  ["skrf", "microstrip", {"w":10,"h":1,"t":0.018,"er":9.8}, "Z0", 10.002288197227097],
  ["skrf", "microstrip", {"w":10,"h":1,"t":0.018,"er":9.8}, "eps_eff", 8.368275963930474],
  ["skrf", "cpwg", {"w":0.3,"gap":0.15,"h":0.3,"er":4.5}, "Z0", 60.95783646845655],
  ["skrf", "cpwg", {"w":0.3,"gap":0.15,"h":0.3,"er":4.5}, "eps_eff", 2.9732907667249817],
  ["skrf", "cpw", {"w":0.5,"gap":0.2,"h":1.6,"er":4.4}, "Z0", 69.08825897006528],
  ["skrf", "cpw", {"w":0.5,"gap":0.2,"h":1.6,"er":4.4}, "eps_eff", 2.67617106670014],
  ["skrf", "cpw", {"w":0.2,"gap":0.1,"h":0.5,"er":4.4}, "Z0", 73.9556884077034],
  ["skrf", "cpw", {"w":0.2,"gap":0.1,"h":0.5,"er":4.4}, "eps_eff", 2.654096663953054],
  ["skrf", "cpwg", {"w":1,"gap":0.15,"h":1,"er":4.4}, "Z0", 48.16057086327058],
  ["skrf", "cpwg", {"w":1,"gap":0.15,"h":1,"er":4.4}, "eps_eff", 2.813245460205847],
  ["cohn", "stripline", {"w":0.15,"h1":0.2,"er":4.1}, "Z0", 57.53618188390254],
  ["cohn", "stripline", {"w":0.05,"h1":0.2,"er":4}, "Z0", 90.45779609167748],
  ["cohn", "stripline", {"w":1,"h1":0.2,"er":3.5}, "Z0", 17.11596875137538],
  ["cohn", "stripline", {"w":0.3,"h1":0.5,"er":1}, "Z0", 129.30620735967562],
  ["cohn", "coupled_stripline", {"w":0.15,"s":0.1,"h1":0.2,"er":4.1}, "Zeven", 66.99967192178678],
  ["cohn", "coupled_stripline", {"w":0.15,"s":0.1,"h1":0.2,"er":4.1}, "Zodd", 46.81811579566902],
  ["cohn", "coupled_stripline", {"w":0.1,"s":0.2,"h1":0.15,"er":4}, "Zeven", 64.54162618734357],
  ["cohn", "coupled_stripline", {"w":0.1,"s":0.2,"h1":0.15,"er":4}, "Zodd", 58.60951872377179],
  ["cohn", "coupled_stripline", {"w":0.5,"s":0.1,"h1":0.25,"er":3.6}, "Zeven", 38.032940016511084],
  ["cohn", "coupled_stripline", {"w":0.5,"s":0.1,"h1":0.25,"er":3.6}, "Zodd", 29.483176252316362],
  ["field", "microstrip", {"w":0.02,"h":0.2,"t":0.035,"er":3}, "Z0"],
  ["field", "microstrip", {"w":0.6,"h":0.2,"t":0.035,"er":3}, "Z0"],
  ["field", "microstrip", {"w":0.24,"h":0.8,"t":0.035,"er":3}, "Z0"],
  ["field", "microstrip", {"w":8,"h":0.8,"t":0.035,"er":3}, "Z0"],
  ["field", "microstrip", {"w":1.6,"h":1.6,"t":0.035,"er":3}, "Z0"],
  ["field", "stripline", {"w":0.02,"h1":0.091,"h2":0.091,"t":0.018,"er":4}, "Z0"],
  ["field", "stripline", {"w":0.6,"h1":0.06067,"h2":0.12133,"t":0.018,"er":4}, "Z0"],
  ["field", "stripline", {"w":0.4,"h1":0.191,"h2":0.191,"t":0.018,"er":4}, "Z0"],
  ["field", "stripline", {"w":0.1,"h1":0.4825,"h2":0.4825,"t":0.035,"er":4}, "Z0"],
  ["field", "stripline", {"w":3,"h1":0.491,"h2":0.491,"t":0.018,"er":4}, "Z0"],
  ["field", "stripline", {"w":0.15,"h1":0.11625,"h2":0.34875,"t":0.035,"er":4}, "Z0"],
  ["field", "cpwg", {"w":0.15,"gap":0.1,"h":0.1,"t":0.035,"er":4.4}, "Z0"],
  ["field", "cpwg", {"w":0.3,"gap":0.2,"h":0.4,"t":0.035,"er":4.4}, "Z0"],
  ["field", "cpwg", {"w":0.6,"gap":0.4,"h":1,"t":0.035,"er":4.4}, "Z0"],
  ["field", "cpwg", {"w":0.15,"gap":0.1,"h":0.4,"t":0.018,"er":4.4}, "Z0"],
  ["field", "cpwg", {"w":0.6,"gap":0.4,"h":0.4,"t":0.018,"er":4.4}, "Z0"],
  ["field", "coupled_microstrip", {"w":0.04,"s":0.04,"h":0.2,"t":0.035,"er":4.4}, "Zodd"],
  ["field", "coupled_microstrip", {"w":0.04,"s":0.04,"h":0.2,"t":0.035,"er":4.4}, "Zeven"],
  ["field", "coupled_microstrip", {"w":0.1,"s":0.2,"h":0.2,"t":0.035,"er":4.4}, "Zodd"],
  ["field", "coupled_microstrip", {"w":0.1,"s":0.2,"h":0.2,"t":0.035,"er":4.4}, "Zeven"],
  ["field", "coupled_microstrip", {"w":0.4,"s":0.04,"h":0.2,"t":0.035,"er":4.4}, "Zodd"],
  ["field", "coupled_microstrip", {"w":0.4,"s":0.04,"h":0.2,"t":0.035,"er":4.4}, "Zeven"],
  ["field", "coupled_stripline", {"w":0.06,"s":0.06,"h1":0.141,"h2":0.141,"t":0.018,"er":4}, "Zodd"],
  ["field", "coupled_stripline", {"w":0.06,"s":0.06,"h1":0.141,"h2":0.141,"t":0.018,"er":4}, "Zeven"],
  ["field", "coupled_stripline", {"w":0.15,"s":0.3,"h1":0.094,"h2":0.188,"t":0.018,"er":4}, "Zodd"],
  ["field", "coupled_stripline", {"w":0.15,"s":0.3,"h1":0.094,"h2":0.188,"t":0.018,"er":4}, "Zeven"],
  ["field", "coupled_stripline", {"w":0.1,"s":0.25,"h1":0.2325,"h2":0.2325,"t":0.035,"er":4}, "Zodd"],
  ["field", "coupled_stripline", {"w":0.1,"s":0.25,"h1":0.2325,"h2":0.2325,"t":0.035,"er":4}, "Zeven"],
  ["field", "coated_microstrip", {"w":0.03,"h":0.1,"t":0.018,"er":4.2,"c":0.01,"erc":3.3}, "Z0"],
  ["field", "coated_microstrip", {"w":0.2,"h":0.2,"t":0.018,"er":4.2,"c":0.02,"erc":3.3}, "Z0"],
  ["field", "coated_microstrip", {"w":0.12,"h":0.4,"t":0.035,"er":4.2,"c":0.04,"erc":3.3}, "Z0"],
  ["field", "coated_microstrip", {"w":0.24,"h":0.8,"t":0.035,"er":4.2,"c":0.01,"erc":3.3}, "Z0"],
  ["field", "coated_microstrip", {"w":0.48,"h":1.6,"t":0.018,"er":4.2,"c":0.02,"erc":3.3}, "Z0"],
  ['field', 'cpw', { w: 0.2, gap: 0.1, h: 0.5, t: 0.035, er: 4.4 }, 'Z0'],
  ['field', 'cpw', { w: 0.5, gap: 0.25, h: 1.6, t: 0.035, er: 4.4 }, 'Z0'],
  ['field', 'cpw', { w: 1, gap: 0.2, h: 1.6, t: 0.035, er: 4.4 }, 'Z0'],
  ['field', 'cpw', { w: 0.3, gap: 0.15, h: 0.8, t: 0.018, er: 4.4 }, 'Z0'],
  ['field', 'coated_microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 }, 'Z0'],
];

// Inputs for parity: every model, inside and outside its validity range.
const PARITY = [];
const add = (model, args) => PARITY.push({ model, args });
for (const [w, h, t, er] of [[0.36, 0.2104, 0.035, 4.4], [0.1, 0.1, 0, 3], [3, 1.6, 0.035, 4.5], [0.005, 1, 0.035, 4.4], [0.2, 0.1, 0.05, 1], [200, 1, 0, 10]]) {
  add('microstrip', { w, h, t, er });
  add('ipc2141_microstrip', { w, h, t, er });
  add('coated_microstrip', { w, h, t, er, c: 0.015, erc: 3.8 });
  add('coupled_microstrip', { w, s: w / 2, h, t, er });
  add('ipc2141_coupled_microstrip', { w, s: w / 2, h, t, er });
}
add('coated_microstrip', { w: 0.3, h: 0.1, t: 0.035, er: 4.2, c: 0.06, erc: 6 });
add('coated_microstrip', { w: 0.3, h: 0.3, t: 0.018, er: 4.2, c: 0, erc: 3.5 });
for (const [w, h1, h2, t, er] of [[0.15, 0.2, 0.2, 0.035, 4.1], [0.15, 0.2, undefined, 0, 4.1], [0.1, 0.1, 0.3, 0.018, 3.8], [0.3, 0.05, 0.4, 0.035, 4], [1, 0.2, 0.4, 0.035, 4.2], [0.02, 0.5, 0.1, 0.07, 3]]) {
  const args = h2 === undefined ? { w, h1, t, er } : { w, h1, h2, t, er };
  add('stripline', args);
  add('ipc2141_stripline', args);
  add('coupled_stripline', { ...args, s: 0.1 });
  add('coupled_stripline', { ...args, s: 0.02 });
  add('ipc2141_coupled_stripline', { ...args, s: 0.1 });
}
for (const [w, gap, h, t, er] of [[0.3, 0.15, 0.3, 0.035, 4.5], [0.3, 0.15, 0.3, 0, 4.5], [0.15, 0.4, 0.1, 0.035, 4.4], [1, 0.05, 1.6, 0.035, 4.4], [0.5, 0.25, 1.6, 0.018, 1]]) {
  add('cpw', { w, gap, h, t, er });
  add('cpwg', { w, gap, h, t, er });
}

const SYNTHESIS = [
  ['microstrip', { h: 0.2104, t: 0.035, er: 4.4 }, 50, {}],
  ['coated_microstrip', { h: 0.2104, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 }, 50, {}],
  ['stripline', { h1: 0.2, h2: 0.3, t: 0.018, er: 4.1 }, 50, {}],
  ['cpwg', { gap: 0.15, h: 0.3, t: 0.035, er: 4.5 }, 50, {}],
  ['coupled_microstrip', { s: 0.15, h: 0.2104, t: 0.035, er: 4.4 }, 90, {}],
  ['coupled_microstrip', { w: 0.15, h: 0.2104, t: 0.035, er: 4.4 }, 90, { vary: 's' }],
  ['coupled_stripline', { s: 0.15, h1: 0.2, t: 0.018, er: 4.1 }, 100, {}],
  ['coupled_stripline', { w: 0.12, s: 0.2, h1: 0.2, t: 0.018, er: 4.1 }, 30, { key: 'Zcommon', vary: 's' }],
];

// Board-model stackups: the royalblue54L_feather golden board's (KiCad reader output) and a synthetic one with
// mixed εr, a voided plane, missing values and a mask with thickness_over_copper.
const ROYALBLUE = JSON.parse(readFileSync(new URL('../royalblue54L_feather/board.json', import.meta.url), 'utf8')).stackup;
const SYNTH = { layers: [
  { name: 'Top Solder Mask', kind: 'mask', side: 'top', thickness: 0.02, thickness_over_copper: 0.012, epsilon_r: 3.6 },
  { name: 'L1', kind: 'copper', side: 'top', thickness: 0.035, layer: 'F.Cu' },
  { name: 'PP1', kind: 'dielectric', side: 'inner', thickness: 0.21, epsilon_r: 4.2, dielectric: 'prepreg' },
  { name: 'L2', kind: 'copper', side: 'inner', thickness: 0.018, layer: 'In1.Cu' },
  { name: 'Core1', kind: 'dielectric', side: 'inner', thickness: 0.15, epsilon_r: 4.6, dielectric: 'core' },
  { name: 'L3', kind: 'copper', side: 'inner', thickness: null, layer: 'In2.Cu' },
  { name: 'PP2', kind: 'dielectric', side: 'inner', thickness: 0.3, epsilon_r: null, dielectric: 'prepreg' },
  { name: 'L4', kind: 'copper', side: 'inner', thickness: 0.018, layer: 'In3.Cu' },
  { name: 'Core2', kind: 'dielectric', side: 'inner', thickness: 0.2, epsilon_r: 4.4 },
  { name: 'L5', kind: 'copper', side: 'bottom', thickness: 0.035, layer: 'B.Cu' },
  { name: 'Bottom Solder Mask', kind: 'mask', side: 'bottom', thickness: null, epsilon_r: null },
] };
const STACKUPS = { royalblue: ROYALBLUE, synthetic: SYNTH };
const LINES = [
  ['royalblue', 'F.Cu', { width: 0.15 }],
  ['royalblue', 'In1.Cu', { width: 0.1 }],
  ['royalblue', 'In2.Cu', { width: 0.1, kind: 'differential', gap: 0.12 }],
  ['royalblue', 'B.Cu', { width: 0.2, kind: 'differential', gap: 0.15 }],
  ['royalblue', 'F.Cu', { width: 0.3, structure: 'coplanar_grounded', coplanarGap: 0.15 }],
  ['royalblue', 'F.Cu', { width: 0.15, mask: false }],
  ['synthetic', 'F.Cu', { width: 0.36 }],
  ['synthetic', 'In1.Cu', { width: 0.1 }],
  ['synthetic', 'In2.Cu', { width: 0.12 }],
  ['synthetic', 'In2.Cu', { width: 0.12, refTop: 'F.Cu' }],
  ['synthetic', 'B.Cu', { width: 0.2 }],
  ['synthetic', 'B.Cu', { width: 0.2, structure: 'coplanar', coplanarGap: 0.2 }],
];
const TARGETS = [
  ['royalblue', { kind: 'differential', target: 90, tolerance_pct: 10, common_mode: null, structure: 'microstrip', source: 'name',
    layers: [{ layer: 'F.Cu', width: 0.15, gap: 0.15, ref_top: null, ref_bottom: null }, { layer: 'B.Cu', width: null, gap: 0.15, ref_top: null, ref_bottom: null }] }],
  ['royalblue', { kind: 'single', target: 50, tolerance_pct: null, common_mode: null, structure: null, source: 'user',
    layers: [{ layer: 'In2.Cu', width: 0.1, gap: null, ref_top: 'In1.Cu', ref_bottom: 'In4.Cu' }, { layer: 'In1.Cu', width: null, gap: null, ref_top: null, ref_bottom: null }] }],
  ['synthetic', { kind: 'single', target: 50, tolerance_pct: 5, common_mode: null, structure: 'stripline', source: 'tuning_profile',
    layers: [{ layer: 'In2.Cu', width: 0.12, gap: null, ref_top: null, ref_bottom: null }] }],
];
// Python keyword names of the lineFromStackup options
const PY_OPTS = { coplanarGap: 'coplanar_gap', refTop: 'ref_top', refBottom: 'ref_bottom' };

const clean = (o) => JSON.parse(JSON.stringify(o));
const SWEEP_ROWS = JSON.parse(readFileSync(new URL('./field-sweep.json', import.meta.url), 'utf8')).rows;
/** A 'field' golden reference: the sweep's value for the geometry, else solved the way the sweep is. */
function fieldRef(model, args, key) {
  const row = SWEEP_ROWS.find(([m, a]) => m === model && JSON.stringify(a) === JSON.stringify(args));
  return (row ?? sweepRow(model, args, [key]))[2][key];
}
const out = {
  about: 'Shared boarddd/impedance cases (generated by make_cases.mjs; lengths in mm). See docs/impedance.md.',
  sources: SOURCES,
  golden: REFS.map(([source, model, args, key, ref], i) => ({ id: `${source}-${i}`, source, model, args, key,
    ref: source === 'field' ? fieldRef(model, args, key) : ref, tol_pct: SOURCES[source].tol_pct })),
  parity: PARITY.map(({ model, args }, i) => ({ id: `${model}-${i}`, model, args, expect: clean(z.calculate(model, args)) })),
  synthesis: SYNTHESIS.map(([model, params, target, opts]) => {
    const r = z.synthesize(model, params, target, opts);
    return { model, params, target, opts, expect: { value: r.value, result: clean(r.result) } };
  }),
  stackups: STACKUPS,
  stackup_lines: LINES.map(([st, layer, opts]) => ({ stackup: st, layer, opts,
    py_opts: Object.fromEntries(Object.entries(opts).map(([k, v]) => [PY_OPTS[k] ?? k, v])),
    expect: clean(z.lineFromStackup(STACKUPS[st], layer, opts)) })),
  stackup_targets: TARGETS.map(([st, target]) => ({ stackup: st, target, expect: clean(z.evaluateTarget(STACKUPS[st], target)) })),
};
// ── field solver cases ─────────────────────────────────────────────────────────────────────────────────────
const FIELD_SOURCES = {
  cohn: { tol_pct: 0.2, about: 'Cohn 1954/1955 exact conformal maps for zero-thickness strips (tier 1 at t = 0, checked against scipy.special.ellipk to 1e-13)' },
  polar: SOURCES.polar,
  hfss: SOURCES.hfss,
  hj: { tol_pct: 0.3, about: 'Hammerstad-Jensen 1980 for t = 0 (Z of the air line within 0.01 %, εeff within 0.2 % by its authors), as scikit-rf computes it' },
  kj: { tol_pct: 1, about: 'Kirschning-Jansen 1984, t = 0 (stated accuracy 0.6 %)' },
};
const FIELD_REFS = [
  ['cohn', 'stripline', { w: 0.15, h1: 0.2, t: 0, er: 4.1 }, 'Z0', 57.53618188390254],
  ['cohn', 'stripline', { w: 0.05, h1: 0.2, t: 0, er: 4 }, 'Z0', 90.45779609167748],
  ['cohn', 'stripline', { w: 1, h1: 0.2, t: 0, er: 3.5 }, 'Z0', 17.11596875137538],
  ['cohn', 'coupled_stripline', { w: 0.15, s: 0.1, h1: 0.2, t: 0, er: 4.1 }, 'Zodd', 46.81811579566902],
  ['cohn', 'coupled_stripline', { w: 0.15, s: 0.1, h1: 0.2, t: 0, er: 4.1 }, 'Zeven', 66.99967192178678],
  ['cohn', 'coupled_stripline', { w: 0.1, s: 0.2, h1: 0.15, t: 0, er: 4 }, 'Zodd', 58.60951872377179],
  ['cohn', 'coupled_stripline', { w: 0.1, s: 0.2, h1: 0.15, t: 0, er: 4 }, 'Zeven', 64.54162618734357],
  ['cohn', 'coupled_stripline', { w: 0.5, s: 0.1, h1: 0.25, t: 0, er: 3.6 }, 'Zodd', 29.483176252316362],
  ['cohn', 'coupled_stripline', { w: 0.5, s: 0.1, h1: 0.25, t: 0, er: 3.6 }, 'Zeven', 38.032940016511084],
  ['polar', 'microstrip', { w: 3.3, h: 0.794, t: 0.035, er: 4.2 }, 'Z0', 30.09],
  ['polar', 'microstrip', { w: 1.5, h: 0.794, t: 0.035, er: 4.2 }, 'Z0', 50.63],
  ['polar', 'microstrip', { w: 0.45, h: 0.794, t: 0.035, er: 4.2 }, 'Z0', 89.63],
  ['hfss', 'microstrip', { w: 3, h: 1.6, t: 0.035, er: 4.5 }, 'Z0', 49.8],
  ['hfss', 'stripline', { w: 0.15, h1: 0.2, h2: 0.2, t: 0.035, er: 4.1 }, 'Z0', 50.611],
  ['hfss', 'cpwg', { w: 0.3, gap: 0.15, h: 0.3, t: 0.035, er: 4.5 }, 'Z0', 55.465],
  ['hfss', 'coupled_microstrip', { w: 3, s: 1, h: 1.6, t: 0.035, er: 4.5 }, 'Zodd', 40.23],
  ['hfss', 'coupled_microstrip', { w: 3, s: 1, h: 1.6, t: 0.035, er: 4.5 }, 'Zeven', 57.65],
  ['hfss', 'coupled_stripline', { w: 0.15, s: 0.1, h1: 0.2, h2: 0.2, t: 0.035, er: 4.1 }, 'Zodd', 37.6],
  ['hfss', 'coupled_stripline', { w: 0.15, s: 0.1, h1: 0.2, h2: 0.2, t: 0.035, er: 4.1 }, 'Zeven', 61.36],
  ['hj', 'microstrip', { w: 0.1, h: 0.1, t: 0, er: 3 }, 'Z0', 83.71029483726397],
  ['hj', 'microstrip', { w: 10, h: 1, t: 0, er: 9.8 }, 'Z0', 10.002288197227097],
  ['hj', 'microstrip', { w: 0.36, h: 0.2104, t: 0, er: 4.4 }, 'Z0', z.microstrip({ w: 0.36, h: 0.2104, t: 0, er: 4.4 }).Z0],
  ['kj', 'coupled_microstrip', { w: 0.15, s: 0.15, h: 0.2, t: 0, er: 4.4 }, 'Zodd', z.coupledMicrostrip({ w: 0.15, s: 0.15, h: 0.2, er: 4.4 }).Zodd],
  ['kj', 'coupled_microstrip', { w: 0.15, s: 0.15, h: 0.2, t: 0, er: 4.4 }, 'Zeven', z.coupledMicrostrip({ w: 0.15, s: 0.15, h: 0.2, er: 4.4 }).Zeven],
];

// Generic sections: what only the field solver does.
const SECTIONS = {
  asymmetric_pair: { conductors: [{ y0: -0.035, y1: 0, net: 'gnd' }, { x0: -0.25, x1: -0.1, y0: 0.2, y1: 0.235, net: 'p' }, { x0: 0.05, x1: 0.15, y0: 0.2, y1: 0.235, net: 'n' }],
    dielectrics: [{ y0: 0, y1: 0.2, er: 4.4 }] },
  three_signals: { conductors: [{ y0: -0.035, y1: 0, net: 'gnd' }, { y0: 0.5, y1: 0.535, net: 'gnd' }, ...[-0.3, 0, 0.3].map((x, k) => ({ x0: x - 0.05, x1: x + 0.05, y0: 0.24, y1: 0.258, net: `s${k}` }))],
    dielectrics: [{ y0: 0, y1: 0.24, er: 4.2 }, { y0: 0.24, y1: 0.5, er: 3.9 }] },
  broadside_pair: { conductors: [{ y0: -0.035, y1: 0, net: 'gnd' }, { y0: 0.6, y1: 0.635, net: 'gnd' }, { x0: -0.1, x1: 0.1, y0: 0.2, y1: 0.218, net: 'top' }, { x0: -0.1, x1: 0.1, y0: 0.382, y1: 0.4, net: 'bottom' }],
    dielectrics: [{ y0: 0, y1: 0.6, er: 4 }] },
  finite_ground_cpwg_masked: { conductors: [{ x0: -1, x1: 1, y0: -0.035, y1: 0, net: 'gnd' }, { x0: -0.15, x1: 0.15, y0: 0.3, y1: 0.335, net: 'sig' },
    { x0: -0.8, x1: -0.3, y0: 0.3, y1: 0.335, net: 'gnd' }, { x0: 0.3, x1: 0.8, y0: 0.3, y1: 0.335, net: 'gnd' }],
  dielectrics: [{ x0: -1, x1: 1, y0: 0, y1: 0.3, er: 4.5 }, { x0: -1, x1: 1, y0: 0.3, y1: 0.32, er: 3.5 }, { x0: -0.17, x1: 0.17, y0: 0.3, y1: 0.355, er: 3.5 },
    { x0: -0.82, x1: -0.28, y0: 0.3, y1: 0.355, er: 3.5 }, { x0: 0.28, x1: 0.82, y0: 0.3, y1: 0.355, er: 3.5 }] },
  plane_void: { conductors: [{ x1: -0.4, y0: -0.035, y1: 0, net: 'gnd' }, { x0: 0.4, y0: -0.035, y1: 0, net: 'gnd' }, { y0: -0.335, y1: -0.3, net: 'gnd' }, { x0: -0.1, x1: 0.1, y0: 0.15, y1: 0.185, net: 'sig' }],
    dielectrics: [{ y0: -0.3, y1: 0, er: 4.6 }, { y0: 0, y1: 0.15, er: 4.2 }] },
};
const FIELD_MODELS = [
  ['microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4 }, {}],
  ['coated_microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 }, {}],
  ['stripline', { w: 0.1, h1: 0.1, h2: 0.3, t: 0.018, er: 3.8 }, {}],
  ['cpw', { w: 0.2, gap: 0.1, h: 0.5, t: 0.035, er: 4.4 }, {}],
  ['cpwg', { w: 0.3, gap: 0.15, h: 0.3, t: 0.035, er: 4.5, fence: 0.6 }, {}],
  ['coupled_microstrip', { w: 0.15, s: 0.15, h: 0.2104, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 }, {}],
  ['coupled_stripline', { w: 0.1, s: 0.15, h1: 0.15, h2: 0.3, t: 0.018, er: 4.1 }, {}],
  ['coupled_cpwg', { w: 0.1, s: 0.15, gap: 0.2, gnd: 0.5, h: 0.15, t: 0.018, er: 4.1 }, {}],
  ['microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4 }, { symmetry: false, tol: 0.003 }],
];
const FIELD_LINES = [
  ['royalblue', 'F.Cu', { width: 0.15 }],
  ['royalblue', 'In2.Cu', { width: 0.1, kind: 'differential', gap: 0.12 }],
  ['royalblue', 'B.Cu', { width: 0.2, kind: 'differential', gap: 0.15 }],
  ['royalblue', 'F.Cu', { width: 0.2, kind: 'differential', gap: 0.15, structure: 'coplanar_grounded', coplanarGap: 0.2 }],
  ['synthetic', 'In1.Cu', { width: 0.1 }],
  ['synthetic', 'In2.Cu', { width: 0.12, refTop: 'F.Cu' }],
  ['synthetic', 'In2.Cu', { width: 0.12, structure: 'coplanar_grounded', coplanarGap: 0.15 }],
  ['synthetic', 'B.Cu', { width: 0.2, structure: 'coplanar', coplanarGap: 0.2 }],
];
const lean = (r) => { const o = clean(r); delete o.ms; return o; };
const fieldOut = {
  about: 'Shared boarddd/impedance field-solver cases (generated by make_cases.mjs; lengths in mm). See docs/impedance.md.',
  sources: FIELD_SOURCES,
  golden: FIELD_REFS.map(([source, model, args, key, ref], i) => ({ id: `${source}-${i}`, source, model, args, key, ref, tol_pct: FIELD_SOURCES[source].tol_pct })),
  sections: [
    ...Object.entries(SECTIONS).map(([id, section]) => ({ id, section, opts: {}, expect: lean(solveCrossSection(section)) })),
    ...FIELD_MODELS.map(([model, args, opts], i) => ({ id: `${model}-${i}`, model, args, opts, expect: lean(fieldCalculate(model, args, opts)) })),
  ],
  stackup_lines: FIELD_LINES.map(([st, layer, opts]) => {
    const o = { ...opts, solver: 'field' };
    const line = z.lineFromStackup(STACKUPS[st], layer, o);
    return { stackup: st, layer, opts: o, py_opts: Object.fromEntries(Object.entries(o).map(([k, v]) => [PY_OPTS[k] ?? k, v])),
      expect: clean(line), result: lean(solveCrossSection(line.section)) };
  }),
  stackup_targets: TARGETS.slice(0, 2).map(([st, target]) => ({ stackup: st, target,
    expect: z.evaluateTarget(STACKUPS[st], target, { solver: 'field' }).map((r) => ({ ...clean(r), result: lean(r.result) })) })),
  // Every 60th row of the sweep, recomputed by both test suites.
  sweep_sample: SWEEP_ROWS.map((r, index) => ({ index, row: r })).filter((_, i) => i % 60 === 0),
};

const files = [['./cases.json', out], ['./field-cases.json', fieldOut]];
let stale = false;
for (const [name, data] of files) {
  const text = `${JSON.stringify(data, null, 1)}\n`;
  const path = new URL(name, import.meta.url);
  if (process.argv.includes('--check')) {
    let old = '';
    try { old = readFileSync(path, 'utf8'); } catch { /* missing */ }
    if (old !== text) { console.error(`fixtures/impedance/${name.slice(2)} is stale: run node fixtures/impedance/make_cases.mjs`); stale = true; }
  } else writeFileSync(path, text);
}
if (stale) process.exit(1);
