// The footprint builder and the .kicad_mod reader, under node.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from 'three';
import { parseKicadFootprint, buildFootprint, chainLoops, footprintOutline, parseSexpr, arcThrough } from '../../src/footprint/index.js';
import { pointToSegment, applyMatrix, BOARD_THICKNESS, COPPER_THICKNESS } from '../../src/geom/index.js';

const FIX = new URL('../fixtures/', import.meta.url);
const read = (p) => readFileSync(new URL(p, FIX), 'utf8');

test('parseSexpr: atoms, strings with escapes, nesting', () => {
  assert.deepEqual(parseSexpr('(a "b \\"c\\"" (d 1 2))'), ['a', { str: 'b "c"' }, ['d', '1', '2']]);
});

test('arcThrough: the mid point picks the direction; ends inclusive', () => {
  const cw = arcThrough([1, 0], [0, 1], [-1, 0]);
  const ccw = arcThrough([1, 0], [0, -1], [-1, 0]);
  assert.ok(cw.every(([, y]) => y >= -1e-12) && ccw.every(([, y]) => y <= 1e-12));
  assert.deepEqual(cw[0], [1, 0]);
  assert.ok(Math.abs(cw.at(-1)[0] + 1) < 1e-12);
});

test('parseKicadFootprint: the USB-C fixture (pads, oval drills with offsets, graphics, model)', () => {
  const fp = parseKicadFootprint(read('pad_placement/USB_C_Receptacle_CNCTech_C-ARA1-AK51X.kicad_mod'));
  assert.equal(fp.name, 'USB_C_Receptacle_CNCTech_C-ARA1-AK51X');
  const sh = fp.pads.filter((p) => p.drill?.shape === 'oval');
  assert.ok(sh.length >= 2);
  assert.ok(fp.graphics.some((g) => g.layer === 'F.CrtYd'));
  assert.ok(fp.graphics.some((g) => g.layer === 'F.SilkS'));
  assert.equal(fp.models.length, 1);
});

test('chainLoops joins segments in any direction; footprintOutline falls back to the courtyard + margin', () => {
  const loops = chainLoops([[[0, 0], [1, 0]], [[1, 1], [1, 0]], [[1, 1], [0, 1]], [[0, 1], [0, 0]], [[5, 5], [6, 6]]]);
  assert.equal(loops.length, 1);
  assert.equal(loops[0].length, 4);
  const fp = { pads: [], graphics: [{ layer: 'F.CrtYd', pts: [[-1, -2], [3, -2], [3, 4], [-1, 4]], closed: true }] };
  const o = footprintOutline(fp, 1);
  const xs = o.board.map((p) => p[0]), ys = o.board.map((p) => p[1]);
  assert.deepEqual([Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)], [-2, 4, -5, 3]);   // y flipped
});

test('buildFootprint: slotted holes are stadiums in the board, the copper and the barrel', () => {
  const fp = parseKicadFootprint(`(footprint "slot" (layer "F.Cu")
    (fp_rect (start -3 -3) (end 3 3) (stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd"))
    (pad "1" thru_hole oval (at 0 0 45) (size 2 3) (drill oval 1 2) (layers "*.Cu" "*.Mask")))`);
  const built = buildFootprint(fp);
  const { meshes } = built;
  assert.equal(meshes.barrels.length, 1);
  assert.equal(meshes.copper.length, 2);   // top and bottom
  // slot axis in the board frame: pad local y turned +45 deg on screen -> board direction 135 deg
  const a = (135 * Math.PI) / 180, e1 = [-0.5 * Math.cos(a), -0.5 * Math.sin(a)], e2 = [0.5 * Math.cos(a), 0.5 * Math.sin(a)];
  const pos = meshes.barrels[0].geometry.attributes.position;
  for (let i = 0; i < pos.count; i++) assert.ok(Math.abs(pointToSegment(pos.getX(i), pos.getY(i), ...e1, ...e2) - 0.5) < 1e-6);
  const box = new THREE.Box3().setFromObject(meshes.barrels[0]);
  assert.ok(Math.abs(box.min.z) < 1e-9 && Math.abs(box.max.z - BOARD_THICKNESS) < 1e-6);
  const top = new THREE.Box3().setFromObject(meshes.copper[0]);
  assert.ok(Math.abs(top.min.z - BOARD_THICKNESS) < 1e-6 && Math.abs(top.max.z - BOARD_THICKNESS - COPPER_THICKNESS) < 1e-6);
  built.dispose();
});

