// buildComponent: one placed component of a board model on a slice of its board, under node.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from 'three';
import { parseKicadFootprint, buildComponent, componentMatrix, boardToComponent, componentOf } from '../../src/footprint/index.js';
import { applyMatrix, COPPER_THICKNESS } from '../../src/geom/index.js';
import { footprintToBoard } from '../../src/model/index.js';

const FIX = new URL('../fixtures/', import.meta.url);
const read = (p) => readFileSync(new URL(p, FIX), 'utf8');
const relay = parseKicadFootprint(read('footprints/Relay_SPST_THT_boarddd.kicad_mod'));
const resistor = parseKicadFootprint(read('pad_placement/R_0603_1608Metric.kicad_mod'));

// A board model with the relay (bottom side, turned) and a 0603 (top), a via beside the relay and the relay's
// own pad holes in the drill list, as a fab package's Excellon file has them.
function board({ side = 'bottom', rotation = 90 } = {}) {
  const K1 = { ref: 'K1', side, x: 50, y: -40, rotation, footprint: 'Relay', mount: 'tht' };
  const R1 = { ref: 'R1', side: 'top', x: 10, y: -10, rotation: 0, footprint: 'R_0603', mount: 'smd' };
  const drills = relay.pads.filter((p) => p.drill).map((p) => {
    const [x, y] = footprintToBoard(K1, p.at);
    const d = typeof p.drill === 'number' ? p.drill : Math.min(...p.drill.size);
    return { x, y, diameter: d, plated: p.type === 'thru_hole', x2: null, y2: null, function: 'component' };
  });
  const via = footprintToBoard(K1, [5, -3]);
  drills.push({ x: via[0], y: via[1], diameter: 0.4, plated: true, x2: null, y2: null, function: 'via' });
  const far = footprintToBoard(K1, [60, 0]);
  drills.push({ x: far[0], y: far[1], diameter: 0.4, plated: true, x2: null, y2: null, function: 'via' });
  return {
    schema: 'boarddd/board@1', name: 't', stackup: { thickness: 1.2 },
    footprints: { Relay: relay, R_0603: resistor }, components: [K1, R1], drills,
  };
}

const boxOf = (m) => new THREE.Box3().setFromObject(m);

test('buildComponent (THT): pads on both faces, barrels through a slice as thick as the stackup, the via punched', () => {
  const b = board();
  const built = buildComponent(b, 'K1');
  assert.equal(built.thickness, 1.2);
  const plated = relay.pads.filter((p) => p.type === 'thru_hole');
  const top = built.meshes.copper.filter((m) => m.userData.side === 'top');
  const bottom = built.meshes.copper.filter((m) => m.userData.side === 'bottom');
  assert.equal(top.length, plated.length);
  assert.equal(bottom.length, plated.length);
  assert.deepEqual(bottom.map((m) => m.userData.pad).sort(), plated.map((p) => p.number).sort());
  for (const m of bottom) assert.ok(Math.abs(boxOf(m).max.z) < 1e-6 && Math.abs(boxOf(m).min.z + COPPER_THICKNESS) < 1e-6);
  for (const m of top) assert.ok(Math.abs(boxOf(m).min.z - 1.2) < 1e-6);
  // a barrel per plated pad plus the via's; the far via and the pad holes are not doubled
  assert.equal(built.meshes.barrels.filter((m) => m.userData.pad !== undefined).length, plated.length);
  assert.equal(built.meshes.barrels.filter((m) => m.userData.hole !== undefined).length, 1);
  assert.equal(built.holes.kept.length, 1);
  for (const m of built.meshes.barrels) {
    const box = boxOf(m);
    assert.ok(Math.abs(box.min.z) < 1e-6 && Math.abs(box.max.z - 1.2) < 1e-6);
  }
  // the board slice has the holes: its top cap is the outline less every drill (4 x 1.3, a 1 x 2 slot, a 1.6
  // NPTH, the 0.4 via)
  const slab = built.meshes.board.geometry;
  slab.computeBoundingBox();
  const cap = slab.groups.find((g) => g.materialIndex === 0);
  const pos = slab.attributes.position, idx = slab.index;
  const at = (k) => new THREE.Vector3().fromBufferAttribute(pos, idx ? idx.getX(k) : k);
  let area = 0;
  for (let k = cap.start; k < cap.start + cap.count; k += 3) {
    const [p, q, r] = [at(k), at(k + 1), at(k + 2)];
    area += q.sub(p).cross(r.sub(p)).length() / 2;
  }
  const xs = built.outline.board.map((p) => p[0]), ys = built.outline.board.map((p) => p[1]);
  const full = (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys));
  const r = (d) => Math.PI * (d / 2) ** 2;
  const holes = 4 * r(1.3) + (r(1) + 1 * 1) + r(1.6) + r(0.4);
  // holes are polygons a hair inside their circles (~1 %); the smallest, the via, is 0.126 mm2
  assert.ok(Math.abs(full - 0.99 * holes - area) < 0.05, `cap ${area}, outline ${full}, holes ${holes}`);
  assert.ok(Math.abs(slab.boundingBox.max.z - 1.2) < 1e-6 && Math.abs(slab.boundingBox.min.z) < 1e-6);
  built.dispose();
});

