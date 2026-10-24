import type { WebGLRenderer, Scene, PerspectiveCamera, Group, Object3D, Box3, Texture, DirectionalLight } from 'three';

export type Vec3 = [number, number, number];
export interface View { dir: Vec3; up: Vec3 }
export type ViewName = 'top' | 'bottom' | 'front' | 'side' | 'back' | 'left' | 'right' | 'iso' | 'isoBottom';
export const VIEWS: Record<ViewName, View>;
export function fitCamera(box: { min: Vec3; max: Vec3 }, view: View, fovDeg: number, aspect: number, pad?: number):
  { position: Vec3; target: Vec3; up: Vec3; distance: number; radius: number };
export function clipPlanes(cameraPos: Vec3, sphere: { center: Vec3; radius: number }): { near: number; far: number };

export interface PickHit { object: Object3D; ref: string | null; point: Vec3; /** pane index (0 without panes) */ pane: number }
export interface ViewerOptions {
  controls?: 'trackball' | 'orbit';
  theme?: 'light' | 'dark';
  /** [top, bottom] gradient, one CSS colour, null (transparent); default: the theme's gradient. */
  background?: [string, string] | string | null;
  viewCube?: boolean;
  fov?: number;
  pixelRatio?: number;
  environment?: boolean;
  /** 'camera' (default): lights and reflections fixed to the camera, so the side facing you is lit, the underside too
   *  (cameraLighting). 'kicad': a world-fixed key and fill plus a headlight, reflections fixed to the board. */
  lighting?: 'camera' | 'kicad';
  antialias?: boolean;
  /** Keep the drawn frame readable after compositing (toDataURL / drawImage outside a frame). Default false. */
  preserveDrawingBuffer?: boolean;
  onPick?(hit: PickHit | null): void;
  /** A right click, or a touch held still: the hit under it (null for none) and where it was. */
  onContextPick?(hit: PickHit | null, where: ContextWhere): void;
  onRender?(): void;
  /** A click on content with a userData.ref makes it the orbit centre (viewer.focus); a click on nothing keeps it. Default true. */
  focusOnPick?: boolean;
}
export interface ContextWhere { clientX: number; clientY: number; pointerType: string }
export interface FlyOptions {
  /** ms (380); 0, or prefers-reduced-motion, jumps. */
  duration?: number;
  pad?: number;
  /** Frame at least this many mm, so a tiny part does not fill the screen. */
  minSize?: number;
  /** Keep the current direction unless given. */
  view?: ViewName | View | null;
}
export interface FocusOptions {
  /** Ease there (default true); false, or prefers-reduced-motion, jumps. */
  animate?: boolean;
  duration?: number;
  /** 'auto' (default): keep the distance unless the part would not fit, then back off to frame it; true: always frame it; false: keep the distance. */
  frame?: 'auto' | boolean;
  pad?: number;
  /** With frame: true, frame at least this many mm. */
  minSize?: number;
  /** Look from this direction instead of the current one. */
  view?: ViewName | View | null;
}
export type FocusTarget = string | Object3D | Object3D[] | Box3 | { min: Vec3; max: Vec3 };
export interface CaptureObjectsOptions { width?: number; height?: number; view?: ViewName | View | null; pad?: number; transparent?: boolean; type?: string }
export interface CaptureOptions { width?: number; height?: number; transparent?: boolean; viewCube?: boolean; type?: string }
export interface Viewer {
  readonly renderer: WebGLRenderer;
  readonly scene: Scene;
  readonly camera: PerspectiveCamera;
  readonly content: Group;
  readonly canvas: HTMLCanvasElement;
  readonly controls: { target: import('three').Vector3; update(): unknown; dispose(): void };
  readonly stats: { frames: number; requests: number };
  readonly theme: 'light' | 'dark';
  add<T extends Object3D>(...objects: T[]): T;
  remove(object: Object3D, opts?: { dispose?: boolean }): void;
  clear(opts?: { dispose?: boolean }): void;
  requestRender(): void;
  render(): void;
  setView(view: ViewName | View, opts?: { fit?: boolean; box?: Box3 | null; pad?: number }): Viewer;
  fit(view?: ViewName | View | null, box?: Box3 | null, pad?: number): Viewer;
  setTheme(theme: 'light' | 'dark'): void;
  setBackground(spec: [string, string] | string | null | 'theme'): void;
  setControls(kind: 'trackball' | 'orbit'): void;
  pick(clientX: number, clientY: number, opts?: { filter?: (object: Object3D) => boolean }): PickHit | null;
  /** Side-by-side panes with one camera: each inner array is what only that pane shows; null = one view. */
  setPanes(panes: Object3D[][] | null): Viewer;
  readonly panes: Object3D[][] | null;
  paneRect(index?: number): { x: number; y: number; width: number; height: number };
  /** The cube face under a client point ('top', ...), '' for its corner off the cube, null elsewhere. */
  cubeAt(clientX: number, clientY: number): string | null;
  cubeFacePoint(face: string): { x: number; y: number } | null;
  capture(opts?: CaptureOptions): string;
  /** Ease the camera to frame a box (Box3 or {min, max}); resolves once there. A press cuts it short. */
  flyTo(box: Box3 | { min: Vec3; max: Vec3 }, opts?: FlyOptions): Promise<Viewer>;
  /**
   * Orbit around a part: controls.target eases to the middle of its bounding box, keeping direction and
   * distance (backing off only if it would not fit). A ref matches content objects by userData.ref; null
   * goes back to the content's centre. Resolves null (camera unmoved) when nothing matched.
   */
  focus(what: FocusTarget | null, opts?: FocusOptions): Promise<Viewer | null>;
  /** World bounding box of a ref / object(s) / box, or null when nothing matches. */
  boxOf(what: FocusTarget): Box3 | null;
  /** PNG data URL of these objects alone, framed; the rest of the content and the camera are put back. */
  captureObjects(objects: Object3D | Object3D[], opts?: CaptureObjectsOptions): string | null;
  captureBlob(opts?: CaptureOptions): Promise<Blob>;
  resize(): void;
  on(event: 'render' | 'view', fn: () => void): () => void;
  on(event: 'cube', fn: (face: string) => void): () => void;
  on(event: 'context', fn: (hit: PickHit | null, where: ContextWhere) => void): () => void;
  dispose(opts?: { content?: boolean }): void;
}
export function createViewer(el: HTMLElement, opts?: ViewerOptions): Viewer;

