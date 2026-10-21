// boarddd/impedance/ui: click-a-trace impedance components for a boarddd/view2d stage; see docs/impedance.md "UI".
import type { Board } from '../../model/board.js';
import type { Copper, Track } from '../../copper/copper.js';
import type { HitIndex, Stage, DrawContent, Region } from '../../view2d/index.js';
import type { ImpedanceAnalysis, Section, Discontinuity, Target } from '../result.js';
import type { RouteOptions, RouteStep } from '../index.js';

export type Vec2 = [number, number];

/** What a copper pick finds. */
export interface CopperHit {
  kind: 'track' | 'pad' | 'via' | 'zone';
  net: string;
  layer: string;
  /** Every layer it is on (pads, vias). */
  layers?: string[];
  /** Track width (vias: the ring diameter), mm; null for pads and zones. */
  width: number | null;
  /** Index of the copper@1 track, for tracks. */
  track: number | null;
  ref?: string | null;
  number?: string;
}

/** A view2d hit index over a copper@1 document's tracks, arcs, pads, vias and zones. */
export function copperHitIndex(copper: Copper, options?: { layers?: string[] | null }): HitIndex<CopperHit>;
/** The differential partner of a net (board@1 Net.pair, else KiCad's naming rule among `names`), or null. */
export function pairOf(net: string, options?: { board?: Pick<Board, 'nets'> | null; names?: string[] }): string | null;
/** A pair in KiCad's order ('+' / 'P' first). */
export function pairOrder(a: string, b: string): [string, string];
/** A track's centre line (an arc flattened). */
export function trackPath(t: Track): Vec2[];
export function arcPoints(t: Track): Vec2[];

/** Copper colours per layer. */
export const LAYER_COLORS: string[];
/** A view2d draw content of a copper@1 document (boards without Gerbers). */
export function copperContent(copper: Copper, options?: { layers?: string[] | null; colors?: Record<string, string>; zoneAlpha?: number; alpha?: number }): DrawContent;
/** The points of a route between s0 and s1 (over a section's tracks). */
export function routeSlice(route: RouteStep[], s0: number, s1: number, tracks?: string[] | null): Array<{ layer: string; width: number; points: Vec2[] }>;
/** The point at distance s along a route. */
export function routePoint(route: RouteStep[], s: number, tracks?: string[] | null): { p: Vec2; layer: string } | null;

export interface Highlight {
  readonly selection: { nets: string[]; track: number | null; target?: { value: number; tolerance_pct?: number } };
  set(sel: { nets?: string[]; track?: number | null }): void;
  section(s: Section | null): void;
  marker(p: Vec2 | null): void;
  /** The cross-section's cut at a point of a trace (d: the trace's direction; half: mm, at least 14 px). */
  cross(c: { p: Vec2; d: Vec2; half?: number } | null): void;
  rings(points: Vec2[] | null): void;
  planes(list: Array<{ layer: string; net?: string | null }>): void;
  routeOf(net: string): RouteStep[];
  /** The layer on show: the nets' copper on other layers is drawn fainter (null: every layer alike). */
  focus(layer: string | null): void;
  invalidate(): void;
  remove(): void;
}
/** An overlay showing the selected nets over a dimmed board, a route section, a marker and rings. */
export function createHighlight(stage: Stage, copper: Copper, options?: { dim?: number }): Highlight;

export interface Selection { nets: string[]; track: number | null; hit?: CopperHit | null }
/** Pointer picking on a stage: hover tooltip (net, width), click selects the net / pair, alt-click one segment. */
export function attachPicker(stage: Stage, copper: Copper, options?: {
  board?: Pick<Board, 'nets'> | null;
  onSelect?: (s: Selection) => void;
  onHover?: (hit: CopperHit | null, e: unknown) => void;
  pairs?: boolean;
  layers?: string[] | null | (() => string[] | null);
  slackPx?: number;
}): { index: HitIndex<CopperHit>; pick(x: number, y: number): CopperHit | null; detach(): void };

/** Board mm of a boarddd/scene pick on the board solid (null for a part). */
export function boardPointFromPick(hit: { object?: unknown; point?: unknown } | null): { x: number; y: number; z: number } | null;

