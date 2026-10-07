"""SVG to PNG, and the review diff of two PNGs: the optional ``[render]`` extra (cairosvg, pillow, numpy).

``svg_to_png`` rasterises with cairosvg (which needs the system cairo library, ``libcairo2``). cairosvg does not
evaluate CSS custom properties, so ``var(--name, fallback)`` is replaced by its fallback first: boarddd's themed
drawings (``drawing``, ``board``, ``step.hlr``) then rasterise in their default colours. ``diff_png`` marks what
changed between two renders of one thing (kipr's library review diff): green where ink appeared, red where it
went, amber where it changed colour, transparent elsewhere.

Importing this module without the extra raises an ImportError naming it.

Source: kipr ``kipr/library/render/main.py`` ``Renderer.png`` / ``diff_png`` at kipr main ``f632b3f``
(boarddd phase F5); the CSS-variable pass is new.
"""

from __future__ import annotations

import importlib.util
import os
import re

INSTALL = "pip install 'boarddd[render]'"
_gone = [name for name in ("cairosvg", "PIL", "numpy") if importlib.util.find_spec(name) is None]
if _gone:
    raise ImportError(
        f"{__name__} needs the [render] extra ({', '.join(_gone)} not installed): {INSTALL}", name=_gone[0]
    )

__all__ = ["svg_to_png", "diff_png", "resolve_css_vars"]

_VAR = re.compile(r"var\(\s*--[\w-]+\s*,\s*((?:[^()]|\([^()]*\))*?)\s*\)")


def resolve_css_vars(svg: str) -> str:
    """Every ``var(--x, fallback)`` replaced by its fallback (innermost first, so nested fallbacks resolve)."""
    previous = None
    while previous != svg:
        previous, svg = svg, _VAR.sub(lambda match: match.group(1), svg)
    return svg


def svg_to_png(
    svg: str,
    path: str | os.PathLike | None = None,
    *,
    scale: float = 1.0,
    width: int | None = None,
    background: str | None = None,
    css_vars: bool = True,
) -> bytes:
    """Rasterise an SVG document. Returns the PNG bytes (and writes them to `path` when given).

    `width` sets the output width in px (height follows the aspect); otherwise the SVG's own size times
    `scale`. `background` fills behind it ('#rrggbb'). `css_vars=False` keeps ``var()`` as it is (kipr's own
    SVGs use plain colours; the output is then exactly kipr's ``cairosvg.svg2png``).
    """
    import cairosvg

    text = resolve_css_vars(svg) if css_vars else svg
    data = cairosvg.svg2png(
        bytestring=text.encode("utf-8"), scale=scale, output_width=width, background_color=background
    )
    if path is not None:
        with open(path, "wb") as out:
            out.write(data)
    return data


def _rgb(colour: str):
    import numpy as np

    return np.array([int(colour[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.int16)


def diff_png(base_png, head_png, out_path, background: str, warnings: list[str] | None = None, paper=()) -> dict | None:
    """Mark the changed pixels of two same-size renders; returns pixel counts.

    `background`: the renders' background colour ('#rrggbb'); `paper`: colours that count as background too
    (a symbol's body fill). Inputs are paths or file objects. Output: RGBA PNG at `out_path`, transparent where
    nothing changed, so it can be laid over either render.
    """
    import numpy as np
    from PIL import Image

    warnings = warnings if warnings is not None else []
    a = np.asarray(Image.open(base_png).convert("RGB")).astype(np.int16)
    b = np.asarray(Image.open(head_png).convert("RGB")).astype(np.int16)
    if a.shape != b.shape:
        h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
        a, b = a[:h, :w], b[:h, :w]
        warnings.append("render: base/head PNG size mismatch; diff cropped")
    bg = _rgb(background)

    def ink(img):
        m = np.abs(img - bg).max(axis=2) > 24
        for pc in paper:  # e.g. symbol body fill: treat as background, not ink
            m &= np.abs(img - _rgb(pc)).max(axis=2) > 24
        return m

    ink_a, ink_b = ink(a), ink(b)
    changed = np.abs(a - b).max(axis=2) > 40
    out = np.zeros(a.shape[:2] + (4,), dtype=np.uint8)
    added = changed & ink_b & ~ink_a
    removed = changed & ink_a & ~ink_b
    both = changed & ink_a & ink_b
    out[added] = [0, 200, 60, 255]
    out[removed] = [230, 40, 40, 255]
    out[both] = [240, 170, 0, 255]
    Image.fromarray(out, "RGBA").save(out_path, optimize=True)
    return {"added_px": int(added.sum()), "removed_px": int(removed.sum()), "changed_px": int(both.sum())}
