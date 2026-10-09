// boarddd/impedance: loss and frequency dependence (impedance phase I9), written for boarddd from the papers
// cited per function (no code from KiCad, Qucs, Transcalc or js_2d_fields). python/src/boarddd/impedance/loss.py
// is the same code; fixtures/impedance/loss-cases.json keeps the two in step. docs/impedance.md, "Loss and
// frequency", has the method and its validation.
//
// - Dielectrics: Djordjevic-Sarkar wideband Debye model from (Dk, Df) at a reference frequency (causal).
// - Conductors: skin effect, Wheeler's incremental inductance rule per metal (tier 1: on the closed-form air
//   impedance; tier 2: the field solver's exact shape derivative), Hammerstad and Huray (cannonball) roughness,
//   DC resistance and internal inductance.
// - Microstrip dispersion (tier 1): Kirschning-Jansen εeff(f), Z0(f), and the coupled-line even/odd modes.
// - RLGC per unit length, γ and Zc per mode, insertion loss, S-parameters and Touchstone .s2p / .s4p.
//
// Units: geometry in mm (as everywhere in boarddd), frequency in Hz, R Ω/m, L H/m, G S/m, C F/m, α Np/m.

import { calculate, ETA0 } from './closedform.js';

const { PI, sqrt, log, exp, atan, atan2, abs, cos, sin, cosh, sinh } = Math;
const C_LIGHT = 299792458;
const EPS0 = 8.8541878128e-12;
const MU0 = 1 / (EPS0 * C_LIGHT * C_LIGHT);
const NP_TO_DB = 20 / Math.LN10;   // 8.686 dB per neper
const INCH = 25.4;                 // mm

/** Defaults: annealed copper (IEC 60028), and the Djordjevic-Sarkar band and reference frequency. */
export const LOSS_DEFAULTS = Object.freeze({
  conductivity: 5.8e7,   // S/m
  frequency: 1e9,        // Hz at which Er/Df are given when the source does not say
  f_low: 1e3,            // Djordjevic-Sarkar lower and upper corner frequencies, Hz
  f_high: 1e12,
});

// ── complex arithmetic on [re, im] ────────────────────────────────────────────────────────────────────────

const cadd = (a, b) => [a[0] + b[0], a[1] + b[1]];
const csub = (a, b) => [a[0] - b[0], a[1] - b[1]];
const cmul = (a, b) => [a[0] * b[0] - a[1] * b[1], a[0] * b[1] + a[1] * b[0]];
const cdiv = (a, b) => { const d = b[0] * b[0] + b[1] * b[1]; return [(a[0] * b[0] + a[1] * b[1]) / d, (a[1] * b[0] - a[0] * b[1]) / d]; };
/** Principal square root (Re >= 0). */
const csqrt = (a) => {
  const r = sqrt(sqrt(a[0] * a[0] + a[1] * a[1]));
  const t = atan2(a[1], a[0]) / 2;
  return [r * cos(t), r * sin(t)];
};
const ccosh = (a) => [cosh(a[0]) * cos(a[1]), sinh(a[0]) * sin(a[1])];
const csinh = (a) => [sinh(a[0]) * cos(a[1]), cosh(a[0]) * sin(a[1])];

// ── dielectrics: Djordjevic-Sarkar ────────────────────────────────────────────────────────────────────────
// A. R. Djordjevic, R. M. Biljić, V. D. Likar-Smiljanić, T. K. Sarkar, "Wideband frequency-domain
// characterization of FR-4 and time-domain causality", IEEE Trans. EMC 43(4), 2001, pp. 662-667: a continuous
// distribution of Debye poles between ω1 and ω2,
//   ε(ω) = ε∞ + Δε / ln(ω2/ω1) · ln((ω2 + jω) / (ω1 + jω)),
// is causal, and its loss tangent is nearly flat across the band, which is how laminates behave. Given Dk, Df at
// a reference frequency f0 (with ε = ε' - jε''), k = Δε / ln(ω2/ω1) = ε'(f0) tanδ(f0) / -Im ln(...)(f0) and
// ε∞ = ε'(f0) - k Re ln(...)(f0). The ratio is the same in Hz or rad/s.

/**
 * A dielectric's Dk and Df at frequency f.
 * @param {number} f  Hz
 * @param {{er: number, tand?: number, frequency?: number, model?: 'constant'|'djordjevic_sarkar', f_low?: number, f_high?: number}} m
 *   er, tand: the values at `frequency` (default 1 GHz); model default djordjevic_sarkar
 * @returns {{er: number, tand: number}}
 */
