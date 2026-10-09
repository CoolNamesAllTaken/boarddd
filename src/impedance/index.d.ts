// boarddd/impedance: tier-1 closed-form PCB transmission-line impedance and the tier-2 field solver; see
// docs/impedance.md.
// Lengths in any one unit (boarddd uses mm); impedances in Ω.

/** An input outside the range the formula was published or checked for. */
export interface ImpedanceFlag {
  /** The checked quantity, e.g. 'w/h', 't/b', 'er'. */
  code: string;
  value: number;
  min: number | null;
  max: number | null;
  message: string;
}

export interface LineResult {
  model: string;
  method: string;
  Z0: number;
  eps_eff: number;
  /** Empty when every input is inside the model's validity range. */
  flags: ImpedanceFlag[];
}

export interface CoupledResult {
  model: string;
  method: string;
  /** 2 Zodd. */
  Zdiff: number;
  /** Zeven / 2. */
  Zcommon: number;
  Zodd: number;
  Zeven: number;
  eps_eff_odd: number;
  eps_eff_even: number;
  flags: ImpedanceFlag[];
}

export interface ComparisonResult {
  model: string;
  method: string;
  comparison: true;
  Z0?: number;
  Zdiff?: number;
  flags: ImpedanceFlag[];
}

/** Trace over one plane: width, dielectric height, copper thickness (default 0), εr. */
export interface MicrostripParams { w: number; h: number; t?: number; er: number }
/** Microstrip under a conformal mask of thickness c and εr erc. */
export interface CoatedMicrostripParams extends MicrostripParams { c: number; erc: number }
/** Trace between planes: h1 dielectric to one plane, h2 (default h1) to the other. */
export interface StriplineParams { w: number; h1: number; h2?: number; t?: number; er: number }
/** Coplanar: signal width w, gap to each coplanar ground, substrate height h; cpwg only: optional solder mask c, erc. */
export interface CoplanarParams { w: number; gap: number; h: number; t?: number; er: number; c?: number; erc?: number }
/** Optional solder mask c (over laminate and copper), erc (boarddd mask model). */
export interface CoupledMicrostripParams extends MicrostripParams { s: number; c?: number; erc?: number }
export interface CoupledStriplineParams extends StriplineParams { s: number }

/** Impedance of free space, Ω. */
export const ETA0: number;
/** Complete elliptic integral of the first kind, modulus k. */
export function ellipticK(k: number): number;
/** K(k)/K(k'); pass kp when k is close to 1. */
export function ellipticRatio(k: number, kp?: number): number;

export function microstrip(p: MicrostripParams): LineResult;
export function coatedMicrostrip(p: CoatedMicrostripParams): LineResult;
export function stripline(p: StriplineParams): LineResult;
export function cpw(p: CoplanarParams): LineResult;
export function cpwg(p: CoplanarParams): LineResult;
export function coupledMicrostrip(p: CoupledMicrostripParams): CoupledResult;
export function coupledStripline(p: CoupledStriplineParams): CoupledResult;

export function ipc2141Microstrip(p: MicrostripParams): ComparisonResult;
export function ipc2141Stripline(p: StriplineParams): ComparisonResult;
export function ipc2141CoupledMicrostrip(p: CoupledMicrostripParams): ComparisonResult;
export function ipc2141CoupledStripline(p: CoupledStriplineParams): ComparisonResult;

export type ModelId =
  | 'microstrip' | 'coated_microstrip' | 'stripline' | 'cpw' | 'cpwg' | 'coupled_microstrip' | 'coupled_stripline'
  | 'ipc2141_microstrip' | 'ipc2141_stripline' | 'ipc2141_coupled_microstrip' | 'ipc2141_coupled_stripline';

export const MODELS: Record<ModelId, (p: any) => LineResult | CoupledResult | ComparisonResult>;
export function calculate(model: ModelId, params: Record<string, number>): LineResult | CoupledResult | ComparisonResult;

