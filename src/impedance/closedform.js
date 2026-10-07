// boarddd/impedance tier 1: closed-form (quasi-static) PCB transmission-line impedance, written for boarddd
// from the original papers (cited per function; no code from KiCad, Qucs, Transcalc or js_2d_fields). Where a
// published correction was more than ~2 % off a field solver (thickness in CPW and coupled lines, offset
// stripline, solder mask) boarddd uses its own physically-based terms with fitted constants, marked "boarddd"
// and measured in docs/impedance.md.
// python/src/boarddd/impedance/ is the same code in Python; fixtures/impedance/cases.json keeps them in step.
//
// Lengths are in any one unit (boarddd uses mm); only their ratios matter. Every model returns plain numbers
// plus `flags`: the inputs that are outside the range the formula was published or checked for (docs/impedance.md).

const { PI, E, log, sqrt, exp, sin, sinh, cosh, tanh } = Math;

/** Impedance of free space, Ω (CODATA 2018). */
export const ETA0 = 376.730313668;

// boarddd's fitted constants (marked "boarddd" below; docs/impedance.md). fixtures/impedance/fit_corrections.py
// refits them on fixtures/impedance/field-sweep.json (boarddd's own field solver); the Python twin has the same table.
const FIT = {
  mask: { k: 2.66, a: 0.316, p: 0.848, b: 0.606, kappa: 0.076 },
  coplanar: { corner: 0.1, backing: 0.05 },
  coupledMicrostrip: { even: 1, odd: 0.8 },
  coupledStripline: { decay: 2.8 },
  offset: { lo: 0.2, span: 30 },
};

// ── elliptic integrals ──────────────────────────────────────────────────────────────────────────────────────

/** Arithmetic-geometric mean. */
function agm(a, b) {
  for (let i = 0; i < 64 && Math.abs(a - b) > 1e-15 * a; i++) [a, b] = [(a + b) / 2, sqrt(a * b)];
  return (a + b) / 2;
}

/** Complete elliptic integral of the first kind K(k), modulus k (0 <= k < 1), by the AGM: K = π / (2 agm(1, k')). */
export function ellipticK(k) {
  return PI / (2 * agm(1, sqrt((1 - k) * (1 + k))));
}

/**
 * K(k) / K(k'), k' = sqrt(1 - k²). Pass `kp` when k is close to 1 and k' is known more accurately than
 * sqrt(1 - k²) (every caller here can write k' in closed form).
 */
export function ellipticRatio(k, kp = sqrt((1 - k) * (1 + k))) {
  return agm(1, k) / agm(1, kp);
}

// ── validity flags ─────────────────────────────────────────────────────────────────────────────────────────

const fmt = (x) => String(Number(x.toPrecision(4)));

/** A flag when `value` is outside [min, max] (either end may be null). */
function range(flags, code, value, min, max, source) {
  if ((min != null && value < min) || (max != null && value > max)) {
    const span = min == null ? `<= ${fmt(max)}` : max == null ? `>= ${fmt(min)}` : `${fmt(min)}..${fmt(max)}`;
    flags.push({ code, value, min, max, message: `${code} = ${fmt(value)} is outside ${span} (${source})` });
  }
}

function positive(p, names) {
  for (const n of names) {
    if (!(typeof p[n] === 'number' && Number.isFinite(p[n]) && p[n] > 0)) throw new RangeError(`${n} must be a positive number (got ${p[n]})`);
  }
  if (p.t != null && !(Number.isFinite(p.t) && p.t >= 0)) throw new RangeError(`t must be a number >= 0 (got ${p.t})`);
  if (!(p.er >= 1)) throw new RangeError(`er must be >= 1 (got ${p.er})`);
}

// ── microstrip: Hammerstad & Jensen 1980 ───────────────────────────────────────────────────────────────────
// E. Hammerstad, Ø. Jensen, "Accurate models for microstrip computer-aided design", IEEE MTT-S Int. Microwave
// Symp. Digest, 1980, pp. 407-409: eq. (1)-(2) Z01 of the air line, (3)-(6) εe, and the strip-thickness correction
// (widths u1 for the air line and ur for the dielectric line). Published accuracy: Z01 0.01 % for u <= 1 and
// 0.03 % for u <= 1000; εe 0.2 % for 0.01 <= u <= 100 and εr <= 128.

/** Air-filled microstrip impedance for normalised width u = w/h, zero thickness (H-J eq. 1-2). */
function z01(u) {
  const f = 6 + (2 * PI - 6) * exp(-((30.666 / u) ** 0.7528));
  return (ETA0 / (2 * PI)) * log(f / u + sqrt(1 + 4 / (u * u)));
}

