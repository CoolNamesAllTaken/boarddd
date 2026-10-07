// src/models/match.js: name-then-position matching, board bodies, board measurement.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  refFromName, naturalCompare, houghTranslation, matchByPosition, mapNodesToRefs, toBoardFrame,
  splitBoardBodies, measureBoard, boardKindFromName, boardKindFromLook,
} from '../../src/models/match.js';

const REFS = new Set(['R1', 'R11', 'U3', 'C10', 'J1']);

test('refFromName: exact, copy suffixes, leading token', () => {
  assert.equal(refFromName('R1', REFS), 'R1');
  assert.equal(refFromName('R1_1', REFS), 'R1');
  assert.equal(refFromName('U3 (2)', REFS), 'U3');
  assert.equal(refFromName('U3:1', REFS), 'U3');
  assert.equal(refFromName('J1 [conn]', REFS), 'J1');
  assert.equal(refFromName(' C10 ', REFS), 'C10');
});

test('refFromName: never strips digits without a separator, ignores opaque names', () => {
  assert.equal(refFromName('R11', REFS), 'R11');
  assert.equal(refFromName('R111', REFS), null);
  assert.equal(refFromName('=>[0:1:1:3]', REFS), null);
  assert.equal(refFromName('pic_programmer_PCB', REFS), null);
  assert.equal(refFromName('', REFS), null);
  assert.equal(refFromName(undefined, REFS), null);
});

test('naturalCompare sorts designators like a person would', () => {
  const refs = ['R10', 'R2', 'C1', 'R1', 'U1A', 'U1', 'R100'];
  assert.deepEqual(refs.sort(naturalCompare), ['C1', 'R1', 'R2', 'R10', 'R100', 'U1', 'U1A']);
});

function board(n = 40, seed = 1) {
  let s = seed;
  const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  return Array.from({ length: n }, (_, i) => ({ ref: `R${i + 1}`, x: 100 + rand() * 80, y: 50 + rand() * 60 }));
}
function nodesFor(components, shift, { names = false, jitter = 0 } = {}) {
  return components.map((c, i) => {
    const a = toBoardFrame(c);
    const x = a.x + shift.x + (i % 2 ? jitter : -jitter), y = a.y + shift.y;
    return { name: names ? c.ref : `=>[0:1:1:${i}]`, x, y, cx: x + 0.3, cy: y };
  });
}

test('toBoardFrame flips KiCad y', () => {
  assert.deepEqual(toBoardFrame({ x: 3, y: 4 }), { x: 3, y: -4 });
});

test('houghTranslation recovers an unknown export origin', () => {
  const comps = board();
  const shift = { x: -140, y: 80 };
  const t = houghTranslation(nodesFor(comps, shift), comps.map(toBoardFrame));
  assert.ok(Math.abs(t.x - shift.x) < 0.05 && Math.abs(t.y - shift.y) < 0.05, JSON.stringify(t));
});

test('mapNodesToRefs by name: offset from the named nodes, all matched', () => {
  const comps = board(30);
  const r = mapNodesToRefs(nodesFor(comps, { x: 5, y: -7 }, { names: true }), comps);
  assert.equal(r.method, 'name');
  assert.equal(r.byRef.size, 30);
  assert.ok(Math.abs(r.offset.x - 5) < 1e-9 && Math.abs(r.offset.y + 7) < 1e-9);
  assert.deepEqual(r.leftover, []);
});

test('mapNodesToRefs by position: shuffled opaque nodes, shifted origin', () => {
  const comps = board(60, 7);
  const nodes = nodesFor(comps, { x: 12.5, y: -3 }, { jitter: 0.05 });
  const order = nodes.map((n, i) => i).reverse();
  const r = mapNodesToRefs(order.map((i) => nodes[i]), comps);
  assert.equal(r.method, 'position');
  assert.equal(r.byRef.size, 60);
  for (const [ref, [k]] of r.byRef) assert.equal(`R${order[k] + 1}`, ref);
});

