// Ink diff of boarddd/view2d (ported from kipr's inkdiff tests).
import test from 'node:test';
import assert from 'node:assert/strict';
import { inkMask, alphaMask, dilate, diffMasks, paintDiff, regions, inkDiff } from '../../src/view2d/inkdiff.js';

function rgba(w, h, inkAt) {
  const d = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) d.set(inkAt(i % w, Math.floor(i / w)) ? [0, 0, 0, 255] : [255, 255, 255, 255], i * 4);
  return d;
}

test('white paper is not ink, dark opaque is', () => {
  assert.deepEqual([...inkMask(rgba(4, 1, (x) => x === 2), 4, 1)], [0, 0, 1, 0]);
  assert.deepEqual([...inkMask(new Uint8ClampedArray([0, 0, 0, 10, 0, 0, 0, 255]), 2, 1)], [0, 1]);
  assert.deepEqual([...alphaMask(new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 0]), 2, 1)], [1, 0]);
});

test('dilate is a square of radius r, clipped at the edges', () => {
  const m = new Uint8Array(25); m[12] = 1;
  assert.equal(dilate(m, 5, 5, 1).reduce((a, b) => a + b, 0), 9);
  const e = new Uint8Array(25); e[0] = 1;
  assert.equal(dilate(e, 5, 5, 2).reduce((a, b) => a + b, 0), 9);
  assert.deepEqual([...dilate(m, 5, 5, 0)], [...m]);
});

test('classify removed / added / common with tolerance', () => {
  const w = 20; const h = 1;
  const base = inkMask(rgba(w, h, (x) => x === 2 || x === 10), w, h);
  const head = inkMask(rgba(w, h, (x) => x === 3 || x === 16), w, h);
  const d = diffMasks(base, head, w, h, 1);
  assert.deepEqual(d.counts, { removed: 1, added: 1, common: 2 });
  assert.deepEqual(diffMasks(base, head, w, h, 0).counts, { removed: 2, added: 2, common: 0 });
  const out = paintDiff(new Uint8ClampedArray(w * 4), d);
  assert.deepEqual([...out.slice(40, 44)], [225, 40, 40, 255]);
  assert.deepEqual([...out.slice(64, 68)], [30, 175, 70, 255]);
  assert.equal(out[3], 0);
});

test('regions merge nearby pixels and drop noise', () => {
  const w = 40; const h = 20;
  const m = new Uint8Array(w * h);
  const set = (x, y) => { m[y * w + x] = 1; };
  for (let x = 2; x < 6; x++) for (let y = 2; y < 4; y++) set(x, y);
  for (let x = 8; x < 10; x++) for (let y = 2; y < 4; y++) set(x, y);
  for (let x = 30; x < 34; x++) for (let y = 10; y < 14; y++) set(x, y);
  set(20, 18);
  const r = regions(m, w, h, { gap: 4, minPixels: 3 });
  assert.deepEqual(r, [{ x: 2, y: 2, w: 8, h: 2, pixels: 12 }, { x: 30, y: 10, w: 4, h: 4, pixels: 16 }]);
});

test('inkDiff: one call, a missing side is all added / removed', () => {
  const w = 10; const h = 10;
  const head = rgba(w, h, (x, y) => x >= 4 && x < 7 && y >= 4 && y < 7);
  const d = inkDiff(null, head, w, h, { gap: 2, minPixels: 1 });
  assert.deepEqual(d.counts, { removed: 0, added: 9, common: 0 });
  assert.deepEqual(d.regions, [{ x: 4, y: 4, w: 3, h: 3, pixels: 9 }]);
  assert.deepEqual([...d.rgba.slice((5 * w + 5) * 4, (5 * w + 5) * 4 + 4)], [30, 175, 70, 255]);
});
