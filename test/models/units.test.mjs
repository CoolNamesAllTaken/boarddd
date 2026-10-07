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

test('stepToObject leaves readStep data alone: a second object from the same (cached) data lands in the same place', async () => {
  const THREE = await import('three');
  const { stepToObject } = await import('../../src/models/step.js');
  const data = {
    root: { name: 'part', meshes: [0], children: [] },
    meshes: [{ name: 'body', color: [0.1, 0.2, 0.4], position: new Float32Array([10, 0, 0, 12, 0, 0, 10, 2, 1]), normal: null,
      index: new Uint32Array([0, 1, 2]), faces: new Int32Array(0), faceColors: [] }],
    triangles: 1,
  };
  const before = Array.from(data.meshes[0].position);
  const boxes = [stepToObject(data), stepToObject(data)].map((o) => new THREE.Box3().setFromObject(o));
  assert.deepEqual(Array.from(data.meshes[0].position), before);
  for (const b of boxes) {
    assert.deepEqual(b.min.toArray(), [10, 0, 0]);
    assert.deepEqual(b.max.toArray(), [12, 2, 1]);
  }
  // the part's node sits at its box middle (for matching), as before
  assert.deepEqual(stepToObject(data).children[0].position.toArray(), [11, 1, 0.5]);
});
