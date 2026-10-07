// boarddd/impedance from the board model: a boarddd/board@1 stackup (StackupLayer, top to bottom) and a net
// class's ImpedanceTarget (structure microstrip | stripline | coplanar | coplanar_grounded, kind single |
// differential) become a closed-form model and its parameters. python/src/boarddd/impedance/stackup.py is the
// same code; fixtures/impedance/cases.json ("stackup") keeps them in step.

import { calculate, synthesize } from './closedform.js';

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
  let h = 0, sum = 0, j = i + dir, refName = null, mask = null;
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
      mask = { c, erc };
    }
  }
  if (ref != null && refName == null) throw new RangeError(`reference plane ${ref} not found ${dir < 0 ? 'above' : 'below'}`);
  return { h, er: h > 0 ? h / sum : null, ref: refName, mask: refName == null ? mask : null };
}

/**
 * A signal layer of a stackup as a closed-form line: { model, structure, params, warnings }.
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
 */
export function lineFromStackup(stackup, layer, o = {}) {
  const layers = stackup?.layers ?? [];
  const i = layers.findIndex((l) => isCopper(l) && (l.layer === layer || l.name === layer));
  if (i < 0) throw new RangeError(`no copper layer ${layer} in the stackup`);
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
  if (structure === 'stripline') {
    if (outer) throw new RangeError(`${layer} is an outer layer: no stripline`);
    // Different εr above and below: weight each by its plane capacitance (εr/h), as the strip sees them in parallel.
    const er = (up.er / up.h + down.er / down.h) / (1 / up.h + 1 / down.h);
    Object.assign(params, { h1: up.h, h2: down.h, er });
    model = modelFor(structure, kind);
  } else {
    if (!outer) throw new RangeError(`${layer} is an inner layer: tier 1 has no embedded ${structure}`);
    Object.assign(params, { h: ref.h, er: ref.er });
    const coated = structure === 'microstrip' && kind === 'single' && o.mask !== false && open.mask != null;
    if (o.mask !== false && open.mask != null && !coated) warnings.push(`${structure} ${kind}: the solder mask is not modelled (tier 1)`);
    if (coated) Object.assign(params, open.mask);
    model = modelFor(structure, kind, { coated });
    if (structure.startsWith('coplanar')) {
      if (o.coplanarGap == null) throw new RangeError(`${structure} needs coplanarGap`);
      params.gap = o.coplanarGap;
    }
  }
  if (model == null) throw new RangeError(`tier 1 has no ${kind} ${structure} model`);
  if (kind === 'differential') {
    if (o.gap == null) throw new RangeError('a differential line needs gap');
    params.s = o.gap;
  }
  return { model, structure, params, warnings };
}

/**
 * Evaluate a net class's ImpedanceTarget on a stackup: one row per target layer with its geometry. A layer with
 * no width gets the width synthesized for the target instead.
 * @returns {{layer: string, model: string, key: string, value: number, target: number, deviation_pct: number,
 *   ok: boolean|null, width: number, synthesized: boolean, result: object, warnings: string[]}[]}
 */
export function evaluateTarget(stackup, target, o = {}) {
  const key = target.kind === 'differential' ? 'Zdiff' : 'Z0';
  return (target.layers ?? []).map((il) => {
    const opts = { ...o, kind: target.kind, structure: target.structure ?? o.structure, gap: il.gap ?? o.gap,
      refTop: il.ref_top ?? undefined, refBottom: il.ref_bottom ?? undefined, width: il.width ?? 1 };
    const line = lineFromStackup(stackup, il.layer, opts);
    let { params } = line, result;
    const synthesized = il.width == null;
    if (synthesized) ({ params, result } = synthesize(line.model, params, target.target, { key }));
    else result = calculate(line.model, params);
    const value = result[key];
    const deviation_pct = (100 * (value - target.target)) / target.target;
    return { layer: il.layer, model: line.model, key, value, target: target.target, deviation_pct,
      ok: target.tolerance_pct == null ? null : Math.abs(deviation_pct) <= target.tolerance_pct,
      width: params.w, synthesized, result, warnings: line.warnings };
  });
}
