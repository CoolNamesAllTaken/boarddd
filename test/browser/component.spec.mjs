// buildComponent in headless Chromium: a through-hole part's holes are open from the top, its bottom pads are
// there from below and through a see-through board from the side; an SMD part has top pads and no holes.
import { test, expect } from '@playwright/test';

const MAGENTA = ([r, g, b]) => r > 200 && g < 60 && b > 200;
// copper (0xe9b934 lit): red and green high, blue low
const COPPER = ([r, g, b]) => r > 150 && g > 100 && b < 120 && r > b + 60;

async function open(page, query) {
  await page.goto(`/test/browser/pages/component.html?${query}`);
  const h = await page.waitForFunction(() => window.harness, null, { timeout: 60_000 }).then((x) => x.jsonValue());
  expect(h.error).toBeUndefined();
  return h.info;
}

// Footprint frame (y up): pad 1 at (0, 0), pad 5 at (0, 7.62); an annulus point 0.9 mm off a pad's centre.
const PAD1 = [0, 0], PAD5 = [0, 7.62], RING = 0.9;

test('THT part from the top: holes open through the board, annular rings copper', async ({ page }) => {
  const info = await open(page, 'ref=K1&view=top');
  expect(info.copper.filter((c) => c.side === 'bottom').length).toBe(5);
  expect(info.holes).toBe(1);
  const [hole1, hole5, ring1, via] = await page.evaluate((p) => window.sample(p),
    [[...PAD1, 1.7], [...PAD5, 1.7], [PAD1[0] + RING, PAD1[1], 1.7], [5, 3, 1.7]]);
  expect(MAGENTA(hole1), JSON.stringify(hole1)).toBe(true);
  expect(MAGENTA(hole5), JSON.stringify(hole5)).toBe(true);
  expect(MAGENTA(via), JSON.stringify(via)).toBe(true);
  expect(COPPER(ring1), JSON.stringify(ring1)).toBe(true);
});

test('THT part from below: the bottom pads are there', async ({ page }) => {
  await open(page, 'ref=K1&view=bottom');
  const [ring1, ring5, hole1] = await page.evaluate((p) => window.sample(p),
    [[PAD1[0] + RING, PAD1[1], -0.05], [PAD5[0] + RING, PAD5[1], -0.05], [...PAD1, -0.05]]);
  expect(COPPER(ring1), JSON.stringify(ring1)).toBe(true);
  expect(COPPER(ring5), JSON.stringify(ring5)).toBe(true);
  expect(MAGENTA(hole1), JSON.stringify(hole1)).toBe(true);
});

test('THT part from the front, see-through board: the bottom pads show through it', async ({ page }) => {
  // Looking along +y at the board's edge: pad 1's bottom plate is under the board's bottom face.
  const solid = await open(page, 'ref=K1&view=front');
  expect(solid.thickness).toBe(1.6);
  const [under] = await page.evaluate((p) => window.sample(p), [[PAD1[0] + RING, PAD1[1], -0.02]]);
  expect(COPPER(under), JSON.stringify(under)).toBe(true);
  // the barrels, inside the board, only show through a see-through one
  await open(page, 'ref=K1&view=front&opacity=0.35');
  const [barrel] = await page.evaluate((p) => window.sample(p), [[0.6, -9, 0.8]]);
  const plainBoard = await open(page, 'ref=K1&view=front&opacity=1').then(() => page.evaluate((p) => window.sample(p), [[0.6, -9, 0.8]]));
  expect(barrel).not.toEqual(plainBoard[0]);
});

test('SMD part: two top pads, nothing on the bottom, no holes', async ({ page }) => {
  const info = await open(page, 'ref=R1&view=top');
  expect(info.copper).toEqual([{ pad: '1', side: 'top' }, { pad: '2', side: 'top' }]);
  expect(info.barrels).toBe(0);
  expect(info.holes).toBe(0);
  const [pad1] = await page.evaluate((p) => window.sample(p), [[-1.1, 0.2, 1.65]]);
  expect(COPPER(pad1), JSON.stringify(pad1)).toBe(true);
  await open(page, 'ref=R1&view=bottom');
  const [under] = await page.evaluate((p) => window.sample(p), [[-1.1, 0.2, -0.01]]);
  expect(COPPER(under), JSON.stringify(under)).toBe(false);
});
