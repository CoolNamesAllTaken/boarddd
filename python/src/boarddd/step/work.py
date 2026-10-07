"""
Working out one model, in a process of its own: what the splitter farms out per product.

OpenCascade is not thread-safe, so models are worked out in a process pool, each task a
product cut out of the board (`index.carve`) with where it sits in its footprint. A task
reads only its own small STEP, so memory is bounded by the biggest model and not the board.

Everything is worked out in the seat frame (the component frame with z from the seat), which
is the same on every board; the splitter lifts it onto each board's seat. The same function
runs inline when there is one task, or `workers` is 1.

Besides measurements and fingerprint, a task makes what `EAGER` names (the exported STEP and
GLB, the drawing) and runs the registered hooks (`register_hook`): an application that wants
more from each model while its solid is loaded (magpie's footprint check takes the contacts)
registers a function of the `Model` returning JSON-able data, and finds the result in
`Model.extras[name]` and, with a cache, in the entry's `<name>.json`. Hooks reach the workers
by fork (the default on Linux): registering one restarts the pool.

Settings: `BOARDDD_STEP_WORKERS` (or magpie's `MAGPIE_STEP_WORKERS`; default 3), overridden by
the `workers=` argument of `split`.

Source: magpie `step/work.py` at internal `claud/magpie` `3a0374d3` (boarddd phase F4); magpie's
built-in footprint-check contacts became a hook.
"""

from __future__ import annotations

import atexit
import multiprocessing
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

from ._extra import require, setting

require(__name__)

__all__ = ["compute", "run", "workers_setting", "register_hook", "HOOKS", "WORKERS_ENV", "EAGER"]

WORKERS_ENV = "BOARDDD_STEP_WORKERS"
#: What a task works out besides measurements and fingerprint.
EAGER = ("step", "glb", "drawing")
#: Per-model extras: name -> function(Model) -> JSON-able data (see the module docstring).
HOOKS: dict[str, Callable] = {}


def register_hook(name: str, function: Callable) -> None:
    """Run `function(model)` on every model a split works out; its result lands in `Model.extras[name]`."""
    global _POOL
    if name in EAGER or name in ("measurements", "fingerprint"):
        raise ValueError(f"{name!r} is taken")
    HOOKS[name] = function
    if _POOL is not None:  # workers forked before this have not got it: start afresh
        _POOL[1].shutdown(wait=True)
        _POOL = None


_POOL: tuple[int, ProcessPoolExecutor] | None = None


def workers_setting(value: int | None = None) -> int:
    if value is not None:
        return max(1, int(value))
    try:
        return max(1, int(setting("STEP_WORKERS", "3")))
    except ValueError:
        return 3


def compute(task: dict) -> dict:
    """
    `task`: {'step': the product's STEP bytes, 'local': its 4x4 seat-frame placement as nested
    lists, 'product': its name, 'eager': which of EAGER to make}. Returns plain data.
    """
    import numpy as np

    from . import occ
    from .split import Model

    occ.load()
    document = occ.read(task["step"])
    roots = occ.free_shapes(document)
    if not roots:
        return {"measurements": None, "fingerprint": None}
    root = max(roots, key=lambda label: len(occ.components(label)))
    model = Model(None, "", root, task["product"], np.array(task["local"], dtype=float), 0.0)
    model._document = document  # keeps the labels alive
    result = {
        "measurements": model.measurements.to_dict() if model.measurements else None,
        "fingerprint": model.fingerprint.to_dict() if model.fingerprint else None,
    }
    if model.measurements is None:
        return result
    eager = task.get("eager", EAGER + tuple(HOOKS))
    if "step" in eager:
        result["step"] = model.step_bytes()
    if "glb" in eager:
        result["glb"] = model.glb_bytes()
    for name, hook in HOOKS.items():
        if name in eager:
            result[name] = hook(model)
    if "drawing" in eager:
        # Drawings are in the component frame, so on this board's seat: the same solid,
        # lifted, with the measurements just made.
        from . import hlr

        seat = float(task.get("seat", 0.0))
        lifted_local = np.array(task["local"], dtype=float)
        lifted_local[2, 3] += seat
        lifted = Model(
            None,
            "",
            root,
            task["product"],
            lifted_local,
            seat,
            computed={"measurements": result["measurements"], "fingerprint": result["fingerprint"]},
        )
        result["drawing"] = hlr.drawing(lifted)
    return result


def _initialize() -> None:
    from . import occ

    occ.load()


def _pool(workers: int) -> ProcessPoolExecutor:
    """One pool per process, kept between boards (starting OCP costs a second per worker)."""
    global _POOL
    if _POOL is not None and _POOL[0] == workers:
        return _POOL[1]
    if _POOL is not None:
        _POOL[1].shutdown(wait=True)
    # Fork where there is fork: a forked worker needs no import of the caller's main module
    # (spawn re-runs an unguarded script in every worker) and starts with OCP already loaded.
    # Nothing here runs OCCT threads (meshing is serial), so forking a process that has loaded
    # it is safe.
    method = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context(method), initializer=_initialize)
    _POOL = (workers, pool)
    return pool


@atexit.register
def _shutdown() -> None:
    global _POOL
    if _POOL is not None:
        _POOL[1].shutdown(wait=False, cancel_futures=True)
        _POOL = None


def run(tasks: list[dict], workers: int) -> list[dict]:
    """Work out every task, in parallel when it pays; results in the tasks' order."""
    if workers <= 1 or len(tasks) <= 1:
        return [compute(task) for task in tasks]
    from concurrent.futures.process import BrokenProcessPool

    pool = _pool(workers)
    # Biggest first, so the long ones do not start last.
    order = sorted(range(len(tasks)), key=lambda i: -len(tasks[i]["step"]))
    try:
        futures = {i: pool.submit(compute, tasks[i]) for i in order}
        return [futures[i].result() for i in range(len(tasks))]
    except BrokenProcessPool:
        # A worker died (out of memory, a crash in OCCT): drop the pool, work inline instead.
        global _POOL
        _POOL = None
        return [compute(task) for task in tasks]
