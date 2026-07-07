"""Connector domain templates for Layer 2 fabrication planning."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from freecad_mcp.tools.fabrication_schema import (
    CoordinateSystemSpec,
    FabricationPlan,
    FeatureSpec,
    SketchSpec,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


_DIMENSION_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|毫米|cm|厘米|m|米|in|inch|英寸)?",
    re.IGNORECASE,
)
_KEYED_DIMENSION_RE = re.compile(
    r"(?P<key>长边|长臂|长|短边|短臂|短|宽度|全局宽度|宽|厚度|厚)"
    r"[^0-9]{0,12}"
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mm|毫米|cm|厘米|m|米|in|inch|英寸)?",
    re.IGNORECASE,
)


def _unit_to_mm(value: float, unit: str | None) -> float:
    normalized = (unit or "mm").lower()
    if normalized in {"cm", "厘米"}:
        return value * 10.0
    if normalized in {"m", "米"}:
        return value * 1000.0
    if normalized in {"in", "inch", "英寸"}:
        return value * 25.4
    return value


def _numbers_from_description(description: str) -> list[float]:
    values: list[float] = []
    for match in _DIMENSION_RE.finditer(description):
        values.append(_unit_to_mm(float(match.group("value")), match.group("unit")))
    return values


def _keyed_dimensions_from_description(description: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for match in _KEYED_DIMENSION_RE.finditer(description):
        key = match.group("key")
        value = _unit_to_mm(float(match.group("value")), match.group("unit"))
        if key in {"长边", "长臂", "长"}:
            values["long_arm"] = value
        elif key in {"短边", "短臂", "短"}:
            values["short_arm"] = value
        elif key in {"宽度", "全局宽度", "宽"}:
            values["width"] = value
        elif key in {"厚度", "厚"}:
            values["thickness"] = value
    return values


def _coalesce_dimension(
    explicit_value: float | None,
    fallback_values: list[float],
    index: int,
    default: float,
) -> float:
    if explicit_value is not None:
        return float(explicit_value)
    if index < len(fallback_values):
        return float(fallback_values[index])
    return default


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


def build_l_connector_plan(
    *,
    description: str = "",
    arm_x_length: float | None = None,
    arm_y_length: float | None = None,
    arm_x_width: float | None = None,
    arm_y_width: float | None = None,
    thickness: float | None = None,
    plane: dict[str, Any] | None = None,
) -> FabricationPlan:
    """Build a FabricationPlan for a fully constrained L connector."""
    parsed = _numbers_from_description(description)
    keyed = _keyed_dimensions_from_description(description)

    short_arm = keyed.get("short_arm")
    long_arm = keyed.get("long_arm")
    global_width = keyed.get("width")
    keyed_thickness = keyed.get("thickness")

    lx = _coalesce_dimension(
        arm_x_length,
        [short_arm] if short_arm is not None else parsed,
        0,
        50.0,
    )
    ly = _coalesce_dimension(
        arm_y_length,
        [long_arm] if long_arm is not None else parsed,
        0 if long_arm is not None else 1,
        80.0,
    )
    wx = _coalesce_dimension(
        arm_x_width,
        [global_width] if global_width is not None else parsed,
        0 if global_width is not None else 2,
        30.0,
    )
    wy = _coalesce_dimension(
        arm_y_width,
        [global_width] if global_width is not None else parsed,
        0 if global_width is not None else 3,
        wx,
    )
    thick = _coalesce_dimension(
        thickness,
        [keyed_thickness] if keyed_thickness is not None else parsed,
        0 if keyed_thickness is not None else 4,
        10.0,
    )

    if min(lx, ly, wx, wy, thick) <= 0:
        raise ValueError("All L connector dimensions must be positive")
    if wx >= ly:
        raise ValueError("arm_x_width must be smaller than arm_y_length")
    if wy >= lx:
        raise ValueError("arm_y_width must be smaller than arm_x_length")

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


def register_connector_templates(
    mcp: Any,
    _get_bridge: Callable[[], Awaitable[Any]],
) -> None:
    """Register connector domain template tools."""

    @mcp.tool()
    async def resolve_l_connector_template(
        description: str = "",
        arm_x_length: float | None = None,
        arm_y_length: float | None = None,
        arm_x_width: float | None = None,
        arm_y_width: float | None = None,
        thickness: float | None = None,
        plane: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve L connector business intent into a FabricationPlan."""
        plan = build_l_connector_plan(
            description=description,
            arm_x_length=arm_x_length,
            arm_y_length=arm_y_length,
            arm_x_width=arm_x_width,
            arm_y_width=arm_y_width,
            thickness=thickness,
            plane=plane,
        )
        return plan.to_dict()