/** Zero-thickness effective permittivity (H-J eq. 3-6). */
function epsEff0(u, er) {
  const a = 1 + log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 + log(1 + (u / 18.1) ** 3) / 18.7;
  const b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053;
  return (er + 1) / 2 + ((er - 1) / 2) * (1 + 10 / u) ** (-a * b);
}

/** H-J thickness correction: [Δu1 (air), Δur (dielectric)] for T = t/h. */
function hjThickness(u, T, er) {
  if (T <= 0) return [0, 0];
  const du1 = (T / PI) * log(1 + (4 * E) / (T / tanh(sqrt(6.517 * u)) ** 2));
  return [du1, 0.5 * (1 + 1 / cosh(sqrt(er - 1))) * du1];
}

/** Microstrip Z0 and εeff, thickness included, without flags (shared by the coated and coupled models). */
function microstripCore(u, T, er) {
  const [du1, dur] = hjThickness(u, T, er);
  const u1 = u + du1, ur = u + dur;
  const ee0 = epsEff0(ur, er);
  const Z0 = z01(ur) / sqrt(ee0);
  return { Z0, eps_eff: ee0 * (z01(u1) / z01(ur)) ** 2, Z0air: z01(u1) };
}

function microstripFlags(flags, u, T, er, src = 'Hammerstad-Jensen') {
  range(flags, 'w/h', u, 0.01, 100, src);
  range(flags, 'er', er, 1, 128, src);
  range(flags, 't/h', T, 0, 0.2, src);
}

/**
 * Surface microstrip over one plane (Hammerstad-Jensen 1980, thickness included).
 * @param {{w:number, h:number, t?:number, er:number}} p  trace width, dielectric height, copper thickness, εr
 */
export function microstrip(p) {
  positive(p, ['w', 'h']);
  const u = p.w / p.h, T = (p.t ?? 0) / p.h;
  const { Z0, eps_eff } = microstripCore(u, T, p.er);
  const flags = [];
  microstripFlags(flags, u, T, p.er);
  return { model: 'microstrip', method: 'Hammerstad-Jensen 1980', Z0, eps_eff, flags };
}

// ── coated microstrip (solder mask) ────────────────────────────────────────────────────────────────────────
// A conformal coating of thickness c and permittivity εc over the trace and the laminate only adds dielectric
// where the bare line has air, so Cair (and the H-J air impedance) is unchanged and εeff rises:
//   εeff = εeff,bare + (1 - q) F (εc - 1) / (1 + 0.076 (εc - 1)),   q = (εeff,bare - 1)/(εr - 1)
//   F = 1 - exp(-2.66 u^-0.316 (c/h)^0.848 (1 - 0.606 t/h))
// (1 - q) is the air's share of the bare line's field (Wheeler's filling factor); F is the share of that air
// field inside the coating, and the denominator its partly series (normal-field) character. The form is
// boarddd's: the published covered-microstrip models (Bahl-Stuchly 1980, Svačina 1992, Wan-Hoorfar 2000) are for
// a planar cover, not a conformal mask, and the constants are fitted to 180 quasi-static field solutions with a
// conformal mask (0.3 <= u <= 3, 0.006 <= c/h <= 0.4, t/h <= 0.35, εc 3.3-4): within 0.65 % of Z0 there.

/**
 * Microstrip under a conformal solder-mask coating (boarddd approximation on Hammerstad-Jensen).
 * @param {{w:number, h:number, t?:number, er:number, c:number, erc:number}} p  c: mask thickness (over the
 *   laminate and the trace), erc: mask εr
 */
export function coatedMicrostrip(p) {
  positive(p, ['w', 'h']);
  if (!(p.c >= 0)) throw new RangeError(`c must be a number >= 0 (got ${p.c})`);
  if (!(p.erc >= 1)) throw new RangeError(`erc must be >= 1 (got ${p.erc})`);
  const u = p.w / p.h, T = (p.t ?? 0) / p.h, C = p.c / p.h;
  const bare = microstripCore(u, T, p.er);
  const q = (bare.eps_eff - 1) / (p.er - 1 || 1);
  const m = FIT.mask;
  const F = 1 - exp(-m.k * u ** -m.a * C ** m.p * Math.max(0, 1 - m.b * T));
  const eps_eff = bare.eps_eff + ((1 - q) * F * (p.erc - 1)) / (1 + m.kappa * (p.erc - 1));
  const flags = [];
  microstripFlags(flags, u, T, p.er);
  const src = 'boarddd mask model';
  range(flags, 'w/h', u, 0.25, 4, src);
  range(flags, 'c/h', C, 0, 0.4, src);
  range(flags, 'erc', p.erc, 2.5, 5, src);
  return { model: 'coated_microstrip', method: 'Hammerstad-Jensen 1980 + boarddd mask model', Z0: bare.Z0air / sqrt(eps_eff),
    eps_eff, flags };
}

