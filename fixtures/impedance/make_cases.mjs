// Writes fixtures/impedance/cases.json: the shared impedance cases that test/impedance (JS) and
// python/tests/test_impedance.py both run.
//   golden     published or independently computed reference values, each with its source and tolerance
//   parity     inputs spanning every model, flagged and not, with the JS results; Python must match them to 1e-9
//   synthesis  width/spacing solves with their JS results
// Usage: node fixtures/impedance/make_cases.mjs [--check]   (--check fails if cases.json is stale)
import { readFileSync, writeFileSync } from 'node:fs';
import * as z from '../../src/impedance/index.js';

const SOURCES = {
  polar: { tol_pct: 1, about: 'Polar Instruments Si9000 (BEM field solver), published microstrip cases: IPC 1999 paper, https://www.polarinstruments.com/support/cits/IPC1999.pdf' },
  hfss: { tol_pct: 2, about: 'Ansys HFSS, 1 GHz full-wave with loss, as published in Ttl/js_2d_fields tests/test_vs_ref.js (loss raises Z about 0.5-1 % over a quasi-static answer)' },
  cohn: { tol_pct: 1e-9, about: 'Cohn 1954/1955 exact conformal maps (t = 0), evaluated with scipy.special.ellipk (an independent elliptic-integral implementation)' },
  skrf: { tol_pct: 0.01, about: 'scikit-rf 2.1.0 media.MLine (hammerstadjensen, no dispersion) and media.CPW (Ghione, t = 0), 1 MHz, lossless: an independent BSD-3 implementation of the same papers' },
  qs: { tol_pct: 2, about: "Quasi-static 2D field solutions from hforsten's solver (Ttl/js_2d_fields, GPL-3.0), run locally as a reference only (numbers, no code); lossless, adaptive mesh" },
  fdm: { tol_pct: 2.5, about: 'The t-0272 spike finite-volume field solver (MIT, ours; coarse grid, reads about 1-2 % low on GCPW)' },
};

