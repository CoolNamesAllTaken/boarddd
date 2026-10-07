"""Shared bits of the [step] extra's tests: skip without the extra, and the public fixtures.

fixtures_occ/tiny.step: magpie's synthetic board (make_board.py) of KiCad stock footprints and models;
fixtures_occ/library/: three KiCad stock STEP models; fixtures/generated/step/royalblue54L_feather.step.gz:
KiCad's royalblue54L_feather demo exported with its stock models (fixtures/generated/step/make.sh).
"""

from __future__ import annotations

import gzip
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures_occ"
TINY = FIXTURES / "tiny.step"
TINY_POS = FIXTURES / "tiny-pos.csv"
LIBRARY = FIXTURES / "library"
REPO = HERE.parents[2]
ROYALBLUE_GZ = REPO / "fixtures" / "generated" / "step" / "royalblue54L_feather.step.gz"
ROYALBLUE_POS = REPO / "fixtures" / "royalblue54L_feather" / "fab" / "pos.csv"
#: Set in CI's step job: a missing extra (or node for the JS parity test) fails instead of skipping.
REQUIRED = bool(os.environ.get("BOARDDD_REQUIRE_STEP"))


def require_occ() -> None:
    """Skip the calling module unless the [step] extra is installed and OCP loads."""
    try:
        from boarddd.step import occ

        occ.load()
    except ImportError as error:  # no extra, or no libGL and no private copy
        if REQUIRED:
            raise
        pytest.skip(str(error), allow_module_level=True)


def royalblue(tmp_path_factory) -> Path:
    """The royalblue54L STEP, unpacked once per session."""
    out = tmp_path_factory.getbasetemp() / "royalblue54L_feather.step"
    if not out.exists():
        out.write_bytes(gzip.decompress(ROYALBLUE_GZ.read_bytes()))
    return out