test('buildFootprint: the pad-placement fixtures build, groups are tagged, models land on the top face', () => {
  for (const name of ['RP2040-Zero_Castellated', 'R_0603_1608Metric', 'SMA_Amphenol_132289_EdgeMount', 'USB_C_Receptacle_CNCTech_C-ARA1-AK51X']) {
    const fp = parseKicadFootprint(read(`pad_placement/${name}.kicad_mod`));
    const built = buildFootprint(fp);
    const groups = new Set();
    built.group.traverse((o) => o.userData.group && groups.add(o.userData.group));
    assert.ok(groups.has('board') && groups.has('copper'), `${name}: ${[...groups]}`);
    for (const m of fp.models) {
      const o = applyMatrix(built.modelMatrix(m), [0, 0, 0]);
      assert.ok(Math.abs(o[2] - (m.offset[2] + BOARD_THICKNESS)) < 1e-9);
    }
    built.dispose();
  }
});

test('buildFootprint: an NPTH without copper ring gets a hole but no copper and no barrel', () => {
  const fp = parseKicadFootprint(`(footprint "h" (layer "F.Cu")
    (fp_circle (center 0 0) (end 3 0) (stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd"))
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers "*.Cu" "*.Mask")))`);
  const b = buildFootprint(fp);
  assert.equal(b.meshes.copper.length, 0);
  assert.equal(b.meshes.barrels.length, 0);
  assert.ok(b.meshes.board.geometry.attributes.position.count > 100);   // the hole's wall is there
});

test('buildFootprint decals: layer pictures become transparent sheets in their group, top above the copper, bottom under it', () => {
  const fp = parseKicadFootprint(read('pad_placement/R_0603_1608Metric.kicad_mod'));
  const pic = () => new THREE.DataTexture(new Uint8Array(4 * 4 * 4), 4, 4);
  const silkTop = pic(), fabBottom = pic();
  const uvBounds = { minX: -3, maxX: 3, minY: -2, maxY: 2 };
  const built = buildFootprint({ ...fp, graphics: [] }, {
    outline: { board: [[-3, -2], [3, -2], [3, 2], [-3, 2]] }, uvBounds, decals: { silk: { top: silkTop }, fab: { bottom: fabBottom } },
  });
  assert.equal(built.meshes.silk.length, 1);
  assert.equal(built.meshes.fab.length, 1);
  const [s] = built.meshes.silk, [f] = built.meshes.fab;
  assert.equal(s.userData.group, 'silk');
  assert.equal(f.userData.group, 'fab');
  assert.equal(s.material.map, silkTop);
  assert.ok(s.material.transparent && !s.material.depthWrite);
  assert.equal(f.material.side, THREE.BackSide);
  s.geometry.computeBoundingBox(); f.geometry.computeBoundingBox();
  assert.ok(s.geometry.boundingBox.min.z > BOARD_THICKNESS + COPPER_THICKNESS);
  assert.ok(f.geometry.boundingBox.max.z < -COPPER_THICKNESS);
  // UVs follow uvBounds: the corner (-3, -2) maps to (0, 0), (3, 2) to (1, 1)
  const uv = s.geometry.attributes.uv, pos = s.geometry.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    assert.ok(Math.abs(uv.getX(i) - (pos.getX(i) + 3) / 6) < 1e-6);
    assert.ok(Math.abs(uv.getY(i) - (pos.getY(i) + 2) / 4) < 1e-6);
  }
  built.dispose();
  assert.equal(silkTop.source.data !== null, true);   // the caller's textures are not disposed by us
});

test('buildFootprint: paste on request; fillUpTo caps round plated pad holes (no hole, no barrel)', () => {
  const fp = parseKicadFootprint(read('pad_placement/USB_C_Receptacle_CNCTech_C-ARA1-AK51X.kicad_mod'));
  const plain = buildFootprint(fp);
  assert.equal(plain.meshes.paste.length, 0);
  const pasted = buildFootprint(fp, { paste: true });
  const onPaste = fp.pads.filter((p) => p.layers.some((l) => /Paste$/.test(l))).length;
  assert.ok(onPaste > 0 && pasted.meshes.paste.length >= onPaste);
  for (const m of pasted.meshes.paste) {
    assert.equal(m.userData.group, 'paste');
    const box = new THREE.Box3().setFromObject(m);
    assert.ok(box.min.z >= BOARD_THICKNESS + COPPER_THICKNESS - 1e-6 || box.max.z <= -COPPER_THICKNESS + 1e-6);
  }
  const round = fp.pads.filter((p) => p.type === 'thru_hole' && typeof p.drill === 'object' && p.drill && p.drill.shape !== 'oval');
  const filled = buildFootprint(fp, { fillUpTo: 0.4 });
  assert.ok(round.length > 0);
  assert.equal(filled.meshes.barrels.length, plain.meshes.barrels.length - round.length);
  assert.equal(buildFootprint(fp, { fillUpTo: 0.39 }).meshes.barrels.length, plain.meshes.barrels.length);
  for (const b of [plain, pasted, filled]) b.dispose();
});
