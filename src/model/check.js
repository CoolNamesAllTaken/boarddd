// The JSON Schema subset python/src/boarddd/_codegen.py emits, checked the way python/src/boarddd/validate.py
// does (same messages): shared by boarddd/model (board@1) and boarddd/copper (copper@1). Internal module.

const fmt = (v) => (Array.isArray(v) ? `[${v.map((x) => JSON.stringify(x)).join(', ')}]` : JSON.stringify(v));
export const ptr = (path, key) => `${path}/${String(key).replace(/~/g, '~0').replace(/\//g, '~1')}`;
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

export function check(s, v, path, root, errors) {
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
