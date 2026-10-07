// Layer stack of boarddd/view2d: classification, paint order, colours, faces, on the fixture boards.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { layerStack, faceBoard, layerColor, sortLayers } from '../../src/view2d/layers.js';

const files = (dir) => readdirSync(new URL(dir, import.meta.url)).filter((f) => /\.(gbr|drl)$/.test(f))
  .map((name) => ({ name, source: readFileSync(new URL(`${dir}/${name}`, import.meta.url), 'utf8') }));
const RB = files('../../fixtures/royalblue54L_feather/fab');
const PIC = files('../fixtures/pic_programmer/base');

test('royalblue54L: back side, inner copper bottom-up, front, outline, drills', () => {
  const s = layerStack(RB);
  const short = (n) => n.replace('RoyalBlue54L-Feather-', '');
  assert.deepEqual(s.map((l) => short(l.name)), [
    'B_Silkscreen.gbr', 'B_Paste.gbr', 'B_Mask.gbr', 'B_Cu.gbr',
    'In6_Cu.gbr', 'In5_Cu.gbr', 'In4_Cu.gbr', 'In3_Cu.gbr', 'In2_Cu.gbr', 'In1_Cu.gbr',
    'F_Cu.gbr', 'F_Mask.gbr', 'F_Paste.gbr', 'F_Silkscreen.gbr',
    'Edge_Cuts.gbr', 'NPTH.drl', 'PTH.drl',
  ]);
  const by = Object.fromEntries(s.map((l) => [short(l.name), l]));
  assert.deepEqual(by['F_Cu.gbr'].color, [0.78, 0.2, 0.2]);
  assert.deepEqual(by['B_Cu.gbr'].color, [0.3, 0.5, 0.77]);
  assert.deepEqual(by['In1_Cu.gbr'].color, [0.5, 0.78, 0.5]);
  assert.notDeepEqual(by['In1_Cu.gbr'].color, by['In2_Cu.gbr'].color);
  assert.deepEqual(by['NPTH.drl'].color, [0.55, 0.85, 0.95]);
  assert.equal(by['F_Mask.gbr'].visible, false);
  assert.equal(by['F_Silkscreen.gbr'].visible, true);
  assert.equal(by['F_Mask.gbr'].alpha, 0.45);
});

test('pic_programmer: custom copper names classified from X2', () => {
  const s = layerStack(PIC);
  const cu = s.filter((l) => l.role === 'copper').map((l) => [l.name, l.side]);
  assert.deepEqual(cu, [['pic_programmer-bottom_layer.gbr', 'bottom'], ['pic_programmer-top_layer.gbr', 'top']]);
});

test('overrides and order of hand-made entries', () => {
  const s = sortLayers([
    { name: 'b', role: 'silk', side: 'top' }, { name: 'a', role: 'drill', side: null }, { name: 'c', role: 'copper', side: 'bottom' },
  ]);
  assert.deepEqual(s.map((l) => l.name), ['c', 'b', 'a']);
  const [one] = layerStack([{ name: 'x.gbr', source: 'G04*', role: 'copper', side: 'top', color: [1, 0, 0], visible: false }]);
  assert.deepEqual([one.role, one.color, one.visible], ['copper', [1, 0, 0], false]);
  assert.deepEqual(layerColor({ role: 'copper', side: 'inner', index: 9 }), layerColor({ role: 'copper', side: 'inner', index: 3 }));
});

test('face board: that side, outline and every drill file', () => {
  const top = faceBoard(PIC, 'top');
  assert.equal(top.top.copper.name, 'pic_programmer-top_layer.gbr');
  assert.equal(top.top.mask.name, 'pic_programmer-F_Mask.gbr');
  assert.equal(top.outline.name, 'pic_programmer-Edge_Cuts.gbr');
  assert.equal(top.drills.length, 2);
  assert.equal(top.bottom, undefined);
  const bottom = faceBoard(RB, 'bottom');
  assert.equal(bottom.bottom.silk.name, 'RoyalBlue54L-Feather-B_Silkscreen.gbr');
  assert.equal(bottom.bottom.paste.name, 'RoyalBlue54L-Feather-B_Paste.gbr');
});
