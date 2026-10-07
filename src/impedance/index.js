// boarddd/impedance: PCB transmission-line impedance (docs/impedance.md). Tier 1 is closed-form; tier 2
// (fieldsolver.js) is a 2D field solver for any cross-section; stackup.js connects both to the board model's
// stackup and impedance targets; route.js analyses a net's route on a real board (boarddd/copper@1) section by
// section into a boarddd/impedance@1 document.
import { check } from '../model/check.js';
import IMPEDANCE_SCHEMA from './schema.js';

export * from './closedform.js';
export * from './stackup.js';
export { solveCrossSection, sectionFor, fieldCalculate } from './fieldsolver.js';
export { analyzeNet, netRoute, ROUTE_DEFAULTS, IMPEDANCE_SCHEMA_ID } from './route.js';
export { IMPEDANCE_SCHEMA };

/**
 * 'json/pointer: message' errors; empty when `doc` is a valid boarddd/impedance@1 document (the schema, plus:
 * a pair has two nets, sections belong to the document's nets and run forwards). As python's
 * boarddd.validate.validate_impedance.
 */
export function validateImpedance(doc) {
  const errors = [];
  check(IMPEDANCE_SCHEMA, doc, '', IMPEDANCE_SCHEMA, errors);
  if (errors.length) return errors;
  if (doc.kind === 'differential' && doc.nets.length !== 2) errors.push('/nets: a differential analysis has two nets');
  doc.sections.forEach((s, i) => {
    if (!doc.nets.includes(s.net)) errors.push(`/sections/${i}/net: '${s.net}' is not in /nets`);
    if (s.s1 < s.s0) errors.push(`/sections/${i}/s1: must be >= s0`);
  });
  return errors;
}
