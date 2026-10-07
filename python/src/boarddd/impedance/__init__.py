"""boarddd.impedance: PCB transmission-line impedance (docs/impedance.md).

Tier 1 is closed-form (`closedform`); tier 2 (`fieldsolver`, needs the ``field`` extra: numpy and scipy) is a 2D
field solver for any cross-section; `stackup` connects both to the board model's stackup and ImpedanceTarget. The
JS twin is `boarddd/impedance` (src/impedance/), checked against the same cases (fixtures/impedance/cases.json
and field-cases.json).
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
from .fieldsolver import FieldResult, field_calculate, section_for, solve_cross_section
from .stackup import STACKUP_DEFAULTS, Line, TargetEvaluation, evaluate_target, line_from_stackup, model_for

__all__ = [
    "ETA0",
    "FieldResult",
    "field_calculate",
    "section_for",
    "solve_cross_section",
    "STACKUP_DEFAULTS",
    "Line",
    "TargetEvaluation",
    "evaluate_target",
    "line_from_stackup",
    "model_for",
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
