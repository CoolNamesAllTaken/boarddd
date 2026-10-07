// boarddd/impedance from the board model: a boarddd/board@1 stackup (StackupLayer, top to bottom) and a net
// class's ImpedanceTarget (structure microstrip | stripline | coplanar | coplanar_grounded, kind single |
// differential) become a closed-form model and its parameters. python/src/boarddd/impedance/stackup.py is the
// same code; fixtures/impedance/cases.json ("stackup") keeps them in step.

import { calculate, synthesize } from './closedform.js';
import { solveCrossSection } from './fieldsolver.js';

/** Defaults for what a stackup leaves out (KiCad's own defaults); each use adds a warning. */
export const STACKUP_DEFAULTS = { copper_thickness: 0.035, epsilon_r: 4.5, mask_thickness: 0.01, mask_epsilon_r: 3.3 };

/** The closed-form model for an ImpedanceTarget structure and kind (null when tier 1 has none). */
export function modelFor(structure, kind = 'single', { coated = false } = {}) {
  const diff = kind === 'differential';
  switch (structure) {
    case 'microstrip': return diff ? 'coupled_microstrip' : coated ? 'coated_microstrip' : 'microstrip';
    case 'stripline': return diff ? 'coupled_stripline' : 'stripline';
    case 'coplanar': return diff ? null : 'cpw';
    case 'coplanar_grounded': return diff ? null : 'cpwg';
    default: throw new RangeError(`unknown structure ${structure}`);
  }
}

const isCopper = (l) => l.kind === 'copper';
const isDielectric = (l) => l.kind === 'dielectric';

/**
 * The dielectric between copper index i and the reference copper in direction `dir` (-1 up, +1 down): its
 * height, the series εr of its layers (field across them, as the model does for sublayers), the reference
 * layer's name (null when there is none: an outer layer), and the mask on the open side.
 */
function side(layers, i, dir, ref, warnings) {
  let h = 0, sum = 0, j = i + dir, refName = null, mask = null, maskIndex = null;
  for (; j >= 0 && j < layers.length; j += dir) {
    const l = layers[j];
    if (isCopper(l)) {
      const name = l.layer ?? l.name;
      if (ref == null || ref === name || ref === l.name) { refName = name; break; }
      h += l.thickness ?? STACKUP_DEFAULTS.copper_thickness;   // a plane voided under the trace: filled with resin
      sum += (l.thickness ?? STACKUP_DEFAULTS.copper_thickness) / (layers[j - dir]?.epsilon_r ?? STACKUP_DEFAULTS.epsilon_r);
      continue;
    }
    if (isDielectric(l)) {
      if (l.thickness == null) throw new RangeError(`stackup layer ${l.name} has no thickness`);
      let er = l.epsilon_r;
      if (er == null) { er = STACKUP_DEFAULTS.epsilon_r; warnings.push(`${l.name}: no epsilon_r, using ${er}`); }
      h += l.thickness;
      sum += l.thickness / er;
    } else if (l.kind === 'mask' && refName == null && mask == null) {
      let c = l.thickness_over_copper ?? l.thickness, erc = l.epsilon_r;
      if (c == null) { c = STACKUP_DEFAULTS.mask_thickness; warnings.push(`${l.name}: no thickness, using ${c} mm`); }
      if (erc == null) { erc = STACKUP_DEFAULTS.mask_epsilon_r; warnings.push(`${l.name}: no epsilon_r, using ${erc}`); }
      mask = { c, erc }; maskIndex = j;
    }
  }
  if (ref != null && refName == null) throw new RangeError(`reference plane ${ref} not found ${dir < 0 ? 'above' : 'below'}`);
  return { h, er: h > 0 ? h / sum : null, ref: refName, mask: refName == null ? mask : null,
    index: refName == null ? null : j, maskIndex: refName == null ? maskIndex : null };
}

/**
 * The real cross-section of signal copper i for the field solver: every layer between the reference planes with
 * its own εr (planes skipped in between become resin of the neighbouring layer's εr), the traces in the copper
 * layer's slab (filled with the adjacent prepreg's εr on an inner layer, else the layer above's; air outside),
 * coplanar grounds, and on an outer layer the solder mask as a conformal coating (`thickness` over the laminate,
 * `thickness_over_copper` over the copper). Built from one reference plane outwards, so y runs away from it.
 */