test('buildComponent (THT): the via lands where the board has it, in the footprint frame', () => {
  const b = board();
  const built = buildComponent(b, 'K1');
  const barrel = built.meshes.barrels.find((m) => m.userData.hole === 0);
  const c = boxOf(barrel).getCenter(new THREE.Vector3());
  // footprint (5, -3) KiCad = (5, 3) in the footprint's board frame (y up)
  assert.ok(Math.abs(c.x - 5) < 1e-6 && Math.abs(c.y - 3) < 1e-6, `${c.x}, ${c.y}`);
  built.dispose();
});

test('buildComponent (SMD): top pads only, no barrels, no holes', () => {
  const built = buildComponent(board(), 'R1');
  assert.equal(built.meshes.copper.length, 2);
  assert.ok(built.meshes.copper.every((m) => m.userData.side === 'top'));
  assert.equal(built.meshes.barrels.length, 0);
  built.dispose();
});

test('componentMatrix: pad holes land on the board drills, top and bottom, any rotation', () => {
  for (const side of ['top', 'bottom']) {
    for (const rotation of [0, 90, 180, -90, 33]) {
      const b = board({ side, rotation });
      const { component } = componentOf(b, 'K1');
      const m = componentMatrix(component, 1.2);
      for (const pad of relay.pads.filter((p) => p.drill)) {
        const [x, y] = applyMatrix(m, [pad.at[0], -pad.at[1], 0]);
        const [ex, ey] = footprintToBoard(component, pad.at);
        assert.ok(Math.hypot(x - ex, y - ey) < 1e-9, `${side} ${rotation} pad ${pad.number}`);
        const back = boardToComponent(component, [ex, ey]);
        assert.ok(Math.hypot(back[0] - pad.at[0], back[1] + pad.at[1]) < 1e-9);
      }
      // the part's own face: top face for a top part, the bottom face for a bottom one
      const own = applyMatrix(m, [0, 0, 1.2])[2];
      assert.ok(Math.abs(own - (side === "top" ? 1.2 : 0)) < 1e-12);
    }
  }
});

test('buildComponent: drills false keeps the footprint\'s own holes only; unknown refs throw', () => {
  const built = buildComponent(board(), 'K1', { drills: false });
  assert.equal(built.holes.kept.length, 0);
  assert.ok(built.meshes.barrels.length > 0);
  built.dispose();
  assert.throws(() => buildComponent(board(), 'U9'), /no component U9/);
});

test('buildFootprint boardOpacity: a see-through board does not write depth', () => {
  const built = buildComponent(board(), 'K1', { boardOpacity: 0.4 });
  for (const m of built.meshes.board.material) {
    assert.equal(m.transparent, true);
    assert.equal(m.depthWrite, false);
    assert.equal(m.opacity, 0.4);
  }
  built.dispose();
});
