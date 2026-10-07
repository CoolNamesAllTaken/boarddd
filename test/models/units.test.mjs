// src/models/units.js: up axis, units, STEP colour space.
import test from 'node:test';
import assert from 'node:assert/strict';
import { detectUp, detectScale, stepColorToLinear } from '../../src/models/units.js';

test('detectUp: thinnest axis, or what is stated', () => {
  assert.equal(detectUp([0.05, 0.0016, 0.04]), 'y');      // kicad-cli GLB: metres, y up
  assert.equal(detectUp([50, 40, 1.6]), 'z');            // STEP / KiCad: z up
  assert.equal(detectUp([1.6, 50, 40]), 'x');
  assert.equal(detectUp([50, 40, 1.6], '+y'), 'y');
});

test('detectScale: stated unit, then the known board size, then the metre heuristic', () => {
  assert.equal(detectScale(0.05, { units: 'mm' }), 1);
  assert.equal(detectScale(0.05, { units: 'in' }), 25.4);
  assert.equal(detectScale(0.0512, { expectedMm: 51 }), 1000);
  assert.equal(detectScale(2.01, { expectedMm: 51 }), 25.4);
  assert.equal(detectScale(5.1, { expectedMm: 51 }), 10);
  assert.equal(detectScale(51, { expectedMm: 51 }), 1);
  assert.equal(detectScale(0.08), 1000);
  assert.equal(detectScale(80), 1);
});

test('stepColorToLinear re-encodes occt linear RGB to the file values (RP2040-Zero blue)', () => {
  const out = stepColorToLinear([0.008568125776946545, 0.040915198624134064, 0.14702726900577545]);
  const want = [0.09, 0.224, 0.42];
  out.forEach((v, i) => assert.ok(Math.abs(v - want[i]) < 0.002, `${v} vs ${want[i]}`));
  assert.equal(stepColorToLinear(null), null);
});
