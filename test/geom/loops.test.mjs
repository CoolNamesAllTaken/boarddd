// Loops, stadium slots and the hole budget. The stadium checks are kipr PR #18's
// (tests/library/viewer/unit.test.mjs, tests/web-3d/boardgeom.test.mjs); the hole-budget checks are
// kipr's boardgeom tests, themselves from gentoo's viewer3d.js rules.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  slotPoints, ringPoints, loopAt, segmentsFor, signedArea2, counterClockwise, clockwise, isClockwise,
  clearance, pointToSegment, loopBounds, usableHoles, HOLE_BUDGET, rectOutline, outlinesDiffer, strokeLoops,
  padDrillSlot, padDrillLoop,
} from '../../src/geom/index.js';

const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const OUTLINE = { board: rect(0, -50, 100, 0), cutouts: [rect(40, -30, 60, -20)] };
// A ring's straight flanks: edges longer than any cap chord, as [length, direction deg mod 180].
const flanks = (ring) => ring.map((p, i) => [p, ring[(i + 1) % ring.length]])
  .map(([[x1, y1], [x2, y2]]) => [Math.hypot(x2 - x1, y2 - y1), ((Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI + 360) % 180])
  .filter(([len]) => len > 0.5);
const toSeg = ([x, y], [ax, ay], [bx, by]) => pointToSegment(x, y, ax, ay, bx, by);

test('slotPoints: a stadium, every point on the radius around the centre segment, straight flanks', () => {
  const ring = slotPoints(-0.5, 0, 0.5, 0, 0.5, 24);
  for (const q of ring) assert.ok(Math.abs(toSeg(q, [-0.5, 0], [0.5, 0]) - 0.5) < 1e-12, `${q}`);
  const f = flanks(ring);
  assert.equal(f.length, 2);
  for (const [len, dir] of f) { assert.ok(Math.abs(len - 1) < 1e-12); assert.ok(dir < 1e-9 || Math.abs(dir - 180) < 1e-9); }
  // the stadium reaches its corners (+-0.5, +-0.5); a 2 x 1 ellipse would not
  assert.ok(ring.some(([x, y]) => Math.abs(x - 0.5) < 1e-12 && Math.abs(y - 0.5) < 1e-12));
  assert.ok(ring.some(([x, y]) => Math.abs(x + 0.5) < 1e-12 && Math.abs(y + 0.5) < 1e-12));
  assert.ok(!isClockwise(ring));
  // coincident ends: a circle
  const c = slotPoints(1, 1, 1, 1, 0.4, 24);
  assert.equal(c.length, 24);
  for (const [x, y] of c) assert.ok(Math.abs(Math.hypot(x - 1, y - 1) - 0.4) < 1e-12);
});

test('slotPoints: a 1.0 x 2.0 mm slot is a stadium (flanks 1.0 mm, ends r 0.5 mm) at any angle', () => {
  for (const deg of [0, 45, 90, 135]) {
    const a = (deg * Math.PI) / 180;
    const e = [[5 - 0.5 * Math.cos(a), 5 - 0.5 * Math.sin(a)], [5 + 0.5 * Math.cos(a), 5 + 0.5 * Math.sin(a)]];
    const loop = loopAt(e, 0.5);
    for (const q of loop) assert.ok(Math.abs(toSeg(q, ...e) - 0.5) < 1e-9);
    const f = flanks(loop);
    assert.equal(f.length, 2);
    for (const [len, dir] of f) {
      assert.ok(Math.abs(len - 1) < 1e-9);
      assert.ok(Math.abs(Math.sin(((dir - deg) * Math.PI) / 180)) < 1e-9, `flank along the slot at ${deg} deg`);
    }
  }
});

test('oval pad drill 1.0 x 2.0 mm: flank 1.0, end radius 0.5, long axis along the larger size, turned with the pad', () => {
  const check = (pad, axisDeg) => {
    const { ends, radius } = padDrillSlot(pad);
    assert.ok(Math.abs(radius - 0.5) < 1e-12);
    const [[x1, y1], [x2, y2]] = ends;
    assert.ok(Math.abs(Math.hypot(x2 - x1, y2 - y1) - 1) < 1e-12);
    const ring = padDrillLoop(pad, { segments: 32 });   // board frame: y up
    const [b1, b2] = ends.map(([x, y]) => [x, -y]);
    for (const q of ring) assert.ok(Math.abs(toSeg(q, b1, b2) - 0.5) < 1e-9);
    const f = flanks(ring);
    assert.equal(f.length, 2, JSON.stringify(f));
    for (const [len, dir] of f) {
      assert.ok(Math.abs(len - 1) < 1e-9);
      const off = Math.abs((((dir - axisDeg) % 180) + 180) % 180);
      assert.ok(off < 1e-6 || Math.abs(off - 180) < 1e-6, `flank at ${dir} deg, want ${axisDeg}`);
    }
    const a = (axisDeg * Math.PI) / 180;
    const span = (v) => Math.max(...v) - Math.min(...v);
    assert.ok(Math.abs(span(ring.map(([x, y]) => x * Math.cos(a) + y * Math.sin(a))) - 2) < 1e-9);
    assert.ok(Math.abs(span(ring.map(([x, y]) => -x * Math.sin(a) + y * Math.cos(a))) - 1) < 1e-9);
  };
  const oval = (w, h, rot) => ({ at: [3, -2, rot], size: [w + 0.6, h + 0.6], shape: 'oval', layers: ['*.Cu'], drill: { shape: 'oval', size: [w, h] } });
  check(oval(2, 1, 0), 0);
  check(oval(1, 2, 0), 90);
  check(oval(2, 1, 90), 90);
  check(oval(1, 2, 90), 0);
  check(oval(2, 1, 45), 45);    // KiCad turns CCW on screen; the board frame is y up, so +45 stays +45
  check(oval(1, 2, 45), 135);
  const round = padDrillLoop({ at: [1, 2, 30], drill: { shape: 'circle', size: [0.8, 0.8] } });
  for (const [x, y] of round) assert.ok(Math.abs(Math.hypot(x - 1, y + 2) - 0.4) < 1e-12);
  assert.ok(Math.abs(padDrillSlot(oval(2, 1, 0), 0.1).radius - 0.6) < 1e-12);
  assert.equal(padDrillLoop({ drill: null }), null);
});

test('winding helpers and segment counts', () => {
  const sq = rect(0, 0, 1, 1);
  assert.equal(signedArea2(sq), 2);
  assert.ok(!isClockwise(sq));
  assert.ok(isClockwise(clockwise(sq)));
  assert.ok(!isClockwise(counterClockwise(clockwise(sq))));
  assert.ok(isClockwise(ringPoints(0, 0, 1)));          // holes come wound clockwise
  assert.equal(segmentsFor(0.15), 10);
  assert.equal(segmentsFor(50), 48);
  assert.deepEqual(loopBounds(sq, [[-1, 3]]), { minX: -1, maxX: 1, minY: 0, maxY: 3 });
});

test('clearance: inside/outside and distance to the nearest edge', () => {
  assert.deepEqual(clearance(OUTLINE.board, 10, -10), { inside: true, distance: 10 });
  assert.equal(clearance(OUTLINE.board, -1, -10).inside, false);
});

test('usableHoles keeps holes clear of the edge and of cutouts, skips filled ones', () => {
  const r = usableHoles([
    { x: 10, y: -10, diameter: 1, plated: true },
    { x: 0.2, y: -10, diameter: 1, plated: true },          // crosses the board edge
    { x: 50, y: -25, diameter: 1, plated: false },          // inside the cutout
    { x: 39.8, y: -25, diameter: 1 },                       // overlaps the cutout's edge
    { x: 20, y: -20, diameter: 0.3, filled: true },         // filled and capped via
    { x: 70, y: -40, diameter: 1, x2: 80, y2: -40, plated: false },   // routed slot
    { x: 30, y: -30, d: 3.2 },                              // `d` alias
    { x: NaN, y: 0, diameter: 1 },
  ], OUTLINE);
  assert.deepEqual(r.kept.map((h) => [h.ends[0][0], h.plated]), [[10, true], [70, false], [30, false]]);
  assert.equal(r.kept[1].ends.length, 2);
  assert.equal(r.kept[1].extent, 11);
  assert.equal(r.leftOut, null);
  assert.equal(r.rejected, 4);
});

test('usableHoles punches the largest openings first when over budget (a slot ranks by its length)', () => {
  const holes = Array.from({ length: HOLE_BUDGET + 10 }, (_, i) => ({ x: 5 + (i % 40) * 2, y: -5 - Math.floor(i / 40) * 4, diameter: i === 7 ? 3 : 0.3 }));
  holes[9] = { ...holes[9], x2: holes[9].x + 1.2, y2: holes[9].y, diameter: 0.8 };
  const r = usableHoles(holes, { board: rect(0, -100, 100, 0), cutouts: [] });
  assert.equal(r.kept.length, HOLE_BUDGET);
  assert.equal(r.kept[0].extent, 3);
  assert.ok(Math.abs(r.kept[1].extent - 2) < 1e-9);
  assert.deepEqual(r.leftOut, { count: 10, total: HOLE_BUDGET + 10, largest_mm: 0.3 });
});

test('outlines: rect fallback from a KiCad board box, and outline comparison', () => {
  const o = rectOutline({ origin_mm: [10, 20], size_mm: [30, 5] });
  assert.deepEqual(loopBounds(o.board), { minX: 10, maxX: 40, minY: -25, maxY: -20 });
  assert.equal(outlinesDiffer(o, rectOutline({ origin_mm: [10, 20], size_mm: [30, 5] })), false);
  assert.equal(outlinesDiffer(o, rectOutline({ origin_mm: [10, 20], size_mm: [30, 5.1] })), true);
  assert.equal(outlinesDiffer(o, null), true);
});

test('strokeLoops: one stadium per segment, width as the diameter', () => {
  const loops = strokeLoops([[0, 0], [2, 0], [2, 1]], 0.2);
  assert.equal(loops.length, 2);
  const b = loopBounds(...loops);
  assert.ok(Math.abs(b.minX + 0.1) < 1e-9 && Math.abs(b.maxX - 2.1) < 1e-9 && Math.abs(b.maxY - 1.1) < 1e-9);
  assert.equal(strokeLoops([[0, 0], [1, 0], [0, 1]], 0.1, true).length, 3);
});