export function dielectricAt(f, m) {
  const er = m.er, tand = m.tand ?? 0;
  if (!(er >= 1)) throw new RangeError(`er must be >= 1 (got ${er})`);
  if (!(tand >= 0)) throw new RangeError(`tand must be >= 0 (got ${tand})`);
  if ((m.model ?? 'djordjevic_sarkar') === 'constant' || tand === 0) return { er, tand };
  if (m.model !== 'djordjevic_sarkar' && m.model != null) throw new RangeError(`unknown dielectric model ${m.model}`);
  const f0 = m.frequency ?? LOSS_DEFAULTS.frequency, f1 = m.f_low ?? LOSS_DEFAULTS.f_low, f2 = m.f_high ?? LOSS_DEFAULTS.f_high;
  if (!(f2 > f1 && f1 > 0)) throw new RangeError('djordjevic_sarkar needs 0 < f_low < f_high');
  // ln((f2 + jx)/(f1 + jx)) = ½ ln((f2² + x²)/(f1² + x²)) + j (atan(x/f2) - atan(x/f1))
  const ln = (x) => [0.5 * log((f2 * f2 + x * x) / (f1 * f1 + x * x)), atan2(x, f2) - atan2(x, f1)];
  const [a0, b0] = ln(f0);
  const k = (-er * tand) / b0;
  const einf = er - k * a0;
  const [a, b] = ln(f);
  const e1 = einf + k * a, e2 = -k * b;
  return { er: e1, tand: e2 / e1 };
}

// ── conductors ────────────────────────────────────────────────────────────────────────────────────────────

/** Skin depth δ = 1/√(π f μ0 σ), metres. */
export function skinDepth(f, sigma = LOSS_DEFAULTS.conductivity) {
  return 1 / sqrt(PI * f * MU0 * sigma);
}

/** Surface resistance Rs = 1/(σ δ) = √(π f μ0 / σ), Ω per square. */
export function surfaceResistance(f, sigma = LOSS_DEFAULTS.conductivity) {
  return sqrt((PI * f * MU0) / sigma);
}

// Copper roughness, as the factor K(f) >= 1 on the smooth surface resistance.
// - Hammerstad: E. Hammerstad, Ø. Bekkadal, "Microstrip Handbook", ELAB report STF44 A74169, Univ. Trondheim 1975
//   (also Hammerstad-Jensen 1980): K = 1 + (2/π) atan(1.4 (Δ/δ)²), Δ the RMS roughness. Saturates at 2.
// - Huray: P. G. Huray et al., "Impact of copper surface texture on loss: a model that works", DesignCon 2010;
//   P. G. Huray, "The Foundations of Signal Integrity", Wiley 2009: spheres ("snowballs") of radius a on a matte
//   base, K = A_matte/A_flat + (3/2) (N 4π a²/A_flat) / (1 + δ/a + δ²/(2a²)).
// - Cannonball: B. Simonovich, "Practical method for modeling conductor surface roughness using close packing of
//   equal spheres", DesignCon 2015, and Polar Instruments application note AP8195 (2017): the Huray parameters
//   from the datasheet Rz alone, 14 spheres of radius a = Rz/16.73 on a square tile of side 6a, A_matte/A_flat = 1.

/** Huray parameters of the cannonball stack for a 10-point roughness Rz (mm): radius (mm), ratio (N 4π a²/A). */
export function cannonball(rz) {
  if (!(rz > 0)) throw new RangeError(`rz must be > 0 (got ${rz})`);
  const radius = rz / 16.73;
  return { radius, count: 14, area: 36 * radius * radius, ratio: (14 * 4 * PI) / 36, matte: 1 };
}

/**
 * The roughness spec of a copper layer: explicit `{model, ...}` or from board@1 fields. Returns null for smooth.
 * @param {object|null} r  {model: 'none'|'hammerstad'|'huray'|'cannonball', rq, rz, radius, ratio, matte}
 */
function roughnessSpec(r) {
  if (r == null || r.model === 'none') return null;
  const model = r.model ?? (r.radius != null ? 'huray' : r.rz != null ? 'cannonball' : r.rq != null ? 'hammerstad' : 'none');
  if (model === 'none') return null;
  if (model === 'hammerstad') {
    if (!(r.rq >= 0)) throw new RangeError(`hammerstad roughness needs rq >= 0 (got ${r.rq})`);
    return { model, rq: r.rq };
  }
  if (model === 'cannonball') return { ...cannonball(r.rz), model: 'huray', rz: r.rz };
  if (model === 'huray') {
    if (!(r.radius > 0)) throw new RangeError(`huray roughness needs radius > 0 (got ${r.radius})`);
    const ratio = r.ratio ?? (r.count != null && r.area != null ? (r.count * 4 * PI * r.radius * r.radius) / r.area : null);
    if (!(ratio >= 0)) throw new RangeError('huray roughness needs ratio (N 4π a² / A_flat) or count and area');
    return { model, radius: r.radius, ratio, matte: r.matte ?? 1 };
  }
  throw new RangeError(`unknown roughness model ${model}`);
}

/**
 * Roughness factor K(f) on the surface resistance (1 for smooth copper).
 * @param {number} f  Hz
 * @param {object|null} r  {model: 'hammerstad', rq} | {model: 'huray', radius, ratio, matte?} | {model: 'cannonball', rz}
 *   (lengths in mm), or board@1-style fields from which the model is inferred; null: smooth
 */
export function roughnessFactor(f, r, sigma = LOSS_DEFAULTS.conductivity) {
  const s = roughnessSpec(r);
  if (!s) return 1;
  const d = skinDepth(f, sigma) * 1e3;   // mm
  if (s.model === 'hammerstad') return 1 + (2 / PI) * atan(1.4 * (s.rq / d) ** 2);
  const a = s.radius;
  return s.matte + (1.5 * s.ratio) / (1 + d / a + (d * d) / (2 * a * a));
}

