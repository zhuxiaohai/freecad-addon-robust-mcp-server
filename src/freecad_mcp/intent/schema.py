"""IntentSpec schema: contract between Intent Model and Layer 2 templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class HoleArraySpec:
    """Rectangular hole array on one semantic face of a template part.

    Attributes:
        face_id: Semantic face key from the template face catalog
            (e.g. ``arm_x_top``, ``bottom``).
        pattern: Array pattern type. Only ``rectangular`` is compiled in v1.
        count_u: Number of holes along the face u-axis.
        count_v: Number of holes along the face v-axis.
        pitch_u: Spacing between hole centres along u (mm).
        pitch_v: Spacing between hole centres along v (mm).
        diameter: Hole diameter in mm, or one value per hole (row-major
            ``count_u * count_v`` order) when sizes differ on the same face.
        margin_u: Distance from the face u-origin to the first hole centre.
        margin_v: Distance from the face v-origin to the first hole centre.
    """

    face_id: str
    pattern: Literal["rectangular", "polar"] = "rectangular"
    count_u: int = 1
    count_v: int = 1
    pitch_u: float = 10.0
    pitch_v: float = 10.0
    diameter: float | list[float] = 5.0
    margin_u: float = 10.0
    margin_v: float = 10.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible plain dict."""
        return {
            "face_id": self.face_id,
            "pattern": self.pattern,
            "count_u": self.count_u,
            "count_v": self.count_v,
            "pitch_u": self.pitch_u,
            "pitch_v": self.pitch_v,
            "diameter": self.diameter,
            "margin_u": self.margin_u,
            "margin_v": self.margin_v,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HoleArraySpec:
        """Deserialise from a plain dict."""
        raw_diameter = d.get("diameter", 5.0)
        if isinstance(raw_diameter, list):
            diameter: float | list[float] = [float(v) for v in raw_diameter]
        else:
            diameter = float(raw_diameter)
        return cls(
            face_id=str(d["face_id"]),
            pattern=d.get("pattern", "rectangular"),
            count_u=int(d.get("count_u", 1)),
            count_v=int(d.get("count_v", 1)),
            pitch_u=float(d.get("pitch_u", 10.0)),
            pitch_v=float(d.get("pitch_v", 10.0)),
            diameter=diameter,
            margin_u=float(d.get("margin_u", 10.0)),
            margin_v=float(d.get("margin_v", 10.0)),
        )


def validate_hole_array_spec(spec: HoleArraySpec) -> None:
    """Validate a single hole array specification.

    Args:
        spec: Hole array to validate.

    Raises:
        ValueError: If counts, pitches, diameters, or pattern are invalid.
    """
    if spec.pattern != "rectangular":
        msg = f"Unsupported hole pattern {spec.pattern!r}; only 'rectangular' is supported"
        raise ValueError(msg)
    if spec.count_u < 1 or spec.count_v < 1:
        msg = "count_u and count_v must be >= 1"
        raise ValueError(msg)
    if spec.pitch_u <= 0 or spec.pitch_v <= 0:
        msg = "pitch_u and pitch_v must be positive"
        raise ValueError(msg)
    if spec.margin_u < 0 or spec.margin_v < 0:
        msg = "margin_u and margin_v must be non-negative"
        raise ValueError(msg)

    hole_count = spec.count_u * spec.count_v
    if isinstance(spec.diameter, list):
        if len(spec.diameter) != hole_count:
            msg = (
                f"diameter list length {len(spec.diameter)} does not match "
                f"hole count {hole_count} (count_u * count_v)"
            )
            raise ValueError(msg)
        for idx, diam in enumerate(spec.diameter):
            if float(diam) <= 0:
                msg = f"diameter[{idx}] must be positive"
                raise ValueError(msg)
    elif float(spec.diameter) <= 0:
        msg = "diameter must be positive"
        raise ValueError(msg)


@dataclass
class IntentSpec:
    """Structured design intent produced by an Intent Model.

    This is the sole input contract for Layer 2 domain templates.  Natural
    language parsing happens upstream (Cursor, skill/RAG, or trained Policy 1).

    Attributes:
        template_name: Registry key for the domain template (e.g. ``l_connector``).
        slots: Numeric parameter values in millimetres.
        assumptions: Human-readable assumptions applied during intent compilation
            (e.g. ``unit=mm``, ``width applies to both arms``).  Stored in plan
            metadata for logging and RL attribution only.
        slot_bindings: Semantic slot coupling rules (e.g. ``arm_x_width = width``).
            Compiled by the template layer into concrete slot values — not passed
            through to OperationPlan geometric constraints.
        placement: Optional sketch-plane placement; passed through to the template
            as ``plane`` and compiled into ``CoordinateSystemSpec``.
        hole_groups: Optional per-face hole array specifications for templates
            that support mounting-hole features (e.g. L connector).
    """

    template_name: str
    slots: dict[str, float]
    assumptions: list[str] = field(default_factory=list)
    slot_bindings: list[str] = field(default_factory=list)
    placement: dict[str, Any] | None = None
    hole_groups: list[HoleArraySpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible plain dict."""
        d: dict[str, Any] = {
            "template_name": self.template_name,
            "slots": self.slots,
            "assumptions": self.assumptions,
            "slot_bindings": self.slot_bindings,
            "hole_groups": [hg.to_dict() for hg in self.hole_groups],
        }
        if self.placement is not None:
            d["placement"] = self.placement
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IntentSpec:
        """Deserialise from a plain dict."""
        raw_groups = d.get("hole_groups", [])
        hole_groups = [HoleArraySpec.from_dict(g) for g in raw_groups]
        return cls(
            template_name=d["template_name"],
            slots={k: float(v) for k, v in d["slots"].items()},
            assumptions=list(d.get("assumptions", [])),
            slot_bindings=list(d.get("slot_bindings", [])),
            placement=d.get("placement"),
            hole_groups=hole_groups,
        )


def apply_slot_bindings(
    slots: dict[str, float],
    slot_bindings: list[str],
    *,
    defaults: dict[str, float] | None = None,
) -> dict[str, float]:
    """Apply semantic slot bindings to produce a fully resolved slot dict.

    Each binding must be of the form ``target = source`` where both names refer
    to slot keys.  Bindings are applied in order after merging ``defaults`` and
    ``slots``.

    Args:
        slots: Explicit slot values from the IntentSpec.
        slot_bindings: Coupling rules such as ``arm_x_width = width``.
        defaults: Optional default values applied before explicit slots.

    Returns:
        Resolved slot dictionary with bindings applied.

    Raises:
        ValueError: If a binding is malformed or references an unknown slot.

    Example:
        >>> apply_slot_bindings(
        ...     {"arm_x_length": 50, "arm_y_length": 80, "width": 30},
        ...     ["arm_x_width = width", "arm_y_width = width"],
        ...     defaults={"thickness": 10.0},
        ... )
        {'arm_x_length': 50.0, 'arm_y_length': 80.0, 'width': 30.0,
         'thickness': 10.0, 'arm_x_width': 30.0, 'arm_y_width': 30.0}
    """
    resolved: dict[str, float] = dict(defaults or {})
    resolved.update({k: float(v) for k, v in slots.items()})

    for binding in slot_bindings:
        parts = [part.strip() for part in binding.split("=", maxsplit=1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            msg = f"Invalid slot_binding (expected 'target = source'): {binding!r}"
            raise ValueError(msg)
        target, source = parts
        if source not in resolved:
            msg = f"slot_binding references unknown slot {source!r} in {binding!r}"
            raise ValueError(msg)
        resolved[target] = resolved[source]

    return resolved
