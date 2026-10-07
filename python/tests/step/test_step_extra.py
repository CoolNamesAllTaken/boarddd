"""The [step] extra is optional: its modules say so when it is missing, the stdlib ones never need it."""

import subprocess
import sys

import pytest

from boarddd.step import _extra, registration

OCP_MODULES = ["occ", "split", "work", "measure", "fingerprint", "hlr", "index"]


def test_require_names_the_extra():
    with pytest.raises(ImportError, match=r"needs the \[step\] extra .*pip install 'boarddd\[step\]'"):
        _extra.require("boarddd.step.demo", ("no_such_package_boarddd",))


@pytest.mark.parametrize("module", OCP_MODULES)
def test_ocp_modules_fail_clearly_without_their_packages(module):
    """numpy, shapely and OCP hidden: every engine module refuses with the install line."""
    code = (
        "import sys\n"
        "for name in ('numpy', 'shapely', 'OCP'): sys.modules[name] = None\n"
        "try:\n"
        f"    import boarddd.step.{module}\n"
        "except ImportError as error:\n"
        "    print(error)\n"
        "else:\n"
        "    print('imported')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert "[step] extra" in out and "pip install 'boarddd[step]'" in out, out


def test_stdlib_modules_import_without_the_extra():
    code = (
        "import sys\n"
        "for name in ('numpy', 'shapely', 'OCP'): sys.modules[name] = None\n"
        "import boarddd.step, boarddd.step.text, boarddd.step.slim, boarddd.step.modelfile\n"
        "import boarddd.step.registration, boarddd.step.cache\n"
        "print('ok')\n"
    )
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout == "ok\n"


def test_settings_read_boarddd_then_magpie_names(monkeypatch):
    monkeypatch.delenv("BOARDDD_STEP_WORKERS", raising=False)
    monkeypatch.setenv("MAGPIE_STEP_WORKERS", "5")
    assert _extra.setting("STEP_WORKERS") == "5"
    monkeypatch.setenv("BOARDDD_STEP_WORKERS", "2")
    assert _extra.setting("STEP_WORKERS") == "2"
    monkeypatch.delenv("BOARDDD_STEP_WORKERS")
    monkeypatch.delenv("MAGPIE_STEP_WORKERS")
    assert _extra.setting("STEP_WORKERS", "3") == "3"


def test_registration_outlier_floor():
    """The splitter's tighter floor sets aside a part modeled 0.5 mm off its anchor; the default keeps it."""
    placements = {f"R{i}": (float(i * 3), float((i * 7) % 11), "top") for i in range(12)}
    step = {ref: (x + 60.0, y - 54.0, "") for ref, (x, y, _side) in placements.items()}
    step["Q1"] = (60.0 + 5.5, -54.0 + 9.2, "")
    placements["Q1"] = (5.0, 9.0, "top")
    loose = registration.register(placements, step)
    tight = registration.register(placements, step, outlier_floor_mm=0.01)
    assert loose.elsewhere == [] and loose.residual_mm > 0.1
    assert [ref for ref, _mm in tight.elsewhere] == ["Q1"] and tight.residual_mm < 1e-9
    assert (tight.transform["dx"], tight.transform["dy"]) == pytest.approx((60.0, -54.0))