export interface Analyzer {
  readonly mode: 'worker' | 'main';
  analyze(nets: string | [string, string], options?: RouteOptions, onProgress?: (p: { phase: 'route' | 'solve'; done: number; total: number }) => void): Promise<ImpedanceAnalysis>;
  destroy(): void;
}
/** analyzeNet in a module Worker (main thread on file:// or when workers are unavailable). */
export function createAnalyzer(options: { board: Board; copper: Copper; worker?: boolean | string | URL }): Analyzer;

/** The layout a section was solved with, and its field-solver geometry. */
export function sectionLayout(section: Section): { traces: Array<{ x0: number; x1: number; net: string }>; grounds: Array<{ x0: number | null; x1: number | null }>; planes: { top: { x0: number | null; x1: number | null } | null; bottom: { x0: number | null; x1: number | null } | null } };
export function sectionGeometry(board: Board, section: Section): { conductors: unknown[]; dielectrics: unknown[]; layout: ReturnType<typeof sectionLayout> } | null;
/** An SVG cross-section of an impedance@1 section on the board's stackup (dimensions on hover). */
export function crossSection(el: HTMLElement, section: Section | null, options: { board: Board; width?: number | null; height?: number | null; margin?: number | null }): SVGSVGElement | HTMLDivElement;

/** Short names and words per structure. */
export const STRUCTURES: Record<Section['structure'], [string, string]>;
/** Symbols and words per discontinuity type. */
export const DISCONTINUITIES: Record<Discontinuity['type'], [string, string]>;
/** ✓ ok, ⚠ warn (≥ 80 % of the tolerance), ✗ bad (outside, or no Z), none (no target). */
export function verdict(z: number | null, target: Target | null): 'ok' | 'warn' | 'bad' | 'none';

/** A group of nets (netGroup): tracks like a pick, or net-name patterns, with the members ticked. */
export interface NetGroupValue {
  source: 'similar' | 'pattern';
  kind: 'single' | 'differential';
  /** select similar: the layer, track width and pair gap (mm) matched. */
  layer: string | null;
  width: number | null;
  gap: number | null;
  /** pattern: the patterns. */
  patterns: string[];
  members: Array<{ nets: string[]; on: boolean; length?: number; note?: string }>;
  /** The ticked members' nets. */
  nets: string[];
  /** The ticked pairs. */
  pairs: Array<[string, string]>;
}
export interface NetGroup {
  readonly root: HTMLDivElement;
  readonly value: NetGroupValue | null;
  /** What ≋ starts from, or null (≋ disabled). */
  setSeed(seed: { layer: string; width?: number | null; nets: string[] } | null): void;
  similar(seed?: { layer: string; width?: number | null; nets: string[] }): NetGroupValue | null;
  patterns(texts: string | string[]): NetGroupValue | null;
  toggle(net: string, on?: boolean): void;
  clear(): void;
  on(name: 'change' | 'apply' | 'clear', fn: (v: NetGroupValue | null) => void): () => void;
  destroy(): void;
}
/** The group box: ≋ select similar, ⌕ net-name patterns, ticked chips, ✓ apply. */
export function netGroup(el: HTMLElement, options: { copper: Copper; board?: Pick<Board, 'nets'> | null; names?: string[] | null; placeholder?: string }): NetGroup;
/** A short name for a net or a pair (last path part, without the pair suffix). */
export function shortName(nets: string[]): string;

export type RouteTarget = number | { value: number; tolerance_pct?: number };
export interface ImpedancePanel {
  readonly root: HTMLDivElement;
  readonly analyzer: Analyzer;
  readonly selection: { nets: string[]; track: number | null; target?: { value: number; tolerance_pct?: number } };
  readonly result: ImpedanceAnalysis | null;
  readonly runs: number;
  /** The section in the cross-section. */
  readonly section: Section | null;
  /** The group box, or null with `group: false`. */
  readonly group: NetGroup | null;
  /** ≋ from the current pick. */
  selectSimilar(): NetGroupValue | null;
  /** `target`: Ω or { value, tolerance_pct } the route is held to instead of the net class's. */
  select(s: string | string[] | (Selection & { target?: RouteTarget | null })): Promise<void | null>;
  /** Hold the route to another target (re-runs when it changes; null: the net class's). */
  setTarget(target: RouteTarget | null): Promise<ImpedanceAnalysis | null | void>;
  setOverride(structure: '' | Section['structure']): Promise<void>;
  /** Show route position s (mm along the first net): stage marker, section, cross-section. */
  hover(s: number | null, section?: Section | null): void;
  /** Select what is under a board point, as a click there (e.g. from boardPointFromPick). */
  selectAt(x: number, y: number, options?: { alt?: boolean }): Promise<void | null>;
  /** The frequency of the loss shown, Hz. */
  readonly frequency: number | null;
  /** Show the loss at another frequency (snapped to the analysis's sweep; no re-run). */
  setFrequency(f: number): number;
  on(name: 'select' | 'result' | 'hover' | 'error' | 'focus' | 'frequency' | 'group', fn: (v: any) => void): () => void;
  destroy(): void;
}
export type Unit = string[];
export interface PathUnit { nets: string[]; kind: 'single' | 'differential'; key: 'Z0' | 'Zdiff'; s0: number; s1: number; length: number; reversed: boolean;
  start: Vec2; end: Vec2; target: { value: number; tolerance_pct?: number } | null; doc: ImpedanceAnalysis | null; index: number }
