import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"


def load(rel: str):
    return json.loads((FIXTURES / rel).read_text("utf-8"))


def apply_patch(doc, patch):
    """[["set"|"delete", "/json/pointer", value?], ...] -> a patched copy (the format of fixtures/model/cases.json)."""
    doc = json.loads(json.dumps(doc))
    for op, path, *value in patch:
        *parents, last = path.split("/")[1:]
        node = doc
        for key in parents:
            node = node[int(key)] if isinstance(node, list) else node[key]
        if isinstance(node, list):
            last = int(last)
        if op == "set":
            node[last] = value[0]
        else:
            del node[last]
    return doc


@pytest.fixture(scope="session")
def golden():
    return load("royalblue54L_feather/board.json")
