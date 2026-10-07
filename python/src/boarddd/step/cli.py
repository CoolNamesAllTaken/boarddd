"""
Command line: split a board STEP into per-component STEP and GLB files plus measurements.json.

    python -m boarddd.step.cli split board.step [--pos pos.csv] --out DIR [--no-step] [--no-glb]
                                [--cache DIR] [--workers N]

Files are named after the designator. Components sharing a model get identical files; the
model is exported once and written under each designator. Needs the [step] extra.

Source: magpie `step/cli.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4), its `split` command.
"""

from __future__ import annotations

import argparse
import json
import re
import resource
import sys
import time
from pathlib import Path

__all__ = ["main"]


def _filename(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.+-]", "_", ref) or "_"


def _split(args) -> int:
    from .split import split

    started = time.perf_counter()
    assembly = split(args.step, pos=args.pos, cache=args.cache, workers=args.workers)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    export_started = time.perf_counter()
    failures: dict[str, str] = {}
    for ref, component in assembly.components.items():
        if component.measurements is None:
            continue
        name = _filename(ref)
        for kind, enabled, method in (
            ("step", args.step_files, component.step_bytes),
            ("glb", args.glb_files, component.glb_bytes),
        ):
            if not enabled:
                continue
            try:
                (out / f"{name}.{kind}").write_bytes(method())
            except Exception as error:  # one bad model must not lose the board
                failures[f"{ref}.{kind}"] = str(error)
    assembly.timings["export"] = time.perf_counter() - export_started

    report = assembly.to_dict()
    report["source"] = str(args.step)
    report["export_failures"] = failures
    report["stats"] = {
        "components": len(assembly.components),
        "models": len(assembly.models),
        "read_from": assembly.read_from,
        "cache": assembly.stats,
        "seconds": round(time.perf_counter() - started, 3),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }
    (out / "measurements.json").write_text(json.dumps(report, indent=1))
    stats = report["stats"]
    print(
        f"{stats['components']} components, {stats['models']} distinct models, "
        f"{stats['seconds']} s, peak RSS {stats['peak_rss_mb']} MB -> {out}"
    )
    if assembly.pos_fit is not None:
        fit = assembly.pos_fit
        print(
            f"pick-and-place fit: {'ok' if fit.ok else 'FAILED'}, residual {fit.residual_mm:.4f} mm "
            f"over {fit.matched}, {len(fit.elsewhere)} modeled away from their anchor, "
            f"{len(assembly.missing_models)} without a 3D model"
        )
    for warning in assembly.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for name, error in failures.items():
        print(f"export failed: {name}: {error}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m boarddd.step.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("split", help="split a board STEP into components")
    command.add_argument("step", help="assembled-board STEP file")
    command.add_argument("--pos", help="pick-and-place file (KiCad CSV or .pos)")
    command.add_argument("--out", required=True, help="output directory")
    command.add_argument("--no-step", dest="step_files", action="store_false", help="skip per-component STEP files")
    command.add_argument("--no-glb", dest="glb_files", action="store_false", help="skip per-component GLB files")
    command.add_argument("--cache", help="model cache directory (default: $BOARDDD_STEP_CACHE, else none)")
    command.add_argument("--workers", type=int, help="worker processes (default: $BOARDDD_STEP_WORKERS, else 3)")
    args = parser.parse_args(argv)
    if args.command == "split":
        return _split(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
