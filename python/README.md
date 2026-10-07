# boarddd (Python)

The server-side half of [boarddd](../README.md): readers turn fab packages and EDA files into one
normalised board model (`boarddd/board@1`, see [docs/model.md](../docs/model.md)); the browser-side JS
renders it. One repository, one version tag for both.

```bash
pip install "boarddd @ git+https://github.com/CoolNamesAllTaken/boarddd@vX.Y.Z#subdirectory=python"
```

```python
from boarddd.model import Board
from boarddd.validate import validate_board

board = Board.from_json(open("board.json").read())
errors = validate_board(json.load(open("board.json")))   # [] when valid

from boarddd.io.package import read_package
board = read_package("fab/")          # a fab package (folder or .zip) -> Board; see docs/readers.md

from boarddd.io.kicad import read_kicad_pcb
board = read_kicad_pcb("board.kicad_pcb")   # KiCad 5-10, no pcbnew; the .kicad_pro next to it adds net classes

from boarddd.impedance import microstrip, synthesize
microstrip(w=0.36, h=0.2104, t=0.035, er=4.4).Z0          # 50.92 Ω; see docs/impedance.md
from boarddd.impedance import field_calculate             # the field solver: pip install "boarddd[field] @ …"
field_calculate("coupled_microstrip", {"w": 0.15, "s": 0.15, "h": 0.2104, "t": 0.035, "er": 4.4}).Zdiff
```

Readers: `boarddd.io` (archive, classify, gerber, pads, excellon, outline, gbrjob, pos, bom, package),
`boarddd.io.kicad` (pcb, footprint, project, symbol, sexpr) and
`boarddd.step` (text, slim, modelfile, registration), stdlib only; `pip install "boarddd[xlsx] @ …"` for
`.xlsx`/`.xls` BOMs. See [docs/readers.md](../docs/readers.md).

`pip install "boarddd[step] @ …"` adds the OpenCascade STEP engine (cadquery-ocp, LGPL, ~440 MB, never vendored):
`boarddd.step.split` splits a board STEP into measured, fingerprinted components with per-part STEP/GLB, and
`python -m boarddd.step.cli split board.step --pos pos.csv --out DIR`. See [docs/step.md](../docs/step.md).

`boarddd.render` draws SVG: KiCad footprints and symbols (kipr's renderer, byte for byte), review drawings and
diffs of footprints, and a board from its model (one side, as a thumbnail or a review board map);
`pip install "boarddd[render] @ …"` adds PNG (cairosvg, pillow) and pixel diffs. See [docs/render.md](../docs/render.md).

Development (Python 3.11+):

```bash
cd python
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
python -m boarddd.model --check     # schema/board.schema.json and src/model/* are up to date
python -m boarddd.model --write     # regenerate them after changing model.py
```
