// src/models/step.js: three objects from occt's result (no occt needed: the result is made up).
import test from 'node:test';
import assert from 'node:assert/strict';
import { stepToObject } from '../../src/models/step.js';

// One triangle per node, far from the origin, two nodes of the same colour.
function data() {
  const tri = (x, y, z) => ({
    name: '', color: [0.5, 0.5, 0.5], faces: new Int32Array(0), faceColors: [], normal: null,
    position: new Float32Array([x, y, z, x + 1, y, z, x, y + 1, z + 2]), index: new Uint32Array([0, 1, 2]),
  });
  return {
    root: { name: 'board', meshes: [], children: [{ name: 'R1', meshes: [0], children: [] }, { name: 'R2', meshes: [1], children: [] }] },
    meshes: [tri(100, 50, 1), tri(110, 50, 1)], triangles: 2,
  };
}

test('stepToObject: groups at their box middle by default', () => {
  const g = stepToObject(data());
  assert.equal(g.children.length, 2);
  assert.deepEqual(g.children[0].position.toArray(), [100.5, 50.5, 2]);
  assert.equal(g.children[0].children[0].geometry.attributes.position.getX(0), -0.5);
});

test('stepToObject center: false keeps groups at the origin and vertices absolute', () => {
  const g = stepToObject(data(), { center: false });
  assert.deepEqual(g.children[0].position.toArray(), [0, 0, 0]);
  assert.equal(g.children[1].children[0].geometry.attributes.position.getX(0), 110);
  // Shared per colour, as documented.
  assert.equal(g.children[0].children[0].material, g.children[1].children[0].material);
});
