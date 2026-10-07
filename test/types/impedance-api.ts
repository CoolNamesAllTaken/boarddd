// Compile-only check of the boarddd/impedance typings.
import { calculate, coupledStripline, evaluateTarget, lineFromStackup, microstrip, MODELS, synthesize } from '../../src/impedance/index.js';
import type { Board } from '../../src/model/index.js';
import type { CoupledResult, ImpedanceFlag, LineResult } from '../../src/impedance/index.js';

const ms: LineResult = microstrip({ w: 0.36, h: 0.2104, t: 0.035, er: 4.4 });
const flags: ImpedanceFlag[] = ms.flags;
const pair: CoupledResult = coupledStripline({ w: 0.1, s: 0.15, h1: 0.2, h2: 0.3, er: 4.1 });
const zdiff: number = pair.Zdiff;
const w: number = synthesize('microstrip', { h: 0.2, er: 4.4 }, 50).value;
const any = calculate('cpwg', { w: 0.3, gap: 0.15, h: 0.3, er: 4.5 });
const ids: string[] = Object.keys(MODELS);
declare const board: Board;
const line = lineFromStackup(board.stackup!, 'In1.Cu', { width: 0.1, refTop: 'F.Cu' });
const rows = board.net_classes?.flatMap((nc) => (nc.impedance ? evaluateTarget(board.stackup!, nc.impedance) : [])) ?? [];
const okay: (boolean | null)[] = rows.map((r) => r.ok);
export { any, flags, ids, line, okay, w, zdiff };

// tier 2
import { fieldCalculate, sectionFor, solveCrossSection } from '../../src/impedance/index.js';
import type { CrossSection, FieldResult } from '../../src/impedance/index.js';
const section: CrossSection = { conductors: [{ y0: -0.035, y1: 0, net: 'gnd' }, { x0: -0.1, x1: 0.1, y0: 0.2, y1: 0.235, net: 'sig' }], dielectrics: [{ y0: 0, y1: 0.2, er: 4.4 }] };
const fr: FieldResult = solveCrossSection(section, { tol: 0.005 });
const z0f: number | undefined = fr.Z0;
const cm: number[][] = fr.matrices.C;
const fc = fieldCalculate('coupled_cpwg', { w: 0.1, s: 0.1, gap: 0.2, h: 0.1, t: 0.018, er: 4.2 });
const zd: number | undefined = fc.Zdiff;
const sec2: CrossSection = sectionFor('coated_microstrip', { w: 0.3, h: 0.2, t: 0.035, er: 4.4, c: 0.015, erc: 3.8 });
const fl = lineFromStackup(board.stackup!, 'F.Cu', { width: 0.15, solver: 'field' });
const fsec: CrossSection = fl.section;
const frows = evaluateTarget(board.stackup!, board.net_classes![0].impedance!, { solver: 'field', fieldOptions: { tol: 0.02 } });
const fz: number = frows[0].result.error_pct;
void [z0f, cm, zd, sec2, fsec, fz];
