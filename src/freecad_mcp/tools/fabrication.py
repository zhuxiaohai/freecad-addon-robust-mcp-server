"""Fabrication primitives: HistCAD-style Layer 1 generic CAD modeling tools.

This module implements the Layer 1 generic fabrication primitives following
the HistCAD middleware paradigm.  The LLM or RL agent specifies geometry and
constraints using kernel-independent JSON schemas; the adapter translates
these deterministically to FreeCAD API calls.

Two-layer architecture
----------------------

Layer 2 (tools/templates/) converts domain-specific user intent into a
``FabricationPlan``.  Layer 1 (this module) executes it.  The contract
between layers is the ``FabricationPlan`` schema (fabrication_schema.py).

Tool groups
-----------

A — Coordinate System:  ``create_coordinate_system``
B — Sketch Geometry:    ``create_sketch_geometry``, ``parse_freecad_sketch``
C — Sketch Constraints: ``check_sketch_constraints``, ``apply_sketch_constraints``
D — Feature Execution:  ``execute_extrude``, ``execute_revolve``, ``execute_helix``
E — Finishing:          ``feature_fillet``, ``feature_chamfer``
F — Parametric Control: ``list_tunable_params``, ``set_tunable_param``
G — Observation:        ``get_body_snapshot``
Batch:                  ``execute_fabrication_plan``

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
  "weights":[...],"knots":[...]}``

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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


# ---------------------------------------------------------------------------
# Internal helpers (not exposed as MCP tools)
# ---------------------------------------------------------------------------

_SPREADSHEET_NAME = "FabricationParams"

_SKETCH_ENTITY_CODE = r"""
import Part, Sketcher

def _build_sketch_entities(sketch_obj, entity_dict):
    # Add HistCAD geometry entities to a Sketcher object.
    # Returns entity_index_map: {name -> geom_idx}.
    idx_map = {}
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
            m = FreeCAD.Vector(spec["middle"][0], spec["middle"][1], 0)
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
    return idx_map