export interface SynthesisOptions {
  /** The dimension to solve for (default 'w'). */
  vary?: string;
  /** The result field to match (default Zdiff for coupled models, else Z0). */
  key?: 'Z0' | 'Zdiff' | 'Zcommon' | 'Zodd' | 'Zeven';
  /** Search bracket (default 1e-3..1e2 times the dielectric height). */
  lo?: number;
  hi?: number;
  /** Relative tolerance on the dimension (default 1e-9). */
  tol?: number;
}

/** Solve for a dimension giving `target` Ω by bisection; throws RangeError when it is out of reach. */
export function synthesize(
  model: ModelId,
  params: Record<string, number>,
  target: number,
  opts?: SynthesisOptions,
): { value: number; params: Record<string, number>; result: LineResult | CoupledResult | ComparisonResult; iterations: number };

// ── from the board model (stackup.js) ──────────────────────────────────────────────────────────────────────
import type { ImpedanceTarget, Stackup, StackupLayer } from '../model/board.js';

export type Structure = NonNullable<ImpedanceTarget['structure']>;

/** What a stackup leaves out is taken from here (KiCad's defaults), with a warning. */
export const STACKUP_DEFAULTS: { copper_thickness: number; epsilon_r: number; mask_thickness: number; mask_epsilon_r: number;
  loss_tangent: number; mask_loss_tangent: number };

/** The roughness of a copper StackupLayer for loss.js (mm), or null when it gives none. */
export function roughnessOf(layer: StackupLayer): Roughness | null;

/** The closed-form model for an ImpedanceTarget structure and kind; null when tier 1 has none (differential coplanar). */
export function modelFor(structure: Structure, kind?: 'single' | 'differential', opts?: { coated?: boolean }): ModelId | null;

export interface LineFromStackupOptions {
  /** Track width, mm. */
  width: number;
  kind?: 'single' | 'differential';
  /** Differential pair gap, mm. */
  gap?: number;
  /** Default: microstrip on an outer layer, stripline on an inner one. */
  structure?: Structure;
  /** Gap to the coplanar ground, mm (coplanar structures). */
  coplanarGap?: number;
  /** Reference planes (ImpedanceLayer ref_top / ref_bottom); default the nearest copper. */
  refTop?: string;
  refBottom?: string;
  /** Model the solder mask on an outer single-ended microstrip (default true). */
  mask?: boolean;
  /** 'field' also returns the real cross-section for the tier-2 field solver (default 'closedform'). */
  solver?: 'closedform' | 'field';
  /** Field solver: a trapezoid trace whose top is this much narrower than `width`, mm (default 0). */
  etch?: number;
  /** Also `loss`: options for lineLoss / sectionLoss from the stackup's Er/Df, mask, conductivity, roughness. */
  loss?: boolean;
}

/** lineFromStackup's `loss`: one options object for both lineLoss (tier 1) and sectionLoss (tier 2). */
export type StackupLossOptions = LineLossOptions & SectionLossOptions;

/**
 * A signal layer as a line for the field solver: the closed-form model and parameters where tier 1 has one (model
 * null for differential coplanar and coplanar on inner layers), plus the real cross-section.
 */
export function lineFromStackup(
  stackup: Pick<Stackup, 'layers'>,
  layer: string,
  opts: LineFromStackupOptions & { solver: 'field' },
): { model: ModelId | null; structure: Structure; params: Record<string, number>; warnings: string[]; section: CrossSection; loss?: StackupLossOptions };
/** A signal layer of a boarddd/board@1 stackup as a closed-form line. */
export function lineFromStackup(
  stackup: Pick<Stackup, 'layers'>,
  layer: string,
  opts: LineFromStackupOptions,
): { model: ModelId; structure: Structure; params: Record<string, number>; warnings: string[]; loss?: StackupLossOptions };

