// boarddd/model: the normalised board model (boarddd/board@1) that server-side readers write as
// board.json and the renderers read. The schema is generated from python/src/boarddd/model.py
// (schema/board.schema.json; ./schema.js is the same object as a module). validateBoard is a small
// checker for the schema subset the generator emits plus the model rules a schema can't express; it
// mirrors python/src/boarddd/validate.py message for message (fixtures/model/ keeps them in step).

import SCHEMA from './schema.js';

export const SCHEMA_ID = 'boarddd/board@1';
export { SCHEMA };

const fmt = (v) => (Array.isArray(v) ? `[${v.map((x) => JSON.stringify(x)).join(', ')}]` : JSON.stringify(v));
const ptr = (path, key) => `${path}/${String(key).replace(/~/g, '~0').replace(/\//g, '~1')}`;
const at = (path) => path || '/';
const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

function typeOk(t, v) {
  switch (t) {
    case 'null': return v === null;
    case 'boolean': return typeof v === 'boolean';
    case 'integer': return Number.isInteger(v);
    case 'number': return typeof v === 'number' && Number.isFinite(v);
    case 'string': return typeof v === 'string';
    case 'array': return Array.isArray(v);
    case 'object': return isObj(v);
    default: throw new Error(`unknown type ${t}`);
  }
}

function check(s, v, path, root, errors) {
  if (s.$ref) check(root.$defs[s.$ref.replace('#/$defs/', '')], v, path, root, errors);
  if (s.anyOf) {
    const results = [];
    let ok = false;
    for (const b of s.anyOf) {
      const errs = [];
      check(b, v, path, root, errs);
      if (!errs.length) { ok = true; break; }
      results.push(errs);
    }
    if (!ok) {
      // nullable: report the non-null branch's errors (more useful than "matches nothing")
      const nonNull = results.filter((_, i) => !(s.anyOf[i].type === 'null' && Object.keys(s.anyOf[i]).length === 1));
      errors.push(...(nonNull.length === 1 ? nonNull[0] : [`${at(path)}: matches none of the allowed shapes`]));
    }
  }
  if ('const' in s && v !== s.const) { errors.push(`${at(path)}: must be ${fmt(s.const)}`); return; }
  if (s.type) {
    const types = Array.isArray(s.type) ? s.type : [s.type];
    if (!types.some((t) => typeOk(t, v))) { errors.push(`${at(path)}: expected ${types.join(' or ')}`); return; }
  }
  if (s.enum && !s.enum.includes(v)) { errors.push(`${at(path)}: must be one of ${fmt(s.enum)}`); return; }
  if (typeof v === 'string' && s.pattern && !new RegExp(s.pattern).test(v)) errors.push(`${at(path)}: does not match ${s.pattern}`);
  if (typeof v === 'number') {
    if ('minimum' in s && v < s.minimum) errors.push(`${at(path)}: must be >= ${fmt(s.minimum)}`);
    if ('maximum' in s && v > s.maximum) errors.push(`${at(path)}: must be <= ${fmt(s.maximum)}`);
    if ('exclusiveMinimum' in s && v <= s.exclusiveMinimum) errors.push(`${at(path)}: must be > ${fmt(s.exclusiveMinimum)}`);
  }
  if (Array.isArray(v)) {
    if ('minItems' in s && v.length < s.minItems) errors.push(`${at(path)}: must have at least ${s.minItems} items`);
    if ('maxItems' in s && v.length > s.maxItems) errors.push(`${at(path)}: must have at most ${s.maxItems} items`);
    const prefix = s.prefixItems || [];
    v.forEach((item, i) => {
      if (i < prefix.length) check(prefix[i], item, ptr(path, i), root, errors);
      else if (isObj(s.items)) check(s.items, item, ptr(path, i), root, errors);
    });
  }
  if (isObj(v)) {
    const props = s.properties || {};
    for (const name of s.required || []) if (!(name in v)) errors.push(`${at(path)}: missing required property '${name}'`);
    const extra = 'additionalProperties' in s ? s.additionalProperties : true;
    for (const [k, item] of Object.entries(v)) {
      if (k in props) check(props[k], item, ptr(path, k), root, errors);
      else if (extra === false) errors.push(`${at(path)}: unknown property '${k}'`);
      else if (isObj(extra)) check(extra, item, ptr(path, k), root, errors);
    }
  }
}

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