export interface PathMark { type: 'part' | 'end' | 'gap' | Discontinuity['type']; s: number; label: string; refs?: string[]; detail?: string; layer?: string; unit: number }
export interface RoutePathDoc {
  length: number;
  units: PathUnit[];
  sections: Array<{ s0: number; s1: number; z: number | null; unit: number; section: Section }>;
  marks: PathMark[];
  routes: RouteStep[][];
}
export interface PathPoint { s: number; unit: PathUnit; local: number; p: Vec2 | null; d: Vec2 | null; layer: string | null; width: number | null; section: Section | null; z: number | null }
export interface Bridge { a: number; b: number; refs: string[]; pads: Copper['pads'] }
/** Grounds, supplies and solid planes: nets a series part may also have. */
export function supplyNets(copper: Copper): Set<string>;
/** The parts between units (every net of the part in the selection or a supply). */
export function bridges(copper: Copper, units: Unit[]): Bridge[];
/** The units in path order, through the parts; via null: not connected to the one before. */
export function chainOrder(units: Unit[], links: Bridge[]): Array<{ unit: number; via: Bridge | null }>;
/** One path through the selection's units from their analyses (null while one runs). */
export function routePath(copper: Copper, units: Unit[], docs?: Array<ImpedanceAnalysis | null>, options?: { targets?: Array<{ value: number; tolerance_pct?: number } | null> }): RoutePathDoc;
/** A route's point and direction at s. */
export function routeAt(route: RouteStep[], s: number, tracks?: string[] | null): { p: Vec2; d: Vec2; layer: string; width: number; track: number } | null;
export function sectionAt(path: Pick<RoutePathDoc, 'sections'>, s: number): RoutePathDoc['sections'][number] | null;
/** What is at a path position: the section, its Z, the board point and the trace's direction. */
export function pathAt(path: RoutePathDoc, s: number): PathPoint | null;
/** A point on one of the selection's traces → its path position (a pair's second net: the first net's nearest point). */
export function pathFromBoard(path: RoutePathDoc, copper: Copper, net: string, p: Vec2, track?: number | null): { s: number; unit: PathUnit; local: number; p?: Vec2; d?: Vec2; width?: number } | null;
export function niceTicks(lo: number, hi: number, n?: number): number[];
export interface RouteProfile {
  readonly root: HTMLDivElement;
  readonly analyzer: Analyzer;
  readonly path: RoutePathDoc | null;
  readonly docs: Array<ImpedanceAnalysis | null>;
  readonly cursor: number | null;
  readonly state: 'empty' | 'busy' | 'ready';
  select(units: Unit[], options?: { targets?: Array<{ value: number; tolerance_pct?: number } | null>; options?: Array<Partial<RouteOptions>> }): Promise<RoutePathDoc | null>;
  setTargets(targets: Array<{ value: number; tolerance_pct?: number } | null>): void;
  /** The cursor at a path position (null: none), without 'hover'. */
  show(s: number | null): PathPoint | null;
  /** A point on a selected trace: the cursor at its place along the path. */
  showBoard(net: string, p: Vec2, track?: number | null): (ReturnType<typeof pathFromBoard> & { at: PathPoint }) | null;
  on(name: 'hover', fn: (h: { s: number; at: PathPoint } | null) => void): () => void;
  on(name: 'path', fn: (p: RoutePathDoc | null) => void): () => void;
  on(name: 'error', fn: (err: unknown) => void): () => void;
  destroy(): void;
}
/** Z along the whole route of a selection: one plot, hover / tap / ← → for the cursor. */
export function routeProfile(el: HTMLElement, options: { board: Board; copper: Copper; analyzer?: Analyzer | { worker?: boolean | string | URL }; options?: Partial<RouteOptions> }): RouteProfile;