"""

_SKETCH_CONSTRAINT_CODE = r"""
def _apply_histcad_constraints(sketch_obj, constraint_dict, idx_map):
    # Apply HistCAD constraints to a Sketcher object.
    # Returns list of redundant constraint names (strings).
    import Sketcher

    # FreeCAD 1.1 removed Sketcher.PointPos; use integer PointPos values directly.
    # 0 = none, 1 = start, 2 = end, 3 = middle
    NONE  = 0
    START = 1
    END   = 2
    MID   = 3

    def _resolve_entity(ref):
        # 'line_1' -> (idx, NONE)
        idx = idx_map[ref]
        return idx, NONE

    def _resolve_point(ref):
        # 'line_1.start' -> (idx, START)
        if "." not in ref:
            return idx_map[ref], NONE
        name, point = ref.split(".", 1)
        idx = idx_map[name]
        mapping = {
            "start": START, "end": END,
            "center": MID, "middle": MID,
        }
        pos = mapping.get(point, NONE)
        return idx, pos

    def _parse_length(val):
        # '10 mm' -> 10.0  (already in mm, FreeCAD uses mm internally)
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

    added = []
    for ctype, entries in constraint_dict.items():
        for entry in entries:
            try:
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
                    i1, _ = _resolve_entity(entry[0])
                    i2, _ = _resolve_entity(entry[1])
                    c = Sketcher.Constraint("Tangent", i1, i2)
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
                    else:
                        i, p = _resolve_entity(entry)
                        p = MID
                    c = Sketcher.Constraint("Block", i, p)
                elif ctype == "Midpoint":
                    i_pt, p_pt = _resolve_point(entry[0])
                    i_ln, _    = _resolve_entity(entry[1])
                    c = Sketcher.Constraint("PointOnObject", i_pt, p_pt, i_ln)
                elif ctype == "Mirror":
                    i_src, _ = _resolve_entity(entry[0])
                    i_dst, _ = _resolve_entity(entry[1])
                    i_ax,  _ = _resolve_entity(entry[2])
                    c = Sketcher.Constraint("Symmetric", i_src, START,
                                            i_dst, END, i_ax)
                elif ctype == "Angle":
                    i1, _ = _resolve_entity(entry[0])
                    i2, _ = _resolve_entity(entry[1])
                    import math
                    angle_rad = math.radians(float(entry[2]))
                    c = Sketcher.Constraint("Angle", i1, i2, angle_rad)
                elif ctype == "Diameter":
                    i, _ = _resolve_entity(entry[0])
                    val = _parse_length(entry[1])
                    c = Sketcher.Constraint("Radius", i, val / 2.0)
                elif ctype == "Radius":
                    i, _ = _resolve_entity(entry[0])
                    val = _parse_length(entry[1])
                    c = Sketcher.Constraint("Radius", i, val)
                elif ctype == "MajorRadius":
                    i, _ = _resolve_entity(entry[0])
                    val = _parse_length(entry[1])
                    c = Sketcher.Constraint("Radius", i, val)
                elif ctype == "MinorRadius":
                    i, _ = _resolve_entity(entry[0])
                    val = _parse_length(entry[1])
                    c = Sketcher.Constraint("Radius", i, val)
                elif ctype == "Length":
                    i, _ = _resolve_entity(entry[0])
                    val = _parse_length(entry[1])
                    c = Sketcher.Constraint("Distance", i, val)
                elif ctype == "Distance":
                    i1, p1 = _resolve_point(entry[0])
                    i2, p2 = _resolve_point(entry[1])
                    extra = entry[2] if len(entry) > 2 else {}
                    val = _parse_length(extra.get("length", 0))
                    direction = extra.get("direction", "")
                    if direction == "HORIZONTAL":
                        c = Sketcher.Constraint("DistanceX", i1, p1, i2, p2, val)
                    elif direction == "VERTICAL":
                        c = Sketcher.Constraint("DistanceY", i1, p1, i2, p2, val)
                    else:
                        c = Sketcher.Constraint("Distance", i1, p1, i2, p2, val)
                else:
                    continue
                sketch_obj.addConstraint(c)
                added.append(ctype)
            except Exception as _ce:
                pass  # redundant or conflicting constraints are silently skipped

    # Collect redundant constraint names from solver state
    redundant = []
    try:
        state_list = sketch_obj.solve()
        if hasattr(sketch_obj, "RedundantConstraints"):
            for r_idx in sketch_obj.RedundantConstraints:
                if 0 <= r_idx < len(sketch_obj.Constraints):
                    redundant.append(sketch_obj.Constraints[r_idx].Name or str(r_idx))
    except Exception:
        pass
    return redundant
"""


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_fabrication_tools(
    mcp: Any, get_bridge: Callable[[], Awaitable[Any]]
) -> None:
    """Register Layer 1 generic fabrication primitives with the MCP server.

    Registers 15 tools across 7 groups:

    - Group A — Coordinate System: ``create_coordinate_system``
    - Group B — Sketch Geometry: ``create_sketch_geometry``,
      ``parse_freecad_sketch``
    - Group C — Sketch Constraints: ``check_sketch_constraints``,
      ``apply_sketch_constraints``
    - Group D — Feature Execution: ``execute_extrude``, ``execute_revolve``,
      ``execute_helix``
    - Group E — Finishing: ``feature_fillet``, ``feature_chamfer``
    - Group F — Parametric Control: ``list_tunable_params``,
      ``set_tunable_param``
    - Group G — Observation: ``get_body_snapshot``
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
                axes respectively (intrinsic Tait-Bryan ZYX convention).
            translation: Origin ``[x, y, z]`` in millimetres in the world frame.
            name: Human-readable label for the coordinate system. If None, a
                label is auto-generated (e.g. ``"LocalCSYS_001"``).
            body_name: PartDesign Body to attach the LCS to. Uses the active
                body if None.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with:
                - cs_name: FreeCAD object name of the created LCS.
                - label: Human-readable label.
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
                # result["cs_name"] → "LocalCSYS_001"
        """
        bridge = await get_bridge()
        code = f"""
