// boarddd/impedance from the board model: a boarddd/board@1 stackup (StackupLayer, top to bottom) and a net
// class's ImpedanceTarget (structure microstrip | stripline | coplanar | coplanar_grounded, kind single |
// differential) become a closed-form model and its parameters. python/src/boarddd/impedance/stackup.py is the
// same code; fixtures/impedance/cases.json ("stackup") keeps them in step.

import { calculate, synthesize } from './closedform.js';
import { etched, mask, solveCrossSection } from './fieldsolver.js';
import { LOSS_DEFAULTS } from './loss.js';

/** Defaults for what a stackup leaves out (KiCad's own defaults); each use adds a warning. */
export const STACKUP_DEFAULTS = { copper_thickness: 0.035, epsilon_r: 4.5, mask_thickness: 0.01, mask_epsilon_r: 3.3,
  loss_tangent: 0.02, mask_loss_tangent: 0.02 };

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
      if (ref !== false && (ref == null || ref === name || ref === l.name)) { refName = name; break; }
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
  if (ref != null && ref !== false && refName == null) throw new RangeError(`reference plane ${ref} not found ${dir < 0 ? 'above' : 'below'}`);
  return { h, er: h > 0 ? h / sum : null, ref: refName, mask: refName == null ? mask : null,
    index: refName == null ? null : j, maskIndex: refName == null ? maskIndex : null };
}

/** The x0/x1 of an extent ({x0?, x1?}; null or missing ends run to the domain edge). */
const span = (e) => ({ ...(e?.x0 != null && { x0: e.x0 }), ...(e?.x1 != null && { x1: e.x1 }) });

/**
 * The real cross-section of signal copper i for the field solver: every layer between the reference planes with
 * its own εr (planes skipped in between become resin of the neighbouring layer's εr), the traces in the copper
 * layer's slab (filled with the adjacent prepreg's εr on an inner layer, else the layer above's; air outside),
 * coplanar grounds, and on an outer layer the solder mask as a conformal coating (`thickness` over the laminate
 * and between traces, `thickness_over_copper` over the copper). With `etch` the traces are trapezoids whose top
 * is `etch` narrower than the base `width`. Built from one reference plane outwards, so y runs away from it.
 */
