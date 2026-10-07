// Filling holes (VIPPO) and the drill size list, under node.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fillHoles, drillSizes, usableHoles, rectLoop } from '../../src/geom/index.js';

const HOLES = [
  { x: 1, y: 1, diameter: 0.3, plated: true, via: true },
  { x: 2, y: 1, diameter: 0.3, plated: true, via: true },
  { x: 3, y: 1, diameter: 0.6, plated: true },
  { x: 4, y: 1, diameter: 0.3, plated: false },                     // NPTH: never filled
  { x: 5, y: 1, diameter: 0.3, plated: true, x2: 5, y2: 2 },        // slot: never filled
  { x: 6, y: 1, diameter: 1.0, plated: true },
];

test('fillHoles: plated round holes up to the drill size, inclusive; nothing else', () => {
  const filled = fillHoles(HOLES, 0.6);
  assert.deepEqual(filled.map((h) => !!h.filled), [true, true, true, false, false, false]);
  assert.equal(HOLES[0].filled, undefined, 'the input is left alone');
  assert.deepEqual(fillHoles(HOLES, 0.3).map((h) => !!h.filled), [true, true, false, false, false, false]);
  for (const off of [null, 0, -1, undefined]) assert.ok(fillHoles(HOLES, off).every((h) => !h.filled));
});

test('fillHoles: filled holes are not punched (usableHoles)', () => {
  const outline = { board: rectLoop(0, 0, 10, 5), cutouts: [] };
  assert.equal(usableHoles(fillHoles(HOLES, 0.6), outline).kept.length, 3);
  assert.equal(usableHoles(HOLES, outline).kept.length, 6);
});

test('drillSizes: plated round sizes, smallest first, with via counts', () => {
  assert.deepEqual(drillSizes(HOLES), [
    { diameter: 0.3, count: 2, vias: 2 },
    { diameter: 0.6, count: 1, vias: 0 },
    { diameter: 1, count: 1, vias: 0 },
  ]);
  assert.deepEqual(drillSizes([{ x: 0, y: 0, d: 0.29999999, plated: true }]), [{ diameter: 0.3, count: 1, vias: 0 }]);
});