// ── stripline: Cohn 1954 (exact, t = 0) + Wheeler 1978 thickness ──────────────────────────────────────────
// S. B. Cohn, "Characteristic impedance of the shielded-strip transmission line", IRE Trans. MTT-2, July 1954,
// pp. 52-57: the conformal map of a zero-thickness strip centred between planes b apart is exact,
// Z0 = (η0 / 4√εr) K(k)/K(k'), k = sech(πw / 2b).
// H. A. Wheeler, "Transmission-line properties of a strip line between parallel planes", IEEE Trans. MTT-26,
// Nov. 1978, pp. 866-876, eq. (9)-(11) (also Wadell, "Transmission Line Design Handbook", 1991, §3.5.1): the
// thick strip as an equivalent zero-thickness width. We use it only for the thickness: Z0(t) = Z0_Cohn(0) ·
// Z0_W(t)/Z0_W(0), so the result is exact at t = 0 and continuous in t (Wheeler alone is within 0.5 %).
// Offset strip (h1 ≠ h2). The textbook parallel combination of two symmetric lines (Wadell §3.5.3) is up to 6 %
// high at 4:1, so boarddd moves Cohn's centred strip off centre with two exact limits and blends them:
//   wide strips: parallel plates w/h1 + w/h2 plus the exact fringing of an offset half-plane edge,
//     Cf/ε = (1/π) [(b/a) ln(b/c) + (b/c) ln(b/a)] (a, c: distances of the strip's centre line to the planes),
//     added as capacitance to the centred line;
//   narrow strips: the exact image-series result for a thin conductor between planes,
//     Z_off = Z_centred + (η0 / 2π√εr) ln sin(πa/b);
//   weighted by a smoothstep in log(w / min(a, c)) from 0.2 (narrow) to 6 (wide), a boarddd choice checked
//   against quasi-static field solutions (within 1.5 % up to h_max/h_min = 4).
function cohnStripline0(w, b) {
  const a = (PI * w) / (2 * b);
  return (ETA0 / 4) * ellipticRatio(1 / cosh(a), tanh(a));   // K(k)/K(k'), k = sech(a), k' = tanh(a)
}

function wheelerStripline(w, b, t) {
  const x = t / b, m0 = w / (b - t);
  let m = m0;
  if (x > 0) {
    const n = 2 / (1 + (2 / 3) * (x / (1 - x)));
    m += (x / (PI * (1 - x))) * (1 - 0.5 * log((x / (2 - x)) ** 2 + ((0.0796 * x) / (w / b + 1.1 * x)) ** n));
  }
  const q = 8 / (PI * m);
  return (ETA0 / (4 * PI)) * log(1 + (4 / (PI * m)) * (q + sqrt(q * q + 6.27)));
}

/** Air impedance (εr = 1) of a strip centred between planes b apart. */
function symStriplineAir(w, b, t) {
  const z0 = cohnStripline0(w, b);
  return t > 0 ? (z0 * wheelerStripline(w, b, t)) / wheelerStripline(w, b, 0) : z0;
}

/** The textbook offset approximation: two symmetric lines (plane spacings 2h1+t, 2h2+t) in parallel. */
function parallelAir(z, h1, h2, t) {
  const za = z(2 * h1 + t), zb = z(2 * h2 + t);
  return (2 * za * zb) / (za + zb);
}

/** Total edge fringing capacitance / ε of a zero-thickness half-plane a above one plane and c below the other. */
function offsetFringe(a, c) {
  const b = a + c;
  return ((b / a) * log(b / c) + (b / c) * log(b / a)) / PI;
}

/** Air impedance of a strip with h1 of dielectric to one plane and h2 to the other (offset model above). */
function striplineAir(w, h1, h2, t) {
  const b = h1 + t + h2, zs = symStriplineAir(w, b, t);
  if (h1 === h2) return zs;
  const a = h1 + t / 2, c = h2 + t / 2;
  const zw = ETA0 / (ETA0 / zs + (w / h1 + w / h2 - (4 * w) / (b - t)) + 2 * (offsetFringe(a, c) - offsetFringe(b / 2, b / 2)));
  const zn = zs + (ETA0 / (2 * PI)) * log(sin((PI * a) / b));
  const q = Math.min(1, Math.max(0, log(w / Math.min(a, c) / FIT.offset.lo) / log(FIT.offset.span)));
  const f = q * q * (3 - 2 * q);
  return zn > 0 ? zn * (1 - f) + zw * f : zw;
}

/**
 * Stripline between two planes, symmetric or offset.
 * @param {{w:number, h1:number, h2?:number, t?:number, er:number}} p  h1: dielectric between the trace and one
 *   plane, h2 (default h1): between the trace and the other plane; plane spacing b = h1 + t + h2.
 */
