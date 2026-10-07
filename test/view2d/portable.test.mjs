// view2d must run from a classic-script bundle opened from disk: no fetch, module workers, dynamic
// imports or import.meta in its sources (the renderer and every input are handed in).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';

test('src/view2d has no fetch, import(), import.meta or workers', () => {
  const dir = new URL('../../src/view2d/', import.meta.url);
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.js'))) {
    const code = readFileSync(new URL(f, dir), 'utf8').replace(/\/\/.*$/gm, '');
    for (const bad of [/\bfetch\s*\(/, /\bimport\s*\(/, /import\.meta/, /new\s+(Shared)?Worker\b/]) assert.doesNotMatch(code, bad, `${f}: ${bad}`);
  }
});
