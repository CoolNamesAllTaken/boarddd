# Impedance (`boarddd/impedance`, `boarddd.impedance`)

Characteristic impedance of PCB transmission lines, the same code in JS (`src/impedance/`) and Python
(`python/src/boarddd/impedance/`), in two tiers:

- **Tier 1: closed-form, quasi-static** formulas. They are instant, so they suit live hover, width synthesis
  and bulk checks (kipr).
- **Tier 2: a 2D quasi-static field solver** (`solveCrossSection`, [below](#tier-2-the-field-solver)) for any
  cross-section built from rectangles: solder mask, etch, finite and coplanar grounds, plane voids, layered
  dielectrics, neighbouring traces. About 50–150 ms per typical line in the browser. It is boarddd's own (MIT); its
  sweep (`fixtures/impedance/field-sweep.json`) is now the reference that tier 1's fitted constants come from.

```js
import { microstrip, coupledStripline, synthesize } from 'boarddd/impedance';

microstrip({ w: 0.36, h: 0.2104, t: 0.035, er: 4.4 });
// { model: 'microstrip', method: 'Hammerstad-Jensen 1980', Z0: 50.92, eps_eff: 3.176, flags: [] }
coupledStripline({ w: 0.1, s: 0.15, h1: 0.2, h2: 0.3, t: 0.018, er: 4.1 }).Zdiff;
synthesize('microstrip', { h: 0.2104, t: 0.035, er: 4.4 }, 50).value;          // 0.3721 mm
synthesize('coupled_microstrip', { w: 0.15, h: 0.2104, t: 0.035, er: 4.4 }, 90, { vary: 's' }).value;
```

```python
from boarddd.impedance import microstrip, coupled_stripline, synthesize, calculate

microstrip(w=0.36, h=0.2104, t=0.035, er=4.4).Z0                   # 50.92
calculate("cpwg", {"w": 0.3, "gap": 0.15, "h": 0.3, "t": 0.035, "er": 4.5}).to_dict()
synthesize("stripline", {"h1": 0.2, "h2": 0.3, "t": 0.018, "er": 4.1}, 50).value
```

## From the board model

`lineFromStackup` / `line_from_stackup` turn a signal layer of a `boarddd/board@1` stackup into a model and its
parameters, and `evaluateTarget` / `evaluate_target` check a net class's `ImpedanceTarget` (see docs/model.md):

```js
import { lineFromStackup, evaluateTarget, calculate } from 'boarddd/impedance';
const line = lineFromStackup(board.stackup, 'F.Cu', { width: 0.15 });
// { model: 'coated_microstrip', structure: 'microstrip', params: { w, t, h, er, c, erc }, warnings: ['F.Mask: no epsilon_r, using 3.3'] }
calculate(line.model, line.params).Z0;
for (const nc of board.net_classes) if (nc.impedance) evaluateTarget(board.stackup, nc.impedance);
// [{ layer, model, key: 'Z0' | 'Zdiff', value, target, deviation_pct, ok, width, synthesized, result, warnings }]
```

| | |
|---|---|
| Structure | `ImpedanceTarget.structure` → model: `microstrip` → `microstrip` (`coated_microstrip` when the layer is under a mask), `coupled_microstrip`; `stripline` → `stripline`, `coupled_stripline`; `coplanar` → `cpw`; `coplanar_grounded` → `cpwg`. Differential coplanar has no tier-1 model (`modelFor` returns null; `lineFromStackup` throws). With no structure: microstrip on an outer layer, stripline on an inner one |
| Heights | the dielectric layers between the trace and its reference copper: the nearest copper on each side, or `ImpedanceLayer.ref_top` / `ref_bottom` (planes skipped in between count as resin-filled voids) |
| εr | the series value of the layers on each side, as the model does for sublayers. A stripline with different εr above and below gets εr weighted by each side's plane capacitance, (ε₁/h₁ + ε₂/h₂)/(1/h₁ + 1/h₂) |
| Mask | outer microstrip (single and coupled) and CPWG include the mask (`thickness_over_copper`, else `thickness`; `epsilon_r`) as `c`, `erc`. CPW warns that tier 1 ignores it |
| Missing data | copper thickness 0.035 mm, dielectric εr 4.5, mask 0.01 mm and εr 3.3 (`STACKUP_DEFAULTS`, KiCad's defaults), each with a warning. A dielectric with no thickness throws |
| Targets | `key` is `Zdiff` for differential targets and `Z0` otherwise; `ok` compares `deviation_pct` with `tolerance_pct` (null without one); a layer with no `width` gets the width synthesized for the target (`synthesized: true`) |

## Conventions

| | |
|---|---|
| Units | lengths in any one unit (boarddd uses mm; only ratios matter), impedance in Ω |
| `t` | copper thickness, default 0. `er`: the dielectric's εr (one value; for a core/prepreg mix use a height-weighted εr until tier 2) |
| Result | `{ model, method, Z0, eps_eff, flags }`; coupled models: `{ Zdiff, Zcommon, Zodd, Zeven, eps_eff_odd, eps_eff_even, … }` with Zdiff = 2 Zodd and Zcommon = Zeven / 2. Python returns frozen dataclasses with the same field names (`to_dict()` gives the JS object) |
| `flags` | `[{ code, value, min, max, message }]`: inputs outside the range where the formula was published or checked (below). Empty means "inside the validity range". The numbers are still returned |
| Errors | non-positive dimensions, `t < 0` or `er < 1` throw `RangeError` (JS) / `ValueError` (Python) |
| Quasi-static | no dispersion, no loss (both in a later phase); fine for impedance control up to a few GHz |

## Models

| id (JS function) | structure | parameters | method |
|---|---|---|---|
| `microstrip` | trace over one plane | `w h t er` | Hammerstad-Jensen 1980, with their thickness correction |
| `coated_microstrip` (`coatedMicrostrip`) | microstrip under solder mask | `w h t er c erc` (mask thickness and εr) | Hammerstad-Jensen + boarddd mask model |
| `stripline` | trace between two planes, symmetric or offset | `w h1 h2 t er` (`h2` defaults to `h1`; plane spacing b = h1 + t + h2) | Cohn 1954 exact (t = 0) × Wheeler 1978 thickness ratio; boarddd offset model |
| `cpw` | coplanar waveguide, no plane under it | `w gap h t er` | Ghione-Naldi 1984 + boarddd thickness |
| `cpwg` | grounded (conductor-backed) CPW | `w gap h t er`, optional mask `c erc` | Ghione-Naldi 1987 + boarddd thickness (+ boarddd mask model) |
| `coupled_microstrip` (`coupledMicrostrip`) | edge-coupled pair on an outer layer | `w s h t er` (`s` edge to edge), optional mask `c erc` | Kirschning-Jansen 1984 (+1985 corrections) + boarddd thickness (+ boarddd mask model) |
| `coupled_stripline` (`coupledStripline`) | edge-coupled pair between planes | `w s h1 h2 t er` | Cohn 1955 exact (t = 0) + boarddd thickness and offset |
| `ipc2141_microstrip`, `ipc2141_stripline`, `ipc2141_coupled_microstrip`, `ipc2141_coupled_stripline` | | as above | IPC-2141 rules of thumb, **comparison only** (`comparison: true`) |

`MODELS` maps every id to its function and `calculate(id, params)` runs one. `synthesize(id, params, target,
{ vary = 'w', key, lo, hi, tol })` solves for one dimension by bisection in log space (default bracket 1e-3 to
1e2 times the dielectric height, relative tolerance 1e-9). `key` defaults to `Zdiff` for coupled models and `Z0`
otherwise; any result field works (e.g. `Zcommon` while varying `s`). Every model is monotonic in `w` (tested),
and an unreachable target throws.

### Sources

The formulas are implemented from the papers, cited in the code next to each function. No code was taken from
KiCad (GPL), Qucs/Transcalc (GPL) or js_2d_fields (GPL-3.0).

- E. Hammerstad, Ø. Jensen, "Accurate models for microstrip computer-aided design", IEEE MTT-S Digest 1980, pp. 407–409.
- S. B. Cohn, "Characteristic impedance of the shielded-strip transmission line", IRE Trans. MTT-2, 1954, pp. 52–57.
- S. B. Cohn, "Shielded coupled-strip transmission line", IRE Trans. MTT-3(5), 1955, pp. 29–38.
- H. A. Wheeler, "Transmission-line properties of a strip line between parallel planes", IEEE Trans. MTT-26, 1978, pp. 866–876.
- G. Ghione, C. U. Naldi, "Analytical formulas for coplanar lines in hybrid and monolithic MICs", Electronics Letters 20(4), 1984; "Coplanar waveguides for MMIC applications…", IEEE Trans. MTT-35(3), 1987, pp. 260–267.
- M. Kirschning, R. H. Jansen, "Accurate wide-range design equations for the frequency-dependent characteristic of parallel coupled microstrip lines", IEEE Trans. MTT-32(1), 1984, pp. 83–90; corrections MTT-33(3), 1985, p. 288.
- B. C. Wadell, "Transmission Line Design Handbook", Artech House 1991 (offset stripline, coupled-line thickness).
- IPC-2141A (2004), for the comparison formulas only.

### Where boarddd departs from the textbook

The zero-thickness conformal maps (Cohn, Ghione-Naldi) are exact and Hammerstad-Jensen and Kirschning-Jansen are
fits to exact numerical results, so tier 1 keeps them as published. Four published *corrections* were more than 2 %
off a quasi-static field solver for ordinary PCB geometry (1 oz copper, 0.1–0.4 mm gaps and cores), and nothing
published models a conformal mask, so boarddd uses physically-based terms whose constants are fitted to boarddd's
own field solver: `fixtures/impedance/field-sweep.json` and, for the mask on CPWG and coupled lines,
`field-mask-sweep.json` (`fixtures/impedance/fit_corrections.py` refits them all). Each is marked "boarddd" in
`method` and in the code; the constants live in one table, `FIT` (JS) / `_FIT` (Python).

| correction | published model | its worst error vs the field solver | boarddd model | boarddd's error vs field-sweep.json (inside the flags) |
|---|---|---|---|---|
| CPW/CPWG strip thickness | Gupta et al. 1996: edges widened by Δ = (1.25t/π)(1 + ln 4πw/t) | −7 % … +12 % (Δ approaches the gap) | air capacitance on top of the t = 0 map: the gap sidewalls as parallel plates (t/g) plus fitted corner and backing terms | −0.8 … +0.5 % |
| coupled microstrip thickness | Jansen 1978 mode widths | odd mode up to +28 % when s ≈ t | the single line's H-J thickness capacitance, shared by its edges, with the inner edge removed (even) or replaced by a parallel plate 2t/s across the gap (odd), weighted by fitted ψ(s/h) | −1.4 … +0.8 % |
| coupled stripline thickness | Cohn 1955 thin-strip corrections | −6.4 … +6.5 % | the same edge model on Cohn's exact modes, ψ(s/(b−t)) | −0.9 … +0.8 % |
| offset stripline | two symmetric lines in parallel (Wadell §3.5.3) | up to +6.5 % at h2/h1 = 4 | Cohn's centred strip moved off centre by two exact limits (wide: offset half-plane fringing; narrow: the image series, Z + (η/2π) ln sin(πa/b)), blended by w/min(a, c) | −1.7 … +1.2 % |
| solder mask on microstrip | none for a conformal coating (Bahl-Stuchly, Svačina and Wan-Hoorfar treat a planar cover) | ignoring the mask reads 0.2–18 % high | εeff rises by the air share (1 − q) × the share F of that air field inside the coating, fitted on 162 field solutions | −0.3 … +0.3 % |
| solder mask on CPWG and coupled microstrip | none | ignoring it reads up to 9 % high (JLC's calculator, below) | the same form per mode, F = 1 − exp(−k u^−a (c/h)^p (1 + b h/s)), s the gap or the pair spacing; k, a, p, b per mode | mask/bare ratio: CPWG max 1.3 %, even max 0.6 %, odd max 2.1 % (rms ≤ 0.5 %) |

**History.** Until I4 these constants were fitted to `qs-sweep.json`, the same 886 geometries solved by hforsten's
solver (GPL-3.0, run locally as a tool; numbers only). That file now stays only as a cross-reference: boarddd's own
solver agrees with it to −0.5 % on average and within 2 % for 98.5 % of the 1240 impedances (below), and the
refit moved the constants by a few per cent. Nothing in boarddd depends on it.

## Validity flags

| model | flagged when outside | source of the range |
|---|---|---|
| microstrip | 0.01 ≤ w/h ≤ 100, εr ≤ 128, t/h ≤ 0.2 | Hammerstad-Jensen's stated range; t/h from the reference sweep |
| coated_microstrip | microstrip's, plus 0.25 ≤ w/h ≤ 4, c/h ≤ 0.4, 2.5 ≤ εc ≤ 5 | the fit's data range |
| stripline | t/b ≤ 0.25, w/(b−t) ≥ 0.05; offset: h_max/h_min ≤ 4, h_min/t ≥ 2 | Wheeler 1978; the offset model's checked range |
| cpw, cpwg | t/gap ≤ 0.7; cpwg: h/gap ≥ 1 (below that Ghione-Naldi's field split is 2–6 % high) | the reference sweep |
| coupled_microstrip | 0.1 ≤ w/h ≤ 10, 0.1 ≤ s/h ≤ 10, εr ≤ 18, t/h ≤ 0.35 | Kirschning-Jansen's stated range; the thickness fit |
| coupled_stripline | t/b ≤ 0.12, s/(b−t) ≥ 0.15; offset: h_max/h_min ≤ 3, h_min/t ≥ 2 | the thickness and offset fits |
| ipc2141_* | microstrip 0.1 ≤ w/h ≤ 2, εr ≤ 15; stripline w/(b−t) ≤ 0.35, t/b ≤ 0.25 | IPC-2141 |

## Accuracy

Target (owner decision, impedance report §6): Z0 within 2 % of Polar/HFSS, closed form within 1 % inside its
validity range. Against published solver results:

| case | key | reference | tier 1 | tier 2 (field solver) | IPC-2141 |
|---|---|---|---|---|---|
| Polar Si9000, h 0.794, εr 4.2, w 3.30 | Z0 | 30.09 | 30.11 (+0.1 %) | 30.10 (+0.0 %) | 21.08 (−30.0 %) |
| Polar Si9000, w 1.50 | Z0 | 50.63 | 50.63 (0.0 %) | 50.65 (+0.0 %) | 49.47 (−2.3 %) |
| Polar Si9000, w 0.45 | Z0 | 89.63 | 89.71 (+0.1 %) | 89.71 (+0.1 %) | 91.34 (+1.9 %) |
| HFSS microstrip w 3, h 1.6, εr 4.5 | Z0 | 49.80 | 49.66 (−0.3 %) | 49.68 (−0.2 %) | 48.97 (−1.7 %) |
| HFSS stripline w 0.15, b 0.435, εr 4.1 | Z0 | 50.61 | 50.20 (−0.8 %) | 50.22 (−0.8 %) | 49.60 (−2.0 %) |
| HFSS GCPW w 0.3, g 0.15, h 0.3 | Z0 | 55.47 | 55.14 (−0.6 %) | 55.12 (−0.6 %) | — |
| HFSS diff. microstrip w 3, s 1, h 1.6 | Zdiff | 80.46 | 80.45 (−0.0 %) | 80.45 (−0.0 %) | 72.15 (−10.3 %) |
| HFSS diff. stripline w 0.15, s 0.1 | Zdiff | 75.20 | 74.44 (−1.0 %) | 74.46 (−1.0 %) | 81.52 (+8.4 %) |

The HFSS values are full-wave at 1 GHz with loss, which raises Z by about 0.5–1 % over a quasi-static answer.

Against boarddd's field-solver sweep (`fixtures/impedance/field-sweep.json`, 886 geometries; real copper,
t ≥ 18 µm, inside the flags):

| model | n | error range | rms |
|---|---|---|---|
| microstrip | 30 | −0.1 … +0.8 % | 0.2 % |
| coated_microstrip | 162 | −0.3 … +0.3 % | 0.1 % |
| stripline (incl. offset to 4:1) | 60 | −0.3 … +1.2 % | 0.3 % |
| cpwg | 69 | −0.8 … +0.5 % | 0.3 % |
| coupled_microstrip (Zodd, Zeven) | 80 | −1.4 … +0.8 % | 0.4 % |
| coupled_stripline (Zodd, Zeven) | 252 | −0.9 … +0.8 % | 0.4 % |

The tests hold it to max 2 % and rms 0.6 % per model, and the mask terms (masked/bare ratio, field-mask-sweep.json)
to max 2.5 % and rms 0.6 %. Zero-thickness Cohn values agree with scipy's elliptic integrals to 1e-13, and
microstrip/CPW agree with scikit-rf's independent implementation to 1e-4 %.

**Against JLCPCB's calculator** (5 published calculator outputs on JLC04161H-7628, from the t-0279 cross-check;
the dataset is private and is not in this repository). With JLC's own assumptions (1.6 mil finished outer copper,
trapezoid top 0.7 mil narrower, mask 1.2 mil over laminate and between traces and 0.6 mil over copper at εr 3.8),
tier 2 gives JLC's target at JLC's width to within −0.4 … +1.0 % (median 0.1 %). Tier 1 with the mask terms
(c = 0.6 mil) is within +1.0 … +2.7 %; before them it read +3.5 … +9.2 % high.

Remember what the formulas cannot see: fab tolerance is ±10 %, Er at frequency, pressed prepreg thickness, mask
thickness over the trace (thinner than over laminate) and etch shape usually matter more than any of these errors.

## Tier 2: the field solver

```js
import { solveCrossSection, fieldCalculate, sectionFor, lineFromStackup, evaluateTarget } from 'boarddd/impedance';

// Any cross-section: rectangles in mm, y up. A missing x0/x1 extends to the domain edge; later dielectrics win.
solveCrossSection({
  conductors: [
    { y0: -0.035, y1: 0, net: 'gnd' },                          // plane
    { x0: -0.25, x1: -0.1, y0: 0.2, y1: 0.235, net: 'p' },      // an asymmetric pair
    { x0: 0.05, x1: 0.15, y0: 0.2, y1: 0.235, net: 'n' },
  ],
  dielectrics: [{ y0: 0, y1: 0.2, er: 4.4 }],
});
// { Zdiff, Zcommon, Zodd, Zeven, eps_eff_odd, eps_eff_even, matrices: { C, C0, L }, error_pct, symmetry, levels, ms, … }

fieldCalculate('coupled_microstrip', { w: 0.15, s: 0.15, h: 0.2104, t: 0.035, er: 4.4, c: 0.03, ct: 0.015, erc: 3.8, etch: 0.018 });
fieldCalculate('coupled_cpwg', { w: 0.1, s: 0.15, gap: 0.2, h: 0.15, t: 0.018, er: 4.1 });    // not in tier 1
const line = lineFromStackup(board.stackup, 'F.Cu', { width: 0.15, kind: 'differential', gap: 0.15, solver: 'field' });
solveCrossSection(line.section).Zdiff;                  // every layer's own εr, the mask conformal
evaluateTarget(board.stackup, netClass.impedance, { solver: 'field' });   // synthesis by secant steps from tier 1
```

```python
from boarddd.impedance import solve_cross_section, field_calculate, line_from_stackup   # pip install "boarddd[field]"

field_calculate("cpwg", {"w": 0.3, "gap": 0.15, "h": 0.3, "t": 0.035, "er": 4.5}).Z0
solve_cross_section(line_from_stackup(stackup, "In1.Cu", width=0.1, solver="field").section).to_dict()
```

| | |
|---|---|
| Section | `conductors: [{x0?, x1?, y0, y1, net}]`, `dielectrics: [{x0?, x1?, y0, y1, er}]`, `ground` (nets at 0 V, default `['gnd']`), `background` (εr elsewhere, default 1). Every other net is a signal, in order of appearance; a signal must be bounded in x. Floating copper is not supported (give it a net) |
| Result | one signal: `Z0, eps_eff, C, L, C0` (F/m, H/m); two: `Zdiff, Zcommon, Zodd, Zeven, eps_eff_odd, eps_eff_even` (Zdiff = 2 Zodd, Zcommon = Zeven/2; for an asymmetric pair the differential and common modes from the matrices); any number: `matrices.C`, `C0` (Maxwell capacitance, with and without dielectrics) and `L` = μ0ε0 C0⁻¹. `error` / `error_pct`: the error estimate; `levels`: each grid's values and size; `symmetry`: `same`, `swap` or `none` |
| Options | `tol` (default 0.01: refine until the estimate is below 1 %), `level` (first grid, default 0), `maxLevel` (default 4), `symmetry` (default true) |
| `sectionFor(model, params)` / `fieldCalculate` | tier-1 parameters as a section, plus `coupled_cpw`, `coupled_cpwg` (`s` the pair spacing), `etch` (trapezoid: the top is `etch` narrower than `w`, as a 4-step staircase), mask `c` (over laminate), `ct` (over copper), `cs` (in a pair's gap; Polar's C1/C2/C3), `erc`; coplanar `gnd` (ground width) and `fence` (CPWG: a ground wall through the substrate at that distance from the gap) |
| `lineFromStackup(…, {solver: 'field'})` | adds `section`: the layers between the reference planes, each with its own εr (planes skipped in between become resin of the neighbouring layer's εr), the copper slab filled with the adjacent prepreg's εr on inner layers, the mask (`thickness` over laminate, `thickness_over_copper` over copper), `etch`; also allows differential coplanar and coplanar on inner layers (`model: null`) |
| Worker | `boarddd/impedance/fieldsolver-worker.js`: post `{id, section, opts}` or `{id, model, params, opts}`, receive `{id, result}` or `{id, error}`. `fieldsolver.js` has no DOM or Worker dependencies, so a `file://` bundle calls it on the main thread |

**Method.** Laplace's equation ∇·(ε∇φ) = 0 by finite volumes (five-point stencil, cell-wise εr) on a graded
rectilinear grid: every rectangle edge is a grid line, cells at conductor edges are 1 % of the smallest conductor
feature and grow geometrically (×1.4 at level 0) away from them, to a far boundary 50 structure sizes away
(zero normal flux; a full-width plane ends the domain). Conductors are fixed potentials. The stored energy of unit
excitations gives the Maxwell matrices with and without dielectrics. A section that is its own mirror image is
solved on half the domain, with a magnetic wall (single lines, even mode) or an electric wall (odd mode). The
linear systems are solved **directly**: sparse Cholesky in nested-dissection order (JS; scipy's SuperLU in
Python), so there is no iteration count to run out and no silent under-convergence. A homogeneous dielectric
reuses the vacuum solution (K = εr K0).

**Error estimate.** Every solve uses two grids, the second with every cell size and the growth excess divided by
√2. The error falls by a measured ratio of 0.51–0.57 per level (Cohn's exact cases and microstrip), so the result
is the Richardson extrapolation with ratio 0.55 and `error` is the size of that extrapolation, i.e. the error of
the finer grid. While it is above `tol`, further levels are added. It is conservative: on the 9 exact Cohn cases
the extrapolated value is within 0.08 % while `error_pct` reports 0.2–0.9 %, and against a fine reference (levels
3/4) on 41 sweep geometries the default result is within 0.08 % median and 0.23 % worst.

**Accuracy (tier 2).** Against exact and published values (`field-cases.json` golden, tested in both languages):

| reference | cases | tier 2 |
|---|---|---|
| Cohn 1954/1955 exact, zero-thickness stripline and coupled stripline | 9 | −0.02 … +0.08 % |
| Polar Si9000 (BEM) microstrip | 3 | +0.04 … +0.09 % |
| HFSS (full-wave, lossy, 1 GHz) microstrip, stripline, GCPW, coupled pairs | 7 | −0.98 … −0.01 % |
| Hammerstad-Jensen t = 0 (0.2 % stated) | 3 | −0.10 … +0.13 % |
| Kirschning-Jansen t = 0 (0.6 % stated) | 2 | −0.13 … +0.21 % |

**Against the GPL reference.** `field-sweep.json` is boarddd's solver on the 886 geometries of `qs-sweep.json`
(hforsten's solver, run locally by t-0278 as a tool). Difference qs − boarddd over 1240 impedances: mean −0.5 %,
−3.2 … +0.1 %, 91 % within 1 % and 98.5 % within 2 %. Per model, real copper (t ≥ 18 µm):

| model | n | qs − boarddd | mean |
|---|---|---|---|
| microstrip | 40 | −0.6 … +0.1 % | −0.3 % |
| coated_microstrip | 180 | −0.5 … −0.1 % | −0.2 % |
| stripline | 76 | −0.6 … −0.1 % | −0.3 % |
| cpwg | 108 | −0.7 … −0.2 % | −0.4 % |
| coupled_microstrip | 120 | −2.0 … +0.1 % | −0.7 % |
| coupled_stripline | 288 | −0.7 … −0.2 % | −0.5 % |

The GPL solver reads low throughout, as t-0278 already found against Cohn's exact values (0.2–1.3 % low), and
most where its strips are thin: the worst rows (−2 … −3.2 %) are coupled microstrip odd modes with t = 0.1 µm, where
boarddd agrees with Kirschning-Jansen to 0.7 %. The test suite checks every row within −3.5 … +0.5 %.

**Speed** (median of 5, Chromium headless and Node 22 on the claud container, shared with other jobs; level 0/1):

| case | browser main thread | browser worker | Node |
|---|---|---|---|
| microstrip (JLC 7628 outer, 0.36 mm) | 55 ms | 68 ms | 45 ms |
| coated microstrip | 57 ms | 70 ms | 45 ms |
| offset stripline | 46 ms | 49 ms | 29 ms |
| CPWG | 71 ms | 66 ms | 54 ms |
| coupled microstrip with mask | 155 ms | 168 ms | 111 ms |
| offset coupled stripline | 93 ms | 97 ms | 68 ms |
| HFSS diff. microstrip (w 3 mm on 1.6 mm, a large grid) | | | 263–294 ms |

The factorisation dominates (about 0.4 Gflop/s in JS); a multifrontal variant was tried and was slower on these
small fronts. Results are deterministic, so the JS numbers in the fixtures are reproduced exactly.

## Tests and fixtures

- `fixtures/impedance/cases.json`, generated by `node fixtures/impedance/make_cases.mjs` (`--check` in CI):
  - `golden`: reference values with their source and tolerance (Polar 1 %, HFSS 2 %, Cohn exact 1e-9 %,
    scikit-rf 0.01 %, boarddd's field solver 2 %); each must also be inside the validity range.
  - `parity`: inputs across every model, inside and outside the flags, with the JS results. Python must match
    every number to 1e-9 and every flag message exactly.
  - `synthesis`: 8 solves (width, spacing, `Zcommon`) with their JS results.
  - `stackups`, `stackup_lines`, `stackup_targets`: the royalblue54L_feather golden stackup and a synthetic one
    (mixed εr, a skipped plane, missing values), with the JS lines and target evaluations.
- `fixtures/impedance/field-cases.json`, from the same script: the tier-2 `golden` references, `sections` (generic
  sections: asymmetric pair, three signals, broadside pair, finite masked CPWG, plane void; tier-1 models with
  mask and etch) and field-mode `stackup_lines` / `stackup_targets`, each with the full JS result for Python
  parity (1e-9), and `sweep_sample`, every 60th row of the sweep, which both suites recompute.
- `fixtures/impedance/field-sweep.json` (`node fixtures/impedance/make_field_sweep.mjs`, ~10 min) and
  `field-mask-sweep.json` (`… --mask`): boarddd's field solver at levels 2/3, the data tier 1 is fitted to
  (`python fixtures/impedance/fit_corrections.py`) and checked against.
- `fixtures/impedance/qs-sweep.json`: the same geometries from hforsten's GPL solver, kept only as the
  cross-reference above.
- `test/impedance/*.test.mjs` and `python/tests/test_impedance*.py` run all of it, plus flags, input errors,
  continuity at t → 0 and offset → symmetric, monotonicity in w, symmetry vs full domain, matrix properties
  (symmetry, L C0 = I/c²), tolerance-driven refinement, and (Python, when installed) live cross-checks against
  `scipy.special.ellipk` and scikit-rf. `test/browser/impedance-field.spec.mjs` runs the solver in Chromium on
  the main thread, in its worker and from a `file://` bundle, and prints the timings.

When a formula changes, run `node fixtures/impedance/make_cases.mjs`, port the change to the other language, and
check that both test suites pass. When the field solver's numerics change, regenerate the sweeps and refit.
