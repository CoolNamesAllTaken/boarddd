// src/models/boardglb.js on boarddd.step.glb's output for the tiny fixture board (quantized: Draco
// needs a browser's workers, see test/browser/models.spec.mjs).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from 'three';
import { loadBoardGLB, upgradeBoardGLB } from '../../src/models/boardglb.js';
import { stepMaterial } from '../../src/models/step.js';

const fixture = (name) => readFileSync(new URL(`../fixtures/tiny-board/${name}`, import.meta.url));
const REFS = ['C1', 'D1', 'Q1', 'R1', 'R2', 'R3', 'R4', 'R5', 'U1', 'U2', 'U3'];

test('loadBoardGLB: one group per instance, named by designator, in the STEP frame', async () => {
  const { object, nodes, meta, triangles } = await loadBoardGLB(fixture('quantize-full.glb'));
  assert.equal(meta.units, 'mm');
  assert.equal(meta.lod, 'full');
  for (const ref of REFS) assert.ok(nodes.some((n) => n.name === ref), ref);
  assert.ok(nodes.some((n) => n.name.startsWith('=>[')), 'the board body');
  assert.equal(object.children.length, nodes.length);
  assert.ok(triangles > 1000);
  object.updateMatrixWorld(true);
  const box = (name) => new THREE.Box3().setFromObject(nodes.find((n) => n.name === name).group);
  // mm, z up: top-side parts above the 1.6 mm board, the bottom-side R5 below it.
  assert.ok(box('R1').min.z > 1.0, `R1 ${box('R1').min.z}`);
  assert.ok(box('R5').max.z < 0.1, `R5 ${box('R5').max.z}`);
  const size = box('R1').getSize(new THREE.Vector3());
  assert.ok(size.x > 0.9 && size.x < 1.2, `0402 length ${size.x}`);
  // Groups at the origin, the placement in the meshes (as stepToObject with center: false).
  for (const n of nodes) assert.deepEqual(n.group.position.toArray(), [0, 0, 0]);
});

test('loadBoardGLB: instances share geometry; materials are the STEP path\'s, shared per colour', async () => {
  const { nodes } = await loadBoardGLB(fixture('quantize-full.glb'));
  const meshesOf = (name) => {
    const out = [];
    nodes.find((n) => n.name === name).group.traverse((o) => o.isMesh && out.push(o));
    return out;
  };
  const r1 = meshesOf('R1'), r2 = meshesOf('R2'), r4 = meshesOf('R4');
  assert.equal(r1.length, r2.length);
  r1.forEach((m, i) => assert.equal(m.geometry, r2[i].geometry));
  assert.notEqual(r1[0].geometry, r4[0].geometry);
  const all = r1.concat(r4);
  for (const m of all) {
    assert.equal(m.material.type, stepMaterial([0.5, 0.5, 0.5]).type);
    assert.ok(m.material.polygonOffset);
    assert.ok(m.userData.lodKey);
  }
  assert.equal(r1[0].material, r2[0].material);
});

test('upgradeBoardGLB swaps geometry by lodKey and keeps groups, materials and names', async () => {
  const { object, nodes } = await loadBoardGLB(fixture('quantize-full.glb'));
  const r1 = nodes.find((n) => n.name === 'R1').group;
  const material = r1.children[0].children[0].children[0]?.material || null;
  const before = [];
  object.traverse((o) => o.isMesh && before.push(o.geometry));
  // The same file again stands in for another level: every mesh gets a new (equal) geometry.
  const { swapped, triangles } = await upgradeBoardGLB(object, fixture('quantize-full.glb'));
  const after = [];
  object.traverse((o) => o.isMesh && after.push(o.geometry));
  assert.equal(swapped, before.length);
  assert.ok(after.every((g, i) => g !== before[i]));
  assert.ok(triangles > 1000);
  assert.equal(nodes.find((n) => n.name === 'R1').group, r1);
  if (material) assert.equal(r1.children[0].children[0].children[0].material, material);
});

test('loadBoardGLB refuses a GLB that is not a board GLB', async () => {
  const kicad = readFileSync(new URL('../../examples/data/royalblue54L_feather/board.glb', import.meta.url));
  await assert.rejects(loadBoardGLB(kicad), /not a boarddd board GLB/);
});
