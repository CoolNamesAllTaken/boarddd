"""The SVG entry points of ``boarddd.render`` in one place (stdlib only).

from boarddd.render.svg import Footprint, render_footprint, render_symbol, footprint_svg, diff_svg, board_svg
"""

from .board import board_svg
from .drawing import diff_svg, footprint_diff, footprint_svg
from .footprint import Footprint, footprint_bbox, geom_json, layer_elements, px_scale, render_footprint, viewbox
from .symbol import Symbol, parse_library, render_symbol, shared_layout, unit_layout

__all__ = [
    "Footprint",
    "render_footprint",
    "layer_elements",
    "footprint_bbox",
    "viewbox",
    "px_scale",
    "geom_json",
    "Symbol",
    "parse_library",
    "unit_layout",
    "shared_layout",
    "render_symbol",
    "footprint_svg",
    "diff_svg",
    "footprint_diff",
    "board_svg",
]
