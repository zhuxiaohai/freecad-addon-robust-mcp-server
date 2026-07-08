"""Tests for IntentSpec schema and template registry."""

from __future__ import annotations

import pytest

from freecad_mcp.intent.registry import resolve_intent_to_plan, validate_intent
from freecad_mcp.intent.schema import IntentSpec, apply_slot_bindings


class TestIntentSpec:
    """Tests for IntentSpec serialisation."""

    def test_round_trip(self) -> None:
        """IntentSpec survives dict round-trip."""
        spec = IntentSpec(
            template_name="l_connector",
            slots={"arm_x_length": 50.0, "arm_y_length": 80.0, "width": 30.0},
            assumptions=["unit=mm"],
            slot_bindings=["arm_x_width = width"],
            placement={"translation": [0.0, 0.0, 100.0]},
        )
        restored = IntentSpec.from_dict(spec.to_dict())
        assert restored.template_name == "l_connector"
        assert restored.slots["width"] == 30.0
        assert restored.assumptions == ["unit=mm"]
        assert restored.slot_bindings == ["arm_x_width = width"]
        assert restored.placement == {"translation": [0.0, 0.0, 100.0]}


class TestApplySlotBindings:
    """Tests for semantic slot binding compilation."""

    def test_applies_width_to_both_arms(self) -> None:
        """Global width binding expands to arm_x_width and arm_y_width."""
        resolved = apply_slot_bindings(
            {"arm_x_length": 50, "arm_y_length": 80, "width": 30},
            ["arm_x_width = width", "arm_y_width = width"],
            defaults={"thickness": 10.0},
        )
        assert resolved["arm_x_width"] == 30.0
        assert resolved["arm_y_width"] == 30.0
        assert resolved["thickness"] == 10.0

    def test_invalid_binding_raises(self) -> None:
        """Malformed bindings raise ValueError."""
        with pytest.raises(ValueError, match="Invalid slot_binding"):
            apply_slot_bindings({"width": 30}, ["not a binding"])

    def test_unknown_source_slot_raises(self) -> None:
        """Bindings referencing missing slots raise ValueError."""
        with pytest.raises(ValueError, match="unknown slot"):
            apply_slot_bindings({}, ["arm_x_width = width"])


class TestResolveIntentToPlan:
    """Tests for deterministic IntentSpec → FabricationPlan compilation."""

    def test_l_connector_with_width_binding(self) -> None:
        """resolve_intent_to_plan compiles L connector from IntentSpec."""
        intent = IntentSpec(
            template_name="l_connector",
            slots={
                "arm_x_length": 50.0,
                "arm_y_length": 80.0,
                "width": 30.0,
                "thickness": 6.0,
            },
            slot_bindings=["arm_x_width = width", "arm_y_width = width"],
            assumptions=["unit=mm", "width applies to both arms"],
        )
        plan = resolve_intent_to_plan(intent)
        sketch = plan.sketches[0].sketch
        length_constraints = plan.sketches[0].constraints["Length"]

        assert sketch["line_1"]["end"] == [50.0, 0.0]
        assert length_constraints[0][1]["length"] == 50.0
        assert length_constraints[1][1]["length"] == 30.0
        assert length_constraints[2][1]["length"] == 30.0
        assert length_constraints[3][1]["length"] == 80.0
        assert plan.features[0].params["towards"] == 6.0
        assert plan.metadata["intent_spec"]["template_name"] == "l_connector"
        assert plan.metadata["assumptions"] == [
            "unit=mm",
            "width applies to both arms",
        ]

    def test_unknown_template_raises(self) -> None:
        """Unknown template_name raises ValueError."""
        intent = IntentSpec(template_name="unknown_part", slots={})
        with pytest.raises(ValueError, match="Unknown template"):
            resolve_intent_to_plan(intent)

    def test_missing_required_slots_raises(self) -> None:
        """Missing required slots after binding resolution raise ValueError."""
        intent = IntentSpec(
            template_name="l_connector",
            slots={"arm_x_length": 50.0},
        )
        with pytest.raises(ValueError, match="Missing required slots"):
            resolve_intent_to_plan(intent)

    def test_validate_intent_unknown_template(self) -> None:
        """validate_intent rejects unknown templates."""
        intent = IntentSpec(template_name="nope", slots={})
        with pytest.raises(ValueError, match="Unknown template"):
            validate_intent(intent)