function stackupSection(layers, i, up, down, o, t, structure) {
  const seq = [];
  if (down.index != null) for (let j = down.index; j >= (up.index ?? 0); j--) seq.push(j);
  else if (up.index != null) for (let j = up.index; j < layers.length; j++) seq.push(j);
  else for (let j = layers.length - 1; j >= 0; j--) seq.push(j);   // no plane at all (a layout's CPW): bottom up
  const refs = new Set([up.index, down.index].filter((v) => v != null));
  const pair = o.kind === 'differential';
  const w = o.width, s = o.gap;
  const conductors = [], dielectrics = [];
  const thick = (l) => (l.kind === 'copper' ? l.thickness ?? STACKUP_DEFAULTS.copper_thickness
    : l.kind === 'dielectric' ? l.thickness : 0);
  const erOf = (l) => l?.epsilon_r ?? STACKUP_DEFAULTS.epsilon_r;
  // Loss data for loss.js (ignored by the impedance solve): each dielectric's material (its layer) and tan δ,
  // each conductor's metal (its copper layer).
  const mat = (l) => ({ tand: l?.loss_tangent ?? (l?.kind === 'mask' ? STACKUP_DEFAULTS.mask_loss_tangent : STACKUP_DEFAULTS.loss_tangent),
    material: l?.name ?? null });
  const metal = (l) => l.layer ?? l.name;
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
      const ext = o.layout?.planes?.[j === up.index ? 'top' : 'bottom'];
      if (!(structure === 'coplanar' && !inner)) conductors.push({ ...span(ext), y0: y, y1: y + d, net: 'gnd', metal: metal(l) });
    } else if (j === i) {
      slab = { y0: y, y1: y + d };
      if (inner) {
        const nb = [layers[i - 1], layers[i + 1]].filter((x) => x?.kind === 'dielectric');
        const fill = nb.find((x) => x.dielectric === 'prepreg') ?? nb[0];
        dielectrics.push({ y0: y, y1: y + d, er: erOf(fill), ...mat(fill) });
      }
    } else if (l.kind === 'copper') {
      const resin = layers[j - Math.sign(j - i)];
      dielectrics.push({ y0: y, y1: y + d, er: erOf(resin), ...mat(resin) });
    } else if (l.kind === 'dielectric') {
      dielectrics.push({ y0: y, y1: y + d, er: erOf(l), ...mat(l) });
    }
    y += d;
  }
  const sm = metal(layers[i]);
  const traces = o.layout ? o.layout.traces.map((b) => ({ x0: b.x0, x1: b.x1, ...slab, net: b.net, metal: sm }))
    : pair
      ? [{ x0: -s / 2 - w, x1: -s / 2, ...slab, net: 'p', metal: sm }, { x0: s / 2, x1: s / 2 + w, ...slab, net: 'n', metal: sm }]
      : [{ x0: -w / 2, x1: w / 2, ...slab, net: 'sig', metal: sm }];
  conductors.push(...traces.flatMap((b) => etched(b, o.etch)));
  const grounds = [];
  if (o.layout) {
    grounds.push(...(o.layout.grounds ?? []).map((g) => ({ ...span(g), ...slab, net: 'gnd', metal: sm })));
    conductors.push(...grounds);
  } else if (structure.startsWith('coplanar')) {
    const e = (pair ? s / 2 + w : w / 2) + o.coplanarGap;
    grounds.push({ x1: -e, ...slab, net: 'gnd', metal: sm }, { x0: e, ...slab, net: 'gnd', metal: sm });
    conductors.push(...grounds);
  }
  if (maskAt && o.mask !== false) {
    const l = maskAt.l;
    const ct = l.thickness_over_copper ?? l.thickness ?? STACKUP_DEFAULTS.mask_thickness;
    dielectrics.push(...mask([...traces, ...grounds], slab.y0, { c: l.thickness ?? ct, ct,
      er: l.epsilon_r ?? STACKUP_DEFAULTS.mask_epsilon_r }).map((r) => ({ ...r, ...mat(l) })));
  }
  return { conductors, dielectrics };
}

/** The roughness of a copper StackupLayer for loss.js (lengths in mm), or null when it gives none. */
export function roughnessOf(l) {
  const r = { model: l.roughness_model ?? undefined, rq: l.roughness_rq ?? undefined, rz: l.roughness_rz ?? undefined,
    radius: l.nodule_radius ?? undefined, ratio: l.nodule_ratio ?? undefined };
  if (r.model === 'none') return { model: 'none' };
  if (r.model == null && r.rq == null && r.rz == null && r.radius == null) return null;
  return Object.fromEntries(Object.entries(r).filter(([, v]) => v !== undefined));
}

/**
 * The loss data of a line (lineFromStackup with `loss`): options for loss.js's lineLoss (tier 1: `dielectric`,
 * `mask`, `signal`, `ground`) and sectionLoss (tier 2: `materials` by layer name, `metals` by copper layer).
 * Tier 1's single dielectric takes the thickness-weighted tan δ of each side (and, between two planes, the sides
 * weighted by their plane capacitance εr/h, as for εr).
 */
