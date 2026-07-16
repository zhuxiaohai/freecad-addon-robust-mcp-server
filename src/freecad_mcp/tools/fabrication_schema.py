"""FabricationPlan schema: the Layer 1 / Layer 2 interface contract.

This module defines the typed data structures that form the sole interface
between Layer 2 (domain template tools) and Layer 1 (generic fabrication
primitives).

Layer 2 (tools/templates/) produces FabricationPlan instances.
Layer 1 (tools/fabrication.py) consumes them via execute_fabrication_plan().

Neither layer needs to know the internal implementation of the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from freecad_mcp.tools.coordinate_system import (
    COORDINATE_PARAMETER_PATHS,
    validate_coordinate_system_payload,
)

SKETCH_ENTITY_PREFIXES = (
    "line_",
    "circle_",
    "arc_",
    "ellipse_",
    "elliptical_arc_",
    "nurbs_",
)

CONSTRAINT_TYPES = (
    "Coincident",
    "Horizontal",
    "Vertical",
    "Perpendicular",
    "Parallel",
    "Equal",
    "Tangent",
    "Normal",
    "Concentric",
    "Fix",
    "Midpoint",
    "Mirror",
    "Angle",
    "Diameter",
    "Radius",
    "MajorRadius",
    "MinorRadius",
    "Length",
    "Distance",
)

FEATURE_TYPES = ("extrude", "boolean", "revolve", "helix")
FINISH_TYPES = ("fillet", "chamfer")


@dataclass
class CoordinateSystemSpec:
    """Specification for a datum coordinate system (sketch plane).

    Attributes:
        euler_angles: Rotation [a, b, g] in degrees around X, Y, Z axes,
            applied as the active local-to-world rotation
            R = Rx(a) * Ry(b) * Rz(g) (HistCAD / Fusion 360 adapter
            convention).
        translation: Origin [x, y, z] in millimetres. It is world-relative
            when unattached and target-local when ``attachment_support`` exists.
        name: Optional label used to reference this CS in SketchSpec.
        attachment_support: Optional attachment to a reference solid.
            Use ``{"target": "L_Connector_Solid"}``. The adapter resolves
            the target without exposing FreeCAD attachment properties.
        param_aliases: Tunable coordinate properties, keyed by neutral paths
            such as ``translation.z``.

    Example:
        >>> cs = CoordinateSystemSpec(
        ...     euler_angles=[0.0, 0.0, 0.0],
        ...     translation=[0.0, 0.0, 50.0],
        ...     name="TopPlane",
        ... )
    """

    euler_angles: list[float]
    translation: list[float]
    name: str | None = None
    attachment_support: dict[str, Any] = field(default_factory=dict)
    param_aliases: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict for JSON transport."""
        d: dict[str, Any] = {
            "euler_angles": self.euler_angles,
            "translation": self.translation,
            "name": self.name,
        }
        if self.attachment_support:
            d["attachment_support"] = dict(self.attachment_support)
        if self.param_aliases:
            d["param_aliases"] = dict(self.param_aliases)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CoordinateSystemSpec:
        """Deserialise from plain dict."""
        raw_attach = d.get("attachment_support", {})
        return cls(
            euler_angles=d["euler_angles"],
            translation=d["translation"],
            name=d.get("name"),
            attachment_support={
                str(k): v for k, v in raw_attach.items() if v is not None
            },
            param_aliases={
                str(k): str(v) for k, v in d.get("param_aliases", {}).items()
            },
        )


