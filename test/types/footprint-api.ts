import { buildComponent, componentMatrix, boardToComponent, type FootprintHole } from "../../src/footprint/index.js";
import type { Board } from "../../src/model/index.js";
import type { Mat4, Vec2 } from "../../src/geom/index.js";

declare const board: Board;

const built = buildComponent(board, "K1", { boardOpacity: 0.5, drills: false, margin: 2 });
const matrix: Mat4 = built.matrix;
const side: string = built.component.side;
const pads: number = built.footprint.pads?.length ?? 0;
const kept: number = built.holes.kept.length;
const m: Mat4 = componentMatrix({ x: 1, y: 2, rotation: 90, side: "bottom" }, 1.6);
const p: Vec2 = boardToComponent(built.component, [1, 2]);
const hole: FootprintHole = { x: 0, y: 0, diameter: 0.3, plated: true };
built.dispose();
export { matrix, side, pads, kept, m, p, hole };
