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

import { cameraLighting, cameraLights, type CameraLights } from "../../src/scene/index.js";
import type { Scene, WebGLRenderer, Camera } from "three";

declare const renderer: WebGLRenderer;
declare const scene: Scene;
declare const camera: Camera;

function lit(): void {
  createViewer(el, { lighting: "camera" });
  createViewer(el, { lighting: "kicad" });
  const l = cameraLighting(renderer, scene, { environmentIntensity: 0.3, headlight: 2, offset: [-0.3, 0.4, 1] });
  l.update(camera);
  const rig: CameraLights = cameraLights({ fill: 1, sky: 0xffffff, ground: "#888" });
  rig.update(camera, scene);
  rig.dispose();
  l.dispose();
}
void lit;