// ── microstrip dispersion: Kirschning-Jansen ──────────────────────────────────────────────────────────────
// M. Kirschning, R. H. Jansen, "Accurate model for effective dielectric constant of microstrip with validity up
// to millimetre-wave frequencies", Electronics Letters 18(6), 1982, pp. 272-273: εeff(f) = εr - (εr - εeff(0))
// / (1 + P(f)); R. H. Jansen, M. Kirschning, "Arguments and an accurate model for the power-current formulation
// of microstrip characteristic impedance", AEÜ 37, 1983, pp. 108-112: Z0(f) = Z0(0) (R13/R14)^R17. Coupled lines:
// M. Kirschning, R. H. Jansen, IEEE Trans. MTT-32(1), 1984, pp. 83-90 (corrections MTT-33(3), 1985): even and
// odd mode εeff(f) (P1-P15) and Z(f) (Q0-Q29). fn = f/GHz · h/mm (the papers' GHz·cm constants rescaled).
// Stated accuracy: single line 0.6 % (εeff) up to fn = 60 GHz·mm with 0.1 <= u <= 100, 1 <= εr <= 20;
// coupled 1.4 % up to fn = 25 GHz·mm with 0.1 <= u, g <= 10, 1 <= εr <= 18.

const clampExp = (x) => Math.min(x, 20);   // exp(-x) of the overflow-prone terms: below e^-20 they are 0 anyway

function kjP(u, er, fn) {
  const P1 = 0.27488 + (0.6315 + 0.525 / (1 + 0.0157 * fn) ** 20) * u - 0.065683 * exp(-8.7513 * u);
  const P2 = 0.33622 * (1 - exp(-0.03442 * er));
  const P3 = 0.0363 * exp(-4.6 * u) * (1 - exp(-((fn / 38.7) ** 4.97)));
  const P4 = 1 + 2.751 * (1 - exp(-((er / 15.916) ** 8)));
  return { P1, P2, P3, P4 };
}

/** Single microstrip εeff(f) (Kirschning-Jansen 1982). */
function kjEpsEff(u, er, ee0, fn) {
  const { P1, P2, P3, P4 } = kjP(u, er, fn);
  const P = P1 * P2 * ((0.1844 + P3 * P4) * fn) ** 1.5763;
  return er - (er - ee0) / (1 + P);
}

/** Single microstrip Z0(f) from Z0(0) (Jansen-Kirschning 1983); also returns R17 (the coupled model's Q0). */
function kjZ0(u, er, ee0, eef, Z00, fn) {
  const R1 = 0.03891 * er ** 1.4, R2 = 0.2671 * u ** 7, R3 = 4.766 * exp(-3.228 * u ** 0.641);
  const R4 = 0.016 + (0.0514 * er) ** 4.524, R5 = (fn / 28.843) ** 12, R6 = 22.2 * u ** 1.92;
  const R7 = 1.206 - 0.3144 * exp(-clampExp(R1)) * (1 - exp(-clampExp(R2)));
  const R8 = 1 + 1.275 * (1 - exp(-0.004625 * R3 * er ** 1.674 * (fn / 18.365) ** 2.745));
  const R9 = ((5.086 * R4 * R5) / (0.3838 + 0.386 * R4)) * (exp(-clampExp(R6)) / (1 + 1.2992 * R5))
    * ((er - 1) ** 6 / (1 + 10 * (er - 1) ** 6));
  const R10 = 0.00044 * er ** 2.136 + 0.0184;
  const R11 = (fn / 19.47) ** 6 / (1 + 0.0962 * (fn / 19.47) ** 6);
  const R12 = 1 / (1 + 0.00245 * u * u);
  const R13 = 0.9408 * eef ** R8 - 0.9603;
  const R14 = (0.9408 - R9) * ee0 ** R8 - 0.9603;
  const R15 = 0.707 * R10 * (fn / 12.3) ** 1.097;
  const R16 = 1 + 0.0503 * er * er * R11 * (1 - exp(-((u / 15) ** 6)));
  const R17 = R7 * (1 - ((1.1241 * R12) / R16) * exp(-0.026 * fn ** 1.15656 - R15));
  return { Z: Z00 * (R13 / R14) ** R17, R17 };
}

/**
 * Microstrip dispersion (Kirschning-Jansen 1982/1983): εeff and Z0 at frequency f from their static values.
 * @param {{w: number, h: number, er: number, eps_eff: number, Z0: number}} p  mm; the static eps_eff and Z0
 *   (thickness and mask included: their ratio to the zero-thickness line is kept)
 * @param {number} f  Hz
 */
export function microstripDispersion(p, f) {
  const u = p.w / p.h, fn = (f / 1e9) * p.h;
  const eef = kjEpsEff(u, p.er, p.eps_eff, fn);
  return { eps_eff: eef, Z0: kjZ0(u, p.er, p.eps_eff, eef, p.Z0, fn).Z };
}

