import { createViewer, type FocusOptions, type Viewer } from "../../src/scene/index.js";

declare const el: HTMLElement;

async function orbit(): Promise<void> {
  const v: Viewer = createViewer(el, { controls: "orbit", focusOnPick: true });
  const opts: FocusOptions = { animate: false, frame: "auto", pad: 1.4 };
  const there: Viewer | null = await v.focus("U1", opts);
  if (!there) await v.focus(null);
  await v.focus({ min: [0, 0, 0], max: [1, 1, 1] }, { frame: true, minSize: 4, view: "top" });
  const box = v.boxOf(v.content);
  if (box) await v.focus(box, { frame: false });
}
void orbit;
