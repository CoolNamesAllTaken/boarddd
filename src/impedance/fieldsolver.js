// boarddd/impedance tier 2: a 2D quasi-static field solver for PCB transmission-line cross-sections, written
// for boarddd (MIT) from textbook methods; python/src/boarddd/impedance/fieldsolver.py is the same algorithm
// (numpy/scipy) and fixtures/impedance/field-cases.json keeps the two in step. See docs/impedance.md.
//
// Method. Laplace's equation ∇·(ε∇φ) = 0 on a graded rectilinear grid, discretised by finite volumes (the
// five-point stencil with cell-wise εr; the same matrix as bilinear finite elements with lumped coupling),
// conductors held at fixed potentials and zero normal flux on the far boundary. The stored energy
// W = ½ ε0 φᵀAφ of unit excitations gives the Maxwell capacitance matrix K, once with the dielectrics (C) and
// once in vacuum (C0); then L = μ0 ε0 C0⁻¹, εeff = C/C0 and Z = 1/(c √(C C0)) per mode
// (e.g. C. R. Paul, "Analysis of Multiconductor Transmission Lines", 2nd ed. 2008, ch. 5).
// - Grid: every rectangle edge is a grid line; cells grow geometrically away from conductor edges (where the
//   field is singular) and dielectric interfaces, out to a far boundary 25 times the structure's size away.
// - Symmetry: a cross-section that is its own mirror image solves half the domain, with a magnetic wall
//   (even modes, and single lines) or an electric wall (odd mode) on the mirror line.
// - Linear algebra: a sparse Cholesky factorisation in nested-dissection order (A. George, "Nested dissection
//   of a regular finite element mesh", SIAM J. Numer. Anal. 10(2), 1973; up-looking factorisation over the
//   elimination tree as in T. A. Davis, "Direct Methods for Sparse Linear Systems", SIAM 2006, ch. 4).
// - Error estimate: the same section on two grids, the second with every cell size divided by √2; the change
//   between them, extrapolated (Richardson, order 2 in the cell size), is the reported error and the
//   extrapolated value the result. Further levels are added until the estimate is below `tol`.
//
// Units. Rectangles in any one length unit (boarddd uses mm, y up); C and C0 come out in F/m and L in H/m
// whatever the unit, since 2D capacitance per unit length is scale-free. Plain ES module with no DOM or Node
// APIs: it runs in a Worker (fieldsolver-worker.js) and on the main thread alike.

const C_LIGHT = 299792458;
const EPS0 = 8.8541878128e-12;
const MU0 = 1 / (EPS0 * C_LIGHT * C_LIGHT);

/** Mesh constants (level 0): cell size at conductor edges as a fraction of the smallest conductor feature, its
 * floor as a fraction of the structure, the cap at dielectric interfaces, and the growth ratio. */
export const MESH = { edgeFraction: 0.01, floorFraction: 1e-3, interfaceFraction: 0.125, growth: 1.4, margin: 50 };

/** Measured error ratio between grid levels (0.51-0.57 on the exact Cohn cases and microstrip; docs). */
const RATIO = 0.55;

const finite = (v) => typeof v === 'number' && Number.isFinite(v);

// ── section → normalised geometry ──────────────────────────────────────────────────────────────────────────

/**
 * Check a section and work out its nets, its extent and the computational domain.
 * A rectangle is {x0, x1, y0, y1}; a missing x0 or x1 extends it to the domain edge (planes, layers, coplanar
 * grounds); y0 and y1 are required.
 */