/**
 * Coupled microstrip dispersion (Kirschning-Jansen 1984, with the 1985 corrections): even and odd εeff and Z at
 * frequency f from their static values and the single line's (width w, zero thickness) static values.
 * @param {{w: number, s: number, h: number, er: number, Zeven: number, Zodd: number, eps_eff_even: number,
 *   eps_eff_odd: number, single: {Z0: number, eps_eff: number}}} p
 */
export function coupledMicrostripDispersion(p, f) {
  const u = p.w / p.h, g = p.s / p.h, er = p.er, fn = (f / 1e9) * p.h;
  const { P1, P2, P3, P4 } = kjP(u, er, fn);
  const P5 = 0.334 * exp(-3.3 * (er / 15) ** 3) + 0.746;
  const P6 = P5 * exp(-((fn / 18) ** 0.368));
  const P7 = 1 + 4.069 * P6 * g ** 0.479 * exp(-1.347 * g ** 0.595 - 0.17 * g ** 2.5);
  const Fe = P1 * P2 * ((P3 * P4 + 0.1844 * P7) * fn) ** 1.5763;
  const P8 = 0.7168 * (1 + 1.076 / (1 + 0.0576 * (er - 1)));
  const P9 = P8 - 0.7913 * (1 - exp(-((fn / 20) ** 1.424))) * atan(2.481 * (er / 8) ** 0.946);
  const P10 = 0.242 * (er - 1) ** 0.55;
  const P11 = 0.6366 * (exp(-0.3401 * fn) - 1) * atan(1.263 * (u / 3) ** 1.629);
  const P12 = P9 + (1 - P9) / (1 + 1.183 * u ** 1.376);
  const P13 = (1.695 * P10) / (0.414 + 1.605 * P10);
  const P14 = 0.8928 + 0.1072 * (1 - exp(-0.42 * (fn / 20) ** 3.215));
  const P15 = abs(1 - (0.8928 * (1 + P11) * exp(-P13 * g ** 1.092) * P12) / P14);
  const Fo = P1 * P2 * ((P3 * P4 + 0.1844) * fn * P15) ** 1.5763;
  const eefE = er - (er - p.eps_eff_even) / (1 + Fe);
  const eefO = er - (er - p.eps_eff_odd) / (1 + Fo);
  // Even-mode impedance.
  const ee0 = p.single.eps_eff, eef = kjEpsEff(u, er, ee0, fn);
  const { Z: ZLf, R17: Q0 } = kjZ0(u, er, ee0, eef, p.single.Z0, fn);
  const Q11 = 0.893 * (1 - 0.3 / (1 + 0.7 * (er - 1)));
  const Q12 = ((2.121 * (fn / 20) ** 4.91) / (1 + Q11 * (fn / 20) ** 4.91)) * exp(-2.87 * g) * g ** 0.902;
  const Q13 = 1 + 0.038 * (er / 8) ** 5.1;
  const Q14 = 1 + (1.203 * (er / 15) ** 4) / (1 + (er / 15) ** 4);
  const Q15 = (1.887 * exp(-1.5 * g ** 0.84) * g ** Q14) / (1 + 0.41 * (fn / 15) ** 3 * (u ** (2 / Q13) / (0.125 + u ** (1.626 / Q13))));
  const Q16 = Q15 * (1 + 9 / (1 + 0.403 * (er - 1) ** 2));
  const Q17 = 0.394 * (1 - exp(-1.47 * (u / 7) ** 0.672)) * (1 - exp(-4.25 * (fn / 20) ** 1.87));
  const Q18 = (0.61 * (1 - exp(-2.13 * (u / 8) ** 1.593))) / (1 + 6.544 * g ** 4.17);
  const Q19 = (0.21 * g ** 4) / ((1 + 0.18 * g ** 4.9) * (1 + 0.1 * u * u) * (1 + (fn / 24) ** 3));
  const Q20 = Q19 * (0.09 + 1 / (1 + 0.1 * (er - 1) ** 2.7));
  const Q21 = abs(1 - 42.54 * g ** 0.133 * exp(-0.812 * g) * (u ** 2.5 / (1 + 0.033 * u ** 2.5)));
  const re = (fn / 28.843) ** 12;
  const qe = 0.016 + (0.0514 * er * Q21) ** 4.524;
  const pe = 4.766 * exp(-3.228 * u ** 0.641);
  const de = ((5.086 * qe * re) / (0.3838 + 0.386 * qe)) * (exp(-clampExp(22.2 * u ** 1.92)) / (1 + 1.2992 * re))
    * ((er - 1) ** 6 / (1 + 10 * (er - 1) ** 6));
  const Ce = 1 + 1.275 * (1 - exp(-0.004625 * pe * er ** 1.674 * (fn / 18.365) ** 2.745)) - Q12 + Q16 - Q17 + Q18 + Q20;
  const Zeven = p.Zeven * ((0.9408 * eef ** Ce - 0.9603) / ((0.9408 - de) * ee0 ** Ce - 0.9603)) ** Q0;
  // Odd-mode impedance.
  const Q29 = 15.16 / (1 + 0.196 * (er - 1) ** 2);
  const Q28 = (0.149 * (er - 1) ** 3) / (94.5 + 0.038 * (er - 1) ** 3);
  const Q27 = 0.4 * g ** 0.84 * (1 + (2.5 * (er - 1) ** 1.5) / (5 + (er - 1) ** 1.5));
  const Q26 = 30 - (22.2 * ((er - 1) / 13) ** 12) / (1 + 3 * ((er - 1) / 13) ** 12) - Q29;
  const Q25 = ((0.3 * fn * fn) / (10 + fn * fn)) * (1 + (2.333 * (er - 1) ** 2) / (5 + (er - 1) ** 2));
  const Q24 = ((2.506 * Q28 * u ** 0.894) / (3.575 + u ** 0.894)) * (((1 + 1.3 * u) * fn) / 99.25) ** 4.29;
  const Q23 = 1 + (0.005 * fn * Q27) / ((1 + 0.812 * (fn / 15) ** 1.9) * (1 + 0.025 * u * u));
  const Q22 = (0.925 * (fn / Q26) ** 1.536) / (1 + 0.3 * (fn / 30) ** 1.536);
  const Zodd = ZLf + (p.Zodd * (eefO / p.eps_eff_odd) ** Q22 - ZLf * Q23) / (1 + Q24 + (0.46 * g) ** 2.2 * Q25);
  return { eps_eff_even: eefE, eps_eff_odd: eefO, Zeven, Zodd };
}

