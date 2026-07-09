"""Template registry: deterministic IntentSpec → FabricationPlan compilation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from freecad_mcp.intent.schema import HoleArraySpec, IntentSpec, apply_slot_bindings
from freecad_mcp.intent.schema import validate_hole_array_spec as _validate_hole_spec
from freecad_mcp.tools.templates.face_catalog import L_CONNECTOR_EXTERIOR_FACE_IDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from freecad_mcp.tools.fabrication_schema import FabricationPlan


@dataclass
class SlotSchema:
    """Slot contract for a domain template.

    Attributes:
        required: Slot names that must be present after binding resolution.
        defaults: Default values applied before explicit slots.
        auto_bindings: Bindings applied when width is given without arm widths.
        allowed_face_ids: Valid semantic face keys for hole_groups (if supported).
    """

    required: list[str]
    defaults: dict[str, float] = field(default_factory=dict)
    auto_bindings: list[str] = field(default_factory=list)
    allowed_face_ids: tuple[str, ...] = field(default_factory=tuple)


TEMPLATE_SLOT_SCHEMAS: dict[str, SlotSchema] = {
    "l_connector": SlotSchema(
        required=["arm_x_length", "arm_y_length", "arm_x_width", "arm_y_width"],
        defaults={"thickness": 10.0},
        auto_bindings=["arm_x_width = width", "arm_y_width = width"],
        allowed_face_ids=L_CONNECTOR_EXTERIOR_FACE_IDS,
    ),
}


def validate_hole_groups_for_template(
    template_name: str,
    hole_groups: list[HoleArraySpec],
) -> None:
    """Validate hole group specifications for a registered template.

    Args:
        template_name: Template registry key.
        hole_groups: Hole arrays from IntentSpec.

    Raises:
        ValueError: If face IDs are unknown or hole specs are invalid.
    """
    schema = TEMPLATE_SLOT_SCHEMAS.get(template_name)
    if schema is None or not schema.allowed_face_ids:
        if hole_groups:
            msg = f"Template {template_name!r} does not support hole_groups"
            raise ValueError(msg)
        return

    allowed = set(schema.allowed_face_ids)
    for group in hole_groups:
        _validate_hole_spec(group)
        if group.face_id not in allowed:
            known = ", ".join(schema.allowed_face_ids)
            msg = (
                f"Unknown face_id {group.face_id!r} for template "
                f"{template_name!r}. Known: {known}"
            )
            raise ValueError(msg)


def _build_l_connector_from_intent(intent: IntentSpec) -> FabricationPlan:
    from freecad_mcp.tools.templates.connectors import build_l_connector_plan

    schema = TEMPLATE_SLOT_SCHEMAS["l_connector"]
    bindings = list(intent.slot_bindings)
    if "width" in intent.slots and "arm_x_width" not in intent.slots:
        for auto in schema.auto_bindings:
            if auto not in bindings:
                bindings.append(auto)

    resolved = apply_slot_bindings(
        intent.slots,
        bindings,
        defaults=schema.defaults,
    )
    missing = [name for name in schema.required if name not in resolved]
    if missing:
        msg = f"Missing required slots for l_connector: {', '.join(missing)}"
        raise ValueError(msg)

    validate_hole_groups_for_template("l_connector", intent.hole_groups)

    plan = build_l_connector_plan(
        slots=resolved,
        plane=intent.placement,
        hole_groups=intent.hole_groups or None,
    )
    plan.metadata["intent_spec"] = intent.to_dict()
    plan.metadata["assumptions"] = list(intent.assumptions)
    return plan


TEMPLATE_BUILDERS: dict[str, Callable[[IntentSpec], FabricationPlan]] = {
    "l_connector": _build_l_connector_from_intent,
}


def validate_intent(intent: IntentSpec) -> None:
    """Validate an IntentSpec before template compilation.

    Args:
        intent: Structured intent from an Intent Model.

    Raises:
        ValueError: If the template is unknown or required slots cannot be resolved.
    """
    if intent.template_name not in TEMPLATE_BUILDERS:
        known = ", ".join(sorted(TEMPLATE_BUILDERS))
        msg = f"Unknown template: {intent.template_name!r}. Known: {known}"
        raise ValueError(msg)

    validate_hole_groups_for_template(intent.template_name, intent.hole_groups)

    # Dry-run resolution to surface missing slots early.
    resolve_intent_to_plan(intent)


def resolve_intent_to_plan(intent: IntentSpec) -> FabricationPlan:
    """Compile an IntentSpec into a FabricationPlan via the template registry.

    This step is fully deterministic — no LLM involvement.

    Args:
        intent: Structured intent with template_name, slots, and optional bindings.

    Returns:
        FabricationPlan ready for ``execute_fabrication_plan()``.

    Raises:
        ValueError: If the template is unknown or slots are invalid.
    """
    builder = TEMPLATE_BUILDERS.get(intent.template_name)
    if builder is None:
        known = ", ".join(sorted(TEMPLATE_BUILDERS))
        msg = f"Unknown template: {intent.template_name!r}. Known: {known}"
        raise ValueError(msg)
    return builder(intent)