function normalise(section) {
  if (!section || typeof section !== 'object') throw new TypeError('section must be an object');
  const ground = new Set(section.ground ?? ['gnd']);
  const background = section.background ?? 1;
  if (!(background >= 1)) throw new RangeError(`background must be >= 1 (got ${background})`);
  const rect = (r, what, i) => {
    for (const k of ['y0', 'y1']) if (!finite(r[k])) throw new RangeError(`${what}[${i}].${k} must be a finite number`);
    for (const k of ['x0', 'x1']) if (r[k] != null && !finite(r[k])) throw new RangeError(`${what}[${i}].${k} must be a finite number or absent`);
    if (r.y1 < r.y0 || (r.x0 != null && r.x1 != null && r.x1 < r.x0)) throw new RangeError(`${what}[${i}] has negative size`);
    return { x0: r.x0 ?? -Infinity, x1: r.x1 ?? Infinity, y0: r.y0, y1: r.y1 };
  };
  const conductors = (section.conductors ?? []).map((c, i) => {
    if (c.net == null) throw new RangeError(`conductors[${i}] has no net`);
    return { ...rect(c, 'conductors', i), net: String(c.net) };
  });
  const dielectrics = (section.dielectrics ?? []).map((d, i) => {
    if (!(d.er >= 1)) throw new RangeError(`dielectrics[${i}].er must be >= 1 (got ${d.er})`);
    return { ...rect(d, 'dielectrics', i), er: d.er };
  });
  const signals = [];
  for (const c of conductors) if (!ground.has(c.net) && !signals.includes(c.net)) signals.push(c.net);
  if (!signals.length) throw new RangeError('section has no signal conductor (every net is ground)');
  if (!conductors.some((c) => ground.has(c.net))) throw new RangeError(`section has no ground conductor (nets ${[...ground].join(', ')})`);
  for (const c of conductors) {
    if (!ground.has(c.net) && !(finite(c.x0) && finite(c.x1))) throw new RangeError(`signal ${c.net} must be bounded in x`);
  }

  // Extent of the structure: every finite coordinate.
  let xa = Infinity, xb = -Infinity, ya = Infinity, yb = -Infinity;
  for (const r of [...conductors, ...dielectrics]) {
    for (const x of [r.x0, r.x1]) if (finite(x)) { xa = Math.min(xa, x); xb = Math.max(xb, x); }
    ya = Math.min(ya, r.y0); yb = Math.max(yb, r.y1);
  }
  const sig = conductors.filter((c) => !ground.has(c.net));
  const sy0 = Math.min(...sig.map((c) => c.y0)), sy1 = Math.max(...sig.map((c) => c.y1));
  // A full-width ground below (above) every signal shields what lies beyond it: the domain stops at its face.
  let shieldBelow = -Infinity, shieldAbove = Infinity;
  for (const c of conductors) {
    if (!ground.has(c.net) || c.x0 !== -Infinity || c.x1 !== Infinity) continue;
    if (c.y1 <= sy0) shieldBelow = Math.max(shieldBelow, c.y1);
    if (c.y0 >= sy1) shieldAbove = Math.min(shieldAbove, c.y0);
  }
  const lo = shieldBelow === -Infinity ? ya : shieldBelow, hi = shieldAbove === Infinity ? yb : shieldAbove;
  const size = Math.max(xb - xa, hi - lo);
  const M = MESH.margin * size;
  const domain = {
    x0: xa - M, x1: xb + M,
    y0: shieldBelow === -Infinity ? ya - M : shieldBelow,
    y1: shieldAbove === Infinity ? yb + M : shieldAbove,
  };
  const clip = (r) => ({ ...r, x0: Math.max(r.x0, domain.x0), x1: Math.min(r.x1, domain.x1),
    y0: Math.max(r.y0, domain.y0), y1: Math.min(r.y1, domain.y1) });
  const keep = (r) => r.x0 <= r.x1 && r.y0 <= r.y1;
  return {
    ground, background, signals, size, domain, extent: { x0: xa, x1: xb },
    conductors: conductors.map(clip).filter(keep),
    dielectrics: dielectrics.map(clip).filter(keep),
  };
}

// ── mirror symmetry ────────────────────────────────────────────────────────────────────────────────────────

/** The net map under x → 2m - x when the conductors are mirror-symmetric ('same' or 'swap'), else null. */
function mirrorMap(g, m) {
  const tol = 1e-9 * g.size;
  const eq = (a, b) => (a === b) || Math.abs(a - b) <= tol;
  const mirror = (r) => ({ ...r, x0: 2 * m - r.x1, x1: 2 * m - r.x0 });
  const same = (a, b) => eq(a.x0, b.x0) && eq(a.x1, b.x1) && eq(a.y0, b.y0) && eq(a.y1, b.y1);
  const matches = (list, key) => list.every((r) => {
    const q = mirror(r);
    return list.some((s) => same(q, s) && key(r, s));
  });
  if (!matches(g.dielectrics, (a, b) => a.er === b.er)) return null;
  const isG = (n) => g.ground.has(n);
  const netOK = (map) => (a, b) => (isG(a.net) ? isG(b.net) : map(a.net) === b.net);
  if (matches(g.conductors, netOK((n) => n))) return 'same';
  if (g.signals.length === 2) {
    const [p, q] = g.signals;
    if (matches(g.conductors, netOK((n) => (n === p ? q : p)))) return 'swap';
  }
  return null;
}

// ── graded grid ────────────────────────────────────────────────────────────────────────────────────────────

/**
 * Grid lines from lo to hi through every breakpoint {x, h}: near a breakpoint cells are h, and they grow
 * linearly with distance (geometrically from cell to cell) by `growth`. Only + - * / and comparisons, so the
 * Python twin produces the same numbers.
 */
function axis(breaks, lo, hi, growth) {
  const pts = breaks.filter((b) => b.x >= lo && b.x <= hi).sort((a, b) => a.x - b.x);
  const xs = [], hs = [];
  for (const b of pts) {
    if (xs.length && b.x - xs[xs.length - 1] <= 0) { hs[hs.length - 1] = Math.min(hs[hs.length - 1], b.h); continue; }
    xs.push(b.x); hs.push(b.h);
  }
  const k = growth - 1;
  const sizeAt = (x) => {
    let h = Infinity;
    for (let i = 0; i < xs.length; i++) {
      const v = hs[i] + k * (x > xs[i] ? x - xs[i] : xs[i] - x);
      if (v < h) h = v;
    }
    return h;
  };
  const out = [xs[0]];
  for (let s = 0; s + 1 < xs.length; s++) {
    const a = xs[s], b = xs[s + 1];
    const seg = [a];
    let x = a, h = sizeAt(a);
    while (x + h < b) {
      if (!(x + h > x)) throw new Error('field solver: grid cell size underflow');
      x += h; seg.push(x); h = sizeAt(x);
    }
    // The last cell would end at x + h >= b: end there and squeeze, or drop x and stretch, whichever is closer.
    const L = b - a;
    let end = x + h;
    if (seg.length > 1 && (x - a) * (end - a) > L * L) { seg.pop(); end = x; }
    const c = L / (end - a);
    for (let i = 1; i < seg.length; i++) out.push(a + (seg[i] - a) * c);
    out.push(b);
  }
  return Float64Array.from(out);
}