// ── one mode: RLGC, γ, Zc ─────────────────────────────────────────────────────────────────────────────────

/**
 * R(f) of a mode: the skin-effect resistance Σ_m Rs_m K_m Γ_m (Wheeler: Γ = (1/μ0) ∂L/∂n per metal), combined
 * with the DC resistance as √(Rdc² + Rac²); and the internal inductance Rac,smooth/ω, held at its value where
 * the skin depth is half the thinnest copper (below that the current is no longer at the surface).
 */
function conductorRL(f, mode) {
  let rac = 0, lint = 0;
  for (const m of mode.metals) {
    const sigma = m.conductivity ?? LOSS_DEFAULTS.conductivity;
    rac += surfaceResistance(f, sigma) * roughnessFactor(f, m.roughness, sigma) * m.gamma;
    const ft = Math.max(f, 4 / (PI * MU0 * sigma * (mode.t * 1e-3) ** 2));
    lint += (surfaceResistance(ft, sigma) * m.gamma) / (2 * PI * ft);
  }
  return { R: sqrt(mode.Rdc * mode.Rdc + rac * rac), Rac: rac, Lint: lint };
}

/** RLGC, γ, Zc of one mode at f; eps = [ε'eff, ε''eff] (ε = ε' - jε''), Zair the air-filled impedance. */
function modeAt(f, mode, Zair, eps) {
  const w = 2 * PI * f;
  const { R, Lint } = conductorRL(f, mode);
  const L = Zair / C_LIGHT + Lint;
  const C = eps[0] / (C_LIGHT * Zair), G = (w * eps[1]) / (C_LIGHT * Zair);
  const Zs = [R, w * L], Yp = [G, w * C];
  const gamma = csqrt(cmul(Zs, Yp)), Zc = csqrt(cdiv(Zs, Yp));
  const Z0 = sqrt(L / C);   // lossless part
  return { R, L, G, C, alpha: gamma[0], beta: gamma[1], Zc, alpha_c: R / (2 * Z0), alpha_d: (G * Z0) / 2, eps_eff: eps[0] };
}

const COLUMNS = ['R', 'L', 'G', 'C', 'alpha', 'alpha_c', 'alpha_d', 'beta', 'eps_eff'];

/** Columns (arrays per frequency) of a mode. */
function modeColumns(rows) {
  const out = Object.fromEntries(COLUMNS.map((k) => [k, rows.map((r) => r[k])]));
  out.Zc = rows.map((r) => r.Zc[0]);
  out.Zc_im = rows.map((r) => r.Zc[1]);
  out.db_per_mm = rows.map((r) => (r.alpha * NP_TO_DB) / 1000);
  out.db_per_inch = rows.map((r) => (r.alpha * NP_TO_DB * INCH) / 1000);
  return out;
}

function checkFrequencies(frequencies) {
  const fs = typeof frequencies === 'number' ? [frequencies] : [...frequencies];
  if (!fs.length) throw new RangeError('frequencies is empty');
  for (const f of fs) if (!(f > 0 && Number.isFinite(f))) throw new RangeError(`frequency must be > 0 Hz (got ${f})`);
  return fs;
}

/**
 * The result of one or two modes over frequency: single `{ frequency, key: 'Z0', Z0, ...mode columns }`; pairs
 * `{ frequency, key: 'Zdiff', Zdiff, Zcommon, db_per_mm (differential), odd: {...}, even: {...} }`.
 */
function assemble(base, fs, modes) {
  if (modes.single) return { ...base, key: 'Z0', frequency: fs, ...modes.single, Z0: modes.single.Zc };
  const { odd, even } = modes;
  return { ...base, key: 'Zdiff', frequency: fs,
    Zdiff: odd.Zc.map((z) => 2 * z), Zcommon: even.Zc.map((z) => z / 2),
    db_per_mm: odd.db_per_mm, db_per_inch: odd.db_per_inch, db_per_mm_common: even.db_per_mm, odd, even };
}

