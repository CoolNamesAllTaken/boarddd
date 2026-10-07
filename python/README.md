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
```

Readers: `boarddd.io` (archive, classify, gerber, pads, excellon, outline, gbrjob, pos, bom, package) and
`boarddd.step` (text, slim, modelfile, registration), stdlib only; `pip install "boarddd[xlsx] @ …"` for
`.xlsx`/`.xls` BOMs. See [docs/readers.md](../docs/readers.md).

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
