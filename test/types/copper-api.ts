// Compile-only check of the boarddd/copper typings.
import { assertCopper, checkFills, copperFromGerbers, parseCopperLayer, planes, unfracture, validateCopper } from '../../src/copper/index.js';
import type { Copper, CopperPad, FillPolygon, Plane, Track, Via, Zone } from '../../src/copper/index.js';

const doc: Copper = copperFromGerbers([{ name: 'b-F_Cu.gbr', text: '' }], { name: 'b', checkFills: false });
const errors: string[] = validateCopper(doc);
const same: Copper = assertCopper(doc);
const t: Track = doc.tracks![0];
const arc: boolean = t.mid != null;
const v: Via = doc.vias![0];
const span: [string, string] = v.span;
const z: Zone = doc.zones![0];
const holes: number = z.fill.reduce((n, p: FillPolygon) => n + p.holes!.length, 0);
const pad: CopperPad = doc.pads![0];
const ref: string | null = pad.ref;
const ps: Plane[] = planes(doc.zones!, null, doc.layers);
const n: number = checkFills(doc);
const layer = parseCopperLayer('', 'F.Cu');
const rings: number = layer.viaRings.length;
const polys: FillPolygon[] = unfracture([[0, 0], [1, 0], [1, 1]]);
export { arc, errors, holes, n, polys, ps, ref, rings, same, span };