export interface TargetEvaluation {
  layer: string;
  model: ModelId;
  key: 'Z0' | 'Zdiff';
  value: number;
  target: number;
  deviation_pct: number;
  /** Within tolerance_pct; null when the target has no tolerance. */
  ok: boolean | null;
  /** The layer's width, or the synthesized one when the target gives none. */
  width: number;
  synthesized: boolean;
  result: LineResult | CoupledResult;
  warnings: string[];
}

/** A TargetEvaluation by the field solver (`solver: 'field'`). */
export interface FieldTargetEvaluation extends Omit<TargetEvaluation, 'model' | 'result'> {
  model: ModelId | null;
  result: FieldResult & { model: ModelId | null };
}

/** Evaluate a net class's ImpedanceTarget with the tier-2 field solver (synthesis by secant steps from tier 1). */
export function evaluateTarget(
  stackup: Pick<Stackup, 'layers'>,
  target: ImpedanceTarget,
  opts: Omit<LineFromStackupOptions, 'width' | 'kind'> & { solver: 'field'; fieldOptions?: FieldOptions },
): FieldTargetEvaluation[];
/** Evaluate a net class's ImpedanceTarget on a stackup, one row per target layer. */
export function evaluateTarget(
  stackup: Pick<Stackup, 'layers'>,
  target: ImpedanceTarget,
  opts?: Omit<LineFromStackupOptions, 'width' | 'kind'>,
): TargetEvaluation[];

// ── tier 2: the field solver (fieldsolver.js) ──────────────────────────────────────────────────────────────

/** A rectangle in the cross-section plane (mm, y up). A missing x0 or x1 extends it to the domain edge. */
export interface SectionRect { x0?: number; x1?: number; y0: number; y1: number }
export interface SectionConductor extends SectionRect {
  /** Ground nets (`CrossSection.ground`, default 'gnd') are the reference; every other net is a signal. */
  net: string;
  /** Loss: its copper (layer), for conductivity and roughness (default 'signal' / 'ground'). */
  metal?: string;
}
export interface SectionDielectric extends SectionRect {
  er: number;
  /** Loss: tan δ at er's frequency (default 0) and a material name to give it a frequency model in sectionLoss. */
  tand?: number;
  material?: string;
}

/** A transmission-line cross-section: copper rectangles by net and dielectric rectangles (later ones win). */
export interface CrossSection {
  conductors: SectionConductor[];
  dielectrics?: SectionDielectric[];
  /** Nets held at 0 V (default ['gnd']). */
  ground?: string[];
  /** εr outside every dielectric (default 1, air). */
  background?: number;
}

export interface FieldOptions {
  /** Target relative error as estimated by the last grid refinement (default 0.01). */
  tol?: number;
  /** First grid level (default 0); each level divides the cell sizes by √2. */
  level?: number;
  /** Last grid level (default 4). */
  maxLevel?: number;
  /** Solve half the domain when the section is its own mirror image (default true). */
  symmetry?: boolean;
  /** Also `loss`, the partials sectionLoss needs (lengths in mm). */
  loss?: boolean;
}

/** solveCrossSection's `loss` (finest grid): what sectionLoss turns into RLGC at any frequency. */
export interface FieldLossPartials {
  /** Vacuum Maxwell matrix, F/m. */
  K0: number[][];
  /** The dielectric materials; `parts[i]` is K's share in materials[i], the last part the background's. */
  materials: { er: number; tand: number; material: string | null }[];
  parts: number[][][];
  /** Metal groups; `dK0[i]` is dK0/dn (per mm of recession of every face of metals[i]). */
  metals: string[];
  dK0: number[][][];
  /** Signal copper cross-section area (mm², all signals) and the smallest side of a signal's bounding box (mm). */
  area: number;
  t: number;
  signalMetal: string;
}

export interface FieldGrid { nx: number; ny: number; nodes: number; unknowns: number; hc: number; growth: number }

