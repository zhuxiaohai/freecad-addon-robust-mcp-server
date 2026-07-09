"""Connector domain templates for Layer 2 fabrication planning."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from freecad_mcp.intent.schema import HoleArraySpec
from freecad_mcp.tools.fabrication_schema import (
    CoordinateSystemSpec,
    FabricationPlan,
    FeatureSpec,
    SketchSpec,
)
from freecad_mcp.tools.templates.hole_arrays import compile_hole_groups

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _plane_to_coordinate_system(plane: dict[str, Any] | None) -> CoordinateSystemSpec:
    if not plane:
        return CoordinateSystemSpec(
            euler_angles=[0.0, 0.0, 0.0],
            translation=[0.0, 0.0, 0.0],
            name="LConnectorPlane",
        )
    return CoordinateSystemSpec(
        euler_angles=[float(v) for v in plane.get("euler_angles", [0.0, 0.0, 0.0])],
        translation=[float(v) for v in plane.get("translation", [0.0, 0.0, 0.0])],
        name=plane.get("name", "LConnectorPlane"),
    )


def _validate_l_connector_slots(slots: dict[str, float]) -> None:
    """Validate required L-connector dimension slots."""
    required = [
        "arm_x_length",
        "arm_y_length",
        "arm_x_width",
        "arm_y_width",
        "thickness",
    ]
    missing = [name for name in required if name not in slots]
    if missing:
        msg = f"Missing required slots: {', '.join(missing)}"
        raise ValueError(msg)

    lx = float(slots["arm_x_length"])
    ly = float(slots["arm_y_length"])
    wx = float(slots["arm_x_width"])
    wy = float(slots["arm_y_width"])
    thick = float(slots["thickness"])

    if min(lx, ly, wx, wy, thick) <= 0:
        raise ValueError("All L connector dimensions must be positive")
    if wx >= ly:
        raise ValueError("arm_x_width must be smaller than arm_y_length")
    if wy >= lx:
        raise ValueError("arm_y_width must be smaller than arm_x_length")


def _build_l_connector_profile_plan(
    *,
    slots: dict[str, float],
    plane: dict[str, Any] | None = None,
) -> FabricationPlan:
    """Build the base L-profile sketch and extrude (no holes)."""
    lx = float(slots["arm_x_length"])
    ly = float(slots["arm_y_length"])
    wx = float(slots["arm_x_width"])
    wy = float(slots["arm_y_width"])
    thick = float(slots["thickness"])

    cs = _plane_to_coordinate_system(plane)
    sketch = {
        "line_1": {"start": [0.0, 0.0], "end": [lx, 0.0]},
        "line_2": {"start": [lx, 0.0], "end": [lx, wx]},
        "line_3": {"start": [lx, wx], "end": [wy, wx]},
        "line_4": {"start": [wy, wx], "end": [wy, ly]},
        "line_5": {"start": [wy, ly], "end": [0.0, ly]},
        "line_6": {"start": [0.0, ly], "end": [0.0, 0.0]},
    }
    constraints = {
        "Coincident": [
            ["line_1.end", "line_2.start"],
            ["line_2.end", "line_3.start"],
            ["line_3.end", "line_4.start"],
            ["line_4.end", "line_5.start"],
            ["line_5.end", "line_6.start"],
            ["line_6.end", "line_1.start"],
            ["line_1.start", "origin"],
        ],
        "Horizontal": ["line_1", "line_3", "line_5"],
        "Vertical": ["line_2", "line_4", "line_6"],
        "Length": [
            [
                "line_1",
                {
                    "length": lx,
                    "alias": "arm_x_length",
                    "label": "X arm length",
                    "role": "sketch_dimension",
                    "unit": "mm",
                    "min": max(wy + 1.0, 1.0),
                    "default": lx,
                },
            ],
            [
                "line_2",
                {
                    "length": wx,
                    "alias": "arm_x_width",
                    "label": "X arm width",
                    "role": "sketch_dimension",
                    "unit": "mm",
                    "min": 1.0,
                    "max": ly - 1.0,
                    "default": wx,
                },
            ],
            [
                "line_5",
                {
                    "length": wy,
                    "alias": "arm_y_width",
                    "label": "Y arm width",
                    "role": "sketch_dimension",
                    "unit": "mm",
                    "min": 1.0,
                    "max": lx - 1.0,
                    "default": wy,
                },
            ],
            [
                "line_6",
                {
                    "length": ly,
                    "alias": "arm_y_length",
                    "label": "Y arm length",
                    "role": "sketch_dimension",
                    "unit": "mm",
                    "min": max(wx + 1.0, 1.0),
                    "default": ly,
                },
            ],
        ],
    }
    return FabricationPlan(
        coordinate_systems=[cs],
        sketches=[
            SketchSpec(
                sketch=sketch,
                constraints=constraints,
                coordinate_system_name=cs.name,
                sketch_name="L_Connector_Profile",
            )
        ],
        features=[
            FeatureSpec(
                type="extrude",
                sketch_name="L_Connector_Profile",
                operation="NewBody",
                params={"towards": thick, "opposite": 0.0},
                param_aliases={"towards": "thickness"},
                feature_name="L_Connector_Solid",
            )
        ],
        metadata={
            "template": "l_connector",
            "domain": "welding_fixture_connector",
            "editable_aliases": [
                "arm_x_length",
                "arm_x_width",
                "arm_y_width",
                "arm_y_length",
                "thickness",
            ],
            "design_intent": {
                "preserve_aliases_by_default": [
                    "arm_x_width",
                    "arm_y_width",
                    "thickness",
                ],
                "coupled_alias_groups": [],
                "required_constraint_types": [
                    "Coincident",
                    "Horizontal",
                    "Vertical",
                    "Length",
                ],
                "expected_dof": 0,
            },
        },
    )


def build_l_connector_plan(
    *,
    slots: dict[str, float],
    plane: dict[str, Any] | None = None,
    hole_groups: list[HoleArraySpec] | None = None,
) -> FabricationPlan:
    """Build a FabricationPlan for a fully constrained L connector.

    Args:
        slots: Resolved dimension slots in millimetres.  Required keys:
            ``arm_x_length``, ``arm_y_length``, ``arm_x_width``, ``arm_y_width``,
            ``thickness``.
        plane: Optional sketch-plane placement (euler_angles, translation, name).
        hole_groups: Optional per-face rectangular hole arrays compiled after
            the base solid is created.

    Returns:
        FabricationPlan ready for ``execute_fabrication_plan()``.

    Raises:
        ValueError: If required slots are missing or geometry is infeasible.
    """
    _validate_l_connector_slots(slots)
    plan = _build_l_connector_profile_plan(slots=slots, plane=plane)

    if not hole_groups:
        return plan

    hole_sketches, hole_features, face_ids, hole_aliases = compile_hole_groups(
        hole_groups,
        slots=slots,
        base_body_name="L_Connector_Solid",
    )

    plan.sketches.extend(hole_sketches)
    plan.features.extend(hole_features)
    plan.metadata["hole_face_ids"] = face_ids
    plan.metadata["hole_group_count"] = len(hole_groups)
    editable = list(plan.metadata.get("editable_aliases", []))
    editable.extend(hole_aliases)
    plan.metadata["editable_aliases"] = editable

    return plan


def register_connector_templates(
    mcp: Any,
    _get_bridge: Callable[[], Awaitable[Any]],
) -> None:
    """Register connector domain template tools."""

    @mcp.tool()
    async def resolve_l_connector_template(
        slots: dict[str, float],
        plane: dict[str, Any] | None = None,
        hole_groups: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build an L connector FabricationPlan from resolved dimension slots.

        Args:
            slots: Dimension values in mm (arm_x_length, arm_y_length,
                arm_x_width, arm_y_width, thickness).  Use ``resolve_template``
                when starting from a full IntentSpec.
            plane: Optional sketch-plane placement dict.
            hole_groups: Optional list of HoleArraySpec dicts (per-face arrays).

        Returns:
            FabricationPlan dict ready for ``execute_fabrication_plan()``.
        """
        parsed_holes = (
            [HoleArraySpec.from_dict(g) for g in hole_groups] if hole_groups else None
        )
        plan = build_l_connector_plan(
            slots=slots,
            plane=plane,
            hole_groups=parsed_holes,
        )
        return plan.to_dict()