export function stripline(p) {
  const q = { ...p, h2: p.h2 ?? p.h1 };
  positive(q, ['w', 'h1', 'h2']);
  const t = q.t ?? 0, b = q.h1 + t + q.h2;
  const flags = [];
  range(flags, 't/b', t / b, 0, 0.25, 'Wheeler 1978 thickness');
  range(flags, 'w/(b-t)', q.w / (b - t), 0.05, null, 'Wheeler 1978 thickness');
  const off = Math.max(q.h1, q.h2) / Math.min(q.h1, q.h2);
  range(flags, 'h_max/h_min', off, 1, 4, 'offset stripline model');
  if (off > 1) range(flags, 'h_min/t', Math.min(q.h1, q.h2) / t, 2, null, 'offset stripline model');
  const method = (t > 0 ? 'Cohn 1954 + Wheeler 1978 thickness' : 'Cohn 1954 (exact)') + (off > 1 ? ' + boarddd offset' : '');
  return { model: 'stripline', method,
    Z0: striplineAir(q.w, q.h1, q.h2, t) / sqrt(q.er), eps_eff: q.er, flags };
}

// ── coplanar waveguide: Ghione & Naldi ────────────────────────────────────────────────────────────────────
// G. Ghione, C. U. Naldi, "Analytical formulas for coplanar lines in hybrid and monolithic MICs", Electronics
// Letters 20(4), 1984, pp. 179-181 (CPW on a substrate of finite height h), and "Coplanar waveguides for MMIC
// applications: effect of upper shielding, conductor backing, finite-extent ground planes, and line-to-line
// coupling", IEEE Trans. MTT-35(3), 1987, pp. 260-267 (conductor-backed CPW). Conformal maps, exact for t = 0
// and infinite coplanar grounds:
//   CPW:  εeff = 1 + (εr-1)/2 · K(k1)/K(k1') · K(k0')/K(k0),  Z0 = η0/(4√εeff) · K(k0')/K(k0)
//         k0 = w/(w+2g), k1 = sinh(πw/4h) / sinh(π(w+2g)/4h)
//   CPWG: C/2ε0 = K(k0)/K(k0') (air above) + εr K(k3)/K(k3') (substrate to the backing plane),
//         k3 = tanh(πw/4h) / tanh(π(w+2g)/4h)
// Strip thickness. The textbook correction (K. C. Gupta et al., "Microstrip Lines and Slotlines", 2nd ed. 1996,
// §7.3: edges widened by Δ = (1.25t/π)(1 + ln(4πw/t))) is up to ±8 % off a field solver for 1 oz copper in
// 0.1-0.4 mm gaps, because Δ approaches the gap. boarddd instead adds the thickness as air capacitance above the
// t = 0 map (per 2ε0): the gaps' sidewalls as parallel plates, t/g, plus corner and backing terms fitted to
// quasi-static field solutions (CPWG, 0.25 <= h/g <= 10, t/g <= 0.7):
//   ΔC = t/g + 0.1 √(t/w) [+ 0.05 √(t/h) g/h with a backing plane].

/** k = tanh(a)/tanh(b) (b > a > 0) and its exact complement k' = sqrt(1-k²). */
function tanhRatio(a, b) {
  const k = tanh(a) / tanh(b);
  const one = sinh(b - a) / (cosh(a) * sinh(b));    // 1 - k
  return [k, sqrt(one * (1 + k))];
}

function coplanar(p, grounded) {
  positive(p, ['w', 'gap', 'h']);
  const { w, gap: g, h, er } = p, t = p.t ?? 0;
  const k0 = w / (w + 2 * g), k0p = (2 * sqrt(g * (w + g))) / (w + 2 * g);
  const r0 = ellipticRatio(k0, k0p);                     // K(k0)/K(k0')
  const dt = t > 0 ? t / g + FIT.coplanar.corner * sqrt(t / w) + (grounded ? FIT.coplanar.backing * sqrt(t / h) * (g / h) : 0) : 0;
  const flags = [];
  const src = grounded ? 'Ghione-Naldi 1987' : 'Ghione-Naldi 1984';
  range(flags, 't/gap', t / g, 0, 0.7, 'boarddd coplanar thickness');
  let Cair, C;                                           // per-unit-length capacitances / (2 ε0)
  if (grounded) {
    const [k3, k3p] = tanhRatio((PI * w) / (4 * h), (PI * (w + 2 * g)) / (4 * h));
    const r3 = ellipticRatio(k3, k3p);
    Cair = r0 + dt + r3;
    C = r0 + dt + er * r3;
    // Ghione-Naldi splits the field at the coplanar plane; with the backing plane closer than about one gap the
    // line is mostly a microstrip and the split is 2-6 % high (docs/impedance.md).
    range(flags, 'h/gap', h / g, 1, null, src);
  } else {
    const a = (PI * w) / (4 * h), b = (PI * (w + 2 * g)) / (4 * h);
    const k1 = sinh(a) / sinh(b);
    const r1 = ellipticRatio(k1);
    // Air above and below (2 r0), plus the dielectric's share of the lower half-plane.
    Cair = 2 * r0 + dt;
    C = 2 * r0 + dt + (er - 1) * r1;
  }
  const eps_eff = C / Cair;
  return { model: grounded ? 'cpwg' : 'cpw', method: `${src}${t > 0 ? ' + boarddd thickness' : ''}`,
    Z0: ETA0 / (2 * sqrt(C * Cair)), eps_eff, flags };
}