export interface FieldResult {
  solver: 'field';
  method: string;
  /** Signal nets in order of first appearance (the rows of the matrices). */
  signals: string[];
  /** One signal. */
  Z0?: number;
  eps_eff?: number;
  /** F/m, H/m and F/m (vacuum). */
  C?: number;
  L?: number;
  C0?: number;
  /** Two signals: differential and common mode (odd and even for a symmetric pair). */
  Zdiff?: number;
  Zcommon?: number;
  Zodd?: number;
  Zeven?: number;
  eps_eff_odd?: number;
  eps_eff_even?: number;
  /** Maxwell capacitance matrices C (with dielectrics) and C0 (vacuum) in F/m, inductance L in H/m (finest grid). */
  matrices: { C: number[][]; C0: number[][]; L: number[][] };
  /** Relative error estimate per key (the size of the extrapolation from the finest grid). */
  error: Record<string, number>;
  /** The largest error estimate, %. */
  error_pct: number;
  symmetry: 'same' | 'swap' | 'none';
  levels: ({ level: number; grid: FieldGrid } & Partial<Record<'Z0' | 'eps_eff' | 'C' | 'L' | 'C0' | 'Zdiff' | 'Zcommon' | 'Zodd' | 'Zeven' | 'eps_eff_odd' | 'eps_eff_even', number>>)[];
  /** Wall time, ms. */
  ms: number;
  /** With `loss: true`. */
  loss?: FieldLossPartials;
}

/** Solve a cross-section with the 2D quasi-static field solver. Throws RangeError on invalid sections. */
export function solveCrossSection(section: CrossSection, opts?: FieldOptions): FieldResult;

export type FieldModelId = Exclude<ModelId, `ipc2141_${string}`> | 'coupled_cpw' | 'coupled_cpwg';

/**
 * The cross-section of a tier-1 model's parameters. Every model also takes `etch` (trapezoid: top narrower than
 * w by etch); outer structures a conformal solder mask `c` (over laminate), `ct` (over copper), `cs` (in a pair's
 * gap), `erc`; coplanar ones `fence` (via-fence distance from the gap, cpwg) and `gnd` (coplanar ground width).
 */
export function sectionFor(model: FieldModelId, params: Record<string, number>): CrossSection;

/** A tier-1 model (or a coplanar pair) solved by the field solver. */
export function fieldCalculate(
  model: FieldModelId,
  params: Record<string, number>,
  opts?: FieldOptions,
): FieldResult & { model: FieldModelId; flags: [] };

// ── along a route on a real board (route.js) ───────────────────────────────────────────────────────────────
import type { Board } from '../model/board.js';
import type { Copper, Track } from '../copper/copper.js';
import type { ImpedanceAnalysis } from './result.js';
export * from './result.js';

/** boarddd/impedance@1 (schema/impedance.schema.json), as an object. */
export const IMPEDANCE_SCHEMA: Record<string, unknown>;
export const IMPEDANCE_SCHEMA_ID: 'boarddd/impedance@1';
/** 'json/pointer: message' errors; empty when `doc` is a valid boarddd/impedance@1 document. */
export function validateImpedance(doc: unknown): string[];

/** A user override of a section's environment (per net or per track id). */
export interface RouteOverride {
  structure?: ImpedanceAnalysis['sections'][number]['structure'];
  /** A copper layer to reference above / below, or false for none. */
  refTop?: string | false;
  refBottom?: string | false;
  /** false: ignore coplanar grounds. */
  coplanar?: false;
}

