"""The ``[step]`` extra: its dependencies, checked once, and the settings its modules read.

``boarddd.step``'s OCP modules (occ, split, work, measure, fingerprint, hlr, cache, index, cli) need
``pip install "boarddd[step]"``: cadquery-ocp (OpenCascade, LGPL-2.1, about 440 MB, never vendored), numpy
and shapely. Importing one without them raises an ImportError that names the extra; the stdlib modules
(text, slim, modelfile, registration) never need it.
"""

from __future__ import annotations

import importlib.util
import os

INSTALL = "pip install 'boarddd[step]'"
#: Import names of the extra's packages (OCP is cadquery-ocp's).
PACKAGES = ("OCP", "numpy", "shapely")


def missing() -> list[str]:
    """The extra's packages that are not installed."""
    return [name for name in PACKAGES if importlib.util.find_spec(name) is None]


def require(module: str, packages: tuple[str, ...] = PACKAGES) -> None:
    """Raise an ImportError naming the extra when one of `packages` is not installed."""
    gone = [name for name in packages if importlib.util.find_spec(name) is None]
    if gone:
        raise ImportError(f"{module} needs the [step] extra ({', '.join(gone)} not installed): {INSTALL}", name=gone[0])


def setting(name: str, default: str | None = None) -> str | None:
    """`BOARDDD_<name>` from the environment, else magpie's old `MAGPIE_<name>`, else `default`."""
    return os.environ.get(f"BOARDDD_{name}") or os.environ.get(f"MAGPIE_{name}") or default
