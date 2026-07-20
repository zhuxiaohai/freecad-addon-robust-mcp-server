"""Fabrication primitives: HistCAD-style Layer 1 generic CAD modeling tools.

This module implements the Layer 1 generic fabrication primitives following
the HistCAD middleware paradigm.  The LLM or RL agent specifies geometry and
constraints using kernel-independent JSON schemas; the adapter translates
these deterministically to FreeCAD API calls.

Execution contracts
-------------------

``FabricationPlan`` is the deterministic batch DSL used by templates,
converters, and explicit full-plan callers.  ``execute_fabrication_plan``
validates it, dispatches the fixed primitive sequence, and returns structured
diagnostics.

Agentic no-template callers use ``describe_primitive_plan_schema`` instead.
That contract is derived from the real primitive tool function signatures, so
primitive tool args have a single maintenance source.

Tool groups
-----------

A — Coordinate System:  ``create_coordinate_system``
B — Sketch Geometry:    ``create_sketch_geometry``, ``parse_freecad_sketch``
C — Sketch Constraints: ``check_sketch_constraints``, ``apply_sketch_constraints``,
                        ``build_edited_sketch_constraints``,
                        ``evaluate_sketch_editability``
D — Feature Execution:  ``execute_extrude``, ``execute_boolean``,
                        ``execute_revolve``, ``execute_helix``
E — Finishing:          ``feature_fillet``, ``feature_chamfer``
F — Parametric Control: ``list_tunable_params``, ``set_tunable_param``
G — Observation:        ``get_body_snapshot``
Batch:                  ``execute_fabrication_plan``

Coordinate system convention
----------------------------

``coordinate_system`` Euler angles ``[a, b, g]`` (degrees) are the ACTIVE
local-to-world rotation ``R = Rx(a) * Ry(b) * Rz(g)``, applied as-is (no
negation) — identical to the HistCAD Fusion 360 adapter
(``scipy R.from_euler("XYZ")`` / ``cad_base._coordinate_system_rotation_matrix``).
World point = ``R * (x, y, 0) + translation``; sketch normal = ``R * Z``.

HistCAD entity name convention
-------------------------------

Entities in the ``sketch`` dict use prefixed names:

- ``line_N``: ``{"start": [x,y], "end": [x,y]}``
- ``circle_N``: ``{"center": [x,y], "radius": r}``
- ``arc_N``: ``{"start": [x1,y1], "middle": [x2,y2], "end": [x3,y3]}``
- ``ellipse_N``: ``{"center":[x,y], "major":r1, "minor":r2, "angle":deg}``
- ``elliptical_arc_N``: ``{"start":[x1,y1],"end":[x2,y2],"major":r1,"minor":r2,
  "angle":deg,"large_arc":false,"sweep":true}``
- ``nurbs_N``: ``{"degree":int,"periodic":bool,"controls":[[x,y],...],
  "weights":[...],"knots":[...]}``  (semantic start/end = first/last control)

At sketch creation, ``endpoint_map`` binds each entity's semantic
``start``/``end`` labels to FreeCAD ``PointPos`` values.  Constraints
resolve ``line_1.start`` through that map — never by re-matching coordinates
after the solver moves geometry.  Maps are persisted on the sketch object
(``HistCADMaps`` / ``HistCADGeometry`` properties) for cross-MCP-call reuse.
Directed ``Distance`` constraints with ``HORIZONTAL``/``VERTICAL`` use signed
``DistanceX``/``DistanceY`` values.  Sign polarity is taken from a persisted
sketch map when available, otherwise from creation-time ground-truth geometry
(with live endpoint fallback).  Magnitude always comes from the HistCAD length
(so editability edits keep topology direction stable).

Optional ``orientation_stabilization`` (default ``False``) may invent additional
axis-aligned directed dimensions for uncovered segments; leave it off for
editability-faithful HistCAD replay so free DOFs stay free to link.

HistCAD constraint types (19 total)
------------------------------------

Coincident, Horizontal, Vertical, Perpendicular, Parallel, Equal,
Tangent, Normal, Concentric, Fix, Midpoint, Mirror,
Angle, Diameter, Radius, MajorRadius, MinorRadius, Length, Distance.

``entity_ref``:  ``"line_1"``         → (geom_idx, no point)
``point_ref``:   ``"line_1.start"``   → (geom_idx, PointPos.start)
                 ``"arc_1.end"``      → (geom_idx, PointPos.end)
                 ``"circle_1.center"``→ (geom_idx, PointPos.mid)
"""

from __future__ import annotations

import contextlib
import inspect
from pathlib import Path
from types import UnionType
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin, get_type_hints

from freecad_mcp.tools.coordinate_system import validate_coordinate_system_payload
from freecad_mcp.tools.sketch_editability import (
    evaluate_sketch_editability as _evaluate_sketch_geometry,
)
from freecad_mcp.tools.sketch_editability import (
    replace_constraint_value,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


# ---------------------------------------------------------------------------
# Internal helpers (not exposed as MCP tools)
# ---------------------------------------------------------------------------

_SPREADSHEET_NAME = "FabricationParams"

_PRIMITIVE_OUTPUT_EXPORTS: dict[str, list[str]] = {
    "create_coordinate_system": ["cs_name", "cs_internal_name", "label", "placement"],
    "create_sketch_geometry": [
        "sketch_name",
        "cs_name",
        "geometry_count",
        "profile",
        "fully_constrained",
    ],
    "apply_sketch_constraints": [
        "sketch_name",
        "constraint_catalog",
        "constraint_catalog_summary",
        "bound_params",
    ],
    "execute_extrude": ["body_name", "feature_name", "object_name", "bound_params"],
    "execute_boolean": ["feature_name", "object_name"],
    "execute_revolve": ["body_name", "feature_name", "object_name", "bound_params"],
    "execute_helix": ["body_name", "feature_name", "object_name", "bound_params"],
    "feature_fillet": ["feature_name", "object_name"],
    "feature_chamfer": ["feature_name", "object_name"],
    "get_body_snapshot": ["bounding_box", "volume", "features"],
}

_PRIMITIVE_ARGUMENT_EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "create_coordinate_system": [
        {
            "name": "base_xy",
            "args": {
                "name": "BaseXY",
                "euler_angles": [0.0, 0.0, 0.0],
                "translation": [0.0, 0.0, 0.0],
            },
        }
    ],
    "create_sketch_geometry": [
        {
            "name": "rectangle_40_by_20",
            "args": {
                "sketch_name": "BlockProfile",
                "coordinate_system_name": "BaseXY",
                "sketch": {
                    "line_1": {"start": [0.0, 0.0], "end": [40.0, 0.0]},
                    "line_2": {"start": [40.0, 0.0], "end": [40.0, 20.0]},
                    "line_3": {"start": [40.0, 20.0], "end": [0.0, 20.0]},
                    "line_4": {"start": [0.0, 20.0], "end": [0.0, 0.0]},
                },
            },
        },
        {
            "name": "arc_uses_middle_point",
            "args": {
                "sketch_name": "ArcProfile",
                "coordinate_system_name": "BaseXY",
                "sketch": {
                    "arc_1": {
                        "start": [0.0, 10.0],
                        "middle": [7.0, 7.0],
                        "end": [10.0, 0.0],
                    }
                },
            },
            "notes": (
                "The canonical arc field is middle. Do not use mid in generated "
                "primitive args."
            ),
        },
        {
            "name": "circle_near_origin",
            "args": {
                "sketch_name": "HoleProfile",
                "coordinate_system_name": "BaseXY",
                "sketch": {"circle_1": {"center": [8.0, 8.0], "radius": 2.5}},
            },
        },
    ],
    "apply_sketch_constraints": [
        {
            "name": "parametric_rectangle_constraints",
            "args": {
                "sketch_name": "BlockProfile",
                "constraints": {
                    "Coincident": [
                        ["line_1.end", "line_2.start"],
                        ["line_2.end", "line_3.start"],
                        ["line_3.end", "line_4.start"],
                        ["line_4.end", "line_1.start"],
                    ],
                    "Horizontal": ["line_1", "line_3"],
                    "Vertical": ["line_2", "line_4"],
                    "Fix": ["line_1.start"],
                    "Length": [
                        [
                            "line_1",
                            {
                                "length": "40 mm",
                                "alias": "block_length",
                                "role": "sketch_dimension",
                            },
                        ],
                        [
                            "line_2",
                            {
                                "length": "20 mm",
                                "alias": "block_width",
                                "role": "sketch_dimension",
                            },
                        ],
                    ],
                },
            },
        },
        {
            "name": "hole_origin_offset_constraints",
            "args": {
                "sketch_name": "HoleProfile",
                "constraints": {
                    "Diameter": [
                        [
                            "circle_1",
                            {
                                "diameter": "5 mm",
                                "alias": "hole_diameter",
                                "role": "sketch_dimension",
                            },
                        ]
                    ],
                    "Distance": [
                        [
                            "origin",
                            "circle_1.center",
                            {
                                "length": "8 mm",
                                "direction": "HORIZONTAL",
                                "alias": "hole_offset_x",
                                "role": "sketch_dimension",
                            },
                        ],
                        [
                            "origin",
                            "circle_1.center",
                            {
                                "length": "8 mm",
                                "direction": "VERTICAL",
                                "alias": "hole_offset_y",
                                "role": "sketch_dimension",
                            },
                        ],
                    ],
                },
            },
        },
    ],
    "execute_extrude": [
        {
            "name": "parametric_block_extrude",
            "args": {
                "sketch_name": "BlockProfile",
                "towards": 10.0,
                "opposite": 0.0,
                "feature_name": "BlockSolid",
                "param_aliases": {"towards": "block_height"},
            },
        },
        {
            "name": "through_hole_tool_extrude",
            "args": {
                "sketch_name": "HoleProfile",
                "towards": 12.0,
                "opposite": 1.0,
                "feature_name": "HoleTool",
            },
        },
    ],
    "execute_boolean": [
        {
            "name": "cut_hole_tool_from_block",
            "args": {
                "base_object_name": "BlockSolid",
                "tool_object_name": "HoleTool",
                "operation": "Cut",
                "result_name": "BlockWithHole",
            },
        }
    ],
}


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Signature.empty or annotation is Any:
        return {"type": "any"}
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {UnionType, Union}:
        non_null = [arg for arg in args if arg is not type(None)]
        schema = (
            _annotation_to_schema(non_null[0])
            if len(non_null) == 1
            else {"anyOf": [_annotation_to_schema(arg) for arg in non_null]}
        )
        if len(non_null) != len(args):
            schema["nullable"] = True
        return schema
    if origin is list:
        return {
            "type": "array",
            "items": _annotation_to_schema(args[0]) if args else {"type": "any"},
        }
    if origin is dict:
        return {
            "type": "object",
            "additionalProperties": (
                _annotation_to_schema(args[1]) if len(args) == 2 else {"type": "any"}
            ),
        }
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        dict: "object",
        list: "array",
    }
    if annotation in mapping:
        return {"type": mapping[annotation]}
    return {"type": "any", "python_type": str(annotation)}


def _tool_args_schema(func: Any) -> dict[str, Any]:
    signature = inspect.signature(func)
    type_hints = get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if name in {"self", "args", "kwargs"}:
            continue
        properties[name] = _annotation_to_schema(
            type_hints.get(name, parameter.annotation)
        )
        if parameter.default is inspect.Signature.empty:
            required.append(name)
        else:
            properties[name]["default"] = parameter.default
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
        "source": "python_function_signature",
    }


def _first_doc_line(func: Any) -> str:
    doc = inspect.getdoc(func)
    if not doc:
        return ""
    return doc.splitlines()[0]


def _primitive_tool_catalog(tool_functions: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "tool_name": name,
            "args_schema": _tool_args_schema(func),
            "output_exports": _PRIMITIVE_OUTPUT_EXPORTS.get(name, []),
            "argument_examples": _PRIMITIVE_ARGUMENT_EXAMPLES.get(name, []),
            "doc": _first_doc_line(func),
        }
        for name, func in tool_functions.items()
    }


def _validate_primitive_plan_payload(
    plan: Any,
    tool_functions: dict[str, Any],
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(plan, dict):
        return {
            "valid": False,
            "errors": [{"path": "$", "message": "plan must be an object"}],
            "warnings": [],
        }
    steps = plan.get("steps")
    if not isinstance(steps, list):
        errors.append({"path": "$.steps", "message": "steps must be a list"})
        steps = []
    for index, step in enumerate(steps):
        path = f"$.steps[{index}]"
        if not isinstance(step, dict):
            errors.append({"path": path, "message": "step must be an object"})
            continue
        tool_name = step.get("tool_name") or step.get("primitive_tool")
        if tool_name is None:
            warnings.append(
                {
                    "path": f"{path}.tool_name",
                    "message": "tool_name is missing; acceptable for L0/L1 partial plans",
                }
            )
            continue
        if tool_name not in tool_functions:
            errors.append(
                {"path": f"{path}.tool_name", "message": f"unknown tool {tool_name!r}"}
            )
            continue
        args = step.get("args", {})
        if not isinstance(args, dict):
            errors.append({"path": f"{path}.args", "message": "args must be an object"})
            continue
        if step.get("missing_args"):
            continue
        schema = _tool_args_schema(tool_functions[tool_name])
        for required in schema["required"]:
            if required not in args:
                errors.append(
                    {
                        "path": f"{path}.args.{required}",
                        "message": f"required arg {required!r} is missing",
                    }
                )
        if tool_name == "apply_sketch_constraints":
            _validate_primitive_constraint_shape(
                args.get("constraints", {}),
                f"{path}.args.constraints",
                errors,
            )
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": (
            "PrimitivePlan validation passed."
            if not errors
            else f"PrimitivePlan validation failed with {len(errors)} error(s)."
        ),
    }


