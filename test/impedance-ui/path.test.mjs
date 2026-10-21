// boarddd/impedance/ui path.js: one route through a selection of nets and pairs (the Z-along-the-route plot), on
// fixtures/impedance/route/chain.json: SIG_A (F.Cu, a via, B.Cu) → R1 → SIG_B (B.Cu, a via, F.Cu, back to J1), and
// TX_P/TX_N → C1/C2 → TX_CP/TX_CN. The browser side: layers.spec.mjs ("Z along the whole route").
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { analyzeNet } from '../../src/impedance/index.js';
import { bridges, chainOrder, niceTicks, pathAt, pathFromBoard, routePath } from '../../src/impedance/ui/index.js';

const { board, copper } = JSON.parse(readFileSync(new URL('../../fixtures/impedance/route/chain.json', import.meta.url), 'utf8'));
const analyse = (u) => analyzeNet(board, copper, u.length === 2 ? u : u[0], { solver: 'closedform', step: 0.5 });
const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol;
const trackIndex = (id) => copper.tracks.findIndex((t) => t.id === id);

test('bridges: a part whose nets are all picked (or supplies) joins two units; an IC with other nets does not', () => {
  const links = bridges(copper, [['SIG_A'], ['SIG_B'], ['TX_P', 'TX_N']]);
  // U1's nets are SIG_A, the pair and GND (a supply): with all of them picked it joins SIG_A to the pair
  assert.deepEqual(links.map((l) => [l.a, l.b, l.refs]).sort(), [[0, 1, ['R1']], [0, 2, ['U1']]]);
  assert.deepEqual(bridges(copper, [['SIG_A'], ['SIG_B']]).map((l) => l.refs), [['R1']]);
  const pair = bridges(copper, [['TX_CP', 'TX_CN'], ['TX_P', 'TX_N']]);
  assert.deepEqual(pair.map((l) => [l.a, l.b, l.refs.sort()]), [[0, 1, ['C1', 'C2']]]);
  assert.deepEqual(bridges(copper, [['SIG_A'], ['TX_CP', 'TX_CN']]), []);
});

test('chainOrder starts at an end, follows the parts, and marks a break', () => {
  const units = [['B'], ['A'], ['C'], ['D']];
  const links = [{ a: 0, b: 1, refs: ['R1'] }, { a: 0, b: 2, refs: ['R2'] }];
  const order = chainOrder(units, links);
  assert.deepEqual(order.map((o) => units[o.unit][0]), ['A', 'B', 'C', 'D']);
  assert.deepEqual(order.map((o) => o.via?.refs[0] ?? null), [null, 'R1', 'R2', null]);
});

test('routePath: two nets through R1 in path order, each one\'s route turned to continue from the part', () => {
  const units = [['SIG_A'], ['SIG_B']];
  const docs = units.map(analyse);
  const path = routePath(copper, units, docs, { targets: [null, { value: 50, tolerance_pct: 10 }] });
  assert.deepEqual(path.units.map((u) => u.nets[0]), ['SIG_A', 'SIG_B']);
  assert.deepEqual(path.units.map((u) => u.reversed), [false, true]); // SIG_B's own route starts at J1 (x = 4)
  assert.ok(near(path.length, 12 + 2.4 + 4 + 4 + 16));
  assert.ok(near(path.units[1].s0, 12) && path.units[1].target.value === 50);
  // picked the other way round: the path starts at the first one picked, its vias read the path's way
  const back = routePath(copper, [['SIG_B'], ['SIG_A']], [docs[1], docs[0]]);
  assert.deepEqual(back.units.map((u) => [u.nets[0], u.reversed]), [['SIG_B', false], ['SIG_A', true]]);
  assert.deepEqual(back.marks.filter((m) => ['end', 'part', 'via'].includes(m.type)).map((m) => [m.type, m.label, +m.s.toFixed(3)]), [
    ['end', 'J1', 0], ['via', 'F→B', 24], ['part', 'R1', 26.4], ['via', 'B→F', 30.4], ['end', 'U1', 38.4]]);
  // the parts and the vias along it, in order
  const marks = path.marks.map((m) => [m.type, m.label, +m.s.toFixed(3)]);
  assert.deepEqual(marks.filter((m) => ['end', 'part', 'via'].includes(m[0])), [
    ['end', 'U1', 0], ['via', 'F→B', 8], ['part', 'R1', 12], ['via', 'B→F', 14.4], ['end', 'J1', 38.4]]);
  assert.ok(marks.some((m) => m[0] === 'ref_change' || m[0] === 'via')); // F.Cu over GND, B.Cu under +3V3
  // the sections cover the whole path without overlapping, each one's layer as routed
  const secs = path.sections;
  assert.ok(near(secs[0].s0, 0) && near(secs.at(-1).s1, path.length));
  for (let i = 1; i < secs.length; i++) assert.ok(near(secs[i].s0, secs[i - 1].s1, 1e-6), `gap at ${secs[i].s0}`);
  assert.ok(secs.every((x) => x.z > 20 && x.z < 120));
  assert.equal(pathAt(path, 13).layer, 'B.Cu');
  assert.equal(pathAt(path, 15).layer, 'F.Cu');
  assert.ok(near(pathAt(path, 13).p[0], 14.6) && near(pathAt(path, 13).p[1], 4));
  assert.deepEqual(pathAt(path, 30).p.map((v) => +v.toFixed(6)), [12.4, 8]); // on the way back to J1
});

