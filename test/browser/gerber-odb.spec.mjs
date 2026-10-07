import { expect, test } from "@playwright/test";

// loadOdbJob in a real browser: KiCad's ODB++ export of the royalblue54L_feather demo (a .zip, read with the
// platform DecompressionStream) becomes renderer layers that draw the same board as the same design's Gerbers.

const ODB = "/fixtures/royalblue54L_feather/exchange/RoyalBlue54L-Feather-odb.zip";
const FAB = "/fixtures/royalblue54L_feather/fab/RoyalBlue54L-Feather-";
const GERBERS = ["F_Cu", "B_Cu", "F_Mask", "B_Mask", "F_Silkscreen", "B_Silkscreen", "Edge_Cuts"].map((n) => `${n}.gbr`);
const DRILLS = ["PTH.drl", "NPTH.drl"];

test.beforeEach(async ({ page }) => {
  page.on("pageerror", (error) => {
    throw error;
  });
  await page.goto("/test/browser/pages/blank.html");
  await page.evaluate(
    async ({ odb, fab, gerbers, drills }) => {
      const api = await import("/src/gerber/index.js");
      const canvas = document.createElement("canvas");
      document.body.appendChild(canvas);
      const renderer = await api.createGerberRenderer(canvas, { contextAttributes: { preserveDrawingBuffer: true } });
      const text = async (url) => (await fetch(url)).text();
      const fabFiles = await Promise.all(
        [...gerbers, ...drills].map(async (name) => ({ name: `RoyalBlue54L-Feather-${name}`, content: await text(fab + name) })),
      );
      const warnings = [];
      const blob = await (await fetch(odb)).blob();
      const odbLayers = await api.loadOdbJob(new File([blob], "RoyalBlue54L-Feather-odb.zip"), {
        renderer,
        onWarning: (label, message) => warnings.push(`${label}: ${message}`),
      });
      // Non-black pixels of the current frame, as a bitmap.
      const ink = () => {
        const { width, height } = renderer.lastFrame;
        const gl = canvas.getContext("webgl2");
        const pixels = new Uint8Array(width * height * 4);
        gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        const out = new Uint8Array(width * height);
        for (let i = 0; i < out.length; i += 1) out[i] = pixels[4 * i] || pixels[4 * i + 1] || pixels[4 * i + 2] ? 1 : 0;
        return out;
      };
      Object.assign(window, { t: { api, renderer, canvas, fabFiles, odbLayers, warnings, ink } });
    },
    { odb: ODB, fab: FAB, gerbers: GERBERS, drills: DRILLS },
  );
});

test("loadOdbJob reads the KiCad ODB++ zip into named renderer layers", async ({ page }) => {
  const result = await page.evaluate(() => {
    const { odbLayers, warnings } = window.t;
    return {
      layers: odbLayers.map((l) => [l.name, l.kind]),
      envelopes: odbLayers.every((l) => l.source.startsWith("%ODB++LAYER%")),
      warnings,
    };
  });
  expect(result.layers).toEqual([
    ["profile.gko", "gerber"],
    ["f.silkscreen.gto", "gerber"],
    ["f.paste.gtp", "gerber"],
    ["f.mask.gts", "gerber"],
    ["f.cu.gtl", "gerber"],
    ["in1.cu-inner1.gbr", "gerber"],
    ["in2.cu-inner2.gbr", "gerber"],
    ["in3.cu-inner3.gbr", "gerber"],
    ["in4.cu-inner4.gbr", "gerber"],
    ["in5.cu-inner5.gbr", "gerber"],
    ["in6.cu-inner6.gbr", "gerber"],
    ["b.cu.gbl", "gerber"],
    ["b.mask.gbs", "gerber"],
    ["b.silkscreen.gbo", "gerber"],
    ["drill_non-plated_f.cu-b.cu.drl", "drill"],
    ["drill_plated_f.cu-b.cu.drl", "drill"],
  ]);
  expect(result.envelopes).toBe(true);
  // B.Paste is empty on this board; the comp_+_top/bot, dielectric and document layers are not board layers.
  expect(result.warnings).toEqual([
    "b.paste.gbp: Layer has no features; skipped",
    "job: Skipped 30 non-board layers: COMPONENT x2, DIELECTRIC x7, MISC/DOCUMENT x21", // KiCad's JOB_NAME
  ]);
});

