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
```

Readers: `boarddd.io` (archive, classify, gerber, pads, excellon, outline, gbrjob, pos, bom, package),
`boarddd.io.kicad` (pcb, footprint, project, symbol, sexpr) and
`boarddd.step` (text, slim, modelfile, registration), stdlib only; `pip install "boarddd[xlsx] @ …"` for
`.xlsx`/`.xls` BOMs. See [docs/readers.md](../docs/readers.md).

`pip install "boarddd[step] @ …"` adds the OpenCascade STEP engine (cadquery-ocp, LGPL, ~440 MB, never vendored):
`boarddd.step.split` splits a board STEP into measured, fingerprinted components with per-part STEP/GLB, and
`python -m boarddd.step.cli split board.step --pos pos.csv --out DIR`. See [docs/step.md](../docs/step.md).

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
