# boarddd

3D PCB rendering for the browser, shared by [kipr](https://github.com/CoolNamesAllTaken/kipr) (KiCad
PR review) and gentoo (a PCB fab shop site). Framework-free ES modules, no build step, `.d.ts` typings.

- **`boarddd/geom`**: pure geometry, no three.js: coordinate frames, outlines and winding, round
  holes and stadium slots, hole budget, KiCad pad shapes (shape offset, rotation, roundrect, ...).
- **`boarddd/board`**: the board solid from an outline and drills (plated barrels, caps, UVs), face
  textures from Gerbers via [wasm-gerber-renderer](https://github.com/CoolNamesAllTaken/wasm-gerber-viewer),
  copper-diff textures.
- **`boarddd/footprint`**: one KiCad footprint on a small board: pads, copper, silk, courtyard.
- **`boarddd/models`**: GLB and STEP loading (occt in a Worker), units/up-axis detection, colour
  space, matching meshes to reference designators.
- **`boarddd/scene`**: `createViewer`: renderer, camera, controls, KiCad-like lighting, render on
  demand, view cube, view presets, capture.

Status: phase 1 in progress; the API below is filled in as modules land.

## Peers

`three` is a peer dependency, imported by the bare specifier `"three"`: map it with an importmap,
or let your bundler resolve it. `wasm-gerber-renderer` (for Gerber face textures) and occt-import-js
(for STEP) are passed in by the caller, never imported by boarddd itself, so bundling boarddd into
a classic script (e.g. esbuild IIFE) works.

```html
<script type="importmap">
{ "imports": { "three": "https://cdn.jsdelivr.net/npm/three@0.185.0/build/three.module.js",
               "boarddd/": "https://cdn.jsdelivr.net/gh/CoolNamesAllTaken/boarddd@main/src/" } }
</script>
```

## Frames

All geometry is in the **board frame**: millimetres, x right, y up (KiCad's y negated; Gerber
coordinates), z up out of the top copper, board bottom face at z = 0 and top face at z = thickness.
`kicadToBoard(x, y)` and `boardToKicad(x, y)` convert points; the viewer uses camera.up = +z.

## Development

```sh
npm install
npm test                      # node --test: test/**/*.test.mjs
npm run test:browser          # playwright, headless Chromium (WebGL2 via SwiftShader)
npm run serve                 # examples at http://127.0.0.1:8417/examples/
```

## Credits

Ported from gentoo's `viewer3d.js` (PantsForBirds), kipr's `web/project/pcba3d` and
`web/library/js` (CoolNamesAllTaken/kipr). Each module names its sources.