/**
 * Coplanar waveguide on a substrate of height h with no plane under it (Ghione-Naldi 1984).
 * @param {{w:number, gap:number, h:number, t?:number, er:number}} p  signal width, gap to each coplanar ground
 */
export const cpw = (p) => coplanar(p, false);

/**
 * Grounded (conductor-backed) coplanar waveguide (Ghione-Naldi 1987): infinite coplanar grounds stitched to
 * the plane h below.
 * @param {{w:number, gap:number, h:number, t?:number, er:number}} p
 */
export const cpwg = (p) => coplanar(p, true);

// ── edge-coupled microstrip: Kirschning & Jansen 1984 ─────────────────────────────────────────────────────
// M. Kirschning, R. H. Jansen, "Accurate wide-range design equations for the frequency-dependent
// characteristic of parallel coupled microstrip lines", IEEE Trans. MTT-32(1), Jan. 1984, pp. 83-90, with the
// corrections in MTT-33(3), Mar. 1985, p. 288: static even/odd εeff (eq. 3-4) and impedances (eq. 8-9) from
// the single line of Hammerstad-Jensen; stated accuracy 0.6 % (εeff 0.7 %) for 0.1 <= u <= 10, 0.1 <= g <= 10,
// 1 <= εr <= 18 (u = w/h, g = s/h), for zero thickness.
// Thickness. Jansen's mode-width corrections (R. H. Jansen, IEEE Trans. MTT-26(2), 1978) are up to 28 % off a
// field solver for the odd mode of 1 oz pairs with s ~ t, so boarddd adds thickness as capacitance on top of the
// t = 0 modes, as for coupled stripline below: ΔCs (C and Cair) is the single line's H-J thickness increase,
// shared by its two edges; even: ΔCe = ΔCs (1 - ψe/2), odd: ΔCo = ΔCs + ψo 2t/s (air, a parallel plate across
// the gap). ψe = (1 + g) exp(-g) and ψo = (1 + g) exp(-0.8g) are boarddd's fit to quasi-static field solutions
// (0.2 <= g <= 3, t/h <= 0.35).

function kjEven(u, g, er) {
  const v = (u * (20 + g * g)) / (10 + g * g) + g * exp(-g);
  return epsEff0(v, er);   // K-J eq. 3: H-J εe at the even-mode width v
}

function kjOdd(u, g, er, ee0) {
  const ao = 0.7287 * (ee0 - (er + 1) / 2) * (1 - exp(-0.179 * u));
  const bo = (0.747 * er) / (0.15 + er);
  const co = bo - (bo - 0.207) * exp(-0.414 * u);
  const d0 = 0.593 + 0.694 * exp(-0.562 * u);
  return ((er + 1) / 2 + ao - ee0) * exp(-co * g ** d0) + ee0;
}

/** K-J static Zoe, Zoo for one normalised width (zero thickness at that width). */
function kjModes(u, g, er) {
  const ee0 = epsEff0(u, er);
  const zl = z01(u) / sqrt(ee0);
  const q1 = 0.8695 * u ** 0.194;
  const q2 = 1 + 0.7519 * g + 0.189 * g ** 2.31;
  const q3 = 0.1975 + (16.6 + (8.4 / g) ** 6) ** -0.387 + log(g ** 10 / (1 + (g / 3.4) ** 10)) / 241;
  const q4 = ((2 * q1) / q2) / (exp(-g) * u ** q3 + (2 - exp(-g)) * u ** -q3);
  const q5 = 1.794 + 1.14 * log(1 + 0.638 / (g + 0.517 * g ** 2.43));
  const q6 = 0.2305 + log(g ** 10 / (1 + (g / 5.8) ** 10)) / 281.3 + log(1 + 0.598 * g ** 1.154) / 5.1;
  const q7 = (10 + 190 * g * g) / (1 + 82.3 * g ** 3);
  const q8 = exp(-6.5 - 0.95 * log(g) - (g / 0.15) ** 5);
  const q9 = log(q7) * (q8 + 1 / 16.5);
  const q10 = q4 - (q5 / q2) * exp((q6 * log(u)) / u ** q9);
  const eeE = kjEven(u, g, er), eeO = kjOdd(u, g, er, ee0);
  const k = (zl / ETA0) * sqrt(ee0);
  return {
    Zeven: (zl * sqrt(ee0 / eeE)) / (1 - k * q4), eps_eff_even: eeE,
    Zodd: (zl * sqrt(ee0 / eeO)) / (1 - k * q10), eps_eff_odd: eeO,
  };
}