function stackupSection(layers, i, up, down, o, t, structure) {
  const seq = [];
  if (down.index != null) for (let j = down.index; j >= (up.index ?? 0); j--) seq.push(j);
  else for (let j = up.index; j < layers.length; j++) seq.push(j);
  const refs = new Set([up.index, down.index].filter((v) => v != null));
  const pair = o.kind === 'differential';
  const w = o.width, s = o.gap;
  const conductors = [], dielectrics = [];
  const thick = (l) => (l.kind === 'copper' ? l.thickness ?? STACKUP_DEFAULTS.copper_thickness
    : l.kind === 'dielectric' ? l.thickness : 0);
  const erOf = (l) => l?.epsilon_r ?? STACKUP_DEFAULTS.epsilon_r;
  const inner = up.index != null && down.index != null;
  let y = 0, slab = null, maskAt = null;
  for (const j of seq) {
    const l = layers[j];
    if (l.kind === 'mask') {
      if (j === (up.maskIndex ?? down.maskIndex)) maskAt = { l, y };
      continue;
    }
    const d = thick(l);
    if (refs.has(j)) {
      // An ungrounded coplanar line has no plane under it: the dielectric ends in air.
      if (!(structure === 'coplanar' && !inner)) conductors.push({ y0: y, y1: y + d, net: 'gnd' });
    } else if (j === i) {
      slab = { y0: y, y1: y + d };
      if (inner) {
        const nb = [layers[i - 1], layers[i + 1]].filter((x) => x?.kind === 'dielectric');
        dielectrics.push({ y0: y, y1: y + d, er: erOf(nb.find((x) => x.dielectric === 'prepreg') ?? nb[0]) });
      }
    } else if (l.kind === 'copper') {
      dielectrics.push({ y0: y, y1: y + d, er: erOf(layers[j - Math.sign(j - i)]) });
    } else if (l.kind === 'dielectric') {
      dielectrics.push({ y0: y, y1: y + d, er: erOf(l) });
    }
    y += d;
  }
  const traces = pair
    ? [{ x0: -s / 2 - w, x1: -s / 2, ...slab, net: 'p' }, { x0: s / 2, x1: s / 2 + w, ...slab, net: 'n' }]
    : [{ x0: -w / 2, x1: w / 2, ...slab, net: 'sig' }];
  conductors.push(...traces);
  const grounds = [];
  if (structure.startsWith('coplanar')) {
    const e = (pair ? s / 2 + w : w / 2) + o.coplanarGap;
    grounds.push({ x1: -e, ...slab, net: 'gnd' }, { x0: e, ...slab, net: 'gnd' });
    conductors.push(...grounds);
  }
  if (maskAt && o.mask !== false) {
    const l = maskAt.l;
    const over = l.thickness_over_copper ?? l.thickness ?? STACKUP_DEFAULTS.mask_thickness;
    const lam = l.thickness ?? over, er = l.epsilon_r ?? STACKUP_DEFAULTS.mask_epsilon_r;
    dielectrics.push({ y0: slab.y0, y1: slab.y0 + lam, er });
    for (const r of [...traces, ...grounds]) {
      dielectrics.push({ ...(r.x0 != null && { x0: r.x0 - over }), ...(r.x1 != null && { x1: r.x1 + over }), y0: slab.y0, y1: slab.y1 + over, er });
    }
  }
  return { conductors, dielectrics };
}

/**
 * A signal layer of a stackup as a closed-form line: { model, structure, params, warnings }. With
 * `solver: 'field'` also `section`, its real cross-section for solveCrossSection (every layer's own εr, the mask
 * on any outer structure); then differential coplanar and coplanar on inner layers are allowed too (model null
 * when tier 1 has none).
 * @param {{layers: object[]}} stackup  boarddd/board@1 `stackup` (layers top to bottom)
 * @param {string} layer  signal layer ('F.Cu', 'In1.Cu'): a copper StackupLayer's `layer` or `name`
 * @param {object} o
 * @param {number} o.width  track width, mm
 * @param {'single'|'differential'} [o.kind]
 * @param {number} [o.gap]  differential pair gap (edge to edge), mm
 * @param {string} [o.structure]  ImpedanceTarget structure; default microstrip on an outer layer, else stripline
 * @param {number} [o.coplanarGap]  gap to the coplanar ground, mm (coplanar structures)
 * @param {string} [o.refTop]  @param {string} [o.refBottom]  reference planes (ImpedanceLayer.ref_top/ref_bottom);
 *   default the nearest copper on each side
 * @param {boolean} [o.mask]  include the solder mask on an outer layer (default true)
 * @param {'closedform'|'field'} [o.solver]  'field' adds the cross-section for the tier-2 field solver
 */
