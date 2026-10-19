// boarddd/impedance/ui: click-a-trace impedance components for a boarddd/view2d stage; see docs/impedance.md "UI".
import type { Board } from '../../model/board.js';
import type { Copper, Track } from '../../copper/copper.js';
import type { HitIndex, Stage, DrawContent } from '../../view2d/index.js';
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
  readonly selection: { nets: string[]; track: number | null };
  set(sel: { nets?: string[]; track?: number | null }): void;
  section(s: Section | null): void;
  marker(p: Vec2 | null): void;
  rings(points: Vec2[] | null): void;
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

export interface ImpedancePanel {
  readonly root: HTMLDivElement;
  readonly analyzer: Analyzer;
  readonly selection: { nets: string[]; track: number | null };
  readonly result: ImpedanceAnalysis | null;
  readonly runs: number;
  /** The section in the cross-section. */
  readonly section: Section | null;
  /** The group box, or null with `group: false`. */
  readonly group: NetGroup | null;
  /** ≋ from the current pick. */
  selectSimilar(): NetGroupValue | null;
  select(s: string | string[] | Selection): Promise<void | null>;
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

/** What the layer view's control form says: a structure to control. */
export interface ControlDraft {
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
  /** Tier 1 on the board's stackup: Z at the routed width, and the width for the target. */
  tier1?: { z: number; model: string; width_for_target: number };
}
export interface LayerView {
  readonly root: HTMLDivElement;
  readonly stage: Stage;
  readonly stops: LayerStop[];
  readonly index: number;
  readonly stop: LayerStop & { index: number };
  readonly selection: { nets: string[]; layers: string[] } | null;
  readonly draft: ControlDraft | null;
  readonly panel: ImpedancePanel | null;
  /** A stop by index, or a copper layer by id. */
  show(stop: number | string): void;
  step(by: number): void;
  /** Pick nets as a right click would. */
  pick(nets: string[], options?: { guessPair?: boolean }): void;
  /** Pick what is under a board point (mm) on the layer on show. */
  pickAt(x: number, y: number): CopperHit | null;
  clear(): void;
  setTarget(v: { target?: number; tolerance?: number }): void;
  on(name: 'layer' | 'segment' | 'net' | 'draft' | 'control' | 'rename' | 'error', fn: (v: any) => void): () => void;
  destroy(): void;
}
/**
 * The board one copper layer at a time (the layers above ghosted), a stepper through the stackup (↑/↓), click a
 * trace for its segment, right click / long-press for its net on every layer and a form to control it.
 */
export function layerView(el: HTMLElement, options: {
  board: Board;
  copper: Copper;
  /** createAnalyzer options: the net's Z along its route under the form; false (default): none. */
  route?: false | { worker?: boolean | string | URL };
  /** Nets named here (auto) can be renamed; pads per net. */
  netInfo?: Record<string, { auto?: boolean; pads?: number }>;
  rename?: ((net: string, name: string) => Promise<unknown>) | null;
  /** The app's tier-2 figure for a draft. */
  evaluate?: ((draft: ControlDraft) => Promise<{ z: number | null; width?: number | null; gap?: number | null; why?: string; note?: string } | null>) | null;
  editable?: boolean;
  defaults?: { target?: { single?: number; differential?: number }; tolerance?: number; pair?: boolean };
  injectCss?: boolean;
}): LayerView;

/** The components' CSS (variables --bdi-*), injected by impedancePanel unless injectCss: false. */
export const IMPEDANCE_UI_CSS: string;
