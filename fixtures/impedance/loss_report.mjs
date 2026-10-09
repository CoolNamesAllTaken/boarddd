// The loss validation table of docs/impedance.md ("Loss and frequency"): every golden of loss-cases.json, evaluated
// as test/impedance/loss.test.mjs does, grouped by source and quantity: n, median and worst error, tolerance.
//   node fixtures/impedance/loss_report.mjs [--json]
import { readFileSync } from 'node:fs';
import { evaluate } from './loss_eval.mjs';

const cases = JSON.parse(readFileSync(new URL('./loss-cases.json', import.meta.url), 'utf8'));
const groups = new Map();
for (const g of cases.golden) {
  const v = evaluate(g), err = (100 * (v - g.ref)) / g.ref;
  const k = `${g.source}|${g.kind}|${g.key}${g.args?.loss?.conductor?.roughness ? ' (rough)' : ''}`;
  if (!groups.has(k)) groups.set(k, { source: g.source, kind: g.kind, key: k.split('|')[2], tol: g.tol_pct, errs: [] });
  groups.get(k).errs.push({ err, v, ref: g.ref, f: g.args.f ?? null });
}
const med = (a) => { const s = [...a].sort((x, y) => x - y), n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; };
const rows = [...groups.values()].map((g) => {
  const abs = g.errs.map((e) => Math.abs(e.err));
  const worst = g.errs.reduce((a, e) => (Math.abs(e.err) > Math.abs(a.err) ? e : a));
  return { ...g, n: g.errs.length, median: med(abs), worst: worst.err };
});
if (process.argv.includes('--json')) console.log(JSON.stringify(rows));
else {
  const fmt = (x) => (Math.abs(x) < 0.001 ? x.toExponential(1) : x.toFixed(Math.abs(x) < 0.1 ? 3 : 2));
  console.log('| source | quantity | n | median |err| | worst | tolerance |\n|---|---|---|---|---|---|');
  for (const r of rows) console.log(`| ${r.source} | ${r.kind} ${r.key} | ${r.n} | ${fmt(r.median)} % | ${r.worst >= 0 ? '+' : ''}${fmt(r.worst)} % | ${r.tol} % |`);
}
