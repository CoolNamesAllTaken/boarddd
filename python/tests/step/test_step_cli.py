"""`python -m boarddd.step.cli split` on the tiny fixture."""

from __future__ import annotations

import json

import pytest
from stepkit import require_occ

require_occ()

from stepkit import FIXTURES  # noqa: E402

from boarddd.step.cli import main  # noqa: E402


def test_cli_split_writes_files_and_measurements(tmp_path, capsys):
    assert (
        main(["split", str(FIXTURES / "tiny.step"), "--pos", str(FIXTURES / "tiny-pos.csv"), "--out", str(tmp_path)])
        == 0
    )
    report = json.loads((tmp_path / "measurements.json").read_text())
    assert len(report["components"]) == 11
    assert report["pos_fit"]["ok"] and report["board"]["name"] == "tiny_PCB"
    for ref in report["components"]:
        assert (tmp_path / f"{ref}.step").stat().st_size > 1000
        assert (tmp_path / f"{ref}.glb").read_bytes()[:4] == b"glTF"
    r1 = report["components"]["R1"]
    model = report["models"][r1["model"]]
    assert model["measurements"]["pick"]["at_origin"]["rect"] == pytest.approx([0.6, 0.5], abs=0.005)
    assert report["stats"]["peak_rss_mb"] > 0
    assert "11 components" in capsys.readouterr().out