/** Breakpoints along one axis: conductor edges at `hc`, dielectric interfaces at most `hd`, both at most half
 * the distance to the next breakpoint. */
function breakpoints(g, dir, hc, hd, lo, hi) {
  const [a, b] = dir === 'x' ? ['x0', 'x1'] : ['y0', 'y1'];
  const raw = [{ x: lo, h: Infinity }, { x: hi, h: Infinity }];
  for (const c of g.conductors) {
    // Faces of full-width planes are smooth; copper with an edge has singular corners.
    const corner = dir === 'x' || finite(c.x0) || finite(c.x1);
    for (const v of [c[a], c[b]]) if (finite(v)) raw.push({ x: v, h: corner ? hc : hd });
  }
  for (const d of g.dielectrics) for (const v of [d[a], d[b]]) if (finite(v)) raw.push({ x: v, h: hd });
  // Coordinates closer than 1e-9 of the structure are one grid line (float noise from mask and etch offsets).
  const tol = 1e-9 * g.size, sorted = [...new Set(raw.map((r) => r.x))].sort((p, q) => p - q), snap = new Map();
  for (const v of sorted) {
    const prev = snap.size ? sorted.find((u) => snap.get(u) === u && v - u <= tol) : undefined;
    snap.set(v, prev ?? v);
  }
  for (const r of raw) r.x = snap.get(r.x);
  const xs = [...new Set(raw.map((r) => r.x))].sort((p, q) => p - q);
  return raw.map((r) => {
    const i = xs.indexOf(r.x);
    let gap = Infinity;
    if (i > 0) gap = Math.min(gap, r.x - xs[i - 1]);
    if (i + 1 < xs.length) gap = Math.min(gap, xs[i + 1] - r.x);
    return { x: r.x, h: Math.min(r.h, gap / 2) };
  });
}

/** Smallest positive distance between conductor faces along either axis (sets the cell size at edges). */
function conductorScale(g) {
  let d = Infinity;
  for (const [a, b] of [['x0', 'x1'], ['y0', 'y1']]) {
    const v = [...new Set(g.conductors.flatMap((c) => [c[a], c[b]]).filter(finite))].sort((p, q) => p - q);
    for (let i = 1; i < v.length; i++) if (v[i] - v[i - 1] < d) d = v[i] - v[i - 1];
  }
  return Math.max(Math.min(d, g.size), MESH.floorFraction * g.size);
}

/** The grid for refinement level `level` (cell sizes and growth excess divided by √2 per level). */
function makeGrid(g, level, mirrorAt) {
  let f = 1;   // √2^-level by repeated division, so that Python computes the same number
  for (let i = 0; i < level; i++) f /= Math.SQRT2;
  for (let i = 0; i > level; i--) f *= Math.SQRT2;
  const hc = MESH.edgeFraction * conductorScale(g) * f;
  const hd = MESH.interfaceFraction * g.size * f;
  const growth = 1 + (MESH.growth - 1) * f;
  const D = g.domain;
  // With mirror symmetry the grid covers the half from the mirror line (i = 0) to the right edge.
  const x0 = mirrorAt ?? D.x0;
  const X = axis(breakpoints(g, 'x', hc, hd, x0, D.x1), x0, D.x1, growth);
  const Y = axis(breakpoints(g, 'y', hc, hd, D.y0, D.y1), D.y0, D.y1, growth);
  return { X, Y, hc, hd, growth };
}

// ── discretisation ─────────────────────────────────────────────────────────────────────────────────────────

const inRect = (r, x, y, e = 0) => x >= r.x0 - e && x <= r.x1 + e && y >= r.y0 - e && y <= r.y1 + e;

/** Cell permittivities (with or without dielectrics) and the conductor index at each node (-1: free). */
function paint(g, X, Y) {
  const nx = X.length, ny = Y.length;
  const er = new Float64Array((nx - 1) * (ny - 1)).fill(g.background);
  for (let j = 0; j < ny - 1; j++) {
    const cy = 0.5 * (Y[j] + Y[j + 1]);
    for (let i = 0; i < nx - 1; i++) {
      const cx = 0.5 * (X[i] + X[i + 1]);
      for (const d of g.dielectrics) if (inRect(d, cx, cy)) er[j * (nx - 1) + i] = d.er;
    }
  }
  const owner = new Int32Array(nx * ny).fill(-1);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    for (let c = 0; c < g.conductors.length; c++) if (inRect(g.conductors[c], X[i], Y[j], 1e-9 * g.size)) owner[j * nx + i] = c;
  }
  return { er, owner };
}

/**
 * Edge conductances (per ε0) of the five-point stencil: gx[k] couples node k to k+1, gy[k] to k+nx; each is the
 * permittivity-weighted half-cell heights (widths) on either side over the edge length.
 */
