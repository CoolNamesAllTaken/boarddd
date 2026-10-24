// createViewer's default lighting ('camera'): the side facing the viewer is lit, so a board seen from below
// shows its bottom pads bright and copper-coloured, as bright as the top ones from above.
import { test, expect } from '@playwright/test';

const luma = ([r, g, b]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
const COPPER = ([r, g, b]) => r > 150 && g > 100 && r > b + 60;
// K1's annular rings, 0.9 mm off pads 1 and 5, on the bottom face (z 0) and the top (1.6)
const RINGS = (z) => [[0.9, 0, z], [0.9, 7.62, z]];

async function open(page, query) {
  await page.goto(`/test/browser/pages/lighting.html?${query}`);
  const h = await page.waitForFunction(() => window.harness, null, { timeout: 60_000 }).then((x) => x.jsonValue());
  expect(h.error).toBeUndefined();
  return h.info;
}

for (const view of ['bottom', 'under']) {
  test(`camera lighting, from ${view}: the bottom pads are bright copper`, async ({ page }) => {
    const info = await open(page, `view=${view}`);
    expect(info.pads).toContain('bottom');
    const rings = await page.evaluate((p) => window.sample(p), RINGS(-0.04));
    for (const px of rings) {
      expect(luma(px), JSON.stringify(rings)).toBeGreaterThan(100);
      expect(COPPER(px), JSON.stringify(rings)).toBe(true);
    }
  });
}

test('camera lighting: the bottom pads from below match the top pads from above', async ({ page }) => {
  await open(page, 'view=top');
  const top = await page.evaluate((p) => window.sample(p), RINGS(1.64));
  await open(page, 'view=bottom');
  const bottom = await page.evaluate((p) => window.sample(p), RINGS(-0.04));
  for (let i = 0; i < top.length; i++) {
    expect(Math.abs(luma(top[i]) - luma(bottom[i])), JSON.stringify({ top, bottom })).toBeLessThan(25);
  }
});

test('camera lighting: the headlight and the fill turn with the camera', async ({ page }) => {
  await open(page, 'view=under');
  const turned = await page.evaluate(() => {
    const { scene, camera } = window.v;
    const lights = scene.getObjectByName('boarddd-camera-lights');
    const head = lights.children.find((l) => l.isDirectionalLight);
    const toLight = head.position.clone().sub(head.target.position).normalize();
    const toCamera = camera.position.clone().sub(window.v.controls.target).normalize();
    return { dot: toLight.dot(toCamera) };
  });
  expect(turned.dot).toBeGreaterThan(0.8);       // from behind the viewer, slightly off-axis
  expect(turned.dot).toBeLessThan(0.999);
});

test("lighting: 'kicad' keeps the older world-fixed rig", async ({ page }) => {
  await open(page, 'lighting=kicad&view=bottom');
  const names = await page.evaluate(() => window.v.scene.children.map((c) => c.name));
  expect(names).toContain('boarddd-lights');
  expect(names).not.toContain('boarddd-camera-lights');
});
