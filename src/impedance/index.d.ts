// boarddd/impedance: tier-1 closed-form PCB transmission-line impedance; see docs/impedance.md.
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
