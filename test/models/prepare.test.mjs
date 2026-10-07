// prepareModel on synthetic kicad-cli-like GLBs, and loadSTEP through occt-import-js, under node.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import * as THREE from 'three';
import { loadGLB, loadSTEP, prepareModel, disposeObject } from '../../src/models/index.js';
import { buildMock, BOARD } from './mock_board.mjs';

const require = createRequire(import.meta.url);
const kicad = (parts) => parts.map(({ ref, x, y, side }) => ({ ref, x, y, side }));

async function prepared(opts, prepOpts = {}) {
  const { buffer, parts } = buildMock(opts);
  const model = await loadGLB(new Uint8Array(buffer));
  return { parts, s: prepareModel(model, kicad(parts), prepOpts) };
}

function assertPlaced(s, parts) {
  for (const c of parts) {
    const e = s.comps.get(c.ref);
    assert.ok(e, `${c.ref} matched`);
    const p = e.objects[0].getWorldPosition(new THREE.Vector3());
    assert.ok(Math.hypot(p.x - c.x, p.y + c.y) < 1e-3, `${c.ref} at ${p.x},${p.y} not ${c.x},${-c.y}`);
    assert.equal(e.bottom, c.side === 'bottom');
    assert.equal(e.meshes[0].userData.group, 'model');
    assert.equal(e.meshes[0].userData.ref, c.ref);
  }
}

test('GLB named by reference: metres/y-up detected, matched by name, board seated at z=0', async () => {
  const { s, parts } = await prepared({ origin: [0, 0] });
  assert.equal(s.report.method, 'name');
  assert.equal(s.report.up, 'y');
  assert.equal(s.report.scale, 1000);
  assert.equal(s.report.matched, parts.length);
  assert.deepEqual(s.report.boardParts, { substrate: 1, mask: 1, copper: 0, silk: 1 });
  assertPlaced(s, parts);
  assert.ok(Math.abs(s.boardBox.min.z) < 1e-6 && Math.abs(s.boardBox.max.z - 1.6) < 1e-6);
  assert.ok(Math.abs(s.board.thickness - 1.6) < 1e-6);
  assert.ok(Math.abs(s.boardBox.min.x - 100) < 1e-3 && Math.abs(s.boardBox.max.y + 50) < 1e-3);
  assert.equal(s.parts.substrate[0].children[0].userData.group, 'board');
});

test('GLB with opaque names and the board centre as export origin: matched by position', async () => {
  const { s, parts } = await prepared({ named: false, origin: [120, 65] });
  assert.equal(s.report.method, 'position');
  assert.equal(s.report.matched, parts.length);
  assert.deepEqual(s.report.ambiguous, []);
  assertPlaced(s, parts);
});

test('a board-wide shield can is a component (left loose here), not a board body', async () => {
  const { s, parts } = await prepared({ can: true });
  assert.deepEqual(s.report.boardParts, { substrate: 1, mask: 1, copper: 0, silk: 1 });
  assert.equal(s.report.loose, 1);
  assertPlaced(s, parts);
});

test('a 0.8 mm board: thickness measured from the substrate; stated units honoured', async () => {
  const { s } = await prepared({ thickness: 0.8 }, { units: 'm', boardSize: BOARD.size });
  assert.ok(Math.abs(s.board.thickness - 0.8) < 1e-6);
  assert.equal(s.report.scale, 1000);
  disposeObject(s.root);
});

test('loadSTEP (occt on the main thread): KiCad colour handling, z-up mm', async () => {
  const occtimportjs = require('occt-import-js');
  const bytes = new Uint8Array(readFileSync(new URL('../fixtures/blue_board.step', import.meta.url)));
  const group = await loadSTEP(bytes, { occtFactory: () => occtimportjs(), workerUrl: false });
  const meshes = [];
  group.traverse((o) => { if (o.isMesh) meshes.push(o); });
  assert.ok(meshes.length >= 1);
  const c = meshes[0].material.color;
  // The file says 0.090/0.224/0.420; used as is (linear working space), like KiCad.
  assert.ok(Math.abs(c.r - 0.09) < 0.003 && Math.abs(c.g - 0.224) < 0.003 && Math.abs(c.b - 0.42) < 0.003, c.getHexString());
  assert.equal(meshes[0].material.polygonOffset, true);
  // Each node is placed at its box middle: the matcher sees the origin where the part is.
  const box = new THREE.Box3().setFromObject(group);
  const mid = box.getCenter(new THREE.Vector3());
  assert.ok(group.children[0].position.distanceTo(mid) < 1e-3);
});
