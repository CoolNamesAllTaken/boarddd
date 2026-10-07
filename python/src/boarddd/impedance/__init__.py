"""boarddd.impedance: PCB transmission-line impedance (docs/impedance.md).

Tier 1 is closed-form (`closedform`); the tier-2 field solver will live next to it. The JS twin is
`boarddd/impedance` (src/impedance/), checked against the same cases (fixtures/impedance/cases.json).
"""

from .closedform import (
    ETA0,
    MODELS,
    ComparisonResult,
    CoupledResult,
    Flag,
    LineResult,
    Result,
    Synthesis,
    calculate,
    coated_microstrip,
    coupled_microstrip,
    coupled_stripline,
    cpw,
    cpwg,
    elliptic_k,
    elliptic_ratio,
    ipc2141_coupled_microstrip,
    ipc2141_coupled_stripline,
    ipc2141_microstrip,
    ipc2141_stripline,
    microstrip,
    stripline,
    synthesize,
)

__all__ = [
    "ETA0",
    "MODELS",
    "ComparisonResult",
    "CoupledResult",
    "Flag",
    "LineResult",
    "Result",
    "Synthesis",
    "calculate",
    "coated_microstrip",
    "coupled_microstrip",
    "coupled_stripline",
    "cpw",
    "cpwg",
    "elliptic_k",
    "elliptic_ratio",
    "ipc2141_coupled_microstrip",
    "ipc2141_coupled_stripline",
    "ipc2141_microstrip",
    "ipc2141_stripline",
    "microstrip",
    "stripline",
    "synthesize",
]
