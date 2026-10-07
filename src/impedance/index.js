// boarddd/impedance: PCB transmission-line impedance (docs/impedance.md). Tier 1 is closed-form; tier 2
// (fieldsolver.js) is a 2D field solver for any cross-section; stackup.js connects both to the board model's
// stackup and impedance targets.
export * from './closedform.js';
export * from './stackup.js';
export { solveCrossSection, sectionFor, fieldCalculate } from './fieldsolver.js';
