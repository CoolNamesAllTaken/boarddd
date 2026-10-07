"""Shared bits of the render tests: skip what needs the [render] extra (cairosvg, pillow, numpy) without it."""

from __future__ import annotations

import io
import os

import pytest

#: Set in CI's python job, which installs the extra: a missing extra fails instead of skipping.
REQUIRED = os.environ.get("BOARDDD_REQUIRE_RENDER", "") not in ("", "0")


def require_render() -> None:
    """Skip the calling module unless boarddd.render.png imports (the [render] extra and libcairo)."""
    try:
        from boarddd.render import png  # noqa: F401
    except (ImportError, OSError) as error:  # OSError: cairosvg without the system cairo library
        if REQUIRED:
            raise
        pytest.skip(str(error), allow_module_level=True)


def raster(svg: str, **options):
    """An SVG as an RGB numpy array (int), white background."""
    import numpy as np
    from PIL import Image

    from boarddd.render.png import svg_to_png

    data = svg_to_png(svg, background="#ffffff", **options)
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(int)