test('mapNodesToRefs mixed: names for some, positions for the rest', () => {
  const comps = board(20, 3);
  const nodes = nodesFor(comps, { x: 0, y: 0 });
  nodes[0].name = 'R1'; nodes[1].name = 'R2'; nodes[2].name = 'R3';
  const r = mapNodesToRefs(nodes, comps);
  assert.equal(r.method, 'mixed');
  assert.equal(r.byName, 3);
  assert.equal(r.byRef.size, 20);
});

test('mapNodesToRefs: a part split over two nodes keeps both; far nodes stay leftover', () => {
  const comps = [{ ref: 'U1', x: 10, y: 10 }, { ref: 'R1', x: 30, y: 10 }, { ref: 'R2', x: 50, y: 20 }];
  const nodes = [
    { name: 'U1', x: 10, y: -10, cx: 10, cy: -10 },
    { name: 'pins', x: 10.2, y: -10, cx: 10.2, cy: -10 },
    { name: 'R1', x: 30, y: -10, cx: 30, cy: -10 },
    { name: 'R2', x: 50, y: -20, cx: 50, cy: -20 },
    { name: 'stray', x: 90, y: -90, cx: 90, cy: -90 },
  ];
  const r = mapNodesToRefs(nodes, comps);
  assert.deepEqual(r.byRef.get('U1'), [0, 1]);
  assert.deepEqual(r.leftover, [4]);
});

test('matchByPosition refuses a coin toss, then settles mutual nearest pairs', () => {
  const nodes = [{ x: 0, y: 0, cx: 0, cy: 0 }, { x: 0.4, y: 0, cx: 0.4, cy: 0 }];
  const r = matchByPosition(nodes, [{ ref: 'A', aim: { x: 0.01, y: 0 } }, { ref: 'B', aim: { x: 0.39, y: 0 } }]);
  assert.equal(r.matched.get('A'), 0);
  assert.equal(r.matched.get('B'), 1);
  const r2 = matchByPosition(nodes, [{ ref: 'C', aim: { x: 20, y: 0 } }]);
  assert.deepEqual(r2.unmatched, ['C']);
});

test('matchByPosition: too close to call in the first pass, settled as mutual nearest', () => {
  const nodes = [{ x: 0.2, y: 0, cx: 0.2, cy: 0 }, { x: 0.3, y: 0, cx: 0.3, cy: 0 }];
  const r = matchByPosition(nodes, [{ ref: 'A', aim: { x: 0, y: 0 } }, { ref: 'B', aim: { x: 0.5, y: 0 } }]);
  assert.deepEqual([...r.matched], [['A', 0], ['B', 1]]);
  assert.deepEqual(r.ambiguous, []);
  // Beyond tolerance is unmatched, not ambiguous.
  assert.deepEqual(matchByPosition(nodes, [{ ref: 'C', aim: { x: 2.4, y: 0 } }]).unmatched, ['C']);
});

test('matchByPosition: a model-less part listed first does not take its neighbour\'s node', () => {
  // C18 has no model; Y2's node is 1.2 mm from C18 and on Y2. C18 comes first in the list.
  const nodes = [{ x: 10, y: 0, cx: 10, cy: 0 }];
  const r = matchByPosition(nodes, [{ ref: 'C18', aim: { x: 8.8, y: 0 } }, { ref: 'Y2', aim: { x: 10, y: 0 } }]);
  assert.deepEqual([...r.matched], [['Y2', 0]]);
  assert.deepEqual(r.unmatched, ['C18']);
  assert.deepEqual(r.ambiguous, []);
});