function coupledResult(model, method, Zeven, Zodd, eps_eff_even, eps_eff_odd, flags) {
  return { model, method, Zdiff: 2 * Zodd, Zcommon: Zeven / 2, Zodd, Zeven, eps_eff_odd, eps_eff_even, flags };
}

/**
 * Edge-coupled microstrip pair (Kirschning-Jansen 1984, boarddd thickness).
 * @param {{w:number, s:number, h:number, t?:number, er:number}} p  each trace's width, edge-to-edge spacing
 */
export function coupledMicrostrip(p) {
  positive(p, ['w', 's', 'h']);
  const u = p.w / p.h, g = p.s / p.h, T = (p.t ?? 0) / p.h, er = p.er;
  let { Zeven, Zodd, eps_eff_even, eps_eff_odd } = kjModes(u, g, er);
  if (T > 0) {
    // Thickness as capacitance (per ε0; C = √εeff η0/Z, Cair = η0/(Z √εeff)) on top of the t = 0 modes.
    const caps = (Z, ee) => [(ETA0 * sqrt(ee)) / Z, ETA0 / (Z * sqrt(ee))];
    const [c0, a0] = caps(z01(u) / sqrt(epsEff0(u, er)), epsEff0(u, er));
    const st = microstripCore(u, T, er);
    const [ct, at] = caps(st.Z0, st.eps_eff);
    const pe = (1 + g) * exp(-FIT.coupledMicrostrip.even * g), po = (1 + g) * exp(-FIT.coupledMicrostrip.odd * g), plate = (2 * T) / g;
    const mode = (Z, ee, dC, dA) => {
      const [c, a] = caps(Z, ee);
      return [ETA0 / sqrt((c + dC) * (a + dA)), (c + dC) / (a + dA)];
    };
    [Zeven, eps_eff_even] = mode(Zeven, eps_eff_even, (ct - c0) * (1 - pe / 2), (at - a0) * (1 - pe / 2));
    [Zodd, eps_eff_odd] = mode(Zodd, eps_eff_odd, ct - c0 + po * plate, at - a0 + po * plate);
  }
  const flags = [];
  const src = 'Kirschning-Jansen 1984';
  range(flags, 'w/h', u, 0.1, 10, src);
  range(flags, 's/h', g, 0.1, 10, src);
  range(flags, 'er', er, 1, 18, src);
  range(flags, 't/h', T, 0, 0.35, 'boarddd coupled thickness');
  return coupledResult('coupled_microstrip', `${src}${T > 0 ? ' + boarddd thickness' : ''}`,
    Zeven, Zodd, eps_eff_even, eps_eff_odd, flags);
}

// ── edge-coupled stripline: Cohn 1955 ──────────────────────────────────────────────────────────────────────
// S. B. Cohn, "Shielded coupled-strip transmission line", IRE Trans. MTT-3(5), Oct. 1955, pp. 29-38. Exact for
// zero-thickness strips centred between planes b apart:
//   Zoe,o = (η0 / 4√εr) K(k')/K(k),  ke = tanh(πw/2b) tanh(π(w+s)/2b),  ko = tanh(πw/2b) coth(π(w+s)/2b).
// Thickness. Cohn's thin-strip corrections (same paper; Wadell §4.6.2) are 2-6 % off a field solver for 1 oz
// copper on thin cores (docs/impedance.md), so boarddd adds thickness the way it does for the single strip, as
// capacitance (C/ε0 = η0/Z_air) on top of Cohn's exact t = 0 modes:
//   ΔCs   thickness increase of one strip alone (Cohn + Wheeler above),
//   e     its share per edge: (ΔCs - ΔCpp)/2, ΔCpp = 4w/(b-t) - 4w/b the parallel-plate part,
//   ψ     the part of the inner edge that couples to the other strip, (1 + 2x) exp(-2.8x), x = s/(b-t),
//   even: ΔCe = ΔCs - ψ e        (the inner sidewall faces a magnetic wall),
//   odd:  ΔCo = ΔCs + ψ 2t/s     (the inner sidewalls form a parallel plate across the gap, electric wall at s/2).
// ψ is boarddd's own fit to quasi-static field solutions (0.2 <= x <= 1.1, t/b <= 0.12); it gives the exact
// t = 0 limit and ψ -> 0 (uncoupled) for wide gaps.
// Offset pairs (h1 ≠ h2): each mode is the parallel combination of two symmetric pairs, scaled by the ratio of
// the offset single strip (above) to the same parallel combination for one strip.