export interface RouteOptions {
  /** Station spacing along the route, mm (default 0.25). */
  step?: number;
  /** 'field' (tier 2, default) or 'closedform' (tier 1 where it has a model, else tier 2). */
  solver?: 'field' | 'closedform';
  /** Tolerance when the target gives none, percent (default 10). */
  tolerancePct?: number;
  /** Half-width of the cut line, mm (default max(1, w/2 + 8 h)). */
  window?: number | null;
  /** A ground this close to the trace edge makes it coplanar, mm (default max(3 w, 5 h)). */
  coplanarWindow?: number | null;
  /** A pair partner this close (edge to edge) and parallel couples, mm (default max(0.5, 4 w)). */
  pairWindow?: number | null;
  /** Largest angle between a pair's tracks that still counts as parallel, degrees (default 20). */
  parallelDeg?: number;
  /** A reference plane must reach this many h beyond the trace edges (default 3). */
  refMargin?: number;
  /** 'ground': other nets' copper within the coplanar window is solved as grounded (default 'ignore'). */
  neighbours?: 'ignore' | 'ground';
  /** No reference plane: no Z ('skip', default) or a CPW between coplanar grounds ('solve'). */
  noPlane?: 'skip' | 'solve';
  /** Field solver options (default { level: -2, maxLevel: -1, tol: 0.05 }). */
  fieldOptions?: FieldOptions;
  /** The target, Ω (or with a tolerance); default the first net's class target. */
  target?: number | { value: number; tolerance_pct?: number };
  /** Nets to treat as ground besides zones, solid planes and GND-like names. */
  groundNets?: string[];
  overrides?: { net?: RouteOverride; tracks?: Record<string, RouteOverride> };
  /** Solved cross-sections by geometry key, shared between calls (e.g. every net of a board). */
  cache?: Map<string, unknown>;
  /** Called while it runs: stations cut along the route (done/total mm), then sections solved. */
  onProgress?: (p: { phase: 'route' | 'solve'; done: number; total: number }) => void;
  /** Hz: also each section's loss and the route's (null: none, the default). */
  frequency?: number | null;
  /** Hz: the loss and Z sweep (default LOSS_SWEEP plus `frequency`). */
  frequencies?: number[] | null;
  /** loss.js options over the stackup's (e.g. { conductor: { roughness: { rq: 0.001 } } }). */
  lossOptions?: StackupLossOptions | null;
}

export const ROUTE_DEFAULTS: Readonly<Required<Omit<RouteOptions, 'target' | 'groundNets' | 'overrides' | 'cache' | 'onProgress'>> & Pick<RouteOptions, 'onProgress'>>;
/** analyzeNet's default loss sweep, Hz (100 MHz to 40 GHz). */
export const LOSS_SWEEP: readonly number[];

/** One track of a route, oriented the way it is walked. */
export interface RouteStep {
  track: Track;
  index: number;
  forward: boolean;
  s0: number;
  s1: number;
  run: number;
  path: { len: number; at(s: number): { p: [number, number]; d: [number, number] } };
}

/** A net's tracks in route order (depth first from its lowest end). */
export function netRoute(copper: Copper, net: string): RouteStep[];

/** Impedance along a net's (or a pair's) route on a real board: a boarddd/impedance@1 document. */
export function analyzeNet(board: Board, copper: Copper, net: string | [string, string], options?: RouteOptions): ImpedanceAnalysis;

// ── loss and frequency (loss.js) ───────────────────────────────────────────────────────────────────────────

/** Annealed copper conductivity (S/m), the Er/Df reference frequency when a source gives none, Djordjevic-Sarkar band (Hz). */
export const LOSS_DEFAULTS: Readonly<{ conductivity: number; frequency: number; f_low: number; f_high: number }>;

/** A dielectric's Dk/Df at `frequency` (Hz) and how they vary (default 'djordjevic_sarkar'). */
export interface Dielectric {
  er: number;
  tand?: number;
  frequency?: number;
  model?: 'constant' | 'djordjevic_sarkar';
  f_low?: number;
  f_high?: number;
}
/** Copper roughness (lengths in mm): Hammerstad (rq), Huray (radius, ratio = N 4πa²/A_flat, matte), cannonball (rz). */
export interface Roughness {
  model?: 'none' | 'hammerstad' | 'huray' | 'cannonball';
  rq?: number;
  rz?: number;
  radius?: number;
  ratio?: number;
  count?: number;
  area?: number;
  matte?: number;
}
export interface Metal { conductivity?: number; roughness?: Roughness | null }

