// Hit-testing of boarddd/view2d: shape distances and the pick rule (gentoo's padIndex).
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHitIndex, segmentShape, rectShape, circleShape, polygonShape } from '../../src/view2d/hit.js';

const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

test('shape distances: 0 inside, edge distance outside', () => {
  const seg = segmentShape(0, 0, 10, 0, 0.5);
  close(seg.distance(5, 0.2), 0);
  close(seg.distance(5, 1.25), 1);
  close(seg.distance(13, 4), 4.75);
  const r = rectShape(0, 0, 2, 2);
  close(r.distance(0.9, -0.9), 0);
  close(r.distance(4, 5), 5);
  close(circleShape(1, 1, 2).distance(4, 5), 4);
  const tri = polygonShape([[0, 0], [10, 0], [0, 10]]);
  close(tri.distance(2, 2), 0);
  close(tri.distance(-3, 5), 3);
  close(tri.area, 50);
});

test('pick: smallest shape under the point; a near small one beats background', () => {
  const ground = rectShape(50, 50, 100, 100, 'ground');
  const pad = rectShape(10, 10, 1, 1, 'pad');
  const via = circleShape(10.5, 10.5, 0.3, 'via');
  const idx = createHitIndex([ground, pad, via]);
  assert.equal(idx.at(10.5, 10.5).data, 'via'); // via sits on the pad
  assert.equal(idx.at(9.8, 9.8).data, 'pad');
  assert.equal(idx.at(9.3, 10, 0.25).data, 'pad'); // 0.2 mm off the pad, on the ground pour
  assert.equal(idx.at(9.3, 10, 0.1).data, 'ground');
  assert.equal(idx.at(200, 200, 1), null);
  assert.deepEqual(idx.all(10.5, 10.5).map((s) => s.data), ['via', 'pad', 'ground']);
});

test('pick a track ("select a line") among many, through the grid', () => {
  const tracks = [];
  for (let i = 0; i < 2000; i++) tracks.push(segmentShape(0, i * 0.5, 100, i * 0.5, 0.2, i));
  const idx = createHitIndex(tracks);
  assert.equal(idx.at(37.2, 250.05).data, 500);
  assert.equal(idx.at(37.2, 250.22, 0.1), null); // 0.12 mm from 500's edge, 0.18 from 501's
  assert.equal(idx.at(37.2, 250.22, 0.15).data, 500);
  assert.equal(idx.at(37.2, 250.22, 0.3).data, 500); // both in reach: the nearest
  assert.equal(idx.at(37.2, 250.36, 0.3).data, 501);
});
