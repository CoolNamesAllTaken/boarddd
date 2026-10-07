// src/scene/views.js: presets, box fitting, clip planes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { VIEWS, fitCamera, clipPlanes } from '../../src/scene/views.js';

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} vs ${b}`);
const BOARD = { min: [0, 0, 0], max: [100, 50, 1.6] };

test('top view: camera above the board middle, y up the screen, the board width just fits', () => {
  const f = fitCamera(BOARD, VIEWS.top, 30, 2, 1);
  assert.deepEqual(f.target, [50, 25, 0.8]);
  near(f.position[0], 50); near(f.position[1], 25);
  assert.ok(f.position[2] > 1.6);
  assert.deepEqual(f.up.map((v) => Math.round(v * 1e9) / 1e9 + 0), [0, 1, 0]);
  // Half-width 50 over tan(hfov/2) (aspect 2) plus the half depth.
  near(f.distance, 50 / (Math.tan(Math.PI / 12) * 2) + 0.8);
});

test('a tall viewport is height-limited on the other axis', () => {
  const f = fitCamera(BOARD, VIEWS.top, 30, 0.5, 1);
  near(f.distance, 50 / (Math.tan(Math.PI / 12) * 0.5) + 0.8);
});

test('bottom view keeps y up (mirrored left-right), iso is above and in front', () => {
  const b = fitCamera(BOARD, VIEWS.bottom, 30, 1);
  assert.ok(b.position[2] < 0);
  near(b.up[1], 1);
  const i = fitCamera(BOARD, VIEWS.iso, 30, 1);
  assert.ok(i.position[2] > 0 && i.position[1] < 25 && i.position[0] > 50);
  assert.equal(VIEWS.side, VIEWS.front);
});

test('clip planes hug the content sphere from where the camera is', () => {
  const p = clipPlanes([0, 0, 100], { center: [0, 0, 0], radius: 10 });
  near(p.far, 110.5);
  near(p.near, 89.5);
  // Inside the sphere: near falls back to a fraction of far, never zero.
  const q = clipPlanes([0, 0, 1], { center: [0, 0, 0], radius: 10 });
  near(q.near, q.far / 2000);
});
