// boarddd/model: the normalised board model (boarddd/board@1); see docs/model.md.
import type { Board, Component, Vec2 } from './board.js';
export * from './board.js';

export const SCHEMA_ID: 'boarddd/board@1';
/** schema/board.schema.json (JSON Schema 2020-12), as an object. */
export const SCHEMA: Record<string, unknown>;
/** 'json/pointer: message' errors; empty when `board` is a valid boarddd/board@1 document. */
export function validateBoard(board: unknown): string[];
/** validateBoard that throws on errors; returns the board. */
export function assertBoard(board: unknown): Board;
/** Board frame (y up) -> KiCad frame (y down). */
export const toKicad: (p: Vec2) => Vec2;
/** KiCad frame (y down) -> board frame (y up). */
export const toBoard: (p: Vec2) => Vec2;
/** A footprint-frame point (KiCad footprint coordinates, y down) placed by a component, in the board frame. */
export function footprintToBoard(component: Pick<Component, 'x' | 'y' | 'rotation' | 'side'>, p: Vec2): Vec2;
