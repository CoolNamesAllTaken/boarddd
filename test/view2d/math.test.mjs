// Pan / zoom maths of boarddd/view2d (ported kipr panzoom tests plus the y-up / mirrored frame).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  fitBounds, toScreen, toWorld, zoomAt, panBy, visibleBounds, regionOf, viewForRegion, viewForBox,
  rasterMatrix, worldMatrix, stepScale, budgetScale, rasterRect, intersect, grow, contains,
} from '../../src/view2d/math.js';

const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);
const B = { minX: 10, maxX: 110, minY: -20, maxY: 30 }; // 100 x 50 mm

test('fit centres the bounds and leaves the padding on the limiting side', () => {
  const v = fitBounds(B, 800, 600, 0.05);
  assert.deepEqual([v.cx, v.cy], [60, 5]);
  close(v.s, 8 * 0.9);
  const [x0, y0] = toScreen(v, 800, 600, B.minX, B.maxY);
  const [x1, y1] = toScreen(v, 800, 600, B.maxX, B.minY);
  close(x0, 40); close(x1, 760); // 5 % of 800 each side
  close(y1 - y0, 50 * v.s);
  close((y0 + y1) / 2, 300);
});

test('screen and world round trip, y up, mirrored x', () => {
  const v = { cx: 3, cy: -4, s: 7.5 };
  for (const flip of [false, true]) {
    for (const [x, y] of [[0, 0], [12.5, -3], [-100, 44]]) {
      const [px, py] = toScreen(v, 640, 480, x, y, flip);
      const [bx, by] = toWorld(v, 640, 480, px, py, flip);
      close(bx, x, 1e-9); close(by, y, 1e-9);
    }
  }
  // y up: a larger world y is higher on screen; flip mirrors x about the centre
  assert.ok(toScreen(v, 640, 480, 3, 0)[1] < toScreen(v, 640, 480, 3, -4)[1]);
  close(toScreen(v, 640, 480, 4, -4, true)[0], 320 - 7.5);
});

test('zoom keeps the anchor point fixed (also mirrored) and clamps', () => {
  const v = { cx: 0, cy: 0, s: 2 };
  for (const flip of [false, true]) {
    const before = toWorld(v, 500, 400, 120, 330, flip);
    const z = zoomAt(v, 500, 400, 120, 330, 3.5, flip);
    close(z.s, 7);
    const after = toWorld(z, 500, 400, 120, 330, flip);
    close(after[0], before[0], 1e-9); close(after[1], before[1], 1e-9);
  }
  assert.equal(zoomAt(v, 500, 400, 0, 0, 1e9, false, 0.1, 50).s, 50);
  assert.equal(zoomAt(v, 500, 400, 0, 0, 1e-9, false, 0.1, 50).s, 0.1);
});

test('pan moves the world with the pointer', () => {
  for (const flip of [false, true]) {
    const v = { cx: 5, cy: 5, s: 4 };
    const p = toScreen(v, 300, 200, 7, 9, flip);
    const w = panBy(v, 25, -10, flip);
    const q = toScreen(w, 300, 200, 7, 9, flip);
    close(q[0] - p[0], 25); close(q[1] - p[1], -10);
  }
});

test('region of a view round trips and does not depend on pane height', () => {
  const v = { cx: 124.76, cy: -88.68, s: 13.3 };
  const r = regionOf(v, 900);
  close(r.w, 900 / 13.3);
  const back = viewForRegion(r, 900);
  close(back.s, v.s); close(back.cx, v.cx); close(back.cy, v.cy);
  // the same region in a narrower pane: same centre and width on show
  const half = viewForRegion(r, 450);
  const vb = visibleBounds(half, 450, 100);
  close(vb.maxX - vb.minX, r.w); close((vb.minX + vb.maxX) / 2, r.cx);
});

test('zoom to a box: small boxes get context', () => {
  const v = viewForBox({ minX: 0, maxX: 1, minY: 0, maxY: 1 }, 400, 400, { minMm: 8, pad: 0 });
  close(v.s, 50);
  assert.deepEqual([v.cx, v.cy], [0.5, 0.5]);
});

test('a raster tile and an overlay land on the same screen pixels', () => {
  const v = { cx: 40, cy: 10, s: 6 };
  const rect = { minX: 20, maxX: 60, minY: -5, maxY: 25 };
  const r = 16;
  for (const flip of [false, true]) {
    const [a, , , d, e, f] = rasterMatrix(v, 700, 500, rect, r, flip);
    const [wa, , , wd, we, wf] = worldMatrix(v, 700, 500, flip);
    for (const [x, y] of [[20, 25], [33.3, 0], [60, -5]]) {
      const u = (x - rect.minX) * r; const w = (rect.maxY - y) * r; // raster pixel of the point
      const [sx, sy] = toScreen(v, 700, 500, x, y, flip);
      close(a * u + e, sx, 1e-9); close(d * w + f, sy, 1e-9);
      close(wa * x + we, sx, 1e-9); close(wd * y + wf, sy, 1e-9);
    }
  }
});

test('render resolution: sqrt(2) steps, budget, whole pixels', () => {
  assert.equal(stepScale(4), 4);
  close(stepScale(4.1), 4 * Math.SQRT2, 1e-9);
  assert.equal(stepScale(4, 2), 8);
  assert.equal(stepScale(0.01, 1, 1), 1);
  close(budgetScale(100, 50, { maxEdge: 4096, maxPixels: 16e6 }), 40.96);
  close(budgetScale(1000, 1000, { maxEdge: 1e9, maxPixels: 1e6 }), 1);
  const { rect, width, height } = rasterRect({ minX: 0, maxX: 10.05, minY: 0, maxY: 3 }, 10);
  assert.deepEqual([width, height], [101, 30]);
  close(rect.maxX - rect.minX, 10.1); close(rect.maxY, 3); close(rect.minY, 0);
});

test('bounds helpers', () => {
  assert.equal(intersect(B, { minX: 200, maxX: 300, minY: 0, maxY: 1 }), null);
  assert.deepEqual(intersect(B, { minX: 100, maxX: 300, minY: 0, maxY: 100 }), { minX: 100, maxX: 110, minY: 0, maxY: 30 });
  assert.deepEqual(grow({ minX: 0, maxX: 10, minY: 0, maxY: 4 }, 0.5), { minX: -5, maxX: 15, minY: -2, maxY: 6 });
  assert.ok(contains(B, { minX: 10, maxX: 20, minY: 0, maxY: 30 }));
  assert.ok(!contains(B, { minX: 9, maxX: 20, minY: 0, maxY: 30 }));
});
