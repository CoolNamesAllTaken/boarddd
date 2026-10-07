// Compile-only check of the boarddd/impedance typings.
import { calculate, coupledStripline, microstrip, MODELS, synthesize } from '../../src/impedance/index.js';
import type { CoupledResult, ImpedanceFlag, LineResult } from '../../src/impedance/index.js';

const ms: LineResult = microstrip({ w: 0.36, h: 0.2104, t: 0.035, er: 4.4 });
const flags: ImpedanceFlag[] = ms.flags;
const pair: CoupledResult = coupledStripline({ w: 0.1, s: 0.15, h1: 0.2, h2: 0.3, er: 4.1 });
const zdiff: number = pair.Zdiff;
const w: number = synthesize('microstrip', { h: 0.2, er: 4.4 }, 50).value;
const any = calculate('cpwg', { w: 0.3, gap: 0.15, h: 0.3, er: 4.5 });
const ids: string[] = Object.keys(MODELS);
export { any, flags, ids, w, zdiff };
