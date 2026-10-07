# Impedance (`boarddd/impedance`, `boarddd.impedance`)

Characteristic impedance of PCB transmission lines, the same code in JS (`src/impedance/`) and Python
(`python/src/boarddd/impedance/`). This is **tier 1: closed-form, quasi-static** formulas. They are instant,
so they suit live hover, width synthesis and bulk checks (kipr). Tier 2, a 2D field solver for real
cross-sections (finite grounds, voids, neighbouring traces), is a later phase and will live next to it.

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
| `cpwg` | grounded (conductor-backed) CPW | `w gap h t er` | Ghione-Naldi 1987 + boarddd thickness |
| `coupled_microstrip` (`coupledMicrostrip`) | edge-coupled pair on an outer layer | `w s h t er` (`s` edge to edge) | Kirschning-Jansen 1984 (+1985 corrections) + boarddd thickness |
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
off a quasi-static field solver for ordinary PCB geometry (1 oz copper, 0.1–0.4 mm gaps and cores), so boarddd
replaces them with physically-based terms whose constants are fitted to field solutions
(`fixtures/impedance/qs-sweep.json`). Each is marked "boarddd" in `method` and in the code.

| correction | published model | its worst error vs the field solver | boarddd model | boarddd's worst error (inside the flags) |
|---|---|---|---|---|
| CPW/CPWG strip thickness | Gupta et al. 1996: edges widened by Δ = (1.25t/π)(1 + ln 4πw/t) | −7 % … +12 % (Δ approaches the gap) | air capacitance on top of the t = 0 map: the gap sidewalls as parallel plates (t/g) plus fitted corner and backing terms | +1.6 % |
| coupled microstrip thickness | Jansen 1978 mode widths | odd mode up to +28 % when s ≈ t | the single line's H-J thickness capacitance, shared by its edges, with the inner edge removed (even) or replaced by a parallel plate 2t/s across the gap (odd), weighted by fitted ψ(s/h) | −1.1 … +2.3 % |
| coupled stripline thickness | Cohn 1955 thin-strip corrections | −6.4 … +6.5 % | the same edge model on Cohn's exact modes, ψ(s/(b−t)) | −0.8 … +1.5 % |
| offset stripline | two symmetric lines in parallel (Wadell §3.5.3) | up to +6.5 % at h2/h1 = 4 | Cohn's centred strip moved off centre by two exact limits (wide: offset half-plane fringing; narrow: the image series, Z + (η/2π) ln sin(πa/b)), blended by w/min(a, c) | −1.4 … +1.4 % |
| solder mask | none for a conformal coating (Bahl-Stuchly, Svačina and Wan-Hoorfar treat a planar cover) | ignoring the mask reads 0.2–18 % high | εeff rises by the air share (1 − q) × the share F of that air field inside the coating, fitted on 180 field solutions | +0.7 % |

These constants come from one reference solver; the tier-2 field solver (I4) will re-check them independently.

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

| case | key | reference | boarddd | IPC-2141 |
|---|---|---|---|---|
| Polar Si9000, h 0.794, εr 4.2, w 3.30 | Z0 | 30.09 | 30.11 (+0.1 %) | 21.08 (−30.0 %) |
| Polar Si9000, w 1.50 | Z0 | 50.63 | 50.63 (0.0 %) | 49.47 (−2.3 %) |
| Polar Si9000, w 0.45 | Z0 | 89.63 | 89.71 (+0.1 %) | 91.34 (+1.9 %) |
| HFSS microstrip w 3, h 1.6, εr 4.5 | Z0 | 49.80 | 49.66 (−0.3 %) | 48.97 (−1.7 %) |
| HFSS stripline w 0.15, b 0.435, εr 4.1 | Z0 | 50.61 | 50.20 (−0.8 %) | 49.60 (−2.0 %) |
| HFSS GCPW w 0.3, g 0.15, h 0.3 | Z0 | 55.47 | 55.33 (−0.2 %) | — |
| HFSS diff. microstrip w 3, s 1, h 1.6 | Zdiff | 80.46 | 80.40 (−0.1 %) | 72.15 (−10.3 %) |
| HFSS diff. stripline w 0.15, s 0.1 | Zdiff | 75.20 | 73.99 (−1.6 %) | 81.52 (+8.4 %) |

The HFSS values are full-wave at 1 GHz with loss, which raises Z by about 0.5–1 % over a quasi-static answer.

Against the quasi-static field-solver sweep (`fixtures/impedance/qs-sweep.json`, 886 geometries; real copper,
t ≥ 18 µm, inside the flags):

| model | n | error range | rms |
|---|---|---|---|
| microstrip | 30 | −0.1 … +1.2 % | 0.4 % |
| coated_microstrip | 162 | −0.2 … +0.7 % | 0.3 % |
| stripline (incl. offset to 4:1) | 60 | −0.2 … +1.4 % | 0.4 % |
| cpwg | 69 | +0.3 … +1.6 % | 0.9 % |
| coupled_microstrip (Zodd, Zeven) | 80 | −1.1 … +2.3 % | 0.9 % |
| coupled_stripline (Zodd, Zeven) | 252 | −0.8 … +1.5 % | 0.6 % |

The reference reads 0.2–1.3 % low where the answer is known exactly (Cohn's zero-thickness stripline), so most of
the positive bias is the reference's. Zero-thickness Cohn values agree with scipy's elliptic integrals to 1e-13,
and microstrip/CPW agree with scikit-rf's independent implementation to 1e-4 %. The tests hold the sweep to
max 2.5 % and rms 1 % per model.

Remember what the formulas cannot see: fab tolerance is ±10 %, Er at frequency, pressed prepreg thickness, mask
thickness over the trace (thinner than over laminate) and etch shape usually matter more than any of these errors.

## Tests and fixtures

- `fixtures/impedance/cases.json`, generated by `node fixtures/impedance/make_cases.mjs` (`--check` in CI):
  - `golden`: 79 reference values with their source and tolerance (Polar 1 %, HFSS 2 %, Cohn exact 1e-9 %,
    scikit-rf 0.01 %, field solver 2 %, the t-0272 spike FDM 2.5 %); each must also be inside the validity range.
  - `parity`: 72 inputs across every model, inside and outside the flags, with the JS results. Python must match
    every number to 1e-9 and every flag message exactly.
  - `synthesis`: 8 solves (width, spacing, `Zcommon`) with their JS results.
- `fixtures/impedance/qs-sweep.json`: the field-solver sweep the boarddd constants were fitted on, used for the
  accuracy envelope test (and as a validation set for tier 2).
- `test/impedance/impedance.test.mjs` and `python/tests/test_impedance.py` run all of it, plus flags, input
  errors, continuity at t → 0 and offset → symmetric, monotonicity in w, and (Python, when installed) live
  cross-checks against `scipy.special.ellipk` and scikit-rf.

When a formula changes, run `node fixtures/impedance/make_cases.mjs`, port the change to the other language, and
check that both test suites pass.
