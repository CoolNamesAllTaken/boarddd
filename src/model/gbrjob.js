// boarddd/model: a Gerber job file (.gbrjob, JSON) as a boarddd/board@1 Stackup, in the browser, for Gerber-only
// uploads (boarddd/impedance needs a stackup). The same rules as python/src/boarddd/io/gbrjob.py read_stackup:
// GeneralSpecs (thickness, copper count, finish, ImpedanceControlled) and MaterialStackup top to bottom; Er and Df
// as numbers or KiCad's strings; a dielectric KiCad splits into "(1/2)", "(2/2)" entries becomes one layer with
// sublayers (thickness summed, Er in series, Df weighted). The job has no prepreg/core, frequency or roughness.

const KIND = { legend: 'silk', solderpaste: 'paste', soldermask: 'mask', copper: 'copper', dielectric: 'dielectric' };
const SUBLAYER = /^(.*?)\s*\((\d+)\/(\d+)\)$/;
const r6 = (v) => Math.round(v * 1e6) / 1e6 + 0;
const num = (v) => { const x = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN; return Number.isFinite(x) ? r6(x) : null; };

function addSublayer(layer, part) {
  const sub = (l) => ({ thickness: l.thickness, material: l.material, color: l.color, epsilon_r: l.epsilon_r, loss_tangent: l.loss_tangent });
  if (!layer.sublayers.length) layer.sublayers = [sub(layer)];
  if (!part) return;
  layer.sublayers.push(sub(part));
  const subs = layer.sublayers;
  if (subs.every((s) => s.thickness != null)) {
    const total = subs.reduce((t, s) => t + s.thickness, 0);
    layer.thickness = r6(total);
    if (total > 0 && subs.every((s) => s.epsilon_r)) layer.epsilon_r = r6(total / subs.reduce((t, s) => t + s.thickness / s.epsilon_r, 0));
    if (total > 0 && subs.every((s) => s.loss_tangent != null)) layer.loss_tangent = r6(subs.reduce((t, s) => t + s.thickness * s.loss_tangent, 0) / total);
  }
  const mats = [...new Set(subs.map((s) => s.material).filter(Boolean))];
  layer.material = mats.length ? mats.join(' + ') : null;
}

/**
 * A board@1 Stackup from a Gerber job file (its JSON text or the parsed object). `copperIds`: the board's copper
 * layer ids top to bottom (default F.Cu, In1.Cu … B.Cu by count), linked to the copper entries when the counts
 * agree; silk/paste/mask entries get KiCad's ids (F.Mask, B.Silkscreen…).
 */
export function stackupFromJob(job, copperIds = null) {
  let data = job;
  if (typeof job === 'string') { try { data = JSON.parse(job); } catch { data = {}; } }
  if (!data || typeof data !== 'object') data = {};
  const specs = data.GeneralSpecs && typeof data.GeneralSpecs === 'object' ? data.GeneralSpecs : {};
  const entries = (Array.isArray(data.MaterialStackup) ? data.MaterialStackup : []).filter((e) => e && typeof e === 'object');
  const kinds = entries.map((e) => KIND[String(e.Type ?? '').replace(/ /g, '').toLowerCase()] ?? 'other');
  const coppers = kinds.flatMap((k, i) => (k === 'copper' ? [i] : []));
  const ids = copperIds ?? (coppers.length >= 2 ? coppers.map((_, i) => (i === 0 ? 'F.Cu' : i === coppers.length - 1 ? 'B.Cu' : `In${i}.Cu`)) : null);
  const KI = { silk: 'Silkscreen', paste: 'Paste', mask: 'Mask' };
  const out = [];
  entries.forEach((entry, i) => {
    const kind = kinds[i];
    const side = !coppers.length || i < coppers[0] || (kind === 'copper' && i === coppers[0]) ? 'top'
      : i > coppers[coppers.length - 1] || (kind === 'copper' && i === coppers[coppers.length - 1]) ? 'bottom' : 'inner';
    let layer = null;
    if (kind === 'copper' && ids && ids.length === coppers.length) layer = ids[coppers.indexOf(i)];
    else if (KI[kind] && side !== 'inner') layer = `${side === 'top' ? 'F' : 'B'}.${KI[kind]}`;
    const name = String(entry.Name || entry.Type || '');
    const sl = {
      name, kind, side, thickness: num(entry.Thickness), material: entry.Material ? String(entry.Material) : null,
      color: entry.Color ? String(entry.Color) : null, epsilon_r: num(entry.DielectricConstant) || null,
      loss_tangent: num(entry.LossTangent), layer, sublayers: [], conductivity: num(entry.Conductivity) || null,
    };
    const sub = kind === 'dielectric' ? SUBLAYER.exec(name) : null;
    const last = out[out.length - 1];
    if (sub && last && Number(sub[2]) > 1 && last.kind === 'dielectric' && last.name === sub[1]) { addSublayer(last, sl); return; }
    if (sub) { sl.name = sub[1]; addSublayer(sl, null); }
    out.push(sl);
  });
  const colour = (kind, side) => out.find((l) => l.kind === kind && l.side === side && l.color)?.color ?? null;
  const count = specs.LayerNumber;
  return {
    thickness: num(specs.BoardThickness),
    copper_layers: Number.isInteger(count) && count > 0 ? count : coppers.length || null,
    finish: specs.Finish ? String(specs.Finish) : null,
    mask_color: { top: colour('mask', 'top'), bottom: colour('mask', 'bottom') },
    silk_color: { top: colour('silk', 'top'), bottom: colour('silk', 'bottom') },
    layers: out,
    impedance_controlled: typeof specs.ImpedanceControlled === 'boolean' ? specs.ImpedanceControlled : null,
  };
}