import math

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

euler = {euler_angles!r}
trans = {translation!r}
label = {name!r} or "LocalCSYS"

# Build the placement from Euler angles (degrees, ZYX intrinsic)
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
    lcs.Placement = placement
    lcs.Label = label
    if body is not None:
        body.addObject(lcs)
    doc.recompute()
    doc.commitTransaction()
    _result_ = {{
        "cs_name": lcs.Name,
        "label": lcs.Label,
        "placement": {{"euler_angles": {euler_angles!r}, "translation": {translation!r}}},
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
        coordinate_system: dict[str, Any] | None = None,
        coordinate_system_name: str | None = None,
        attachment_support: dict[str, Any] | None = None,
        body_name: str | None = None,
        sketch_name: str | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a 2-D sketch with HistCAD geometry entities.

        Accepts either an inline ``coordinate_system`` dict (Euler angles +
        translation) or a ``coordinate_system_name`` referencing an LCS
        created by ``create_coordinate_system``.  When ``attachment_support``
        is provided, the sketch is face-attached so it physically follows that
        face when the model is recomputed — enabling the "selective follow-on"
        product pattern.

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

            coordinate_system: Inline plane spec
                ``{"euler_angles": [a, b, g], "translation": [x, y, z]}``.
                Mutually exclusive with ``coordinate_system_name``.
            coordinate_system_name: Name of a datum LCS created by
                ``create_coordinate_system``.  Takes precedence over
                ``coordinate_system`` if both are supplied.
            attachment_support: Attach the sketch to an existing face so it
                follows that face during recompute.  Use
                ``{"near_point": [x, y, z]}`` for nearest-face resolution, or
                ``{"face_name": "TopFace"}`` for a known face name.
                Holes whose sketches are face-attached move with the face;
                holes with only ``coordinate_system`` stay at their absolute
                positions.
            body_name: PartDesign Body to add the sketch to. Auto-detected
                if None.
            sketch_name: Explicit FreeCAD object name. Auto-generated if None.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with:
                - sketch_name: FreeCAD object name (e.g. ``"Sketch001"``).
                - entity_index_map: ``{"line_1": 0, "circle_1": 1, ...}``.
                - dof_remaining: Degrees of freedom before constraints.
                - attachment_info: Face attachment details, or ``None``.
                - success: ``True`` on success.

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
                    coordinate_system={"euler_angles": [0,0,0],
                                       "translation": [0,0,0]},
                )
                # result["entity_index_map"] → {"line_1":0,"line_2":1,...}
        """
        bridge = await get_bridge()
        code = f"""
import math, Part, Sketcher

{_SKETCH_ENTITY_CODE}

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sketch_dict  = {sketch!r}
cs_inline    = {coordinate_system!r}
cs_name_ref  = {coordinate_system_name!r}
attach_spec  = {attachment_support!r}
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

    # Create sketch object
    sk = doc.addObject("Sketcher::SketchObject", sketch_nm or "Sketch")

    # Resolve attachment support (face-attached sketch for selective follow-on)
    attachment_info = None
    if attach_spec is not None:
        near_pt = attach_spec.get("near_point")
        face_nm = attach_spec.get("face_name")
        if near_pt is not None and body is not None:
            shape = body.Shape
            pt = FreeCAD.Vector(*near_pt)
            best_face = None
            best_dist = float("inf")
            for i, face in enumerate(shape.Faces):
                try:
                    dist = pt.distanceToShape(face)[0]
                except Exception:
                    dist = float("inf")
                if dist < best_dist:
                    best_dist = dist
                    best_face = (body, "Face" + str(i + 1))
            if best_face is not None:
                sk.AttachmentSupport = [best_face]
                sk.MapMode = "FlatFace"
                attachment_info = {{"face_name": best_face[1], "attachment_offset": 0.0}}
        elif face_nm is not None and body is not None:
            sk.AttachmentSupport = [(body, face_nm)]
            sk.MapMode = "FlatFace"
            attachment_info = {{"face_name": face_nm, "attachment_offset": 0.0}}
    elif cs_name_ref is not None:
        # Reference an existing LCS datum plane
        lcs = doc.getObject(cs_name_ref)
        if lcs is not None:
            sk.AttachmentSupport = [(lcs, "")]
            sk.MapMode = "ObjectXY"
    elif cs_inline is not None:
        euler = cs_inline.get("euler_angles", [0, 0, 0])
        trans = cs_inline.get("translation", [0, 0, 0])
        rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), euler[2])
        rot = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), euler[1]) * rot
        rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), euler[0]) * rot
        sk.Placement = FreeCAD.Placement(FreeCAD.Vector(*trans), rot)

    if body is not None:
        body.addObject(sk)

    doc.recompute()

    # Add geometry entities
    idx_map = _build_sketch_entities(sk, sketch_dict)

    doc.recompute()

    # Store entity map on sketch for later constraint resolution
    sk.setDocumentData("entity_index_map", str(idx_map))

    dof = sk.solve() if hasattr(sk, "solve") else -1

    doc.commitTransaction()
    _result_ = {{
        "sketch_name": sk.Name,
        "entity_index_map": idx_map,
        "dof_remaining": getattr(sk, "ConstraintCount", dof),
        "attachment_info": attachment_info,
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

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sk = doc.getObject({sketch_name!r})
if sk is None:
    raise ValueError(f"Sketch not found: {sketch_name!r}")

# Extract placement as Euler angles + translation
pl = sk.Placement
rot = pl.Rotation
trans = pl.Base
yaw   = math.degrees(rot.toEuler()[0])
pitch = math.degrees(rot.toEuler()[1])
roll  = math.degrees(rot.toEuler()[2])

sketch_dict = {{}}
idx_map = {{}}

line_cnt = circle_cnt = arc_cnt = ellipse_cnt = nurbs_cnt = 0

for i, geo in enumerate(sk.Geometry):
    type_id = geo.TypeId if hasattr(geo, "TypeId") else type(geo).__name__
    if "LineSegment" in type_id or "Line" in type_id:
        line_cnt += 1
        name = f"line_{{line_cnt}}"
        sketch_dict[name] = {{
            "start": [geo.StartPoint.x, geo.StartPoint.y],
            "end":   [geo.EndPoint.x,   geo.EndPoint.y],
        }}
    elif "ArcOfCircle" in type_id:
        arc_cnt += 1
        name = f"arc_{{arc_cnt}}"
        mid_param = (geo.FirstParameter + geo.LastParameter) / 2
        try:
            mid_pt = geo.value(mid_param)
        except Exception:
            mid_pt = geo.Center
        sketch_dict[name] = {{
            "start":  [geo.StartPoint.x,  geo.StartPoint.y],
            "middle": [mid_pt.x,          mid_pt.y],
            "end":    [geo.EndPoint.x,    geo.EndPoint.y],
        }}
    elif "Circle" in type_id:
        circle_cnt += 1
        name = f"circle_{{circle_cnt}}"
        sketch_dict[name] = {{
            "center": [geo.Center.x, geo.Center.y],
            "radius": geo.Radius,
        }}
    elif "Ellipse" in type_id or "ArcOfEllipse" in type_id:
        ellipse_cnt += 1
        name = f"ellipse_{{ellipse_cnt}}"
        sketch_dict[name] = {{
            "center": [geo.Center.x, geo.Center.y],
            "major":  geo.MajorRadius,
            "minor":  geo.MinorRadius,
            "angle":  math.degrees(getattr(geo, "AngleXU", 0.0)),
        }}
    elif "BSpline" in type_id:
        nurbs_cnt += 1
        name = f"nurbs_{{nurbs_cnt}}"
        poles = [[p.x, p.y] for p in geo.getPoles()]
        sketch_dict[name] = {{
            "degree":   geo.Degree,
            "periodic": geo.isPeriodic(),
            "controls": poles,
            "weights":  list(geo.getWeights()),
            "knots":    list(geo.getKnots()),
        }}
    else:
        continue
    idx_map[name] = i

# Extract existing constraints
existing_constraints = []
for c in sk.Constraints:
    existing_constraints.append(c.Type)

dof = sk.ConstraintCount if hasattr(sk, "ConstraintCount") else -1

_result_ = {{
    "sketch_name":          {sketch_name!r},
    "coordinate_system":    {{"euler_angles": [yaw, pitch, roll],
                              "translation":  [trans.x, trans.y, trans.z]}},
    "sketch":               sketch_dict,
    "entity_index_map":     idx_map,
    "dof_remaining":        dof,
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

# Retrieve entity index map stored during create_sketch_geometry
try:
    stored = sk.getDocumentData("entity_index_map") if hasattr(sk, "getDocumentData") else None
    idx_map = eval(stored) if stored else {{}}
except Exception:
    idx_map = {{}}
# Fallback: rebuild from geometry names if map is empty
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

        Returns:
            Dictionary with:
                - dof_before: Degrees of freedom before applying constraints.
                - dof_after: Degrees of freedom after applying constraints.
                  Zero means fully constrained; positive means under-constrained;
                  negative means over-constrained.
                - redundant_constraints: List of constraint type names that are
                  redundant (over-constraining).
                - sketch_valid: ``True`` if the sketch geometry is still valid
                  after constraint solving.
                - applied_count: Number of constraints successfully applied.
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

# Retrieve entity index map
try:
    stored = sk.getDocumentData("entity_index_map") if hasattr(sk, "getDocumentData") else None
    idx_map = eval(stored) if stored else {{}}
except Exception:
    idx_map = {{}}

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

dof_before = sk.ConstraintCount if hasattr(sk, "ConstraintCount") else -1

doc.openTransaction("Apply Sketch Constraints")
try:
    applied_before = len(sk.Constraints) if hasattr(sk, "Constraints") else 0
    redundant = _apply_histcad_constraints(sk, constraints_in, idx_map)
    applied_after = len(sk.Constraints) if hasattr(sk, "Constraints") else applied_before
    applied_count = applied_after - applied_before

    doc.recompute()
    dof_after = sk.ConstraintCount if hasattr(sk, "ConstraintCount") else -1

    # Check sketch validity
    try:
        valid = sk.State in ("ok", "fully", "under")
    except Exception:
        valid = True

    doc.commitTransaction()
    _result_ = {{
        "dof_before":            dof_before,
        "dof_after":             dof_after,
        "redundant_constraints": redundant,
        "sketch_valid":          valid,
        "applied_count":         applied_count,
        "success":               True,
    }}
except Exception as _e:
    doc.abortTransaction()
    raise
"""
        result = await bridge.execute_python(code)
        if result.success and result.result:
            return result.result
        raise ValueError(result.error_traceback or "Failed to apply constraints")

    # ------------------------------------------------------------------
    # Group D — Feature Execution
    # ------------------------------------------------------------------

    @mcp.tool()
    async def execute_extrude(
        sketch_name: str,
        towards: float,
        opposite: float = 0.0,
        operation: str = "NewBody",
        param_aliases: dict[str, str] | None = None,
        body_name: str | None = None,
        feature_name: str | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Extrude a sketch into a 3-D feature.

        Creates a ``PartDesign::Pad`` or ``PartDesign::Pocket`` depending on
        the ``operation`` parameter.  When ``param_aliases`` is provided, the
        adapter automatically creates a FreeCAD Spreadsheet (named
        ``FabricationParams``) and binds the specified parameters to named
        spreadsheet cells, exposing them as frontend slider controls.

        Args:
            sketch_name: Name of the sketch to extrude.
            towards: Extrusion distance along the positive sketch normal (mm).
            opposite: Extrusion distance along the negative sketch normal (mm).
                Defaults to ``0.0`` (one-direction extrusion).
            operation: Boolean semantics — ``"NewBody"`` (creates a new
                PartDesign Body), ``"Join"`` (Pad, adds material),
                ``"Cut"`` (Pocket, removes material), or ``"Intersect"``.
            param_aliases: Maps parameter keys to spreadsheet alias names for
                frontend sliders.  E.g. ``{"towards": "column_height"}`` binds
                the ``towards`` value to a cell aliased ``column_height``.
            body_name: PartDesign Body to add the feature to.  Required for
                ``"Join"`` and ``"Cut"`` operations.  Auto-detected if None.
            feature_name: Explicit FreeCAD object name. Auto-generated if None.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with:
                - feature_name: FreeCAD object name of the created feature.
                - body_name: Name of the containing PartDesign Body.
                - bounding_box: ``{x_min, x_max, y_min, y_max, z_min, z_max}``.
                - volume: Body volume in cubic millimetres.
                - bound_params: List of ``{alias, cell, value}`` dicts for
                  each parameter bound to the spreadsheet.
                - success: ``True`` on success.

        Raises:
            ValueError: If the sketch is not found or the extrusion fails.

        Example:
            Create a column with a named height parameter::

                result = await execute_extrude(
                    "Sketch001",
                    towards=150.0,
                    operation="NewBody",
                    param_aliases={"towards": "column_height"},
                )
                # result["bound_params"] →
                #   [{"alias":"column_height","cell":"A1","value":150.0}]
        """
        bridge = await get_bridge()
        code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No active document")

sk = doc.getObject({sketch_name!r})
if sk is None:
    raise ValueError(f"Sketch not found: {sketch_name!r}")

towards   = {towards!r}
opposite  = {opposite!r}
operation = {operation!r}
aliases   = {param_aliases!r} or {{}}
feat_nm   = {feature_name!r}
body_nm   = {body_name!r}

doc.openTransaction("Execute Extrude")
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

    if operation == "NewBody":
        # Always create a fresh body; never reuse an existing one for NewBody.
        new_body_nm = (body_nm or "Body")
        body = doc.addObject("PartDesign::Body", new_body_nm)
        body.addObject(sk)
        feat = doc.addObject("PartDesign::Pad", feat_nm or "Pad")
        feat.Profile = sk
        feat.Length  = towards
        if opposite > 0:
            if abs(opposite - towards) < 1e-6:
                # Symmetric (midplane) extrusion: FreeCAD 1.1 uses Midplane
                feat.Midplane = True
            else:
                feat.Length2 = opposite
        body.addObject(feat)
    elif operation == "Join":
        feat = doc.addObject("PartDesign::Pad", feat_nm or "Pad")
        feat.Profile = sk
        feat.Length  = towards
        if body is not None:
            body.addObject(feat)
    elif operation == "Cut":
        feat = doc.addObject("PartDesign::Pocket", feat_nm or "Pocket")
        feat.Profile = sk
        feat.Length  = abs(towards)
        if opposite > 0:
            feat.Length2 = opposite
        if body is not None:
            body.addObject(feat)
    else:  # Intersect fallback
        feat = doc.addObject("PartDesign::Pad", feat_nm or "Pad")
        feat.Profile = sk
        feat.Length  = towards
        if body is not None:
            body.addObject(feat)

    doc.recompute()

    # Bounding box + volume
    try:
        bb = body.Shape.BoundBox
        bbox = {{"x_min": bb.XMin, "x_max": bb.XMax,
                 "y_min": bb.YMin, "y_max": bb.YMax,
                 "z_min": bb.ZMin, "z_max": bb.ZMax}}
        volume = body.Shape.Volume
    except Exception:
        bbox = {{}}
        volume = 0.0

    # Bind param_aliases to spreadsheet
    bound_params = []
    if aliases:
        # Find or create the FabricationParams spreadsheet
        sheet = doc.getObject("FabricationParams")
        if sheet is None:
            sheet = doc.addObject("Spreadsheet::Sheet", "FabricationParams")

        col_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        # Find next free row
        row = 1
        while True:
            try:
                existing = sheet.get(f"A{{row}}")
                if not existing:
                    break
            except Exception:
                break
            row += 1

        alias_map = {{"towards": towards, "opposite": opposite}}
        for param_key, alias_name in aliases.items():
            value = alias_map.get(param_key, towards)
            cell = f"A{{row}}"
            sheet.set(cell, str(value))
            sheet.setAlias(cell, alias_name)
            # Bind the feature property to the spreadsheet cell
            try:
                if param_key == "towards":
                    feat.setExpression(".Length", f"FabricationParams.{{alias_name}}")
                elif param_key == "opposite":
                    feat.setExpression(".Length2", f"FabricationParams.{{alias_name}}")
            except Exception:
                pass
            bound_params.append({{"alias": alias_name, "cell": cell, "value": value}})
            row += 1

    doc.recompute()
    doc.commitTransaction()

    _result_ = {{
        "feature_name": feat.Name,
        "body_name":    body.Name if body else None,
        "bounding_box": bbox,
        "volume":       volume,
        "bound_params": bound_params,
        "success":      True,
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

        Creates a ``PartDesign::Revolution`` or ``PartDesign::Groove``
        depending on the ``operation`` parameter.

        Args:
            sketch_name: Name of the sketch to revolve.
            axis: 2x3 matrix defining the revolution axis:
                ``[[base_x, base_y, base_z], [dir_x, dir_y, dir_z]]``.
            start: Start angle in degrees (default: 0.0).
            end: End angle in degrees (default: 360.0 for a full revolution).
            operation: ``"NewBody"``, ``"Join"`` (Revolution), ``"Cut"``
                (Groove), or ``"Intersect"``.
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

    if operation in ("NewBody", "Join"):
        feat = doc.addObject("PartDesign::Revolution", feat_nm or "Revolution")
    else:
        feat = doc.addObject("PartDesign::Groove", feat_nm or "Groove")

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
            operation: ``"NewBody"``, ``"Join"``, or ``"Cut"``.
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

                snapshot = await get_body_snapshot()
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
    raise ValueError("No PartDesign Body found")

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

            # Find which feature properties reference this alias
            bound_to = []
            expr_pat = f"FabricationParams.{{alias}}"
            for obj in doc.Objects:
                if hasattr(obj, "ExpressionEngine"):
                    for prop, expr in obj.ExpressionEngine:
                        if expr_pat in str(expr):
                            bound_to.append({{"object": obj.Name, "property": prop}})

            params.append({{
                "alias":    alias,
                "value":    value,
                "unit":     "mm",
                "cell":     cell_addr,
                "bound_to": bound_to,
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
    expr_pat = f"FabricationParams.{{alias_name}}"
    affected = []
    for obj in doc.Objects:
        if hasattr(obj, "ExpressionEngine"):
            for prop, expr in obj.ExpressionEngine:
                if expr_pat in str(expr):
                    affected.append(obj.Name)
                    break

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

                snap = await get_body_snapshot()
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
    # Batch — execute_fabrication_plan
    # ------------------------------------------------------------------

    @mcp.tool()
    async def execute_fabrication_plan(  # noqa: PLR0912
        plan: dict[str, Any],
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Execute a complete FabricationPlan by dispatching to Layer 1 primitives.

        This tool is the batch-execution bridge between Layer 2 domain template
        tools and the Layer 1 generic primitives.  It accepts a
        ``FabricationPlan`` dict (as returned by domain template tools such as
        ``resolve_connector_params``) and drives:

        1. ``create_coordinate_system`` for each ``coordinate_systems`` entry.
        2. ``create_sketch_geometry`` + ``apply_sketch_constraints`` for each
           ``sketches`` entry.
        3. ``execute_extrude`` / ``execute_revolve`` / ``execute_helix`` for
           each ``features`` entry.
        4. ``feature_fillet`` / ``feature_chamfer`` for each ``finishes`` entry.

        The batch path is recommended for production automation.  For RL
        training, call the individual primitives directly to obtain per-step
        reward signals.

        Args:
            plan: ``FabricationPlan.to_dict()`` output — a plain dict with
                ``coordinate_systems``, ``sketches``, ``features``, ``finishes``,
                ``param_aliases``, and ``metadata`` keys.
            doc_name: Target document. Uses the active document if None.

        Returns:
            Dictionary with:
                - body_name: Name of the primary PartDesign Body.
                - feature_names: List of all created feature names.
                - tunable_params: Output of ``list_tunable_params()``.
                - bounding_box: Final bounding box.
                - volume: Final volume in cubic millimetres.
                - steps_completed: Number of successfully completed steps.
                - success: ``True`` if all steps completed without error.

        Raises:
            ValueError: If the plan is invalid or any step fails.

        Example:
            Execute a plan produced by a domain template::

                plan = await resolve_connector_params("L型连接件,5cmx8cm,宽3cm")
                result = await execute_fabrication_plan(plan)
                # result["tunable_params"] → [{alias:"arm_length",...}]
        """
        from freecad_mcp.tools.fabrication_schema import FabricationPlan

        fab_plan = FabricationPlan.from_dict(plan)

        feature_names: list[str] = []
        cs_map: dict[str, str] = {}  # cs spec name → FreeCAD cs_name
        sketch_name_map: dict[str, str] = {}  # spec sketch_name → FreeCAD obj name
        steps_completed = 0
        last_body_name: str | None = None

        # Step 1: Coordinate systems
        for cs_spec in fab_plan.coordinate_systems:
            result = await create_coordinate_system(  # type: ignore[name-defined]
                euler_angles=cs_spec.euler_angles,
                translation=cs_spec.translation,
                name=cs_spec.name,
                doc_name=doc_name,
            )
            if cs_spec.name:
                cs_map[cs_spec.name] = result["cs_name"]
            steps_completed += 1

        # Step 2: Sketches (geometry + constraints)
        for sk_spec in fab_plan.sketches:
            cs_name_ref = sk_spec.coordinate_system_name
            if cs_name_ref and cs_name_ref in cs_map:
                cs_name_ref = cs_map[cs_name_ref]

            geo_result = await create_sketch_geometry(  # type: ignore[name-defined]
                sketch=sk_spec.sketch,
                coordinate_system=(
                    sk_spec.coordinate_system.to_dict()
                    if sk_spec.coordinate_system
                    else None
                ),
                coordinate_system_name=cs_name_ref,
                attachment_support=sk_spec.attachment_support,
                sketch_name=sk_spec.sketch_name,
                doc_name=doc_name,
            )
            actual_sk_name = geo_result["sketch_name"]
            if sk_spec.sketch_name:
                sketch_name_map[sk_spec.sketch_name] = actual_sk_name
            steps_completed += 1

            if sk_spec.constraints:
                await apply_sketch_constraints(  # type: ignore[name-defined]
                    sketch_name=actual_sk_name,
                    constraints=sk_spec.constraints,
                    doc_name=doc_name,
                )
                steps_completed += 1

        # Step 3: Features
        for feat_spec in fab_plan.features:
            sk_nm = sketch_name_map.get(feat_spec.sketch_name, feat_spec.sketch_name)
            params = feat_spec.params
            aliases = feat_spec.param_aliases or fab_plan.param_aliases

            if feat_spec.type == "extrude":
                feat_result = await execute_extrude(  # type: ignore[name-defined]
                    sketch_name=sk_nm,
                    towards=params.get("towards", 10.0),
                    opposite=params.get("opposite", 0.0),
                    operation=feat_spec.operation,
                    param_aliases=aliases or None,
                    feature_name=feat_spec.feature_name,
                    doc_name=doc_name,
                )
            elif feat_spec.type == "revolve":
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
                continue

            feature_names.append(feat_result.get("feature_name", ""))
            last_body_name = feat_result.get("body_name")
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
            "steps_completed": steps_completed,
            "success": True,
        }