test('pathFromBoard: a point on a selected trace maps to its place on the path, and back', () => {
  const units = [['SIG_A'], ['SIG_B']];
  const path = routePath(copper, units, units.map(analyse));
  const hit = pathFromBoard(path, copper, 'SIG_B', [18, 4.05], trackIndex('b-top'));
  assert.ok(near(hit.s, 12 + 2.4 + 2), hit.s);
  assert.ok(near(Math.abs(hit.d[0]), 1) && near(hit.d[1], 0));
  assert.deepEqual(hit.p, [18, 4]);
  for (const s of [0.5, 7.9, 11, 12.7, 16, 19.1, 25, 37.9]) {
    const at = pathAt(path, s);
    const back = pathFromBoard(path, copper, at.unit.nets[0], at.p);
    assert.ok(near(back.s, s, 1e-3), `${s} → ${back.s}`);
  }
  assert.equal(pathFromBoard(path, copper, 'TX_P', [5, -3.8]), null); // not on the path
});

test('a pair through AC caps is one path; its second net maps onto the first one\'s route', () => {
  const units = [['TX_P', 'TX_N'], ['TX_CP', 'TX_CN']];
  const path = routePath(copper, units, units.map(analyse), { targets: [{ value: 90, tolerance_pct: 10 }, { value: 90, tolerance_pct: 10 }] });
  assert.deepEqual(path.units.map((u) => [u.nets[0], u.kind, u.key]), [['TX_P', 'differential', 'Zdiff'], ['TX_CP', 'differential', 'Zdiff']]);
  const part = path.marks.find((m) => m.type === 'part');
  assert.deepEqual([part.refs.sort(), +part.s.toFixed(3)], [['C1', 'C2'], 10]);
  assert.ok(near(path.length, 22.5));
  assert.ok(path.sections.every((x) => x.section.net === 'TX_P' || x.section.net === 'TX_CP'));
  assert.ok(path.sections.filter((x) => x.z != null).every((x) => x.z > 60 && x.z < 130)); // Zdiff
  const n = pathFromBoard(path, copper, 'TX_CN', [15, -4.2], trackIndex('n-b'));
  assert.ok(near(n.s, 10 + 3.5, 1e-3), n.s);
  assert.deepEqual(n.p.map((v) => +v.toFixed(6)), [15, -4.2]); // the cut is drawn on the trace under the pointer
});

test('lines that do not touch follow each other with a break', () => {
  const units = [['SIG_A'], ['TX_CP', 'TX_CN']];
  const path = routePath(copper, units, [null, null]);
  assert.deepEqual(path.marks.filter((m) => m.type === 'gap').map((m) => m.s), [12]);
  assert.equal(path.sections.length, 0); // nothing analysed yet: the units and their lengths only
  assert.ok(near(path.length, 12 + 12.5));
});

test('niceTicks: round steps', () => {
  assert.deepEqual(niceTicks(0, 38.4, 4), [0, 10, 20, 30]);
  assert.deepEqual(niceTicks(43.2, 57.1, 5), [45, 50, 55]);
  assert.deepEqual(niceTicks(43.2, 57.1, 8), [44, 46, 48, 50, 52, 54, 56]);
});