/** Dk and Df at f (Djordjevic-Sarkar from the values at the reference frequency, or constant). */
export function dielectricAt(f: number, m: Dielectric): { er: number; tand: number };
/** Skin depth, m. */
export function skinDepth(f: number, sigma?: number): number;
/** Surface resistance, Ω/sq. */
export function surfaceResistance(f: number, sigma?: number): number;
/** The cannonball stack's Huray parameters for a datasheet Rz (mm). */
export function cannonball(rz: number): { radius: number; count: number; area: number; ratio: number; matte: number };
/** Roughness factor K(f) >= 1 on the surface resistance. */
export function roughnessFactor(f: number, r: Roughness | null, sigma?: number): number;
/** Kirschning-Jansen microstrip dispersion from the static eps_eff, Z0. */
export function microstripDispersion(p: { w: number; h: number; er: number; eps_eff: number; Z0: number }, f: number): { eps_eff: number; Z0: number };
/** Kirschning-Jansen coupled microstrip dispersion (even and odd modes). */
export function coupledMicrostripDispersion(
  p: { w: number; s: number; h: number; er: number; Zeven: number; Zodd: number; eps_eff_even: number; eps_eff_odd: number; single: { Z0: number; eps_eff: number } },
  f: number,
): { eps_eff_even: number; eps_eff_odd: number; Zeven: number; Zodd: number };

/** One mode over frequency (arrays per frequency): R Ω/m, L H/m, G S/m, C F/m, α Np/m, β rad/m, Zc = Zc + j Zc_im. */
export interface LossMode {
  R: number[]; L: number[]; G: number[]; C: number[];
  alpha: number[]; alpha_c: number[]; alpha_d: number[]; beta: number[]; eps_eff: number[];
  Zc: number[]; Zc_im: number[];
  db_per_mm: number[]; db_per_inch: number[];
}
export interface SingleLoss extends LossMode {
  model: string | null; method: string; solver: 'closedform' | 'field'; key: 'Z0'; frequency: number[];
  /** Re Zc. */
  Z0: number[];
}
export interface PairLoss {
  model: string | null; method: string; solver: 'closedform' | 'field'; key: 'Zdiff'; frequency: number[];
  Zdiff: number[]; Zcommon: number[];
  /** Differential (odd mode), and common (even mode). */
  db_per_mm: number[]; db_per_inch: number[]; db_per_mm_common: number[];
  /** Per line. */
  odd: LossMode; even: LossMode;
}
export type LossResult = SingleLoss | PairLoss;

export interface LineLossOptions {
  dielectric?: Omit<Dielectric, 'er'>;
  mask?: Omit<Dielectric, 'er'>;
  conductor?: Metal;
  signal?: Metal;
  ground?: Metal;
  /** Kirschning-Jansen for (coupled) microstrip (default true). */
  dispersion?: boolean;
}
export interface SectionLossOptions {
  dielectric?: Omit<Dielectric, 'er' | 'tand'>;
  materials?: Record<string, Omit<Dielectric, 'er' | 'tand'>>;
  conductor?: Metal;
  metals?: Record<string, Metal>;
}

/** Loss and frequency dependence of a tier-1 line (params in mm; er, erc at the dielectric's frequency; t > 0). */
export function lineLoss(model: ModelId, params: Record<string, number>, frequencies: number | number[], opts?: LineLossOptions): LossResult;
/** Loss and frequency dependence from solveCrossSection(section, { loss: true }). */
export function sectionLoss(result: FieldResult, frequencies: number | number[], opts?: SectionLossOptions): LossResult;
/** S-parameters of `length` mm of line: per frequency a 2x2 or (pair: ports 1→2 line A, 3→4 line B) 4x4 matrix of [re, im]. */
export function sParameters(loss: LossResult, length: number, opts?: { z0?: number }): [number, number][][][];
/** A Touchstone 1.1 .s2p (single) or .s4p (pair) file of `length` mm of line. */
export function touchstone(loss: LossResult, length: number, opts?: { z0?: number; comment?: string }): string;
