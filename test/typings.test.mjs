// Every runtime export of geom/board/footprint/gerber is declared in its index.d.ts, and nothing extra.
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

// gerber's index.d.ts re-exports (export *), so ask the TypeScript checker for its value exports.
test('gerber: index.d.ts declares exactly the runtime exports', async () => {
  const { default: ts } = await import('typescript');
  const file = new URL('../src/gerber/index.d.ts', import.meta.url).pathname;
  const program = ts.createProgram([file], { noEmit: true, lib: ['lib.es2022.d.ts', 'lib.dom.d.ts'], moduleResolution: ts.ModuleResolutionKind.Bundler, module: ts.ModuleKind.ESNext });
  const checker = program.getTypeChecker();
  const exports = checker.getExportsOfModule(checker.getSymbolAtLocation(program.getSourceFile(file)));
  const declared = exports.filter((s) => {
    const t = s.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(s) : s;
    return t.flags & ts.SymbolFlags.Value;
  }).map((s) => s.name);
  const mod = await import('../src/gerber/index.js');
  assert.deepEqual(Object.keys(mod).sort(), declared.sort());
});
