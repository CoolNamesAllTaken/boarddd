// boarddd/impedance: PCB transmission-line impedance (docs/impedance.md). Tier 1 is closed-form; stackup.js
// connects it to the board model's stackup and impedance targets. The tier-2 field solver will live next to it.
export * from './closedform.js';
export * from './stackup.js';
