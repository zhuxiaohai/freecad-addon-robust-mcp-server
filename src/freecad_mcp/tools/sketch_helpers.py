"""Pure helpers for HistCAD sketch constraint adaptation in FreeCAD.

These functions are embedded into FreeCAD-executed code by ``fabrication.py``
and are unit-tested directly so directed Distance sign logic stays correct when
dimension values are edited (editability benchmark).
"""

from __future__ import annotations

import math
from typing import Any

_EPS = 1e-12


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


def distance_polarity_key(ref_a: str, ref_b: str, direction: str) -> str:
    """Stable key for a directed Distance polarity entry.

    Args:
        ref_a: First HistCAD point reference.
        ref_b: Second HistCAD point reference.
        direction: ``HORIZONTAL`` or ``VERTICAL``.

    Returns:
        String key suitable for ``HistCADPolarities`` JSON storage.
    """
    return f"Distance:{ref_a}:{ref_b}:{str(direction).strip().upper()}"


def polarity_from_signed(signed_value: float) -> int:
    """Map a signed FreeCAD distance to stored polarity ``+1`` or ``-1``."""
    return -1 if float(signed_value) < 0.0 else 1


def directed_axis_distance(
    ref_a: str,
    ref_b: str,
    axis_index: int,
    magnitude: float,
    ground_truth: dict[str, Any] | None,
    *,
    polarity: int | None = None,
    live_a: list[float] | tuple[float, float] | None = None,
    live_b: list[float] | tuple[float, float] | None = None,
) -> float:
    """Compute signed FreeCAD ``DistanceX``/``DistanceY`` value for a directed dim.

    FreeCAD directed distances use ``coord(ref_b) - coord(ref_a)`` on the axis.
    HistCAD ``Distance`` entries store an unsigned ``length`` plus a
    ``direction`` (``HORIZONTAL`` / ``VERTICAL``).  Sign priority:

    1. Persisted ``polarity`` (``+1`` / ``-1``) from a prior successful apply.
    2. Ground-truth topology at sketch creation when ``|delta|`` is non-trivial.
    3. Live sketch endpoint coordinates (same fallback purpose).
    4. Unsigned ``+abs(magnitude)`` last resort.

    ``magnitude`` may change during editability edits; polarity must not flip.

    Args:
        ref_a: First point reference (FreeCAD first argument).
        ref_b: Second point reference (FreeCAD second argument).
        axis_index: ``0`` for X (``DistanceX``), ``1`` for Y (``DistanceY``).
        magnitude: Target distance in mm (may be edited; sign is ignored).
        ground_truth: Cached input sketch coordinates.
        polarity: Optional persisted sign ``+1`` or ``-1``.
        live_a: Optional live ``[x, y]`` for ``ref_a``.
        live_b: Optional live ``[x, y]`` for ``ref_b``.

    Returns:
        Signed distance value for ``Sketcher.Constraint``.
    """
    mag = abs(float(magnitude))
    if polarity in (1, -1):
        return mag * float(polarity)

    pa = ground_truth_xy(ground_truth, ref_a)
    pb = ground_truth_xy(ground_truth, ref_b)
    if pa is not None and pb is not None:
        delta = float(pb[axis_index]) - float(pa[axis_index])
        if abs(delta) >= _EPS:
            return math.copysign(mag, delta)

    if (
        live_a is not None
        and live_b is not None
        and len(live_a) > axis_index
        and len(live_b) > axis_index
    ):
        delta = float(live_b[axis_index]) - float(live_a[axis_index])
        if abs(delta) >= _EPS:
            return math.copysign(mag, delta)

    return mag
