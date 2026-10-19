// boarddd/nets: choosing nets by group; see docs/nets.md.
import type { Board } from '../model/board.js';
import type { Copper } from '../copper/copper.js';

export interface ParsedPattern {
  text: string;
  /** 're:' pattern. */
  regex: boolean;
  /** [positive half, negative half] of a pair pattern ('*_{P,N}', '*_P/*_N'). */
  pair: [string, string] | null;
  /** Why it matches nothing (bad syntax, empty). */
  error: string | null;
}
export interface PatternMatch extends ParsedPattern {
  nets: string[];
  pairs: Array<[string, string]>;
}
export interface NetMatch {
  /** Every matched net, in the order of the names given. */
  nets: string[];
  /** Pairs from pair patterns, then pairsAmong the other matches. */
  pairs: Array<[string, string]>;
  patterns: PatternMatch[];
}
/** Parse a pattern: wildcards * ? [abc] [!abc], alternatives {a,b}, pairs *_{P,N} or *_P/*_N, regex 're:...'. */
export function parsePattern(text: string, options?: { caseSensitive?: boolean }): ParsedPattern;
export function matchesPattern(pattern: string | ParsedPattern, name: string, options?: { caseSensitive?: boolean }): boolean;
/**
 * Match patterns against net names (case-insensitive unless `caseSensitive`, as KiCad's netclass patterns; a pattern
 * without '/' also matches a name's last part).
 */
export function matchNets(names: string[], patterns: string | string[], options?: { board?: Pick<Board, 'nets'> | null; caseSensitive?: boolean }): NetMatch;
/** Differential pairs among nets: board@1 Net.pair, else the naming rule (+/-, _P/_N, P/N). */
export function pairsAmong(nets: string[], options?: { board?: Pick<Board, 'nets'> | null }): Array<[string, string]>;

export interface SimilarNet {
  net: string;
  /** Length of its tracks at the width on the layer, mm. */
  length: number;
  tracks: number;
  /** Its partner, when that matched too. */
  pair: string | null;
  /** A solid-plane net (left out by default). */
  plane: boolean;
}
export interface SimilarPair { nets: [string, string]; gap: number; length: number }
export interface Similar {
  kind: 'single' | 'differential';
  layer: string;
  width: number | null;
  /** The seed pair's gap, mm. */
  gap: number | null;
  nets: SimilarNet[];
  pairs: SimilarPair[];
}
/** Nets with tracks like the seed's: same width on the same layer; for a pair seed, pairs with the same gap. */
export function similarTracks(copper: Copper, seed: { layer: string; width?: number | null; nets: string[] }, options?: { board?: Pick<Board, 'nets'> | null }): Similar;
/** Edge-to-edge gap between two nets' parallel tracks on a layer (the gap most of the coupled length has). */
export function pairGap(copper: Copper, a: string, b: string, options: { layer: string; width?: number | null }): { gap: number; length: number } | null;
/**
 * The net running beside `net` as a pair partner would (parallel at one gap for at least `share`, default 0.5, of its
 * routed length on `layer`, default its main layer): for boards whose nets have no names to pair them by.
 */
export function parallelPartner(copper: Copper, net: string, options?: { layer?: string | null; share?: number; exclude?: string[] }): { net: string; gap: number; length: number; share: number; layer: string } | null;
/** mm: max(1 µm, 0.5 % of w). */
export function widthTolerance(w: number): number;
/** mm: max(2 µm, 2 % of g). */
export function gapTolerance(g: number): number;
