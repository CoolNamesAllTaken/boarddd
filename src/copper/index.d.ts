// boarddd/copper: the copper model (boarddd/copper@1); see docs/copper.md.
import type { Copper, FillPolygon, Plane, Track, Zone, CopperPad, Vec2 } from './copper.js';
export * from './copper.js';

export const SCHEMA_ID: 'boarddd/copper@1';
/** schema/copper.schema.json (JSON Schema 2020-12), as an object. */
export const SCHEMA: Record<string, unknown>;
/** 'json/pointer: message' errors; empty when `copper` is a valid boarddd/copper@1 document. */
export function validateCopper(copper: unknown): string[];
/** validateCopper that throws on errors; returns the document. */
export function assertCopper(copper: unknown): Copper;

/** A pour covering at least this share of the board's area is a solid plane. */
export const PLANE_COVERAGE: number;
/** Shoelace area: > 0 counter-clockwise (board frame, y up). */
export function signedArea(pts: Vec2[]): number;
/** Even-odd ray cast. */
export function pointInRing(p: Vec2, ring: Vec2[]): boolean;
/** A fractured polygon (KiCad filled_polygon, Gerber region with cut-ins) as polygons with holes. */
export function unfracture(ring: Vec2[]): FillPolygon[];
/** Filled area of polygons with holes, mm^2. */
export function fillArea(fill: FillPolygon[]): number;
/** Whether a point is inside a polygon with holes. */
export function inFill(p: Vec2, poly: FillPolygon): boolean;
/** A full circle as two half arcs [start, end, mid] (tracks have no full circles). */
export function circleHalves(center: Vec2, start: Vec2): [Vec2, Vec2, Vec2][];
/** Pour area per (layer, net), teardrops and no-net copper excluded. */
export function planes(zones: Zone[], boardArea: number | null, layers?: string[]): Plane[];
/** Mark zones whose fill holds copper of another net as stale (with a warning each); returns how many. */
export function checkFills(copper: Copper): number;

/** Gerber X2 aperture functions of pads, lower case. */
export const PAD_FUNCTIONS: Set<string>;
/** One copper Gerber's objects, before vias are joined across layers. */
export interface LayerCopper {
  layer: string;
  tracks: Track[];
  pads: CopperPad[];
  zones: Zone[];
  /** [centre, diameter, net] of each ViaPad flash. */
  viaRings: [Vec2, number, string][];
  /** What was skipped, by reason. */
  skipped: Record<string, number>;
  generator: string | null;
}
/** One copper Gerber's tracks, pads, regions and via rings, board frame, mm. */
export function parseCopperLayer(text: string, layer: string, options?: { segments?: number }): LayerCopper;
/** A fab file: its name and its text (or bytes). */
export interface GerberFile {
  name: string;
  text?: string;
  content?: string | Uint8Array;
  data?: Uint8Array;
}
/** boarddd/copper@1 from Gerber X2 copper layers (+ Excellon drills, + an outline for plane coverage). */
export function copperFromGerbers(
  files: GerberFile[] | Record<string, string>,
  options?: { name?: string; checkFills?: boolean; segments?: number },
): Copper;
