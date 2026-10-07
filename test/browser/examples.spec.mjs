// The example page loads the KiCad demo board from its Gerbers without errors.
import { test, expect } from '@playwright/test';

test('examples/: the demo board builds, holes punched, no page errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/examples/');
  const demo = await page.waitForFunction(() => window.demo, null, { timeout: 120_000 }).then((h) => h.jsonValue());
  expect(demo.error).toBeUndefined();
  expect(demo.holes).toBeGreaterThan(50);
  expect(demo.matched).toBeGreaterThanOrEqual(10);   // the parts kicad-cli's GLB has models for
  await page.click('#diff');
  await page.waitForTimeout(500);
  expect(errors).toEqual([]);
});
