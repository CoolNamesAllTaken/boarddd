// One placed component of a board model (boarddd/board@1) on a slice of its board: the footprint's pads on every
// copper face they are on, plated barrels, its holes and the board's other drills around it punched through a
// slice as thick as the stackup says. Built in the footprint's frame (buildFootprint's: y up, the component's own
// face on top at z = thickness); `matrix` puts it where it sits in the board frame, bottom-side parts flipped.

import { BOARD_THICKNESS } from '../geom/index.js';
import { buildFootprint, footprintOutline } from './build.js';

/** The component `ref` of a board model and its footprint: {component, footprint}, or null. */
export function componentOf(board, ref) {
  const component = (board?.components || []).find((c) => c.ref === ref);
  if (!component) return null;
  const footprint = board.footprints?.[component.footprint];
  return footprint ? { component, footprint } : null;
}

/**
 * Footprint frame (buildFootprint's: x right, y up, z 0 .. thickness with the part's own face on top) -> board
 * frame (the board solid's: bottom face z = 0, top face z = thickness), column-major 4x4. A bottom-side part is
 * turned over about its x axis (KiCad's flip: y and z mirrored), so its own face lands on the bottom face.
 */
export function componentMatrix(component, thickness = BOARD_THICKNESS) {
  const a = ((Number(component.rotation) || 0) * Math.PI) / 180;
  const c = Math.cos(a), s = Math.sin(a);
  const x = Number(component.x) || 0, y = Number(component.y) || 0;
  if (component.side === 'bottom') return [c, s, 0, 0, s, -c, 0, 0, 0, 0, -1, 0, x, y, thickness, 1];
  return [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, x, y, 0, 1];
}

/** A board-frame point (x, y) in the component's footprint frame (the inverse of componentMatrix, in plan). */
export function boardToComponent(component, [px, py]) {
  const a = ((Number(component.rotation) || 0) * Math.PI) / 180;
  const c = Math.cos(a), s = Math.sin(a);
  const dx = px - (Number(component.x) || 0), dy = py - (Number(component.y) || 0);
  const qx = c * dx + s * dy, qy = -s * dx + c * dy;
  return component.side === 'bottom' ? [qx, -qy] : [qx, qy];
}

/**
 * The board's drills inside `outline`'s box, in the component's footprint frame ({x, y, x2, y2, diameter, plated,
 * function}): vias and holes near the part. Its own pad holes are among them; buildFootprint leaves those to the pads.
 */
export function drillsNear(board, component, outline) {
  const xs = outline.board.map((p) => p[0]), ys = outline.board.map((p) => p[1]);
  const box = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  const out = [];
  for (const d of board?.drills || []) {
    const [x, y] = boardToComponent(component, [d.x, d.y]);
    const slot = d.x2 != null && d.y2 != null;
    const [x2, y2] = slot ? boardToComponent(component, [d.x2, d.y2]) : [null, null];
    const ends = slot ? [[x, y], [x2, y2]] : [[x, y]];
    if (!ends.every(([u, v]) => u >= box[0] && u <= box[2] && v >= box[1] && v <= box[3])) continue;
    out.push({ x, y, x2, y2, diameter: d.diameter, plated: d.plated !== false, function: d.function ?? null });
  }
  return out;
}

/**
 * buildFootprint for the component `ref` of a board model (`board.json`: its footprint, the stackup's thickness,
 * the board's drills around it). Options are buildFootprint's, plus:
 *   drills   false: only the footprint's own holes (default: the board's drills in the slice too)
 * The slice is the footprint's outline (Edge.Cuts in the footprint, else the courtyard's box, else the pads',
 * grown by `margin`), unless `outline` is given (footprint frame).
 * Returns buildFootprint's result plus {component, footprint, matrix}: `matrix` (componentMatrix) places the
 * group in the board frame.
 */
export function buildComponent(board, ref, options = {}) {
  const found = componentOf(board, ref);
  if (!found) throw new Error(`boarddd: no component ${ref} with a footprint in this board`);
  const { component, footprint } = found;
  const thickness = options.thickness ?? (Number(board.stackup?.thickness) || BOARD_THICKNESS);
  const outline = options.outline || footprintOutline(footprint, options.margin ?? 1);
  const holes = [...(options.drills === false ? [] : drillsNear(board, component, outline)), ...(options.holes || [])];
  const built = buildFootprint(footprint, { ...options, thickness, outline, holes });
  built.group.name = `component-${ref}`;
  return { ...built, component, footprint, matrix: componentMatrix(component, thickness) };
}