test('mapNodesToRefs: a module claims the anonymous solids inside its box, after the rest', () => {
  const comps = [
    { ref: 'M1', x: 20, y: 20, assembly: true, box: [10, 10, 30, 30] },
    { ref: 'U9', x: 15, y: 15 },             // a can inside the module's box: its own part
    { ref: 'R1', x: 50, y: 50 },
  ];
  const at = (x, y, name = '') => ({ name, x, y: -y, cx: x, cy: -y });
  const nodes = [at(15, 15), at(50, 50), at(12, 22), at(25, 28), at(29, 11), at(80, 80)];
  const r = mapNodesToRefs(nodes, comps);
  assert.deepEqual(r.byRef.get('U9'), [0]);
  assert.deepEqual(r.byRef.get('R1'), [1]);
  assert.deepEqual(r.byRef.get('M1'), [2, 3, 4]);
  assert.deepEqual(r.leftover, [5]);
});

test('mapNodesToRefs: nothing to go by falls back to the given offset', () => {
  const r = mapNodesToRefs([], [{ ref: 'R1', x: 1, y: 1 }], { fallbackOffset: { x: 3, y: 4 } });
  assert.deepEqual(r.offset, { x: 3, y: 4 });
  assert.deepEqual(r.unmatched, ['R1']);
  assert.equal(r.method, 'none');
});

test('board body names as kicad-cli writes them', () => {
  assert.equal(boardKindFromName('=>[0:1:1:89] pic_programmer_PCB'), 'substrate');
  assert.equal(boardKindFromName('x pic_programmer_silkscreen'), 'silk');
  assert.equal(boardKindFromName('x pic_programmer_soldermask'), 'mask');
  assert.equal(boardKindFromName('x pic_programmer_pad'), 'copper');
  assert.equal(boardKindFromName('R12'), null);
  assert.equal(boardKindFromLook(null, 1.6), 'substrate');
  assert.equal(boardKindFromLook({ h: 0, s: 0, l: 0.95 }, 0.01), 'silk');
  assert.equal(boardKindFromLook({ h: 0.1, s: 0.6, l: 0.5 }, 0.035), 'copper');
  assert.equal(boardKindFromLook({ h: 0.4, s: 0.6, l: 0.3 }, 0.02), 'mask');
});

const box = (min, max) => ({ box: { min, max } });

test('splitBoardBodies: flat board-wide films are board, a board-wide shield can is not', () => {
  const nodes = [
    box([0, 0, 0], [20, 20, 0.8]),           // substrate
    box([0, 0, 0.8], [20, 20, 0.81]),        // mask film
    box([1, 1, 0.8], [19, 19, 2.55]),        // shield can over 81 % of the board
    box([5, 5, 0.8], [6, 6, 1.2]),           // a resistor
  ];
  const r = splitBoardBodies(nodes, 400);
  assert.deepEqual(r.board, [0, 1]);
  assert.deepEqual(r.components, [2, 3]);
});

test('splitBoardBodies with measured boxes: closest pair first', () => {
  const nodes = [box([0, 0, -0.065], [20, 20, 0]), box([0, 0, 0], [20, 20, 0.01]), box([0, 0, 0], [20, 20, 1.6])];
  const measured = [{ box: [0, 0, 0, 20, 20, 0.01], name: 'board_silk' }, { box: [0, 0, 0, 20, 20, 1.6], substrate: true }];
  const r = splitBoardBodies(nodes, 400, measured);
  assert.deepEqual(r.board, [1, 2]);
  assert.equal(r.measuredBy.get(1).name, 'board_silk');
  assert.deepEqual(r.components, [0]);
});

test('measureBoard: the substrate, not the film stack or the thicker pad body', () => {
  const r = measureBoard([
    { kind: 'copper', box: { min: [0, 0, -0.04], max: [1, 1, 0.78] } },
    { kind: 'substrate', box: { min: [0, 0, 0], max: [1, 1, 0.741] } },
    { kind: 'mask', box: { min: [0, 0, 0.741], max: [1, 1, 0.76] } },
  ]);
  assert.deepEqual(r, { bottom: 0, top: 0.741, thickness: 0.741 });
  assert.equal(measureBoard([]), null);
  assert.equal(measureBoard([{ box: { min: [0, 0, 0], max: [1, 1, 1] } }]).thickness, 1);
});
