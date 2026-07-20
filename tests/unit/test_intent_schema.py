"""Tests for IntentSpec schema and template registry."""

from __future__ import annotations

from typing import Any, cast

import pytest

from freecad_mcp.intent.registry import (
    compile_intent_package,
    describe_template,
    list_template_catalog,
    resolve_intent_to_plan,
    validate_intent,
)
from freecad_mcp.intent.schema import HoleArraySpec, IntentSpec, apply_slot_bindings


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

    def test_round_trip_with_hole_groups(self) -> None:
        """hole_groups survive dict serialisation."""
        spec = IntentSpec(
            template_name="l_connector",
            slots={"arm_x_length": 50.0, "arm_y_length": 80.0, "width": 30.0},
            hole_groups=[
                HoleArraySpec(
                    face_id="arm_x_top",
                    count_u=2,
                    count_v=2,
                    diameter=6.0,
                )
            ],
        )
        restored = IntentSpec.from_dict(spec.to_dict())
        assert len(restored.hole_groups) == 1
        assert restored.hole_groups[0].face_id == "arm_x_top"
        assert restored.hole_groups[0].count_u == 2
        assert restored.hole_groups[0].diameter == 6.0

    def test_hole_groups_default_empty(self) -> None:
        """Legacy IntentSpec dicts without hole_groups deserialise cleanly."""
        restored = IntentSpec.from_dict(
            {
                "template_name": "l_connector",
                "slots": {"arm_x_length": 50.0},
            }
        )
        assert restored.hole_groups == []


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
    """Tests for deterministic IntentSpec → OperationPlan compilation."""

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
        sketch = plan.operations[1].args["sketch"]
        length_constraints = plan.operations[2].args["constraints"]["Length"]

        assert sketch["line_1"]["end"] == [50.0, 0.0]
        assert length_constraints[0][1]["length"] == 50.0
        assert length_constraints[1][1]["length"] == 30.0
        assert length_constraints[2][1]["length"] == 30.0
        assert length_constraints[3][1]["length"] == 80.0
        # A user-facing shared width must remain a real Spreadsheet alias,
        # rather than being expanded into two unrelated internal parameters.
        assert length_constraints[1][1]["alias"] == "width"
        assert length_constraints[2][1]["alias"] == "width"
        assert "width" in plan.metadata["editable_aliases"]
        assert "arm_x_width" not in plan.metadata["editable_aliases"]
        assert plan.metadata["design_intent"]["shared_aliases"] == [
            "width",
            "thickness",
        ]
        assert plan.operations[3].args["towards"] == 6.0
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

    def test_resolve_intent_to_plan_is_deterministic(self) -> None:
        """Same IntentSpec produces identical OperationPlan dicts."""
        intent = IntentSpec(
            template_name="l_connector",
            slots={
                "arm_x_length": 50.0,
                "arm_y_length": 80.0,
                "width": 30.0,
            },
        )

        first = resolve_intent_to_plan(intent).to_dict()
        second = resolve_intent_to_plan(intent).to_dict()

        assert first == second


class TestTemplateCatalog:
    """Tests for machine-readable template catalog entries."""

    def test_list_template_catalog_contains_l_connector(self) -> None:
        """Catalog exposes L connector selection metadata."""
        catalog = list_template_catalog()
        names = {entry["template_name"] for entry in catalog}

        assert "l_connector" in names
        entry = cast("dict[str, Any]", describe_template("l_connector"))
        assert "L型连接件" in entry["aliases"]
        assert "arm_x_length" in entry["required_slots"]
        assert "arm_x_top" in entry["valid_face_ids"]
        assert entry["example_intent"]["template_name"] == "l_connector"

    def test_describe_unknown_template_raises(self) -> None:
        """Unknown catalog keys raise a clear error."""
        with pytest.raises(ValueError, match="Unknown template"):
            describe_template("not_a_template")

    def test_compile_intent_package_preserves_provenance(self) -> None:
        """compile_intent_package returns plan plus IntentSpec provenance."""
        intent = IntentSpec(
            template_name="l_connector",
            slots={
                "arm_x_length": 50.0,
                "arm_y_length": 80.0,
                "width": 30.0,
            },
            assumptions=["unit=mm"],
        )

        package = cast("dict[str, Any]", compile_intent_package(intent))

        assert package["intent_spec"]["template_name"] == "l_connector"
        assert package["operation_plan"]["metadata"]["template"] == "l_connector"
        assert package["assumptions"] == ["unit=mm"]
        assert package["template_provenance"]["deterministic"] is True
        assert package["compile_diagnostics"]["ok"] is True
