// A synthetic GLB laid out like `kicad-cli pcb export glb` (KiCad 10): metres, y up, glTF
// (x, y, z) = (KiCad x, height, KiCad y), a node per component named by its reference (or by its
// model name when `named` is false), board bodies as unnamed nodes whose mesh names carry the kind.
import { GlbBuilder } from './glb_writer.mjs';

export const BOARD = { origin: [100, 50], size: [40, 30] };

export function components() {
  const out = [];
  let n = 0;
  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 6; c++, n++) {
      out.push({ ref: `${n % 3 ? 'R' : 'C'}${n + 1}`, x: 103 + c * 6.5 + (r % 2) * 0.7, y: 54 + r * 7, side: n % 5 === 4 ? 'bottom' : 'top', size: [1.6, 0.8, 0.5 + (n % 4) * 0.2] });
    }
  }
  return out;
}

export function buildMock({ named = true, origin = [0, 0], can = false, thickness = 1.6 } = {}) {
  const g = new GlbBuilder();
  const M = 0.001;
  const root = g.node({ name: 'mock-board' });
  const body = g.material([0.1, 0.1, 0.12, 1]);
  const parts = components();
  for (const c of parts) {
    const bottom = c.side === 'bottom';
    const [L, W, H] = c.size.map((v) => v * M);
    const node = g.node({
      name: named ? c.ref : 'R_0603_1608Metric',
      translation: [(c.x - origin[0]) * M, (bottom ? 0 : thickness) * M, (c.y - origin[1]) * M],
      rotation: bottom ? [1, 0, 0, 0] : [0, 0, 0, 1],
    }, root);
    g.node({ name: `=>[0:1:1:${node}]`, mesh: g.boxMesh(`m${node}`, [{ min: [-L / 2, 0, -W / 2], max: [L / 2, H, W / 2], material: body }]) }, node);
  }
  const [bx, by] = BOARD.origin.map((v, i) => (v - origin[i]) * M);
  const [bw, bh] = BOARD.size.map((v) => v * M);
  const T = thickness * M;
  const layer = (label, y0, y1, rgba) => g.node({ name: `=>[0:1:1:${900 + g.json.nodes.length}]`,
    mesh: g.boxMesh(`mock_${label}`, [{ min: [bx, y0, by], max: [bx + bw, y1, by + bh], material: g.material(rgba) }]) }, root);
  layer('PCB', 0, T, [0.42, 0.45, 0.29, 1]);
  layer('soldermask', T, T + 0.01 * M, [0.06, 0.16, 0.11, 1]);
  layer('silkscreen', T + 0.01 * M, T + 0.011 * M, [0.96, 0.96, 0.96, 1]);
  if (can) {
    // A shield can over most of the board, with an opaque name.
    g.node({ name: 'SHIELD', translation: [bx + bw / 2, T, by + bh / 2],
      mesh: g.boxMesh('can', [{ min: [-bw * 0.45, 0, -bh * 0.45], max: [bw * 0.45, 2 * M, bh * 0.45], material: g.material([0.8, 0.8, 0.82, 1]) }]) }, root);
  }
  return { buffer: g.toBuffer(), parts };
}
