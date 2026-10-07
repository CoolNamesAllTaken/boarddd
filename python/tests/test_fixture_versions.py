"""The generated fixtures (each make_* script with a --check in CI) must not depend on boarddd's version: a release
bumps the version, and a fixture that records it (a reader's "boarddd X.Y.Z io.kicad") fails its --check. Every
make_* script's output is built here with the readers reporting a fake version, which must not appear in it."""

from __future__ import annotations

import gzip
import importlib.util

import pytest

from boarddd.io import gerber_copper
from boarddd.io.kicad import pcb

from conftest import FIXTURES

FAKE = "9.99.999"
SCRIPTS = {
    "royalblue54L_feather/make_board.py": lambda mod: mod.build().to_json(),
    "royalblue54L_nfc_antenna/make_copper.py": lambda mod: mod.build().to_json(),
    "generated/bom/make_bom.py": lambda mod: mod.build(),
    "impedance/route/make_route_boards.py": lambda mod: "\n".join(
        (gzip.decompress(data) if name.endswith(".gz") else data).decode() for name, data in mod.outputs().items()
    ),
}


def load(rel: str):
    spec = importlib.util.spec_from_file_location(f"fixture_{rel.replace('/', '_')[:-3]}", FIXTURES / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("rel", SCRIPTS, ids=lambda r: r.split("/")[-1])
def test_generated_fixture_has_no_version(rel, monkeypatch):
    for module in (pcb, gerber_copper):
        monkeypatch.setattr(module, "__version__", FAKE)
    assert pcb.read_kicad_pcb(
        FIXTURES / "royalblue54L_nfc_antenna/kicad/RoyalBlue54L-NFC-Antenna.kicad_pcb"
    ).source.reader.startswith(f"boarddd {FAKE}")
    text = SCRIPTS[rel](load(rel))
    assert FAKE not in text
