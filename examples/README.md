# Examples

`npm install && npm run serve`, then open http://127.0.0.1:8417/examples/.

- `index.html` + `demo.js`: KiCad's own demo board `royalblue54L_feather` (**KiCad demo data**, from
  KiCad's `demos/` folder, shipped with KiCad) built from its Gerbers and drill files with
  `boarddd/board`, its components from kicad-cli's GLB matched to their reference designators with
  `boarddd/models` (`components.json`: the placements, from `kicad-cli pcb export pos`), shown in
  `boarddd/scene`'s viewer; ▣ toggles the components, Δ the copper diff. `examples/data/` was exported
  with `scripts/export-demo.sh` (kicad-cli 10.0.6).
- `view2d.html` + `view2d.js`: `boarddd/view2d` on pic_programmer base vs head (KiCad demo; head moves
  mounting hole P101 and edits copper), royalblue54L with placement ticks, and a pic_programmer schematic
  sheet (R7 10K → 4.7K) as an ink diff. Every compare mode, measure, hole picking; the view is kept in the URL.
- `impedance.html` + `impedance.js`: `boarddd/impedance/ui`. royalblue54L comes from its Gerber X2 export (copper by
  `copperFromGerbers` in the browser, stackup from the gbrjob, a 90 Ω target entered for the USB pair). CM5 MINIMA
  comes from its copper@1 (`fixtures/impedance/route/cm5.json.gz`). Click a trace; `#b=cm5` opens CM5, and
  `#select=0` starts with nothing selected.
- The peers come from `importmap.js`: `three` from `node_modules`. The Gerber faces use `boarddd/gerber`
  and its committed wasm (no renderer to inject).