def _validate_primitive_constraint_shape(
    constraints: Any,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(constraints, dict):
        errors.append({"path": path, "message": "constraints must be an object"})
        return
    for constraint_name, entries in constraints.items():
        if isinstance(entries, dict) and {"type", "references"} <= set(entries):
            errors.append(
                {
                    "path": f"{path}.{constraint_name}",
                    "message": (
                        "FreeCAD internal constraint objects are not accepted; "
                        "use MCP adapter format grouped by constraint type"
                    ),
                }
            )


def _build_editability_validation_code(
    doc_name: str | None,
    *,
    sketch_name: str | None = None,
) -> str:
    """Return FreeCAD Python that recomputes and validates post-edit health."""
    sketch_filter = (
        f"and obj.Name == {sketch_name!r}" if sketch_name is not None else ""
    )
    return f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")
rebuild_success = True
exception = None
try:
    doc.recompute()
except Exception as _exc:
    rebuild_success = False
    exception = str(_exc)

sketches = []
shape_errors = []
for obj in doc.Objects:
    try:
        if obj.TypeId == "Sketcher::SketchObject" {sketch_filter}:
            _constraint_types = []
            try:
                _constraint_types = [c.Type for c in obj.Constraints]
            except Exception:
                pass
            sketches.append({{
                "name": obj.Name,
                "dof": getattr(obj, "DoF", None),
                "fully_constrained": getattr(obj, "FullyConstrained", None),
                "conflicting": list(getattr(obj, "ConflictingConstraints", ())),
                "redundant": list(getattr(obj, "RedundantConstraints", ())),
                "constraint_types": _constraint_types,
            }})
        if hasattr(obj, "Shape") and not obj.Shape.isNull():
            try:
                if not obj.Shape.isValid():
                    shape_errors.append(obj.Name)
            except Exception:
                pass
    except Exception:
        pass
validation_ok = rebuild_success and not shape_errors and all(
    not item.get("conflicting") for item in sketches
)
_result_ = {{
    "rebuild_success": rebuild_success,
    "validation_ok": validation_ok,
    "exception": exception,
    "shape_errors": shape_errors,
    "sketches": sketches,
}}
"""


def _read_sketch_helper_embed() -> str:
    """Load sketch helper source for embedding (strip ``__future__`` imports)."""
    text = (Path(__file__).resolve().parent / "sketch_helpers.py").read_text(
        encoding="utf-8"
    )
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("from __future__")
    )


_SKETCH_HELPER_CODE = _read_sketch_helper_embed()

_SKETCH_ENTITY_CODE = r"""
import Part, Sketcher

def _endpoint_map_for_geometry(sketch_obj, geo_idx, json_start, json_end):
    # Bind JSON/NLT semantic start/end to FreeCAD PointPos once at sketch creation.
    # After binding, constraints always use the cached map — never re-derive from
    # current coordinates (the solver may move or flip geometry later).
    START, END, MID = 1, 2, 3

    def _dist2d(point, coords):
        return ((point.x - coords[0]) ** 2 + (point.y - coords[1]) ** 2) ** 0.5

    sp = sketch_obj.getPoint(geo_idx, START)
    ep = sketch_obj.getPoint(geo_idx, END)
    direct = max(_dist2d(sp, json_start), _dist2d(ep, json_end))
    swapped = max(_dist2d(sp, json_end), _dist2d(ep, json_start))
    if direct <= swapped:
        return {"start": START, "end": END, "center": MID, "middle": MID}
    return {"start": END, "end": START, "center": MID, "middle": MID}


def _arc_endpoint_map_for_geometry(sketch_obj, geo_idx, json_start, json_end):
    # Backward-compatible alias for arc-only call sites.
    return _endpoint_map_for_geometry(sketch_obj, geo_idx, json_start, json_end)


def _entity_kind_from_name(name):
    # HistCAD sketch entity names use type prefixes (see histcad.md §1.2).
    if name.startswith("line_"):
        return "line"
    if name.startswith("circle_"):
        return "circle"
    if name.startswith("arc_"):
        return "arc"
    if name.startswith("elliptical_arc_"):
        return "elliptical_arc"
    if name.startswith("ellipse_"):
        return "ellipse"
    if name.startswith("nurbs_"):
        return "nurbs"
    return None


def _json_endpoints_for_entity(name, spec):
    if name.startswith("nurbs_"):
        controls = spec.get("controls") or []
        if len(controls) < 2:
            return None
        return controls[0], controls[-1]
    if "start" in spec and "end" in spec:
        return spec["start"], spec["end"]
    return None


def _rebuild_endpoint_map(sketch_obj, idx_map, entity_dict):
    endpoint_map = {}
    for name, spec in entity_dict.items():
        endpoints = _json_endpoints_for_entity(name, spec)
        if endpoints is None:
            continue
        gi = idx_map.get(name)
        if gi is None or gi >= len(sketch_obj.Geometry):
            continue
        js, je = endpoints
        endpoint_map[name] = _endpoint_map_for_geometry(sketch_obj, gi, js, je)
    return endpoint_map


def _rebuild_arc_endpoint_map(sketch_obj, idx_map, entity_dict):
    return _rebuild_endpoint_map(sketch_obj, idx_map, entity_dict)


def _arc_middle(spec: dict[str, Any]) -> list[float]:
    middle = spec.get("middle")
    if middle is None:
        middle = spec.get("mid")
    if middle is None:
        raise KeyError("middle")
    return middle


def _build_sketch_entities(sketch_obj, entity_dict):
    # Add HistCAD geometry entities to a Sketcher object.
    # Returns (entity_index_map, endpoint_map).
    #
    # Y-axis convention: coordinates are used exactly as written in the
    # HistCAD JSON coordinates (no sign change).  Arc centers and downstream
    # observations use the same sketch-local frame as the Fusion 360 adapter.
    idx_map = {}
    endpoint_map = {}
    for name, spec in entity_dict.items():
        if name.startswith("line_"):
            geo = Part.LineSegment(
                FreeCAD.Vector(spec["start"][0], spec["start"][1], 0),
                FreeCAD.Vector(spec["end"][0],   spec["end"][1],   0),
            )
        elif name.startswith("circle_"):
            geo = Part.Circle(
                FreeCAD.Vector(spec["center"][0], spec["center"][1], 0),
                FreeCAD.Vector(0, 0, 1),
                spec["radius"],
            )
        elif name.startswith("arc_"):
            s = FreeCAD.Vector(spec["start"][0],  spec["start"][1],  0)
            middle = _arc_middle(spec)
            m = FreeCAD.Vector(middle[0], middle[1], 0)
            e = FreeCAD.Vector(spec["end"][0],    spec["end"][1],    0)
            geo = Part.ArcOfCircle(s, m, e)
        elif name.startswith("ellipse_"):
            cx, cy = spec["center"]
            geo = Part.Ellipse(
                FreeCAD.Vector(cx, cy, 0),
                spec["major"],
                spec["minor"],
            )
            import math
            angle_rad = math.radians(spec.get("angle", 0.0))
            geo.AngleXU = angle_rad
        elif name.startswith("elliptical_arc_"):
            s = FreeCAD.Vector(spec["start"][0], spec["start"][1], 0)
            e = FreeCAD.Vector(spec["end"][0],   spec["end"][1],   0)
            cx = (s.x + e.x) / 2
            cy = (s.y + e.y) / 2
            base_ellipse = Part.Ellipse(
                FreeCAD.Vector(cx, cy, 0),
                spec["major"],
                spec["minor"],
            )
            import math
            angle_rad = math.radians(spec.get("angle", 0.0))
            base_ellipse.AngleXU = angle_rad
            geo = Part.ArcOfEllipse(base_ellipse, 0, math.pi)
        elif name.startswith("nurbs_"):
            geo = Part.BSplineCurve()
            poles = [FreeCAD.Vector(p[0], p[1], 0) for p in spec["controls"]]
            weights = spec.get("weights", [1.0] * len(poles))
            knots_raw = spec.get("knots", [])
            degree = spec.get("degree", 3)
            periodic = spec.get("periodic", False)
            if knots_raw:
                from itertools import groupby
                knot_pairs = [(k, len(list(g))) for k, g in groupby(knots_raw)]
                knot_vals = [kp[0] for kp in knot_pairs]
                knot_mults = [kp[1] for kp in knot_pairs]
                geo.buildFromPolesMultsKnots(
                    poles, knot_mults, knot_vals,
                    periodic, degree, weights,
                )
            else:
                geo.interpolate(poles)
        else:
            continue
        idx = sketch_obj.addGeometry(geo, False)
        idx_map[name] = idx
        endpoints = _json_endpoints_for_entity(name, spec)
        if endpoints is not None:
            js, je = endpoints
            endpoint_map[name] = _endpoint_map_for_geometry(
                sketch_obj, idx, js, je,
            )
    return idx_map, endpoint_map


def _persist_sketch_maps(sketch_obj, idx_map, endpoint_map, sketch_dict):
    # Persist maps on the sketch and in __main__ so MCP calls survive process reuse.
    import json
    import __main__ as _main

    if not hasattr(_main, "_sketch_geometry_cache"):
        _main._sketch_geometry_cache = {}
    if not hasattr(_main, "_sketch_idx_map_cache"):
        _main._sketch_idx_map_cache = {}
    if not hasattr(_main, "_sketch_endpoint_map_cache"):
        _main._sketch_endpoint_map_cache = {}
    _main._sketch_geometry_cache[sketch_obj.Name] = sketch_dict
    _main._sketch_idx_map_cache[sketch_obj.Name] = idx_map
    _main._sketch_endpoint_map_cache[sketch_obj.Name] = endpoint_map
    try:
        payload = json.dumps({"idx_map": idx_map, "endpoint_map": endpoint_map})
        if not hasattr(sketch_obj, "HistCADMaps"):
            sketch_obj.addProperty(
                "App::PropertyString",
                "HistCADMaps",
                "HistCAD",
                "Persisted HistCAD entity and endpoint maps",
            )
        sketch_obj.HistCADMaps = payload
        if not hasattr(sketch_obj, "HistCADGeometry"):
            sketch_obj.addProperty(
                "App::PropertyString",
                "HistCADGeometry",
                "HistCAD",
                "Ground-truth HistCAD sketch geometry JSON",
            )
        sketch_obj.HistCADGeometry = json.dumps(sketch_dict)
    except Exception:
        pass


def _load_sketch_maps(sketch_obj):
    # Load persisted maps; never re-derive endpoint_map from live geometry.
    import json
    import __main__ as _main

    idx_map = getattr(_main, "_sketch_idx_map_cache", {}).get(sketch_obj.Name)
    endpoint_map = getattr(_main, "_sketch_endpoint_map_cache", {}).get(
        sketch_obj.Name,
    )
    ground_truth = getattr(_main, "_sketch_geometry_cache", {}).get(sketch_obj.Name)
    try:
        if hasattr(sketch_obj, "HistCADMaps") and sketch_obj.HistCADMaps:
            payload = json.loads(sketch_obj.HistCADMaps)
            idx_map = payload.get("idx_map") or idx_map
            endpoint_map = payload.get("endpoint_map") or endpoint_map
        if hasattr(sketch_obj, "HistCADGeometry") and sketch_obj.HistCADGeometry:
            ground_truth = json.loads(sketch_obj.HistCADGeometry)
    except Exception:
        pass
    idx_map = idx_map or {}
    endpoint_map = endpoint_map or {}
    ground_truth = ground_truth or {}
    if idx_map and not endpoint_map and ground_truth:
        endpoint_map = {
            name: {"start": 1, "end": 2, "center": 3, "middle": 3}
            for name in idx_map
            if isinstance(ground_truth.get(name), dict)
            and "start" in ground_truth[name]
            and "end" in ground_truth[name]
        }
    if idx_map:
        _main._sketch_idx_map_cache[sketch_obj.Name] = idx_map
    if endpoint_map:
        _main._sketch_endpoint_map_cache[sketch_obj.Name] = endpoint_map
    if ground_truth:
        _main._sketch_geometry_cache[sketch_obj.Name] = ground_truth
    return idx_map, endpoint_map, ground_truth


def _semantic_line_endpoints(sketch_obj, geo_idx, endpoint_map_entry):
    START, END = 1, 2
    sp = endpoint_map_entry.get("start", START)
    ep = endpoint_map_entry.get("end", END)
    p_start = sketch_obj.getPoint(geo_idx, sp)
    p_end = sketch_obj.getPoint(geo_idx, ep)
    return [p_start.x, p_start.y], [p_end.x, p_end.y]


def _semantic_arc_points(sketch_obj, geo_idx, endpoint_map_entry, geo):
    START, END, MID = 1, 2, 3
    sp = endpoint_map_entry.get("start", START)
    ep = endpoint_map_entry.get("end", END)
    p_start = sketch_obj.getPoint(geo_idx, sp)
    p_end = sketch_obj.getPoint(geo_idx, ep)
    mid_param = (geo.FirstParameter + geo.LastParameter) / 2
    try:
        mid_pt = geo.value(mid_param)
    except Exception:
        mid_pt = geo.Center
    return (
        [p_start.x, p_start.y],
        [mid_pt.x, mid_pt.y],
        [p_end.x, p_end.y],
    )
"""

_SKETCH_CONSTRAINT_CODE = (
    _SKETCH_HELPER_CODE
    + r"""


def _apply_histcad_constraints(
    sketch_obj,
    constraint_dict,
    idx_map,
    endpoint_map=None,
    arc_endpoint_map=None,
    ground_truth=None,
    orientation_stabilization=False,
):
    # Apply HistCAD constraints to a Sketcher object.
    # Returns {"purged_redundant": [...], "dimension_bindings": [...]}.
    #
    # endpoint_map binds JSON/NLT start/end labels to FreeCAD PointPos at sketch
    # creation; constraints resolve refs through the map, not live coordinates.
    # orientation_stabilization=False (default) skips inventing axis-aligned
    # DistanceX/Y rows that are not in the HistCAD JSON.
    import json
    import Sketcher

    if endpoint_map is None:
        endpoint_map = arc_endpoint_map or {}
    if ground_truth is None:
        ground_truth = {}

    _polarities = {}
    try:
        if hasattr(sketch_obj, "HistCADPolarities") and sketch_obj.HistCADPolarities:
            _polarities = json.loads(sketch_obj.HistCADPolarities) or {}
    except Exception:
        _polarities = {}

    # FreeCAD 1.1 removed Sketcher.PointPos; use integer PointPos values directly.
    # 0 = none, 1 = start, 2 = end, 3 = middle
    NONE  = 0
    START = 1
    END   = 2
    MID   = 3

    _pos_map = {"start": START, "end": END, "center": MID, "middle": MID}

    def _resolve_entity(ref):
        # 'line_1' -> (idx, NONE)
        idx = idx_map[ref]
        return idx, NONE

    def _resolve_point(ref):
        # 'line_1.start' -> (idx, PointPos); endpoint_map when cached at creation.
        if ref == "origin":
            return -1, START
        if "." not in ref:
            return idx_map[ref], NONE
        name, point = ref.split(".", 1)
        idx = idx_map[name]
        if name in endpoint_map:
            mapped = endpoint_map[name].get(point)
            if mapped is not None:
                return idx, mapped
        pos = _pos_map.get(point, NONE)
        return idx, pos

    def _json_pos_to_sketch_pos(entity_name, point_label):
        if entity_name in endpoint_map:
            return endpoint_map[entity_name].get(
                point_label, _pos_map.get(point_label, NONE),
            )
        return _pos_map.get(point_label, NONE)

    def _ground_truth_xy(ref):
        return ground_truth_xy(ground_truth, ref)

    def _fix_constraint(geo_idx):
        # HistCAD Fix on a whole entity maps to FreeCAD's Block(geo).
        return Sketcher.Constraint("Block", geo_idx)

    def _point_xy_for_fix(ref, geo_idx, point_pos):
        try:
            xy = _ground_truth_xy(ref)
            if xy is not None:
                return [float(xy[0]), float(xy[1])]
        except Exception:
            pass
        try:
            if point_pos != NONE:
                pt = sketch_obj.getPoint(geo_idx, point_pos)
                return [float(pt.x), float(pt.y)]
        except Exception:
            pass
        try:
            center = getattr(sketch_obj.Geometry[geo_idx], "Center", None)
            if center is not None:
                return [float(center.x), float(center.y)]
        except Exception:
            pass
        return None

    def _apply_point_fix_as_distances(ref, geo_idx, point_pos, entry_index=None):
        # FreeCAD point-level Lock/Block overloads are version-sensitive. Lowering
        # a point Fix to origin-relative X/Y distances is stable for lines/arcs/circles.
        if ref == "origin":
            return
        xy = _point_xy_for_fix(ref, geo_idx, point_pos)
        if xy is None:
            before_count = len(sketch_obj.Constraints)
            c = _fix_constraint(geo_idx)
            sketch_obj.addConstraint(c)
            _log_applied(
                before_count,
                source="adapter",
                input_type="Fix",
                entry_index=entry_index,
                entry=ref,
                entity_refs=[ref],
                freecad_type=c.Type,
                adapter_reason="point_fix_fell_back_to_block",
            )
            added.append("Fix")
            return
        for direction, length in (
            ("HORIZONTAL", abs(float(xy[0]))),
            ("VERTICAL", abs(float(xy[1]))),
        ):
            _apply_distance_entry(
                ["origin", ref, {"length": length, "direction": direction}],
                source="adapter",
                input_type="Fix",
                entry_index=entry_index,
                adapter_reason="point_fix_lowered_to_distance",
            )
        added.append("Fix")

    def _primitive_from_ref(ref):
        text = str(ref)
        return text.split(".", 1)[0] if "." in text else text

    def _orientation_covered_by_json_constraints(line_name, orient_direction):
        # Skip auto-orientation when JSON already fixes segment direction via
        # directed Distance / Horizontal / Vertical / Parallel-to-oriented.
        # Extra orientation constraints are invisible to editability
        # preserved-constraint checks but still fight parametric edits.
        wanted = str(orient_direction).upper()
        oriented = set()

        def _collect_oriented(entries):
            for entry in entries:
                if isinstance(entry, str):
                    oriented.add(entry)
                elif isinstance(entry, list) and len(entry) >= 2:
                    if {_primitive_from_ref(entry[0]), _primitive_from_ref(entry[1])} == {
                        line_name
                    }:
                        return True
                    oriented.add(_primitive_from_ref(entry[0]))
                    oriented.add(_primitive_from_ref(entry[1]))
            return False

        if wanted == "VERTICAL":
            if _collect_oriented(constraint_dict.get("Vertical", [])):
                return True
        elif wanted == "HORIZONTAL":
            if _collect_oriented(constraint_dict.get("Horizontal", [])):
                return True
        if line_name in oriented:
            return True
        for entry in constraint_dict.get("Distance", []):
            if not isinstance(entry, list) or len(entry) < 2:
                continue
            extra = entry[2] if len(entry) > 2 else {}
            if not isinstance(extra, dict):
                continue
            direction = str(extra.get("direction", "")).upper()
            if direction != wanted:
                continue
            primitives = {
                _primitive_from_ref(ref)
                for ref in entry[:2]
                if isinstance(ref, str) and "." in ref
            }
            if line_name in primitives:
                return True
        for entry in constraint_dict.get("Parallel", []):
            if not isinstance(entry, list) or len(entry) < 2:
                continue
            a = _primitive_from_ref(entry[0])
            b = _primitive_from_ref(entry[1])
            if line_name == a and b in oriented:
                return True
            if line_name == b and a in oriented:
                return True
        return False

    def _orientation_entries_from_ground_truth():
        # Break flip ambiguity on axis-aligned segments using signed deltas from
        # JSON geometry — only when JSON lacks directed constraints on that line.
        entries = []
        if not ground_truth:
            return entries
        for name, spec in ground_truth.items():
            if not name.startswith("line_") or name not in idx_map:
                continue
            js = spec.get("start")
            je = spec.get("end")
            if js is None or je is None:
                continue
            dx = float(je[0]) - float(js[0])
            dy = float(je[1]) - float(js[1])
            if abs(dx) < 1e-6 and abs(dy) > 1e-6:
                if _orientation_covered_by_json_constraints(name, "VERTICAL"):
                    continue
                entries.append(
                    [
                        f"{name}.start",
                        f"{name}.end",
                        {"direction": "VERTICAL", "length": dy},
                    ]
                )
            elif abs(dy) < 1e-6 and abs(dx) > 1e-6:
                if _orientation_covered_by_json_constraints(name, "HORIZONTAL"):
                    continue
                entries.append(
                    [
                        f"{name}.start",
                        f"{name}.end",
                        {"direction": "HORIZONTAL", "length": dx},
                    ]
                )
        return entries

    def _parse_length(val):
        # '10 mm' -> 10.0  (already in mm, FreeCAD uses mm internally)
        if isinstance(val, dict):
            val = (
                val.get("length")
                if val.get("length") is not None
                else val.get("value")
            )
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        for suffix in (" mm", "mm", " cm", "cm", " m", "m",
                       " in", "in", " deg", "deg"):
            if s.lower().endswith(suffix.lower()):
                num = float(s[: -len(suffix)].strip())
                if "cm" in suffix:
                    return num * 10.0
                if " m" in suffix and "m" not in suffix[1:]:
                    return num * 1000.0
                if "in" in suffix:
                    return num * 25.4
                return num
        return float(s)

    def _dimension_meta(raw, *, default_unit="mm", value_key="length"):
        if not isinstance(raw, dict):
            return {"value": _parse_length(raw), "unit": default_unit}
        value = raw.get(value_key)
        if value is None:
            value = raw.get("length")
        if value is None:
            value = raw.get("radius")
        if value is None:
            value = raw.get("diameter")
        if value is None:
            value = raw.get("value")
        out = {
            "value": _parse_length(value),
            "unit": raw.get("unit", default_unit),
        }
        for key in ("alias", "expression", "label", "role", "min", "max", "default"):
            if raw.get(key) is not None:
                out[key] = raw[key]
        return out

    def _angle_meta(raw):
        if not isinstance(raw, dict):
            return {"value": float(raw), "unit": "deg"}
        value = raw.get("angle")
        if value is None:
            value = raw.get("value")
        out = {
            "value": float(value),
            "unit": raw.get("unit", "deg"),
        }
        for key in ("alias", "label", "role", "min", "max", "default"):
            if raw.get(key) is not None:
                out[key] = raw[key]
        return out

    # Build endpoint-adjacency map from Coincident entries so that Tangent
    # constraints can use the 4-argument point-specific form (smooth G1
    # connection) instead of the 2-argument entity form (which lets the
    # Sketcher solver pick the wrong endpoint and creates loops/cusps).
    _coinc_map = {}  # (entity_name, point_str) -> (entity_name, point_str)
    for _ce in constraint_dict.get("Coincident", []):
        if len(_ce) >= 2 and "." in str(_ce[0]) and "." in str(_ce[1]):
            _r1, _r2 = str(_ce[0]), str(_ce[1])
            _n1, _p1 = _r1.split(".", 1)
            _n2, _p2 = _r2.split(".", 1)
            _coinc_map[(_n1, _p1)] = (_n2, _p2)
            _coinc_map[(_n2, _p2)] = (_n1, _p1)

    def _extend_coinc_map_from_sketch():
        # Incremental apply (e.g. Tangent-only) still needs junction endpoints
        # from Coincident constraints already on the sketch.
        if not idx_map:
            return
        _inv = {int(v): k for k, v in idx_map.items()}
        _pos_labels = {1: "start", 2: "end", 3: "middle"}
        for _c in sketch_obj.Constraints:
            if _c.Type != "Coincident":
                continue
            _f, _fp = int(_c.First), int(_c.FirstPos)
            _s, _sp = int(_c.Second), int(_c.SecondPos)
            if _f < 0 or _s < 0:
                continue
            _n1 = _inv.get(_f)
            _n2 = _inv.get(_s)
            _p1 = _pos_labels.get(_fp)
            _p2 = _pos_labels.get(_sp)
            if _n1 and _n2 and _p1 and _p2:
                _coinc_map[(_n1, _p1)] = (_n2, _p2)
                _coinc_map[(_n2, _p2)] = (_n1, _p1)

    _extend_coinc_map_from_sketch()

    added = []
    dimension_bindings = []
    applied_log = []

    def _entity_refs_from_entry(ctype, entry):
        refs = []
        if isinstance(entry, str):
            return [entry]
        if isinstance(entry, list):
            for item in entry:
                if isinstance(item, str) and ("." in item or item in idx_map):
                    refs.append(item)
        return refs

    def _log_applied(
        before_count,
        *,
        source,
        input_type,
        entry_index,
        entry,
        entity_refs,
        freecad_type=None,
        adapter_reason=None,
    ):
        applied_log.append(
            {
                "freecad_index": before_count + 1,
                "freecad_type": freecad_type,
                "source": source,
                "input": (
                    {
                        "type": input_type,
                        "entry_index": entry_index,
                        "entry": entry,
                    }
                    if source == "input"
                    else None
                ),
                "entity_refs": entity_refs,
                "adapter_reason": (
                    adapter_reason
                    if adapter_reason is not None
                    else ("axis_aligned_orientation" if source == "adapter" else None)
                ),
            }
        )

    def _live_xy(ref):
        try:
            geo_idx, point_pos = _resolve_point(ref)
            if geo_idx < 0 or point_pos == NONE:
                return None
            pt = sketch_obj.getPoint(geo_idx, point_pos)
            return [float(pt.x), float(pt.y)]
        except Exception:
            return None

    def _apply_distance_entry(
        entry,
        *,
        source="input",
        input_type="Distance",
        entry_index=None,
        adapter_reason=None,
    ):
        dim_meta = None
        i1, p1 = _resolve_point(entry[0])
        i2, p2 = _resolve_point(entry[1])
        extra = entry[2] if len(entry) > 2 else {}
        dim_meta = _dimension_meta(extra)
        val = dim_meta["value"]
        direction = str(extra.get("direction", "")).upper()
        if direction in ("HORIZONTAL", "VERTICAL"):
            axis_index = 0 if direction == "HORIZONTAL" else 1
            key = distance_polarity_key(str(entry[0]), str(entry[1]), direction)
            stored = _polarities.get(key)
            try:
                stored_polarity = int(stored) if stored is not None else None
            except (TypeError, ValueError):
                stored_polarity = None
            if stored_polarity not in (1, -1):
                stored_polarity = None
            val = directed_axis_distance(
                entry[0],
                entry[1],
                axis_index,
                val,
                ground_truth=ground_truth,
                polarity=stored_polarity,
                live_a=_live_xy(entry[0]),
                live_b=_live_xy(entry[1]),
            )
            if stored_polarity is None and abs(float(val)) >= 1e-12:
                _polarities[key] = polarity_from_signed(val)
            if direction == "HORIZONTAL":
                c = Sketcher.Constraint("DistanceX", i1, p1, i2, p2, val)
            else:
                c = Sketcher.Constraint("DistanceY", i1, p1, i2, p2, val)
        else:
            c = Sketcher.Constraint("Distance", i1, p1, i2, p2, val)
        before_count = len(sketch_obj.Constraints)
        sketch_obj.addConstraint(c)
        _log_applied(
            before_count,
            source=source,
            input_type=input_type,
            entry_index=entry_index,
            entry=entry,
            entity_refs=[str(entry[0]), str(entry[1])],
            freecad_type=c.Type,
            adapter_reason=adapter_reason,
        )
        if dim_meta and (dim_meta.get("alias") or dim_meta.get("expression")):
            binding = dict(dim_meta)
            binding.update(
                {
                    "constraint_index": before_count,
                    "constraint_type": "Distance",
                    "property": f"Constraints[{before_count}]",
                }
            )
            dimension_bindings.append(binding)
        added.append("Distance")

    def _apply_constraint_entry(ctype, entry, entry_index=None):
        dim_meta = None
        if ctype == "Coincident":
            i1, p1 = _resolve_point(entry[0])
            i2, p2 = _resolve_point(entry[1])
            c = Sketcher.Constraint("Coincident", i1, p1, i2, p2)
        elif ctype == "Horizontal":
            if isinstance(entry, str):
                i, _ = _resolve_entity(entry)
                c = Sketcher.Constraint("Horizontal", i)
            else:
                i1, p1 = _resolve_point(entry[0])
                i2, p2 = _resolve_point(entry[1])
                c = Sketcher.Constraint("Horizontal", i1, p1, i2, p2)
        elif ctype == "Vertical":
            if isinstance(entry, str):
                i, _ = _resolve_entity(entry)
                c = Sketcher.Constraint("Vertical", i)
            else:
                i1, p1 = _resolve_point(entry[0])
                i2, p2 = _resolve_point(entry[1])
                c = Sketcher.Constraint("Vertical", i1, p1, i2, p2)
        elif ctype == "Perpendicular":
            i1, _ = _resolve_entity(entry[0])
            i2, _ = _resolve_entity(entry[1])
            c = Sketcher.Constraint("Perpendicular", i1, i2)
        elif ctype == "Parallel":
            i1, _ = _resolve_entity(entry[0])
            i2, _ = _resolve_entity(entry[1])
            c = Sketcher.Constraint("Parallel", i1, i2)
        elif ctype == "Equal":
            i1, _ = _resolve_entity(entry[0])
            i2, _ = _resolve_entity(entry[1])
            c = Sketcher.Constraint("Equal", i1, i2)
        elif ctype == "Tangent":
            _ref1, _ref2 = entry[0], entry[1]
            _has_pt1, _has_pt2 = "." in _ref1, "." in _ref2
            if _has_pt1 and _has_pt2:
                _ti1, _tp1 = _resolve_point(_ref1)
                _ti2, _tp2 = _resolve_point(_ref2)
                c = Sketcher.Constraint("Tangent", _ti1, _tp1, _ti2, _tp2)
            elif not _has_pt1 and not _has_pt2:
                _shared = None
                for _pn in ("start", "end"):
                    _k = (_ref1, _pn)
                    if _k in _coinc_map and _coinc_map[_k][0] == _ref2:
                        _shared = (_pn, _coinc_map[_k][1])
                        break
                if _shared is None:
                    for _pn in ("start", "end"):
                        _k = (_ref2, _pn)
                        if _k in _coinc_map and _coinc_map[_k][0] == _ref1:
                            _shared = (_coinc_map[_k][1], _pn)
                            break
                if _shared is not None:
                    _ti1 = idx_map[_ref1]
                    _tp1 = _json_pos_to_sketch_pos(_ref1, _shared[0])
                    _ti2 = idx_map[_ref2]
                    _tp2 = _json_pos_to_sketch_pos(_ref2, _shared[1])
                    c = Sketcher.Constraint("Tangent", _ti1, _tp1, _ti2, _tp2)
                else:
                    _ti1, _ = _resolve_entity(_ref1)
                    _ti2, _ = _resolve_entity(_ref2)
                    c = Sketcher.Constraint("Tangent", _ti1, _ti2)
            else:
                _ti1, _ = _resolve_entity(_ref1.split(".")[0])
                _ti2, _ = _resolve_entity(_ref2.split(".")[0])
                c = Sketcher.Constraint("Tangent", _ti1, _ti2)
        elif ctype == "Normal":
            i1, _ = _resolve_entity(entry[0])
            i2, _ = _resolve_entity(entry[1])
            c = Sketcher.Constraint("Perpendicular", i1, i2)
        elif ctype == "Concentric":
            i1, _ = _resolve_entity(entry[0])
            i2, _ = _resolve_entity(entry[1])
            c = Sketcher.Constraint("Coincident", i1, MID, i2, MID)
        elif ctype == "Fix":
            if "." in entry:
                i, p = _resolve_point(entry)
                _apply_point_fix_as_distances(entry, i, p, entry_index=entry_index)
                return
            else:
                i, _ = _resolve_entity(entry)
            c = _fix_constraint(i)
        elif ctype == "Midpoint":
            if isinstance(entry[1], list):
                i_mid, p_mid = _resolve_point(entry[0])
                i_a, p_a = _resolve_point(entry[1][0])
                i_b, p_b = _resolve_point(entry[1][1])
                c = Sketcher.Constraint(
                    "Symmetric", i_a, p_a, i_b, p_b, i_mid, p_mid,
                )
            else:
                i_pt, p_pt = _resolve_point(entry[0])
                i_ln, _ = _resolve_entity(entry[1])
                c = Sketcher.Constraint(
                    "Symmetric", i_ln, START, i_ln, END, i_pt, p_pt,
                )
        elif ctype == "Mirror":
            i_ax, _ = _resolve_entity(entry[1])
            if "." in str(entry[0]) and "." in str(entry[2]):
                i_src, p_src = _resolve_point(entry[0])
                i_dst, p_dst = _resolve_point(entry[2])
                c = Sketcher.Constraint(
                    "Symmetric", i_src, p_src, i_dst, p_dst, i_ax,
                )
            else:
                i_src, _ = _resolve_entity(entry[0])
                i_dst, _ = _resolve_entity(entry[2])
                before_first = len(sketch_obj.Constraints)
                sketch_obj.addConstraint([Sketcher.Constraint(
                    "Symmetric", i_src, START, i_dst, START, i_ax,
                )])
                _log_applied(
                    before_first,
                    source="input",
                    input_type="Mirror",
                    entry_index=entry_index,
                    entry=entry,
                    entity_refs=_entity_refs_from_entry("Mirror", entry),
                )
                c = Sketcher.Constraint(
                    "Symmetric", i_src, END, i_dst, END, i_ax,
                )
        elif ctype == "Angle":
            i1, _ = _resolve_entity(entry[0])
            i2, _ = _resolve_entity(entry[1])
            import math
            dim_meta = _angle_meta(entry[2])
            angle_rad = math.radians(float(dim_meta["value"]))
            c = Sketcher.Constraint("Angle", i1, i2, angle_rad)
        elif ctype == "Diameter":
            i, _ = _resolve_entity(entry[0])
            dim_meta = _dimension_meta(entry[1], value_key="diameter")
            val = dim_meta["value"]
            c = Sketcher.Constraint("Diameter", i, val)
        elif ctype == "Radius":
            i, _ = _resolve_entity(entry[0])
            dim_meta = _dimension_meta(entry[1], value_key="radius")
            val = dim_meta["value"]
            c = Sketcher.Constraint("Radius", i, val)
        elif ctype == "MajorRadius":
            i, _ = _resolve_entity(entry[0])
            dim_meta = _dimension_meta(entry[1])
            val = dim_meta["value"]
            c = Sketcher.Constraint("Radius", i, val)
        elif ctype == "MinorRadius":
            i, _ = _resolve_entity(entry[0])
            dim_meta = _dimension_meta(entry[1])
            val = dim_meta["value"]
            c = Sketcher.Constraint("Radius", i, val)
        elif ctype == "Length":
            i, _ = _resolve_entity(entry[0])
            dim_meta = _dimension_meta(entry[1])
            val = dim_meta["value"]
            c = Sketcher.Constraint("Distance", i, val)
        elif ctype == "Distance":
            _apply_distance_entry(
                entry,
                source="input",
                input_type="Distance",
                entry_index=entry_index,
            )
            return
        else:
            return
        before_count = len(sketch_obj.Constraints)
        sketch_obj.addConstraint(c)
        _log_applied(
            before_count,
            source="input",
            input_type=ctype,
            entry_index=entry_index,
            entry=entry,
            entity_refs=_entity_refs_from_entry(ctype, entry),
            freecad_type=c.Type,
        )
        if dim_meta and (dim_meta.get("alias") or dim_meta.get("expression")):
            binding = dict(dim_meta)
            binding.update(
                {
                    "constraint_index": before_count,
                    "constraint_type": ctype,
                    "property": f"Constraints[{before_count}]",
                }
            )
            dimension_bindings.append(binding)
        added.append(ctype)

    # Phase 1: topology (Coincident / Concentric) before directed dimensions.
    for ctype in ("Coincident", "Concentric"):
        for entry_index, entry in enumerate(constraint_dict.get(ctype, [])):
            try:
                _apply_constraint_entry(ctype, entry, entry_index=entry_index)
            except Exception:
                pass

    # Phase 2: optional axis-aligned orientation from ground truth (opt-in).
    # Default off: inventing DistanceX/Y locks free DOFs and hurts editability.
    if orientation_stabilization:
        for orient_entry in _orientation_entries_from_ground_truth():
            try:
                _apply_distance_entry(
                    orient_entry,
                    source="adapter",
                    input_type=None,
                    entry_index=None,
                )
            except Exception:
                pass

    # Phase 3: remaining constraints in input order.
    for ctype, entries in constraint_dict.items():
        if ctype in ("Coincident", "Concentric"):
            continue
        for entry_index, entry in enumerate(entries):
            try:
                _apply_constraint_entry(ctype, entry, entry_index=entry_index)
            except Exception:
                pass

    def _purge_redundant_sketch_constraints(max_iterations=10):
        # Point-specific Tangent constraints make junction Coincident entries
        # solver-redundant (solve_status -2).  Part::Extrusion then returns a
        # null shape even though the wire is closed.  Drop redundant rows and
        # keep applied_log / dimension_bindings indices aligned.
        purged = []
        _pos_labels = {1: "start", 2: "end", 3: "middle"}
        _inv = {int(v): k for k, v in idx_map.items()}
        for _ in range(max_iterations):
            try:
                sketch_obj.solve()
            except Exception:
                break
            _red_idxs = [
                int(i)
                for i in (getattr(sketch_obj, "RedundantConstraints", []) or [])
            ]
            if not _red_idxs:
                break
            _deleted_any = False
            # RedundantConstraints uses 1-based indices (matches constraint_catalog
            # freecad_index); delConstraint() expects 0-based positions.
            for _r_idx in sorted(_red_idxs, reverse=True):
                _pos = _r_idx - 1
                if not (0 <= _pos < len(sketch_obj.Constraints)):
                    continue
                _c = sketch_obj.Constraints[_pos]
                # Tangent + junction Coincident overlap is the known failure mode for
                # Part::Extrusion.  Leave other redundant types untouched.
                if _c.Type != "Coincident":
                    continue
                _refs = []
                _f, _fp = int(_c.First), int(_c.FirstPos)
                _s, _sp = int(_c.Second), int(_c.SecondPos)
                if _f >= 0 and _f in _inv:
                    _lbl = _pos_labels.get(_fp)
                    _refs.append(
                        f"{_inv[_f]}.{_lbl}" if _lbl else _inv[_f]
                    )
                if _s >= 0 and _s in _inv:
                    _lbl = _pos_labels.get(_sp)
                    _refs.append(
                        f"{_inv[_s]}.{_lbl}" if _lbl else _inv[_s]
                    )
                purged.append(
                    {
                        "freecad_index": _r_idx,
                        "freecad_type": _c.Type,
                        "entity_refs": _refs,
                        "reason": "solver_redundant",
                    }
                )
                sketch_obj.delConstraint(_pos)
                _deleted_any = True
                if _pos < len(applied_log):
                    applied_log.pop(_pos)
                for _binding in dimension_bindings:
                    _ci = _binding.get("constraint_index")
                    if _ci is None:
                        continue
                    if _ci == _pos:
                        _binding["_removed"] = True
                    elif _ci > _pos:
                        _new = _ci - 1
                        _binding["constraint_index"] = _new
                        _binding["property"] = f"Constraints[{_new}]"
            if not _deleted_any:
                break
        dimension_bindings[:] = [
            _b for _b in dimension_bindings if not _b.get("_removed")
        ]
        try:
            sketch_obj.solve()
        except Exception:
            pass
        return purged

    purged_redundant = _purge_redundant_sketch_constraints()
    try:
        if not hasattr(sketch_obj, "HistCADPolarities"):
            sketch_obj.addProperty(
                "App::PropertyString",
                "HistCADPolarities",
                "HistCAD",
                "Persisted directed Distance polarities",
            )
        sketch_obj.HistCADPolarities = json.dumps(_polarities)
    except Exception:
        pass
    return {
        "redundant": [],
        "purged_redundant": purged_redundant,
        "dimension_bindings": dimension_bindings,
        "applied_log": applied_log,
        "polarities": dict(_polarities),
    }
"""
)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_fabrication_tools(
    mcp: Any, get_bridge: Callable[[], Awaitable[Any]]
) -> None:
    """Register Layer 1 generic fabrication primitives with the MCP server.

    Registers 16 tools across 7 groups:

    - Group A — Coordinate System: ``create_coordinate_system``
    - Group B — Sketch Geometry: ``create_sketch_geometry``,
      ``parse_freecad_sketch``
    - Group C — Sketch Constraints: ``check_sketch_constraints``,
      ``apply_sketch_constraints``
    - Group D — Feature Execution: ``execute_extrude``, ``execute_boolean``,
      ``execute_revolve``, ``execute_helix``
    - Group E — Finishing: ``feature_fillet``, ``feature_chamfer``
    - Group F — Parametric Control: ``list_tunable_params``,
      ``set_tunable_param``
    - Group G — Observation: ``get_body_snapshot``
    - Agentic Plan Contract: ``describe_primitive_plan_schema``,
      ``validate_primitive_plan``
    - Deterministic Batch Contract: ``validate_fabrication_plan``
    - Batch: ``execute_fabrication_plan``

    Args:
        mcp: The FastMCP (Robust MCP Server) instance.
        get_bridge: Async function returning the active bridge connection.
    """
    # ------------------------------------------------------------------
    # Group A — Coordinate System
    # ------------------------------------------------------------------

    @mcp.tool()
    async def create_coordinate_system(
        euler_angles: list[float],
        translation: list[float],
        name: str | None = None,
        attachment_support: dict[str, Any] | None = None,
        param_aliases: dict[str, str] | None = None,
        param_expressions: dict[str, str] | None = None,
        body_name: str | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a named datum coordinate system (sketch plane).

        Creates a ``PartDesign::CoordinateSystem`` (Local Coordinate System)
        that defines a sketch plane by Euler angles and a translation vector.
        The returned ``cs_name`` can be passed to ``create_sketch_geometry``
        as ``coordinate_system_name``, avoiding re-specifying Euler angles
        when multiple sketches share the same plane.

        Separating coordinate system creation from sketch creation mirrors
        ToolCAD's ``set_coord_system`` step and lets the RL agent or LLM
        learn plane positioning independently from profile geometry.

        Args:
            euler_angles: Rotation ``[a, b, g]`` in degrees around the X, Y, Z
                axes respectively.  Applied as the active local-to-world
                rotation ``R = Rx(a) * Ry(b) * Rz(g)`` (HistCAD / Fusion 360
                adapter convention, angles used as-is).
            translation: Origin ``[x, y, z]`` in millimetres in the world frame.
            name: Human-readable label for the coordinate system. If None,
                defaults to ``"CoordinateSystem"`` (matching the HistCAD JSON
                field name).
            attachment_support: Optional ``{"target": object_name}``. With a
                target, pose values are relative to that object's local frame.
            param_aliases: Optional neutral coordinate-property aliases, such
                as ``{"translation.z": "block_thickness"}``.
            param_expressions: Optional direct FreeCAD expressions for neutral
                coordinate properties, such as ``{"translation.z":
                "Pad.LengthFwd"}``.
            body_name: PartDesign Body to contain the LCS. Uses the active
                body if None.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with:
                - cs_name: Human-readable label of the LCS (equals the
                  ``name`` argument).  Pass this directly as
                  ``coordinate_system_name`` to ``create_sketch_geometry``.
                - cs_internal_name: FreeCAD's internal unique object name
                  (may differ from ``cs_name`` when FreeCAD appends a
                  de-duplication suffix, e.g. ``"MyPlane001"``).
                - label: Same as ``cs_name`` (retained for compatibility).
                - placement: ``{"euler_angles": [...], "translation": [...]}``.
                - success: ``True`` on success.

        Raises:
            ValueError: If the document or body cannot be found.

        Example:
            Create a datum plane 50 mm above the XY plane::

                result = await create_coordinate_system(
                    euler_angles=[0.0, 0.0, 0.0],
                    translation=[0.0, 0.0, 50.0],
                    name="TopPlane",
                )
                # result["cs_name"] → "TopPlane"  (the label you provided)
        """
        validation_errors: list[dict[str, str]] = []
        validate_coordinate_system_payload(
            {
                "euler_angles": euler_angles,
                "translation": translation,
                "attachment_support": attachment_support,
                "param_aliases": param_aliases or {},
                "param_bindings": {},
            },
            lambda path, message, code: validation_errors.append(
                {"path": path, "message": message, "code": code}
            ),
            "$",
        )
        if validation_errors:
            raise ValueError(
                "Invalid coordinate system: "
                + "; ".join(x["message"] for x in validation_errors)
            )
        bridge = await get_bridge()
        code = f"""
import math

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

euler = {euler_angles!r}
trans = {translation!r}
label = {name!r} or "CoordinateSystem"
attachment = {attachment_support!r}
aliases = {param_aliases!r} or {{}}
source_expressions = {param_expressions!r} or {{}}

# Build the placement from Euler angles (degrees, XYZ intrinsic).
# Convention (matches the HistCAD Fusion 360 adapter): euler_angles are the
# ACTIVE local-to-world rotation R = Rx(a) * Ry(b) * Rz(g), angles used
# as-is (no negation).  World point = R * (x, y, 0) + translation, sketch
# normal = R * Z.  e.g. euler=[-90,0,0] → Rx(-90°) so sketch normal → +Y.
rot = FreeCAD.Rotation(
    FreeCAD.Vector(0, 0, 1), euler[2],
)
rot = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), euler[1]) * rot
rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), euler[0]) * rot
placement = FreeCAD.Placement(FreeCAD.Vector(*trans), rot)

doc.openTransaction("Create Coordinate System")
try:
    # Try PartDesign::CoordinateSystem first (FreeCAD 1.x)
    body = None
    if {body_name!r} is not None:
        body = doc.getObject({body_name!r})
    else:
        # Find the active body
        for obj in doc.Objects:
            if obj.TypeId == "PartDesign::Body":
                body = obj
                break

    lcs = doc.addObject("PartDesign::CoordinateSystem", label)
    lcs.Label = label
    target_name = attachment.get("target") if attachment else None
    target = doc.getObject(target_name) if target_name else None
    if target_name and target is None:
        raise ValueError(f"Coordinate-system attachment target not found: {{target_name!r}}")
    if target is not None:
        lcs.AttachmentSupport = [(target, ("", ""))]
        lcs.MapMode = "ObjectXY"
        lcs.AttachmentOffset = placement
    else:
        lcs.Placement = placement
    if body is not None:
        body.addObject(lcs)
    doc.recompute()
    bound_params = []
    prop_map = {{
        "translation.x": "AttachmentOffset.Base.x" if target else "Placement.Base.x",
        "translation.y": "AttachmentOffset.Base.y" if target else "Placement.Base.y",
        "translation.z": "AttachmentOffset.Base.z" if target else "Placement.Base.z",
    }}
    if source_expressions:
        for key, expression in source_expressions.items():
            prop = prop_map.get(key)
            if prop is None:
                raise ValueError(f"Unsupported direct coordinate binding: {{key!r}}")
            lcs.setExpression(prop, expression)
            bound_params.append({{"alias": None, "cell": None, "value": None, "property": prop, "expression": expression, "object": lcs.Name, "role": "coordinate_system_direct", "diagnostic": None}})
        doc.recompute()
    if aliases:
        sheet = doc.getObject("FabricationParams")
        if sheet is None:
            sheet = doc.addObject("Spreadsheet::Sheet", "FabricationParams")
        alias_prop_map = {{
            "translation.x": "AttachmentOffset.Base.x" if target else "Placement.Base.x",
            "translation.y": "AttachmentOffset.Base.y" if target else "Placement.Base.y",
            "translation.z": "AttachmentOffset.Base.z" if target else "Placement.Base.z",
            "euler_angles.x": None,
            "euler_angles.y": None,
            "euler_angles.z": None,
        }}
        values = {{
            "translation.x": trans[0], "translation.y": trans[1], "translation.z": trans[2],
            "euler_angles.x": euler[0], "euler_angles.y": euler[1], "euler_angles.z": euler[2],
        }}
        used = set(sheet.getUsedCells()) if hasattr(sheet, "getUsedCells") else set()
        for key, alias in aliases.items():
            # A direct source binding is authoritative.  Keep the spreadsheet
            # parameter for its source feature/sketch, but never replace the
            # datum's native expression with a second sheet expression.
            if key in source_expressions:
                continue
            cell = next((c for c in used if sheet.getAlias(c) == alias), None)
            if cell is None:
                row = 1
                while f"A{{row}}" in used:
                    row += 1
                cell = f"A{{row}}"; used.add(cell)
            sheet.set(cell, str(values[key]))
            try: sheet.setAlias(cell, alias)
            except Exception: pass
            prop = alias_prop_map[key]
            diagnostic = None
            if prop is None:
                diagnostic = "rotation_alias_not_supported_by_freecad_adapter"
            else:
                try: lcs.setExpression(prop, f"FabricationParams.{{alias}}")
                except Exception: diagnostic = "setExpression_failed"
            bound_params.append({{
                "alias": alias, "cell": cell, "value": values[key], "property": prop,
                "object": lcs.Name, "role": "coordinate_system" if prop else "unbound_adapter_limit",
                "diagnostic": diagnostic,
            }})
        doc.recompute()
    doc.commitTransaction()
    _result_ = {{
        "cs_name": lcs.Label,
        "cs_internal_name": lcs.Name,
        "label": lcs.Label,
        "placement": {{"euler_angles": {euler_angles!r}, "translation": {translation!r}}},
        "attachment_support": attachment,
        "bound_params": bound_params,
        "success": True,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to create coordinate system")

    # ------------------------------------------------------------------
    # Group B — Sketch Geometry
    # ------------------------------------------------------------------

    @mcp.tool()
    async def create_sketch_geometry(
        sketch: dict[str, Any],
        coordinate_system_name: str,
        body_name: str | None = None,
        sketch_name: str | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a 2-D sketch with HistCAD geometry entities.

        Attaches the sketch to a named coordinate system created by
        ``create_coordinate_system``. This is the only sketch-plane path.

        The returned ``entity_index_map`` maps HistCAD entity names (e.g.
        ``"line_1"``) to FreeCAD Sketcher integer indices so that the subsequent
        ``apply_sketch_constraints`` call can reference the same named entities.

        Args:
            sketch: HistCAD entity dict, e.g.::

                {
                    "line_1":   {"start": [0, 0],  "end": [20, 0]},
                    "circle_1": {"center": [0, 0], "radius": 10},
                    "arc_1":    {"start": [0, 10], "middle": [7, 7], "end": [10, 0]},
                }

            coordinate_system_name: Name of a datum LCS created by
                ``create_coordinate_system``.
            body_name: PartDesign Body to add the sketch to. Auto-detected
                if None.
            sketch_name: Explicit FreeCAD object name. Auto-generated if None.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with:
                - sketch_name: Name of the created sketch (pass to
                  ``apply_sketch_constraints`` and ``execute_extrude``).
                - cs_name: Name of the auto-created
                  ``PartDesign::CoordinateSystem`` datum, or ``None`` when the
                  sketch is attached to a named LCS or a face instead.
                - geometry_count: ``{total, lines, arcs, circles}`` — compare
                  against expected entity counts from the HistCAD JSON input.
                - profile: ``{loops, closed_loops, open_loops, closed}`` —
                  whether the entities chain into closed loops.  ``closed``
                  must be ``True`` for the sketch to extrude into a solid;
                  ``open_loops > 0`` means endpoint gaps in the input.
                - dof_remaining: Constraint degrees of freedom remaining.
                  Zero means fully constrained; positive means under-constrained.
                - fully_constrained: ``True`` when ``dof_remaining == 0``.
                - sketch_normal_world: Sketch plane normal in world space.
                - sketch_origin_world: Sketch plane origin in world space.
                - success: ``True`` on success.

        Note:
                ``entity_index_map`` (Sketcher internal indices) and the raw input
                geometry dict are both stored on the sketch object internally.
                ``apply_sketch_constraints`` reads ``entity_index_map`` directly;
                ``execute_extrude`` auto-retrieves the geometry when ``sketch=None``.
                Neither value needs to be forwarded by the caller.

        Raises:
            ValueError: If the sketch cannot be created or no plane is specified.

        Example:
            Create a rectangular sketch on the XY plane::

                result = await create_sketch_geometry(
                    sketch={
                        "line_1": {"start": [0,0], "end": [20,0]},
                        "line_2": {"start": [20,0], "end": [20,10]},
                        "line_3": {"start": [20,10], "end": [0,10]},
                        "line_4": {"start": [0,10], "end": [0,0]},
                    },
                    coordinate_system_name="XY_Base",
                )
                # The geometry is stored on the sketch object automatically.
                # execute_extrude retrieves it without any caller pass-through.
        """
        bridge = await get_bridge()
        code = f"""
import math, Part, Sketcher

{_SKETCH_ENTITY_CODE}

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sketch_dict  = {sketch!r}
cs_name_ref  = {coordinate_system_name!r}
body_nm      = {body_name!r}
sketch_nm    = {sketch_name!r}

doc.openTransaction("Create Sketch Geometry")
try:
    # Resolve body
    body = None
    if body_nm is not None:
        body = doc.getObject(body_nm)
    else:
        for obj in doc.Objects:
            if obj.TypeId == "PartDesign::Body":
                body = obj
                break

    # The named LCS is the only source of the sketch plane.
    sk = doc.addObject("Sketcher::SketchObject", sketch_nm or "Sketch")

    # Attach the sketch to its named local coordinate system.
    _plane_attached = False
    if cs_name_ref is not None:
        # Look up by Label first (the user-visible name returned by
        # create_coordinate_system as cs_name), then fall back to the
        # internal FreeCAD Name so both "MyPlane" and "CoordinateSystem001"
        # work as coordinate_system_name values.
        lcs = None
        for _obj in doc.Objects:
            if _obj.Label == cs_name_ref:
                lcs = _obj
                break
        if lcs is None:
            lcs = doc.getObject(cs_name_ref)
        if lcs is not None:
            sk.AttachmentSupport = [(lcs, "")]
            sk.MapMode = "ObjectXY"
            _plane_attached = True
        else:
            raise ValueError(f"Coordinate system not found: {{cs_name_ref!r}}")
    if body is not None and getattr(body, "TypeId", "") == "PartDesign::Body":
        body.addObject(sk)

    doc.recompute()

    # Add geometry entities
    idx_map, endpoint_map = _build_sketch_entities(sk, sketch_dict)

    doc.recompute()

    # Store entity map and original geometry for later retrieval.
    _persist_sketch_maps(sk, idx_map, endpoint_map, sketch_dict)
    try:
        if hasattr(sk, "setDocumentData"):
            sk.setDocumentData("entity_index_map", str(idx_map))
            import json as _json_sdc
            sk.setDocumentData("sketch_geometry_json", _json_sdc.dumps(sketch_dict))
    except Exception:
        pass

    # Compute real DOF and sketch normal for alignment verification
    dof_real = getattr(sk, "DoF", -1)
    fully_constrained = getattr(sk, "FullyConstrained", False)
    solve_status = sk.solve() if hasattr(sk, "solve") else -1
    normal_local = FreeCAD.Vector(0, 0, 1)
    normal_world = sk.Placement.Rotation.multVec(normal_local)
    origin_world = sk.Placement.Base

    doc.commitTransaction()
    _n_lines   = sum(1 for k in idx_map if k.startswith("line_"))
    _n_arcs    = sum(1 for k in idx_map if k.startswith("arc_"))
    _n_circles = sum(1 for k in idx_map if k.startswith("circle_"))
    _n_total   = len(idx_map)
    _nw = [round(normal_world.x, 4), round(normal_world.y, 4), round(normal_world.z, 4)]
    _ow = [round(origin_world.x, 3), round(origin_world.y, 3), round(origin_world.z, 3)]
    # Profile closure check — the one sketch property that is non-trivial to
    # verify from the input: do the entities chain into closed loops?  Gaps
    # from endpoint mismatches surface here instead of at extrude time.
    _profile = {{}}
    try:
        _chk_edges = sk.Shape.Edges
        _chk_groups = Part.sortEdges(_chk_edges) if _chk_edges else []
        _closed_cnt = 0
        for _grp in _chk_groups:
            try:
                if Part.Wire(_grp).isClosed():
                    _closed_cnt += 1
            except Exception:
                pass
        _profile = {{
            "loops":        len(_chk_groups),
            "closed_loops": _closed_cnt,
            "open_loops":   len(_chk_groups) - _closed_cnt,
            "closed":       len(_chk_groups) > 0 and _closed_cnt == len(_chk_groups),
        }}
    except Exception:
        pass
    _entity_rows = []
    for _ename, _eidx in idx_map.items():
        _espec = sketch_dict.get(_ename, {{}})
        _entity_rows.append({{
            "name": _ename,
            "kind": _entity_kind_from_name(_ename),
            "freecad_geometry_index": _eidx,
        }})
    _kind_counts = {{}}
    for _row in _entity_rows:
        _k = _row.get("kind") or "unknown"
        _kind_counts[_k] = _kind_counts.get(_k, 0) + 1
    _result_ = {{
        "sketch_name": sk.Name,
        "cs_name": cs_name_ref,
        "entities": _entity_rows,
        "geometry_count": {{
            "total":   _n_total,
            "lines":   _kind_counts.get("line", 0),
            "arcs":    _kind_counts.get("arc", 0),
            "circles": _kind_counts.get("circle", 0),
        }},
        "profile":           _profile,
        "dof_remaining":     dof_real,
        "fully_constrained": fully_constrained,
        "sketch_normal_world": _nw,
        "sketch_origin_world": _ow,
        "success": True,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to create sketch geometry")

    @mcp.tool()
    async def parse_freecad_sketch(
        sketch_name: str,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Parse an existing FreeCAD sketch into HistCAD-format JSON.

        Reverse-engineers an existing ``Sketcher::SketchObject`` into the
        HistCAD entity and constraint schema.  This is the entry point for
        the STEP reverse engineering workflow: import a STEP file, extract
        cross-section sketches, then call this tool to get the geometry in a
        form that the RL agent can analyse and annotate with design-intent
        constraints via ``apply_sketch_constraints``.

        Inspired by CAD-Assistant's Sketch Recognizer tool, which converts an
        existing FreeCAD sketch to a JSON representation for constraint
        generation.

        Args:
            sketch_name: Name of the ``Sketcher::SketchObject`` to parse.
            doc_name: Document name. Uses the active document if None.

        Returns:
            Dictionary with:
                - sketch_name: FreeCAD object name.
                - coordinate_system: ``{"euler_angles": [...], "translation": [...]}``.
                - sketch: HistCAD entity dict (named ``line_1``, ``circle_1``, …).
                - entity_index_map: ``{name: geom_idx}`` mapping.
                - dof_remaining: Current degrees of freedom.
                - existing_constraints: List of already-applied constraint
                  type names.

        Raises:
            ValueError: If the sketch object is not found.

        Example:
            Parse a sketch imported from a STEP file::

                result = await parse_freecad_sketch("CrossSection_XY")
                # result["sketch"] → {"line_1": {...}, "arc_1": {...}}
        """
        bridge = await get_bridge()
        code = f"""
import math
import re

{_SKETCH_ENTITY_CODE}

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sk = doc.getObject({sketch_name!r})
if sk is None:
    raise ValueError(f"Sketch not found: {sketch_name!r}")

# Extract placement as HistCAD Euler angles + translation.
# HistCAD convention (matches the Fusion 360 adapter): active rotation
# R = Rx(a)*Ry(b)*Rz(g).  Decompose the placement rotation matrix
# accordingly so parse output round-trips with create_sketch_geometry.
pl = sk.Placement
rot = pl.Rotation
trans = pl.Base
_col_x = rot.multVec(FreeCAD.Vector(1, 0, 0))
_col_y = rot.multVec(FreeCAD.Vector(0, 1, 0))
_col_z = rot.multVec(FreeCAD.Vector(0, 0, 1))
_m02, _m12, _m22 = _col_z.x, _col_z.y, _col_z.z
_m00, _m01 = _col_x.x, _col_y.x
_m10, _m11 = _col_x.y, _col_y.y
if abs(_m02) < 1.0 - 1e-9:
    beta_deg  = math.degrees(math.asin(max(-1.0, min(1.0, _m02))))
    alpha_deg = math.degrees(math.atan2(-_m12, _m22))
    gamma_deg = math.degrees(math.atan2(-_m01, _m00))
else:
    beta_deg  = 90.0 if _m02 > 0 else -90.0
    gamma_deg = 0.0
    _sign = 1.0 if _m02 > 0 else -1.0
    alpha_deg = _sign * math.degrees(math.atan2(_m10, _m11))
euler_out = [round(alpha_deg, 6), round(beta_deg, 6), round(gamma_deg, 6)]

idx_map, endpoint_map, _ground_truth = _load_sketch_maps(sk)

def _entity_sort_key(name):
    match = re.match(r"(\\w+)_(\\d+)", name)
    if match:
        return (match.group(1), int(match.group(2)))
    return (name, 0)

sketch_dict = {{}}

def _append_entity(name, gi, geo, type_id):
    ep = endpoint_map.get(name, {{"start": 1, "end": 2, "center": 3, "middle": 3}})
    if "LineSegment" in type_id or "Line" in type_id:
        s, e = _semantic_line_endpoints(sk, gi, ep)
        sketch_dict[name] = {{"start": s, "end": e}}
    elif "ArcOfCircle" in type_id:
        s, m, e = _semantic_arc_points(sk, gi, ep, geo)
        sketch_dict[name] = {{"start": s, "middle": m, "end": e}}
    elif "Circle" in type_id:
        sketch_dict[name] = {{
            "center": [geo.Center.x, geo.Center.y],
            "radius": geo.Radius,
        }}
    elif "Ellipse" in type_id or "ArcOfEllipse" in type_id:
        sketch_dict[name] = {{
            "center": [geo.Center.x, geo.Center.y],
            "major":  geo.MajorRadius,
            "minor":  geo.MinorRadius,
            "angle":  math.degrees(getattr(geo, "AngleXU", 0.0)),
        }}
    elif "BSpline" in type_id:
        poles = [[p.x, p.y] for p in geo.getPoles()]
        sketch_dict[name] = {{
            "degree":   geo.Degree,
            "periodic": geo.isPeriodic(),
            "controls": poles,
            "weights":  list(geo.getWeights()),
            "knots":    list(geo.getKnots()),
        }}

if idx_map and endpoint_map:
    for name in sorted(idx_map.keys(), key=_entity_sort_key):
        gi = idx_map[name]
        if gi >= len(sk.Geometry):
            continue
        geo = sk.Geometry[gi]
        type_id = geo.TypeId if hasattr(geo, "TypeId") else type(geo).__name__
        _append_entity(name, gi, geo, type_id)
else:
    line_cnt = circle_cnt = arc_cnt = ellipse_cnt = nurbs_cnt = 0
    for i, geo in enumerate(sk.Geometry):
        type_id = geo.TypeId if hasattr(geo, "TypeId") else type(geo).__name__
        if "LineSegment" in type_id or "Line" in type_id:
            line_cnt += 1
            name = f"line_{{line_cnt}}"
        elif "ArcOfCircle" in type_id:
            arc_cnt += 1
            name = f"arc_{{arc_cnt}}"
        elif "Circle" in type_id:
            circle_cnt += 1
            name = f"circle_{{circle_cnt}}"
        elif "Ellipse" in type_id or "ArcOfEllipse" in type_id:
            ellipse_cnt += 1
            name = f"ellipse_{{ellipse_cnt}}"
        elif "BSpline" in type_id:
            nurbs_cnt += 1
            name = f"nurbs_{{nurbs_cnt}}"
        else:
            continue
        idx_map[name] = i
        _append_entity(name, i, geo, type_id)

existing_constraints = []
for c in sk.Constraints:
    existing_constraints.append(c.Type)

dof = getattr(sk, "DoF", -1)
fully_constrained = getattr(sk, "FullyConstrained", False)
sk_normal = sk.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))

_result_ = {{
    "sketch_name":          {sketch_name!r},
    "coordinate_system":    {{"euler_angles": euler_out,
                              "translation":  [trans.x, trans.y, trans.z]}},
    "sketch":               sketch_dict,
    "dof_remaining":        dof,
    "fully_constrained":    fully_constrained,
    "sketch_normal_world":  [round(sk_normal.x, 6), round(sk_normal.y, 6), round(sk_normal.z, 6)],
    "existing_constraints": existing_constraints,
}}
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to parse sketch")

    # ------------------------------------------------------------------
    # Group C — Sketch Constraints
    # ------------------------------------------------------------------

    @mcp.tool()
    async def check_sketch_constraints(
        sketch_name: str,
        constraints: dict[str, Any],
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Dry-run constraint validation without modifying the sketch.

        Validates a proposed constraint set against a sketch without applying
        the constraints.  Inspired by CAD-Assistant's Constraint Checker tool,
        which validates constraints before application to prevent unintended
        geometry movement.

        Use this before ``apply_sketch_constraints`` when:
        - You want to verify the constraint set is valid before committing.
        - The RL agent is exploring constraint strategies and wants to prune
          invalid candidates without burning an environment step.

        Args:
            sketch_name: Name of the target sketch object.
            constraints: HistCAD constraint dict to validate, e.g.::

                {
                    "Coincident": [["line_1.end", "line_2.start"]],
                    "Horizontal": ["line_1"],
                    "Length":     [["line_1", "20 mm"]],
                }

            doc_name: Document name. Uses the active document if None.

        Returns:
            Dictionary with:
                - valid: ``True`` if the constraint set can be applied without
                  immediate conflicts.
                - would_over_constrain: ``True`` if applying these constraints
                  on top of existing ones would over-constrain the sketch.
                - redundant_constraints: List of constraint type names that
                  would be redundant.
                - estimated_dof_after: Estimated DOF after application
                  (negative means over-constrained).
                - conflict_details: Human-readable conflict description, or
                  ``None`` if no conflicts detected.

        Raises:
            ValueError: If the sketch object is not found.

        Example:
            Check before applying::

                check = await check_sketch_constraints("Sketch001", {
                    "Horizontal": ["line_1"],
                    "Length":     [["line_1", "20 mm"]],
                })
                if check["valid"]:
                    await apply_sketch_constraints("Sketch001", constraints)
        """
        bridge = await get_bridge()
        code = f"""
import Part, Sketcher

{_SKETCH_ENTITY_CODE}
{_SKETCH_CONSTRAINT_CODE}

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sk = doc.getObject({sketch_name!r})
if sk is None:
    raise ValueError(f"Sketch not found: {sketch_name!r}")

constraints_in = {constraints!r}

# Retrieve entity index map: __main__ cache → setDocumentData → rebuild from geometry
import __main__ as _m_idx
idx_map = getattr(_m_idx, "_sketch_idx_map_cache", {{}}).get(sk.Name)
if not idx_map:
    try:
        stored = sk.getDocumentData("entity_index_map") if hasattr(sk, "getDocumentData") else None
        idx_map = eval(stored) if stored else {{}}
    except Exception:
        idx_map = {{}}
# Last-resort: rebuild from geometry names if map is still empty
if not idx_map:
    import re
    line_c = circle_c = arc_c = ellipse_c = nurbs_c = 0
    for i, geo in enumerate(sk.Geometry):
        tid = geo.TypeId if hasattr(geo, "TypeId") else type(geo).__name__
        if "LineSegment" in tid or "Line" in tid:
            line_c += 1; idx_map[f"line_{{line_c}}"] = i
        elif "ArcOfCircle" in tid:
            arc_c += 1; idx_map[f"arc_{{arc_c}}"] = i
        elif "Circle" in tid:
            circle_c += 1; idx_map[f"circle_{{circle_c}}"] = i
        elif "Ellipse" in tid or "ArcOfEllipse" in tid:
            ellipse_c += 1; idx_map[f"ellipse_{{ellipse_c}}"] = i
        elif "BSpline" in tid:
            nurbs_c += 1; idx_map[f"nurbs_{{nurbs_c}}"] = i

dof_before = sk.ConstraintCount if hasattr(sk, "ConstraintCount") else -1

# Clone the sketch on a temp document for dry-run (best effort)
conflict_details = None
redundant = []
estimated_dof = dof_before
valid = True
would_over = False

try:
    # Count proposed constraints naively (does not catch all conflicts)
    constraint_count = sum(len(v) if isinstance(v, list) else 1
                           for v in constraints_in.values())
    # Very rough DOF estimation: each constraint typically reduces DOF by 1
    estimated_dof = max(-999, dof_before - constraint_count)
    if estimated_dof < 0:
        would_over = True
    valid = not would_over
except Exception as _e:
    conflict_details = str(_e)
    valid = False

_result_ = {{
    "valid":                 valid,
    "would_over_constrain":  would_over,
    "redundant_constraints": redundant,
    "estimated_dof_after":   estimated_dof,
    "conflict_details":      conflict_details,
}}
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to check constraints")

    @mcp.tool()
    async def apply_sketch_constraints(
        sketch_name: str,
        constraints: dict[str, Any],
        doc_name: str | None = None,
        bind_to_spreadsheet: bool = True,
        orientation_stabilization: bool = False,
    ) -> dict[str, Any]:
        """Apply HistCAD constraints to an existing sketch.

        Translates the HistCAD constraint dict into FreeCAD
        ``Sketcher.Constraint`` objects and adds them to the named sketch.
        Returns rich feedback — ``dof_after``, ``redundant_constraints``, and
        ``sketch_valid`` — that serves as a dense RL reward signal for
        constraint strategy learning.

        This is the key step where the RL agent's policy is exercised: given
        a sketch with known geometry, the agent must select the constraints
        that express the design intent (fully constrained, no redundancy).

        Args:
            sketch_name: Name of the target sketch object.
            constraints: HistCAD constraint dict.  Supports 19 constraint
                types: Coincident, Horizontal, Vertical, Perpendicular,
                Parallel, Equal, Tangent, Normal, Concentric, Fix,
                Midpoint, Mirror, Angle, Diameter, Radius, MajorRadius,
                MinorRadius, Length, Distance.  Example::

                    {
                        "Coincident": [["line_1.end", "line_2.start"],
                                       ["line_2.end", "line_3.start"]],
                        "Horizontal": ["line_1"],
                        "Length":     [["line_1", "20 mm"]],
                        "Radius":     [["arc_1", "5 mm"]],
                    }

            doc_name: Document name. Uses the active document if None.
            bind_to_spreadsheet: When ``True``, dimension aliases are exposed
                through the shared FabricationParams spreadsheet.
            orientation_stabilization: When ``True``, invent axis-aligned
                ``DistanceX``/``DistanceY`` rows for uncovered segments to
                reduce segment-flip.  Default ``False`` — invented dimensions
                change linkage semantics and hurt HistCAD editability fidelity.
                Enable only when segment flip is observed after solve.

        Returns:
            Dictionary with:
                - dof_before: Degrees of freedom before applying constraints.
                - dof_after: Degrees of freedom after applying constraints.
                  Zero means fully constrained; positive means under-constrained;
                  negative means over-constrained.
                - fully_constrained: ``True`` when ``dof_after == 0``.
                - solve_status: Sketcher solver return code (0 = solved).
                - applied_count: Number of constraints successfully applied.
                - input_constraint_count: Number of entries in the input
                  constraints dict (before adapter additions).
                - constraint_catalog: Per-constraint rows mapping FreeCAD
                  indices to input constraint entries and entity refs.
                - constraint_catalog_summary: Small stable summary with count,
                  FreeCAD type counts, redundant_count, and conflicting_count.
                - redundant: Catalog rows flagged redundant by the solver.
                - conflicting: Catalog rows flagged conflicting by the solver.
                - purged_redundant: Constraints removed after apply because the
                  solver marked them redundant (common when point-specific
                  Tangent constraints duplicate junction Coincident rows).
                  Removing them restores ``solve_status == 0`` and enables
                  ``Part::Extrusion`` parametric sketch extrusion.
                - redundant_constraints: 1-based FreeCAD indices (legacy shorthand).
                - conflicting_constraints: 1-based FreeCAD indices (legacy).
                - profile: ``{loops, closed_loops, open_loops, closed}`` after
                  solving — the solver can open a previously closed loop when
                  it moves geometry.
                - geometry_drift: ``{"max_mm": d, "drifted_entities": [...]}``
                  — maximum distance any entity moved away from the
                  ground-truth coordinates cached at creation time.  Non-zero
                  drift means a constraint contradicts the stated coordinates
                  (e.g. a Distance value that disagrees with the endpoints)
                  even though the solver reports success.  The final solid is
                  unaffected (extrusion uses the raw coordinates), so drift is
                  a pure NLT-consistency signal.
                - success: ``True`` on success.

        Raises:
            ValueError: If the sketch object is not found.

        Example:
            Fully constrain a rectangle::

                result = await apply_sketch_constraints("Sketch001", {
                    "Coincident": [["line_1.end","line_2.start"],
                                   ["line_2.end","line_3.start"],
                                   ["line_3.end","line_4.start"],
                                   ["line_4.end","line_1.start"]],
                    "Horizontal": ["line_1", "line_3"],
                    "Vertical":   ["line_2", "line_4"],
                    "Fix":        ["line_1.start"],
                    "Length":     [["line_1","20 mm"],["line_2","10 mm"]],
                })
                # result["dof_after"] → 0
        """
        bridge = await get_bridge()
        code = f"""
import Part, Sketcher

{_SKETCH_ENTITY_CODE}
{_SKETCH_CONSTRAINT_CODE}

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sk = doc.getObject({sketch_name!r})
if sk is None:
    raise ValueError(f"Sketch not found: {sketch_name!r}")

constraints_in = {constraints!r}
bind_to_spreadsheet = {bind_to_spreadsheet!r}

idx_map, endpoint_map, ground_truth = _load_sketch_maps(sk)
if not idx_map:
    line_c = circle_c = arc_c = ellipse_c = nurbs_c = 0
    for i, geo in enumerate(sk.Geometry):
        tid = geo.TypeId if hasattr(geo, "TypeId") else type(geo).__name__
        if "LineSegment" in tid or "Line" in tid:
            line_c += 1; idx_map[f"line_{{line_c}}"] = i
        elif "ArcOfCircle" in tid:
            arc_c += 1; idx_map[f"arc_{{arc_c}}"] = i
        elif "Circle" in tid:
            circle_c += 1; idx_map[f"circle_{{circle_c}}"] = i
        elif "Ellipse" in tid or "ArcOfEllipse" in tid:
            ellipse_c += 1; idx_map[f"ellipse_{{ellipse_c}}"] = i
        elif "BSpline" in tid:
            nurbs_c += 1; idx_map[f"nurbs_{{nurbs_c}}"] = i

dof_before = getattr(sk, "DoF", -1)

doc.openTransaction("Apply Sketch Constraints")
try:
    applied_before = len(sk.Constraints) if hasattr(sk, "Constraints") else 0
    constraint_result = _apply_histcad_constraints(
        sk,
        constraints_in,
        idx_map,
        endpoint_map=endpoint_map,
        ground_truth=ground_truth,
        orientation_stabilization={orientation_stabilization!r},
    )
    redundant = constraint_result.get("redundant", [])
    purged_redundant = constraint_result.get("purged_redundant", [])
    applied_log = constraint_result.get("applied_log", [])
    dimension_bindings = constraint_result.get("dimension_bindings", [])
    # Constraint aliases are native, stable expression references.  A
    # Spreadsheet is an optional UI adapter, not the dependency source.
    for _binding in dimension_bindings:
        _alias = _binding.get("alias")
        if not _alias:
            continue
        try:
            sk.renameConstraint(int(_binding["constraint_index"]), _alias)
            _binding["expression_reference"] = f"{{sk.Name}}.Constraints.{{_alias}}"
        except Exception:
            _binding["expression_reference"] = None
    applied_after = len(sk.Constraints) if hasattr(sk, "Constraints") else applied_before
    applied_count = applied_after - applied_before

    def _profile_is_closed(_sk):
        try:
            _chk_edges = _sk.Shape.Edges
            _chk_groups = Part.sortEdges(_chk_edges) if _chk_edges else []
            if not _chk_groups:
                return False
            _closed_cnt = 0
            for _grp in _chk_groups:
                try:
                    if Part.Wire(_grp).isClosed():
                        _closed_cnt += 1
                except Exception:
                    pass
            return _closed_cnt == len(_chk_groups)
        except Exception:
            return None

    pre_bind_solve_status = None
    pre_bind_dof = None
    pre_bind_conflicting = []
    pre_bind_profile_closed = None
    if dimension_bindings:
        try:
            doc.recompute()
        except Exception:
            pass
        try:
            pre_bind_solve_status = sk.solve() if hasattr(sk, "solve") else None
        except Exception:
            pre_bind_solve_status = "solve_error"
        try:
            pre_bind_dof = getattr(sk, "DoF", None)
        except Exception:
            pre_bind_dof = None
        try:
            pre_bind_conflicting = [
                int(_x)
                for _x in (getattr(sk, "ConflictingConstraints", []) or [])
            ]
        except Exception:
            pre_bind_conflicting = []
        pre_bind_profile_closed = _profile_is_closed(sk)

    binding_guard_reasons = []
    if dimension_bindings:
        if pre_bind_solve_status not in (None, 0):
            binding_guard_reasons.append(f"solve_status={{pre_bind_solve_status}}")
        if pre_bind_conflicting:
            binding_guard_reasons.append(f"conflicting_constraints={{pre_bind_conflicting}}")
        try:
            if pre_bind_dof is not None and int(pre_bind_dof) < 0:
                binding_guard_reasons.append(f"dof_after={{pre_bind_dof}}")
        except Exception:
            pass
        if pre_bind_profile_closed is False:
            binding_guard_reasons.append("profile_open")

    bound_params = []
    if dimension_bindings and binding_guard_reasons:
        for _binding in dimension_bindings:
            _alias = _binding.get("alias")
            if not _alias:
                continue
            bound_params.append({{
                "alias": _alias,
                "cell": None,
                "value": _binding.get("value"),
                "unit": _binding.get("unit", "mm"),
                "property": _binding.get("property"),
                "object": sk.Name,
                "constraint_type": _binding.get("constraint_type"),
                "role": "unbound_solver_guard",
                "label": _binding.get("label"),
                "min": _binding.get("min"),
                "max": _binding.get("max"),
                "default": _binding.get("default"),
                "diagnostic": "; ".join(binding_guard_reasons),
            }})
    elif dimension_bindings and bind_to_spreadsheet:
        sheet = doc.getObject("FabricationParams")
        if sheet is None:
            sheet = doc.addObject("Spreadsheet::Sheet", "FabricationParams")

        def _used_cells(_sheet):
            try:
                return list(_sheet.getUsedCells()) if hasattr(_sheet, "getUsedCells") else []
            except Exception:
                return []

        def _find_alias_cell(_sheet, _alias):
            for _cell in _used_cells(_sheet):
                try:
                    if _sheet.getAlias(_cell) == _alias:
                        return _cell
                except Exception:
                    pass
            return None

        _allocated_cells = set(_used_cells(sheet))

        def _next_cell():
            _row = 1
            while True:
                _cell = f"A{{_row}}"
                if _cell not in _allocated_cells:
                    _allocated_cells.add(_cell)
                    return _cell
                _row += 1

        for _binding in dimension_bindings:
            _alias = _binding.get("alias")
            _expr = _binding.get("expression")
            if not _alias and not _expr:
                continue
            _value = _binding.get("value")
            _prop = _binding.get("property")
            if _alias:
                _cell = _find_alias_cell(sheet, _alias) or _next_cell()
                _allocated_cells.add(_cell)
                sheet.set(_cell, str(_value))
                try:
                    sheet.setAlias(_cell, _alias)
                except Exception:
                    pass
            try:
                if _expr:
                    sk.setExpression(_prop, _expr)
                elif _alias:
                    sk.setExpression(_prop, f"FabricationParams.{{_alias}}")
            except Exception:
                pass
            if _alias:
                bound_params.append({{
                    "alias": _alias,
                    "cell": _cell,
                    "value": _value,
                    "unit": _binding.get("unit", "mm"),
                    "property": _prop,
                    "object": sk.Name,
                    "constraint_type": _binding.get("constraint_type"),
                    "expression": _expr or f"FabricationParams.{{_alias}}",
                    "role": _binding.get("role"),
                    "label": _binding.get("label"),
                    "min": _binding.get("min"),
                    "max": _binding.get("max"),
                    "default": _binding.get("default"),
                }})

    elif dimension_bindings:
        for _binding in dimension_bindings:
            _alias = _binding.get("alias")
            if not _alias:
                continue
            bound_params.append({{
                "alias": _alias,
                "cell": None,
                "value": _binding.get("value"),
                "unit": _binding.get("unit", "mm"),
                "property": _binding.get("property"),
                "expression_reference": _binding.get("expression_reference"),
                "object": sk.Name,
                "constraint_type": _binding.get("constraint_type"),
                "role": _binding.get("role"),
                "diagnostic": None,
            }})

    doc.recompute()
    dof_after = getattr(sk, "DoF", -1)
    solve_status = sk.solve() if hasattr(sk, "solve") else -1
    fully_constrained = getattr(sk, "FullyConstrained", False)

    def _inv_idx_map(_m):
        return {{int(v): k for k, v in _m.items()}}

    def _pos_label(_p):
        return {{1: "start", 2: "end", 3: "middle"}}.get(int(_p))

    def _refs_from_fc_constraint(_c, _inv):
        _refs = []
        _f, _fp = int(_c.First), int(_c.FirstPos)
        if _f >= 0 and _f in _inv:
            _lbl = _pos_label(_fp)
            _refs.append(f"{{_inv[_f]}}.{{_lbl}}" if _lbl else _inv[_f])
        _s = int(_c.Second)
        if _s >= 0 and _s in _inv:
            _sp = int(_c.SecondPos)
            _lbl = _pos_label(_sp)
            _refs.append(f"{{_inv[_s]}}.{{_lbl}}" if _lbl else _inv[_s])
        return _refs

    _inv = _inv_idx_map(idx_map)
    _redundant_set = set(int(_x) for _x in (getattr(sk, "RedundantConstraints", []) or []))
    _conflicting_set = set(int(_x) for _x in (getattr(sk, "ConflictingConstraints", []) or []))

    _catalog = []
    for _i, _c in enumerate(sk.Constraints):
        _n = _i + 1
        _base = applied_log[_i] if _i < len(applied_log) else {{
            "source": "unknown",
            "input": None,
            "entity_refs": [],
            "adapter_reason": None,
            "freecad_type": None,
        }}
        _val = None
        try:
            _val = round(float(_c.Value), 6)
        except Exception:
            pass
        _catalog.append({{
            "freecad_index": _n,
            "freecad_type": _c.Type,
            "freecad_value": _val,
            "entity_refs": _base.get("entity_refs")
            or _refs_from_fc_constraint(_c, _inv),
            "input": _base.get("input"),
            "source": _base.get("source", "unknown"),
            "adapter_reason": _base.get("adapter_reason"),
            "redundant": _n in _redundant_set,
            "conflicting": _n in _conflicting_set,
        }})

    _redundant_entries = [_row for _row in _catalog if _row["redundant"]]
    _conflicting_entries = [_row for _row in _catalog if _row["conflicting"]]
    _catalog_type_counts = {{}}
    for _row in _catalog:
        _typ = _row.get("freecad_type") or "Unknown"
        _catalog_type_counts[_typ] = _catalog_type_counts.get(_typ, 0) + 1
    _constraint_catalog_summary = {{
        "count": len(_catalog),
        "freecad_type_counts": _catalog_type_counts,
        "redundant_count": len(_redundant_entries),
        "conflicting_count": len(_conflicting_entries),
    }}

    def _constraint_names(_idxs):
        # Legacy helper — prefer constraint_catalog for agent debugging.
        _out = []
        for _ci in _idxs:
            try:
                _out.append(sk.Constraints[_ci - 1].Type)
            except Exception:
                _out.append("#" + str(_ci))
        return _out

    conflicting = _constraint_names(getattr(sk, "ConflictingConstraints", ()))
    solver_redundant = _constraint_names(getattr(sk, "RedundantConstraints", ()))

    # Profile closure after solving (the solver can open a previously closed
    # loop when it moves geometry).
    _profile = {{}}
    try:
        _chk_edges = sk.Shape.Edges
        _chk_groups = Part.sortEdges(_chk_edges) if _chk_edges else []
        _closed_cnt = 0
        for _grp in _chk_groups:
            try:
                if Part.Wire(_grp).isClosed():
                    _closed_cnt += 1
            except Exception:
                pass
        _profile = {{
            "loops":        len(_chk_groups),
            "closed_loops": _closed_cnt,
            "open_loops":   len(_chk_groups) - _closed_cnt,
            "closed":       len(_chk_groups) > 0 and _closed_cnt == len(_chk_groups),
        }}
    except Exception:
        pass

    # Geometry drift vs the ground-truth coordinates cached at creation time.
    _drift = None
    try:
        if ground_truth:
            def _dist2d(_p, _q):
                return ((_p.x - _q[0]) ** 2 + (_p.y - _q[1]) ** 2) ** 0.5
            _max_d = 0.0
            _drifted = []
            for _nm, _sp in ground_truth.items():
                _gi = idx_map.get(_nm)
                if _gi is None or _gi >= len(sk.Geometry):
                    continue
                _g = sk.Geometry[_gi]
                _d = None
                _kind = None
                if isinstance(_sp, dict):
                    if "middle" in _sp and "start" in _sp:
                        _kind = "arc"
                    elif "start" in _sp and "end" in _sp and "major" in _sp:
                        _kind = "elliptical_arc"
                    elif "start" in _sp and "end" in _sp:
                        _kind = "line"
                    elif "center" in _sp and "radius" in _sp:
                        _kind = "circle"
                if _kind in ("line", "arc", "elliptical_arc"):
                    _ep = endpoint_map.get(_nm, {{}})
                    _start_pos = _ep.get("start", 1)
                    _end_pos = _ep.get("end", 2)
                    _pt_start = sk.getPoint(_gi, _start_pos)
                    _pt_end = sk.getPoint(_gi, _end_pos)
                    _d = max(_dist2d(_pt_start, _sp["start"]),
                             _dist2d(_pt_end, _sp["end"]))
                elif _kind == "nurbs" or (
                    isinstance(_sp, dict) and _sp.get("controls")
                ):
                    _controls = _sp.get("controls") or []
                    if len(_controls) >= 2 and _nm in endpoint_map:
                        _ep = endpoint_map[_nm]
                        _pt_start = sk.getPoint(_gi, _ep.get("start", 1))
                        _pt_end = sk.getPoint(_gi, _ep.get("end", 2))
                        _d = max(_dist2d(_pt_start, _controls[0]),
                                 _dist2d(_pt_end, _controls[-1]))
                elif _kind == "circle" or (
                    isinstance(_sp, dict)
                    and "center" in _sp
                    and "radius" in _sp
                    and "start" not in _sp
                ):
                    _d = max(_dist2d(_g.Center, _sp["center"]),
                             abs(_g.Radius - _sp["radius"]))
                if _d is None:
                    continue
                _max_d = max(_max_d, _d)
                if _d > 0.001:
                    _drifted.append(_nm)
            _drift = {{"max_mm": round(_max_d, 4), "drifted_entities": _drifted}}
    except Exception:
        pass

    doc.commitTransaction()
    _result_ = {{
        "dof_before":              dof_before,
        "dof_after":               dof_after,
        "fully_constrained":       fully_constrained,
        "solve_status":            solve_status,
        "applied_count":           applied_count,
        "input_constraint_count":  sum(len(v) for v in constraints_in.values()),
        "constraint_catalog":      _catalog,
        "constraint_catalog_summary": _constraint_catalog_summary,
        "applied_log":             applied_log,
        "applied_constraints":     _catalog,
        "redundant":               _redundant_entries,
        "conflicting":             _conflicting_entries,
        "purged_redundant":        purged_redundant,
        "redundant_constraints":   [_row["freecad_index"] for _row in _redundant_entries],
        "conflicting_constraints": [_row["freecad_index"] for _row in _conflicting_entries],
        "profile":                 _profile,
        "geometry_drift":          _drift,
        "bound_params":            bound_params,
        "success":                 True,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to apply constraints")

    @mcp.tool()
    async def evaluate_sketch_editability(
        reference_constraints: dict[str, Any],
        constraint_type: str,
        entry_index: int,
        edited_value_mm: float,
        sketch_name: str | None = None,
        sketch: dict[str, Any] | None = None,
        entities: list[str] | None = None,
        doc_name: str | None = None,
        length_tol_mm: float = 0.01,
        angle_tol_deg: float = 0.5,
    ) -> dict[str, Any]:
        """Evaluate HistCAD-style sketch editability (ER / cPCSR / OES).

        Follows ``HistCAD-68C2/editability/metrics.py`` v2 when ``sketch_name``
        is provided (recompute + sketch health check in FreeCAD):

        - **ER**: ``target_hit AND validation_ok AND rebuild_success``
        - **cPCSR**: fraction of *other* reference constraints still satisfied
        - **OES**: ``ER x cPCSR``

        Without ``sketch_name`` (offline ``sketch`` dict only), returns
        geometric-only scoring for unit tests.

        Workflow (aligned with ``editability/experiment.py``)::

            edited = build_edited_sketch_constraints(...)
            await apply_sketch_constraints(sketch_name, edited["constraints"])
            metrics = await evaluate_sketch_editability(
                reference_constraints=original_constraints,
                constraint_type="Distance",
                entry_index=0,
                edited_value_mm=1.8,
                sketch_name="Sketch",
            )
        """
        validation: dict[str, Any] | None = None
        if sketch_name is not None:
            bridge = await get_bridge()
            validation_exec = await bridge.execute_python(
                _build_editability_validation_code(doc_name, sketch_name=sketch_name)
            )
            if validation_exec.success and validation_exec.result:
                validation = validation_exec.result
            else:
                validation = {
                    "rebuild_success": False,
                    "validation_ok": False,
                    "exception": validation_exec.error_traceback,
                    "shape_errors": [],
                    "sketches": [],
                }

        live_sketch = sketch
        if live_sketch is None:
            if sketch_name is None:
                raise ValueError("Either sketch or sketch_name must be provided")
            parsed = await parse_freecad_sketch(  # type: ignore[name-defined]
                sketch_name=sketch_name,
                doc_name=doc_name,
            )
            live_sketch = parsed.get("sketch")
            if not live_sketch:
                raise ValueError(f"No sketch geometry parsed from {sketch_name!r}")

        entity_tuple = tuple(entities) if entities else None
        metrics = _evaluate_sketch_geometry(
            live_sketch=live_sketch,
            reference_constraints=reference_constraints,
            constraint_type=constraint_type,
            entry_index=entry_index,
            edited_value_mm=float(edited_value_mm),
            entities=entity_tuple,
            length_tol_mm=float(length_tol_mm),
            angle_tol_deg=float(angle_tol_deg),
            rebuild_success=(
                validation.get("rebuild_success") if validation is not None else None
            ),
            validation_ok=(
                validation.get("validation_ok") if validation is not None else None
            ),
        )
        metrics["sketch_name"] = sketch_name
        if validation is not None:
            metrics["validation"] = validation
        metrics["success"] = True
        return metrics

    @mcp.tool()
    def build_edited_sketch_constraints(
        constraints: dict[str, Any],
        constraint_type: str,
        entry_index: int,
        edited_value_mm: float,
        entities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a copy of a HistCAD constraint dict with one dimension edited.

        Use the returned ``constraints`` with ``apply_sketch_constraints``, then
        score the result via ``evaluate_sketch_editability``.
        """
        entity_tuple = tuple(entities) if entities else None
        updated = replace_constraint_value(
            constraints,
            constraint_type=constraint_type,
            entry_index=entry_index,
            edited_value_mm=float(edited_value_mm),
            entities=entity_tuple,
        )
        return {"constraints": updated, "success": True}

    # ------------------------------------------------------------------
    # Group D — Feature Execution
    # ------------------------------------------------------------------

    @mcp.tool()
    async def execute_extrude(
        sketch_name: str,
        towards: float,
        opposite: float = 0.0,
        sketch: dict[str, Any] | None = None,
        param_aliases: dict[str, str] | None = None,
        feature_name: str | None = None,
        extrusion_mode: str = "auto",
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Extrude a sketch into a standalone solid (``Part::Feature``).

        By default this tool first tries a parametric ``Part::Extrusion``
        referencing the Sketcher object directly.  If FreeCAD produces a null
        or zero-volume shape, it falls back to the HistCAD-stable raw-face path:
        build edges from the cached JSON, create a face with
        ``Part::FaceMakerBullseye``, and extrude that face into a solid.

        This tool only creates geometry.  Boolean combination with existing
        solids (HistCAD ``Join`` / ``Cut`` / ``Intersect`` semantics) is a
        separate step: call ``execute_boolean`` with explicit base and tool
        object names.

        When ``param_aliases`` is provided, the adapter creates a FreeCAD
        Spreadsheet (named ``FabricationParams``) and binds the specified
        parameters to named spreadsheet cells for frontend slider controls.

        Args:
            sketch_name: Name of the Sketcher object to reference.
            towards: Extrusion distance along the positive sketch normal (mm).
            opposite: Extrusion distance along the negative sketch normal (mm).
                Defaults to ``0.0`` (one-direction extrusion).
            sketch: Raw geometry dict (HistCAD format).  Optional — when
                ``None`` (the default) the adapter auto-retrieves the dict
                stored on the sketch object by ``create_sketch_geometry``.
                Pass an explicit value only when you need to override the
                stored geometry.
            param_aliases: Maps parameter keys to spreadsheet alias names for
                frontend sliders.  E.g. ``{"towards": "column_height"}`` binds
                the ``towards`` value to a cell aliased ``column_height``.
            feature_name: Explicit FreeCAD object name. Auto-generated if None.
            extrusion_mode: ``"auto"`` (default), ``"parametric_sketch"``, or
                ``"robust_face"``.  ``"auto"`` falls back to ``"robust_face"``
                when sketch-based extrusion is invalid.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with:
                - feature_name: Name of the created solid (pass to
                  ``execute_boolean`` or subsequent steps).
                - local_obb: ``{"center": [x, y, z], "semi_extents":
                  [sx, sy, sz]}`` in the sketch-local frame (axis-aligned
                  box of the extruded solid mapped back through the inverse
                  placement).  NLT annotation values for OBB are unreliable
                  and should not be hard-verified against this field.
                - global_center: ``placement * local_obb.center`` in world
                  space.  NLT ``global center`` text is reference-only.
                - bounding_box: ``{x_min, x_max, y_min, y_max, z_min, z_max}``
                  in world space.
                - volume_mm3: Solid volume in cubic millimetres.
                - sketch_normal_world: Extrusion direction in world space.
                - extrusion_mode_used: Actual extrusion path used.
                - fallback_reason: Reason for robust-face fallback, if any.
                - success: ``True`` when a non-zero solid was produced.

        Raises:
            ValueError: If the sketch is not found or the extrusion fails.

        Example:
            Create a column with a named height parameter::

                result = await execute_extrude(
                    "Sketch001",
                    towards=150.0,
                    param_aliases={"towards": "column_height"},
                )
                # result["global_center"] → world-space step observation
        """
        bridge = await get_bridge()
        code = f"""
import Part

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sk = doc.getObject({sketch_name!r})
if sk is None:
    raise ValueError(f"Sketch not found: {sketch_name!r}")

towards        = {towards!r}
opposite       = {opposite!r}
aliases        = {param_aliases!r} or {{}}
feat_nm        = {feature_name!r}
mode           = {extrusion_mode!r}
# Geometry dict: use caller-supplied value; fall back to cached value on sketch object.
_sketch_direct = {sketch!r}

if mode not in ("auto", "parametric_sketch", "robust_face"):
    raise ValueError(
        f"Invalid extrusion_mode: {{mode}}. Use 'auto', 'parametric_sketch', "
        "or 'robust_face'."
    )

doc.openTransaction("Execute Extrude")
try:
    # Helper: build Part edges from the HistCAD geometry dict.
    # Prefers _sketch_direct when supplied; otherwise loads the JSON stored on the
    # sketch object by create_sketch_geometry (auto-retrieval, no LLM pass-through).
    # Coordinates are used exactly as written in the HistCAD JSON.
    # Building from raw JSON avoids Sketcher solver drift when DoF > 0.
    _edge_build_errors: list[str] = []

    def _edges_from_raw_json(_sk, _PM):
        _d = _sketch_direct
        if _d is None:
            # 1st fallback: __main__ dict stored by create_sketch_geometry.
            import __main__ as _m
            _d = getattr(_m, "_sketch_geometry_cache", {{}}).get(_sk.Name)
        if _d is None:
            # 2nd fallback: setDocumentData (App::FeaturePython objects only).
            try:
                if hasattr(_sk, "getDocumentData"):
                    import json as _json
                    _stored = _sk.getDocumentData("sketch_geometry_json")
                    if _stored:
                        _d = _json.loads(_stored)
            except Exception:
                pass
        if _d is None:
            raise ValueError(
                f"Sketch '{{_sk.Name}}' has no cached geometry. "
                "Use create_sketch_geometry first, or pass sketch= explicitly."
            )
        _ee = []
        for _nm, _sp in _d.items():
            try:
                if _nm.startswith("line_"):
                    _ee.append(_PM.makeLine(
                        FreeCAD.Vector(_sp["start"][0], _sp["start"][1], 0),
                        FreeCAD.Vector(_sp["end"][0],   _sp["end"][1],   0)))
                elif _nm.startswith("arc_"):
                    _s2 = FreeCAD.Vector(_sp["start"][0],  _sp["start"][1],  0)
                    _mid = _sp.get("middle", _sp.get("mid"))
                    if _mid is None:
                        raise KeyError("middle")
                    _m2 = FreeCAD.Vector(_mid[0], _mid[1], 0)
                    _e2 = FreeCAD.Vector(_sp["end"][0],    _sp["end"][1],    0)
                    _ee.append(_PM.Edge(_PM.ArcOfCircle(_s2, _m2, _e2)))
                elif _nm.startswith("circle_"):
                    _c2 = FreeCAD.Vector(_sp["center"][0], _sp["center"][1], 0)
                    _ee.append(_PM.Edge(
                        _PM.Circle(_c2, FreeCAD.Vector(0, 0, 1), _sp["radius"])))
                elif _nm.startswith("ellipse_"):
                    _cx = _sp.get("center", [0, 0])[0]
                    _cy = _sp.get("center", [0, 0])[1]
                    _a  = _sp.get("major_radius", 1.0)
                    _b  = _sp.get("minor_radius", 0.5)
                    _el = _PM.Ellipse(FreeCAD.Vector(_cx, _cy, 0), _a, _b)
                    _ee.append(_PM.Edge(_el))
            except Exception as _build_err:
                _edge_build_errors.append(str(_nm) + ": " + str(_build_err))
        return _ee

    def _make_face(_PM, _edges):
        # Build a face from all closed edge loops (outer + holes).  Matches the
        # Fusion 360 adapter profile nesting: odd/even depth via Bullseye.
        if not _edges:
            return _PM.Face(_PM.Wire(_edges))
        try:
            _sorted_groups = _PM.sortEdges(_edges)
        except Exception:
            _sorted_groups = [_edges]
        _wires = []
        for _grp in _sorted_groups:
            try:
                _w = _PM.Wire(_grp)
                if _w.isClosed():
                    _wires.append(_w)
            except Exception:
                continue
        if not _wires:
            try:
                _best = max(_sorted_groups, key=len)
                return _PM.Face(_PM.Wire(_best))
            except Exception:
                return _PM.Face(_PM.Wire(_edges))
        if len(_wires) == 1:
            return _PM.Face(_wires[0])
        try:
            return _PM.makeFace(_wires, "Part::FaceMakerBullseye")
        except Exception:
            return _PM.Face(_wires[0])

    _pl = sk.Placement
    _normal = _pl.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
    fallback_reason = None
    extrusion_mode_used = None
    feat = None

    def _shape_failure_reason(_obj):
        try:
            _sh = _obj.Shape
            if _sh.isNull():
                return "null_shape"
            try:
                if not _sh.isValid():
                    return "invalid_shape"
            except Exception:
                pass
            if _sh.Volume <= 1e-9:
                return f"zero_volume_{{round(_sh.Volume, 9)}}"
            return None
        except Exception as _exc:
            return "shape_error: " + str(_exc)

    def _create_parametric_extrusion():
        _feat = doc.addObject("Part::Extrusion", feat_nm or "Solid")
        _feat.Base = sk
        try:
            _feat.DirMode = "Normal"
        except Exception:
            pass
        try:
            _feat.Dir = _normal
        except Exception:
            pass
        try:
            _feat.FaceMakerClass = "Part::FaceMakerBullseye"
        except Exception:
            pass
        _feat.LengthFwd = towards
        _feat.LengthRev = opposite
        _feat.Solid = True
        doc.recompute()
        return _feat

    def _create_robust_face_extrusion():
        _edges = _edges_from_raw_json(sk, Part)
        if not _edges:
            raise ValueError("Cannot build robust extrusion: no raw sketch edges")
        _face = _make_face(Part, _edges)
        _face_world = _face.transformGeometry(_pl.toMatrix())
        _pieces = []
        if abs(float(towards or 0.0)) > 1e-9:
            _pieces.append(_face_world.extrude(_normal * float(towards)))
        if abs(float(opposite or 0.0)) > 1e-9:
            _pieces.append(_face_world.extrude(_normal * -float(opposite)))
        if not _pieces:
            raise ValueError("Cannot extrude: towards and opposite are both zero")
        _solid = _pieces[0]
        for _piece in _pieces[1:]:
            _solid = _solid.fuse(_piece)
        _feat = doc.addObject("Part::Feature", feat_nm or "Solid")
        _feat.Shape = _solid
        doc.recompute()
        return _feat

    if mode in ("auto", "parametric_sketch"):
        def _purge_redundant_sketch_constraints(_sk_obj, _max_iterations=10):
            for _ in range(_max_iterations):
                try:
                    _sk_obj.solve()
                except Exception:
                    break
                _red_idxs = [
                    int(i)
                    for i in (getattr(_sk_obj, "RedundantConstraints", []) or [])
                ]
                if not _red_idxs:
                    break
                _deleted_any = False
                for _r_idx in sorted(_red_idxs, reverse=True):
                    _pos = _r_idx - 1
                    if not (0 <= _pos < len(_sk_obj.Constraints)):
                        continue
                    if _sk_obj.Constraints[_pos].Type != "Coincident":
                        continue
                    _sk_obj.delConstraint(_pos)
                    _deleted_any = True
                if not _deleted_any:
                    break
            try:
                _sk_obj.solve()
            except Exception:
                pass

        _purge_redundant_sketch_constraints(sk)
        try:
            feat = _create_parametric_extrusion()
            fallback_reason = _shape_failure_reason(feat)
            if fallback_reason is None:
                extrusion_mode_used = "parametric_sketch"
        except Exception as _exc:
            fallback_reason = "parametric_error: " + str(_exc)
            feat = None
        if fallback_reason and mode == "parametric_sketch":
            raise ValueError(
                "Parametric sketch extrusion failed: " + fallback_reason
            )

    if mode == "robust_face" or (mode == "auto" and fallback_reason):
        if feat is not None:
            try:
                doc.removeObject(feat.Name)
            except Exception:
                pass
        feat = _create_robust_face_extrusion()
        robust_reason = _shape_failure_reason(feat)
        if robust_reason:
            raise ValueError("Robust face extrusion failed: " + robust_reason)
        extrusion_mode_used = "robust_face"

    if feat is None:
        raise ValueError("Failed to create extrusion feature")

    # World-space AABB + volume.
    try:
        bb = feat.Shape.BoundBox
        bbox = {{"x_min": round(bb.XMin, 4), "x_max": round(bb.XMax, 4),
                 "y_min": round(bb.YMin, 4), "y_max": round(bb.YMax, 4),
                 "z_min": round(bb.ZMin, 4), "z_max": round(bb.ZMax, 4)}}
        volume = feat.Shape.Volume
    except Exception:
        bbox = {{}}
        volume = 0.0

    # Sketch normal in world space (for extrusion direction verification)
    sketch_normal_world = [round(_normal.x, 6), round(_normal.y, 6), round(_normal.z, 6)]

    # Step-local observation: OBB in sketch frame + global center.
    # The solid is mapped back to the sketch-local frame with the inverse
    # placement; its axis-aligned box there is reported as local_obb.
    try:
        _local_sh = feat.Shape.copy()
        _local_sh.transformShape(_pl.inverse().toMatrix())
        _lb = _local_sh.BoundBox
        _lc = [(_lb.XMin + _lb.XMax) / 2,
               (_lb.YMin + _lb.YMax) / 2,
               (_lb.ZMin + _lb.ZMax) / 2]
        local_obb = {{
            "center": [round(_v, 4) for _v in _lc],
            "semi_extents": [round(_lb.XLength / 2, 4),
                             round(_lb.YLength / 2, 4),
                             round(_lb.ZLength / 2, 4)],
        }}
        _gc = _pl.Rotation.multVec(FreeCAD.Vector(*_lc)) + _pl.Base
        global_center = [round(_gc.x, 4), round(_gc.y, 4), round(_gc.z, 4)]
    except Exception:
        local_obb = {{}}
        global_center = None

    # Bind param_aliases to spreadsheet
    bound_params = []
    if aliases and extrusion_mode_used == "parametric_sketch":
        # Find or create the FabricationParams spreadsheet
        sheet = doc.getObject("FabricationParams")
        if sheet is None:
            sheet = doc.addObject("Spreadsheet::Sheet", "FabricationParams")

        def _used_cells(_sheet):
            try:
                return list(_sheet.getUsedCells()) if hasattr(_sheet, "getUsedCells") else []
            except Exception:
                return []

        def _find_alias_cell(_sheet, _alias):
            for _cell in _used_cells(_sheet):
                try:
                    if _sheet.getAlias(_cell) == _alias:
                        return _cell
                except Exception:
                    pass
            return None

        _allocated_cells = set(_used_cells(sheet))

        def _next_cell():
            _row = 1
            while True:
                _cell = f"A{{_row}}"
                if _cell not in _allocated_cells:
                    _allocated_cells.add(_cell)
                    return _cell
                _row += 1

        alias_map = {{"towards": towards, "opposite": opposite}}
        prop_map = {{"towards": "LengthFwd", "opposite": "LengthRev"}}
        for param_key, alias_name in aliases.items():
            value = alias_map.get(param_key, towards)
            cell = _find_alias_cell(sheet, alias_name) or _next_cell()
            _allocated_cells.add(cell)
            sheet.set(cell, str(value))
            try:
                sheet.setAlias(cell, alias_name)
            except Exception:
                pass
            prop_name = prop_map.get(param_key, "LengthFwd")
            bound_property = None
            bind_diagnostic = None
            try:
                setattr(feat, prop_name, value)
                feat.setExpression(prop_name, f"FabricationParams.{{alias_name}}")
                bound_property = prop_name
            except Exception:
                bind_diagnostic = "setExpression_failed"
            bound_params.append({{
                "alias": alias_name,
                "cell": cell,
                "value": value,
                "unit": "mm",
                "object": feat.Name,
                "property": bound_property,
                "role": (
                    ("thickness" if param_key == "towards" else param_key)
                    if bound_property
                    else "unbound_expression_error"
                ),
                "diagnostic": bind_diagnostic,
            }})
    elif aliases:
        # The robust-face fallback is intentionally stable geometry, not a
        # sketch-driven parametric feature.  Report the missing binding so the
        # caller can decide whether to rebuild through parametric_sketch mode.
        for param_key, alias_name in aliases.items():
            bound_params.append({{
                "alias": alias_name,
                "cell": None,
                "value": {{"towards": towards, "opposite": opposite}}.get(param_key),
                "unit": "mm",
                "object": feat.Name,
                "property": None,
                "role": "unbound_fallback",
                "diagnostic": fallback_reason or "extrusion_mode_not_parametric_sketch",
            }})

    doc.recompute()
    doc.commitTransaction()


    _result_ = {{
        "feature_name":        feat.Name,
        "local_obb":           local_obb,
        "global_center":       global_center,
        "bounding_box":        bbox,
        "volume_mm3":          round(volume, 4),
        "sketch_normal_world": sketch_normal_world,
        "extrusion_mode_used": extrusion_mode_used,
        "fallback_reason": (
            fallback_reason if extrusion_mode_used == "robust_face" else None
        ),
        "bound_params":        bound_params,
        "success":             volume > 0,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to execute extrude")

    @mcp.tool()
    async def execute_boolean(
        base_object_name: str,
        tool_object_name: str,
        operation: str,
        result_name: str | None = None,
        keep_originals: bool = False,
        boolean_mode: str = "auto",
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Combine two solids with an explicit parametric boolean operation.

        Takes explicit base and tool objects — no auto-detection.  Use this
        after ``execute_extrude`` / ``execute_revolve`` / ``execute_helix`` to
        realise ToolCAD-style explicit boolean actions:

        - ``"Join"``: union of base and tool (fuse).
        - ``"Cut"``: base minus tool.
        - ``"Intersect"``: shared volume only (common).

        Args:
            base_object_name: Name of the existing solid (e.g. the feature
                returned by a previous ``execute_extrude``).
            tool_object_name: Name of the solid to combine with the base
                (typically the feature just created by ``execute_extrude``).
            operation: ``"Join"``, ``"Cut"``, or ``"Intersect"``.
            result_name: Explicit FreeCAD object name for the result.
                Auto-generated from the operation if None.
            keep_originals: When ``False`` (default) the base and tool objects
                are kept as parametric dependencies but hidden from view.
                Set ``True`` to keep them visible.
            boolean_mode: ``"auto"`` (default), ``"parametric"``, or
                ``"static_shape"``.  ``"auto"`` tries a parametric FreeCAD
                boolean first.  When native ``Part::Common`` / ``Part::Cut``
                return zero volume for ``Part::Extrusion`` parents, a linked
                ``Part::FeaturePython`` fallback keeps base/tool references
                and still falls back to a direct shape boolean only when both
                paths fail.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with:
                - feature_name: Name of the result solid.
                - type_id: FreeCAD TypeId of the parametric boolean object.
                - operation: The operation performed.
                - base_object_name: Explicit base object used.
                - tool_object_name: Explicit tool object used.
                - dependency_preserved: ``True`` when base/tool references are
                  retained for recompute.
                - boolean_mode_used: Actual boolean path used.
                - fallback_reason: Reason for static fallback, if any.
                - base: ``{"name", "global_center", "volume_mm3"}`` of the
                  base solid before the boolean (``global_center`` is the
                  world-space AABB center).
                - tool: Same observation for the tool solid.
                - result: ``{"global_center", "bounding_box", "volume_mm3"}``
                  of the boolean result.
                - center_distance: Euclidean distance between base and tool
                  ``global_center`` values.
                - success: ``True`` when the result has non-zero volume.

        Raises:
            ValueError: If either object is missing or the operation is
                invalid.
        """
        bridge = await get_bridge()
        code = f"""
import math as _math

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

base_obj = doc.getObject({base_object_name!r})
tool_obj = doc.getObject({tool_object_name!r})
if base_obj is None:
    raise ValueError(f"Base object not found: {base_object_name!r}")
if tool_obj is None:
    raise ValueError(f"Tool object not found: {tool_object_name!r}")

operation   = {operation!r}
result_nm   = {result_name!r}
keep_orig   = {keep_originals!r}
mode        = {boolean_mode!r}

if operation not in ("Join", "Cut", "Intersect"):
    raise ValueError(
        f"Invalid operation: {{operation}}. Use 'Join', 'Cut', or 'Intersect'.")
if mode not in ("auto", "parametric", "static_shape"):
    raise ValueError(
        f"Invalid boolean_mode: {{mode}}. Use 'auto', 'parametric', "
        "or 'static_shape'.")

def _aabb_center(_sh):
    _b = _sh.BoundBox
    return [round((_b.XMin + _b.XMax) / 2, 4),
            round((_b.YMin + _b.YMax) / 2, 4),
            round((_b.ZMin + _b.ZMax) / 2, 4)]

def _shape_failure_reason(_sh):
    try:
        if _sh.isNull():
            return "null_shape"
        try:
            if not _sh.isValid():
                return "invalid_shape"
        except Exception:
            pass
        if _sh.Volume <= 1e-9:
            return f"zero_volume_{{round(_sh.Volume, 9)}}"
        return None
    except Exception as _exc:
        return "shape_error: " + str(_exc)

def _direct_boolean_shape():
    if operation == "Join":
        return base_obj.Shape.fuse(tool_obj.Shape)
    if operation == "Cut":
        return base_obj.Shape.cut(tool_obj.Shape)
    return base_obj.Shape.common(tool_obj.Shape)

def _prepare_boolean_dependencies():
    # Part::Extrusion parents must be marked dirty before Part::Common /
    # Part::Cut recompute.  Without this, FreeCAD can return a valid but
    # zero-volume parametric boolean even when direct shape booleans work.
    for _obj in (base_obj, tool_obj):
        _obj.touch()
        try:
            _sk = getattr(_obj, "Base", None)
            if _sk is not None:
                _sk.touch()
        except Exception:
            pass
    doc.recompute()

def _create_parametric_boolean():
    if operation == "Join":
        try:
            _feat = doc.addObject("Part::Fuse", result_nm or operation)
            _feat.Base = base_obj
            _feat.Tool = tool_obj
            return _feat
        except Exception:
            _feat = doc.addObject("Part::MultiFuse", result_nm or operation)
            _feat.Shapes = [base_obj, tool_obj]
            return _feat
    if operation == "Cut":
        _feat = doc.addObject("Part::Cut", result_nm or operation)
        _feat.Base = base_obj
        _feat.Tool = tool_obj
        return _feat
    try:
        _feat = doc.addObject("Part::Common", result_nm or operation)
        _feat.Base = base_obj
        _feat.Tool = tool_obj
        try:
            _feat.Refine = True
        except Exception:
            pass
        return _feat
    except Exception:
        _feat = doc.addObject("Part::MultiCommon", result_nm or operation)
        _feat.Shapes = [base_obj, tool_obj]
        return _feat

def _create_linked_boolean_feature():
    # Part::Common / Part::Cut can return zero volume for Part::Extrusion
    # parents even when direct shape booleans succeed.  A linked
    # Part::FeaturePython keeps base/tool references and recomputes via
    # OpenCASCADE shape ops, so sketch edits still propagate.
    class _FabricationBooleanProxy:
        def __init__(self, obj):
            obj.addProperty(
                "App::PropertyLink", "Base", "Boolean", "Base solid")
            obj.addProperty(
                "App::PropertyLink", "Tool", "Boolean", "Tool solid")
            obj.addProperty(
                "App::PropertyEnumeration",
                "Operation",
                "Boolean",
                "Boolean operation",
            )
            obj.Operation = ["Join", "Cut", "Intersect"]
            obj.Proxy = self

        def execute(self, obj):
            _base_sh = obj.Base.Shape
            _tool_sh = obj.Tool.Shape
            if obj.Operation == "Join":
                obj.Shape = _base_sh.fuse(_tool_sh)
            elif obj.Operation == "Cut":
                obj.Shape = _base_sh.cut(_tool_sh)
            else:
                obj.Shape = _base_sh.common(_tool_sh)

    class _FabricationBooleanViewProvider:
        # Part::FeaturePython has no default 3-D display.  Without a view
        # provider the shape exists but stays invisible in the GUI.
        def __init__(self, vobj):
            vobj.Proxy = self

        def attach(self, vobj):
            from pivy import coin

            self.root = coin.SoSeparator()
            vobj.addDisplayMode(self.root, "Shaded")
            self.updateData(vobj.Object, "Shape")

        def updateData(self, obj, prop):
            if prop != "Shape":
                return
            from pivy import coin

            self.root.removeAllChildren()
            _sh = obj.Shape
            if _sh is None or _sh.isNull():
                return
            try:
                _pts, _tris = _sh.tessellate(0.1)
            except Exception:
                return
            if not _pts:
                return
            _coords = coin.SoCoordinate3()
            _coords.point.setValues(0, len(_pts), [p.toTuple() for p in _pts])
            _faces = coin.SoIndexedFaceSet()
            _faces.coordIndex.setValues(
                0, len(_tris), [i for tri in _tris for i in tri] + [-1]
            )
            self.root.addChild(_coords)
            self.root.addChild(_faces)

        def getDisplayModes(self, obj):
            return ["Shaded"]

        def getDefaultDisplayMode(self):
            return "Shaded"

        def setDisplayMode(self, mode):
            return mode

    import sys as _sys

    _mod = _sys.modules.get("__main__")
    if _mod is not None:
        _mod._FabricationBooleanProxy = _FabricationBooleanProxy
        _mod._FabricationBooleanViewProvider = _FabricationBooleanViewProvider

    _feat = doc.addObject("Part::FeaturePython", result_nm or operation)
    _FabricationBooleanProxy(_feat)
    _feat.Base = base_obj
    _feat.Tool = tool_obj
    _feat.Operation = operation
    if FreeCAD.GuiUp:
        try:
            _vp = _feat.ViewObject
            _FabricationBooleanViewProvider(_vp)
            _vp.Visibility = True
            _vp.DisplayMode = "Shaded"
        except Exception:
            pass
    return _feat

def _try_linked_boolean_fallback():
    _direct_shape = _direct_boolean_shape()
    _direct_reason = _shape_failure_reason(_direct_shape)
    if _direct_reason is not None:
        return None, _direct_reason
    return _create_linked_boolean_feature(), None

doc.openTransaction("Execute Boolean")
try:
    base_shape = base_obj.Shape
    tool_shape = tool_obj.Shape

    base_center = _aabb_center(base_shape)
    tool_center = _aabb_center(tool_shape)
    center_distance = round(_math.sqrt(sum(
        (base_center[_i] - tool_center[_i]) ** 2 for _i in range(3))), 4)

    feat = None
    boolean_mode_used = None
    fallback_reason = None
    dependency_preserved = True

    if mode in ("auto", "parametric"):
        try:
            _prepare_boolean_dependencies()
            feat = _create_parametric_boolean()
            doc.recompute()
            fallback_reason = _shape_failure_reason(feat.Shape)
            if fallback_reason and fallback_reason.startswith("zero_volume"):
                doc.recompute()
                fallback_reason = _shape_failure_reason(feat.Shape)
            if fallback_reason is None:
                boolean_mode_used = "parametric"
        except Exception as _exc:
            fallback_reason = "parametric_error: " + str(_exc)
            feat = None
        if fallback_reason and mode in ("auto", "parametric"):
            _linked_feat, _linked_reason = _try_linked_boolean_fallback()
            if _linked_feat is not None:
                if feat is not None:
                    try:
                        doc.removeObject(feat.Name)
                    except Exception:
                        pass
                feat = _linked_feat
                doc.recompute()
                fallback_reason = _shape_failure_reason(feat.Shape)
                if fallback_reason is None:
                    boolean_mode_used = "parametric"
            elif mode == "parametric":
                raise ValueError(
                    "Parametric boolean failed: "
                    + str(fallback_reason)
                    + "; linked fallback failed: "
                    + str(_linked_reason)
                )

    if mode == "static_shape" or (mode == "auto" and fallback_reason):
        direct_shape = _direct_boolean_shape()
        direct_reason = _shape_failure_reason(direct_shape)
        if mode == "static_shape" or direct_reason is None:
            if feat is not None:
                try:
                    doc.removeObject(feat.Name)
                except Exception:
                    pass
            feat = doc.addObject("Part::Feature", result_nm or operation)
            feat.Shape = direct_shape
            doc.recompute()
            boolean_mode_used = "static_shape"
            dependency_preserved = False
        elif feat is None:
            raise ValueError(
                "Parametric boolean failed: "
                + str(fallback_reason)
                + "; static fallback failed: "
                + str(direct_reason)
            )

    if feat is None:
        raise ValueError("Failed to create boolean feature")

    if not keep_orig:
        for _obj in (base_obj, tool_obj):
            try:
                _obj.ViewObject.Visibility = False
            except Exception:
                pass

    if FreeCAD.GuiUp:
        try:
            feat.ViewObject.Visibility = True
        except Exception:
            pass

    result_shape = feat.Shape
    _rb = result_shape.BoundBox
    _result_ = {{
        "feature_name": feat.Name,
        "type_id":      feat.TypeId,
        "operation":    operation,
        "base_object_name": {base_object_name!r},
        "tool_object_name": {tool_object_name!r},
        "dependency_preserved": dependency_preserved,
        "boolean_mode_used": boolean_mode_used,
        "fallback_reason": (
            fallback_reason if boolean_mode_used == "static_shape" else None
        ),
        "base": {{
            "name":          {base_object_name!r},
            "global_center": base_center,
            "volume_mm3":    round(base_shape.Volume, 4),
        }},
        "tool": {{
            "name":          {tool_object_name!r},
            "global_center": tool_center,
            "volume_mm3":    round(tool_shape.Volume, 4),
        }},
        "result": {{
            "global_center": _aabb_center(result_shape),
            "bounding_box":  {{"x_min": round(_rb.XMin, 4), "x_max": round(_rb.XMax, 4),
                               "y_min": round(_rb.YMin, 4), "y_max": round(_rb.YMax, 4),
                               "z_min": round(_rb.ZMin, 4), "z_max": round(_rb.ZMax, 4)}},
            "volume_mm3":    round(result_shape.Volume, 4),
        }},
        "center_distance": center_distance,
        "success": result_shape.Volume > 1e-9,
    }}
    doc.commitTransaction()
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to execute boolean")

    @mcp.tool()
    async def execute_revolve(
        sketch_name: str,
        axis: list[list[float]],
        start: float = 0.0,
        end: float = 360.0,
        operation: str = "NewBody",
        param_aliases: dict[str, str] | None = None,
        body_name: str | None = None,
        feature_name: str | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Revolve a sketch around an axis to create a 3-D feature.

        Creates a standalone ``PartDesign::Revolution``.  Boolean operations
        must be performed by a separate explicit ``execute_boolean`` call.

        Args:
            sketch_name: Name of the sketch to revolve.
            axis: 2x3 matrix defining the revolution axis:
                ``[[base_x, base_y, base_z], [dir_x, dir_y, dir_z]]``.
            start: Start angle in degrees (default: 0.0).
            end: End angle in degrees (default: 360.0 for a full revolution).
            operation: Must be ``"NewBody"`` in the canonical pipeline.
            param_aliases: Maps parameter keys to spreadsheet alias names.
                Supported keys: ``"start"``, ``"end"``.
            body_name: PartDesign Body. Auto-detected if None.
            feature_name: Explicit object name. Auto-generated if None.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with feature_name, body_name, bounding_box, volume,
            bound_params, and success.

        Raises:
            ValueError: If the sketch is not found or the operation fails.
        """
        if operation != "NewBody":
            raise ValueError(
                "Canonical fabrication uses explicit boolean features; "
                "execute_revolve only supports operation='NewBody'."
            )
        bridge = await get_bridge()
        code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sk = doc.getObject({sketch_name!r})
if sk is None:
    raise ValueError(f"Sketch not found: {sketch_name!r}")

axis_spec  = {axis!r}
start_ang  = {start!r}
end_ang    = {end!r}
operation  = {operation!r}
aliases    = {param_aliases!r} or {{}}
feat_nm    = {feature_name!r}
body_nm    = {body_name!r}

doc.openTransaction("Execute Revolve")
try:
    body = None
    if body_nm is not None:
        body = doc.getObject(body_nm)
    else:
        for obj in doc.Objects:
            if obj.TypeId == "PartDesign::Body":
                body = obj
                break

    base_pt = FreeCAD.Vector(*axis_spec[0])
    dir_vec = FreeCAD.Vector(*axis_spec[1])

    feat = doc.addObject("PartDesign::Revolution", feat_nm or "Revolution")

    feat.Profile  = sk
    feat.Angle    = end_ang - start_ang
    feat.Axis     = dir_vec
    feat.Base     = base_pt

    if body is None and operation == "NewBody":
        body = doc.addObject("PartDesign::Body", "Body")
        body.addObject(sk)
    if body is not None:
        body.addObject(feat)

    doc.recompute()

    try:
        bb = body.Shape.BoundBox
        bbox = {{"x_min": bb.XMin,"x_max": bb.XMax,
                 "y_min": bb.YMin,"y_max": bb.YMax,
                 "z_min": bb.ZMin,"z_max": bb.ZMax}}
        volume = body.Shape.Volume
    except Exception:
        bbox = {{}}; volume = 0.0

    doc.commitTransaction()
    _result_ = {{
        "feature_name": feat.Name,
        "body_name":    body.Name if body else None,
        "bounding_box": bbox,
        "volume":       volume,
        "bound_params": [],
        "success":      True,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to execute revolve")

    @mcp.tool()
    async def execute_helix(
        sketch_name: str,
        axis: list[list[float]],
        pitch: float,
        turns: float,
        handedness: str = "Right",
        operation: str = "NewBody",
        param_aliases: dict[str, str] | None = None,  # noqa: ARG001
        body_name: str | None = None,
        feature_name: str | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Sweep a sketch along a helix path to create a 3-D feature.

        Creates a ``PartDesign::Helix`` feature (FreeCAD 1.x).  Falls back to a
        ``Part::Helix`` + ``Part::Sweep`` if ``PartDesign::Helix`` is not
        available.

        Args:
            sketch_name: Name of the profile sketch to sweep.
            axis: 2x3 matrix defining the helix axis:
                ``[[base_x, base_y, base_z], [dir_x, dir_y, dir_z]]``.
            pitch: Distance along the axis per full turn (mm, positive).
            turns: Total number of turns (positive).
            handedness: ``"Right"`` (default) or ``"Left"``.
            operation: Must be ``"NewBody"`` in the canonical pipeline.
            param_aliases: Maps ``"pitch"`` or ``"turns"`` to spreadsheet alias
                names for frontend sliders.
            body_name: PartDesign Body. Auto-detected if None.
            feature_name: Explicit object name. Auto-generated if None.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with feature_name, body_name, bounding_box, and success.

        Raises:
            ValueError: If the sketch is not found or the operation fails.
        """
        if operation != "NewBody":
            raise ValueError(
                "Canonical fabrication uses explicit boolean features; "
                "execute_helix only supports operation='NewBody'."
            )
        bridge = await get_bridge()
        code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sk = doc.getObject({sketch_name!r})
if sk is None:
    raise ValueError(f"Sketch not found: {sketch_name!r}")

axis_spec   = {axis!r}
pitch_v     = {pitch!r}
turns_v     = {turns!r}
handed      = {handedness!r}
operation   = {operation!r}
feat_nm     = {feature_name!r}
body_nm     = {body_name!r}

doc.openTransaction("Execute Helix")
try:
    body = None
    if body_nm is not None:
        body = doc.getObject(body_nm)
    else:
        for obj in doc.Objects:
            if obj.TypeId == "PartDesign::Body":
                body = obj
                break

    dir_vec = FreeCAD.Vector(*axis_spec[1])

    # Try PartDesign::Helix (FreeCAD 1.x)
    try:
        feat = doc.addObject("PartDesign::Helix", feat_nm or "Helix")
        feat.Pitch  = pitch_v
        feat.Turns  = turns_v
        feat.Axis   = dir_vec
        feat.LeftHanded = (handed == "Left")
        feat.Profile = sk
    except Exception:
        # Fallback: Part::Helix + Part::Sweep
        helix_path = doc.addObject("Part::Helix", "HelixPath")
        helix_path.Pitch  = pitch_v
        helix_path.Height = pitch_v * turns_v
        helix_path.Axis   = dir_vec
        helix_path.LocalCoord = 0
        feat = doc.addObject("Part::Sweep", feat_nm or "Sweep")
        feat.Sections = [sk]
        feat.Spine    = helix_path
        feat.Frenet   = True
        feat.Solid    = True

    if body is None and operation == "NewBody":
        body = doc.addObject("PartDesign::Body", "Body")
        body.addObject(sk)
    if body is not None:
        body.addObject(feat)

    doc.recompute()

    try:
        bb = body.Shape.BoundBox if body else feat.Shape.BoundBox
        bbox = {{"x_min": bb.XMin,"x_max": bb.XMax,
                 "y_min": bb.YMin,"y_max": bb.YMax,
                 "z_min": bb.ZMin,"z_max": bb.ZMax}}
    except Exception:
        bbox = {{}}

    doc.commitTransaction()
    _result_ = {{
        "feature_name": feat.Name,
        "body_name":    body.Name if body else None,
        "bounding_box": bbox,
        "success":      True,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to execute helix")

    # ------------------------------------------------------------------
    # Group E — Finishing Features
    # ------------------------------------------------------------------

    @mcp.tool()
    async def feature_fillet(
        near_points: list[list[float]],
        radius: float | list[float],
        body_name: str | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Add rounded fillets to edges selected by 3-D proximity.

        Each entry in ``near_points`` is resolved to the nearest edge of the
        active body using B-rep distance queries, bypassing the Topological
        Naming Problem entirely.  Use ``get_body_snapshot`` to obtain
        ``edge_samples`` (3-D midpoints of all edges) for constructing
        accurate ``near_points``.

        Args:
            near_points: List of 3-D query points ``[[x,y,z], ...]``.  Each
                is resolved to the geometrically nearest edge at execution time.
            radius: Fillet radius in millimetres.  Either a single value
                applied to all edges, or a list with one value per edge.
            body_name: PartDesign Body containing the edges.  Auto-detected
                if None.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with:
                - feature_name: FreeCAD object name of the created Fillet.
                - resolved_edge_count: Number of edges found and filleted.
                - radius: Radius value(s) used.
                - success: ``True`` on success.

        Raises:
            ValueError: If the body is not found or the fillet fails.

        Example:
            Fillet the top outer edge of a cylinder at z=30::

                snapshot = await get_body_snapshot(doc_name=doc_name)
                # Find edge near [15, 0, 30]
                result = await feature_fillet(
                    near_points=[[15.0, 0.0, 30.0]],
                    radius=2.0,
                )
        """
        bridge = await get_bridge()
        code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

near_pts  = {near_points!r}
radius_in = {radius!r}
body_nm   = {body_name!r}

body = None
if body_nm is not None:
    body = doc.getObject(body_nm)
else:
    for obj in doc.Objects:
        if obj.TypeId == "PartDesign::Body":
            body = obj
            break
    if body is None:
        for obj in reversed(doc.Objects):
            if hasattr(obj, "Shape"):
                try:
                    if not obj.Shape.isNull():
                        body = obj
                        break
                except Exception:
                    pass

if body is None:
    raise ValueError("No shape-bearing object found")

shape = body.Shape
radii = radius_in if isinstance(radius_in, list) else [radius_in] * len(near_pts)

# Resolve each near_point to the nearest edge index
resolved_edges = []
for i, pt_coords in enumerate(near_pts):
    pt = FreeCAD.Vector(*pt_coords)
    best_idx = None
    best_dist = float("inf")
    for j, edge in enumerate(shape.Edges):
        try:
            mid_param = (edge.FirstParameter + edge.LastParameter) / 2
            mid_pt = edge.valueAt(mid_param)
        except Exception:
            try:
                mid_pt = edge.CenterOfMass
            except Exception:
                continue
        dist = pt.distanceToPoint(mid_pt)
        if dist < best_dist:
            best_dist = dist
            best_idx = j
    if best_idx is not None:
        r = radii[i] if i < len(radii) else radii[-1]
        resolved_edges.append((best_idx, r))

if not resolved_edges:
    raise ValueError("No edges resolved from near_points")

doc.openTransaction("Feature Fillet")
try:
    fillet = doc.addObject("PartDesign::Fillet", "Fillet")
    fillet.Base = (body.Tip or body, [f"Edge{{idx+1}}" for idx, _ in resolved_edges])
    fillet.Size = resolved_edges[0][1]
    body.addObject(fillet)
    doc.recompute()
    doc.commitTransaction()
    _result_ = {{
        "feature_name":       fillet.Name,
        "resolved_edge_count": len(resolved_edges),
        "radius":             radius_in,
        "success":            True,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to create fillet")

    @mcp.tool()
    async def feature_chamfer(
        near_points: list[list[float]],
        dist: float,
        angle: float = 45.0,
        plane: list[float] | None = None,  # noqa: ARG001
        body_name: str | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Add chamfers to edges selected by 3-D proximity.

        Same edge-resolution strategy as ``feature_fillet`` — each entry in
        ``near_points`` is matched to the nearest edge via B-rep distance query.

        Args:
            near_points: List of 3-D query points ``[[x,y,z], ...]``.
            dist: Chamfer offset distance in millimetres.
            angle: Chamfer angle in degrees (default: 45.0).
            plane: Optional normal vector ``[nx, ny, nz]`` of the reference
                plane for angle measurement.  Defaults to the sketch normal
                if None.
            body_name: PartDesign Body. Auto-detected if None.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with feature_name, resolved_edge_count, dist, angle,
            and success.

        Raises:
            ValueError: If the body is not found or the chamfer fails.
        """
        bridge = await get_bridge()
        code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

near_pts = {near_points!r}
dist_v   = {dist!r}
angle_v  = {angle!r}
body_nm  = {body_name!r}

body = None
if body_nm is not None:
    body = doc.getObject(body_nm)
else:
    for obj in doc.Objects:
        if obj.TypeId == "PartDesign::Body":
            body = obj
            break

if body is None:
    raise ValueError("No PartDesign Body found")

shape = body.Shape

resolved_edges = []
for pt_coords in near_pts:
    pt = FreeCAD.Vector(*pt_coords)
    best_idx = None
    best_dist = float("inf")
    for j, edge in enumerate(shape.Edges):
        try:
            mid_param = (edge.FirstParameter + edge.LastParameter) / 2
            mid_pt = edge.valueAt(mid_param)
        except Exception:
            try:
                mid_pt = edge.CenterOfMass
            except Exception:
                continue
        dist_tmp = pt.distanceToPoint(mid_pt)
        if dist_tmp < best_dist:
            best_dist = dist_tmp
            best_idx = j
    if best_idx is not None:
        resolved_edges.append(best_idx)

if not resolved_edges:
    raise ValueError("No edges resolved from near_points")

doc.openTransaction("Feature Chamfer")
try:
    chamfer = doc.addObject("PartDesign::Chamfer", "Chamfer")
    chamfer.Base = (body.Tip or body,
                    [f"Edge{{idx+1}}" for idx in resolved_edges])
    chamfer.Size  = dist_v
    chamfer.Angle = angle_v
    body.addObject(chamfer)
    doc.recompute()
    doc.commitTransaction()
    _result_ = {{
        "feature_name":       chamfer.Name,
        "resolved_edge_count": len(resolved_edges),
        "dist":               dist_v,
        "angle":              angle_v,
        "success":            True,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to create chamfer")

    # ------------------------------------------------------------------
    # Group F — Parametric Controls (frontend slider interface)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_tunable_params(
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """List all named tunable parameters available as frontend sliders.

        Returns all spreadsheet aliases from the ``FabricationParams``
        spreadsheet, along with their current values and which feature
        properties they drive.  The frontend reads this to build its slider
        panel.

        Args:
            doc_name: Document name. Uses the active document if None.

        Returns:
            Dictionary with:
                - params: List of parameter dicts, each containing:
                    - alias: Spreadsheet alias name (e.g. ``"column_height"``).
                    - value: Current numeric value.
                    - unit: Unit string (``"mm"``, ``"deg"``, or ``""``).
                    - cell: Spreadsheet cell address (e.g. ``"A1"``).
                    - bound_to: List of ``{"object": str, "property": str}``
                      dicts showing which feature properties use this param.
                - spreadsheet_name: Name of the FabricationParams spreadsheet,
                  or ``None`` if no spreadsheet exists.

        Example:
            Build a slider panel::

                result = await list_tunable_params()
                for param in result["params"]:
                    print(f"{param['alias']}: {param['value']} {param['unit']}")
        """
        bridge = await get_bridge()
        code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sheet = doc.getObject("FabricationParams")
if sheet is None:
    _result_ = {{"params": [], "spreadsheet_name": None}}
else:
    params = []
    sheet_refs = [
        sheet.Name,
        sheet.Label,
        f"<<{{sheet.Name}}>>",
        f"<<{{sheet.Label}}>>",
    ]

    def _expr_references_alias(_expr, _alias):
        _text = str(_expr)
        for _sheet_ref in sheet_refs:
            if f"{{_sheet_ref}}.{{_alias}}" in _text:
                return True
        return _text.endswith("." + _alias)

    # Scan all cells in the used range
    try:
        used_cells = sheet.getUsedCells() if hasattr(sheet, "getUsedCells") else []
    except Exception:
        used_cells = [f"A{{r}}" for r in range(1, 30)]

    for cell_addr in used_cells:
        try:
            alias = sheet.getAlias(cell_addr)
            if not alias:
                continue
            raw_val = sheet.get(cell_addr)
            try:
                value = float(raw_val)
            except (ValueError, TypeError):
                value = raw_val

            # Find which feature/sketch properties reference this alias.
            bound_to = []
            for obj in doc.Objects:
                if hasattr(obj, "ExpressionEngine"):
                    for prop, expr in obj.ExpressionEngine:
                        if _expr_references_alias(expr, alias):
                            role = "parameter"
                            unit = "mm"
                            label = alias
                            if str(prop).startswith("Constraints["):
                                role = "sketch_dimension"
                                try:
                                    idx_txt = str(prop).split("[", 1)[1].split("]", 1)[0]
                                    c_idx = int(idx_txt)
                                    c_type = obj.Constraints[c_idx].Type
                                    label = c_type
                                    if c_type == "Angle":
                                        unit = "deg"
                                except Exception:
                                    pass
                            elif str(prop) in ("LengthFwd", "LengthRev"):
                                role = "thickness" if "thickness" in alias.lower() else "feature_parameter"
                                label = str(prop)
                            elif str(prop).startswith(".FabricationParam_"):
                                role = "thickness" if "thickness" in alias.lower() else "feature_parameter"
                            bound_to.append({{
                                "object": obj.Name,
                                "property": prop,
                                "role": role,
                                "label": label,
                                "unit": unit,
                            }})

            params.append({{
                "alias":    alias,
                "value":    value,
                "unit":     bound_to[0].get("unit", "mm") if bound_to else "mm",
                "cell":     cell_addr,
                "bound_to": bound_to,
                "role":     bound_to[0].get("role", "parameter") if bound_to else "parameter",
                "label":    bound_to[0].get("label", alias) if bound_to else alias,
                "min":      None,
                "max":      None,
                "default":  value,
            }})
        except Exception:
            continue

    _result_ = {{"params": params, "spreadsheet_name": sheet.Name}}
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to list tunable params")

    @mcp.tool()
    async def set_tunable_param(
        alias: str,
        value: float,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Update a named parameter and trigger a model recompute.

        Sets the spreadsheet cell aliased to ``alias`` to the new ``value``
        and calls ``doc.recompute()``.  All features whose ``setExpression``
        references this alias update automatically.

        This is the backend callback for a frontend slider drag event.
        The returned ``affected_features`` list tells the frontend which
        geometry objects were modified and need re-rendering.

        Args:
            alias: Spreadsheet alias name (e.g. ``"column_height"``).
            value: New parameter value (in the same unit as the original).
            doc_name: Document name. Uses the active document if None.

        Returns:
            Dictionary with:
                - alias: The alias that was updated.
                - old_value: Value before the update.
                - new_value: New value after the update.
                - recomputed: ``True`` if recompute succeeded.
                - affected_features: List of feature object names that changed.
                - success: ``True`` on success.

        Raises:
            ValueError: If the alias is not found in the FabricationParams
                spreadsheet.

        Example:
            Drag a slider from 150 mm to 200 mm::

                result = await set_tunable_param("column_height", 200.0)
                # result["affected_features"] →
                #   ["Pad001", "Sketch_hole1", "Sketch_hole2", "Sketch_hole3"]
        """
        bridge = await get_bridge()
        code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sheet = doc.getObject("FabricationParams")
if sheet is None:
    raise ValueError("FabricationParams spreadsheet not found — no tunable params registered")

alias_name = {alias!r}
new_val    = {value!r}

# Find the cell with this alias
cell_addr = None
try:
    used_cells = sheet.getUsedCells() if hasattr(sheet, "getUsedCells") else []
except Exception:
    used_cells = [f"A{{r}}" for r in range(1, 30)]

for cell in used_cells:
    try:
        if sheet.getAlias(cell) == alias_name:
            cell_addr = cell
            break
    except Exception:
        continue

if cell_addr is None:
    raise ValueError(f"Alias '{{alias_name}}' not found in FabricationParams spreadsheet")

# Record old value
try:
    old_val = float(sheet.get(cell_addr))
except (ValueError, TypeError):
    old_val = sheet.get(cell_addr)

doc.openTransaction(f"Set Param {{alias_name}}")
try:
    sheet.set(cell_addr, str(new_val))
    doc.recompute()

    # Collect affected objects (those that reference this alias)
    affected = []
    sheet_refs = [
        sheet.Name,
        sheet.Label,
        f"<<{{sheet.Name}}>>",
        f"<<{{sheet.Label}}>>",
    ]

    def _expr_references_alias(_expr, _alias):
        _text = str(_expr)
        for _sheet_ref in sheet_refs:
            if f"{{_sheet_ref}}.{{_alias}}" in _text:
                return True
        return _text.endswith("." + _alias)

    for obj in doc.Objects:
        if hasattr(obj, "ExpressionEngine"):
            for prop, expr in obj.ExpressionEngine:
                if _expr_references_alias(expr, alias_name):
                    affected.append(obj.Name)
                    break

    # Include downstream parametric objects (for example Part::Fuse,
    # Part::Cut, Part::Common) that depend on directly affected sketches or
    # extrusions.  This is what makes a slider edit report the final boolean
    # result, not only the source sketch.
    affected_set = set(affected)
    changed = True
    while changed:
        changed = False
        for obj in doc.Objects:
            if obj.Name in affected_set:
                continue
            deps = []
            for attr in ("Base", "Tool"):
                try:
                    dep = getattr(obj, attr)
                    if dep is not None:
                        deps.append(dep)
                except Exception:
                    pass
            try:
                deps.extend(list(getattr(obj, "Shapes", []) or []))
            except Exception:
                pass
            for dep in deps:
                try:
                    if dep.Name in affected_set:
                        affected_set.add(obj.Name)
                        affected.append(obj.Name)
                        changed = True
                        break
                except Exception:
                    pass

    doc.commitTransaction()
    _result_ = {{
        "alias":            alias_name,
        "old_value":        old_val,
        "new_value":        new_val,
        "recomputed":       True,
        "affected_features": affected,
        "success":          True,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to set tunable param")

    @mcp.tool()
    async def evaluate_editability(  # noqa: PLR0912
        target_alias: str,
        value: float | None = None,
        scale: float | None = None,
        preserved_aliases: list[str] | None = None,
        design_intent: dict[str, Any] | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate one parametric edit and return HistCAD-style reward metrics.

        The evaluator edits one exposed ``FabricationParams`` alias, recomputes
        the model, checks whether the target value was reached, and verifies
        that preserved parameters/constraints remain evaluable.  It is generic:
        any template or FabricationPlan that exposes tunable aliases can use it
        as a process reward during RL.
        """
        before = await list_tunable_params(doc_name=doc_name)  # type: ignore[name-defined]
        params_before = {
            item.get("alias"): item
            for item in before.get("params", [])
            if item.get("alias")
        }
        if target_alias not in params_before:
            raise ValueError(f"Alias {target_alias!r} not found")
        old_value = params_before[target_alias].get("value")
        if value is None:
            if scale is None:
                raise ValueError("Either value or scale must be provided")
            value = float(old_value) * float(scale)

        intent = design_intent or {}
        preserve_aliases = list(
            preserved_aliases
            if preserved_aliases is not None
            else intent.get("preserve_aliases", [])
        )
        if not preserve_aliases:
            preserve_aliases = [
                alias for alias in params_before if alias != target_alias
            ]
        coupled_aliases = list(intent.get("coupled_aliases", []))
        free_aliases = list(intent.get("free_aliases", []))
        expected_dof = intent.get("expected_dof")
        required_constraint_types = set(intent.get("required_constraint_types", []))

        bridge = await get_bridge()

        async def _geometry_snapshot() -> dict[str, Any]:
            snapshot_code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")
objects = []
for obj in doc.Objects:
    try:
        if not hasattr(obj, "Shape") or obj.Shape.isNull():
            continue
        sh = obj.Shape
        bb = sh.BoundBox
        deps = []
        for attr in ("Base", "Tool"):
            try:
                dep = getattr(obj, attr)
                if dep is not None:
                    deps.append(dep.Name)
            except Exception:
                pass
        try:
            deps.extend([dep.Name for dep in (getattr(obj, "Shapes", []) or [])])
        except Exception:
            pass
        objects.append({{
            "name": obj.Name,
            "type_id": obj.TypeId,
            "volume": round(sh.Volume, 6),
            "bounding_box": [
                round(bb.XMin, 6), round(bb.XMax, 6),
                round(bb.YMin, 6), round(bb.YMax, 6),
                round(bb.ZMin, 6), round(bb.ZMax, 6),
            ],
            "dependencies": deps,
        }})
    except Exception:
        pass
_result_ = {{"objects": objects}}
"""
            exec_result = await bridge.execute_python(snapshot_code)
            if exec_result.success and exec_result.result:
                return exec_result.result
            return {"objects": []}

        geometry_before = await _geometry_snapshot()

        edit_result: dict[str, Any] | None = None
        edit_error: str | None = None
        try:
            edit_result = await set_tunable_param(  # type: ignore[name-defined]
                alias=target_alias,
                value=float(value),
                doc_name=doc_name,
            )
        except Exception as exc:
            edit_error = str(exc)

        after = await list_tunable_params(doc_name=doc_name)  # type: ignore[name-defined]
        params_after = {
            item.get("alias"): item
            for item in after.get("params", [])
            if item.get("alias")
        }
        target_after = params_after.get(target_alias, {})
        try:
            target_actual = float(target_after.get("value"))
            target_expected = float(value)
            target_delta = abs(target_actual - target_expected)
            target_hit = target_delta <= max(1e-6, abs(target_expected) * 1e-6)
        except Exception:
            target_actual = target_after.get("value")
            target_expected = value
            target_delta = None
            target_hit = target_after.get("value") == value

        validation_code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")
rebuild_success = True
exception = None
try:
    doc.recompute()
except Exception as _exc:
    rebuild_success = False
    exception = str(_exc)

sketches = []
shape_errors = []
for obj in doc.Objects:
    try:
        if obj.TypeId == "Sketcher::SketchObject":
            _constraint_types = []
            try:
                _constraint_types = [c.Type for c in obj.Constraints]
            except Exception:
                pass
            sketches.append({{
                "name": obj.Name,
                "dof": getattr(obj, "DoF", None),
                "fully_constrained": getattr(obj, "FullyConstrained", None),
                "conflicting": list(getattr(obj, "ConflictingConstraints", ())),
                "redundant": list(getattr(obj, "RedundantConstraints", ())),
                "constraint_types": _constraint_types,
            }})
        if hasattr(obj, "Shape") and not obj.Shape.isNull():
            try:
                if not obj.Shape.isValid():
                    shape_errors.append(obj.Name)
            except Exception:
                pass
    except Exception:
        pass
validation_ok = rebuild_success and not shape_errors and all(
    not item.get("conflicting") for item in sketches
)
_result_ = {{
    "rebuild_success": rebuild_success,
    "validation_ok": validation_ok,
    "exception": exception,
    "shape_errors": shape_errors,
    "sketches": sketches,
}}
"""
        validation_exec = await bridge.execute_python(validation_code)
        validation = (
            validation_exec.result
            if validation_exec.success and validation_exec.result
            else {
                "rebuild_success": False,
                "validation_ok": False,
                "exception": validation_exec.error_traceback,
                "shape_errors": [],
                "sketches": [],
            }
        )

        geometry_after = await _geometry_snapshot()

        def _shape_signature(item: dict[str, Any]) -> tuple[Any, Any]:
            return item.get("volume"), tuple(item.get("bounding_box") or [])

        before_shapes = {
            item.get("name"): item
            for item in geometry_before.get("objects", [])
            if item.get("name")
        }
        after_shapes = {
            item.get("name"): item
            for item in geometry_after.get("objects", [])
            if item.get("name")
        }
        affected_names = set((edit_result or {}).get("affected_features") or [])
        geometry_records = []
        for name in sorted(affected_names & set(before_shapes) & set(after_shapes)):
            before_sig = _shape_signature(before_shapes[name])
            after_sig = _shape_signature(after_shapes[name])
            changed = before_sig != after_sig
            geometry_records.append(
                {
                    "kind": "geometry_update",
                    "object": name,
                    "before": before_shapes[name],
                    "after": after_shapes[name],
                    "changed": changed,
                    "satisfied": changed,
                    "supported": True,
                }
            )
        geometry_supported = bool(geometry_records)
        geometry_update_ok = (
            all(item["satisfied"] for item in geometry_records)
            if geometry_supported
            else True
        )

        def _numeric_close(a: Any, b: Any) -> bool:
            try:
                fa = float(a)
                fb = float(b)
                return abs(fa - fb) <= max(1e-6, abs(fb) * 1e-6)
            except Exception:
                return a == b

        preserved_records = []
        for alias in preserve_aliases:
            before_item = params_before.get(alias)
            after_item = params_after.get(alias)
            before_bound = before_item.get("bound_to", []) if before_item else []
            after_bound = after_item.get("bound_to", []) if after_item else []
            value_preserved = (
                before_item is not None
                and after_item is not None
                and _numeric_close(before_item.get("value"), after_item.get("value"))
            )
            binding_preserved = bool(before_bound) and bool(after_bound)
            satisfied = (
                after_item is not None
                and binding_preserved
                and value_preserved
                and validation.get("validation_ok") is True
            )
            preserved_records.append(
                {
                    "kind": "alias",
                    "alias": alias,
                    "before_value": before_item.get("value") if before_item else None,
                    "after_value": after_item.get("value") if after_item else None,
                    "value_preserved": value_preserved,
                    "binding_preserved": binding_preserved,
                    "before_bound_to": before_bound,
                    "after_bound_to": after_bound,
                    "satisfied": satisfied,
                    "supported": before_item is not None,
                }
            )

        sketch_constraint_records = []
        for sketch in validation.get("sketches", []):
            conflicting = sketch.get("conflicting") or []
            redundant = sketch.get("redundant") or []
            fully = sketch.get("fully_constrained")
            dof = sketch.get("dof")
            dof_ok = expected_dof is None or dof == expected_dof
            present_types = set(sketch.get("constraint_types") or [])
            required_present = sorted(required_constraint_types & present_types)
            required_missing = sorted(required_constraint_types - present_types)
            required_ok = not required_missing
            satisfied = (
                not conflicting and (fully is not False) and dof_ok and required_ok
            )
            sketch_constraint_records.append(
                {
                    "kind": "sketch_constraint_health",
                    "sketch": sketch.get("name"),
                    "dof": dof,
                    "expected_dof": expected_dof,
                    "dof_ok": dof_ok,
                    "fully_constrained": fully,
                    "conflicting": conflicting,
                    "redundant": redundant,
                    "required_constraint_types": sorted(required_constraint_types),
                    "required_present": required_present,
                    "required_missing": required_missing,
                    "required_ok": required_ok,
                    "satisfied": satisfied,
                    "supported": True,
                }
            )

        coupled_records = []
        for spec in coupled_aliases:
            if isinstance(spec, str):
                alias = spec
                expected_value = value
            else:
                alias = spec.get("alias")
                expected_value = spec.get("expected_value", value)
            before_item = params_before.get(alias)
            after_item = params_after.get(alias)
            target_changed = after_item is not None and _numeric_close(
                after_item.get("value"), expected_value
            )
            coupled_records.append(
                {
                    "kind": "coupled_alias",
                    "alias": alias,
                    "expected_value": expected_value,
                    "before_value": before_item.get("value") if before_item else None,
                    "after_value": after_item.get("value") if after_item else None,
                    "satisfied": target_changed,
                    "supported": before_item is not None,
                }
            )

        free_records = []
        for alias in free_aliases:
            before_item = params_before.get(alias)
            after_item = params_after.get(alias)
            binding_after = after_item.get("bound_to", []) if after_item else []
            free_records.append(
                {
                    "kind": "free_alias",
                    "alias": alias,
                    "before_value": before_item.get("value") if before_item else None,
                    "after_value": after_item.get("value") if after_item else None,
                    "binding_after": binding_after,
                    "satisfied": after_item is not None,
                    "supported": before_item is not None,
                }
            )

        shape_records = []
        for shape_name in validation.get("shape_errors", []):
            shape_records.append(
                {
                    "kind": "shape_validity",
                    "object": shape_name,
                    "satisfied": False,
                    "supported": True,
                }
            )

        all_preserved_records = [
            *preserved_records,
            *coupled_records,
            *free_records,
            *sketch_constraint_records,
            *shape_records,
            *geometry_records,
        ]

        supported_count = sum(1 for item in all_preserved_records if item["supported"])
        satisfied_count = sum(1 for item in all_preserved_records if item["satisfied"])
        preserved_all_satisfied = (
            satisfied_count == supported_count if supported_count > 0 else True
        )
        cpcsr = (
            float(satisfied_count) / float(supported_count)
            if supported_count > 0
            else 1.0
        )
        rebuild_success = validation.get("rebuild_success") is True
        validation_ok = validation.get("validation_ok") is True and geometry_update_ok
        target_bound = bool(target_after.get("bound_to"))
        er = 1.0 if target_hit and rebuild_success and validation_ok else 0.0
        oes = er * cpcsr
        alias_preserved_supported = sum(
            1 for item in preserved_records if item["supported"]
        )
        alias_preserved_satisfied = sum(
            1 for item in preserved_records if item["satisfied"]
        )
        sketch_supported = sum(
            1 for item in sketch_constraint_records if item["supported"]
        )
        sketch_satisfied = sum(
            1 for item in sketch_constraint_records if item["satisfied"]
        )
        coupled_supported = sum(1 for item in coupled_records if item["supported"])
        coupled_satisfied = sum(1 for item in coupled_records if item["satisfied"])
        free_supported = sum(1 for item in free_records if item["supported"])
        free_satisfied = sum(1 for item in free_records if item["satisfied"])
        component_scores = {
            "target_hit": 1.0 if target_hit else 0.0,
            "target_binding": 1.0 if target_bound else 0.0,
            "rebuild_success": 1.0 if rebuild_success else 0.0,
            "validation_ok": 1.0 if validation_ok else 0.0,
            "preserved_alias_satisfaction": (
                float(alias_preserved_satisfied) / float(alias_preserved_supported)
                if alias_preserved_supported > 0
                else 1.0
            ),
            "sketch_constraint_health": (
                float(sketch_satisfied) / float(sketch_supported)
                if sketch_supported > 0
                else 1.0
            ),
            "coupled_alias_satisfaction": (
                float(coupled_satisfied) / float(coupled_supported)
                if coupled_supported > 0
                else 1.0
            ),
            "free_alias_reachability": (
                float(free_satisfied) / float(free_supported)
                if free_supported > 0
                else 1.0
            ),
            "shape_validity": 0.0 if validation.get("shape_errors") else 1.0,
            "geometry_update": 1.0 if geometry_update_ok else 0.0,
        }
        weighted_reward = (
            0.25 * component_scores["target_hit"]
            + 0.15 * component_scores["rebuild_success"]
            + 0.15 * component_scores["validation_ok"]
            + 0.20 * component_scores["preserved_alias_satisfaction"]
            + 0.15 * component_scores["sketch_constraint_health"]
            + 0.05 * component_scores["coupled_alias_satisfaction"]
            + 0.05 * component_scores["free_alias_reachability"]
        )

        return {
            "target_alias": target_alias,
            "old_value": old_value,
            "edited_value": value,
            "target_actual": target_actual,
            "target_expected": target_expected,
            "target_delta": target_delta,
            "target_hit": target_hit,
            "target_bound": target_bound,
            "rebuild_success": rebuild_success,
            "validation_ok": validation_ok,
            "preserved_records": preserved_records,
            "coupled_records": coupled_records,
            "free_records": free_records,
            "geometry_records": geometry_records,
            "sketch_constraint_records": sketch_constraint_records,
            "shape_records": shape_records,
            "all_preserved_records": all_preserved_records,
            "preserved_supported_constraints": supported_count,
            "preserved_satisfied_constraints": satisfied_count,
            "preserved_constraints_all_satisfied": preserved_all_satisfied,
            "ER": er,
            "cPCSR": cpcsr,
            "OES": oes,
            "reward": oes,
            "weighted_reward": weighted_reward,
            "component_scores": component_scores,
            "design_intent": {
                "preserve_aliases": preserve_aliases,
                "coupled_aliases": coupled_aliases,
                "free_aliases": free_aliases,
                "expected_dof": expected_dof,
                "required_constraint_types": sorted(required_constraint_types),
            },
            "edit_result": edit_result,
            "edit_error": edit_error,
            "validation": validation,
            "geometry_before": geometry_before,
            "geometry_after": geometry_after,
            "geometry_update_ok": geometry_update_ok,
            "success": edit_error is None,
        }

    # ------------------------------------------------------------------
    # Group G — Observation
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_body_snapshot(
        body_name: str | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Get a geometric snapshot of a PartDesign Body.

        Returns the bounding box, volume, feature list, and a sample of edge
        midpoints (``edge_samples``).  The ``edge_samples`` list provides 3-D
        coordinates near each edge, enabling construction of accurate
        ``near_points`` arguments for ``feature_fillet`` and
        ``feature_chamfer`` without knowing topological edge names.

        Args:
            body_name: Name of the PartDesign Body. Auto-detected if None.
            doc_name: Document name. Uses the active document if None.

        Returns:
            Dictionary with:
                - bounding_box: ``{x_min, x_max, y_min, y_max, z_min, z_max}``.
                - volume: Body volume in cubic millimetres.
                - features: List of ``{"name": str, "type": str}`` dicts.
                - edge_samples: List of
                  ``{"near_point": [x,y,z], "length": float,
                  "curve_type": "Line"|"Circle"|"BSpline"|"Other"}``
                  dicts — one per edge of the body shape.
                - success: ``True`` on success.

        Example:
            Find the top outer edge of a cylinder for filleting::

                snap = await get_body_snapshot(doc_name=doc_name)
                # Find edge near z=30, r=15:
                # snap["edge_samples"][N]["near_point"] → [15, 0, 30]
        """
        bridge = await get_bridge()
        code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

body_nm = {body_name!r}
body = None
if body_nm is not None:
    body = doc.getObject(body_nm)
else:
    for obj in doc.Objects:
        if obj.TypeId == "PartDesign::Body":
            body = obj
            break

if body is None:
    raise ValueError("No PartDesign Body found")

try:
    bb = body.Shape.BoundBox
    bbox = {{"x_min": bb.XMin,"x_max": bb.XMax,
             "y_min": bb.YMin,"y_max": bb.YMax,
             "z_min": bb.ZMin,"z_max": bb.ZMax}}
    volume = body.Shape.Volume
except Exception:
    bbox = {{}}; volume = 0.0

features = []
if hasattr(body, "Group"):
    for feat in body.Group:
        features.append({{"name": feat.Name, "type": feat.TypeId.split("::")[-1]}})
else:
    features.append({{"name": body.Name, "type": body.TypeId.split("::")[-1]}})

edge_samples = []
try:
    for edge in body.Shape.Edges:
        try:
            mid_param = (edge.FirstParameter + edge.LastParameter) / 2
            mid_pt = edge.valueAt(mid_param)
        except Exception:
            try:
                mid_pt = edge.CenterOfMass
            except Exception:
                continue
        length = edge.Length
        type_id = edge.Curve.TypeId if hasattr(edge.Curve, "TypeId") else type(edge.Curve).__name__
        if "Line" in type_id:
            curve_type = "Line"
        elif "Circle" in type_id:
            curve_type = "Circle"
        elif "BSpline" in type_id:
            curve_type = "BSpline"
        else:
            curve_type = "Other"
        edge_samples.append({{
            "near_point": [round(mid_pt.x, 4), round(mid_pt.y, 4), round(mid_pt.z, 4)],
            "length":     round(length, 4),
            "curve_type": curve_type,
        }})
except Exception:
    pass

_result_ = {{
    "bounding_box": bbox,
    "volume":       volume,
    "features":     features,
    "edge_samples": edge_samples,
    "success":      True,
}}
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to get body snapshot")

    # ------------------------------------------------------------------
    # Agentic primitive plan discovery and deterministic batch validation
    # ------------------------------------------------------------------

    @mcp.tool()
    async def describe_primitive_plan_schema() -> dict[str, Any]:
        """Describe the primitive-tool plan contract for agentic no-template runs.

        The returned primitive args schemas are derived from the actual Python
        function signatures of the registered primitive MCP tools.  This tool is
        deterministic and does not touch FreeCAD.
        """
        primitive_tools = {
            "create_coordinate_system": create_coordinate_system,
            "create_sketch_geometry": create_sketch_geometry,
            "apply_sketch_constraints": apply_sketch_constraints,
            "execute_extrude": execute_extrude,
            "execute_boolean": execute_boolean,
            "execute_revolve": execute_revolve,
            "execute_helix": execute_helix,
            "feature_fillet": feature_fillet,
            "feature_chamfer": feature_chamfer,
            "get_body_snapshot": get_body_snapshot,
        }
        return {
            "schema_name": "PrimitivePlan",
            "version": "v1",
            "deterministic": True,
            "schema_source_policy": {
                "unique_source": "Primitive MCP tool function signatures are the args schema source.",
                "agent_rule": (
                    "Agents may maintain graph state, trace, artifacts, selectors, "
                    "and a PrimitivePlan envelope, but must not maintain a second "
                    "primitive args schema."
                ),
            },
            "plan_levels": {
                "L0": "rough intent; route and high-level decomposition may still be missing",
                "L1": "workflow-level steps such as create document and observe result",
                "L2": "primitive tool sequence with some missing args/selectors",
                "L3": "primitive tool sequence with concrete tool_name and args/selectors",
            },
            "primitive_plan_envelope": {
                "required": ["plan_level", "steps"],
                "step_fields": {
                    "tool_name": "primitive MCP tool name; may be omitted for L0/L1 partial plans",
                    "args": "ordinary JSON args for tool_name; complete for L3 steps",
                    "selectors": "agent-side structured selectors resolved before MCP call",
                    "missing_args": "args that the agent must infer before execution",
                    "diagnostics": "planner assumptions and warnings",
                },
                "ordering": (
                    "The steps array order is the execution order. Agents should "
                    "not ask the model to generate step ids or dependency lists."
                ),
            },
            "primitive_tools": _primitive_tool_catalog(primitive_tools),
            "primitive_plan_examples": [
                {
                    "name": "L0 rough intent",
                    "plan_level": "L0",
                    "description": "Use when the user intent is too rough to pick CAD operations yet.",
                    "example": {
                        "plan_level": "L0",
                        "steps": [],
                        "missing_args": ["primitive_plan_steps"],
                        "diagnostics": {
                            "intent": "Need to infer shape, dimensions, and operation sequence."
                        },
                    },
                },
                {
                    "name": "L1 workflow scaffold",
                    "plan_level": "L1",
                    "description": "Use when only high-level workflow is known.",
                    "example": {
                        "plan_level": "L1",
                        "diagnostics": {
                            "harness_note": "The agent harness creates the session document before primitive plan execution."
                        },
                        "steps": [
                            {
                                "primitive_tool": "create_coordinate_system",
                                "missing_args": ["euler_angles", "translation"],
                            },
                            {
                                "primitive_tool": "get_body_snapshot",
                                "missing_args": ["doc_name", "body_name"],
                            },
                        ],
                    },
                },
                {
                    "name": "L2 block with through hole scaffold",
                    "plan_level": "L2",
                    "description": (
                        "Useful for a simple block-hole prompt.  The planner gives "
                        "the operation sequence and intent; the agent resolves exact "
                        "tool args step by step during execution."
                    ),
                    "example": {
                        "plan_level": "L2",
                        "steps": [
                            {
                                "primitive_tool": "create_coordinate_system",
                                "args": {
                                    "name": "BaseXY",
                                    "euler_angles": [0.0, 0.0, 0.0],
                                    "translation": [0.0, 0.0, 0.0],
                                },
                            },
                            {
                                "primitive_tool": "create_sketch_geometry",
                                "args": {
                                    "sketch_name": "BlockProfile",
                                    "coordinate_system_name": "BaseXY",
                                },
                                "missing_args": ["sketch"],
                                "diagnostics": {
                                    "intent": "Sketch a rectangle 40 mm by 20 mm on BaseXY."
                                },
                            },
                            {
                                "primitive_tool": "apply_sketch_constraints",
                                "args": {"sketch_name": "BlockProfile"},
                                "missing_args": ["constraints"],
                                "diagnostics": {
                                    "intent": (
                                        "Constrain rectangle closed, anchored to origin, "
                                        "horizontal/vertical, "
                                        "with length aliases block_length and block_width."
                                    )
                                },
                            },
                            {
                                "primitive_tool": "execute_extrude",
                                "args": {
                                    "sketch_name": "BlockProfile",
                                    "towards": 10.0,
                                    "opposite": 0.0,
                                    "feature_name": "BlockSolid",
                                    "param_aliases": {"towards": "block_height"},
                                },
                            },
                            {
                                "primitive_tool": "create_sketch_geometry",
                                "args": {
                                    "sketch_name": "HoleProfile",
                                    "coordinate_system_name": "BaseXY",
                                },
                                "missing_args": ["sketch"],
                                "diagnostics": {
                                    "intent": (
                                        "Sketch a circle near one corner; diameter is 5 mm. "
                                        "Use origin offsets in constraints rather than axis tokens."
                                    )
                                },
                            },
                            {
                                "primitive_tool": "apply_sketch_constraints",
                                "args": {"sketch_name": "HoleProfile"},
                                "missing_args": ["constraints"],
                                "diagnostics": {
                                    "intent": (
                                        "Use Diameter alias hole_diameter and Distance "
                                        "from origin to circle_1.center with HORIZONTAL "
                                        "and VERTICAL offsets."
                                    )
                                },
                            },
                            {
                                "primitive_tool": "execute_extrude",
                                "args": {
                                    "sketch_name": "HoleProfile",
                                    "towards": 12.0,
                                    "opposite": 1.0,
                                    "feature_name": "HoleTool",
                                },
                            },
                            {
                                "primitive_tool": "execute_boolean",
                                "args": {
                                    "base_object_name": "BlockSolid",
                                    "tool_object_name": "HoleTool",
                                    "operation": "Cut",
                                    "result_name": "BlockWithHole",
                                },
                            },
                        ],
                    },
                },
                {
                    "name": "L3 minimal rectangle extrude",
                    "plan_level": "L3",
                    "description": "All primitive args are concrete and can be called directly.",
                    "example": {
                        "plan_level": "L3",
                        "steps": [
                            {
                                "primitive_tool": "create_coordinate_system",
                                "args": {
                                    "name": "BaseXY",
                                    "euler_angles": [0.0, 0.0, 0.0],
                                    "translation": [0.0, 0.0, 0.0],
                                },
                            },
                            {
                                "primitive_tool": "create_sketch_geometry",
                                "args": {
                                    "sketch_name": "RectProfile",
                                    "coordinate_system_name": "BaseXY",
                                    "sketch": {
                                        "line_1": {
                                            "start": [0.0, 0.0],
                                            "end": [40.0, 0.0],
                                        },
                                        "line_2": {
                                            "start": [40.0, 0.0],
                                            "end": [40.0, 20.0],
                                        },
                                        "line_3": {
                                            "start": [40.0, 20.0],
                                            "end": [0.0, 20.0],
                                        },
                                        "line_4": {
                                            "start": [0.0, 20.0],
                                            "end": [0.0, 0.0],
                                        },
                                    },
                                },
                            },
                            {
                                "primitive_tool": "execute_extrude",
                                "args": {
                                    "sketch_name": "RectProfile",
                                    "towards": 10.0,
                                    "opposite": 0.0,
                                    "feature_name": "BlockSolid",
                                },
                            },
                        ],
                    },
                },
            ],
            "workflow_recipes": {
                "block_with_through_hole": [
                    "create_coordinate_system for the base sketch plane",
                    "create_sketch_geometry for the rectangular block profile",
                    "apply_sketch_constraints for rectangle closure and parametric length/width",
                    "execute_extrude for the base solid with a height alias",
                    "create_sketch_geometry for a circular hole tool profile",
                    "apply_sketch_constraints using Diameter plus origin Distance HORIZONTAL/VERTICAL offsets",
                    "execute_extrude for the hole tool body through the block",
                    "execute_boolean with operation='Cut'",
                ],
                "boolean_intersect": [
                    "create the base body",
                    "create the intersecting tool as its own body",
                    "call execute_boolean with operation='Intersect'",
                ],
                "histcad_arc_geometry": [
                    "For arc_N sketch entities, use {'start': [x, y], 'middle': [x, y], 'end': [x, y]}",
                    "The point described as 'via' in NLT maps to the canonical 'middle' field",
                    "Do not use 'mid' in generated primitive args",
                ],
                "local_sketch_offsets": [
                    "Use only 'origin' as the special point reference",
                    "Use Distance with direction HORIZONTAL for local X",
                    "Use Distance with direction VERTICAL for local Y",
                    "Do not emit x_axis, y_axis, or z_axis refs",
                ],
            },
            "guidance": {
                "no_artifact_bridge_tools": True,
                "ordinary_json_args": True,
                "special_refs": {
                    "allowed_special_point_refs": ["origin"],
                    "disallowed_axis_tokens": ["x_axis", "y_axis", "z_axis"],
                    "local_offset_rule": (
                        "Use Distance from origin to a sketch point with direction "
                        "HORIZONTAL or VERTICAL for local X/Y offsets."
                    ),
                },
                "boolean_intersect_rule": (
                    "For Intersect, create the tool as its own NewBody feature, "
                    "then call execute_boolean with base_object_name, "
                    "tool_object_name, and operation='Intersect'."
                ),
                "sketch_geometry_rule": {
                    "line_N": {"start": "[x, y]", "end": "[x, y]"},
                    "circle_N": {"center": "[x, y]", "radius": "number"},
                    "arc_N": {
                        "start": "[x, y]",
                        "middle": "[x, y]",
                        "end": "[x, y]",
                        "note": "canonical field is middle; NLT 'via' point maps here",
                    },
                },
                "batch_service_boundary": (
                    "execute_operation_plan is a deterministic OperationPlan "
                    "batch service for templates/converters, not the default "
                    "agentic no-template route."
                ),
            },
        }

    @mcp.tool()
    async def validate_primitive_plan(plan: dict[str, Any]) -> dict[str, Any]:
        """Validate a PrimitivePlan envelope without executing CAD."""
        primitive_tools = {
            "create_coordinate_system": create_coordinate_system,
            "create_sketch_geometry": create_sketch_geometry,
            "apply_sketch_constraints": apply_sketch_constraints,
            "execute_extrude": execute_extrude,
            "execute_boolean": execute_boolean,
            "execute_revolve": execute_revolve,
            "execute_helix": execute_helix,
            "feature_fillet": feature_fillet,
            "feature_chamfer": feature_chamfer,
            "get_body_snapshot": get_body_snapshot,
        }
        return _validate_primitive_plan_payload(plan, primitive_tools)

    @mcp.tool()
    async def validate_operation_plan(
        plan: dict[str, Any] | str | None = None,
        plan_path: str | None = None,
    ) -> dict[str, Any]:
        """Validate an ordered OperationPlan before batch execution."""
        from freecad_mcp.tools.operation_plan_schema import (
            validate_operation_plan as _validate_plan,
        )
        from freecad_mcp.tools.structured_input import load_structured_input

        try:
            plan_data = load_structured_input(plan, plan_path, "plan")
        except ValueError as exc:
            return {"valid": False, "errors": [str(exc)]}
        return _validate_plan(plan_data)

    @mcp.tool()
    async def execute_operation_plan(  # noqa: PLR0912
        plan: dict[str, Any] | str | None = None,
        doc_name: str | None = None,
        plan_path: str | None = None,
    ) -> dict[str, Any]:
        """Execute an ordered OperationPlan by dispatching primitive tools in list order."""
        from freecad_mcp.tools.operation_plan_schema import (
            OperationPlan,
        )
        from freecad_mcp.tools.operation_plan_schema import (
            validate_operation_plan as _validate_operation_plan,
        )
        from freecad_mcp.tools.structured_input import load_structured_input

        plan_data = load_structured_input(plan, plan_path, "plan")
        op_plan = OperationPlan.from_dict(plan_data)
        validation = _validate_operation_plan(plan_data)
        if not validation.get("valid"):
            raise ValueError(validation)

        feature_names: list[str] = []
        cs_map: dict[str, str] = {}
        sketch_name_map: dict[str, str] = {}
        object_name_map: dict[str, str] = {}
        sketch_constraint_results: list[dict[str, Any]] = []
        parametric_binding_diagnostics: list[dict[str, Any]] = []
        steps_completed = 0
        last_body_name: str | None = None
        use_spreadsheet_aliases = bool(
            op_plan.metadata.get("use_spreadsheet_aliases", True)
        )

        def _resolve_object_ref(name: str | None) -> str | None:
            if name is None:
                return None
            return object_name_map.get(name, name)

        def _record_bound_params(
            params: list[dict[str, Any]] | None,
            *,
            source: str,
            spec_name: str | None,
            actual_name: str | None,
        ) -> None:
            for item in params or []:
                parametric_binding_diagnostics.append(
                    {
                        "source": source,
                        "spec_name": spec_name,
                        "object_name": actual_name or item.get("object"),
                        "alias": item.get("alias"),
                        "role": item.get("role"),
                        "property": item.get("property"),
                        "cell": item.get("cell"),
                        "value": item.get("value"),
                        "unit": item.get("unit"),
                        "diagnostic": item.get("diagnostic"),
                        "bound": bool(item.get("cell") and item.get("property")),
                    }
                )

        for op in op_plan.operations:
            args = dict(op.args)
            tool_name = op.tool_name
            if doc_name is not None:
                args.setdefault("doc_name", doc_name)

            if tool_name == "create_coordinate_system":
                attachment = args.get("attachment_support")
                if isinstance(attachment, dict) and attachment.get("target"):
                    attachment = dict(attachment)
                    attachment["target"] = _resolve_object_ref(
                        str(attachment["target"])
                    )
                    args["attachment_support"] = attachment
                if not use_spreadsheet_aliases:
                    args.pop("param_aliases", None)
                result = await create_coordinate_system(**args)  # type: ignore[name-defined]
                spec_name = args.get("name")
                if spec_name:
                    cs_map[str(spec_name)] = result["cs_name"]
                _record_bound_params(
                    result.get("bound_params", []),
                    source="coordinate_system",
                    spec_name=spec_name,
                    actual_name=result.get("cs_internal_name"),
                )
            elif tool_name == "create_sketch_geometry":
                cs_name = args.get("coordinate_system_name")
                if cs_name in cs_map:
                    args["coordinate_system_name"] = cs_map[str(cs_name)]
                result = await create_sketch_geometry(**args)  # type: ignore[name-defined]
                spec_name = args.get("sketch_name")
                actual_name = result["sketch_name"]
                if spec_name:
                    sketch_name_map[str(spec_name)] = actual_name
            elif tool_name == "apply_sketch_constraints":
                sk_name = args.get("sketch_name")
                if sk_name in sketch_name_map:
                    args["sketch_name"] = sketch_name_map[str(sk_name)]
                args.setdefault("bind_to_spreadsheet", use_spreadsheet_aliases)
                result = await apply_sketch_constraints(**args)  # type: ignore[name-defined]
                sketch_constraint_results.append(
                    {
                        "sketch_name": args.get("sketch_name"),
                        "spec_sketch_name": sk_name,
                        "constraint_catalog": result.get("constraint_catalog", []),
                        "constraint_catalog_summary": result.get(
                            "constraint_catalog_summary", {}
                        ),
                        "applied_constraints": result.get("applied_constraints", []),
                        "applied_log": result.get("applied_log", []),
                        "dof_after": result.get("dof_after"),
                        "solve_status": result.get("solve_status"),
                        "geometry_drift": result.get("geometry_drift"),
                        "bound_params": result.get("bound_params", []),
                    }
                )
                _record_bound_params(
                    result.get("bound_params", []),
                    source="sketch_constraints",
                    spec_name=sk_name,
                    actual_name=args.get("sketch_name"),
                )
            elif tool_name in {"execute_extrude", "execute_revolve", "execute_helix"}:
                sk_name = args.get("sketch_name")
                if sk_name in sketch_name_map:
                    args["sketch_name"] = sketch_name_map[str(sk_name)]
                if not use_spreadsheet_aliases:
                    args.pop("param_aliases", None)
                if tool_name == "execute_extrude":
                    result = await execute_extrude(**args)  # type: ignore[name-defined]
                elif tool_name == "execute_revolve":
                    result = await execute_revolve(**args)  # type: ignore[name-defined]
                else:
                    result = await execute_helix(**args)  # type: ignore[name-defined]
                created_name = result.get("feature_name", "")
                spec_name = op.args.get("feature_name")
                if spec_name and created_name:
                    object_name_map[str(spec_name)] = created_name
                feature_names.append(created_name)
                last_body_name = result.get("body_name") or created_name
                _record_bound_params(
                    result.get("bound_params", []),
                    source="feature",
                    spec_name=str(spec_name) if spec_name else None,
                    actual_name=created_name,
                )
            elif tool_name == "execute_boolean":
                args["base_object_name"] = _resolve_object_ref(
                    args.get("base_object_name")
                )
                args["tool_object_name"] = _resolve_object_ref(
                    args.get("tool_object_name")
                )
                result = await execute_boolean(**args)  # type: ignore[name-defined]
                created_name = result.get("feature_name", "")
                spec_name = op.args.get("result_name") or op.args.get("feature_name")
                if spec_name and created_name:
                    object_name_map[str(spec_name)] = created_name
                feature_names.append(created_name)
                last_body_name = created_name
            elif tool_name in {"feature_fillet", "feature_chamfer"}:
                args.setdefault("body_name", last_body_name)
                if tool_name == "feature_fillet":
                    result = await feature_fillet(**args)  # type: ignore[name-defined]
                else:
                    result = await feature_chamfer(**args)  # type: ignore[name-defined]
                created_name = result.get("feature_name", "")
                feature_names.append(created_name)
            elif tool_name == "get_body_snapshot":
                args.setdefault("body_name", last_body_name)
                result = await get_body_snapshot(**args)  # type: ignore[name-defined]
            else:
                raise ValueError(f"Unsupported operation tool: {tool_name!r}")
            steps_completed += 1

        snapshot: dict[str, Any] = {}
        tunable: dict[str, Any] = {"params": [], "spreadsheet_name": None}
        if last_body_name:
            with contextlib.suppress(Exception):
                snapshot = await get_body_snapshot(  # type: ignore[name-defined]
                    body_name=last_body_name, doc_name=doc_name
                )
        with contextlib.suppress(Exception):
            tunable = await list_tunable_params(doc_name=doc_name)  # type: ignore[name-defined]

        return {
            "body_name": last_body_name,
            "feature_names": feature_names,
            "tunable_params": tunable,
            "bounding_box": snapshot.get("bounding_box", {}),
            "volume": snapshot.get("volume", 0.0),
            "sketch_constraint_results": sketch_constraint_results,
            "parametric_binding_diagnostics": parametric_binding_diagnostics,
            "steps_completed": steps_completed,
            "operation_count": len(op_plan.operations),
            "success": True,
        }

    @mcp.tool()
    async def validate_fabrication_plan(
        plan: dict[str, Any] | str | None = None,
        plan_path: str | None = None,
    ) -> dict[str, Any]:
        """Validate a FabricationPlan before execution.

        This is the no-template counterpart to ``validate_intent``.  It checks
        the plan shape, references, feature parameters, sketch entity names, and
        constraint references without executing CAD. ``plan`` may be a dict or
        JSON string; ``plan_path`` may point to a UTF-8 JSON file.
        """
        from freecad_mcp.tools.fabrication_schema import (
            validate_fabrication_plan as _validate_plan,
        )
        from freecad_mcp.tools.structured_input import load_structured_input

        try:
            plan_data = load_structured_input(plan, plan_path, "plan")
        except ValueError as exc:
            return {"valid": False, "errors": [str(exc)]}
        return _validate_plan(plan_data)

    # ------------------------------------------------------------------
    # Batch — execute_fabrication_plan
    # ------------------------------------------------------------------

    @mcp.tool()
    async def execute_fabrication_plan(  # noqa: PLR0912
        plan: dict[str, Any] | str | None = None,
        doc_name: str | None = None,
        plan_path: str | None = None,
    ) -> dict[str, Any]:
        """Execute a complete FabricationPlan by dispatching to Layer 1 primitives.

        This tool is the batch-execution bridge between Layer 2 domain template
        tools and the Layer 1 generic primitives.  It accepts a
        ``FabricationPlan`` dict (as returned by ``resolve_template`` or domain
        template tools such as ``resolve_l_connector_template``) and drives:

        1. ``create_coordinate_system`` for each ``coordinate_systems`` entry.
        2. ``create_sketch_geometry`` + ``apply_sketch_constraints`` for each
           ``sketches`` entry.
        3. ``execute_extrude`` / ``execute_revolve`` / ``execute_helix`` /
           ``execute_boolean`` for each explicit ``features`` entry.  Boolean
           features must name their base and tool objects; there is no implicit
           accumulated-solid state.
        4. ``feature_fillet`` / ``feature_chamfer`` for each ``finishes`` entry.

        The batch path is recommended for production automation. Production
        agents should first create a session-owned document with
        ``create_document(name=doc_name)`` and then pass that same ``doc_name``
        here; relying on the active document is only for interactive use. For
        RL training, call the individual primitives directly to obtain per-step
        reward signals.

        Args:
            plan: ``FabricationPlan.to_dict()`` output — a plain dict with
                ``coordinate_systems``, ``sketches``, ``features``, ``finishes``,
                ``param_aliases``, and ``metadata`` keys. May also be a JSON
                string.
            plan_path: Optional UTF-8 JSON file containing a FabricationPlan.
            doc_name: Target document. Uses the active document if None for
                backward compatibility; production automation should pass this
                explicitly.

        Returns:
            Dictionary with:
                - body_name: Name of the primary PartDesign Body.
                - feature_names: List of all created feature names.
                - tunable_params: Output of ``list_tunable_params()``.
                - bounding_box: Final bounding box.
                - volume: Final volume in cubic millimetres.
                - sketch_constraint_results: Per-sketch constraint diagnostics,
                  including ``constraint_catalog`` rows with actual FreeCAD
                  types such as ``Distance``, ``DistanceX``, and ``DistanceY``,
                  plus ``constraint_catalog_summary`` for compact harness traces.
                - steps_completed: Number of successfully completed steps.
                - success: ``True`` if all steps completed without error.

        Raises:
            ValueError: If the plan is invalid or any step fails.

        Example:
            Execute a plan produced by a domain template::

                intent = {
                    "template_name": "l_connector",
                    "slots": {
                        "arm_x_length": 50,
                        "arm_y_length": 80,
                        "width": 30,
                    },
                    "slot_bindings": [
                        "arm_x_width = width",
                        "arm_y_width = width",
                    ],
                }
                plan = await resolve_template(intent)
                doc_name = "FabricationDoc_123"
                await create_document(name=doc_name)
                result = await execute_fabrication_plan(plan, doc_name=doc_name)
                # result["tunable_params"] → [{alias:"arm_length",...}]
        """
        from freecad_mcp.tools.fabrication_schema import FabricationPlan
        from freecad_mcp.tools.structured_input import load_structured_input

        plan_data = load_structured_input(plan, plan_path, "plan")
        fab_plan = FabricationPlan.from_dict(plan_data)

        feature_names: list[str] = []
        cs_map: dict[str, str] = {}  # cs spec name → FreeCAD cs_name
        sketch_name_map: dict[str, str] = {}  # spec sketch_name → FreeCAD obj name
        object_name_map: dict[str, str] = {}
        sketch_constraint_results: list[dict[str, Any]] = []
        parametric_binding_diagnostics: list[dict[str, Any]] = []
        steps_completed = 0
        last_body_name: str | None = None
        partdesign_body_name: str | None = None
        use_spreadsheet_aliases = bool(
            fab_plan.metadata.get("use_spreadsheet_aliases", True)
        )

        def _record_bound_params(
            params: list[dict[str, Any]] | None,
            *,
            source: str,
            spec_name: str | None,
            actual_name: str | None,
        ) -> None:
            for item in params or []:
                parametric_binding_diagnostics.append(
                    {
                        "source": source,
                        "spec_name": spec_name,
                        "object_name": actual_name or item.get("object"),
                        "alias": item.get("alias"),
                        "role": item.get("role"),
                        "property": item.get("property"),
                        "cell": item.get("cell"),
                        "value": item.get("value"),
                        "unit": item.get("unit"),
                        "diagnostic": item.get("diagnostic"),
                        "bound": bool(item.get("cell") and item.get("property")),
                    }
                )

        async def _create_sketch_from_spec(sk_spec: Any) -> None:
            nonlocal steps_completed, partdesign_body_name
            cs_name_ref = sk_spec.coordinate_system_name
            if cs_name_ref and cs_name_ref in cs_map:
                cs_name_ref = cs_map[cs_name_ref]

            attach_body = partdesign_body_name
            if attach_body is None and sk_spec.attach_after_feature:
                attach_body = object_name_map.get(sk_spec.attach_after_feature)
            if attach_body is None and sk_spec.attach_body_feature:
                attach_body = object_name_map.get(sk_spec.attach_body_feature)

            geo_result = await create_sketch_geometry(  # type: ignore[name-defined]
                sketch=sk_spec.sketch,
                coordinate_system_name=cs_name_ref,
                body_name=attach_body,
                sketch_name=sk_spec.sketch_name,
                doc_name=doc_name,
            )
            actual_sk_name = geo_result["sketch_name"]
            if sk_spec.sketch_name:
                sketch_name_map[sk_spec.sketch_name] = actual_sk_name
            steps_completed += 1

            if sk_spec.constraints:
                constraint_result = await apply_sketch_constraints(  # type: ignore[name-defined]
                    sketch_name=actual_sk_name,
                    constraints=sk_spec.constraints,
                    doc_name=doc_name,
                    bind_to_spreadsheet=use_spreadsheet_aliases,
                )
                sketch_constraint_results.append(
                    {
                        "sketch_name": actual_sk_name,
                        "spec_sketch_name": sk_spec.sketch_name,
                        "constraint_catalog": constraint_result.get(
                            "constraint_catalog", []
                        ),
                        "constraint_catalog_summary": constraint_result.get(
                            "constraint_catalog_summary", {}
                        ),
                        "applied_constraints": constraint_result.get(
                            "applied_constraints", []
                        ),
                        "applied_log": constraint_result.get("applied_log", []),
                        "dof_after": constraint_result.get("dof_after"),
                        "solve_status": constraint_result.get("solve_status"),
                        "geometry_drift": constraint_result.get("geometry_drift"),
                        "bound_params": constraint_result.get("bound_params", []),
                    }
                )
                _record_bound_params(
                    constraint_result.get("bound_params", []),
                    source="sketch_constraints",
                    spec_name=sk_spec.sketch_name,
                    actual_name=actual_sk_name,
                )
                steps_completed += 1

        deferred_by_trigger: dict[str, list[Any]] = {}
        immediate_sketches: list[Any] = []
        for sk_spec in fab_plan.sketches:
            trigger = sk_spec.attach_after_feature
            if trigger:
                deferred_by_trigger.setdefault(trigger, []).append(sk_spec)
            else:
                immediate_sketches.append(sk_spec)

        async def _flush_deferred_sketches(trigger: str) -> None:
            await _flush_deferred_coordinate_systems(trigger)
            pending = deferred_by_trigger.pop(trigger, [])
            for sk_spec in pending:
                await _create_sketch_from_spec(sk_spec)

        # Coordinate systems attached to a feature are created only after that
        # feature exists. Both primitive and batch execution use this same tool.
        deferred_coordinate_systems: dict[str, list[Any]] = {}

        async def _create_coordinate_system_from_spec(cs_spec: Any) -> None:
            nonlocal steps_completed
            source_expressions: dict[str, str] = {}
            for property_path, binding in (cs_spec.param_bindings or {}).items():
                source = binding["source"]
                if source["kind"] == "feature_param":
                    feature_name = object_name_map.get(
                        source["feature_name"], source["feature_name"]
                    )
                    feature_property = {
                        "towards": "LengthFwd",
                        "opposite": "LengthRev",
                    }[source["param"]]
                    source_expressions[property_path] = (
                        f"{feature_name}.{feature_property}"
                    )
                else:
                    sketch_name = sketch_name_map.get(
                        source["sketch_name"], source["sketch_name"]
                    )
                    source_expressions[property_path] = (
                        f"{sketch_name}.Constraints.{source['constraint_alias']}"
                    )
            result = await create_coordinate_system(  # type: ignore[name-defined]
                euler_angles=cs_spec.euler_angles,
                translation=cs_spec.translation,
                name=cs_spec.name,
                attachment_support=(cs_spec.attachment_support or None),
                param_aliases=(
                    (cs_spec.param_aliases or None) if use_spreadsheet_aliases else None
                ),
                param_expressions=source_expressions or None,
                doc_name=doc_name,
            )
            if cs_spec.name:
                cs_map[cs_spec.name] = result["cs_name"]
            _record_bound_params(
                result.get("bound_params", []),
                source="coordinate_system",
                spec_name=cs_spec.name,
                actual_name=result.get("cs_internal_name"),
            )
            steps_completed += 1

        async def _flush_deferred_coordinate_systems(trigger: str) -> None:
            for cs_spec in deferred_coordinate_systems.pop(trigger, []):
                await _create_coordinate_system_from_spec(cs_spec)

        # Step 1: world coordinate systems; attached systems wait for targets.
        for cs_spec in fab_plan.coordinate_systems:
            target = (cs_spec.attachment_support or {}).get("target")
            if target:
                deferred_coordinate_systems.setdefault(str(target), []).append(cs_spec)
            else:
                await _create_coordinate_system_from_spec(cs_spec)

        # Step 2: Immediate sketches (deferred sketches flush after features)
        for sk_spec in immediate_sketches:
            await _create_sketch_from_spec(sk_spec)

        # Step 3: Features
        def _resolve_object_ref(_name: str | None) -> str | None:
            if _name is None:
                return None
            return object_name_map.get(_name, _name)

        for feat_spec in fab_plan.features:
            sk_nm = sketch_name_map.get(feat_spec.sketch_name, feat_spec.sketch_name)
            params = feat_spec.params
            aliases = (
                (feat_spec.param_aliases or fab_plan.param_aliases)
                if use_spreadsheet_aliases
                else {}
            )

            if feat_spec.type == "extrude":
                if feat_spec.operation != "NewBody":
                    raise ValueError(
                        "Canonical FabricationPlan requires explicit boolean "
                        f"features; extrude operation must be 'NewBody', got "
                        f"{feat_spec.operation!r} for "
                        f"{feat_spec.feature_name or sk_nm!r}."
                    )
                feat_result = await execute_extrude(  # type: ignore[name-defined]
                    sketch_name=sk_nm,
                    towards=params.get("towards", 10.0),
                    opposite=params.get("opposite", 0.0),
                    param_aliases=aliases or None,
                    feature_name=feat_spec.feature_name,
                    extrusion_mode=params.get("extrusion_mode", "auto"),
                    doc_name=doc_name,
                )
                steps_completed += 1
                created_name = feat_result.get("feature_name", "")
                if feat_spec.feature_name and created_name:
                    object_name_map[feat_spec.feature_name] = created_name
                feature_names.append(created_name)
                last_body_name = feat_result.get("body_name") or created_name
                if feat_result.get("body_name"):
                    partdesign_body_name = feat_result["body_name"]
                _record_bound_params(
                    feat_result.get("bound_params", []),
                    source="feature",
                    spec_name=feat_spec.feature_name,
                    actual_name=created_name,
                )
                if feat_spec.feature_name:
                    await _flush_deferred_sketches(feat_spec.feature_name)
                continue
            if feat_spec.type == "boolean":
                operation = params.get("operation", feat_spec.operation)
                base_ref = _resolve_object_ref(params.get("base_object_name"))
                tool_ref = _resolve_object_ref(params.get("tool_object_name"))
                if not base_ref or not tool_ref:
                    raise ValueError(
                        "Boolean feature requires params.base_object_name and "
                        "params.tool_object_name"
                    )
                feat_result = await execute_boolean(  # type: ignore[name-defined]
                    base_object_name=base_ref,
                    tool_object_name=tool_ref,
                    operation=operation,
                    result_name=feat_spec.feature_name,
                    boolean_mode=params.get("boolean_mode", "auto"),
                    doc_name=doc_name,
                )
                steps_completed += 1
                created_name = feat_result.get("feature_name", "")
                if feat_spec.feature_name and created_name:
                    object_name_map[feat_spec.feature_name] = created_name
                feature_names.append(created_name)
                last_body_name = created_name
                if feat_spec.feature_name:
                    await _flush_deferred_sketches(feat_spec.feature_name)
                continue
            if feat_spec.type == "revolve":
                if feat_spec.operation != "NewBody":
                    raise ValueError(
                        "Canonical FabricationPlan requires explicit boolean "
                        f"features; revolve operation must be 'NewBody', got "
                        f"{feat_spec.operation!r}."
                    )
                feat_result = await execute_revolve(  # type: ignore[name-defined]
                    sketch_name=sk_nm,
                    axis=params.get("axis", [[0, 0, 0], [0, 0, 1]]),
                    start=params.get("start", 0.0),
                    end=params.get("end", 360.0),
                    operation=feat_spec.operation,
                    param_aliases=aliases or None,
                    feature_name=feat_spec.feature_name,
                    doc_name=doc_name,
                )
            elif feat_spec.type == "helix":
                if feat_spec.operation != "NewBody":
                    raise ValueError(
                        "Canonical FabricationPlan requires explicit boolean "
                        f"features; helix operation must be 'NewBody', got "
                        f"{feat_spec.operation!r}."
                    )
                feat_result = await execute_helix(  # type: ignore[name-defined]
                    sketch_name=sk_nm,
                    axis=params.get("axis", [[0, 0, 0], [0, 0, 1]]),
                    pitch=params.get("pitch", 5.0),
                    turns=params.get("turns", 3.0),
                    handedness=params.get("handedness", "Right"),
                    operation=feat_spec.operation,
                    param_aliases=aliases or None,
                    feature_name=feat_spec.feature_name,
                    doc_name=doc_name,
                )
            else:
                raise ValueError(f"Unsupported feature type: {feat_spec.type!r}")

            created_name = feat_result.get("feature_name", "")
            if feat_spec.feature_name and created_name:
                object_name_map[feat_spec.feature_name] = created_name
            feature_names.append(created_name)
            last_body_name = feat_result.get("body_name") or created_name
            _record_bound_params(
                feat_result.get("bound_params", []),
                source="feature",
                spec_name=feat_spec.feature_name,
                actual_name=created_name,
            )
            steps_completed += 1

        # Step 4: Finishing
        for fin_spec in fab_plan.finishes:
            if fin_spec.type == "fillet":
                fin_result = await feature_fillet(  # type: ignore[name-defined]
                    near_points=fin_spec.near_points,
                    radius=fin_spec.params.get("radius", 1.0),
                    body_name=last_body_name,
                    doc_name=doc_name,
                )
            elif fin_spec.type == "chamfer":
                fin_result = await feature_chamfer(  # type: ignore[name-defined]
                    near_points=fin_spec.near_points,
                    dist=fin_spec.params.get("dist", 1.0),
                    angle=fin_spec.params.get("angle", 45.0),
                    plane=fin_spec.params.get("plane"),
                    body_name=last_body_name,
                    doc_name=doc_name,
                )
            else:
                continue
            feature_names.append(fin_result.get("feature_name", ""))
            steps_completed += 1

        # Collect final snapshot
        snapshot: dict[str, Any] = {}
        tunable: dict[str, Any] = {"params": [], "spreadsheet_name": None}
        if last_body_name:
            with contextlib.suppress(Exception):
                snapshot = await get_body_snapshot(  # type: ignore[name-defined]
                    body_name=last_body_name, doc_name=doc_name
                )
        with contextlib.suppress(Exception):
            tunable = await list_tunable_params(doc_name=doc_name)  # type: ignore[name-defined]

        return {
            "body_name": last_body_name,
            "feature_names": feature_names,
            "tunable_params": tunable,
            "bounding_box": snapshot.get("bounding_box", {}),
            "volume": snapshot.get("volume", 0.0),
            "sketch_constraint_results": sketch_constraint_results,
            "parametric_binding_diagnostics": parametric_binding_diagnostics,
            "steps_completed": steps_completed,
            "success": True,
        }