// ── tier 1 ────────────────────────────────────────────────────────────────────────────────────────────────

/**
 * The parameters of a tier-1 model with the conductor surfaces of one metal ('signal': the strips and coplanar
 * grounds; 'ground': the planes) receded by d mm: strips narrower and thinner, gaps wider, and each receding face
 * adds d to its distance from the facing metal.
 */
function recede(model, p, metal, d) {
  const q = { ...p };
  if (metal === 'signal') {
    q.w = p.w - 2 * d; q.t = p.t - 2 * d;
    if (p.s != null) q.s = p.s + 2 * d;
    if (p.gap != null) q.gap = p.gap + 2 * d;
  }
  if (model === 'cpw') return q;   // no plane: h is only the substrate
  if (p.h1 != null) { q.h1 = p.h1 + d; q.h2 = (p.h2 ?? p.h1) + d; } else q.h = p.h + d;
  return q;
}

const TIER1_MODES = {
  microstrip: 'single', coated_microstrip: 'single', stripline: 'single', cpw: 'single', cpwg: 'single',
  coupled_microstrip: 'pair', coupled_stripline: 'pair',
};

/** Air impedances of a tier-1 result per mode ({single} or {odd, even}). */
function airZ(r) {
  return r.Z0 != null ? { single: r.Z0 * sqrt(r.eps_eff) }
    : { odd: r.Zodd * sqrt(r.eps_eff_odd), even: r.Zeven * sqrt(r.eps_eff_even) };
}

/** Wheeler's Γ = (1/μ0) ∂L/∂n = (1/η0) ∂Zair/∂n per metal and mode, 1/m (central difference in mm). */
function wheelerTier1(model, p) {
  const metals = model === 'cpw' ? ['signal'] : ['signal', 'ground'];
  const dims = [p.w, p.t, p.s, p.gap].filter((v) => v != null);
  const d = 1e-4 * Math.min(...dims);
  const out = {};
  for (const m of metals) {
    const a = airZ(calculate(model, recede(model, p, m, d))), b = airZ(calculate(model, recede(model, p, m, -d)));
    for (const k of Object.keys(a)) (out[k] ??= {})[m] = ((a[k] - b[k]) / (2 * d)) * 1e3 / ETA0;
  }
  return out;
}

/** Static values of every mode with εr (and the mask's εr) at their values at f. */
function staticModes(r) {
  return r.Z0 != null ? { single: { Z: r.Z0, ee: r.eps_eff } }
    : { odd: { Z: r.Zodd, ee: r.eps_eff_odd }, even: { Z: r.Zeven, ee: r.eps_eff_even } };
}

/**
 * Loss and frequency dependence of a tier-1 line: RLGC, γ, Zc and insertion loss per frequency.
 * @param {string} model  a tier-1 model id (microstrip, coated_microstrip, stripline, cpw, cpwg, coupled_microstrip,
 *   coupled_stripline)
 * @param {object} params  its parameters (mm; `er`, `erc` are Dk at the dielectric's reference frequency); t > 0
 * @param {number|number[]} frequencies  Hz
 * @param {object} [o]
 * @param {object} [o.dielectric]  {tand, frequency, model: 'djordjevic_sarkar'|'constant', f_low, f_high}
 * @param {object} [o.mask]  the same for the mask (εr `erc`)
 * @param {object} [o.conductor]  {conductivity, roughness} for every metal; `o.signal` / `o.ground` override it
 * @param {boolean} [o.dispersion]  Kirschning-Jansen dispersion for (coupled) microstrip (default true)
 */