function conductances(X, Y, eps) {
  const nx = X.length, ny = Y.length, N = nx * ny;
  const gx = new Float64Array(N), gy = new Float64Array(N);
  const e = (i, j) => (i < 0 || j < 0 || i >= nx - 1 || j >= ny - 1 ? 0 : eps[j * (nx - 1) + i]);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const k = j * nx + i;
    if (i < nx - 1) {
      const below = j > 0 ? Y[j] - Y[j - 1] : 0, above = j < ny - 1 ? Y[j + 1] - Y[j] : 0;
      gx[k] = (0.5 * (e(i, j - 1) * below + e(i, j) * above)) / (X[i + 1] - X[i]);
    }
    if (j < ny - 1) {
      const left = i > 0 ? X[i] - X[i - 1] : 0, right = i < nx - 1 ? X[i + 1] - X[i] : 0;
      gy[k] = (0.5 * (e(i - 1, j) * left + e(i, j) * right)) / (Y[j + 1] - Y[j]);
    }
  }
  return { gx, gy };
}

/** φᵀAφ for the stencil (twice the energy per ε0). With two vectors, φᵀAψ. */
function quad(nx, ny, gx, gy, p, q = p) {
  let s = 0;
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const k = j * nx + i;
    if (i < nx - 1) s += gx[k] * (p[k] - p[k + 1]) * (q[k] - q[k + 1]);
    if (j < ny - 1) s += gy[k] * (p[k] - p[k + nx]) * (q[k] - q[k + nx]);
  }
  return s;
}

// ── sparse Cholesky in nested-dissection order ─────────────────────────────────────────────────────────────

/** Nested-dissection order of the free nodes of an nx × ny grid: halves first, then the separating line. */
function dissect(nx, ny, free) {
  const order = [];
  const rec = (i0, i1, j0, j1) => {
    const w = i1 - i0, h = j1 - j0;
    if (w <= 0 || h <= 0) return;
    if (w * h <= 64 || w <= 2 || h <= 2) {
      for (let j = j0; j < j1; j++) for (let i = i0; i < i1; i++) if (free[j * nx + i]) order.push(j * nx + i);
      return;
    }
    if (w >= h) {
      const m = (i0 + i1) >> 1;
      rec(i0, m, j0, j1); rec(m + 1, i1, j0, j1);
      for (let j = j0; j < j1; j++) if (free[j * nx + m]) order.push(j * nx + m);
    } else {
      const m = (j0 + j1) >> 1;
      rec(i0, i1, j0, m); rec(i0, i1, m + 1, j1);
      for (let i = i0; i < i1; i++) if (free[m * nx + i]) order.push(m * nx + i);
    }
  };
  rec(0, nx, 0, ny);
  return order;
}

/**
 * Symbolic analysis for the stencil on the free nodes of a grid: the nested-dissection order, the upper
 * triangle's pattern by columns in that order (diagonal last), the elimination tree and the column counts of L.
 * Shared by every factorisation on the same grid and Dirichlet set.
 */
function analyse(nx, ny, fixed) {
  const N = nx * ny, free = new Uint8Array(N);
  for (let k = 0; k < N; k++) free[k] = fixed[k] ? 0 : 1;
  const order = dissect(nx, ny, free);
  const n = order.length, perm = new Int32Array(N).fill(-1);
  for (let q = 0; q < n; q++) perm[order[q]] = q;
  // Column c: the neighbours of node order[c] ordered before it, then the diagonal. Entry kinds say which
  // conductance each one is (0: gx[k-1], 1: gx[k], 2: gy[k-nx], 3: gy[k]); fixed neighbours go to the rhs.
  const Ap = new Int32Array(n + 1), Ai = new Int32Array(5 * n), kind = new Int8Array(5 * n);
  let p = 0;
  for (let c = 0; c < n; c++) {
    const k = order[c], i = k % nx;
    Ap[c] = p;
    const add = (m, kd) => { if (perm[m] >= 0 && perm[m] < c) { Ai[p] = perm[m]; kind[p++] = kd; } };
    if (i > 0) add(k - 1, 0);
    if (i < nx - 1) add(k + 1, 1);
    if (k >= nx) add(k - nx, 2);
    if (k + nx < N) add(k + nx, 3);
    Ai[p] = c; kind[p++] = -1;
  }
  Ap[n] = p;
  const parent = new Int32Array(n), anc = new Int32Array(n), flag = new Int32Array(n).fill(-1);
  for (let k = 0; k < n; k++) {
    parent[k] = -1; anc[k] = -1;
    for (let q = Ap[k]; q < Ap[k + 1]; q++) {
      let i = Ai[q];
      while (i !== -1 && i < k) { const next = anc[i]; anc[i] = k; if (next === -1) parent[i] = k; i = next; }
    }
  }
  const Lp = new Int32Array(n + 1), count = new Int32Array(n).fill(1);
  for (let k = 0; k < n; k++) {
    flag[k] = k;
    for (let q = Ap[k]; q < Ap[k + 1]; q++) for (let i = Ai[q]; flag[i] !== k; i = parent[i]) { flag[i] = k; count[i]++; }
  }
  for (let k = 0; k < n; k++) Lp[k + 1] = Lp[k] + count[k];
  // The pattern of every row of L in topological order (Rp, Rj), and the rows of every column (Li).
  const nnz = Lp[n], Rp = new Int32Array(n + 1), Rj = new Int32Array(nnz - n), Li = new Int32Array(nnz);
  const next = Int32Array.from(Lp.subarray(0, n)), stack = new Int32Array(n);
  flag.fill(-1);
  let r = 0;
  for (let k = 0; k < n; k++) {
    Rp[k] = r;
    let top = n;
    flag[k] = k;
    for (let q = Ap[k]; q < Ap[k + 1]; q++) {
      let i = Ai[q], len = 0;
      for (; flag[i] !== k; i = parent[i]) { stack[len++] = i; flag[i] = k; }
      while (len > 0) count[--top] = stack[--len];
    }
    for (let q = top; q < n; q++) { const j = count[q]; Rj[r++] = j; Li[++next[j]] = k; }
    Li[Lp[k]] = k;
  }
  Rp[n] = r;
  return { nx, ny, N, n, order, perm, Ap, Ai, kind, Lp, Li, Rp, Rj };
}

