"""Cut KiCad_Stock_Excerpt.kicad_sym out of KiCad's stock symbol libraries (KICAD_SYMBOL_DIR, else
/usr/share/kicad/symbols): a few symbols that exercise the symbol renderer, with the parents derived ones need.

    python python/tests/render/fixtures/make_stock_symbols.py

Symbols are copied verbatim (source spans from boarddd.io.kicad.sexpr). KiCad's libraries are CC-BY-SA 4.0
with the KiCad libraries exception.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

from boarddd.io.kicad.sexpr import parse  # noqa: E402

LIBRARY = Path(os.environ.get("KICAD_SYMBOL_DIR", "/usr/share/kicad/symbols"))
WANTED = {
    "Device": ["R", "C_Polarized", "LED", "Q_NPN", "Crystal_GND2"],  # passives, a transistor, a 3-pin crystal
    "74xx": ["74LS00", "74LS04"],  # multi-unit with a power unit and De Morgan bodies
    "Amplifier_Operational": ["LM358"],  # a derived symbol (extends) with units
    "power": ["GND", "+3V3"],  # power symbols, hidden pins
}


def main() -> None:
    pieces = []
    for lib, names in WANTED.items():
        text = (LIBRARY / f"{lib}.kicad_sym").read_text(encoding="utf-8")
        root = parse(text)
        by_name = {str(s.arg(0)): s for s in root.children("symbol")}
        todo, seen = list(names), []
        while todo:
            name = todo.pop(0)
            if name in seen:
                continue
            seen.append(name)
            parent = by_name[name].value("extends")
            if parent:
                todo.append(str(parent))
        for name in sorted(seen, key=lambda n: by_name[n].value("extends") is not None):
            node = by_name[name]
            pieces.append("\t" + text[node.start : node.end])
    out = '(kicad_symbol_lib\n\t(version 20241209)\n\t(generator "boarddd_fixture")\n' + "\n".join(pieces) + "\n)\n"
    (HERE / "KiCad_Stock_Excerpt.kicad_sym").write_text(out, encoding="utf-8")
    print(f"{len(pieces)} symbols")


if __name__ == "__main__":
    main()
