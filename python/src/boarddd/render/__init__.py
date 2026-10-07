"""Server-side renderers: SVG from KiCad files and the board model, PNG with the ``[render]`` extra.

- ``footprint``: KiCad footprints per layer and combined, and the 3D viewer's geom.json (kipr's renderer,
  byte for byte);
- ``symbol``: KiCad symbols per unit, one shared layout for two revisions (kipr's renderer);
- ``drawing``: review drawings of a footprint and of two versions overlaid (magpie's);
- ``board``: a board from its model, one side, as a thumbnail or a review board map;
- ``png``: SVG to PNG (cairosvg) and the pixel diff of two renders: ``pip install "boarddd[render]"``.

``svg`` gathers the SVG entry points. See docs/render.md.
"""
