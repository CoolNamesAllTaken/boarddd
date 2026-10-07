// Copper diff: head = slots-board, base = the same board without H1. H1's copper is added (green), J1's
// is unchanged (the dim copper tint), bare board is the diff background.
import { test, expect } from '@playwright/test';

test('copper diff marks the added footprint green and leaves the rest unchanged', async ({ page }) => {
  await page.goto('/test/browser/pages/diff.html');
  const d = await page.waitForFunction(() => window.diff, null, { timeout: 120_000 }).then((x) => x.jsonValue());
  expect(d.error).toBeUndefined();
  const at = (face, x, y) => page.evaluate(([f, a, b]) => window.diff.sample(f, a, b), [face, x, y]);
  // H1 pad 1: PTH 4.2 x 2.6 oval at KiCad (128, 97): copper ring at x = 128 + 1.8
  const added = await at('top', 129.8, -97);
  expect(added[1]).toBeGreaterThan(added[0] + 40);            // green
  const addedBottom = await at('bottom', 129.8, -97);
  expect(addedBottom[1]).toBeGreaterThan(addedBottom[0] + 40);
  // J1: the HRO USB-C at KiCad (110, 100); its signal pads row is unchanged copper on top
  const unchangedPts = [];
  for (let x = 107; x <= 113; x += 0.1) unchangedPts.push(await at('top', x, -(100 - 4.045)));
  const tinted = unchangedPts.filter(([r, g, b]) => r > 100 && r > b + 30 && Math.abs(r - g) < 60);
  expect(tinted.length).toBeGreaterThan(5);
  expect(unchangedPts.some(([r, g]) => g > r + 40)).toBe(false);   // nothing there is "added"
  // empty board: the diff background (#2d333b)
  const bg = await at('top', 104, -107);
  expect(bg).toEqual([0x2d, 0x33, 0x3b]);
});
