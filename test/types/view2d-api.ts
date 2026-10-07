import { createGerberRenderer, type FrameBounds } from "../../src/gerber/index.js";
import {
  createCompare,
  createHitIndex,
  createStage,
  face,
  faceBoard,
  formatViewState,
  image,
  layerStack,
  layers,
  parseViewState,
  repeat,
  segmentShape,
  type Hole,
  type Placement,
  type CompareMode,
  type MeasureResult,
  type Region,
} from "../../src/view2d/index.js";

declare const host: HTMLElement;
declare const files: Array<{ name: string; source: string }>;
declare const sheet: string;
declare const bounds: FrameBounds;

async function app(): Promise<string> {
  const renderer = await createGerberRenderer(document.createElement("canvas"));
  const stage = createStage(host, { renderer, bounds, flip: false, settleMs: 100 });
  const stack = layerStack(files);
  const top = face(faceBoard(files, "top"), { side: "top", palette: { mask: "green" } });
  const cmp = createCompare(stage, { base: top, head: layers(stack), mode: "swipe", swipe: 0.25 });
  const mode: CompareMode = cmp.mode;
  stage.setScene([{ label: "sheet", layers: [{ content: image(sheet, bounds), opacity: 0.5 }] }]);
  const index = createHitIndex([segmentShape(0, 0, 10, 0, 0.2, { net: "GND" })]);
  stage.on("click", (e) => {
    const hit = index.at(e.x, e.y, 4 * stage.mmPerPx());
    if (hit) console.log(hit.data.net, e.side);
  });
  stage.on("measure", (e) => {
    const m: MeasureResult | null = e.result;
    if (m) console.log(m.distance);
  });
  const marks = stage.addOverlay({ space: "world", draw: (g, ctx) => { ctx.svg("rect", { x: 1, y: 2, width: 3, height: 4 }, g); } });
  marks.invalidate();
  const region: Region | null = stage.getRegion();
  cmp.setState({ ...parseViewState(new URLSearchParams("z=1,2,3&mode=onion&op=0.4")), region });
  // a board in an app's own colours: the mask inverted to the outline, laminate, holes open; a panel's copies
  const rings: Array<[number, number]>[] = [[[0, 0], [10, 0], [10, 5], [0, 5]]];
  const holes: Hole[] = [{ x: 1, y: 1, d: 0.8 }, { x: 3, y: 1, diameter: 1, x2: 4, y2: 1, filled: false }];
  const board = layers([{ source: files[0].source, color: [0.05, 0.32, 0.16], inverted: true }], { outline: { board: rings[0], cutouts: [] }, substrate: "#c9b27c", holes });
  const copies: Placement[] = [{ x: 0, y: 0 }, { x: 20, y: 10, rotation: 180 }];
  const picture = createStage(host, { renderer: () => renderer, interactive: false, pixelSnap: true, paddingPx: 16, padding: 0 });
  picture.setScene([{ layers: [{ content: repeat(board, copies, bounds), opacity: 0.8 }] }]);
  await stage.ready();
  return `${mode} ${new URLSearchParams(formatViewState(cmp.getState()))}`;
}

void app;