/**
 * Numeric Cholesky factor L (A = LLᵀ) of the stencil with conductances gx, gy. Up-looking: row k of L is a
 * sparse triangular solve over the precomputed row pattern (the reach of column k in the elimination tree).
 */
function factor(S, gx, gy) {
  const { nx, N, n, order, Ap, Ai, kind, Lp, Li, Rp, Rj } = S;
  const Lx = new Float64Array(Lp[n]), next = Int32Array.from(Lp.subarray(0, n)), x = new Float64Array(n);
  for (let k = 0; k < n; k++) {
    const node = order[k], i0 = node % nx;
    // Diagonal: every conductance at the node, to free and fixed neighbours alike.
    let d = (i0 > 0 ? gx[node - 1] : 0) + (i0 < nx - 1 ? gx[node] : 0) + (node >= nx ? gy[node - nx] : 0) + (node + nx < N ? gy[node] : 0);
    for (let q = Ap[k]; q < Ap[k + 1] - 1; q++) {
      const kd = kind[q];
      x[Ai[q]] = -(kd === 0 ? gx[node - 1] : kd === 1 ? gx[node] : kd === 2 ? gy[node - nx] : gy[node]);
    }
    for (let q = Rp[k]; q < Rp[k + 1]; q++) {
      const j = Rj[q], lkj = x[j] / Lx[Lp[j]];
      x[j] = 0;
      for (let p = Lp[j] + 1, e = next[j] + 1; p < e; p++) x[Li[p]] -= Lx[p] * lkj;
      d -= lkj * lkj;
      Lx[++next[j]] = lkj;
    }
    if (!(d > 0)) throw new Error('field solver: matrix is not positive definite');
    Lx[Lp[k]] = Math.sqrt(d);
  }
  return { n, Lp, Li, Lx, nnz: Lp[n] };
}

/** Solve LLᵀx = b in place. */
function cholSolve({ n, Lp, Li, Lx }, b) {
  for (let j = 0; j < n; j++) {
    const v = (b[j] /= Lx[Lp[j]]);
    for (let p = Lp[j] + 1; p < Lp[j + 1]; p++) b[Li[p]] -= Lx[p] * v;
  }
  for (let j = n - 1; j >= 0; j--) {
    let v = b[j];
    for (let p = Lp[j] + 1; p < Lp[j + 1]; p++) v -= Lx[p] * b[Li[p]];
    b[j] = v / Lx[Lp[j]];
  }
  return b;
}

/**
 * Solve the stencil for several excitations sharing one matrix: each excitation is a full vector holding the
 * Dirichlet nodes' values (S's fixed set); returns the full potentials.
 */
function solveGrid(S, gx, gy, excitations) {
  const { nx, N, n, order, perm } = S;
  const L = factor(S, gx, gy);
  const phi = excitations.map((v) => {
    const b = new Float64Array(n);
    for (let c = 0; c < n; c++) {
      const k = order[c], i = k % nx;
      let r = 0;
      if (i > 0 && perm[k - 1] < 0) r += gx[k - 1] * v[k - 1];
      if (i < nx - 1 && perm[k + 1] < 0) r += gx[k] * v[k + 1];
      if (k >= nx && perm[k - nx] < 0) r += gy[k - nx] * v[k - nx];
      if (k + nx < N && perm[k + nx] < 0) r += gy[k] * v[k + nx];
      b[c] = r;
    }
    cholSolve(L, b);
    const out = Float64Array.from(v);
    for (let c = 0; c < n; c++) out[order[c]] = b[c];
    return out;
  });
  return { phi, nnz: L.nnz, unknowns: n };
}

// ── capacitance matrices on one grid ───────────────────────────────────────────────────────────────────────

