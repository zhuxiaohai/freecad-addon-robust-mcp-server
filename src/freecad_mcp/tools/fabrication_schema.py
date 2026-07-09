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


@dataclass
class CoordinateSystemSpec:
    """Specification for a datum coordinate system (sketch plane).

    Attributes:
        euler_angles: Rotation [a, b, g] in degrees around X, Y, Z axes,
            applied as the active local-to-world rotation
            R = Rx(a) * Ry(b) * Rz(g) (HistCAD / Fusion 360 adapter
            convention).
        translation: Default attachment offset origin [x, y, z] in millimetres
            in the reference object's local frame (HistCAD ``Translation Vector``).
        name: Optional label used to reference this CS in SketchSpec.
        attachment_support: Optional attachment to a reference solid.
            Use ``{"feature_name": "L_Connector_Solid"}`` in plans; Layer 1
            resolves to ``{"object_name": "<FreeCADName>"}`` at execution.
        offset_expressions: FreeCAD expressions on ``AttachmentOffset`` (or
            ``Placement`` when unattached).  Layer 2 supplies these; Layer 1
            applies them as-is.

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
    offset_expressions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict for JSON transport."""
        d: dict[str, Any] = {
            "euler_angles": self.euler_angles,
            "translation": self.translation,
            "name": self.name,
        }
        if self.attachment_support:
            d["attachment_support"] = dict(self.attachment_support)
        if self.offset_expressions:
            d["offset_expressions"] = dict(self.offset_expressions)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CoordinateSystemSpec:
        """Deserialise from plain dict."""
        raw_attach = d.get("attachment_support", {})
        raw_exprs = d.get("offset_expressions", d.get("placement_expressions", {}))
        offset_expressions: dict[str, str] = {}
        for key, value in raw_exprs.items():
            prop = str(key)
            if prop.startswith("Placement."):
                prop = "AttachmentOffset." + prop.removeprefix("Placement.")
            offset_expressions[prop] = str(value)
        return cls(
            euler_angles=d["euler_angles"],
            translation=d["translation"],
            name=d.get("name"),
            attachment_support={
                str(k): v for k, v in raw_attach.items() if v is not None
            },
            offset_expressions=offset_expressions,
        )


@dataclass
class SketchSpec:
    """Specification for a 2-D sketch: geometry entities + constraints + plane.

    Either ``coordinate_system`` (inline) or ``coordinate_system_name``
    (reference to a CoordinateSystemSpec in the parent FabricationPlan) must
    be provided, or ``attachment_support`` for face-attached sketches.

    Attributes:
        sketch: HistCAD entity dict, e.g.
            ``{"line_1": {"start": [0,0], "end": [10,0]}, ...}``
        constraints: HistCAD constraint dict, e.g.
            ``{"Horizontal": ["line_1"], "Length": [["line_1", "10 mm"]]}``
        coordinate_system: Inline plane spec (mutually exclusive with
            ``coordinate_system_name``).
        coordinate_system_name: Name of a CoordinateSystemSpec declared in
            the parent plan's ``coordinate_systems`` list.
        attachment_support: Face-attachment spec so the sketch follows a face.
            ``{"near_point": [x, y, z]}`` — adapter resolves to the nearest
            face; ``{"face_name": "TopFace"}`` for a known face name.
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
    coordinate_system: CoordinateSystemSpec | None = None
    coordinate_system_name: str | None = None
    attachment_support: dict[str, Any] | None = None
    sketch_name: str | None = None
    attach_after_feature: str | None = None
    attach_body_feature: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict."""
        d: dict[str, Any] = {
            "sketch": self.sketch,
            "constraints": self.constraints,
        }
        if self.coordinate_system is not None:
            d["coordinate_system"] = self.coordinate_system.to_dict()
        if self.coordinate_system_name is not None:
            d["coordinate_system_name"] = self.coordinate_system_name
        if self.attachment_support is not None:
            d["attachment_support"] = self.attachment_support
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
        cs = d.get("coordinate_system")
        return cls(
            sketch=d["sketch"],
            constraints=d.get("constraints", {}),
            coordinate_system=(
                CoordinateSystemSpec.from_dict(cs) if cs is not None else None
            ),
            coordinate_system_name=d.get("coordinate_system_name"),
            attachment_support=d.get("attachment_support"),
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