// Each layer drawn twice in one view; `near` is the share of either drawing's ink with ink of the other within
// 2 px (KiCad writes ODB++ to 0.01 mm, the Gerbers to 1e-6 mm; drills draw thin rims).
// Known renderer difference on the plated drills (hence the lower bound there): the vendored wasm draws an
// ODB++ oval drill pad (KiCad's 4 plated slots, `oval600x1700`) as a round hole, and the 183 placeholder vias
// (`r0.01`, dropped from the Gerber path by withoutEmptyTools) as specks. boarddd.io.odbpp reads them as slots.
// Non-plated holes draw as frame background without a rim, so they leave no ink to compare here; their
// positions are checked by python/tests/io/test_exchange.py.
for (const [odbName, gerberName, minIou, minNear] of [
  ["f.cu.gtl", "RoyalBlue54L-Feather-F_Cu.gbr", 0.97, 0.99],
  ["b.cu.gbl", "RoyalBlue54L-Feather-B_Cu.gbr", 0.97, 0.99],
  ["f.mask.gts", "RoyalBlue54L-Feather-F_Mask.gbr", 0.95, 0.99],
  ["drill_plated_f.cu-b.cu.drl", "RoyalBlue54L-Feather-PTH.drl", null, 0.9],
]) {
  test(`the ODB++ ${odbName} draws the same pixels as ${gerberName}`, async ({ page }, testInfo) => {
    const result = await page.evaluate(
      async ({ odbName, gerberName }) => {
        const { renderer, fabFiles, odbLayers, ink, api } = window.t;
        const gerber = fabFiles.find((f) => f.name === gerberName);
        const odb = odbLayers.find((l) => l.name === odbName);
        const kind = odb.kind;
        const source = kind === "drill" ? api.withoutEmptyTools(gerber.content) : gerber.content;
        const frame = { width: 1200, height: 500, padding: 1, background: "#000000" };
        await renderer.withFrame(frame, async () => {
          await renderer.renderLayer({ source, name: gerber.name, kind, color: [1, 1, 1] });
        });
        const view = renderer.lastFrame.view;
        const a = ink();
        await renderer.withFrame({ ...frame, fit: false, view }, async () => {
          await renderer.renderLayer({ source: odb.source, name: odb.name, kind, color: [1, 1, 1] });
        });
        const b = ink();
        let both = 0;
        let either = 0;
        for (let i = 0; i < a.length; i += 1) {
          both += a[i] & b[i];
          either += a[i] | b[i];
        }
        const { width, height } = renderer.lastFrame;
        const near = (x, y) => {
          let hit = 0;
          let total = 0;
          for (let r = 0; r < height; r += 1) {
            for (let c = 0; c < width; c += 1) {
              if (!x[r * width + c]) continue;
              total += 1;
              found: for (let dr = -2; dr <= 2; dr += 1) {
                for (let dc = -2; dc <= 2; dc += 1) {
                  const rr = r + dr;
                  const cc = c + dc;
                  if (rr >= 0 && rr < height && cc >= 0 && cc < width && y[rr * width + cc]) {
                    hit += 1;
                    break found;
                  }
                }
              }
            }
          }
          return total ? hit / total : 0;
        };
        return { iou: either ? both / either : 0, ink: either, gerberNearOdb: near(a, b), odbNearGerber: near(b, a) };
      },
      { odbName, gerberName },
    );
    testInfo.annotations.push({ type: "pixels", description: JSON.stringify(result) });
    expect(result.ink).toBeGreaterThan(1000);
    if (minIou !== null) expect(result.iou).toBeGreaterThan(minIou);
    expect(result.gerberNearOdb).toBeGreaterThan(minNear);
    expect(result.odbNearGerber).toBeGreaterThan(minNear);
  });
}

test("renderBoard draws the ODB++ job and the Gerbers as the same board", async ({ page }, testInfo) => {
  const results = {};
  for (const side of ["top", "bottom"]) {
    const pixels = {};
    for (const label of ["gerber", "odb"]) {
      pixels[label] = await page.evaluate(
        async ({ label, side }) => {
          const { api, renderer, canvas, fabFiles, odbLayers } = window.t;
          const files =
            label === "odb"
              ? odbLayers.map((l) => ({ name: l.name, content: l.source }))
              : fabFiles.map((f) => ({ ...f, content: f.name.endsWith(".drl") ? api.withoutEmptyTools(f.content) : f.content }));
          const board = api.groupBoardLayers(files);
          await api.renderBoard(renderer, board, { side, width: 1200, height: 520, padding: 1, background: "#202020" });
          const { width, height } = renderer.lastFrame;
          const gl = canvas.getContext("webgl2");
          const data = new Uint8Array(width * height * 4);
          gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, data);
          return Array.from(data);
        },
        { label, side },
      );
      await testInfo.attach(`${label}-${side}`, { body: await page.locator("canvas").screenshot(), contentType: "image/png" });
    }
    const a = pixels.gerber;
    const b = pixels.odb;
    let same = 0;
    let board = 0;
    for (let i = 0; i < a.length; i += 4) {
      const background = (r, g, bl) => r === 0x20 && g === 0x20 && bl === 0x20;
      if (background(b[i], b[i + 1], b[i + 2])) continue;
      board += 1;
      if (Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]) < 24) same += 1;
    }
    results[side] = { board, same: same / Math.max(board, 1) };
  }
  testInfo.annotations.push({ type: "pixels", description: JSON.stringify(results) });
  for (const side of ["top", "bottom"]) {
    // the board fills most of the frame, and draws (substrate, copper, mask, finish, silk, holes) as the Gerbers do
    expect(results[side].board).toBeGreaterThan(400_000);
    expect(results[side].same).toBeGreaterThan(0.99); // the rest: the 4 slots drawn round, and edges
  }
});