@dataclass
class SketchSpec:
    """Specification for a 2-D sketch: geometry entities + constraints + plane.

    ``coordinate_system_name`` references a CoordinateSystemSpec in the parent
    FabricationPlan. It is the sole sketch-plane mechanism.

    Attributes:
        sketch: HistCAD entity dict, e.g.
            ``{"line_1": {"start": [0,0], "end": [10,0]}, ...}``
        constraints: HistCAD constraint dict, e.g.
            ``{"Horizontal": ["line_1"], "Length": [["line_1", "10 mm"]]}``
        coordinate_system_name: Name of a CoordinateSystemSpec declared in
            the parent plan's ``coordinate_systems`` list.
        sketch_name: Explicit sketch object name. Auto-generated if None.
        attach_after_feature: When set, the sketch is created only after this
            feature (by ``feature_name``) has been executed — required for
            face-attached hole sketches on an existing solid.
        attach_body_feature: Body object used for face attachment. Defaults to
            ``attach_after_feature`` when omitted.

    Example:
        >>> spec = SketchSpec(
        ...     sketch={"circle_1": {"center": [0, 0], "radius": 10}},
        ...     constraints={"Fix": ["circle_1.center"],
        ...                   "Radius": [["circle_1", "10 mm"]]},
        ...     coordinate_system_name="XY_Base",
        ... )
    """

    sketch: dict[str, Any]
    constraints: dict[str, Any]
    coordinate_system_name: str | None = None
    sketch_name: str | None = None
    attach_after_feature: str | None = None
    attach_body_feature: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict."""
        d: dict[str, Any] = {
            "sketch": self.sketch,
            "constraints": self.constraints,
        }
        if self.coordinate_system_name is not None:
            d["coordinate_system_name"] = self.coordinate_system_name
        if self.sketch_name is not None:
            d["sketch_name"] = self.sketch_name
        if self.attach_after_feature is not None:
            d["attach_after_feature"] = self.attach_after_feature
        if self.attach_body_feature is not None:
            d["attach_body_feature"] = self.attach_body_feature
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SketchSpec:
        """Deserialise from plain dict."""
        return cls(
            sketch=d["sketch"],
            constraints=d.get("constraints", {}),
            coordinate_system_name=d.get("coordinate_system_name"),
            sketch_name=d.get("sketch_name"),
            attach_after_feature=d.get("attach_after_feature"),
            attach_body_feature=d.get("attach_body_feature"),
        )


@dataclass
class FeatureSpec:
    """Specification for a 3-D feature execution (extrude, revolve, or helix).

    Attributes:
        type: Feature type — ``"extrude"``, ``"boolean"``, ``"revolve"``,
            or ``"helix"``.
        sketch_name: Name of the sketch to operate on.  Must match a
            ``SketchSpec.sketch_name`` resolved during plan execution, or be a
            pre-existing sketch object name in the document.  Not used for
            ``"boolean"`` features.
        operation: Feature operation.  For ``"extrude"``, ``"revolve"``, and
            ``"helix"``, the canonical pipeline only supports ``"NewBody"``.
            For ``"boolean"``, use ``"Join"``, ``"Cut"``, or ``"Intersect"``.
        params: Type-specific parameter dict:
            - extrude: ``{"towards": float, "opposite": float,
              "extrusion_mode": "auto"|"parametric_sketch"|"robust_face"}``
            - boolean: ``{"base_object_name": str, "tool_object_name": str,
              "operation": "Join"|"Cut"|"Intersect",
              "boolean_mode": "auto"|"parametric"|"static_shape"}``
            - revolve: ``{"axis": [[bx,by,bz],[dx,dy,dz]], "start": float,
              "end": float}``
            - helix:   ``{"axis": ..., "pitch": float, "turns": float,
              "handedness": "Right"|"Left"}``
        param_aliases: Maps param keys to spreadsheet alias names for
            frontend sliders, e.g. ``{"towards": "column_height"}``.
        feature_name: Explicit object name. Auto-generated if None.

    Example:
        >>> feat = FeatureSpec(
        ...     type="extrude",
        ...     sketch_name="Sketch001",
        ...     operation="NewBody",
        ...     params={"towards": 50.0, "opposite": 0.0},
        ...     param_aliases={"towards": "arm_length"},
        ... )
    """

    type: str
    sketch_name: str
    operation: str
    params: dict[str, Any]
    param_aliases: dict[str, str] = field(default_factory=dict)
    feature_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict."""
        return {
            "type": self.type,
            "sketch_name": self.sketch_name,
            "operation": self.operation,
            "params": self.params,
            "param_aliases": self.param_aliases,
            "feature_name": self.feature_name,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FeatureSpec:
        """Deserialise from plain dict."""
        return cls(
            type=d["type"],
            sketch_name=d.get("sketch_name", ""),
            operation=d.get(
                "operation",
                d.get("params", {}).get("operation", "NewBody"),
            ),
            params=d["params"],
            param_aliases=d.get("param_aliases", {}),
            feature_name=d.get("feature_name"),
        )


@dataclass
class FinishSpec:
    """Specification for a finishing operation (fillet or chamfer).

    Attributes:
        type: ``"fillet"`` or ``"chamfer"``.
        near_points: List of 3-D points ``[[x,y,z], ...]``.  Each is
            resolved to the nearest edge of the active body at execution time.
        params: Type-specific parameters:
            - fillet:  ``{"radius": float}`` or
              ``{"radius": [r1, r2, ...]}`` per edge.
            - chamfer: ``{"dist": float, "angle": float, "plane": [nx,ny,nz]}``

    Example:
        >>> fin = FinishSpec(
        ...     type="fillet",
        ...     near_points=[[10.0, 0.0, 50.0], [-10.0, 0.0, 50.0]],
        ...     params={"radius": 2.0},
        ... )
    """

    type: str
    near_points: list[list[float]]
    params: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict."""
        return {
            "type": self.type,
            "near_points": self.near_points,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FinishSpec:
        """Deserialise from plain dict."""
        return cls(
            type=d["type"],
            near_points=d["near_points"],
            params=d["params"],
        )


@dataclass
class FabricationPlan:
    """Complete parametric solid modelling plan.

    A FabricationPlan is the contract between Layer 2 (domain template tools)
    and Layer 1 (generic fabrication primitives).  Layer 2 produces plans;
    Layer 1 executes them via ``execute_fabrication_plan()``.

    The execution order enforced by the adapter is:

    1. Create all ``coordinate_systems`` (datum planes).
    2. For each sketch in ``sketches``: create geometry then apply constraints.
    3. For each feature in ``features``: execute extrude / revolve / helix.
    4. Apply all ``finishes`` (fillet / chamfer).

    Attributes:
        coordinate_systems: Datum planes created before any sketch.
        sketches: Ordered list of sketch specs (geometry + constraints).
        features: Ordered list of 3-D feature specs referencing sketches.
        finishes: Optional list of fillet / chamfer specs.
        param_aliases: Top-level alias registry; individual FeatureSpec entries
            take precedence for the same alias name.
        metadata: Free-form dict for domain-specific annotations (e.g. product
            family, material, tolerance class).

    Example:
        Build a simple cylinder plan::

            plan = FabricationPlan(
                coordinate_systems=[
                    CoordinateSystemSpec([0,0,0], [0,0,0], name="XY_Base")
                ],
                sketches=[
                    SketchSpec(
                        sketch={"circle_1": {"center":[0,0], "radius":10}},
                        constraints={"Fix":["circle_1.center"],
                                     "Radius":[["circle_1","10 mm"]]},
                        coordinate_system_name="XY_Base",
                        sketch_name="Sketch001",
                    )
                ],
                features=[
                    FeatureSpec("extrude","Sketch001","NewBody",
                                {"towards":30.0},
                                param_aliases={"towards":"height"})
                ],
            )
            result = await execute_fabrication_plan(plan.to_dict())
    """

    coordinate_systems: list[CoordinateSystemSpec]
    sketches: list[SketchSpec]
    features: list[FeatureSpec]
    finishes: list[FinishSpec] = field(default_factory=list)
    param_aliases: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible plain dict."""
        return {
            "coordinate_systems": [cs.to_dict() for cs in self.coordinate_systems],
            "sketches": [s.to_dict() for s in self.sketches],
            "features": [f.to_dict() for f in self.features],
            "finishes": [fin.to_dict() for fin in self.finishes],
            "param_aliases": self.param_aliases,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FabricationPlan:
        """Deserialise from a plain dict (as returned by a template tool)."""
        return cls(
            coordinate_systems=[
                CoordinateSystemSpec.from_dict(cs)
                for cs in d.get("coordinate_systems", [])
            ],
            sketches=[SketchSpec.from_dict(s) for s in d.get("sketches", [])],
            features=[FeatureSpec.from_dict(f) for f in d.get("features", [])],
            finishes=[FinishSpec.from_dict(fin) for fin in d.get("finishes", [])],
            param_aliases=d.get("param_aliases", {}),
            metadata=d.get("metadata", {}),
        )


def minimal_fabrication_plan_example() -> dict[str, Any]:
    """Return a minimal executable FabricationPlan example for agents."""
    return FabricationPlan(
        coordinate_systems=[
            CoordinateSystemSpec([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], name="XY_Base"),
        ],
        sketches=[
            SketchSpec(
                sketch={
                    "circle_1": {"center": [0.0, 0.0], "radius": 10.0},
                },
                constraints={
                    "Fix": ["circle_1.center"],
                    "Radius": [["circle_1", "10 mm"]],
                },
                coordinate_system_name="XY_Base",
                sketch_name="BaseCircle",
            )
        ],
        features=[
            FeatureSpec(
                type="extrude",
                sketch_name="BaseCircle",
                operation="NewBody",
                params={"towards": 20.0, "opposite": 0.0},
                feature_name="Cylinder",
            )
        ],
        metadata={"source": "describe_fabrication_plan_schema"},
    ).to_dict()


def describe_fabrication_plan_schema() -> dict[str, Any]:
    """Return the agent-facing FabricationPlan contract."""
    return {
        "schema_name": "FabricationPlan",
        "version": "v1",
        "deterministic": True,
        "description": (
            "Layer 1 CAD execution contract. Agents should generate this only "
            "when no registered template fits, then call validate_fabrication_plan "
            "before execute_fabrication_plan."
        ),
        "top_level_fields": {
            "coordinate_systems": "list[CoordinateSystemSpec], created before sketches",
            "sketches": "list[SketchSpec], ordered 2D profiles and constraints",
            "features": "list[FeatureSpec], ordered extrude/boolean/revolve/helix features",
            "finishes": "optional list[FinishSpec], fillet/chamfer operations",
            "param_aliases": (
                "optional fallback dict[str,str] for tunable feature aliases; "
                "prefer per-feature param_aliases when possible"
            ),
            "metadata": "optional dict for provenance and agent trace",
        },
        "coordinate_system_spec": {
            "required": ["euler_angles", "translation"],
            "optional": ["name", "attachment_support", "param_aliases"],
            "attachment_support": "optional {'target': '<upstream feature or object>'}; pose is target-local when present",
            "param_aliases": {
                "description": "optional mapping of tunable coordinate properties to aliases",
                "supported_properties": sorted(COORDINATE_PARAMETER_PATHS),
            },
            "notes": "Euler angles are active local-to-world XYZ degrees; units are mm.",
        },
        "sketch_spec": {
            "required": ["sketch"],
            "optional": [
                "constraints",
                "coordinate_system_name",
                "sketch_name",
                "attach_after_feature",
                "attach_body_feature",
            ],
            "notes": (
                "Provide coordinate_system_name. Named sketches are recommended because "
                "features reference sketch_name."
            ),
        },
        "feature_spec": {
            "types": list(FEATURE_TYPES),
            "extrude": {
                "required": ["type", "sketch_name", "operation", "params"],
                "operation": "NewBody",
                "params": {"towards": "float", "opposite": "float optional"},
                "param_aliases": (
                    "optional per-feature mapping such as "
                    "{'towards': 'thickness'} for reliable extrude linkage"
                ),
            },
            "boolean": {
                "required": ["type", "operation", "params"],
                "operation": "Join | Cut | Intersect",
                "params": {
                    "base_object_name": "feature/object name",
                    "tool_object_name": "feature/object name",
                    "operation": "Join | Cut | Intersect optional mirror",
                },
            },
            "revolve": {
                "required": ["type", "sketch_name", "operation", "params"],
                "params": {
                    "axis": "[[x,y,z],[dx,dy,dz]]",
                    "start": "float",
                    "end": "float",
                },
                "param_aliases_note": (
                    "accepted by the interface but not a reliable auto-binding "
                    "path in the current executor"
                ),
            },
            "helix": {
                "required": ["type", "sketch_name", "operation", "params"],
                "params": {
                    "axis": "[[x,y,z],[dx,dy,dz]]",
                    "pitch": "float",
                    "turns": "float",
                    "handedness": "Right | Left",
                },
                "param_aliases_note": (
                    "accepted by the interface but not a reliable auto-binding "
                    "path in the current executor"
                ),
            },
        },
        "finish_spec": {
            "types": list(FINISH_TYPES),
            "fillet": {"params": {"radius": "float or list[float]"}},
            "chamfer": {"params": {"dist": "float", "angle": "float optional"}},
        },
        "sketch_entity_conventions": {
            "line_N": {"start": "[x,y]", "end": "[x,y]"},
            "circle_N": {"center": "[x,y]", "radius": "float"},
            "arc_N": {"start": "[x,y]", "middle": "[x,y]", "end": "[x,y]"},
            "ellipse_N": {
                "center": "[x,y]",
                "major": "float",
                "minor": "float",
                "angle": "deg",
            },
            "elliptical_arc_N": {
                "start": "[x,y]",
                "end": "[x,y]",
                "major": "float",
                "minor": "float",
                "angle": "deg",
                "large_arc": "bool",
                "sweep": "bool",
            },
            "nurbs_N": {
                "degree": "int",
                "periodic": "bool",
                "controls": "[[x,y],...]",
                "weights": "[float,...]",
                "knots": "[float,...]",
            },
        },
        "constraint_types": list(CONSTRAINT_TYPES),
        "constraint_entry_formats": {
            "Coincident": [["line_1.end", "line_2.start"]],
            "Horizontal": ["line_1", ["line_1.start", "line_1.end"]],
            "Vertical": ["line_2", ["line_2.start", "line_2.end"]],
            "Perpendicular": [["line_1", "line_2"]],
            "Parallel": [["line_1", "line_3"]],
            "Equal": [["line_1", "line_3"]],
            "Tangent": [["arc_1", "line_1"]],
            "Concentric": [["circle_1", "circle_2"]],
            "Fix": ["line_1.start", "line_1"],
            "Angle": [["line_1", "line_2", "90 deg"]],
            "Length": [
                ["line_1", "20 mm"],
                [
                    "line_1",
                    {
                        "length": "20 mm",
                        "alias": "base_length",
                        "role": "sketch_dimension",
                    },
                ],
            ],
            "Radius": [
                ["arc_1", "5 mm"],
                [
                    "arc_1",
                    {
                        "radius": "5 mm",
                        "alias": "corner_radius",
                        "role": "sketch_dimension",
                    },
                ],
            ],
            "Diameter": [
                ["circle_1", "10 mm"],
                [
                    "circle_1",
                    {
                        "diameter": "10 mm",
                        "alias": "hole_diameter",
                        "role": "sketch_dimension",
                    },
                ],
            ],
            "Distance": [
                ["line_1.end", "line_2.start", "5 mm"],
                [
                    "line_1.end",
                    "line_2.start",
                    {"length": "5 mm", "direction": "HORIZONTAL"},
                ],
                [
                    "line_3.end",
                    "line_4.start",
                    {"length": "1.5 mm", "direction": "VERTICAL"},
                ],
                [
                    "line_1.end",
                    "line_2.start",
                    {
                        "length": "5 mm",
                        "direction": "HORIZONTAL",
                        "alias": "edge_offset",
                        "role": "sketch_dimension",
                    },
                ],
            ],
        },
        "parametric_linkage": {
            "sketch_dimensions": (
                "When the user asks for parametric/tunable/linked sketch "
                "dimensions, write the dimension as a dict with alias. "
                "Supported dimension dict keys include length, radius, "
                "diameter, angle, value, expression, alias, label, role, min, "
                "max, and default."
            ),
            "diameter_note": (
                "Diameter and Radius are separate sketch dimension semantics. "
                "Diameter entries create FreeCAD Sketcher Diameter constraints "
                "and bind aliases directly to the diameter value; Radius entries "
                "create Radius constraints and bind aliases directly to radius."
            ),
            "diameter_example": [
                "circle_1",
                {
                    "diameter": "5 mm",
                    "alias": "hole_diameter",
                    "role": "sketch_dimension",
                },
            ],
            "angle_example": [
                "line_1",
                "line_2",
                {
                    "angle": 90,
                    "alias": "bend_angle",
                    "role": "sketch_dimension",
                },
            ],
            "extrude_feature_example": {
                "type": "extrude",
                "sketch_name": "PlateProfile",
                "operation": "NewBody",
                "params": {"towards": 6.0, "opposite": 0.0},
                "param_aliases": {"towards": "thickness"},
                "feature_name": "PlateSolid",
            },
            "safety_note": (
                "The executor may safely skip alias expression binding when "
                "the sketch solver reports conflicts, over-constraint, or an "
                "opened profile. In that case geometry is still generated and "
                "bound_params reports an unbound role with diagnostics."
            ),
        },
        "distance_semantics": {
            "plain": (
                "[point_or_entity_a, point_or_entity_b, length] creates a "
                "normal Euclidean Distance constraint."
            ),
            "horizontal": (
                '[point_a, point_b, {"length": value, "direction": '
                '"HORIZONTAL"}] creates a signed DistanceX constraint.'
            ),
            "vertical": (
                '[point_a, point_b, {"length": value, "direction": '
                '"VERTICAL"}] creates a signed DistanceY constraint.'
            ),
            "minimum": (
                '[ref_a, ref_b, {"length": value, "direction": "MINIMUM"}] '
                "documents ordinary minimum-distance intent while preserving "
                "plain Distance execution."
            ),
        },
        "point_ref_format": "entity.point, e.g. line_1.start or circle_1.center; 'origin' is the sketch-local origin",
        "examples": [minimal_fabrication_plan_example()],
    }


def validate_fabrication_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a FabricationPlan dict without executing FreeCAD."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def add_error(path: str, message: str, code: str = "invalid") -> None:
        errors.append({"path": path, "message": message, "code": code})

    def add_warning(path: str, message: str, code: str = "warning") -> None:
        warnings.append({"path": path, "message": message, "code": code})

    if not isinstance(plan, dict):
        return {
            "valid": False,
            "errors": [
                {"path": "$", "message": "plan must be a dict", "code": "type_error"}
            ],
            "warnings": [],
            "summary": "FabricationPlan validation failed with 1 error(s).",
        }

    for field_name in ("coordinate_systems", "sketches", "features"):
        if field_name not in plan:
            add_error(f"$.{field_name}", "required field is missing", "missing")
        elif not isinstance(plan[field_name], list):
            add_error(f"$.{field_name}", "must be a list", "type_error")

    if errors:
        return _fabrication_validation_result(errors, warnings)

    try:
        FabricationPlan.from_dict(plan)
    except Exception as exc:
        add_error("$", f"dataclass deserialisation failed: {exc}", "deserialisation")
        return _fabrication_validation_result(errors, warnings)

    cs_names = _validate_coordinate_systems(plan["coordinate_systems"], add_error)
    sketch_names, entity_names_by_sketch = _validate_sketches(
        plan["sketches"],
        cs_names,
        add_error,
        add_warning,
    )
    feature_names = _validate_features(
        plan["features"],
        sketch_names,
        add_error,
        add_warning,
    )
    for index, cs in enumerate(plan["coordinate_systems"]):
        if isinstance(cs, dict):
            validate_coordinate_system_payload(
                cs,
                add_error,
                f"$.coordinate_systems[{index}]",
                known_targets=feature_names,
            )
    _validate_finishes(plan.get("finishes", []), add_error)

    for index, sketch in enumerate(plan["sketches"]):
        name = str(sketch.get("sketch_name") or f"#{index}")
        entities = entity_names_by_sketch.get(name, set())
        _validate_constraints(
            sketch.get("constraints", {}),
            entities,
            f"$.sketches[{index}].constraints",
            add_error,
            add_warning,
        )

    known_feature_refs = feature_names | {"<pre-existing FreeCAD object>"}
    if not feature_names and plan["features"]:
        add_warning(
            "$.features",
            "No feature_name values were supplied; later booleans cannot reference anonymous generated features reliably.",
            "anonymous_features",
        )
    if known_feature_refs:
        _ = known_feature_refs

    return _fabrication_validation_result(errors, warnings)


def _fabrication_validation_result(
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    valid = not errors
    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "summary": (
            "FabricationPlan validation passed."
            if valid
            else f"FabricationPlan validation failed with {len(errors)} error(s)."
        ),
    }


def _is_number_list(value: Any, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(isinstance(item, int | float) for item in value)
    )


def _validate_coordinate_systems(
    coordinate_systems: list[Any],
    add_error: Any,
) -> set[str]:
    names: set[str] = set()
    for index, cs in enumerate(coordinate_systems):
        path = f"$.coordinate_systems[{index}]"
        if not isinstance(cs, dict):
            add_error(path, "coordinate system must be a dict", "type_error")
            continue
        validate_coordinate_system_payload(cs, add_error, path)
        name = cs.get("name")
        if name is not None:
            if not isinstance(name, str) or not name:
                add_error(
                    f"{path}.name",
                    "must be a non-empty string when provided",
                    "type_error",
                )
            elif name in names:
                add_error(
                    f"{path}.name",
                    f"duplicate coordinate system name {name!r}",
                    "duplicate",
                )
            else:
                names.add(name)
    return names


def _validate_sketches(  # noqa: PLR0912
    sketches: list[Any],
    cs_names: set[str],
    add_error: Any,
    add_warning: Any,
) -> tuple[set[str], dict[str, set[str]]]:
    sketch_names: set[str] = set()
    entity_names_by_sketch: dict[str, set[str]] = {}
    for index, sketch in enumerate(sketches):
        path = f"$.sketches[{index}]"
        if not isinstance(sketch, dict):
            add_error(path, "sketch must be a dict", "type_error")
            continue
        name = sketch.get("sketch_name") or f"#{index}"
        if sketch.get("sketch_name"):
            if name in sketch_names:
                add_error(
                    f"{path}.sketch_name",
                    f"duplicate sketch name {name!r}",
                    "duplicate",
                )
            sketch_names.add(str(name))
        else:
            add_warning(
                f"{path}.sketch_name",
                "named sketches are recommended for feature references",
            )

        cs_ref = sketch.get("coordinate_system_name")
        if "coordinate_system" in sketch:
            add_error(
                f"{path}.coordinate_system",
                "inline coordinate_system is retired; declare it in top-level coordinate_systems and reference coordinate_system_name",
                "retired_field",
            )
        if "attachment_support" in sketch:
            add_error(
                f"{path}.attachment_support",
                "sketch-level attachment_support is retired; attach a named coordinate system instead",
                "retired_field",
            )
        if not cs_ref:
            add_error(
                path,
                "sketch needs coordinate_system_name",
                "missing_plane",
            )
        if cs_ref and cs_ref not in cs_names:
            add_error(
                f"{path}.coordinate_system_name",
                f"unknown coordinate system {cs_ref!r}",
                "unknown_ref",
            )

        entities = sketch.get("sketch")
        if not isinstance(entities, dict) or not entities:
            add_error(f"{path}.sketch", "must be a non-empty entity dict", "type_error")
            entity_names_by_sketch[str(name)] = set()
            continue
        entity_names = set()
        for entity_name, spec in entities.items():
            entity_path = f"{path}.sketch.{entity_name}"
            if not isinstance(entity_name, str) or not entity_name.startswith(
                SKETCH_ENTITY_PREFIXES
            ):
                add_error(
                    entity_path,
                    "entity name must use a supported HistCAD prefix",
                    "bad_entity_name",
                )
            if not isinstance(spec, dict):
                add_error(entity_path, "entity spec must be a dict", "type_error")
            entity_names.add(str(entity_name))
        entity_names_by_sketch[str(name)] = entity_names
    return sketch_names, entity_names_by_sketch


def _validate_features(  # noqa: PLR0912
    features: list[Any],
    sketch_names: set[str],
    add_error: Any,
    add_warning: Any,
) -> set[str]:
    feature_names: set[str] = set()
    for index, feature in enumerate(features):
        path = f"$.features[{index}]"
        if not isinstance(feature, dict):
            add_error(path, "feature must be a dict", "type_error")
            continue
        feature_type = feature.get("type")
        params = feature.get("params")
        if feature_type not in FEATURE_TYPES:
            add_error(
                f"{path}.type",
                f"unsupported feature type {feature_type!r}",
                "unsupported",
            )
        if not isinstance(params, dict):
            add_error(f"{path}.params", "must be a dict", "type_error")
            params = {}
        feature_name = feature.get("feature_name")
        if feature_name:
            if feature_name in feature_names:
                add_error(
                    f"{path}.feature_name",
                    f"duplicate feature name {feature_name!r}",
                    "duplicate",
                )
            feature_names.add(str(feature_name))

        if feature_type in {"extrude", "revolve", "helix"}:
            sketch_name = feature.get("sketch_name")
            if not sketch_name:
                add_error(
                    f"{path}.sketch_name", "required for this feature type", "missing"
                )
            elif sketch_names and sketch_name not in sketch_names:
                add_error(
                    f"{path}.sketch_name",
                    f"unknown sketch {sketch_name!r}",
                    "unknown_ref",
                )
            if feature.get("operation", "NewBody") != "NewBody":
                add_warning(
                    f"{path}.operation",
                    "canonical fabrication currently expects NewBody",
                )
        if (
            feature_type == "extrude"
            and "towards" not in params
            and "opposite" not in params
        ):
            add_error(
                f"{path}.params", "extrude requires towards and/or opposite", "missing"
            )
        if feature_type == "boolean":
            for key in ("base_object_name", "tool_object_name"):
                if not params.get(key):
                    add_error(
                        f"{path}.params.{key}",
                        "boolean feature requires this reference",
                        "missing",
                    )
            operation = feature.get("operation") or params.get("operation")
            if operation not in {"Join", "Cut", "Intersect"}:
                add_error(
                    f"{path}.operation",
                    "boolean operation must be Join, Cut, or Intersect",
                    "unsupported",
                )
        if feature_type == "revolve":
            for key in ("axis", "start", "end"):
                if key not in params:
                    add_error(
                        f"{path}.params.{key}",
                        "revolve requires this parameter",
                        "missing",
                    )
        if feature_type == "helix":
            for key in ("axis", "pitch", "turns"):
                if key not in params:
                    add_error(
                        f"{path}.params.{key}",
                        "helix requires this parameter",
                        "missing",
                    )
    return feature_names


def _validate_finishes(finishes: Any, add_error: Any) -> None:
    if finishes is None:
        return
    if not isinstance(finishes, list):
        add_error("$.finishes", "must be a list when provided", "type_error")
        return
    for index, finish in enumerate(finishes):
        path = f"$.finishes[{index}]"
        if not isinstance(finish, dict):
            add_error(path, "finish must be a dict", "type_error")
            continue
        if finish.get("type") not in FINISH_TYPES:
            add_error(
                f"{path}.type",
                f"unsupported finish type {finish.get('type')!r}",
                "unsupported",
            )
        if not isinstance(finish.get("near_points"), list):
            add_error(
                f"{path}.near_points", "must be a list of 3D points", "type_error"
            )
        if not isinstance(finish.get("params"), dict):
            add_error(f"{path}.params", "must be a dict", "type_error")


def _validate_constraints(
    constraints: Any,
    entities: set[str],
    path: str,
    add_error: Any,
    add_warning: Any,
) -> None:
    if constraints in (None, {}):
        return
    if not isinstance(constraints, dict):
        add_error(path, "constraints must be a dict", "type_error")
        return
    for constraint_type, values in constraints.items():
        constraint_path = f"{path}.{constraint_type}"
        if constraint_type not in CONSTRAINT_TYPES:
            add_error(
                constraint_path,
                f"unsupported constraint type {constraint_type!r}",
                "unsupported",
            )
            continue
        _validate_constraint_shape(
            str(constraint_type),
            values,
            entities,
            constraint_path,
            add_error,
            add_warning,
        )
        _walk_constraint_refs(values, entities, constraint_path, add_error)


def _walk_constraint_refs(
    value: Any, entities: set[str], path: str, add_error: Any
) -> None:
    if isinstance(value, str):
        entity = value.split(".", maxsplit=1)[0]
        if entity.startswith(SKETCH_ENTITY_PREFIXES) and entity not in entities:
            add_error(path, f"unknown sketch entity reference {value!r}", "unknown_ref")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _walk_constraint_refs(item, entities, f"{path}[{index}]", add_error)


def _constraint_entries(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _is_entity_ref(value: Any, entities: set[str]) -> bool:
    return isinstance(value, str) and "." not in value and value in entities


def _is_point_ref(value: Any, entities: set[str]) -> bool:
    return value == "origin" or (
        isinstance(value, str)
        and "." in value
        and value.split(".", maxsplit=1)[0] in entities
    )


def _is_entity_or_point_ref(value: Any, entities: set[str]) -> bool:
    return _is_entity_ref(value, entities) or _is_point_ref(value, entities)


def _looks_like_dimension(value: Any) -> bool:
    if isinstance(value, int | float):
        return True
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(
            key in value
            for key in ("length", "value", "radius", "diameter", "expression", "alias")
        )
    return False


def _validate_ref(
    ref: Any,
    entities: set[str],
    path: str,
    add_error: Any,
    *,
    point_only: bool = False,
    entity_only: bool = False,
) -> None:
    if point_only:
        if not _is_point_ref(ref, entities):
            add_error(path, f"expected point reference, got {ref!r}", "bad_ref_shape")
        return
    if entity_only:
        if not _is_entity_ref(ref, entities):
            add_error(path, f"expected entity reference, got {ref!r}", "bad_ref_shape")
        return
    if not _is_entity_or_point_ref(ref, entities):
        add_error(path, f"expected sketch reference, got {ref!r}", "bad_ref_shape")


def _validate_pair_entry(
    entry: Any,
    entities: set[str],
    path: str,
    add_error: Any,
    *,
    point_only: bool = False,
    entity_only: bool = False,
) -> None:
    if not isinstance(entry, list | tuple) or len(entry) != 2:
        add_error(path, "entry must be a 2-item list", "bad_arity")
        return
    _validate_ref(
        entry[0],
        entities,
        f"{path}[0]",
        add_error,
        point_only=point_only,
        entity_only=entity_only,
    )
    _validate_ref(
        entry[1],
        entities,
        f"{path}[1]",
        add_error,
        point_only=point_only,
        entity_only=entity_only,
    )


def _validate_constraint_shape(  # noqa: PLR0912
    constraint_type: str,
    values: Any,
    entities: set[str],
    path: str,
    add_error: Any,
    add_warning: Any,
) -> None:
    for index, entry in enumerate(_constraint_entries(values)):
        entry_path = f"{path}[{index}]"

        if constraint_type in {"Coincident"}:
            _validate_pair_entry(
                entry, entities, entry_path, add_error, point_only=True
            )
            continue

        if constraint_type in {"Perpendicular", "Parallel", "Equal", "Concentric"}:
            _validate_pair_entry(
                entry, entities, entry_path, add_error, entity_only=True
            )
            continue

        if constraint_type in {"Tangent", "Normal"}:
            if not isinstance(entry, list | tuple) or len(entry) not in {2, 4}:
                add_error(
                    entry_path,
                    "entry must be a 2-item entity pair or 4-item point-specific list",
                    "bad_arity",
                )
                continue
            point_specific = len(entry) == 4
            for ref_index, ref in enumerate(entry):
                _validate_ref(
                    ref,
                    entities,
                    f"{entry_path}[{ref_index}]",
                    add_error,
                    point_only=point_specific,
                    entity_only=not point_specific,
                )
            continue

        if constraint_type in {"Horizontal", "Vertical"}:
            if isinstance(entry, str):
                _validate_ref(entry, entities, entry_path, add_error, entity_only=True)
            else:
                _validate_pair_entry(
                    entry, entities, entry_path, add_error, point_only=True
                )
            continue

        if constraint_type == "Fix":
            _validate_ref(entry, entities, entry_path, add_error)
            continue

        if constraint_type in {
            "Length",
            "Radius",
            "Diameter",
            "MajorRadius",
            "MinorRadius",
        }:
            if not isinstance(entry, list | tuple) or len(entry) != 2:
                add_error(entry_path, "entry must be [entity, value]", "bad_arity")
                continue
            _validate_ref(
                entry[0], entities, f"{entry_path}[0]", add_error, entity_only=True
            )
            if not _looks_like_dimension(entry[1]):
                add_error(
                    f"{entry_path}[1]",
                    f"{constraint_type} value must be a number, string, or dimension dict",
                    "bad_value",
                )
            continue

        if constraint_type == "Angle":
            if not isinstance(entry, list | tuple) or len(entry) != 3:
                add_error(
                    entry_path, "entry must be [entity_a, entity_b, value]", "bad_arity"
                )
                continue
            _validate_ref(
                entry[0], entities, f"{entry_path}[0]", add_error, entity_only=True
            )
            _validate_ref(
                entry[1], entities, f"{entry_path}[1]", add_error, entity_only=True
            )
            if not _looks_like_dimension(entry[2]):
                add_error(f"{entry_path}[2]", "Angle value is missing", "bad_value")
            continue

        if constraint_type == "Distance":
            if not isinstance(entry, list | tuple) or len(entry) != 3:
                add_error(
                    entry_path,
                    "entry must be [ref_a, ref_b, value_or_dimension_dict]",
                    "bad_arity",
                )
                continue
            _validate_ref(entry[0], entities, f"{entry_path}[0]", add_error)
            _validate_ref(entry[1], entities, f"{entry_path}[1]", add_error)
            value = entry[2]
            if entry[0] == entry[1]:
                add_error(
                    entry_path,
                    "Distance cannot reference the same point twice; use origin and the target point for local offsets",
                    "self_distance",
                )
            if not _looks_like_dimension(value):
                add_error(
                    f"{entry_path}[2]",
                    "Distance value must be a number, string, or dimension dict",
                    "bad_value",
                )
                continue
            if isinstance(value, dict):
                direction = value.get("direction")
                if direction is not None:
                    normalized = str(direction).upper()
                    if normalized not in {"HORIZONTAL", "VERTICAL", "MINIMUM"}:
                        add_error(
                            f"{entry_path}[2].direction",
                            "Distance.direction must be HORIZONTAL, VERTICAL, or MINIMUM",
                            "bad_direction",
                        )
                    elif normalized in {"HORIZONTAL", "VERTICAL"}:
                        for ref_index, ref in enumerate(entry[:2]):
                            _validate_ref(
                                ref,
                                entities,
                                f"{entry_path}[{ref_index}]",
                                add_error,
                                point_only=True,
                            )
            elif all(_is_point_ref(ref, entities) for ref in entry[:2]):
                add_warning(
                    f"{entry_path}[2]",
                    "Plain string/number Distance is Euclidean. If source text says direction HORIZONTAL or VERTICAL, use {'length': value, 'direction': ...}.",
                    "plain_distance_no_direction",
                )
            continue

        if constraint_type in {"Midpoint", "Mirror"}:
            if not isinstance(entry, list | tuple) or len(entry) < 2:
                add_error(
                    entry_path,
                    "entry must be a list with at least two refs",
                    "bad_arity",
                )
                continue
            for ref_index, ref in enumerate(entry):
                _validate_ref(ref, entities, f"{entry_path}[{ref_index}]", add_error)
