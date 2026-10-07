// KiCad pad semantics against KiCad's own numbers:
//   test/fixtures/pad_placement/golden.json  kipr PR #13 (copper at the shape offset, hole at `at`, models)
//   test/fixtures/pad_shapes/golden.json     pcbnew's effective polygon of every pad shape (boarddd)
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import {
  padOutline, padToKicad, padDrill, padHoleCenter, padDrillSlot, padCopperSides, padHasCopper, padOffset,
  padCopperLoops, kicadModelMatrix, applyMatrix, signedArea2, loopBounds,
} from '../../src/geom/index.js';
import { parseKicadFootprint } from '../../src/footprint/kicad_mod.js';

const FIX = new URL('../fixtures/', import.meta.url);
const read = (p) => readFileSync(new URL(p, FIX), 'utf8');
const bbox = (pts) => {
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
};
const near = (a, b, tol, msg) => {
  assert.equal(a.length, b.length, msg);
  a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) <= tol, `${msg}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`));
};

test('pad placement (kipr PR #13 golden): copper at the shape offset, hole at `at`, model origins', () => {
  const golden = JSON.parse(read('pad_placement/golden.json'));
  const files = readdirSync(new URL('pad_placement/', FIX)).filter((f) => f.endsWith('.kicad_mod'));
  assert.deepEqual(files.map((f) => f.replace('.kicad_mod', '')).sort(), Object.keys(golden).sort());
  for (const [name, gold] of Object.entries(golden)) {
    const fp = parseKicadFootprint(read(`pad_placement/${name}.kicad_mod`));
    assert.equal(fp.pads.length, gold.pads.length, name);
    const custom = fp.pads.some((p) => p.shape === 'custom');
    fp.pads.forEach((pad, i) => {
      const g = gold.pads[i];
      const where = `${name} pad ${pad.number}`;
      assert.equal(pad.number, g.number, where);
      near(pad.at.slice(0, 2), g.at, 1e-6, where);
      const { outer, extra } = padOutline(pad, 32);
      const local = bbox(outer);
      near(padToKicad(pad, [(local[0] + local[2]) / 2, (local[1] + local[3]) / 2]), g.copper_center, 0.01, `${where} copper`);
      if (pad.number && !custom) near(bbox([outer, ...extra].flat().map((q) => padToKicad(pad, q))), g.copper_bbox, 0.02, `${where} copper bbox`);
      if (g.hole_center === null) assert.equal(padDrill(pad), null, where);
      else near(padHoleCenter(pad), g.hole_center, 1e-6, `${where} hole`);
    });
    fp.models.forEach((m, i) => {
      for (const k of ['offset', 'rotate', 'scale']) near(m[k], gold.models[i][k], 1e-6, `${name} model ${k}`);
      near(applyMatrix(kicadModelMatrix(m), [0, 0, 0]), gold.models[i].offset, 1e-9, `${name} model origin`);
    });
  }
  // the case that was wrong in kipr: copper 0.65 mm outward of `at`
  assert.deepEqual(golden['RP2040-Zero_Castellated'].pads[0].copper_center, [-8.27, -10.16]);
});

test('every pad shape matches pcbnew: area and bbox of the copper, slot of the hole', () => {
  const golden = JSON.parse(read('pad_shapes/golden.json'));
  const fp = parseKicadFootprint(read('pad_shapes/Pad_Shapes_boarddd.kicad_mod'));
  assert.equal(fp.pads.length, golden.length);
  fp.pads.forEach((pad, i) => {
    const g = golden[i];
    const where = `pad ${pad.number} (${pad.shape})`;
    const loops = padCopperLoops(pad, 32);                   // board frame (y up)
    const kicad = loops.map((l) => l.map(([x, y]) => [x, -y]));
    near(bbox(kicad.flat()), g.bbox, 0.01, `${where} bbox`);
    if (pad.shape !== 'custom') {
      // pcbnew polygonises arcs inside the true shape (ERROR_INSIDE), so curved pads come out a little
      // smaller there than the true area; straight-edged ones must match exactly.
      const area = Math.abs(signedArea2(loops[0])) / 2;
      const curved = ['circle', 'oval'].includes(pad.shape) || (pad.shape === 'roundrect' && pad.roundrect_rratio > 0);
      assert.ok(Math.abs(area - g.area) <= (curved ? g.area * 0.01 : 1e-6), `${where} area ${area} != ${g.area}`);
    }
    const slot = padDrillSlot(pad);
    if (!g.hole) { assert.equal(slot, null, where); return; }
    assert.ok(Math.abs(2 * slot.radius - g.hole.width) < 1e-9, `${where} hole width`);
    const ends = slot.ends.map((e) => e.map((v) => Math.round(v * 1e6) / 1e6)).sort();
    const want = [g.hole.start, g.hole.end].map((e) => e.map((v) => Math.round(v * 1e6) / 1e6)).sort();
    near(ends.flat(), want.flat(), 1e-5, `${where} hole ends`);
  });
});

test('trapezoid: rect_delta as pcbnew draws it (dx: left edge taller; dy: bottom edge wider)', () => {
  const t = (delta) => padOutline({ shape: 'trapezoid', size: [2, 1], rect_delta: delta }).outer;
  // pcbnew GetEffectivePolygon, KiCad frame: [(-1,-0.7), (1,-0.3), (1,0.3), (-1,0.7)]
  const a = t([0.4, 0]);
  near(bbox(a.filter(([x]) => x < 0)), [-1, -0.7, -1, 0.7], 1e-12, 'left edge');
  near(bbox(a.filter(([x]) => x > 0)), [1, -0.3, 1, 0.3], 1e-12, 'right edge');
  // [(-0.8,-0.5), (0.8,-0.5), (1.2,0.5), (-1.2,0.5)]
  const b = t([0, 0.4]);
  near(bbox(b.filter(([, y]) => y < 0)), [-0.8, -0.5, 0.8, -0.5], 1e-12, 'top edge');
  near(bbox(b.filter(([, y]) => y > 0)), [-1.2, 0.5, 1.2, 0.5], 1e-12, 'bottom edge');
});

test('pad helpers: rotation, sides, copperless NPTH, offsets', () => {
  const pad = { at: [1, 2, 90], size: [2, 1], shape: 'rect', layers: ['*.Cu', '*.Mask'], drill: { shape: 'circle', size: [0.8, 0.8] } };
  near(padToKicad(pad, [1, 0]), [1, 1], 1e-12, 'CCW on screen: +x goes to -y (up)');
  assert.deepEqual(padCopperSides(pad), { top: true, bottom: true });
  assert.deepEqual(padCopperSides({ layers: ['B.Cu'] }), { top: false, bottom: true });
  assert.equal(padHasCopper({ type: 'np_thru_hole', size: [3, 3], layers: ['*.Cu'], drill: 3 }), false);
  assert.equal(padHasCopper({ type: 'np_thru_hole', size: [4, 4], layers: ['*.Cu'], drill: 3 }), true);
  assert.deepEqual(padOffset({ drill: { size: [1, 1], offset: [0.2, 0] } }), [0.2, 0]);
  assert.deepEqual(padDrill({ drill: 0.6 }), { w: 0.6, h: 0.6, oval: false });
  assert.equal(padOutline({ size: [1, 1], shape: 'roundrect', roundrect_rratio: 0.25 }).outer.length, 36);
  const b = loopBounds(padCopperLoops({ at: [0, 0, 0], size: [2, 1], shape: 'rect' })[0]);
  assert.deepEqual(b, { minX: -1, maxX: 1, minY: -0.5, maxY: 0.5 });
});
