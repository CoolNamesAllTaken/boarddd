"""boarddd.render.png: the [render] extra's guard, CSS variables, rasterising, the review diff."""

import io
import subprocess
import sys
from pathlib import Path

import pytest

from boarddd.render.footprint import BACKGROUND


def test_needs_the_extra():
    code = (
        "import sys\n"
        "sys.modules['cairosvg'] = None\n"
        "try:\n"
        "    import boarddd.render.png\n"
        "except ImportError as error:\n"
        "    print(error)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert "[render] extra" in out and "pip install 'boarddd[render]'" in out
    # the SVG renderers are stdlib
    code = "import sys\nfor n in ('cairosvg', 'PIL', 'numpy'): sys.modules[n] = None\nimport boarddd.render.svg\nprint('ok')"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout == "ok\n"


def test_css_vars_resolve_to_their_fallbacks():
    from rendkit import require_render

    require_render()
    from boarddd.render.png import resolve_css_vars

    assert resolve_css_vars("fill:var(--a,#fff);stroke:var(--b, var(--c,#123))") == "fill:#fff;stroke:#123"
    assert resolve_css_vars("fill:var(--a,rgb(1,2,3))") == "fill:rgb(1,2,3)"


def test_kipr_render_rasterises_and_diffs(tmp_path):
    from rendkit import require_render

    require_render()
    from PIL import Image
    from rendkit import raster

    from boarddd.io.kicad.sexpr import load
    from boarddd.render import footprint as fpmod
    from boarddd.render.png import diff_png, svg_to_png

    src = Path(__file__).parent / "fixtures" / "kicad-libs" / "SOP-8_3.76x4.96mm_P1.27mm.kicad_mod"
    fp = fpmod.Footprint(load(src))
    vb = fpmod.viewbox(fpmod.footprint_bbox(fp))
    svg, _layers = fpmod.render_footprint(fp, vb, 40)
    data = svg_to_png(svg, tmp_path / "base.png", css_vars=False)
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and (tmp_path / "base.png").read_bytes() == data
    width, height = Image.open(io.BytesIO(data)).size
    assert (width, height) == pytest.approx(((vb[2] - vb[0]) * 40, (vb[3] - vb[1]) * 40), abs=1)
    assert tuple(raster(svg)[1, 1]) == tuple(int(BACKGROUND[i : i + 2], 16) for i in (1, 3, 5))
    moved = fpmod.Footprint(load(src))
    moved.pads[0]["x"] += 0.5
    svg_to_png(fpmod.render_footprint(moved, vb, 40)[0], tmp_path / "head.png", css_vars=False)
    stats = diff_png(tmp_path / "base.png", tmp_path / "head.png", tmp_path / "diff.png", BACKGROUND)
    assert stats["added_px"] > 100 and stats["removed_px"] > 100
    assert stats == diff_png(tmp_path / "base.png", tmp_path / "head.png", tmp_path / "diff2.png", BACKGROUND)
    assert Image.open(tmp_path / "diff.png").mode == "RGBA"
    same = diff_png(tmp_path / "base.png", tmp_path / "base.png", tmp_path / "none.png", BACKGROUND)
    assert same == {"added_px": 0, "removed_px": 0, "changed_px": 0}