/** Air Zoe, Zoo of a pair centred between planes b apart. */
function symCoupledAir(w, s, b, t) {
  const a1 = (PI * w) / (2 * b), a2 = (PI * (w + s)) / (2 * b);
  // ke = tanh(a1) tanh(a2) and 1 - ke = cosh(a2 - a1) / (cosh a1 cosh a2), exact for ke near 1.
  const ke = tanh(a1) * tanh(a2), kep = sqrt((cosh(a2 - a1) / (cosh(a1) * cosh(a2))) * (1 + ke));
  const [ko, kop] = tanhRatio(a1, a2);
  const zoe0 = (ETA0 / 4) / ellipticRatio(ke, kep), zoo0 = (ETA0 / 4) / ellipticRatio(ko, kop);
  if (t <= 0) return [zoe0, zoo0];
  const dCs = ETA0 / symStriplineAir(w, b, t) - ETA0 / cohnStripline0(w, b);
  const e = (dCs - ((4 * w) / (b - t) - (4 * w) / b)) / 2;
  const x = s / (b - t), psi = (1 + 2 * x) * exp(-FIT.coupledStripline.decay * x);
  return [ETA0 / (ETA0 / zoe0 + dCs - psi * e), ETA0 / (ETA0 / zoo0 + dCs + (psi * 2 * t) / s)];
}

/**
 * Edge-coupled stripline pair (Cohn 1955), symmetric or offset.
 * @param {{w:number, s:number, h1:number, h2?:number, t?:number, er:number}} p
 */
export function coupledStripline(p) {
  const q = { ...p, h2: p.h2 ?? p.h1 };
  positive(q, ['w', 's', 'h1', 'h2']);
  const t = q.t ?? 0, b = q.h1 + t + q.h2;
  let ze, zo;
  if (q.h1 === q.h2) [ze, zo] = symCoupledAir(q.w, q.s, b, t);
  else {
    // Each mode as two symmetric pairs in parallel, scaled by how far that combination is off for one strip.
    const k = striplineAir(q.w, q.h1, q.h2, t) / parallelAir((bb) => symStriplineAir(q.w, bb, t), q.h1, q.h2, t);
    ze = k * parallelAir((bb) => symCoupledAir(q.w, q.s, bb, t)[0], q.h1, q.h2, t);
    zo = k * parallelAir((bb) => symCoupledAir(q.w, q.s, bb, t)[1], q.h1, q.h2, t);
  }
  const n = sqrt(q.er);
  const flags = [];
  range(flags, 't/b', t / b, 0, 0.12, 'boarddd coupled thickness');
  range(flags, 's/(b-t)', q.s / (b - t), 0.15, null, 'boarddd coupled thickness');
  range(flags, 'h_max/h_min', Math.max(q.h1, q.h2) / Math.min(q.h1, q.h2), 1, 3, 'offset coupled stripline model');
  if (q.h1 !== q.h2) range(flags, 'h_min/t', Math.min(q.h1, q.h2) / t, 2, null, 'offset coupled stripline model');
  const method = (t > 0 ? 'Cohn 1955 + boarddd thickness' : 'Cohn 1955 (exact)') + (q.h1 !== q.h2 ? ' + boarddd offset' : '');
  return coupledResult('coupled_stripline', method,
    ze / n, zo / n, q.er, q.er, flags);
}

// ── IPC-2141 (comparison only) ─────────────────────────────────────────────────────────────────────────────
// IPC-2141A (2004) / IPC-D-317A rules of thumb, as fabs and old calculators quote them. Results carry
// `comparison: true`: they are up to 30 % off (docs/impedance.md) and are never a design value.

function ipc(model, key, value, flags) {
  return { model, method: 'IPC-2141 (comparison only)', comparison: true, [key]: value, flags };
}

/** IPC-2141 surface microstrip (comparison only). @param {{w:number, h:number, t?:number, er:number}} p */
export function ipc2141Microstrip(p) {
  positive(p, ['w', 'h']);
  const { w, h, er } = p, t = p.t ?? 0, flags = [];
  range(flags, 'w/h', w / h, 0.1, 2, 'IPC-2141');
  range(flags, 'er', er, 1, 15, 'IPC-2141');
  return ipc('ipc2141_microstrip', 'Z0', (87 / sqrt(er + 1.41)) * log((5.98 * h) / (0.8 * w + t)), flags);
}

