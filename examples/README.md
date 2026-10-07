# Examples

`npm install && npm run serve`, then open http://127.0.0.1:8417/examples/.

- `index.html` + `demo.js`: KiCad's own demo board `royalblue54L_feather` (**KiCad demo data**, from
  KiCad's `demos/` folder, shipped with KiCad) built from its Gerbers and drill files with
  `boarddd/board`, and its copper diff (Δ). `examples/data/` was exported with
  `scripts/export-demo.sh` (kicad-cli 10.0.6); `board.glb` is there for `boarddd/models`.
- The peers come from `importmap.js`: `three` from `node_modules`, our wasm-gerber-renderer fork from
  `vendor/` (a dev copy).
