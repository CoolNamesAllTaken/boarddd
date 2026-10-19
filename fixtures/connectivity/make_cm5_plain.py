"""CM5 MINIMA (fixtures/cm5_minima) exported to plain Gerbers (no X2, no netlist attributes, no job file; the drill
files as kicad-cli writes them, PTH and NPTH apart, which say which holes are plated and which are vias) and read back
with boarddd.connectivity: the nets from the copper alone.

    python fixtures/connectivity/make_cm5_plain.py [--kicad-cli PATH]   # rewrites cm5-plain.json.gz

{"board": board@1 (the KiCad file's stackup; no nets, no footprints), "copper": copper@1 (connectivity's),
"connectivity": its summary, "accuracy": its nets against the KiCad file's (compare_nets)}. The layer view's example
and browser test read it (examples/layers.html, test/impedance-ui/layers.spec.mjs). Needs kicad-cli (10.x) and
shapely.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "python" / "src"))

from boarddd import model as m  # noqa: E402
from boarddd.connectivity import compare_nets, connect_files  # noqa: E402
from boarddd.io.kicad import read_kicad_copper, read_kicad_pcb  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kicad-cli", default="kicad-cli")
    args = ap.parse_args()
    src = REPO / "fixtures" / "cm5_minima" / "kicad" / "CM5_MINIMA_3.kicad_pcb.gz"
    with tempfile.TemporaryDirectory() as tmp:
        pcb = Path(tmp) / "CM5_MINIMA_3.kicad_pcb"
        pcb.write_bytes(gzip.decompress(src.read_bytes()))
        text = pcb.read_text()
        cu = re.findall(r'\(\d+ "([^"]+\.Cu)"', text)
        out = Path(tmp) / "plain"
        out.mkdir()
        cli = args.kicad_cli
        subprocess.run(
            [
                cli,
                "pcb",
                "export",
                "gerbers",
                "--no-x2",
                "--no-netlist",
                "--check-zones",
                "-l",
                ",".join([*cu, "Edge.Cuts", "F.Mask", "B.Mask"]),
                "-o",
                f"{out}/",
                str(pcb),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run([cli, "pcb", "export", "drill", "-o", f"{out}/", str(pcb)], check=True, capture_output=True)
        files = {}
        for p in sorted(out.iterdir()):
            if p.suffix == ".gbrjob":
                continue
            files[p.name] = p.read_bytes()
        doc, conn = connect_files(files, name="CM5_MINIMA_3")
        ref_board = read_kicad_pcb(pcb)
        ref = read_kicad_copper(pcb)
    truth = {
        (p.at[0], p.at[1], lid): p.net or f"~{p.ref}.{p.number}@{p.at}"
        for p in ref.pads
        for lid in p.layers
        if lid in doc.layers
    }
    acc = compare_nets(truth, doc)
    board = m.Board(
        name="CM5_MINIMA_3 (plain Gerbers)",
        source=ref_board.source,
        layers=ref_board.layers,
        stackup=ref_board.stackup,
        outline=ref_board.outline,
    )
    data = {"board": board.to_dict(), "copper": doc.to_dict(), "connectivity": conn.to_json(), "accuracy": acc}
    data["connectivity"]["timings"] = {}
    blob = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    (HERE / "cm5-plain.json.gz").write_bytes(gzip.compress(blob, mtime=0))
    print(
        f"cm5-plain.json.gz: {len(conn.nets)} nets, {len(doc.tracks)} tracks, accuracy {acc['pct']} % ({acc['matched']}/{acc['nets']})"
    )


if __name__ == "__main__":
    main()