export function lineLoss(model, params, frequencies, o = {}) {
  const kind = TIER1_MODES[model];
  if (!kind) throw new RangeError(`no loss model for ${model} (one of ${Object.keys(TIER1_MODES).join(', ')})`);
  const fs = checkFrequencies(frequencies);
  if (!(params.t > 0)) throw new RangeError('conductor loss needs copper thickness t > 0');
  calculate(model, params);   // validates
  const sub = { ...o.dielectric, er: params.er };
  const msk = params.erc != null ? { ...o.mask, er: params.erc } : null;
  const metal = (name) => ({ name, ...o.conductor, ...o[name] });
  const gammas = wheelerTier1(model, params);
  const Rdc = 1 / ((o.signal?.conductivity ?? o.conductor?.conductivity ?? LOSS_DEFAULTS.conductivity) * params.w * params.t * 1e-6);
  const microstripLike = model === 'microstrip' || model === 'coated_microstrip' || model === 'coupled_microstrip';
  const dispersion = (o.dispersion ?? true) && microstripLike;
  const rows = {};
  for (const f of fs) {
    const ds = dielectricAt(f, sub), dm = msk ? dielectricAt(f, msk) : null;
    const at = (er, erc) => staticModes(calculate(model, { ...params, er, ...(dm && { erc }) }));
    const base = at(ds.er, dm?.er);
    // ε''eff = Σ_m (∂εeff/∂ε_m) ε''_m: the loss share of each dielectric, by central differences (one-sided at εr = 1).
    const hs = 1e-6 * ds.er, sLo = Math.max(1, ds.er - hs), up = at(ds.er + hs, dm?.er), dn = at(sLo, dm?.er);
    let mup = null, mdn = null, hm = 0, mLo = 0;
    if (dm) { hm = 1e-6 * dm.er; mLo = Math.max(1, dm.er - hm); mup = at(ds.er, dm.er + hm); mdn = at(ds.er, mLo); }
    let disp = null;
    if (dispersion && kind === 'single') {
      const d = microstripDispersion({ w: params.w, h: params.h, er: ds.er, eps_eff: base.single.ee, Z0: base.single.Z }, f);
      disp = { single: { Z: d.Z0, ee: d.eps_eff } };
    } else if (dispersion) {
      const single = calculate('microstrip', { w: params.w, h: params.h, er: ds.er });
      const c = coupledMicrostripDispersion({ w: params.w, s: params.s, h: params.h, er: ds.er, Zeven: base.even.Z, Zodd: base.odd.Z,
        eps_eff_even: base.even.ee, eps_eff_odd: base.odd.ee, single }, f);
      disp = { even: { Z: c.Zeven, ee: c.eps_eff_even }, odd: { Z: c.Zodd, ee: c.eps_eff_odd } };
    }
    for (const k of Object.keys(base)) {
      let e2 = ((up[k].ee - dn[k].ee) / (ds.er + hs - sLo)) * ds.er * ds.tand;
      if (dm) e2 += ((mup[k].ee - mdn[k].ee) / (dm.er + hm - mLo)) * dm.er * dm.tand;
      let { Z, ee } = base[k];
      if (disp) {
        // Dispersion pulls the field into the substrate: scale the loss share with the filled fraction.
        if (ee > 1) e2 *= (disp[k].ee - 1) / (ee - 1);
        ({ Z, ee } = disp[k]);
      }
      const mode = { Rdc, t: params.t, metals: Object.entries(gammas[k]).map(([n, gamma]) => ({ ...metal(n), gamma })) };
      (rows[k] ??= []).push(modeAt(f, mode, Z * sqrt(ee), [ee, e2]));
    }
  }
  const modes = Object.fromEntries(Object.entries(rows).map(([k, r]) => [k, modeColumns(r)]));
  const method = ['Wheeler incremental inductance', 'Djordjevic-Sarkar'];
  if (dispersion) method.push('Kirschning-Jansen dispersion');
  return assemble({ model, method: method.join(' + '), solver: 'closedform' }, fs, modes);
}

// ── tier 2 ────────────────────────────────────────────────────────────────────────────────────────────────

const cdM = (M) => (M[0][0] + M[1][1] - 2 * M[0][1]) / 4;
const ccM = (M) => M[0][0] + M[1][1] + 2 * M[0][1];

/**
 * Loss and frequency dependence from a field solve: solveCrossSection(section, { loss: true }). The dielectric
 * part of the Maxwell matrix is split per material, so each material's ε*(f)/ε (Djordjevic-Sarkar) rescales its
 * own share (first order in the change of ε, exact for a single material, the first-order perturbation for
 * the loss); conductor loss is Wheeler's rule with the solver's ∂L/∂n per metal.
 * @param {object} result  solveCrossSection(..., { loss: true }) (one or two signals)
 * @param {number|number[]} frequencies  Hz
 * @param {object} [o]
 * @param {object} [o.dielectric]  {frequency, model, f_low, f_high} for every dielectric (its er, tand come from the
 *   section); `o.materials[material]` overrides it per section `material`
 * @param {object} [o.conductor]  {conductivity, roughness} for every metal; `o.metals[metal]` per section `metal`
 */
export function sectionLoss(result, frequencies, o = {}) {
  const ls = result?.loss;
  if (!ls) throw new RangeError('sectionLoss needs a solveCrossSection result with { loss: true }');
  const fs = checkFrequencies(frequencies);
  const n = result.signals.length;
  if (n > 2) throw new RangeError('sectionLoss handles one or two signals');
  if (!(ls.t > 0)) throw new RangeError('conductor loss needs copper with thickness > 0');
  const reduce = n === 1 ? { single: (M) => M[0][0] } : { odd: (M) => 2 * cdM(M), even: (M) => ccM(M) / 2 };
  // Static (extrapolated) impedance and εeff per mode; the frequency dependence from the finest grid's shares.
  const stat = n === 1 ? { single: [result.Z0, result.eps_eff] } : { odd: [result.Zodd, result.eps_eff_odd], even: [result.Zeven, result.eps_eff_even] };
  const mats = ls.materials.map((m) => ({ ...o.dielectric, ...m, ...(m.material != null && o.materials?.[m.material]) }));
  const metals = ls.metals.map((name) => ({ name, ...o.conductor, ...o.metals?.[name] }));
  const sigmaSig = o.metals?.[ls.signalMetal]?.conductivity ?? o.conductor?.conductivity ?? LOSS_DEFAULTS.conductivity;
  const Rdc = 1 / (sigmaSig * (ls.area / n) * 1e-6);
  const modes = {};
  for (const [k, red] of Object.entries(reduce)) {
    const C0 = red(ls.K0), parts = ls.parts.map((P) => (P ? red(P) : 0)), total = parts.reduce((a, b) => a + b, 0);
    const [Z, ee] = stat[k], Zair = Z * sqrt(ee);
    const gamma = metals.map((m, i) => ({ ...m, gamma: (-EPS0 * red(ls.dK0[i]) * 1e3) / (C0 * C0) }));
    const mode = { Rdc, t: ls.t, metals: gamma };
    const rows = fs.map((f) => {
      let re = 0, im = 0;
      parts.forEach((P, i) => {
        if (i === mats.length) { re += P; return; }   // background: lossless, constant
        const d = dielectricAt(f, mats[i]), s = d.er / mats[i].er;
        re += P * s; im += P * s * d.tand;
      });
      return modeAt(f, mode, Zair, [(ee * re) / total, (ee * im) / total]);
    });
    modes[k] = modeColumns(rows);
  }
  return assemble({ model: result.model ?? null, method: 'boarddd field solver + Wheeler incremental inductance + Djordjevic-Sarkar', solver: 'field' }, fs, modes);
}