/** Maxwell capacitance matrices K (dielectrics) and K0 (vacuum), in F/m, on the grid of `level`. */
function capacitances(g, level, sym, m) {
  const { X, Y, hc, growth } = makeGrid(g, level, sym ? m : null);
  const nx = X.length, ny = Y.length, N = nx * ny;
  const { er, owner } = paint(g, X, Y);
  const nS = g.signals.length;
  const unit = (net) => {
    const v = new Float64Array(N);
    for (let k = 0; k < N; k++) if (owner[k] >= 0 && g.conductors[owner[k]].net === net) v[k] = 1;
    return v;
  };
  const fixed = Uint8Array.from(owner, (o) => (o >= 0 ? 1 : 0));
  const stats = { nx, ny, nodes: N, unknowns: 0, hc, growth };
  // A homogeneous dielectric scales the vacuum solution: K = εr K0.
  const uniform = er.every((v) => v === er[0]) ? er[0] : null;
  const media = uniform ? [new Float64Array(er.length).fill(1)] : [er, new Float64Array(er.length).fill(1)];
  const solve = (S, eps, ex) => {
    const { gx, gy } = conductances(X, Y, eps);
    const r = solveGrid(S, gx, gy, ex);
    stats.unknowns += r.unknowns;
    return { gx, gy, phi: r.phi };
  };
  const mats = media.map(() => Array.from({ length: nS }, () => new Array(nS).fill(0)));
  if (sym === 'swap') {
    // Even (magnetic wall) and odd (electric wall, φ = 0 on the mirror line i = 0) modes of the pair; the half
    // domain holds one trace of each net pair, so the excitation is 1 on both nets.
    const v = unit(g.signals[0]), vb = unit(g.signals[1]);
    for (let k = 0; k < N; k++) v[k] += vb[k];
    const oddFixed = Uint8Array.from(fixed);
    for (let j = 0; j < ny; j++) oddFixed[j * nx] = 1;
    const vOdd = Float64Array.from(v, (x, k) => (k % nx === 0 ? 0 : x));
    for (const [S, ex, sign] of [[analyse(nx, ny, fixed), v, 1], [analyse(nx, ny, oddFixed), vOdd, -1]]) {
      media.forEach((eps, e) => {
        const { gx, gy, phi } = solve(S, eps, [ex]);
        const c = EPS0 * quad(nx, ny, gx, gy, phi[0]);   // ε0 φᵀAφ over the half = the mode's C per line
        mats[e][0][0] += c / 2; mats[e][1][1] += c / 2; mats[e][0][1] += sign * c / 2; mats[e][1][0] += sign * c / 2;
      });
    }
  } else {
    const S = analyse(nx, ny, fixed), ex = g.signals.map(unit), w = sym === 'same' ? 2 : 1;
    media.forEach((eps, e) => {
      const { gx, gy, phi } = solve(S, eps, ex);
      for (let a = 0; a < nS; a++) for (let b = a; b < nS; b++) {
        mats[e][a][b] = mats[e][b][a] = w * EPS0 * quad(nx, ny, gx, gy, phi[a], phi[b]);
      }
    });
  }
  const K0 = mats[mats.length - 1], K = uniform ? K0.map((r) => r.map((v) => uniform * v)) : mats[0];
  return { K, K0, stats };
}

// ── line parameters from the matrices ──────────────────────────────────────────────────────────────────────

function inverse(M) {
  const n = M.length, A = M.map((r, i) => [...r, ...M.map((_, j) => (i === j ? 1 : 0))]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    [A[c], A[p]] = [A[p], A[c]];
    const d = A[c][c];
    for (let k = 0; k < 2 * n; k++) A[c][k] /= d;
    for (let r = 0; r < n; r++) if (r !== c) {
      const f = A[r][c];
      for (let k = 0; k < 2 * n; k++) A[r][k] -= f * A[c][k];
    }
  }
  return A.map((r) => r.slice(n));
}

/** Impedances from K, K0: Z0 for one signal; differential/common and odd/even modes for two. */
function lineParams(K, K0) {
  const n = K.length;
  const Linv = inverse(K0).map((r) => r.map((v) => MU0 * EPS0 * v));
  const out = {};
  if (n === 1) {
    const C = K[0][0], C0 = K0[0][0];
    Object.assign(out, { Z0: 1 / (C_LIGHT * Math.sqrt(C * C0)), eps_eff: C / C0, C, L: Linv[0][0], C0 });
  } else if (n === 2) {
    // V = ±½ Vd on the two lines and I = ±Id (differential), V = Vc and I = Ic/2 each (common); for a
    // symmetric pair these are exactly the odd and even modes, Zdiff = 2 Zodd and Zcommon = Zeven/2.
    const cd = (M) => (M[0][0] + M[1][1] - 2 * M[0][1]) / 4, cc = (M) => M[0][0] + M[1][1] + 2 * M[0][1];
    const Cd = cd(K), Cd0 = cd(K0), Cc = cc(K), Cc0 = cc(K0);
    const Zdiff = 1 / (C_LIGHT * Math.sqrt(Cd * Cd0)), Zcommon = 1 / (C_LIGHT * Math.sqrt(Cc * Cc0));
    Object.assign(out, { Zdiff, Zcommon, Zodd: Zdiff / 2, Zeven: 2 * Zcommon, eps_eff_odd: Cd / Cd0, eps_eff_even: Cc / Cc0 });
  }
  return { ...out, matrices: { C: K, C0: K0, L: Linv } };
}

const KEYS = ['Z0', 'eps_eff', 'C', 'L', 'C0', 'Zdiff', 'Zcommon', 'Zodd', 'Zeven', 'eps_eff_odd', 'eps_eff_even'];