export function lineFromStackup(stackup, layer, o = {}) {
  const layers = stackup?.layers ?? [];
  const i = layers.findIndex((l) => isCopper(l) && (l.layer === layer || l.name === layer));
  if (i < 0) throw new RangeError(`no copper layer ${layer} in the stackup`);
  const field = o.solver === 'field';
  if (o.solver != null && o.solver !== 'field' && o.solver !== 'closedform') throw new RangeError(`unknown solver ${o.solver}`);
  const warnings = [];
  let t = layers[i].thickness;
  if (t == null) { t = STACKUP_DEFAULTS.copper_thickness; warnings.push(`${layer}: no thickness, using ${t} mm`); }
  const up = side(layers, i, -1, o.refTop, warnings), down = side(layers, i, +1, o.refBottom, warnings);
  const kind = o.kind ?? 'single';
  const outer = up.ref == null || down.ref == null;
  if (up.ref == null && down.ref == null) throw new RangeError(`${layer} has no reference plane`);
  const structure = o.structure ?? (outer ? 'microstrip' : 'stripline');
  const ref = up.ref == null ? down : up, open = up.ref == null ? up : down;
  const params = { w: o.width, t };
  let model;
  if (structure === 'stripline' || (field && !outer && structure.startsWith('coplanar'))) {
    if (outer) throw new RangeError(`${layer} is an outer layer: no stripline`);
    // Different εr above and below: weight each by its plane capacitance (εr/h), as the strip sees them in parallel.
    const er = (up.er / up.h + down.er / down.h) / (1 / up.h + 1 / down.h);
    Object.assign(params, { h1: up.h, h2: down.h, er });
    model = structure === 'stripline' ? modelFor(structure, kind) : null;
    if (structure !== 'stripline') {
      if (o.coplanarGap == null) throw new RangeError(`${structure} needs coplanarGap`);
      params.gap = o.coplanarGap;
    }
  } else {
    if (!outer) throw new RangeError(`${layer} is an inner layer: tier 1 has no embedded ${structure}`);
    Object.assign(params, { h: ref.h, er: ref.er });
    const coated = structure === 'microstrip' && kind === 'single' && o.mask !== false && open.mask != null;
    if (o.mask !== false && open.mask != null && !coated && !field) warnings.push(`${structure} ${kind}: the solder mask is not modelled (tier 1)`);
    if (coated) Object.assign(params, open.mask);
    model = modelFor(structure, kind, { coated });
    if (structure.startsWith('coplanar')) {
      if (o.coplanarGap == null) throw new RangeError(`${structure} needs coplanarGap`);
      params.gap = o.coplanarGap;
    }
  }
  if (model == null && !field) throw new RangeError(`tier 1 has no ${kind} ${structure} model`);
  if (kind === 'differential') {
    if (o.gap == null) throw new RangeError('a differential line needs gap');
    params.s = o.gap;
  }
  const line = { model, structure, params, warnings };
  if (field) line.section = stackupSection(layers, i, up, down, { ...o, kind }, t, structure);
  return line;
}

/**
 * The width giving `target` with the field solver: secant steps from the tier-1 width (or the dielectric height),
 * to 1e-4 of the width.
 */
function fieldSynthesis(stackup, layer, opts, key, target, start) {
  const run = (w) => solveCrossSection(lineFromStackup(stackup, layer, { ...opts, width: w }).section, opts.fieldOptions);
  let w0 = start, r0 = run(w0), w1 = w0 * (r0[key] > target ? 1.1 : 0.9), r1 = run(w1);
  for (let it = 0; it < 20 && Math.abs(w1 - w0) > 1e-4 * w1; it++) {
    const f0 = r0[key] - target, f1 = r1[key] - target;
    if (f1 === f0) break;
    // Clamp each step to a factor of two so a poor start cannot jump to a negative width.
    const w2 = Math.min(2 * w1, Math.max(w1 / 2, w1 - (f1 * (w1 - w0)) / (f1 - f0)));
    [w0, r0] = [w1, r1];
    w1 = w2; r1 = run(w1);
  }
  return { width: w1, result: r1 };
}

/**
 * Evaluate a net class's ImpedanceTarget on a stackup: one row per target layer with its geometry. A layer with
 * no width gets the width synthesized for the target instead. `solver: 'field'` evaluates (and synthesizes) with
 * the tier-2 field solver (`fieldOptions` go to solveCrossSection); `result` is then its result.
 * @returns {{layer: string, model: string, key: string, value: number, target: number, deviation_pct: number,
 *   ok: boolean|null, width: number, synthesized: boolean, result: object, warnings: string[]}[]}
 */
export function evaluateTarget(stackup, target, o = {}) {
  const key = target.kind === 'differential' ? 'Zdiff' : 'Z0';
  return (target.layers ?? []).map((il) => {
    const opts = { ...o, kind: target.kind, structure: target.structure ?? o.structure, gap: il.gap ?? o.gap,
      refTop: il.ref_top ?? undefined, refBottom: il.ref_bottom ?? undefined, width: il.width ?? 1 };
    const line = lineFromStackup(stackup, il.layer, opts);
    let { params } = line, result, width;
    const synthesized = il.width == null;
    if (o.solver === 'field') {
      if (synthesized) {
        const start = line.model ? synthesize(line.model, params, target.target, { key }).value : params.h ?? params.h1;
        ({ width, result } = fieldSynthesis(stackup, il.layer, opts, key, target.target, start));
      } else {
        width = params.w;
        result = solveCrossSection(line.section, o.fieldOptions);
      }
      result = { model: line.model, ...result, flags: [] };
    } else {
      if (synthesized) ({ params, result } = synthesize(line.model, params, target.target, { key }));
      else result = calculate(line.model, params);
      width = params.w;
    }
    const value = result[key];
    const deviation_pct = (100 * (value - target.target)) / target.target;
    return { layer: il.layer, model: line.model, key, value, target: target.target, deviation_pct,
      ok: target.tolerance_pct == null ? null : Math.abs(deviation_pct) <= target.tolerance_pct,
      width, synthesized, result, warnings: line.warnings };
  });
}
