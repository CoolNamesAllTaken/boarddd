// The value of a loss golden case (fixtures/impedance/loss-cases.json), shared by test/impedance/loss.test.mjs and
// loss_report.mjs; python/tests/test_impedance_loss.py has the same evaluator.
import * as z from '../../src/impedance/index.js';

const NP_DB_IN = (20 / Math.LN10) * 0.0254;
const solved = new Map();
/** A golden case's value. */
export function evaluate(g) {
  const a = g.args;
  const pick = (r, i = 0) => ({
    alpha_c: r.alpha_c[i], alpha_d: r.alpha_d[i], alpha: r.alpha[i], db_per_inch: r.db_per_inch[i], Z0: r.Z0[i],
    alpha_c_db_per_inch: r.alpha_c[i] * NP_DB_IN, alpha_d_db_per_inch: r.alpha_d[i] * NP_DB_IN, Z0_lossless: Math.sqrt(r.L[i] / r.C[i]),
  })[g.key];
  switch (g.kind) {
    case 'dielectric': return z.dielectricAt(a.f, a)[g.key];
    case 'cannonball': return z.cannonball(a.rz)[g.key];
    case 'huray_area': return 36 * a.radius * a.radius;
    case 'dispersion': return z.microstripDispersion(a, a.f)[g.key];
    case 'line': return pick(z.lineLoss(a.model, a.params, [a.f], a.loss));
    case 'section': {
      const key = JSON.stringify([a.section, a.solve]);
      if (!solved.has(key)) solved.set(key, z.solveCrossSection(a.section, { ...a.solve, loss: true }));
      return pick(z.sectionLoss(solved.get(key), [a.f], a.loss));
    }
    case 'wheeler': {
      const f = 1e10, r = z.lineLoss(a.model, a.params, [f]);
      return r.R[0] / ((2 * z.surfaceResistance(f)) / (a.params.w * 1e-3));
    }
    default: throw new Error(`unknown golden kind ${g.kind}`);
  }
}

