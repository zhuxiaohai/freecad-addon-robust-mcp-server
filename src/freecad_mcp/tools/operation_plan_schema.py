"""Ordered OperationPlan schema for deterministic batch execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BATCH_OPERATION_TOOLS = {
    "create_coordinate_system",
    "create_sketch_geometry",
    "apply_sketch_constraints",
    "execute_extrude",
    "execute_boolean",
    "execute_revolve",
    "execute_helix",
    "feature_fillet",
    "feature_chamfer",
    "get_body_snapshot",
}


@dataclass
class OperationSpec:
    """One deterministic primitive call in execution order."""

    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain JSON-compatible data."""
        data: dict[str, Any] = {
            "tool_name": self.tool_name,
            "args": self.args,
        }
        if self.description:
            data["description"] = self.description
        if self.source:
            data["source"] = self.source
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationSpec:
        """Build an operation spec from plain JSON-compatible data."""
        return cls(
            tool_name=str(data.get("tool_name") or data.get("primitive_tool") or ""),
            args=dict(data.get("args") or {}),
            description=str(data.get("description") or ""),
            source=dict(data.get("source") or {}),
        )


@dataclass
class OperationPlan:
    """Ordered batch handoff between deterministic templates and primitives."""

    operations: list[OperationSpec]
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = "operation_plan/v1"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain JSON-compatible data."""
        return {
            "schema": self.version,
            "operations": [op.to_dict() for op in self.operations],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationPlan:
        """Build an operation plan from plain JSON-compatible data."""
        operations = data.get("operations")
        if not isinstance(operations, list):
            msg = "OperationPlan requires an operations list"
            raise ValueError(msg)
        return cls(
            operations=[
                OperationSpec.from_dict(op) for op in operations if isinstance(op, dict)
            ],
            metadata=dict(data.get("metadata") or {}),
            version=str(
                data.get("schema") or data.get("version") or "operation_plan/v1"
            ),
        )


def validate_operation_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate ordered OperationPlan shape without executing FreeCAD."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        parsed = OperationPlan.from_dict(plan)
    except Exception as exc:
        return {
            "valid": False,
            "errors": [{"path": "$", "message": str(exc)}],
            "warnings": [],
            "summary": "OperationPlan validation failed.",
        }

    for index, op in enumerate(parsed.operations):
        path = f"$.operations[{index}]"
        if not op.tool_name:
            errors.append(
                {"path": f"{path}.tool_name", "message": "tool_name is required"}
            )
        elif op.tool_name not in BATCH_OPERATION_TOOLS:
            errors.append(
                {
                    "path": f"{path}.tool_name",
                    "message": f"unsupported batch operation tool: {op.tool_name!r}",
                }
            )
        if not isinstance(op.args, dict):
            errors.append({"path": f"{path}.args", "message": "args must be an object"})

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": (
            "OperationPlan validation passed."
            if not errors
            else f"OperationPlan validation failed with {len(errors)} error(s)."
        ),
        "operation_count": len(parsed.operations),
    }


def minimal_operation_plan_example() -> dict[str, Any]:
    """Return a minimal ordered rectangle-extrude batch example."""
    return OperationPlan(
        operations=[
            OperationSpec(
                "create_coordinate_system",
                {
                    "name": "BaseXY",
                    "euler_angles": [0.0, 0.0, 0.0],
                    "translation": [0.0, 0.0, 0.0],
                },
            ),
            OperationSpec(
                "create_sketch_geometry",
                {
                    "sketch_name": "RectProfile",
                    "coordinate_system_name": "BaseXY",
                    "sketch": {
                        "line_1": {"start": [0.0, 0.0], "end": [40.0, 0.0]},
                        "line_2": {"start": [40.0, 0.0], "end": [40.0, 20.0]},
                        "line_3": {"start": [40.0, 20.0], "end": [0.0, 20.0]},
                        "line_4": {"start": [0.0, 20.0], "end": [0.0, 0.0]},
                    },
                },
            ),
            OperationSpec(
                "execute_extrude",
                {
                    "sketch_name": "RectProfile",
                    "towards": 10.0,
                    "opposite": 0.0,
                    "feature_name": "BlockSolid",
                },
            ),
        ],
        metadata={"template": "minimal_block"},
    ).to_dict()
