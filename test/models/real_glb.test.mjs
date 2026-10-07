// prepareModel on a real kicad-cli GLB (KiCad demo data: royalblue54L_feather, examples/data).
// That export carries 11 component models (the library passives have none) and only the PCB body.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from 'three';
import { loadGLB, prepareModel } from '../../src/models/index.js';

const glb = () => new Uint8Array(readFileSync(new URL('../../examples/data/royalblue54L_feather/board.glb', import.meta.url)));
const { components } = JSON.parse(readFileSync(new URL('../fixtures/royalblue54L_components.json', import.meta.url), 'utf8'));

function placementError(s) {
  let worst = 0;
  for (const [ref, e] of s.comps) {
    const c = components.find((k) => k.ref === ref);
    const p = e.objects[0].getWorldPosition(new THREE.Vector3());
    worst = Math.max(worst, Math.hypot(p.x - c.x, p.y + c.y));
  }
  return worst;
}

test('real GLB by name: every component with a model found, on its placement, board seated', async () => {
  const s = prepareModel(await loadGLB(glb()), components);
  assert.equal(s.report.method, 'name');
  assert.equal(s.report.up, 'y');
  assert.equal(s.report.scale, 1000);
  assert.equal(s.report.matched, 11, JSON.stringify(s.report));
  assert.equal(s.report.loose, 0);
  assert.deepEqual(s.report.boardParts, { substrate: 1, mask: 0, copper: 0, silk: 0 });
  // Node origins are the placements, except where KiCad folds a model offset in (J4, J5: 4.5, 1.4 mm).
  assert.ok(placementError(s) < 5, `worst ${placementError(s)}`);
  assert.ok(Math.abs(s.boardBox.min.z) < 1e-6);
  assert.ok(s.board.thickness > 1.4 && s.board.thickness < 1.7, JSON.stringify(s.board));
  for (const e of s.comps.values()) assert.equal(e.bottom, e.component.side === 'bottom', e.ref);
});

test('real GLB with every name scrubbed: position matching names the same nodes', async () => {
  // Scrub: component nodes lose their refs; mark each with its true ref to check afterwards.
  const model = await loadGLB(glb());
  const refs = new Set(components.map((c) => c.ref));
  model.scene.traverse((o) => { if (refs.has(o.name)) { o.userData.truth = o.name; o.name = 'model'; } });
  const s = prepareModel(model, components);
  assert.equal(s.report.method, 'position');
  let right = 0, wrong = [];
  for (const [ref, e] of s.comps) {
    const t = e.objects.map((o) => o.userData.truth).filter(Boolean);
    if (t.length && t.every((x) => x === ref)) right++; else wrong.push(`${ref}<-${t}`);
  }
  assert.deepEqual(wrong, []);
  assert.equal(right, 11, JSON.stringify(s.report));
  assert.ok(Math.hypot(s.report.offset.x, s.report.offset.y) < 0.01);
});
