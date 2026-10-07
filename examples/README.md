# Examples

`npm install && npm run serve`, then open http://127.0.0.1:8417/examples/.

- `index.html` + `demo.js`: KiCad's own demo board `royalblue54L_feather` (**KiCad demo data**, from
  KiCad's `demos/` folder, shipped with KiCad) built from its Gerbers and drill files with
  `boarddd/board`, its components from kicad-cli's GLB matched to their reference designators with
  `boarddd/models` (`components.json`: the placements, from `kicad-cli pcb export pos`), shown in
  `boarddd/scene`'s viewer; ▣ toggles the components, Δ the copper diff. `examples/data/` was exported
  with `scripts/export-demo.sh` (kicad-cli 10.0.6).
- The peers come from `importmap.js`: `three` from `node_modules`. The Gerber faces use `boarddd/gerber`
  and its committed wasm (no renderer to inject).