/** IPC-2141 symmetric stripline (comparison only; b = h1 + t + h2). @param {{w:number, h1:number, h2?:number, t?:number, er:number}} p */
export function ipc2141Stripline(p) {
  const q = { ...p, h2: p.h2 ?? p.h1 };
  positive(q, ['w', 'h1', 'h2']);
  const t = q.t ?? 0, b = q.h1 + t + q.h2, flags = [];
  range(flags, 'w/(b-t)', q.w / (b - t), null, 0.35, 'IPC-2141');
  range(flags, 't/b', t / b, null, 0.25, 'IPC-2141');
  return ipc('ipc2141_stripline', 'Z0', (60 / sqrt(q.er)) * log((1.9 * b) / (0.8 * q.w + t)), flags);
}

/** IPC-2141 edge-coupled microstrip, Zdiff (comparison only). @param {{w:number, s:number, h:number, t?:number, er:number}} p */
export function ipc2141CoupledMicrostrip(p) {
  positive(p, ['s']);
  const r = ipc2141Microstrip(p);
  return ipc('ipc2141_coupled_microstrip', 'Zdiff', 2 * r.Z0 * (1 - 0.48 * exp((-0.96 * p.s) / p.h)), r.flags);
}

/** IPC-2141 edge-coupled stripline, Zdiff (comparison only). @param {{w:number, s:number, h1:number, h2?:number, t?:number, er:number}} p */
export function ipc2141CoupledStripline(p) {
  positive(p, ['s']);
  const r = ipc2141Stripline(p);
  const b = p.h1 + (p.t ?? 0) + (p.h2 ?? p.h1);
  return ipc('ipc2141_coupled_stripline', 'Zdiff', 2 * r.Z0 * (1 - 0.347 * exp((-2.9 * p.s) / b)), r.flags);
}

// ── registry and synthesis ─────────────────────────────────────────────────────────────────────────────────

/** Every model by its id (the `model` field of its result; also the Python function names). */
export const MODELS = {
  microstrip,
  coated_microstrip: coatedMicrostrip,
  stripline,
  cpw,
  cpwg,
  coupled_microstrip: coupledMicrostrip,
  coupled_stripline: coupledStripline,
  ipc2141_microstrip: ipc2141Microstrip,
  ipc2141_stripline: ipc2141Stripline,
  ipc2141_coupled_microstrip: ipc2141CoupledMicrostrip,
  ipc2141_coupled_stripline: ipc2141CoupledStripline,
};

/** Run a model by id. */
export function calculate(model, params) {
  const fn = MODELS[model];
  if (!fn) throw new RangeError(`unknown model ${model} (one of ${Object.keys(MODELS).join(', ')})`);
  return fn(params);
}

/**
 * Solve for one dimension (default the width `w`) that gives a target impedance, by bisection in log space.
 * Impedance is monotonic in w (and in s, h, gap) for every model, so the bracket [lo, hi] (default 1e-3..1e2
 * times the dielectric height) holds at most one solution.
 * @param {string} model  a MODELS id
 * @param {object} params  the model's parameters; the one being solved for is ignored
 * @param {number} target  impedance, Ω
 * @param {{vary?: string, key?: string, lo?: number, hi?: number, tol?: number}} [opts]  key: the result field
 *   to match (default Zdiff for coupled models, else Z0); tol: relative tolerance on the dimension
 * @returns {{value: number, params: object, result: object, iterations: number}}
 */
export function synthesize(model, params, target, opts = {}) {
  const vary = opts.vary ?? 'w';
  const run = (x) => calculate(model, { ...params, [vary]: x });
  const key = opts.key ?? ('Zdiff' in run(params[vary] ?? 1) ? 'Zdiff' : 'Z0');
  const scale = params.h ?? params.h1 + (params.h2 ?? params.h1) + (params.t ?? 0);
  let lo = opts.lo ?? 1e-3 * scale, hi = opts.hi ?? 1e2 * scale;
  const tol = opts.tol ?? 1e-9;
  const f = (x) => run(x)[key] - target;
  const flo = f(lo), fhi = f(hi);
  if (!(Math.sign(flo) !== Math.sign(fhi) || flo === 0 || fhi === 0)) {
    throw new RangeError(`${key} = ${fmt(target)} is not reachable for ${vary} in [${fmt(lo)}, ${fmt(hi)}] (${fmt(flo + target)}..${fmt(fhi + target)} Ω)`);
  }
  let i = 0;
  for (; i < 200 && hi / lo - 1 > tol; i++) {
    const mid = sqrt(lo * hi);
    if (Math.sign(f(mid)) === Math.sign(flo)) lo = mid;
    else hi = mid;
  }
  const value = sqrt(lo * hi);
  return { value, params: { ...params, [vary]: value }, result: run(value), iterations: i };
}
