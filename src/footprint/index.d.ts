import type * as THREE from 'three';
import type { Pad, Outline, Bounds, Mat4, Vec2, Vec3, Loop } from '../geom/index.js';
import type { Face } from '../board/index.js';
import type { Board, Component as BoardComponent, Footprint as BoardFootprint } from '../model/board.js';

export interface Graphic {
  layer: string; kind: 'line' | 'rect' | 'circle' | 'arc' | 'poly' | 'curve';
  /** KiCad frame (y down), arcs/circles/curves flattened */
  pts: Vec2[]; width: number; closed: boolean; filled: boolean;
}
export interface FootprintModel { path: string; offset: Vec3; rotate: Vec3; scale: Vec3; hide: boolean; opacity: number }
export interface Footprint { name: string; layer: string; attr: string[]; pads: Pad[]; graphics: Graphic[]; models: FootprintModel[] }

export function parseSexpr(text: string): any[];
export function parseKicadFootprint(text: string): Footprint;
export function arcThrough(start: Vec2, mid: Vec2, end: Vec2, segments?: number): Vec2[];

export const FOOTPRINT_COLORS: { mask: number; fr4: number; copper: number; silk: number; fab: number; courtyard: number };
export function chainLoops(polylines: Vec2[][], tol?: number): Loop[];
export function footprintOutline(fp: Footprint, margin?: number): Outline;
export function footprintModelMatrix(model: FootprintModel, thickness?: number): Mat4;
export function wallGeometry(loop: Loop, z0: number, z1: number): THREE.BufferGeometry;
/** A drill as parseExcellon / geom usableHoles take it, board frame of the footprint (y up), mm. */
export interface FootprintHole { x: number; y: number; diameter: number; plated?: boolean; x2?: number | null; y2?: number | null; function?: string | null }
export interface BuiltFootprint {
  group: THREE.Group;
  outline: Outline;
  thickness: number;
  uvBounds: Bounds;
  /** geom usableHoles report of the `holes` option (pad holes are left to their pads). */
  holes: { kept: unknown[]; leftOut: { count: number; total: number; largest_mm: number } | null; rejected: number };
  meshes: { board: THREE.Mesh; copper: THREE.Mesh[]; barrels: THREE.Mesh[]; paste: THREE.Mesh[]; silk: THREE.Mesh[]; fab: THREE.Mesh[]; courtyard: THREE.Mesh[] };
  /** Board-frame placement for one of fp.models (KiCad's model matrix on the top face). */
  modelMatrix(model: FootprintModel): Mat4;
  dispose(): void;
}
export function buildFootprint(fp: Footprint, options?: {
  thickness?: number; margin?: number; outline?: Outline; faces?: { top?: Face; bottom?: Face }; uvBounds?: Bounds;
  /** Layer pictures over uvBounds drawn as transparent sheets in the silk / fab / courtyard groups. */
  decals?: Partial<Record<'silk' | 'fab' | 'courtyard', { top?: Face; bottom?: Face }>>;
  colors?: Partial<{ mask: number; fr4: number; copper: number; paste: number; silk: number; fab: number; courtyard: number }>;
  /** Fill and cap plated round pad holes up to this drill diameter, mm (no hole, no barrel). */
  fillUpTo?: number | null;
  /** Build paste deposits on the pads' paste layers (group 'paste'); default false. */
  paste?: boolean;
  /** More drills punched through the board (vias, holes near the part); plated ones get a barrel. */
  holes?: FootprintHole[];
  /** Below 1: a see-through board (no depth write), so far-face pads and barrels show through it. Default 1. */
  boardOpacity?: number;
}): BuiltFootprint;
export type FootprintOptions = NonNullable<Parameters<typeof buildFootprint>[1]>;

/** A board model's component and its footprint, or null. */
export function componentOf(board: Board, ref: string): { component: BoardComponent; footprint: BoardFootprint } | null;
/** Footprint frame (buildFootprint's) -> board frame; a bottom-side part is turned over about its x axis. */
export function componentMatrix(component: Pick<BoardComponent, 'x' | 'y' | 'rotation' | 'side'>, thickness?: number): Mat4;
/** A board-frame point in the component's footprint frame (plan only). */
export function boardToComponent(component: Pick<BoardComponent, 'x' | 'y' | 'rotation' | 'side'>, p: Vec2): Vec2;
/** The board's drills inside the outline's box, footprint frame. */
export function drillsNear(board: Board, component: BoardComponent, outline: Outline): FootprintHole[];
/** buildFootprint for one component of a board model: its footprint, the stackup's thickness, the board's drills around it. */
export function buildComponent(board: Board, ref: string, options?: FootprintOptions & {
  /** false: only the footprint's own holes (default: the board's drills in the slice too). */
  drills?: boolean;
}): BuiltFootprint & { component: BoardComponent; footprint: BoardFootprint; matrix: Mat4 };
/** Which paste faces the pad is on (F.Paste, B.Paste, *.Paste). */
export function padPasteSides(pad: import('../geom/index.js').Pad): { top: boolean; bottom: boolean };
/** A plated round pad hole of drill diameter <= upTo mm. */
export function padFilled(pad: import('../geom/index.js').Pad, upTo: number | null | undefined): boolean;