/**
 * Solve a cross-section.
 * @param {object} section  { conductors: [{x0?, x1?, y0, y1, net}], dielectrics: [{x0?, x1?, y0, y1, er}],
 *   ground?: string[] (default ['gnd']), background?: number (εr outside every dielectric, default 1) }.
 *   Rectangles in any one length unit; a missing x0/x1 extends to the domain edge; later dielectrics win.
 * @param {{tol?: number, level?: number, maxLevel?: number, symmetry?: boolean}} [opts]  tol: target relative
 *   error, as estimated by the last refinement (default 0.01); level: the first grid level (default 0); maxLevel (default 4); symmetry: use mirror
 *   symmetry when the section has it (default true)
 */
export function solveCrossSection(section, opts = {}) {
  const t0 = typeof performance !== 'undefined' ? performance.now() : Date.now();
  const g = normalise(section);
  const tol = opts.tol ?? 0.01, first = opts.level ?? 0, maxLevel = Math.max(first + 1, opts.maxLevel ?? 4);
  const m = 0.5 * (g.extent.x0 + g.extent.x1);
  const sym = opts.symmetry === false ? null : mirrorMap(g, m);
  const levels = [];
  let est = null, error = null, worst = 0;
  for (let level = first; level <= maxLevel; level++) {
    const c = capacitances(g, level, sym, m);
    levels.push({ level, ...lineParams(c.K, c.K0), grid: c.stats });
    if (levels.length < 2) continue;
    const [a, b] = levels.slice(-2);
    // Richardson: each level shrinks the error by RATIO, so the fine value is off by (b - a) RATIO/(1 - RATIO).
    est = {}; error = {};
    for (const k of KEYS) if (k in b) {
      const corr = ((b[k] - a[k]) * RATIO) / (1 - RATIO);
      est[k] = b[k] + corr; error[k] = Math.abs(corr / est[k]);
    }
    // Converged when the impedances are (with more than two signals: every self capacitance).
    const watch = ['Z0', 'Zdiff', 'Zcommon'].filter((k) => k in error).map((k) => error[k]);
    if (!watch.length) {
      for (const M of ['C', 'C0']) b.matrices[M].forEach((row, i) => watch.push(Math.abs((row[i] - a.matrices[M][i][i]) * RATIO / (1 - RATIO) / row[i])));
    }
    worst = Math.max(...watch);
    if (worst <= tol) break;
  }
  const fine = levels[levels.length - 1];
  const t1 = typeof performance !== 'undefined' ? performance.now() : Date.now();
  const r = { solver: 'field', method: 'boarddd 2D quasi-static field solver (finite volumes)', signals: g.signals };
  for (const k of KEYS) if (k in est) r[k] = est[k];
  // C, L and C0 of the matrices are the finest grid's (not extrapolated).
  r.matrices = fine.matrices;
  r.error = error;
  r.error_pct = 100 * Math.max(worst, ...Object.values(error));
  r.symmetry = sym ?? 'none';
  r.levels = levels.map((l) => ({ level: l.level, ...Object.fromEntries(KEYS.filter((k) => k in l).map((k) => [k, l[k]])), grid: l.grid }));
  r.ms = t1 - t0;
  return r;
}

// ── standard structures ────────────────────────────────────────────────────────────────────────────────────

const req = (p, keys) => {
  for (const k of keys) if (!(finite(p[k]) && p[k] > 0)) throw new RangeError(`${k} must be a positive number (got ${p[k]})`);
  if (p.t != null && !(p.t >= 0)) throw new RangeError(`t must be >= 0 (got ${p.t})`);
  if (!(p.er >= 1)) throw new RangeError(`er must be >= 1 (got ${p.er})`);
};

/** Traces (one, or a pair s apart) centred on x = 0 with bottom at y and thickness t, as boxes with their net. */
const traces = (w, s, y, t) => (s == null
  ? [{ x0: -w / 2, x1: w / 2, y0: y, y1: y + t, net: 'sig' }]
  : [{ x0: -s / 2 - w, x1: -s / 2, y0: y, y1: y + t, net: 'p' }, { x0: s / 2, x1: s / 2 + w, y0: y, y1: y + t, net: 'n' }]);

/**
 * The copper rectangles of a trace box: the box itself, or with `etch` > 0 a trapezoid whose top is `etch`
 * narrower than its base (y0), as four steps at the trapezoid's width at each step's mid-height.
 */
export function etched(box, etch = 0) {
  if (!(etch > 0) || !(box.y1 > box.y0)) return [box];
  const out = [];
  for (let k = 0; k < 4; k++) {
    const inset = (etch / 2) * ((k + 0.5) / 4), h = (box.y1 - box.y0) / 4;
    out.push({ ...box, x0: box.x0 + inset, x1: box.x1 - inset, y0: box.y0 + k * h, y1: k === 3 ? box.y1 : box.y0 + (k + 1) * h });
  }
  return out;
}

/**
 * A conformal solder mask (εr er) on copper boxes standing on the laminate at y: `c` thick over the laminate,
 * `ct` over the copper's top and sides (default c) and `cs` in the gap of a pair (default c; `pairGap` the gap's
 * x range). Polar's C1, C2 and C3.
 */