/** The click-a-trace impedance panel (see docs/impedance.md "UI"). */
export function impedancePanel(el: HTMLElement, options: {
  board: Board;
  copper: Copper;
  stage?: Stage | null;
  analyzer?: Analyzer | { worker?: boolean | string | URL };
  options?: RouteOptions;
  pick?: boolean;
  layers?: string[] | null | (() => string[] | null);
  injectCss?: boolean;
  /** Hz: the loss shown and analyzeNet's frequency (default 5.6e9); null: no loss. */
  frequency?: number | null;
  /** false: no group box (≋ select similar, net-name patterns). */
  group?: boolean;
}): ImpedancePanel;
/** 5.6e9 → '5.6 GHz'. */
export function fmtHz(f: number): string;

/** A stop of the layer view's stepper: a copper layer, or the dielectric between two (stackup layers merged). */
export type LayerStop =
  | { kind: 'copper'; layer: string; name: string; thickness: number | null }
  | { kind: 'dielectric'; name: string; thickness: number | null; epsilon_r: number | null; material?: string | null; dielectric?: 'prepreg' | 'core' | null; parts: unknown[] };
/** Top to bottom: every copper layer of the copper, and the stackup's dielectrics between them. */
export function layerStops(copper: Copper, stackup?: Board['stackup'] | null): LayerStop[];
/** The section most of a net's route is (layer, structure, width / gap), from an analysis. */
export function mainSection(doc: ImpedanceAnalysis | null): Section | null;

/** A net or pair's reference plane as the route analysis found it (or the stackup's nearest copper). */
export interface Reference { side: 'top' | 'bottom'; layer: string; net: string; h: number; skipped?: string[] }
/** Where a route's reference breaks: no plane, a split it crosses, a change (at a via or along a layer). */
export interface ReferenceBreak { type: 'no_ref' | 'plane_gap' | 'via' | 'ref_change'; at: [number, number]; layer: string; plane?: string | null; text: string; count: number }
/** One structure to control: a net or a pair, as the layer view's form says it. */
export interface ControlItem {
  nets: string[];
  kind: 'single' | 'differential';
  layer: string;
  /** The stackup's structure names; null: microstrip / stripline by the layer. */
  structure: 'microstrip' | 'stripline' | 'coplanar' | 'coplanar_grounded' | null;
  target: number;
  tolerance_pct: number;
  width: number | null;
  gap: number | null;
  coplanar_gap: number | null;
  /** A point on each net's copper, for an app that keeps nets by place across revisions. */
  anchors: Array<{ net: string; layer: string; x: number; y: number }>;
  /** The reference planes the figures use; `chosen`: picked by the user, not found. */
  reference: { top: { layer: string; net: string; h: number } | null; bottom: { layer: string; net: string; h: number } | null; chosen: boolean };
  /** Tier 1 on the board's stackup: Z at the routed width, and the width for the target. */
  tier1?: { z: number; model: string; width_for_target: number };
}
/** What the layer view's form says for the whole selection: one item per net or pair (a single item's fields also at the top). */
export type ControlDraft = Partial<ControlItem> & {
  nets: string[];
  pairs: string[][];
  structure: ControlItem['structure'];
  target: number;
  target_differential: number;
  tolerance_pct: number;
  items: ControlItem[];
};
export interface LayerView {
  readonly root: HTMLDivElement;
  readonly stage: Stage;
  readonly stops: LayerStop[];
  readonly index: number;
  readonly stop: LayerStop & { index: number };
  readonly selection: { nets: string[]; pairs: string[][]; layers: string[] } | null;
  readonly draft: ControlDraft | null;
  /** Z along the route of the whole selection, under the board (with `route`). */
  readonly profile: RouteProfile | null;
  /** What an app keeps across reloads (pass back as options). */
  readonly state: { region: Region | null; layer: string | null };
  /** A stop by index, or a copper layer by id. */
  show(stop: number | string): void;
  step(by: number): void;
  /** Select exactly these nets (two: a pair unless pair: false). */
  pick(nets: string[], options?: { pair?: boolean }): void;
  add(net: string): void;
  remove(net: string): void;
  toggle(net: string): void;
  removeLast(): void;
  /** The net under a board point (mm) on the layer on show, without toggling it. */
  netAt(x: number, y: number): string | null;
  /** Toggle what is under a board point (mm) on the layer on show, as a click there. */
  pickAt(x: number, y: number): CopperHit | null;
  pair(a: string, b: string): void;
  unpair(net: string): void;
  reference(nets: string[]): { refs: Reference[]; coplanar_gap: number | null; breaks: ReferenceBreak[]; from: 'route' | 'stackup'; state: string | null; section?: Section | null } | null;
  /** A reference layer for a selected net or pair ('' or null: as found). */
  setReference(nets: string[], layer: string | null): void;
  tint(nets: string[] | null): void;
  /** The explicit camera moves: the selection, the board. */
  zoomToSelection(): void;
  fit(): void;
  clear(): void;
  setTarget(v: { target?: number; differential?: number; tolerance?: number }): void;
  on(name: 'layer' | 'segment' | 'net' | 'draft' | 'control' | 'rename' | 'error', fn: (v: any) => void): () => void;
  destroy(): void;
}
/**
 * The board one copper layer at a time (the layers above ghosted), a stepper through the stackup (↑/↓); a click on
 * a trace toggles its net in the selection (a drag or a long press pans), and a form controls the selection.
 * Selecting never moves the view.
 */
