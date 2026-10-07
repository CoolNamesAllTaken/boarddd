// Every runtime export of geom/board/footprint is declared in its index.d.ts, and nothing extra.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

for (const m of ['geom', 'board', 'footprint']) {
  test(`${m}: index.d.ts declares exactly the runtime exports`, async () => {
    const mod = await import(`../src/${m}/index.js`);
    const dts = readFileSync(new URL(`../src/${m}/index.d.ts`, import.meta.url), 'utf8');
    const declared = new Set([...dts.matchAll(/^export (?:declare )?(?:function|const) (\w+)/gm)].map((x) => x[1]));
    assert.deepEqual([...Object.keys(mod)].sort(), [...declared].sort());
  });
}
