"""IntentSpec schema: contract between Intent Model and Layer 2 templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
            through to FabricationPlan geometric constraints.
        placement: Optional sketch-plane placement; passed through to the template
            as ``plane`` and compiled into ``CoordinateSystemSpec``.
    """

    template_name: str
    slots: dict[str, float]
    assumptions: list[str] = field(default_factory=list)
    slot_bindings: list[str] = field(default_factory=list)
    placement: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible plain dict."""
        d: dict[str, Any] = {
            "template_name": self.template_name,
            "slots": self.slots,
            "assumptions": self.assumptions,
            "slot_bindings": self.slot_bindings,
        }
        if self.placement is not None:
            d["placement"] = self.placement
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IntentSpec:
        """Deserialise from a plain dict."""
        return cls(
            template_name=d["template_name"],
            slots={k: float(v) for k, v in d["slots"].items()},
            assumptions=list(d.get("assumptions", [])),
            slot_bindings=list(d.get("slot_bindings", [])),
            placement=d.get("placement"),
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
