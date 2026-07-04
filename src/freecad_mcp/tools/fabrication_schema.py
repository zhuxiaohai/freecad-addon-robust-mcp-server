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
        translation: Origin [x, y, z] in millimetres in the world frame.
        name: Optional label used to reference this CS in SketchSpec.

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

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict for JSON transport."""
        return {
            "euler_angles": self.euler_angles,
            "translation": self.translation,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CoordinateSystemSpec:
        """Deserialise from plain dict."""
        return cls(
            euler_angles=d["euler_angles"],
            translation=d["translation"],
            name=d.get("name"),
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
        )


@dataclass
class FeatureSpec:
    """Specification for a 3-D feature execution (extrude, revolve, or helix).

    Attributes:
        type: Feature type — ``"extrude"``, ``"revolve"``, or ``"helix"``.
        sketch_name: Name of the sketch to operate on.  Must match a
            ``SketchSpec.sketch_name`` resolved during plan execution, or be a
            pre-existing sketch object name in the document.
        operation: Boolean semantics — ``"NewBody"``, ``"Join"``,
            ``"Cut"``, or ``"Intersect"``.
        params: Type-specific parameter dict:
            - extrude: ``{"towards": float, "opposite": float}``
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
            sketch_name=d["sketch_name"],
            operation=d["operation"],
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
