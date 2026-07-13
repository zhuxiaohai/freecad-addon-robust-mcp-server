"""Pure helpers for HistCAD sketch constraint adaptation in FreeCAD.

These functions are embedded into FreeCAD-executed code by ``fabrication.py``
and are unit-tested directly so directed Distance sign logic stays correct when
dimension values are edited (editability benchmark).
"""

from __future__ import annotations

import math
from typing import Any


def ground_truth_xy(
    ground_truth: dict[str, Any] | None,
    ref: str,
) -> list[float] | None:
    """Return ``[x, y]`` for a HistCAD point reference from cached sketch JSON.

    Args:
        ground_truth: Sketch entity dict keyed by HistCAD names (``line_1``, …).
        ref: Point reference such as ``line_4.start`` or ``arc_1.center``.

    Returns:
        Coordinate pair, or ``None`` when the reference cannot be resolved.
    """
    if not ground_truth or "." not in str(ref):
        return None
    name, point = str(ref).split(".", 1)
    spec = ground_truth.get(name)
    if not isinstance(spec, dict):
        return None
    if point == "start":
        coords = spec.get("start")
    elif point == "end":
        coords = spec.get("end")
    elif point in ("center", "middle"):
        coords = spec.get("center") or spec.get("middle")
    else:
        return None
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    return [float(coords[0]), float(coords[1])]


def directed_axis_distance(
    ref_a: str,
    ref_b: str,
    axis_index: int,
    magnitude: float,
    ground_truth: dict[str, Any] | None,
) -> float:
    """Compute signed FreeCAD ``DistanceX``/``DistanceY`` value for a directed dim.

    FreeCAD directed distances use ``coord(ref_b) - coord(ref_a)`` on the axis.
    HistCAD ``Distance`` entries store an unsigned ``length`` plus a
    ``direction`` (``HORIZONTAL`` / ``VERTICAL``).  The sign must come from the
    sketch topology (ground-truth coordinates at creation), while ``magnitude``
    may change during editability edits.

    Args:
        ref_a: First point reference (FreeCAD first argument).
        ref_b: Second point reference (FreeCAD second argument).
        axis_index: ``0`` for X (``DistanceX``), ``1`` for Y (``DistanceY``).
        magnitude: Target distance in mm (may be edited; sign is ignored).
        ground_truth: Cached input sketch coordinates.

    Returns:
        Signed distance value for ``Sketcher.Constraint``.
    """
    mag = abs(float(magnitude))
    pa = ground_truth_xy(ground_truth, ref_a)
    pb = ground_truth_xy(ground_truth, ref_b)
    if pa is None or pb is None:
        return mag
    delta = float(pb[axis_index]) - float(pa[axis_index])
    if abs(delta) < 1e-12:
        return mag
    return math.copysign(mag, delta)