export function layerView(el: HTMLElement, options: {
  board: Board;
  copper: Copper;
  /** createAnalyzer options: each net's reference planes along its route, the last one's Z under the form; false (default): none. */
  route?: false | { worker?: boolean | string | URL };
  /** Nets named here (auto) can be renamed; pads per net. */
  netInfo?: Record<string, { auto?: boolean; pads?: number }>;
  rename?: ((net: string, name: string) => Promise<unknown>) | null;
  /** The app's tier-2 figure for one geometry. */
  evaluate?: ((item: ControlItem) => Promise<{ z: number | null; width?: number | null; gap?: number | null; why?: string; note?: string } | null>) | null;
  editable?: boolean;
  defaults?: { target?: { single?: number; differential?: number }; tolerance?: number; pair?: boolean };
  /** The view and stop to start on (LayerView.state). */
  region?: Region | null;
  layer?: string | null;
  injectCss?: boolean;
}): LayerView;

/** A net for a net box. */
export interface NetEntry { name: string; pads?: number; layers?: string[]; auto?: boolean; hint?: string }
/** The nets of a copper@1 document for a net box (pads, layers; auto from netInfo). */
export function netEntries(copper: Copper, netInfo?: Record<string, { auto?: boolean; pads?: number }>): NetEntry[];
/** The layer and width a net is mostly routed at (by track length). */
export function routedOn(copper: Copper, net: string): { layer: string; width: number } | null;
/** The reference planes the stackup alone gives a trace on `layer` (nearest copper, or `pick` on its side). */
export function stackupRefs(stackup: Board['stackup'] | null, layer: string, pick?: string | null): Reference[];
/** 'over In1.Cu (GND) · 0.21 mm', both planes of a stripline, the coplanar ground's gap. */
export function referenceText(refs: Reference[], coplanarGap?: number | null): string;
/** Where an analysis's reference breaks along the route. */
export function referenceBreaks(doc: ImpedanceAnalysis): ReferenceBreak[];
/** How well a net name matches a lower-case query: 0 best, null none. */
export function netScore(name: string, query: string): number | null;
/** Nets matching a query, best first (`first`: names to put on top when they match). */
export function fuzzyNets<T extends string | { name: string }>(entries: T[], query: string, options?: { limit?: number; first?: string[] }): T[];
/** A type-ahead net box: ↑ ↓ Enter (adds, stays open), Esc. */
export function netPicker(el: HTMLElement, options: {
  source: Array<string | NetEntry> | ((query: string) => Promise<Array<string | NetEntry>>);
  onPick: (net: string) => void;
  first?: () => string[];
  exclude?: () => string[];
  placeholder?: string;
  label?: string;
  keepOpen?: boolean;
}): { root: HTMLDivElement; input: HTMLInputElement; focus(): void; set(text: string): void; refresh(): Promise<void>; destroy(): void };

/** The components' CSS (variables --bdi-*), injected by impedancePanel unless injectCss: false. */
export const IMPEDANCE_UI_CSS: string;
