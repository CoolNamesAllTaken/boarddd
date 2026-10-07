import { test, expect } from '@playwright/test';

test('every subpath imports in the browser and WebGL2 is available', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/test/browser/pages/smoke.html');
  const smoke = await page.waitForFunction(() => window.smoke).then((h) => h.jsonValue());
  expect(smoke.modules).toBe(5);
  expect(smoke.webgl2).toBe(true);
  expect(errors).toEqual([]);
});
