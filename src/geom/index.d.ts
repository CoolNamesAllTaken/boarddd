export type Vec2 = [number, number];
export type Vec3 = [number, number, number];
export type Mat4 = number[];

export const BOARD_THICKNESS: number;
export const COPPER_THICKNESS: number;
export function kicadToBoard(x: number, y: number): Vec2;
export function boardToKicad(x: number, y: number): Vec2;
export interface KicadModelPlacement { offset?: Vec3; rotate?: Vec3; scale?: Vec3 }
export function kicadModelMatrix(model?: KicadModelPlacement): Mat4;
export function applyMatrix(m: Mat4, p: Vec3): Vec3;
