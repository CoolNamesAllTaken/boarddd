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
```

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
