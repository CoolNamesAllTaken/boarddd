"""Layer ids and stacking order shared by the package, IPC-2581 and ODB++ readers (boarddd's own).

Ids are KiCad's canonical names where they apply ('F.Cu', 'In2.Cu', 'B.Mask', 'Edge.Cuts', 'PTH');
`order` puts copper 1..N top to bottom, then outline, paste, silk, mask at N+1..N+4, plated drills
at N+7, non-plated at N+8, then fab, courtyard, adhesive and user layers (docs/readers.md).
"""

from __future__ import annotations

__all__ = ["layer_id", "layer_order", "sort_key"]

_DRAW_ORDER = ["outline", "paste", "silk", "mask", "copper", "drill"]
_AFTER_DRILLS = ["PTH", "NPTH", "fab", "user", "courtyard", "adhesive", "other"]
_KICAD_PREFIX = {"top": "F", "bottom": "B"}
_KICAD_SUFFIX = {
    "mask": "Mask",
    "paste": "Paste",
    "silk": "Silkscreen",
    "fab": "Fab",
    "courtyard": "Courtyard",
    "adhesive": "Adhesive",
}


def layer_id(role: str, side: str, copper: int | None, count: int | None, *, plated: bool | None = None) -> str:
    """KiCad's canonical layer name for a layer, where there is one ('F.Cu', 'In2.Cu', 'B.Mask', 'Edge.Cuts', 'PTH')."""
    if role == "copper":
        if copper == 1 or (copper is None and side == "top"):
            return "F.Cu"
        if (count and copper == count) or (copper is None and side == "bottom"):
            return "B.Cu"
        if copper:
            return f"In{copper - 1}.Cu"
        return "Cu"
    if role == "outline":
        return "Edge.Cuts"
    if role == "drill":
        return "PTH" if plated else "NPTH" if plated is False else "Drill"
    if role in _KICAD_SUFFIX and side in _KICAD_PREFIX:
        return f"{_KICAD_PREFIX[side]}.{_KICAD_SUFFIX[role]}"
    return role


def layer_order(role: str, side: str, copper: int | None, count: int | None, *, plated: bool | None = None) -> int:
    """The Layer.order of a layer (see the module docstring)."""
    n = count or 0
    if role == "copper":
        return copper or (1 if side == "top" else n or 1)
    if role == "drill":
        return n + 1 + len(_DRAW_ORDER) + _AFTER_DRILLS.index("NPTH" if plated is False else "PTH")
    if role in _DRAW_ORDER:
        return n + 1 + _DRAW_ORDER.index(role)
    return n + 1 + len(_DRAW_ORDER) + _AFTER_DRILLS.index(role if role in _AFTER_DRILLS else "other")


def sort_key(layer):
    """Layers sort by order, top before bottom, then id."""
    return (layer.order, layer.side != "top", layer.id)
