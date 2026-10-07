import test from 'node:test';
import assert from 'node:assert/strict';
import { kicadToBoard, boardToKicad, kicadModelMatrix, applyMatrix } from '../../src/geom/index.js';

const near = (a, b, eps = 1e-9) => a.every((v, i) => Math.abs(v - b[i]) < eps);

test('KiCad y-down <-> board y-up', () => {
  assert.deepEqual(kicadToBoard(1, 2), [1, -2]);
  assert.deepEqual(boardToKicad(...kicadToBoard(3, -4)), [3, -4]);
});

// from kipr tests/library/viewer/unit.test.mjs
test('model matrix: KiCad 3D-viewer order and signs', () => {
  assert.ok(near(applyMatrix(kicadModelMatrix(), [1, 2, 3]), [1, 2, 3]));
  assert.ok(near(applyMatrix(kicadModelMatrix({ rotate: [0, 0, 90] }), [1, 0, 0]), [0, -1, 0]));
  const b = kicadModelMatrix({ rotate: [90, 180, 0], offset: [0, -2, 5] });
  assert.ok(near(applyMatrix(b, [0, 0, 0]), [0, -2, 5]));
});
