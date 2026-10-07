// boarddd/model: the normalised board model (boarddd/board@1) that server-side readers write as
// board.json and the renderers read. The schema is generated from python/src/boarddd/model.py
// (schema/board.schema.json; ./schema.js is the same object as a module). validateBoard is a small
// checker for the schema subset the generator emits plus the model rules a schema can't express; it
// mirrors python/src/boarddd/validate.py message for message (fixtures/model/ keeps them in step).

import { check, ptr } from './check.js';
import SCHEMA from './schema.js';

export const SCHEMA_ID = 'boarddd/board@1';
export { SCHEMA };

/** Model rules beyond the schema (run only on schema-valid documents). */
function rules(b, errors) {
  const comps = b.components || [];
  const seen = new Map();
  comps.forEach((c, i) => {
    if (seen.has(c.ref)) errors.push(`/components/${i}/ref: duplicate reference '${c.ref}' (also /components/${seen.get(c.ref)})`);
    else seen.set(c.ref, i);
  });
  const fps = b.footprints || {};
  if (Object.keys(fps).length) {
    comps.forEach((c, i) => {
      if (c.footprint != null && !(c.footprint in fps)) errors.push(`/components/${i}/footprint: '${c.footprint}' is not in /footprints`);
    });
  }
  for (const [key, fp] of Object.entries(fps)) {
    if (fp.name !== key) errors.push(`${ptr('/footprints', key)}/name: must equal its key '${key}'`);
  }
  const ids = new Map();
  (b.layers || []).forEach((l, i) => {
    if (ids.has(l.id)) errors.push(`/layers/${i}/id: duplicate layer id '${l.id}' (also /layers/${ids.get(l.id)})`);
    else ids.set(l.id, i);
  });
  (b.drills || []).forEach((d, i) => {
    if ((d.x2 == null) !== (d.y2 == null)) errors.push(`/drills/${i}: x2 and y2 must both be set (slot) or both be null (round hole)`);
    if (d.layer != null && !ids.has(d.layer)) errors.push(`/drills/${i}/layer: '${d.layer}' is not a layer id`);
  });
  const st = b.stackup || {};
  const copper = (st.layers || []).filter((x) => x.kind === 'copper');
  if (copper.length && st.copper_layers != null && st.copper_layers !== copper.length) {
    errors.push(`/stackup/copper_layers: ${st.copper_layers} but /stackup/layers has ${copper.length} copper layers`);
  }
}

/** 'json/pointer: message' errors; empty when `board` is a valid boarddd/board@1 document. */
export function validateBoard(board) {
  const errors = [];
  check(SCHEMA, board, '', SCHEMA, errors);
  if (!errors.length) rules(board, errors);
  return errors;
}

/** validateBoard that throws (message lists the first errors); returns the board for chaining. */
export function assertBoard(board) {
  const errors = validateBoard(board);
  if (errors.length) {
    const more = errors.length > 10 ? `\n... and ${errors.length - 10} more` : '';
    throw new Error(`invalid ${SCHEMA_ID} board:\n${errors.slice(0, 10).join('\n')}${more}`);
  }
  return board;
}

/** Board frame (y up) -> KiCad frame (y down), and back: the model stores the board frame. */
export const toKicad = ([x, y]) => [x, -y];
export const toBoard = ([x, y]) => [x, -y];

/**
 * Where a footprint-frame point (KiCad footprint coordinates, y down, library orientation) lands on
 * the board (board frame) for a component: p = c + R(rotation) * q, with q = (fx, -fy) on the top and
 * q = (fx, fy) on the bottom (KiCad's flip mirrors the footprint across its x axis).
 */
export function footprintToBoard(component, [fx, fy]) {
  const qx = fx, qy = component.side === 'bottom' ? fy : -fy;
  const a = (component.rotation * Math.PI) / 180;
  const c = Math.cos(a), s = Math.sin(a);
  return [component.x + c * qx - s * qy, component.y + s * qx + c * qy];
}
