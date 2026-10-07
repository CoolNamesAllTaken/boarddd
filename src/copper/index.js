// boarddd/copper: the copper model (boarddd/copper@1): tracks, arcs, vias, zone fills, keepouts, pads and net ties
// of a board revision, with nets, in board@1's frame (mm, y up); see docs/copper.md. Server-side readers write it
// as copper.json (python: boarddd.io.kicad.read_kicad_copper, boarddd.io.gerber_copper); copperFromGerbers builds
// it in the browser from a Gerber X2 upload. The schema is generated from python/src/boarddd/copper.py
// (schema/copper.schema.json; ./schema.js is the same object as a module); validateCopper mirrors
// python's boarddd.validate.validate_copper message for message (fixtures/copper/cases.json keeps them in step).

import { check } from '../model/check.js';
import SCHEMA from './schema.js';

export const SCHEMA_ID = 'boarddd/copper@1';
export { SCHEMA };
export { PLANE_COVERAGE, signedArea, pointInRing, unfracture, fillArea, inFill, circleHalves, planes, checkFills } from './geometry.js';
export { PAD_FUNCTIONS, parseCopperLayer, copperFromGerbers } from './gerber.js';

/** copper@1 rules beyond the schema (run only on schema-valid documents). */
function rules(c, errors) {
  const order = new Map();
  c.layers.forEach((l, i) => {
    if (order.has(l)) errors.push(`/layers/${i}: duplicate layer id '${l}' (also /layers/${order.get(l)})`);
    else order.set(l, i);
  });
  const nets = new Set(c.nets ?? []);
  const layer = (path, l) => { if (!order.has(l)) errors.push(`${path}: '${l}' is not in /layers`); };
  const net = (path, n) => { if (!nets.has(n)) errors.push(`${path}: '${n}' is not in /nets`); };
  for (const key of ['tracks', 'zones']) {
    (c[key] ?? []).forEach((item, i) => {
      layer(`/${key}/${i}/layer`, item.layer);
      net(`/${key}/${i}/net`, item.net);
    });
  }
  (c.vias ?? []).forEach((v, i) => {
    v.span.forEach((l, j) => layer(`/vias/${i}/span/${j}`, l));
    if (v.span.every((l) => order.has(l)) && order.get(v.span[0]) > order.get(v.span[1])) errors.push(`/vias/${i}/span: must run top to bottom`);
    (v.pad_layers ?? []).forEach((l, j) => layer(`/vias/${i}/pad_layers/${j}`, l));
    (v.padstack ?? []).forEach((vp, j) => layer(`/vias/${i}/padstack/${j}/layer`, vp.layer));
    net(`/vias/${i}/net`, v.net);
  });
  (c.pads ?? []).forEach((p, i) => {
    p.layers.forEach((l, j) => layer(`/pads/${i}/layers/${j}`, l));
    net(`/pads/${i}/net`, p.net);
  });
  (c.keepouts ?? []).forEach((k, i) => k.layers.forEach((l, j) => layer(`/keepouts/${i}/layers/${j}`, l)));
  (c.planes ?? []).forEach((p, i) => {
    layer(`/planes/${i}/layer`, p.layer);
    net(`/planes/${i}/net`, p.net);
  });
}

/** 'json/pointer: message' errors; empty when `copper` is a valid boarddd/copper@1 document. */
export function validateCopper(copper) {
  const errors = [];
  check(SCHEMA, copper, '', SCHEMA, errors);
  if (!errors.length) rules(copper, errors);
  return errors;
}

/** validateCopper that throws (message lists the first errors); returns the document for chaining. */
export function assertCopper(copper) {
  const errors = validateCopper(copper);
  if (errors.length) {
    const more = errors.length > 10 ? `\n... and ${errors.length - 10} more` : '';
    throw new Error(`invalid ${SCHEMA_ID} document:\n${errors.slice(0, 10).join('\n')}${more}`);
  }
  return copper;
}
