"""Regenerate the magpie goldens: magpie's own review drawing (`footprint/svg.footprint_svg`) of public footprints.

    python python/tests/render/fixtures/magpie/make_golden.py MAGPIE_SRC

MAGPIE_SRC: magpie's `src` directory (internal `claud/magpie` 3a0374d3), run with a Python that has magpie's dependencies (shapely). Each
footprint is read by magpie's own `.kicad_mod` reader and drawn 400 px wide; boarddd.render.drawing must draw
the same picture (test_render_drawing.py compares them as pixels).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, sys.argv[1])

from magpie.footprint.svg import footprint_svg  # noqa: E402
from magpie.pcb.kicad_pcb import read_footprint  # noqa: E402

FOOTPRINTS = sorted(
    [
        *(HERE.parent / "kicad-libs").glob("*.kicad_mod"),
        *(REPO / "python" / "tests" / "kicad" / "fixtures" / "pad_placement").glob("*.kicad_mod"),
        *(REPO / "test" / "fixtures" / "footprints").glob("*.kicad_mod"),
    ]
)

for path in FOOTPRINTS:
    fp = read_footprint(path.read_text(encoding="utf-8"), file=path.name)
    (HERE / f"{path.stem}.svg").write_text(footprint_svg(fp, width=400, title=path.stem) + "\n", encoding="utf-8")
print(f"{len(FOOTPRINTS)} drawings")
