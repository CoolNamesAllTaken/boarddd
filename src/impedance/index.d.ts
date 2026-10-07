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
/** Coplanar: signal width w, gap to each coplanar ground, substrate height h. */
export interface CoplanarParams { w: number; gap: number; h: number; t?: number; er: number }
export interface CoupledMicrostripParams extends MicrostripParams { s: number }
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
import type { ImpedanceTarget, Stackup } from '../model/board.js';

export type Structure = NonNullable<ImpedanceTarget['structure']>;

/** What a stackup leaves out is taken from here (KiCad's defaults), with a warning. */
export const STACKUP_DEFAULTS: { copper_thickness: number; epsilon_r: number; mask_thickness: number; mask_epsilon_r: number };

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
}

/**
 * A signal layer as a line for the field solver: the closed-form model and parameters where tier 1 has one (model
 * null for differential coplanar and coplanar on inner layers), plus the real cross-section.
 */
export function lineFromStackup(
  stackup: Pick<Stackup, 'layers'>,
  layer: string,
  opts: LineFromStackupOptions & { solver: 'field' },
): { model: ModelId | null; structure: Structure; params: Record<string, number>; warnings: string[]; section: CrossSection };
/** A signal layer of a boarddd/board@1 stackup as a closed-form line. */
export function lineFromStackup(
  stackup: Pick<Stackup, 'layers'>,
  layer: string,
  opts: LineFromStackupOptions,
): { model: ModelId; structure: Structure; params: Record<string, number>; warnings: string[] };

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
}
export interface SectionDielectric extends SectionRect { er: number }

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
}

/** Solve a cross-section with the 2D quasi-static field solver. Throws RangeError on invalid sections. */
export function solveCrossSection(section: CrossSection, opts?: FieldOptions): FieldResult;

export type FieldModelId = Exclude<ModelId, `ipc2141_${string}`> | 'coupled_cpw' | 'coupled_cpwg';

/**
 * The cross-section of a tier-1 model's parameters. Outer structures also take a conformal solder mask `c`,
 * `erc`; coplanar ones `fence` (via-fence distance from the gap, cpwg) and `gnd` (coplanar ground width).
 */
export function sectionFor(model: FieldModelId, params: Record<string, number>): CrossSection;

/** A tier-1 model (or a coplanar pair) solved by the field solver. */
export function fieldCalculate(
  model: FieldModelId,
  params: Record<string, number>,
  opts?: FieldOptions,
): FieldResult & { model: FieldModelId; flags: [] };