export function mask(boxes, y, { c, ct = c, cs = c, er, pairGap = null }) {
  if (!(c > 0 || ct > 0)) return [];
  const out = [];
  if (c > 0) out.push({ y0: y, y1: y + c, er });
  if (pairGap && cs !== c && cs > 0) out.push({ x0: pairGap[0], x1: pairGap[1], y0: y, y1: y + cs, er });
  if (ct > 0) for (const r of boxes) out.push({ ...(r.x0 != null && { x0: r.x0 - ct }), ...(r.x1 != null && { x1: r.x1 + ct }), y0: y, y1: r.y1 + ct, er });
  return out;
}

/** The mask of a model's parameters c, ct, cs, erc. */
const maskOf = (p, boxes, y) => mask(boxes, y, { c: p.c ?? 0, ct: p.ct ?? p.c ?? 0, cs: p.cs ?? p.c ?? 0, er: p.erc ?? 1,
  pairGap: p.s != null ? [-p.s / 2, p.s / 2] : null });

/**
 * The cross-section of a tier-1 model's parameters (same names and units), for the field solver. Every model
 * also takes `etch` (a trapezoid trace whose top is `etch` narrower than its base `w`); outer structures a
 * conformal solder mask `c`, `erc` (as coated_microstrip; `ct` over the copper and `cs` in a pair's gap default
 * to c); coplanar ones a via fence `fence` (distance from the gap's outer edge to a wall stitching the coplanar
 * grounds to the plane; cpwg only) and `gnd` (coplanar ground width; default infinite).
 */
export function sectionFor(model, p) {
  const t = p.t ?? 0;
  switch (model) {
    case 'microstrip': case 'coated_microstrip': case 'coupled_microstrip': {
      req(p, model === 'coupled_microstrip' ? ['w', 'h', 's'] : ['w', 'h']);
      const tr = traces(p.w, model === 'coupled_microstrip' ? p.s : null, p.h, t);
      return { conductors: [{ y0: -Math.max(t, p.h / 20), y1: 0, net: 'gnd' }, ...tr.flatMap((b) => etched(b, p.etch))],
        dielectrics: [{ y0: 0, y1: p.h, er: p.er }, ...maskOf(p, tr, p.h)] };
    }
    case 'stripline': case 'coupled_stripline': {
      const h2 = p.h2 ?? p.h1;
      req({ ...p, h2 }, model === 'coupled_stripline' ? ['w', 'h1', 'h2', 's'] : ['w', 'h1', 'h2']);
      const b = p.h1 + t + h2, tp = Math.max(t, b / 20);
      return { conductors: [{ y0: -tp, y1: 0, net: 'gnd' }, { y0: b, y1: b + tp, net: 'gnd' },
        ...traces(p.w, model === 'coupled_stripline' ? p.s : null, p.h1, t).flatMap((r) => etched(r, p.etch))],
      dielectrics: [{ y0: 0, y1: b, er: p.er }] };
    }
    case 'cpw': case 'cpwg': case 'coupled_cpw': case 'coupled_cpwg': {
      const pair = model.startsWith('coupled'), grounded = model.endsWith('cpwg');
      req(p, pair ? ['w', 'gap', 'h', 's'] : ['w', 'gap', 'h']);
      const tr = traces(p.w, pair ? p.s : null, p.h, t);
      const e = (pair ? p.s / 2 + p.w : p.w / 2) + p.gap, tg = Math.max(t, p.h / 20);
      const gw = p.gnd ?? null;
      const gnds = [{ ...(gw != null && { x0: -(e + gw) }), x1: -e, y0: p.h, y1: p.h + t, net: 'gnd' },
        { x0: e, ...(gw != null && { x1: e + gw }), y0: p.h, y1: p.h + t, net: 'gnd' }];
      const conductors = [...tr.flatMap((b) => etched(b, p.etch)), ...gnds];
      if (grounded) {
        conductors.push({ y0: -tg, y1: 0, net: 'gnd' });
        if (p.fence != null) {
          conductors.push({ x1: -(e + p.fence), y0: 0, y1: p.h + t, net: 'gnd' }, { x0: e + p.fence, y0: 0, y1: p.h + t, net: 'gnd' });
        }
      }
      return { conductors, dielectrics: [{ y0: 0, y1: p.h, er: p.er }, ...maskOf(p, [...tr, ...gnds], p.h)] };
    }
    default: throw new RangeError(`the field solver has no builder for model ${model}`);
  }
}

/**
 * A tier-1 model solved by the field solver: solveCrossSection's result with `model` and `flags: []`, so it has
 * the tier-1 fields (`Z0, eps_eff` or `Zdiff, Zcommon, Zodd, Zeven, eps_eff_odd, eps_eff_even`).
 * Also takes 'coupled_cpw' and 'coupled_cpwg' (a pair between coplanar grounds), which tier 1 lacks.
 */
export function fieldCalculate(model, params, opts = {}) {
  return { model, ...solveCrossSection(sectionFor(model, params), opts), flags: [] };
}

export const _internal = { normalise, mirrorMap, axis, makeGrid, analyse, factor, cholSolve, dissect, capacitances };
