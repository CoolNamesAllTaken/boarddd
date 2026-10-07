"""The splitter's pick-and-place reading and fit: boarddd.io.pos + boarddd.step.registration."""

import math

import pytest
from stepkit import TINY_POS, require_occ

require_occ()

from boarddd.step.split import fit_pos, parse_pos  # noqa: E402

ASCII_POS = """\
### Footprint positions - created on 2026-01-01 ###
## Unit = mm, Angle = deg.
## Side : All
# Ref     Val       Package              PosX       PosY       Rot  Side
C1        100n      C_0402_1005Metric    10.0000    -5.0000    90.0000  top
R7        10k       R_0402_1005Metric    12.5000    -6.0000   180.0000  bottom
## End
"""

ALTIUM_POS = """Altium Designer Pick and Place Locations
C:\\\\Users\\\\someone\\\\Pick Place\\\\PNP-Board.csv

========================================================================================================================
File Design Information:

Date:       07/06/24
Units used: mm

"Layer","Designator","Center-X(mm)","Center-Y(mm)","Rotation","Comment","ComponentKind","Description","Footprint","Footprint Description"
"TopLayer","C1","10.2000","15.9620","270","GRM216R61E105KA12D","Standard","","CAPC2013X70X45NL10T25","Chip Capacitor"
"BottomLayer","R7","12.5000","-6.0000","180","10k","Standard","","RESC1005X40N",""
"""


def test_parse_kicad_csv():
    placements = parse_pos(TINY_POS.read_text())
    assert len(placements) == 11
    assert placements["R5"].side == "bottom" and placements["R5"].rot == 150.0
    assert (placements["R1"].x, placements["R1"].y) == (5.0, -5.0)


def test_parse_kicad_ascii_and_inches():
    placements = parse_pos(ASCII_POS)
    assert set(placements) == {"C1", "R7"}
    assert placements["R7"].side == "bottom" and placements["C1"].rot == 90.0
    inches = parse_pos(ASCII_POS.replace("Unit = mm", "Unit = inches"))
    assert inches["C1"].x == pytest.approx(254.0)


def test_parse_altium():
    placements = parse_pos(ALTIUM_POS)
    assert set(placements) == {"C1", "R7"}
    assert (placements["C1"].x, placements["C1"].y, placements["C1"].rot) == (10.2, 15.962, 270.0)
    assert placements["R7"].side == "bottom" and placements["C1"].package == "CAPC2013X70X45NL10T25"


def test_fit_recovers_shift_and_turn_and_trims_offsets():
    placements = parse_pos(TINY_POS.read_text())
    angle = math.radians(90)

    def moved(x, y):
        return (x * math.cos(angle) - y * math.sin(angle) + 60.0, x * math.sin(angle) + y * math.cos(angle) - 54.0)

    step = {ref: moved(p.x, p.y) for ref, p in placements.items()}
    step["Q1"] = (step["Q1"][0] + 3.0, step["Q1"][1])  # a model drawn off its anchor
    result = fit_pos(step, placements)
    assert result.ok and result.rot_deg == 90.0 and not result.flip and result.method == "designators"
    assert (result.dx, result.dy) == pytest.approx((60.0, -54.0), abs=1e-9)
    assert result.residual_mm < 1e-9
    assert [ref for ref, _miss in result.elsewhere] == ["Q1"]
    assert result.angle(30.0) == pytest.approx(120.0)


def test_fit_with_nothing_shared():
    assert not fit_pos({"X1": (0.0, 0.0)}, parse_pos(TINY_POS.read_text())).ok
