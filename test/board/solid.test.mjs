// The board solid (three.js geometry, run under node). Ported from kipr tests/web-3d/boardgeom.test.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { boardGeometry, buildBoard, splitCaps } from '../../src/board/solid.js';
import { pointToSegment, PLATING_MM } from '../../src/geom/index.js';

const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];

test('a plated slot gets a stadium hole and a stadium barrel, not an ellipse', () => {
  const { body, barrels } = boardGeometry({ board: rect(0, -10, 10, 0), cutouts: [] },
    [{ x: 4.5, y: -5, x2: 5.5, y2: -5, diameter: 1, plated: true }], 1.6);
  const pos = barrels.attributes.position;
  const d = [];
  for (let i = 0; i < pos.count; i += 1) d.push(pointToSegment(pos.getX(i), pos.getY(i), 4.5, -5, 5.5, -5));
  const bore = Math.min(...d), outer = Math.max(...d);
  assert.ok(Math.abs(bore - (0.5 - PLATING_MM)) < 1e-6 && Math.abs(outer - 0.505) < 1e-6, `${bore} ${outer}`);
  for (const x of d) assert.ok(Math.abs(x - bore) < 1e-6 || Math.abs(x - outer) < 1e-6);
  const b = body.attributes.position;
  let corner = false;
  for (let i = 0; i < b.count; i += 1) if (Math.abs(b.getX(i) - 5.5) < 1e-6 && Math.abs(b.getY(i) + 4.5) < 1e-6) corner = true;
  assert.ok(corner, 'the stadium corner (5.5, -4.5) is on the hole');
});

test('hole walls face into the hole whichever way the outline is wound (the culled-wall bug)', () => {
  for (const board of [rect(0, 0, 10, 10), rect(0, 0, 10, 10).reverse()]) {
    const { body } = boardGeometry({ board }, [{ x: 5, y: 5, diameter: 2, plated: false }], 1.6);
    body.computeVertexNormals();
    const walls = body.groups.find((g) => g.materialIndex === 2);
    const pos = body.attributes.position;
    let checked = 0;
    for (let i = walls.start; i < walls.start + walls.count; i += 3) {
      const a = new THREE.Vector3().fromBufferAttribute(pos, i), b = new THREE.Vector3().fromBufferAttribute(pos, i + 1), c = new THREE.Vector3().fromBufferAttribute(pos, i + 2);
      const mid = a.clone().add(b).add(c).divideScalar(3);
      if (Math.hypot(mid.x - 5, mid.y - 5) > 1.5) continue;     // the hole's wall, not the board's rim
      const n = b.clone().sub(a).cross(c.clone().sub(a));
      assert.ok(n.x * (5 - mid.x) + n.y * (5 - mid.y) > 0, 'wall normal points into the hole');
      checked += 1;
    }
    assert.ok(checked > 10);
  }
});

test('caps split into top (z = thickness) and bottom (z = 0) groups, walls third', () => {
  const { body } = boardGeometry({ board: rect(0, 0, 4, 3) }, [], 1.2);
  assert.deepEqual(body.groups.map((g) => g.materialIndex), [0, 1, 2]);
  const z = (g) => body.attributes.position.getZ(body.groups[g].start);
  assert.ok(Math.abs(z(0) - 1.2) < 1e-6);
  assert.equal(z(1), 0);
  splitCaps(body);   // idempotent enough: still three groups
  assert.equal(body.groups.length, 3);
});

test('planar UVs map the uv bounds linearly', () => {
  const { body } = boardGeometry({ board: rect(0, 0, 4, 2) }, [], 1.6, { minX: -1, maxX: 5, minY: -1, maxY: 3 });
  const pos = body.attributes.position, uv = body.attributes.uv;
  for (let i = 0; i < pos.count; i++) {
    assert.ok(Math.abs(uv.getX(i) - (pos.getX(i) + 1) / 6) < 1e-6);
    assert.ok(Math.abs(uv.getY(i) - (pos.getY(i) + 1) / 4) < 1e-6);
  }
});

test('buildBoard: groups tagged, faces as colours, dispose', () => {
  const b = buildBoard({ outline: { board: rect(0, 0, 10, 10) }, holes: [{ x: 5, y: 5, diameter: 1, plated: true }], faces: { top: 0x123456 } });
  assert.equal(b.body.userData.group, 'board');
  assert.equal(b.barrels.userData.group, 'barrels');
  assert.equal(b.materials.top.color.getHex(), 0x123456);
  assert.equal(b.holes.kept.length, 1);
  b.setFaces({ bottom: 0xff0000 });
  assert.equal(b.body.material[1].color.getHex(), 0xff0000);
  b.dispose();
  assert.throws(() => buildBoard({ outline: { board: [[0, 0]] } }));
});