export class ViewCube {
  constructor(theme?: 'light' | 'dark');
  setTheme(theme: 'light' | 'dark'): void;
  draw(renderer: WebGLRenderer, camera: PerspectiveCamera, target: import('three').Vector3, width: number, height: number): void;
  faceAt(x: number, y: number, width: number, height: number): string | null;
  facePoint(face: string, width: number, height: number): { x: number; y: number } | null;
  setHover(face: string | null): boolean;
  dispose(): void;
}
export const BACKGROUNDS: Record<'light' | 'dark', [string, string]>;
export function gradientTexture(stops: [string, string]): Texture;
export function roomEnvironment(renderer: WebGLRenderer): import("three").WebGLRenderTarget;
export function kicadLights(): { group: Group; headlight: DirectionalLight; update(camera: PerspectiveCamera, target: import('three').Vector3): void; dispose(): void };
export interface CameraLightsOptions {
  /** Headlight intensity (2.0). */
  headlight?: number;
  /** Hemisphere fill intensity (0.8). */
  fill?: number;
  sky?: import('three').ColorRepresentation;
  ground?: import('three').ColorRepresentation;
  /** The headlight's direction in camera space (x right, y up, z toward the viewer); default [-0.35, 0.45, 1]. */
  offset?: Vec3;
}
export interface CameraLights {
  group: Group;
  headlight: DirectionalLight;
  fill: import('three').HemisphereLight;
  /** Before each draw (per camera); with a scene, turns scene.environment with the camera. */
  update(camera: import('three').Camera, scene?: Scene): void;
  dispose(): void;
}
export function cameraLights(opts?: CameraLightsOptions): CameraLights;
export interface CameraLightingOptions extends CameraLightsOptions {
  environment?: boolean;
  environmentIntensity?: number;
  exposure?: number;
}
export function cameraLighting(renderer: WebGLRenderer, scene: Scene, opts?: CameraLightingOptions): {
  lights: CameraLights;
  update(camera: import('three').Camera): void;
  dispose(): void;
};