function lossOf(layers, i, up, down, warnings) {
  const between = (a, b) => layers.slice(Math.min(a, b) + 1, Math.max(a, b)).filter(isDielectric);
  const dielectricOf = (l) => {
    if (l.loss_tangent == null) warnings.push(`${l.name}: no loss_tangent, using ${l.kind === 'mask' ? STACKUP_DEFAULTS.mask_loss_tangent : STACKUP_DEFAULTS.loss_tangent}`);
    return { frequency: l.frequency ?? LOSS_DEFAULTS.frequency, model: l.dielectric_model ?? 'djordjevic_sarkar' };
  };
  const materials = {}, metals = {};
  const used = layers.filter((l, j) => (isDielectric(l) || l.kind === 'mask')
    && j >= (up.index ?? up.maskIndex ?? 0) && j <= (down.index ?? down.maskIndex ?? layers.length - 1));
  for (const l of used) materials[l.name] = dielectricOf(l);
  if (used.some((l) => l.frequency == null)) warnings.push(`Er/Df frequency not given: ${LOSS_DEFAULTS.frequency / 1e9} GHz assumed`);
  const metalOf = (l) => {
    const roughness = roughnessOf(l);
    if (roughness == null) warnings.push(`${l.layer ?? l.name}: no roughness, smooth copper`);
    return { conductivity: l.conductivity ?? LOSS_DEFAULTS.conductivity, roughness };
  };
  for (const j of [i, up.index, down.index]) if (j != null) metals[layers[j].layer ?? layers[j].name] = metalOf(layers[j]);
  // Tier 1: one effective dielectric.
  const sideOf = (ref) => {
    const ls = between(i, ref);
    const h = ls.reduce((a, l) => a + l.thickness, 0);
    const tand = ls.reduce((a, l) => a + l.thickness * (l.loss_tangent ?? STACKUP_DEFAULTS.loss_tangent), 0) / (h || 1);
    return { tand, h, er: ls.length ? h / ls.reduce((a, l) => a + l.thickness / (l.epsilon_r ?? STACKUP_DEFAULTS.epsilon_r), 0) : 1, near: ls[0] };
  };
  const sides = [up.index, down.index].filter((j) => j != null).map(sideOf);
  const w = sides.map((sd) => sd.er / sd.h);
  const tand = sides.reduce((a, sd, k) => a + w[k] * sd.tand, 0) / w.reduce((a, b) => a + b, 0);
  const near = sides[0]?.near;
  const out = { dielectric: { tand, ...(near ? materials[near.name] : {}) }, materials, metals,
    signal: metals[layers[i].layer ?? layers[i].name] };
  const ref = up.index ?? down.index;
  if (ref != null) out.ground = metals[layers[ref].layer ?? layers[ref].name];
  const mi = up.maskIndex ?? down.maskIndex;
  if (mi != null) {
    const l = layers[mi];
    out.mask = { tand: l.loss_tangent ?? STACKUP_DEFAULTS.mask_loss_tangent, ...materials[l.name] };
  }
  return out;
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
 * @param {number} [o.etch]  field solver: the trace top is this much narrower than `width` (trapezoid), mm
 * @param {boolean} [o.loss]  also `loss`: options for loss.js (lineLoss with model and params, sectionLoss with
 *   the section solved with { loss: true }): Er/Df at their frequency, mask, copper conductivity and roughness
 * @param {object} [o.layout]  field solver: the copper in the signal layer as found on a board (route.js):
 *   `traces` [{x0, x1, net}], coplanar `grounds` [{x0?, x1?}] and the reference planes' extents
 *   `planes: {top, bottom}` ({x0?, x1?}; a missing end runs to the domain edge). refTop/refBottom `false`
 *   means no plane on that side (the dielectric runs to the board surface).
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
  // A layout (boarddd/impedance route.js) may describe a CPW with no plane at all: its coplanar grounds.
  if (up.ref == null && down.ref == null && !(field && o.layout?.grounds?.length)) throw new RangeError(`${layer} has no reference plane`);
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
    const masked = o.mask !== false && open.mask != null;
    const coated = masked && structure === 'microstrip' && kind === 'single';
    // Tier 1 models the mask on microstrip (single and coupled) and CPWG; not on CPW.
    const withMask = masked && (structure === 'microstrip' || (structure === 'coplanar_grounded' && kind === 'single'));
    if (masked && !withMask && !field) warnings.push(`${structure} ${kind}: the solder mask is not modelled (tier 1)`);
    if (withMask) Object.assign(params, open.mask);
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
  if (o.loss) line.loss = lossOf(layers, i, up, down, warnings);
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
