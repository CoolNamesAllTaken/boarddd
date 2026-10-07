"""Regenerate the kipr goldens: kipr's own footprint/symbol renderers on the fixtures, as JSON.

    python python/tests/render/fixtures/kipr/make_golden.py KIPR_SRC

KIPR_SRC: a kipr checkout (the source tree with `kipr/`), e.g. `git -C kipr archive f632b3f kipr | tar -x`.
Only `kipr.common.sexpr` and `kipr.library.render.{fp,sym,geom}` are imported (stdlib). Per footprint:
kipr's combined SVG and per-layer SVGs (`render_footprint` in the shared viewBox `viewbox(footprint_bbox)`
at `px_scale`), `geom_json` and `stats`; per symbol: `render_symbol` of every unit at kipr's library-review
scale, and `stats`. boarddd.render must reproduce them byte for byte (test_render_kipr_goldens.py).
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, sys.argv[1])

from kipr.common.sexpr import parse  # noqa: E402
from kipr.library.render import fp as fpmod  # noqa: E402
from kipr.library.render import sym as symmod  # noqa: E402

FOOTPRINTS = sorted(
    [
        *(HERE.parent / "kicad-libs").glob("*.kicad_mod"),
        *(REPO / "python" / "tests" / "kicad" / "fixtures" / "pad_placement").glob("*.kicad_mod"),
        *(REPO / "test" / "fixtures" / "pad_shapes").glob("*.kicad_mod"),
        *(REPO / "test" / "fixtures" / "footprints").glob("*.kicad_mod"),
    ]
)
SYMBOLS = sorted([*(HERE.parent / "kicad-libs").glob("*.kicad_sym"), *HERE.parent.glob("*.kicad_sym")])
#: Two revisions drawn in one shared layout (kipr's base/head view): a derived symbol and its parent.
PAIR = ("KiCad_Stock_Excerpt", "LM2904", "LM358")


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def footprint(path: Path) -> dict:
    fp = fpmod.Footprint(parse(path.read_text(encoding="utf-8")))
    vb = fpmod.viewbox(fpmod.footprint_bbox(fp))
    scale = fpmod.px_scale(vb)
    combined, layers = fpmod.render_footprint(fp, vb, scale)
    return {
        "source": rel(path),
        "viewbox": vb,
        "scale": scale,
        "combined": combined,
        "layers": layers,
        "geom": fpmod.geom_json(fp, vb),
        "stats": fpmod.Footprint(parse(path.read_text(encoding="utf-8"))).stats(),
    }


def symbols(path: Path) -> dict:
    root = parse(path.read_text(encoding="utf-8"))
    lib = symmod.parse_library(root)
    out = {}
    for name, node in lib.items():
        sym = symmod.Symbol(node, lib)
        layout = symmod.unit_layout(sym)
        slots, vb = symmod.shared_layout([layout])
        w, h = vb[2] - vb[0], vb[3] - vb[1]
        scale = min(80.0, 1600 / max(w, h, 1e-3))
        out[name] = {
            "viewbox": vb,
            "scale": scale,
            "svg": symmod.render_symbol(layout, slots, vb, scale, sym),
            "stats": sym.stats(),
        }
    return {"source": rel(path), "symbols": out}


def pair() -> dict:
    lib_name, a, b = PAIR
    root = parse((HERE.parent / f"{lib_name}.kicad_sym").read_text(encoding="utf-8"))
    lib = symmod.parse_library(root)
    syms = {name: symmod.Symbol(lib[name], lib) for name in (a, b)}
    layouts = {name: symmod.unit_layout(s) for name, s in syms.items()}
    slots, vb = symmod.shared_layout(list(layouts.values()))
    w, h = vb[2] - vb[0], vb[3] - vb[1]
    scale = min(80.0, 1600 / max(w, h, 1e-3))
    return {
        "viewbox": vb,
        "scale": scale,
        "slots": {str(k): v for k, v in slots.items()},
        "svg": {name: symmod.render_symbol(layouts[name], slots, vb, scale, s) for name, s in syms.items()},
    }


def main() -> None:
    golden = {
        "footprints": {p.stem: footprint(p) for p in FOOTPRINTS},
        "symbols": {p.stem: symbols(p) for p in SYMBOLS},
        "pair": pair(),
    }
    (HERE / "golden.json").write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"{len(golden['footprints'])} footprints, {sum(len(s['symbols']) for s in golden['symbols'].values())} symbols"
    )


if __name__ == "__main__":
    main()
