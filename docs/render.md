# Server-side renderers (`boarddd.render`)

SVG from KiCad files and from the board model, for pages without a browser renderer (reports, emails, PDFs,
thumbnails) and for review tools; PNG with the optional `[render]` extra.

```bash
pip install "boarddd[render] @ git+https://github.com/CoolNamesAllTaken/boarddd@vX.Y.Z#subdirectory=python"
# the SVG renderers are stdlib; [render] adds cairosvg (needs the system libcairo2), pillow and numpy for PNG
```

| module | draws | input | source |
|---|---|---|---|
| `render.footprint` | a footprint per layer and combined (KiCad colours), pad numbers, holes; `geom_json` for the 3D viewer | `Footprint(load("x.kicad_mod"))` (= `io.kicad.KicadFootprint` + kipr's statistics) | kipr `library/render/fp.py` render half, **byte for byte** |
| `render.symbol` | a symbol per unit; two revisions in one shared layout | `io.kicad.Symbol` | kipr `library/render/sym.py` render half, **byte for byte** |
| `render.geom` | the SVG helpers both use (text, arcs, boxes) | | kipr `library/render/geom.py` |
| `render.drawing` | the review drawing of one footprint (pads, numbers, paste, courtyard, fab body, origin, pitch, pin 1) and of two versions overlaid (`diff_svg`, `footprint_diff`) | `model.Footprint` (e.g. `read_kicad_mod`, `Board.footprints`) | magpie `footprint/svg.py`, onto boarddd's model |
| `render.board` | a board from its model, one side: outline with cut-outs, every placed footprint's copper, drills; the bottom seen from below; per-component `<a>` with state classes, links, tooltips, a highlight (a review board map) | `model.Board` | magpie `django/boardmap.py` layout half |
| `render.png` | `svg_to_png` (cairosvg; `var(--x, fallback)` resolved first), `diff_png` (green added, red removed, amber changed, transparent elsewhere) | SVG text, PNGs | kipr `Renderer.png` / `diff_png` |
| `render.svg` | the SVG entry points gathered | | |

```python
from boarddd.io.kicad import read_kicad_mod
from boarddd.io.kicad.sexpr import load
from boarddd.render import footprint as fp, symbol as sym
from boarddd.render.drawing import footprint_svg, diff_svg
from boarddd.render.board import board_svg
from boarddd.render.png import svg_to_png, diff_png

f = fp.Footprint(load("R_0603.kicad_mod"))
vb = fp.viewbox(fp.footprint_bbox(f))
combined, per_layer = fp.render_footprint(f, vb, fp.px_scale(vb))   # kipr's library review images
svg_to_png(combined, "r0603.png", css_vars=False)                  # exactly kipr's PNG

footprint_svg(read_kicad_mod("R_0603.kicad_mod"), width=400, pin1="1")
board_svg(board, "bottom", states={"U1": "approved"}, links={"U1": "/parts/U1"}, highlight="C3")
```

Frames: `render.footprint`/`symbol` draw in KiCad's own coordinates (y down; symbols flipped from their y-up
library frame), viewBox in mm. `render.drawing` draws y up in pixels (a group scaled from mm). `render.board`
uses the board frame (mm, y up), viewBox in mm from the outline's corner, with a 2 mm margin.

Theming: `drawing` and `board` colours are CSS variables with fallbacks (`--fp-*`, `--bm-*`), so a page themes
an inline SVG; `png` rasterises the fallbacks. Inside `drawing`'s mm-scaled group, stroke widths and dashes are
written in mm (`scaled_style`) as well as with `vector-effect:non-scaling-stroke` for the legend, so browsers and
cairosvg (which ignores `non-scaling-stroke`) draw the same lines.

## Goldens

| check | reference | how compared | test |
|---|---|---|---|
| footprints: 13 (kipr's kicad-libs parts, the pad-placement and pad-shape sets, two stock footprints) | kipr's own `render_footprint`, `geom_json`, `stats` at kipr `f632b3f` (`fixtures/kipr/make_golden.py`) | **bytes**: combined SVG, every per-layer SVG, viewBox, scale, geom.json, statistics | `test_render_kipr_goldens.py` |
| symbols: 12 (kipr's fixture + an excerpt of KiCad's stock libraries: units, De Morgan, `extends`, power) and one two-revision shared layout | kipr's `render_symbol` / `shared_layout` | **bytes** | same |
| review drawings of 12 footprints | magpie's `footprint_svg` (internal `claud/magpie` `3a0374d3`, `fixtures/magpie/make_golden.py`) | **pixels**: both rasterised; pad-colour IoU ≥ 0.96 and under 1.5 % of pixels differing (antialiasing of thin lines) | `test_render_drawing.py` |
| royalblue54L top side | KiCad's own SVG plot of F.Mask (mask clearance 0: the pad openings) | **pixels**: 98 % of our pad pixels inside KiCad's openings (the rest: a jumper pad without a mask opening) | `test_render_board.py` |

Where boarddd and magpie differ, boarddd follows pcbnew (checked in `tests/kicad`): magpie drops an SMD pad's
shape offset (the castellated RP2040 module's pads sit 0.65 mm too far in), draws holes unrotated at the copper's
centre (slotted holes), and draws courtyard and fab arcs as their three defining points. Those footprints are
held to a weaker bound and named in the test. magpie's version diff relies on its footprint identity (canonical
frames, turn and pin-1 evidence), which stays in magpie; boarddd's `footprint_diff` pairs pads by number, then by
position, and is tested on edits it should report.

## 3D previews

kipr's `library/render/model3d.py` (a footprint's GLB with cascadio and a numpy z-buffer preview PNG) stays in
kipr for now: decision D8 keeps cascadio as kipr's lightweight GLB path, and boarddd's OpenCascade engine is the
`[step]` extra (`step.split` exports per-part GLB; `step.hlr` draws line drawings). Folding the preview in means
choosing one of the two backends for it, a later step once kipr's CI image can take OCP.