// ── S-parameters and Touchstone ───────────────────────────────────────────────────────────────────────────

/** S11, S21 of a uniform line (Zc, γ) of length ℓ between ports of impedance z0 (ABCD → S). */
function lineS(Zc, gamma, len, z0) {
  const gl = [gamma[0] * len, gamma[1] * len];
  const A = ccosh(gl), sh = csinh(gl);
  const B = cmul(Zc, sh), C = cdiv(sh, Zc);
  const den = cadd(cadd(cadd(A, A), [B[0] / z0, B[1] / z0]), [C[0] * z0, C[1] * z0]);
  const S11 = cdiv(csub([B[0] / z0, B[1] / z0], [C[0] * z0, C[1] * z0]), den);
  const S21 = cdiv([2, 0], den);
  return [S11, S21];
}

/**
 * S-parameters of a length of line from lineLoss / sectionLoss: per frequency a matrix of [re, im]. A single line
 * is a 2-port (1 near, 2 far); a pair a 4-port with ports 1 → 2 on the first line and 3 → 4 on the second,
 * built from its even and odd modes (exact for a symmetric pair).
 * @param {object} loss  lineLoss / sectionLoss result
 * @param {number} length  mm
 * @param {{z0?: number}} [o]  port impedance (default 50 Ω)
 */
export function sParameters(loss, length, o = {}) {
  const z0 = o.z0 ?? 50, len = length * 1e-3;
  if (!(length > 0)) throw new RangeError(`length must be > 0 mm (got ${length})`);
  const modeS = (m, i) => lineS([m.Zc[i], m.Zc_im[i]], [m.alpha[i], m.beta[i]], len, z0);
  return loss.frequency.map((_, i) => {
    if (loss.key === 'Z0') {
      const [S11, S21] = modeS(loss, i);
      return [[S11, S21], [S21, S11]];
    }
    const [e11, e21] = modeS(loss.even, i), [o11, o21] = modeS(loss.odd, i);
    const h = (a, b, s) => [(a[0] + s * b[0]) / 2, (a[1] + s * b[1]) / 2];
    const r = h(e11, o11, 1), x = h(e11, o11, -1), t = h(e21, o21, 1), c = h(e21, o21, -1);
    // Ports: 1 line A near, 2 line A far, 3 line B near, 4 line B far.
    return [[r, t, x, c], [t, r, c, x], [x, c, r, t], [c, x, t, r]];
  });
}

/**
 * A Touchstone 1.1 file (.s2p for a single line, .s4p for a pair) of a length of line: frequencies in Hz, S in
 * real/imaginary, reference `z0`.
 * @param {object} loss  lineLoss / sectionLoss result
 * @param {number} length  mm
 * @param {{z0?: number, comment?: string}} [o]
 */
export function touchstone(loss, length, o = {}) {
  const z0 = o.z0 ?? 50, S = sParameters(loss, length, o), n = S[0].length;
  const g = (v) => Number(v.toPrecision(10)).toString();
  const lines = [`! boarddd ${loss.solver} ${loss.model ?? 'section'}: ${g(length)} mm, ${n === 2 ? 'single line' : 'pair (ports 1-2 line A, 3-4 line B)'}`];
  if (o.comment) for (const c of String(o.comment).split('\n')) lines.push(`! ${c}`);
  lines.push(`# Hz S RI R ${g(z0)}`);
  loss.frequency.forEach((f, i) => {
    const M = S[i];
    if (n === 2) {
      // Touchstone 1.1 2-port order: S11 S21 S12 S22.
      lines.push([f, ...[M[0][0], M[1][0], M[0][1], M[1][1]].flatMap((z) => [z[0], z[1]])].map(g).join(' '));
    } else {
      // 4-port: one row of the matrix per line, the first prefixed by the frequency.
      M.forEach((row, r) => lines.push([...(r === 0 ? [f] : []), ...row.flatMap((z) => [z[0], z[1]])].map(g).join(' ')));
    }
  });
  return `${lines.join('\n')}\n`;
}

export const _internal = { csqrt, lineS, conductorRL, wheelerTier1, roughnessSpec };
