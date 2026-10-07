// What boarddd's browser path (src/models: occt-import-js, mapNodesToRefs, splitBoardBodies,
// measureBoard) makes of a board STEP, as JSON for the Python parity test (test_step_parity_js.py).
//
//   node python/tests/step/js_step_dump.mjs board.step pos.csv > out.json
//
// pos.csv: kicad-cli's CSV (Ref, Val, Package, PosX, PosY, Rot, Side), board frame (y up).
// Output: {board: {top, bottom, thickness}, method, unmatched, refs: {ref: {min, max}}}: each matched
// designator's tessellated box in the STEP frame, mm.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { readStep } from '../../../src/models/step.js';
import { boardKindFromName, mapNodesToRefs, measureBoard, splitBoardBodies } from '../../../src/models/match.js';

const require = createRequire(import.meta.url);
const [stepPath, posPath] = process.argv.slice(2);

function placements(text) {
  const [head, ...rows] = text.trim().split(/\r?\n/);
  const cols = head.split(',').map((c) => c.replace(/"/g, '').trim());
  const at = (name) => cols.indexOf(name);
  return rows.map((line) => {
    const cells = line.split(',').map((c) => c.replace(/"/g, '').trim());
    return { ref: cells[at('Ref')], x: Number(cells[at('PosX')]), y: Number(cells[at('PosY')]) };
  });
}

function boxOf(data, node, box = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] }) {
  for (const m of node.meshes || []) {
    const p = data.meshes[m].position;
    for (let i = 0; i < p.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        box.min[k] = Math.min(box.min[k], p[i + k]);
        box.max[k] = Math.max(box.max[k], p[i + k]);
      }
    }
  }
  for (const child of node.children || []) boxOf(data, child, box);
  return box;
}

const occtimportjs = require('occt-import-js');
const data = await readStep(new Uint8Array(readFileSync(stepPath)), { occtFactory: () => occtimportjs(), workerUrl: false });
// The board assembly: unwrap single-child shells, as the Python splitter does.
let root = data.root;
while ((root.children || []).length === 1 && !(root.meshes || []).length) root = root.children[0];
const nodes = root.children.map((child) => {
  const box = boxOf(data, child);
  const c = box.min.map((v, k) => (v + box.max[k]) / 2);
  return { name: child.name || '', box, x: c[0], y: c[1], cx: c[0], cy: c[1], cz: c[2] };
}).filter((n) => Number.isFinite(n.box.min[0]));

const outline = nodes.find((n) => boardKindFromName(n.name) === 'substrate');
const area = outline ? (outline.box.max[0] - outline.box.min[0]) * (outline.box.max[1] - outline.box.min[1]) : 0;
const split = splitBoardBodies(nodes, area);
const bodies = split.board.map((i) => ({ kind: boardKindFromName(nodes[i].name), box: nodes[i].box }));
const parts = split.components.map((i) => nodes[i]);
const match = mapNodesToRefs(parts, placements(readFileSync(posPath, 'utf8')), { frame: 'board' });
const refs = {};
for (const [ref, idx] of match.byRef) {
  const box = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
  for (const i of idx) for (let k = 0; k < 3; k++) {
    box.min[k] = Math.min(box.min[k], parts[i].box.min[k]);
    box.max[k] = Math.max(box.max[k], parts[i].box.max[k]);
  }
  refs[ref] = box;
}
process.stdout.write(JSON.stringify({
  board: measureBoard(bodies), bodies: split.board.map((i) => nodes[i].name), method: match.method,
  unmatched: match.unmatched, leftover: match.leftover.map((i) => parts[i].name), refs,
}));