// [source, model, args (mm), key, reference]
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
  ["qs", "microstrip", {"w":0.02,"h":0.2,"t":0.035,"er":3}, "Z0", 141.9559],
  ["qs", "microstrip", {"w":0.6,"h":0.2,"t":0.035,"er":3}, "Z0", 42.7911],
  ["qs", "microstrip", {"w":0.24,"h":0.8,"t":0.035,"er":3}, "Z0", 126.1335],
  ["qs", "microstrip", {"w":8,"h":0.8,"t":0.035,"er":3}, "Z0", 17.6375],
  ["qs", "microstrip", {"w":1.6,"h":1.6,"t":0.035,"er":3}, "Z0", 82.2398],
  ["qs", "stripline", {"w":0.02,"h1":0.091,"h2":0.091,"t":0.018,"er":4}, "Z0", 72.5305],
  ["qs", "stripline", {"w":0.6,"h1":0.06067,"h2":0.12133,"t":0.018,"er":4}, "Z0", 10.9383],
  ["qs", "stripline", {"w":0.4,"h1":0.191,"h2":0.191,"t":0.018,"er":4}, "Z0", 30.0864],
  ["qs", "stripline", {"w":0.1,"h1":0.4825,"h2":0.4825,"t":0.035,"er":4}, "Z0", 83.5944],
  ["qs", "stripline", {"w":3,"h1":0.491,"h2":0.491,"t":0.018,"er":4}, "Z0", 13.3185],
  ["qs", "stripline", {"w":0.15,"h1":0.11625,"h2":0.34875,"t":0.035,"er":4}, "Z0", 46.2525],
  ["qs", "cpwg", {"w":0.15,"gap":0.1,"h":0.1,"t":0.035,"er":4.4}, "Z0", 47.7801],
  ["qs", "cpwg", {"w":0.3,"gap":0.2,"h":0.4,"t":0.035,"er":4.4}, "Z0", 63.3042],
  ["qs", "cpwg", {"w":0.6,"gap":0.4,"h":1,"t":0.035,"er":4.4}, "Z0", 68.6747],
  ["qs", "cpwg", {"w":0.15,"gap":0.1,"h":0.4,"t":0.018,"er":4.4}, "Z0", 69.1546],
  ["qs", "cpwg", {"w":0.6,"gap":0.4,"h":0.4,"t":0.018,"er":4.4}, "Z0", 53.3764],
  ["qs", "coupled_microstrip", {"w":0.04,"s":0.04,"h":0.2,"t":0.035,"er":4.4}, "Zodd", 48.7767],
  ["qs", "coupled_microstrip", {"w":0.04,"s":0.04,"h":0.2,"t":0.035,"er":4.4}, "Zeven", 163.3729],
  ["qs", "coupled_microstrip", {"w":0.1,"s":0.2,"h":0.2,"t":0.035,"er":4.4}, "Zodd", 68.8111],
  ["qs", "coupled_microstrip", {"w":0.1,"s":0.2,"h":0.2,"t":0.035,"er":4.4}, "Zeven", 101.5553],
  ["qs", "coupled_microstrip", {"w":0.4,"s":0.04,"h":0.2,"t":0.035,"er":4.4}, "Zodd", 27.3521],
  ["qs", "coupled_microstrip", {"w":0.4,"s":0.04,"h":0.2,"t":0.035,"er":4.4}, "Zeven", 57.6368],
  ["qs", "coupled_stripline", {"w":0.06,"s":0.06,"h1":0.141,"h2":0.141,"t":0.018,"er":4}, "Zodd", 44.4107],
  ["qs", "coupled_stripline", {"w":0.06,"s":0.06,"h1":0.141,"h2":0.141,"t":0.018,"er":4}, "Zeven", 82.191],
  ["qs", "coupled_stripline", {"w":0.15,"s":0.3,"h1":0.094,"h2":0.188,"t":0.018,"er":4}, "Zodd", 40.2428],
  ["qs", "coupled_stripline", {"w":0.15,"s":0.3,"h1":0.094,"h2":0.188,"t":0.018,"er":4}, "Zeven", 41.401],
  ["qs", "coupled_stripline", {"w":0.1,"s":0.25,"h1":0.2325,"h2":0.2325,"t":0.035,"er":4}, "Zodd", 55.8746],
  ["qs", "coupled_stripline", {"w":0.1,"s":0.25,"h1":0.2325,"h2":0.2325,"t":0.035,"er":4}, "Zeven", 69.998],
  ["qs", "coated_microstrip", {"w":0.03,"h":0.1,"t":0.018,"er":4.2,"c":0.01,"erc":3.3}, "Z0", 94.5722],
  ["qs", "coated_microstrip", {"w":0.2,"h":0.2,"t":0.018,"er":4.2,"c":0.02,"erc":3.3}, "Z0", 66.8044],
  ["qs", "coated_microstrip", {"w":0.12,"h":0.4,"t":0.035,"er":4.2,"c":0.04,"erc":3.3}, "Z0", 99.8505],
  ["qs", "coated_microstrip", {"w":0.24,"h":0.8,"t":0.035,"er":4.2,"c":0.01,"erc":3.3}, "Z0", 108.8425],
  ["qs", "coated_microstrip", {"w":0.48,"h":1.6,"t":0.018,"er":4.2,"c":0.02,"erc":3.3}, "Z0", 112.1329],
  ['fdm', 'cpw', { w: 0.2, gap: 0.1, h: 0.5, t: 0.035, er: 4.4 }, 'Z0', 61.86],
  ['fdm', 'cpw', { w: 0.5, gap: 0.25, h: 1.6, t: 0.035, er: 4.4 }, 'Z0', 67.81],
  ['fdm', 'cpw', { w: 1, gap: 0.2, h: 1.6, t: 0.035, er: 4.4 }, 'Z0', 53.1],
  ['fdm', 'cpw', { w: 0.3, gap: 0.15, h: 0.8, t: 0.018, er: 4.4 }, 'Z0', 68.7],
  ['fdm', 'coated_microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 }, 'Z0', 49.71],
  ['qs', 'coated_microstrip', { w: 0.36, h: 0.2104, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 }, 'Z0', 49.555],
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

const clean = (o) => JSON.parse(JSON.stringify(o));
const out = {
  about: 'Shared boarddd/impedance cases (generated by make_cases.mjs; lengths in mm). See docs/impedance.md.',
  sources: SOURCES,
  golden: REFS.map(([source, model, args, key, ref], i) => ({ id: `${source}-${i}`, source, model, args, key, ref, tol_pct: SOURCES[source].tol_pct })),
  parity: PARITY.map(({ model, args }, i) => ({ id: `${model}-${i}`, model, args, expect: clean(z.calculate(model, args)) })),
  synthesis: SYNTHESIS.map(([model, params, target, opts]) => {
    const r = z.synthesize(model, params, target, opts);
    return { model, params, target, opts, expect: { value: r.value, result: clean(r.result) } };
  }),
};
const text = `${JSON.stringify(out, null, 1)}\n`;
const path = new URL('./cases.json', import.meta.url);
if (process.argv.includes('--check')) {
  let old = '';
  try { old = readFileSync(path, 'utf8'); } catch { /* missing */ }
  if (old !== text) { console.error('fixtures/impedance/cases.json is stale: run node fixtures/impedance/make_cases.mjs'); process.exit(1); }
} else writeFileSync(path, text);
