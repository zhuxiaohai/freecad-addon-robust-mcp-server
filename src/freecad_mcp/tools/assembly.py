"""Assembly connector-contract tools for deterministic FreeCAD operations.

The LLM supplies structured connector parameters as a software-free intermediate
layer. FreeCAD remains the deterministic adapter that creates and tracks connector
frames via Part::LocalCoordinateSystem objects attached to parts.

Three-layer architecture:
  - Discovery (adapter-specific): get_mounting_features, find_faces_by_constraints
    scan B-rep geometry and return explicit [x,y,z] candidates.
  - LLM layer (software-free): receives explicit coordinates, adds semantic labels,
    calls create_connector with plain list[float] values only.
  - Execution (adapter-specific): create_connector, align_coordinate_systems,
    list_assembly_state operate on FreeCAD objects directly.

Connector state tracking:
  Part::LocalCoordinateSystem objects are attached to reference parts via
  AttachmentSupport + MapMode="ObjectXY". When a part moves, FreeCAD recompute
  updates the LCS automatically. list_assembly_state reads lcs.getGlobalPlacement()
  on-query to return the current world-frame coordinates.

Requires FreeCAD 1.1+ for Part::LocalCoordinateSystem support.
"""

from collections.abc import Awaitable, Callable
from typing import Any


def _raise_if_failed(result: Any, message: str) -> dict[str, Any]:
    """Return the bridge result payload or raise a useful error."""
    if result.success:
        return result.result
    raise ValueError(result.error_traceback or message)


def register_assembly_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register assembly-oriented tools with the Robust MCP Server."""

    @mcp.tool()
    async def find_faces_by_constraints(
        object_name: str,
        constraints: dict[str, Any],
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Find faces matching structured local geometric constraints.

        Constraint vectors and regions are interpreted in the object's local
        geometry frame. Returned candidate geometry is local-only.
        """
        bridge = await get_bridge()
        code = f"""
constraints = {constraints!r}
if "external_only" not in constraints:
    constraints["external_only"] = True

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No document found")
obj = doc.getObject({object_name!r})
if obj is None:
    raise ValueError(f"Object not found: {object_name!r}")
if not hasattr(obj, "Shape"):
    raise ValueError("Object has no shape")

# Transform shape to part's local design frame so constraints and outputs
# are stable regardless of how the part has been moved or rotated.
gpl = obj.getGlobalPlacement()
shape = obj.Shape.copy()
shape.transformShape(gpl.inverse().toMatrix())
bb = shape.BoundBox

def v(data):
    return FreeCAD.Vector(float(data[0]), float(data[1]), float(data[2]))

def unit(vector):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.normalize()
    return result

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
    return result

def arr(vec):
    return [round(vec.x, 6), round(vec.y, 6), round(vec.z, 6)]

def bbox_values(b):
    return [round(b.XMin, 6), round(b.YMin, 6), round(b.ZMin, 6), round(b.XMax, 6), round(b.YMax, 6), round(b.ZMax, 6)]

def face_normal(face):
    try:
        pr = face.ParameterRange
        return unit(face.normalAt((pr[0] + pr[1]) / 2, (pr[2] + pr[3]) / 2))
    except Exception:
        return None

def external(face, normal):
    if normal is None or not shape.Solids:
        return None
    try:
        return not shape.isInside(face.CenterOfMass + scaled(normal, 0.05), 1e-5, True)
    except Exception:
        return None

def side_matches(face, side):
    fbb = face.BoundBox
    side = str(side).lower()
    eps = max(bb.XLength, bb.YLength, bb.ZLength, 1.0) * 1e-6
    return (
        (side in ["+x", "xmax"] and abs(fbb.XMax - bb.XMax) <= eps)
        or (side in ["-x", "xmin"] and abs(fbb.XMin - bb.XMin) <= eps)
        or (side in ["+y", "ymax"] and abs(fbb.YMax - bb.YMax) <= eps)
        or (side in ["-y", "ymin"] and abs(fbb.YMin - bb.YMin) <= eps)
        or (side in ["+z", "zmax"] and abs(fbb.ZMax - bb.ZMax) <= eps)
        or (side in ["-z", "zmin"] and abs(fbb.ZMin - bb.ZMin) <= eps)
    )

def in_region(face, region):
    if not region:
        return True
    c = face.CenterOfMass
    for axis, value in region.items():
        if axis == "x_min" and c.x < value:
            return False
        if axis == "x_max" and c.x > value:
            return False
        if axis == "y_min" and c.y < value:
            return False
        if axis == "y_max" and c.y > value:
            return False
        if axis == "z_min" and c.z < value:
            return False
        if axis == "z_max" and c.z > value:
            return False
    return True

def canonical_axis(axis):
    axis = unit(axis)
    vals = [axis.x, axis.y, axis.z]
    for val in vals:
        if abs(val) > 1e-9:
            if val < 0:
                axis = FreeCAD.Vector(-axis.x, -axis.y, -axis.z)
            break
    return axis

def projected_axis_point(surface, face, normal):
    center = surface.Center
    offset = center - face.CenterOfMass
    return center - scaled(normal, offset.dot(normal))

def overlaps_face_region(surface_bbox, face_bbox, normal):
    ax = max(
        [("x", abs(normal.x)), ("y", abs(normal.y)), ("z", abs(normal.z))],
        key=lambda item: item[1],
    )[0]
    checks = []
    if ax != "x":
        checks.append(not (surface_bbox.XMax < face_bbox.XMin or surface_bbox.XMin > face_bbox.XMax))
    if ax != "y":
        checks.append(not (surface_bbox.YMax < face_bbox.YMin or surface_bbox.YMin > face_bbox.YMax))
    if ax != "z":
        checks.append(not (surface_bbox.ZMax < face_bbox.ZMin or surface_bbox.ZMin > face_bbox.ZMax))
    return all(checks)

def cylinder_holes_for_face(face, normal, axes):
    if normal is None:
        return {{"faces": [], "holes": []}}
    matched_faces = []
    holes_by_key = {{}}
    face_bb = face.BoundBox
    for cidx, other in enumerate(shape.Faces, start=1):
        surface_type = other.Surface.__class__.__name__
        if surface_type not in ["Cylinder", "Cone"]:
            continue
        axis = unit(other.Surface.Axis)
        axis_ok = abs(axis.dot(normal)) > 0.98
        for axis_hint in axes or []:
            axis_ok = axis_ok and abs(axis.dot(unit(v(axis_hint)))) > 0.98
        if not axis_ok:
            continue
        if overlaps_face_region(other.BoundBox, face_bb, normal):
            face_name = f"Face{{cidx}}"
            matched_faces.append(face_name)
            projected = projected_axis_point(other.Surface, face, normal)
            canon_axis = canonical_axis(other.Surface.Axis)
            key = (
                round(projected.x, 3), round(projected.y, 3), round(projected.z, 3),
                round(canon_axis.x, 3), round(canon_axis.y, 3), round(canon_axis.z, 3),
            )
            hole = holes_by_key.setdefault(key, {{
                "axis_point_local": arr(projected),
                "axis_local": arr(canon_axis),
                "surface_types": [],
                "radii": [],
                "faces": [],
            }})
            hole["faces"].append(face_name)
            if surface_type not in hole["surface_types"]:
                hole["surface_types"].append(surface_type)
            radius = round(getattr(other.Surface, "Radius", 0.0), 6)
            if radius and radius not in hole["radii"]:
                hole["radii"].append(radius)
    holes = list(holes_by_key.values())
    holes.sort(key=lambda h: (h["axis_point_local"][0], h["axis_point_local"][1], h["axis_point_local"][2]))
    for hole in holes:
        hole["radii"].sort()
    return {{"faces": matched_faces, "holes": holes}}

def hole_radius_matches(hole, hole_constraints):
    min_radius = hole_constraints.get("min_hole_radius")
    max_radius_bound = hole_constraints.get("max_hole_radius")
    if min_radius is None and max_radius_bound is None:
        return True
    radii = hole.get("radii") or []
    if not radii:
        return False
    largest_radius = max(radii)
    if min_radius is not None and largest_radius < float(min_radius):
        return False
    if max_radius_bound is not None and largest_radius > float(max_radius_bound):
        return False
    return True

def filter_holes_by_radius(holes, hole_constraints):
    filtered = [hole for hole in holes if hole_radius_matches(hole, hole_constraints)]
    if not filtered:
        return [], []
    hole_faces = sorted({{face for hole in filtered for face in hole["faces"]}})
    return filtered, hole_faces

candidates = []
for idx, face in enumerate(shape.Faces, start=1):
    surface_type = face.Surface.__class__.__name__
    normal = face_normal(face)
    reasons = []
    score = 0

    wanted_type = constraints.get("surface_type")
    if wanted_type and surface_type.lower() != str(wanted_type).lower():
        continue
    if wanted_type:
        score += 10
        reasons.append(f"surface_type={{surface_type}}")

    if constraints.get("min_area") is not None:
        if face.Area < float(constraints["min_area"]):
            continue
        score += 5
        reasons.append("area>=min_area")

    is_ext = external(face, normal)
    if constraints["external_only"] and is_ext is not True:
        continue
    if constraints["external_only"]:
        score += 10
        reasons.append("external")

    if constraints.get("normal_parallel_to") is not None:
        if normal is None:
            continue
        target = unit(v(constraints["normal_parallel_to"]))
        dot = abs(normal.dot(target))
        if dot < float(constraints.get("normal_tolerance", 0.98)):
            continue
        score += 20 + dot
        reasons.append("normal_parallel_to")

    if constraints.get("normal_same_direction_to") is not None:
        if normal is None:
            continue
        target = unit(v(constraints["normal_same_direction_to"]))
        dot = normal.dot(target)
        if dot < float(constraints.get("normal_tolerance", 0.98)):
            continue
        score += 20 + dot
        reasons.append("normal_same_direction_to")

    if constraints.get("bbox_side") is not None:
        if not side_matches(face, constraints["bbox_side"]):
            continue
        score += 8
        reasons.append(f"bbox_side={{constraints['bbox_side']}}")

    if not in_region(face, constraints.get("region_hint")):
        continue
    if constraints.get("region_hint"):
        score += 4
        reasons.append("region_hint")

    hole_axes = constraints.get("contains_hole_axes")
    hole_matches = cylinder_holes_for_face(face, normal, hole_axes)
    holes = hole_matches["holes"]
    hole_faces = hole_matches["faces"]
    radius_constrained = (
        constraints.get("min_hole_radius") is not None
        or constraints.get("max_hole_radius") is not None
    )
    if radius_constrained:
        holes, hole_faces = filter_holes_by_radius(holes, constraints)
    hole_count = len(holes)

    if hole_axes is not None and not holes:
        continue
    if radius_constrained and not holes:
        continue
    if constraints.get("min_hole_count") is not None and hole_count < int(constraints["min_hole_count"]):
        continue
    if constraints.get("max_hole_count") is not None and hole_count > int(constraints["max_hole_count"]):
        continue
    if holes:
        score += min(hole_count, 8)
        reasons.append(f"hole_axis_matches={{hole_count}}")
    if radius_constrained and holes:
        score += 6
        reasons.append(f"hole_radius_matches={{hole_count}}")

    candidates.append({{
        "face": f"Face{{idx}}",
        "index": idx,
        "surface_type": surface_type,
        "score": round(score, 6),
        "reasons": reasons,
        "area": face.Area,
        "center_local": arr(face.CenterOfMass),
        "normal_local": arr(normal) if normal is not None else None,
        "bbox_local": bbox_values(face.BoundBox),
        "is_external": is_ext,
        "matching_hole_faces": hole_faces,
        "matching_holes": holes,
        "hole_count": hole_count,
        "cylindrical_face_count": len(hole_faces),
    }})

candidates.sort(key=lambda item: (-item["score"], -item["area"], item["index"]))
_result_ = {{"object_name": obj.Name, "constraints": constraints, "candidates": candidates}}
"""
        result = await bridge.execute_python(code)
        return _raise_if_failed(result, "Find faces by constraints failed")

    @mcp.tool()
    async def get_mounting_features(
        object_name: str,
        face: str,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Extract local connector candidates from a mounting face."""
        bridge = await get_bridge()
        code = f"""
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No document found")
obj = doc.getObject({object_name!r})
if obj is None:
    raise ValueError(f"Object not found: {object_name!r}")
if not hasattr(obj, "Shape"):
    raise ValueError("Object has no shape")

face_name = {face!r}

# Transform shape to part's local design frame so all computations are stable
# regardless of how the part has been moved or rotated.  obj.Shape coords are in
# world space; applying the inverse global placement gives the design-frame shape.
gpl = obj.getGlobalPlacement()
local_shape = obj.Shape.copy()
local_shape.transformShape(gpl.inverse().toMatrix())

face_index = int(face_name.replace("Face", "")) - 1
if face_index < 0 or face_index >= len(local_shape.Faces):
    raise ValueError(f"Invalid face reference: {{face_name}}")
mount_face = local_shape.Faces[face_index]

def arr(vec):
    return [round(vec.x, 6), round(vec.y, 6), round(vec.z, 6)]

def unit(vector):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.normalize()
    return result

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
    return result

def normal_at(f):
    pr = f.ParameterRange
    result = f.normalAt((pr[0] + pr[1]) / 2, (pr[2] + pr[3]) / 2)
    result.normalize()
    return result

def canonical_axis(axis):
    axis = unit(axis)
    vals = [axis.x, axis.y, axis.z]
    for val in vals:
        if abs(val) > 1e-9:
            if val < 0:
                axis = FreeCAD.Vector(-axis.x, -axis.y, -axis.z)
            break
    return axis

def projected_axis_point(surface, face, normal):
    center = surface.Center
    offset = center - face.CenterOfMass
    return center - scaled(normal, offset.dot(normal))

def overlaps_face_region(surface_bbox, face_bbox, normal):
    ax = max(
        [("x", abs(normal.x)), ("y", abs(normal.y)), ("z", abs(normal.z))],
        key=lambda item: item[1],
    )[0]
    checks = []
    if ax != "x":
        checks.append(not (surface_bbox.XMax < face_bbox.XMin or surface_bbox.XMin > face_bbox.XMax))
    if ax != "y":
        checks.append(not (surface_bbox.YMax < face_bbox.YMin or surface_bbox.YMin > face_bbox.YMax))
    if ax != "z":
        checks.append(not (surface_bbox.ZMax < face_bbox.ZMin or surface_bbox.ZMin > face_bbox.ZMax))
    return all(checks)

def projected_face_axis(vector, normal):
    axis = vector - scaled(normal, vector.dot(normal))
    if axis.Length <= 1e-9:
        return None
    axis.normalize()
    return axis

normal = normal_at(mount_face)
face_bb = mount_face.BoundBox
# BoundBox of the local-space shape is a stable AABB in the design frame,
# so this direction is consistent across rotations.
bbox_center_axis = projected_face_axis(mount_face.CenterOfMass - local_shape.BoundBox.Center, normal)

holes_by_key = {{}}
hole_faces = []
for idx, f in enumerate(local_shape.Faces, start=1):
    surface_type = f.Surface.__class__.__name__
    if surface_type not in ["Cylinder", "Cone"]:
        continue
    axis = unit(f.Surface.Axis)
    if abs(axis.dot(normal)) < 0.98:
        continue
    if not overlaps_face_region(f.BoundBox, face_bb, normal):
        continue
    canon_axis = canonical_axis(f.Surface.Axis)
    projected = projected_axis_point(f.Surface, mount_face, normal)
    face_ref = f"Face{{idx}}"
    hole_faces.append(face_ref)
    key = (
        round(projected.x, 3), round(projected.y, 3), round(projected.z, 3),
        round(canon_axis.x, 3), round(canon_axis.y, 3), round(canon_axis.z, 3),
    )
    hole = holes_by_key.setdefault(key, {{
        "axis_point_local": arr(projected),
        "axis_local": arr(canon_axis),
        "surface_types": [],
        "radii": [],
        "faces": [],
    }})
    hole["faces"].append(face_ref)
    if surface_type not in hole["surface_types"]:
        hole["surface_types"].append(surface_type)
    radius = round(getattr(f.Surface, "Radius", 0.0), 6)
    if radius and radius not in hole["radii"]:
        hole["radii"].append(radius)

unique_holes = list(holes_by_key.values())
unique_holes.sort(key=lambda h: (h["axis_point_local"][0], h["axis_point_local"][1], h["axis_point_local"][2]))
for hole in unique_holes:
    hole["radii"].sort()

hole_array_center = None
if unique_holes:
    hole_array_center = [
        round(sum(h["axis_point_local"][0] for h in unique_holes) / len(unique_holes), 6),
        round(sum(h["axis_point_local"][1] for h in unique_holes) / len(unique_holes), 6),
        round(sum(h["axis_point_local"][2] for h in unique_holes) / len(unique_holes), 6),
    ]

tertiary = bbox_center_axis
if tertiary is None:
    trial = FreeCAD.Vector(0, 0, 1)
    if abs(normal.dot(trial)) > 0.95:
        trial = FreeCAD.Vector(0, 1, 0)
    tertiary = projected_face_axis(trial, normal)
secondary = tertiary.cross(normal)
secondary.normalize()

connector_candidates = [
    {{
        "name_hint": f"{{obj.Name}}_{{face_name}}_face_center",
        "semantic_role": "mounting_face",
        "origin_local": arr(mount_face.CenterOfMass),
        "primary_axis_local": arr(normal),
        "secondary_axis_local": arr(secondary),
        "tertiary_axis_local": arr(tertiary),
        "source_features": {{
            "resolver": "face_center",
            "face": face_name,
            "hole_faces": hole_faces,
        }},
    }}
]
if hole_array_center is not None:
    connector_candidates.append({{
        "name_hint": f"{{obj.Name}}_{{face_name}}_hole_array",
        "semantic_role": "hole_array_mount",
        "origin_local": hole_array_center,
        "primary_axis_local": arr(normal),
        "secondary_axis_local": arr(secondary),
        "tertiary_axis_local": arr(tertiary),
        "source_features": {{
            "resolver": "hole_array_center",
            "face": face_name,
            "hole_faces": hole_faces,
        }},
    }})

_result_ = {{
    "object_name": obj.Name,
    "face": face_name,
    "surface_type": mount_face.Surface.__class__.__name__,
    "unique_holes": unique_holes,
    "hole_count": len(unique_holes),
    "connector_candidates": connector_candidates,
}}
"""
        result = await bridge.execute_python(code)
        return _raise_if_failed(result, "Get mounting features failed")

    @mcp.tool()
    async def create_connector(
        object_name: str,
        name: str,
        origin: list[float],
        primary_axis: list[float],
        tertiary_axis: list[float],
        semantic_label: str = "",
        source_features: dict[str, Any] | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a connector on a part as a Part::LocalCoordinateSystem (LCS).

        The connector tracks the part's movement automatically via FreeCAD's
        parametric attachment system. All input coordinates are in the part's
        local frame (as returned by get_mounting_features candidates).

        This tool implements the software-free intermediate layer: the LLM passes
        only explicit [x, y, z] values derived from discovery tools. No B-rep
        face references or resolver types are accepted.

        The observation returned contains global coordinates reflecting the
        connector's current world-frame position for reflective modeling.

        Requires FreeCAD 1.1+.

        Args:
            object_name: Name of the part object to attach the connector to.
            name: Unique identifier for this connector in the document.
            origin: [x, y, z] origin point in the part's local frame.
            primary_axis: [x, y, z] primary axis unit vector (assembly direction /
                face normal). Corresponds to ArtiCAD connector ẑ.
            tertiary_axis: [x, y, z] reference axis unit vector (orientation
                reference, perpendicular to primary). Corresponds to ArtiCAD x̂.
            semantic_label: Human-readable description of the connector's purpose,
                e.g. "top face bolt pattern center". Corresponds to ArtiCAD label l.
            source_features: Optional provenance metadata forwarded from
                get_mounting_features candidates (face name, resolver type, etc.).
            doc_name: Document name, defaults to active document.
        """
        bridge = await get_bridge()
        code = f"""
import json

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No document found")
obj = doc.getObject({object_name!r})
if obj is None:
    raise ValueError(f"Object not found: {object_name!r}")

origin_raw = {origin!r}
primary_raw = {primary_axis!r}
tertiary_raw = {tertiary_axis!r}
semantic_label = {semantic_label!r}
source_features = {source_features!r} or {{}}

def arr(vec):
    return [round(vec.x, 6), round(vec.y, 6), round(vec.z, 6)]

def unit(vector):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.normalize()
    return result

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
    return result

# Validate: only explicit list[float] accepted - no resolver dicts
if not isinstance(origin_raw, (list, tuple)) or len(origin_raw) != 3:
    raise TypeError(f"origin must be a list of 3 floats, got: {{type(origin_raw).__name__}}")
if not isinstance(primary_raw, (list, tuple)) or len(primary_raw) != 3:
    raise TypeError(f"primary_axis must be a list of 3 floats, got: {{type(primary_raw).__name__}}")
if not isinstance(tertiary_raw, (list, tuple)) or len(tertiary_raw) != 3:
    raise TypeError(f"tertiary_axis must be a list of 3 floats, got: {{type(tertiary_raw).__name__}}")

origin_local = FreeCAD.Vector(float(origin_raw[0]), float(origin_raw[1]), float(origin_raw[2]))
primary_local = FreeCAD.Vector(float(primary_raw[0]), float(primary_raw[1]), float(primary_raw[2]))
tertiary_raw_vec = FreeCAD.Vector(float(tertiary_raw[0]), float(tertiary_raw[1]), float(tertiary_raw[2]))

if primary_local.Length <= 1e-9:
    raise ValueError("primary_axis must be non-zero")
primary_local = unit(primary_local)

# Build orthonormal basis: primary->X, tertiary->Z (projected perp to primary), secondary=ZxX->Y
tertiary_local = tertiary_raw_vec - scaled(primary_local, tertiary_raw_vec.dot(primary_local))
if tertiary_local.Length <= 1e-9:
    raise ValueError("tertiary_axis cannot be parallel to primary_axis")
tertiary_local.normalize()
secondary_local = tertiary_local.cross(primary_local)
secondary_local.normalize()

# Build AttachmentOffset as 4x4 matrix: columns = [primary, secondary, tertiary, origin]
# This encodes the connector frame in the part's local coordinate system.
# Reading back: Rotation.multVec([1,0,0]) = primary, [0,1,0] = secondary, [0,0,1] = tertiary
m = FreeCAD.Matrix()
m.A11, m.A21, m.A31 = primary_local.x, primary_local.y, primary_local.z
m.A12, m.A22, m.A32 = secondary_local.x, secondary_local.y, secondary_local.z
m.A13, m.A23, m.A33 = tertiary_local.x, tertiary_local.y, tertiary_local.z
m.A14, m.A24, m.A34 = origin_local.x, origin_local.y, origin_local.z
attachment_offset = FreeCAD.Placement(m)

# Require FreeCAD 1.1+ for Part::LocalCoordinateSystem
ver = App.Version()
ver_major = int(str(ver[0])) if str(ver[0]).isdigit() else 0
ver_minor = int(str(ver[1])) if len(ver) > 1 and str(ver[1]).isdigit() else 0
if (ver_major, ver_minor) < (1, 1):
    raise RuntimeError(
        f"Part::LocalCoordinateSystem requires FreeCAD 1.1+. "
        f"Detected version: {{ver_major}}.{{ver_minor}}"
    )

doc.openTransaction("Create Connector")
try:
    lcs = doc.addObject("Part::LocalCoordinateSystem", {name!r})
    lcs.Label = {name!r}

    # Place LCS in the same container as the reference object so it moves with
    # the assembly hierarchy when a parent App::Part is repositioned.
    parent = obj.getParentGeoFeatureGroup() if hasattr(obj, "getParentGeoFeatureGroup") else None
    if parent is not None:
        parent.addObjects([lcs])

    # Parametric attachment: LCS tracks obj's full placement (translation + rotation).
    # When obj.Placement changes, FreeCAD recompute updates lcs.getGlobalPlacement().
    lcs.AttachmentSupport = [(obj, ("",))]
    lcs.MapMode = "ObjectXY"
    lcs.AttachmentOffset = attachment_offset

    # Store semantic metadata as custom properties (software-agnostic connector contract)
    lcs.addProperty("App::PropertyString", "SemanticLabel", "Assembly")
    lcs.addProperty("App::PropertyString", "ReferenceObjectName", "Assembly")
    lcs.addProperty("App::PropertyString", "SourceFeaturesJson", "Assembly")
    lcs.addProperty("App::PropertyInteger", "ContractVersion", "Assembly")
    lcs.SemanticLabel = semantic_label
    lcs.ReferenceObjectName = obj.Name
    lcs.SourceFeaturesJson = json.dumps(source_features, sort_keys=True)
    lcs.ContractVersion = 2

    doc.recompute()
    doc.commitTransaction()
except Exception:
    doc.abortTransaction()
    raise

# Read current global placement after recompute for observation
global_pl = lcs.getGlobalPlacement()
global_rot = global_pl.Rotation
global_origin = global_pl.Base
global_primary = global_rot.multVec(FreeCAD.Vector(1, 0, 0))
global_secondary = global_rot.multVec(FreeCAD.Vector(0, 1, 0))
global_tertiary = global_rot.multVec(FreeCAD.Vector(0, 0, 1))

obj_global_pl = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement

contract = {{
    "name": lcs.Name,
    "reference_object": obj.Name,
    "semantic_label": semantic_label,
    "origin_local": arr(origin_local),
    "primary_axis_local": arr(primary_local),
    "secondary_axis_local": arr(secondary_local),
    "tertiary_axis_local": arr(tertiary_local),
    "origin_global": arr(global_origin),
    "primary_axis_global": arr(global_primary),
    "secondary_axis_global": arr(global_secondary),
    "tertiary_axis_global": arr(global_tertiary),
    "source_features": source_features,
    "contract_version": 2,
}}

observation = {{
    "document": doc.Name,
    "objects": [{{
        "name": obj.Name,
        "label": obj.Label,
        "global_position": arr(obj_global_pl.Base),
        "global_orientation_quat": [round(v, 9) for v in obj_global_pl.Rotation.Q],
        "connectors": [{{
            "name": lcs.Name,
            "semantic_label": semantic_label,
            "origin_global": arr(global_origin),
            "primary_axis_global": arr(global_primary),
        }}],
    }}],
    "relationships": [],
}}

_result_ = {{
    "name": lcs.Name,
    "label": lcs.Label,
    "contract": contract,
    "observation": observation,
}}
"""
        result = await bridge.execute_python(code)
        return _raise_if_failed(result, "Create connector failed")

    @mcp.tool()
    async def align_coordinate_systems(
        moving_object: str,
        moving_csys: str,
        fixed_object: str,
        fixed_csys: str,
        flip_primary: bool = False,
        preserve_offset: list[float] | None = None,
        debug: bool = False,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Align a moving object by matching two connector coordinate systems.

        Computes a rigid SE(3) transform so that the moving connector frame
        coincides with the fixed connector frame. The moving object's Placement
        is updated and all LCS connectors attached to it update automatically
        via FreeCAD recompute.

        Supports both new LCS-based connectors (Part::LocalCoordinateSystem)
        and legacy Part::Feature connectors for backward compatibility.

        Args:
            moving_object: Name of the part to move.
            moving_csys: Name of the connector on the moving part.
            fixed_object: Name of the fixed reference part.
            fixed_csys: Name of the connector on the fixed part.
            flip_primary: If True, align primary axes in the same direction.
                Default False aligns them antiparallel (standard mating faces).
            preserve_offset: [dx, dy, dz] offset along target frame axes applied
                after alignment (e.g. to set a gap).
            debug: If True, include resolved world frames in result.
            doc_name: Document name, defaults to active document.
        """
        bridge = await get_bridge()
        code = f"""
import json

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No document found")

moving = doc.getObject({moving_object!r})
fixed = doc.getObject({fixed_object!r})
mcs = doc.getObject({moving_csys!r})
fcs = doc.getObject({fixed_csys!r})
if moving is None:
    raise ValueError(f"Moving object not found: {moving_object!r}")
if fixed is None:
    raise ValueError(f"Fixed object not found: {fixed_object!r}")
if mcs is None:
    raise ValueError(f"Moving connector not found: {moving_csys!r}")
if fcs is None:
    raise ValueError(f"Fixed connector not found: {fixed_csys!r}")

def _is_lcs_connector(c):
    return c.TypeId == "Part::LocalCoordinateSystem" and hasattr(c, "SemanticLabel")

def _is_legacy_connector(c):
    return hasattr(c, "OriginLocal") and hasattr(c, "ReferenceObject")

for connector, label in [(mcs, "Moving connector"), (fcs, "Fixed connector")]:
    if not _is_lcs_connector(connector) and not _is_legacy_connector(connector):
        raise ValueError(
            f"{{label}} {{connector.Name}} is not a valid connector object. "
            f"Expected Part::LocalCoordinateSystem with SemanticLabel or "
            f"Part::Feature with OriginLocal."
        )

def arr(vec):
    return [round(vec.x, 6), round(vec.y, 6), round(vec.z, 6)]

def unit(vector):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.normalize()
    return result

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
    return result

def object_placement(o):
    return o.getGlobalPlacement() if hasattr(o, "getGlobalPlacement") else o.Placement

def local_point_to_world(o, point):
    return object_placement(o).multVec(point)

def local_axis_to_world(o, axis):
    return object_placement(o).Rotation.multVec(axis)

def basis_from_primary_tertiary(primary, tertiary):
    x = unit(primary)
    z = FreeCAD.Vector(tertiary.x, tertiary.y, tertiary.z) - scaled(x, tertiary.dot(x))
    if z.Length <= 1e-9:
        raise ValueError("Connector tertiary axis is parallel to primary axis")
    z.normalize()
    y = z.cross(x)
    y.normalize()
    return x, y, z

def resolve_connector_world_frame(o, connector):
    # Returns world-frame dict with keys: origin, x, y, z
    if _is_lcs_connector(connector):
        # LCS-based: getGlobalPlacement() directly encodes the connector frame.
        # lcs world placement = obj.getGlobalPlacement() * AttachmentOffset
        p = connector.getGlobalPlacement()
        primary = p.Rotation.multVec(FreeCAD.Vector(1, 0, 0))
        tertiary = p.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        x, y, z = basis_from_primary_tertiary(primary, tertiary)
        return {{"origin": p.Base, "x": x, "y": y, "z": z}}
    else:
        # Legacy Part::Feature connector with OriginLocal/PrimaryAxisLocal properties
        origin = local_point_to_world(o, FreeCAD.Vector(connector.OriginLocal))
        primary = unit(local_axis_to_world(o, FreeCAD.Vector(connector.PrimaryAxisLocal)))
        tertiary = unit(local_axis_to_world(o, FreeCAD.Vector(connector.TertiaryAxisLocal)))
        x, y, z = basis_from_primary_tertiary(primary, tertiary)
        return {{"origin": origin, "x": x, "y": y, "z": z}}

def matrix_from_frame(origin, x, y, z):
    m = FreeCAD.Matrix()
    m.A11, m.A21, m.A31 = x.x, x.y, x.z
    m.A12, m.A22, m.A32 = y.x, y.y, y.z
    m.A13, m.A23, m.A33 = z.x, z.y, z.z
    m.A14, m.A24, m.A34 = origin.x, origin.y, origin.z
    return m

def placement_info(o):
    placement = object_placement(o)
    return {{
        "base": arr(placement.Base),
        "rotation_euler": [round(v, 6) for v in placement.Rotation.toEuler()],
    }}

def connector_global_info(connector, ref_obj=None):
    if _is_lcs_connector(connector):
        p = connector.getGlobalPlacement()
        rot = p.Rotation
        return {{
            "name": connector.Name,
            "semantic_label": connector.SemanticLabel,
            "origin_global": arr(p.Base),
            "primary_axis_global": arr(rot.multVec(FreeCAD.Vector(1, 0, 0))),
        }}
    elif ref_obj is not None and _is_legacy_connector(connector):
        origin = local_point_to_world(ref_obj, FreeCAD.Vector(connector.OriginLocal))
        primary = unit(local_axis_to_world(ref_obj, FreeCAD.Vector(connector.PrimaryAxisLocal)))
        return {{
            "name": connector.Name,
            "semantic_label": getattr(connector, "SemanticRole", ""),
            "origin_global": arr(origin),
            "primary_axis_global": arr(primary),
        }}
    return {{"name": connector.Name}}

def describe_object_global(obj):
    global_pl = object_placement(obj)
    obj_connectors = []
    for candidate in doc.Objects:
        if _is_lcs_connector(candidate) and getattr(candidate, "ReferenceObjectName", None) == obj.Name:
            obj_connectors.append(connector_global_info(candidate))
        elif _is_legacy_connector(candidate) and getattr(candidate, "ReferenceObject", None) == obj.Name:
            obj_connectors.append(connector_global_info(candidate, obj))
    return {{
        "name": obj.Name,
        "label": obj.Label,
        "global_position": arr(global_pl.Base),
        "global_orientation_quat": [round(v, 9) for v in global_pl.Rotation.Q],
        "connectors": obj_connectors,
    }}

def relationship_payload(error):
    return {{
        "type": "aligned",
        "moving_object": moving.Name,
        "moving_connector": mcs.Name,
        "fixed_object": fixed.Name,
        "fixed_connector": fcs.Name,
        "flip_primary": {flip_primary},
        "alignment_error": error,
    }}

moving_frame = resolve_connector_world_frame(moving, mcs)
fixed_frame = resolve_connector_world_frame(fixed, fcs)

# Default (flip_primary=False): antiparallel primary axes = mating faces pointing toward each other
target_x = fixed_frame["x"] if {flip_primary} else fixed_frame["x"].negative()
target_z = fixed_frame["z"] - scaled(target_x, fixed_frame["z"].dot(target_x))
if target_z.Length <= 1e-9:
    raise ValueError("Target tertiary axis is parallel to target primary axis")
target_z.normalize()
target_y = target_z.cross(target_x)
target_y.normalize()
target_origin = FreeCAD.Vector(fixed_frame["origin"])
offset = {preserve_offset!r}
if offset is not None:
    target_origin = (
        target_origin
        + scaled(target_x, float(offset[0]))
        + scaled(target_y, float(offset[1]))
        + scaled(target_z, float(offset[2]))
    )

source_matrix = matrix_from_frame(moving_frame["origin"], moving_frame["x"], moving_frame["y"], moving_frame["z"])
target_matrix = matrix_from_frame(target_origin, target_x, target_y, target_z)
delta = FreeCAD.Placement(target_matrix).multiply(FreeCAD.Placement(source_matrix).inverse())

before = placement_info(moving)
doc.openTransaction("Align Connector Contracts")
try:
    moving.Placement = delta.multiply(moving.Placement)
    # Recompute updates all LCS connectors attached to moving automatically
    doc.recompute()
    doc.commitTransaction()
except Exception:
    doc.abortTransaction()
    raise

# After recompute, LCS connectors reflect updated placement
aligned_frame = resolve_connector_world_frame(moving, mcs)
origin_error = (aligned_frame["origin"] - target_origin).Length
primary_error = 1.0 - abs(aligned_frame["x"].dot(target_x))
secondary_error = 1.0 - abs(aligned_frame["y"].dot(target_y))
tertiary_error = 1.0 - abs(aligned_frame["z"].dot(target_z))
alignment_error = {{
    "origin": round(origin_error, 9),
    "primary_axis": round(primary_error, 9),
    "secondary_axis": round(secondary_error, 9),
    "tertiary_axis": round(tertiary_error, 9),
}}

relationship = relationship_payload(alignment_error)
relationship_name = f"AssemblyRelation_{{moving.Name}}_{{mcs.Name}}_to_{{fixed.Name}}_{{fcs.Name}}"
existing = doc.getObject(relationship_name)
doc.openTransaction("Record Assembly Relationship")
try:
    rel = existing or doc.addObject("App::FeaturePython", relationship_name)
    if not hasattr(rel, "RelationshipJson"):
        rel.addProperty("App::PropertyString", "RelationshipJson", "Assembly")
    rel.RelationshipJson = json.dumps(relationship, sort_keys=True)
    doc.recompute()
    doc.commitTransaction()
except Exception:
    doc.abortTransaction()
    raise

mat = delta.toMatrix()
observation = {{
    "document": doc.Name,
    "objects": [describe_object_global(moving), describe_object_global(fixed)],
    "relationships": [relationship],
}}
_result_ = {{
    "moving_object": moving.Name,
    "fixed_object": fixed.Name,
    "moving_connector": mcs.Name,
    "fixed_connector": fcs.Name,
    "before": before,
    "after": placement_info(moving),
    "transform_matrix": [
        [mat.A11, mat.A12, mat.A13, mat.A14],
        [mat.A21, mat.A22, mat.A23, mat.A24],
        [mat.A31, mat.A32, mat.A33, mat.A34],
        [0.0, 0.0, 0.0, 1.0],
    ],
    "alignment_error": alignment_error,
    "relationship": relationship,
    "observation": observation,
}}
if {debug}:
    _result_["resolved_frames"] = {{
        "moving_before": {{"origin": arr(moving_frame["origin"]), "primary": arr(moving_frame["x"]), "secondary": arr(moving_frame["y"]), "tertiary": arr(moving_frame["z"])}},
        "fixed_target": {{"origin": arr(target_origin), "primary": arr(target_x), "secondary": arr(target_y), "tertiary": arr(target_z)}},
        "moving_after": {{"origin": arr(aligned_frame["origin"]), "primary": arr(aligned_frame["x"]), "secondary": arr(aligned_frame["y"]), "tertiary": arr(aligned_frame["z"])}},
    }}
"""
        result = await bridge.execute_python(code)
        return _raise_if_failed(result, "Align coordinate systems failed")

    @mcp.tool()
    async def list_assembly_state(
        object_names: list[str] | None = None,
        include_local: bool = False,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """List assembly state as a geometric object list with global coordinates.

        Returns the current spatial state of parts and their connectors in world
        coordinates, suitable for reflective modeling (ToolCAD geometric object
        list pattern). Global coordinates are always returned; local coordinates
        are returned only when include_local=True (for debugging).

        Both new LCS-based connectors (Part::LocalCoordinateSystem) and legacy
        Part::Feature connectors are included for backward compatibility.

        Args:
            object_names: Filter to specific object names. Defaults to all objects
                that have connectors.
            include_local: Include local-frame coordinates for debugging.
                Default False keeps the output concise.
            doc_name: Document name, defaults to active document.
        """
        bridge = await get_bridge()
        code = f"""
import json

object_names = {object_names!r}
include_local = {include_local!r}
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No document found")

def arr(vec):
    return [round(vec.x, 6), round(vec.y, 6), round(vec.z, 6)]

def unit(vector):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.normalize()
    return result

def object_placement(o):
    return o.getGlobalPlacement() if hasattr(o, "getGlobalPlacement") else o.Placement

# Collect connectors grouped by reference object (both LCS and legacy)
lcs_by_object = {{}}
legacy_by_object = {{}}
warnings = []

for o in doc.Objects:
    if o.TypeId == "Part::LocalCoordinateSystem" and hasattr(o, "SemanticLabel"):
        ref = getattr(o, "ReferenceObjectName", "")
        if ref:
            if doc.getObject(ref) is None:
                warnings.append(f"LCS connector {{o.Name}} references missing object {{ref}}")
            lcs_by_object.setdefault(ref, []).append(o)
    elif hasattr(o, "OriginLocal") and hasattr(o, "ReferenceObject"):
        ref = o.ReferenceObject
        if ref:
            if doc.getObject(ref) is None:
                warnings.append(f"Connector {{o.Name}} references missing object {{ref}}")
            legacy_by_object.setdefault(ref, []).append(o)

# Determine which objects to include
selected = set(object_names or [])
if not selected:
    selected.update(lcs_by_object.keys())
    selected.update(legacy_by_object.keys())

def connector_info_lcs(lcs):
    # Build connector info from LCS using live global placement (on-query)
    global_pl = lcs.getGlobalPlacement()
    global_rot = global_pl.Rotation
    info = {{
        "name": lcs.Name,
        "semantic_label": lcs.SemanticLabel,
        "reference_object": lcs.ReferenceObjectName,
        "origin_global": arr(global_pl.Base),
        "primary_axis_global": arr(global_rot.multVec(FreeCAD.Vector(1, 0, 0))),
        "secondary_axis_global": arr(global_rot.multVec(FreeCAD.Vector(0, 1, 0))),
        "tertiary_axis_global": arr(global_rot.multVec(FreeCAD.Vector(0, 0, 1))),
        "source_features": json.loads(getattr(lcs, "SourceFeaturesJson", None) or "{{}}"),
        "contract_version": getattr(lcs, "ContractVersion", 2),
    }}
    if include_local:
        offset = lcs.AttachmentOffset
        offset_rot = offset.Rotation
        info["origin_local"] = arr(offset.Base)
        info["primary_axis_local"] = arr(offset_rot.multVec(FreeCAD.Vector(1, 0, 0)))
        info["secondary_axis_local"] = arr(offset_rot.multVec(FreeCAD.Vector(0, 1, 0)))
        info["tertiary_axis_local"] = arr(offset_rot.multVec(FreeCAD.Vector(0, 0, 1)))
    return info

def connector_info_legacy(connector, obj):
    # Build connector info from legacy Part::Feature connector
    pl = object_placement(obj)
    global_origin = pl.multVec(FreeCAD.Vector(connector.OriginLocal))
    global_primary = unit(pl.Rotation.multVec(FreeCAD.Vector(connector.PrimaryAxisLocal)))
    global_secondary = unit(pl.Rotation.multVec(FreeCAD.Vector(connector.SecondaryAxisLocal)))
    global_tertiary = unit(pl.Rotation.multVec(FreeCAD.Vector(connector.TertiaryAxisLocal)))
    info = {{
        "name": connector.Name,
        "semantic_label": getattr(connector, "SemanticRole", ""),
        "reference_object": connector.ReferenceObject,
        "origin_global": arr(global_origin),
        "primary_axis_global": arr(global_primary),
        "secondary_axis_global": arr(global_secondary),
        "tertiary_axis_global": arr(global_tertiary),
        "source_features": json.loads(getattr(connector, "SourceFeaturesJson", None) or "{{}}"),
        "contract_version": getattr(connector, "ContractVersion", 1),
    }}
    if include_local:
        info["origin_local"] = arr(FreeCAD.Vector(connector.OriginLocal))
        info["primary_axis_local"] = arr(FreeCAD.Vector(connector.PrimaryAxisLocal))
        info["secondary_axis_local"] = arr(FreeCAD.Vector(connector.SecondaryAxisLocal))
        info["tertiary_axis_local"] = arr(FreeCAD.Vector(connector.TertiaryAxisLocal))
    return info

objects = []
for name in sorted(selected):
    obj = doc.getObject(name)
    if obj is None:
        warnings.append(f"Assembly object not found: {{name}}")
        continue
    global_pl = object_placement(obj)
    connectors = []
    for lcs in lcs_by_object.get(name, []):
        connectors.append(connector_info_lcs(lcs))
    for legacy in legacy_by_object.get(name, []):
        connectors.append(connector_info_legacy(legacy, obj))
    objects.append({{
        "name": obj.Name,
        "label": obj.Label,
        "type_id": obj.TypeId,
        "global_position": arr(global_pl.Base),
        "global_orientation_quat": [round(v, 9) for v in global_pl.Rotation.Q],
        "connectors": connectors,
    }})

relationships = []
for o in doc.Objects:
    if hasattr(o, "RelationshipJson"):
        try:
            rel = json.loads(o.RelationshipJson or "{{}}")
        except Exception:
            warnings.append(f"Relationship {{o.Name}} has invalid JSON")
            continue
        if not object_names or rel.get("moving_object") in selected or rel.get("fixed_object") in selected:
            relationships.append(rel)

_result_ = {{
    "document": doc.Name,
    "objects": objects,
    "relationships": relationships,
    "warnings": warnings,
}}
"""
        result = await bridge.execute_python(code)
        return _raise_if_failed(result, "List assembly state failed")

    @mcp.tool()
    async def preview_or_highlight_references(
        object_name: str,
        faces: list[str] | None = None,
        edges: list[str] | None = None,
        points: list[list[float]] | None = None,
        axes: list[dict[str, Any]] | None = None,
        colors: dict[str, list[float]] | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Highlight faces/edges and create preview point/axis markers."""
        bridge = await get_bridge()
        code = f"""
import Part

faces = {faces!r} or []
edges = {edges!r} or []
points = {points!r} or []
axes = {axes!r} or []
colors = {colors!r} or {{}}

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No document found")
obj = doc.getObject({object_name!r})
if obj is None:
    raise ValueError(f"Object not found: {object_name!r}")
if not hasattr(obj, "Shape"):
    raise ValueError("Object has no shape")

def rgb_color(values, default=(0.8, 0.8, 0.8)):
    vals = list(values) if values else list(default)
    return (float(vals[0]), float(vals[1]), float(vals[2]))

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
    return result

created = []

doc.openTransaction("Preview Assembly References")
try:
    if faces and hasattr(obj, "ViewObject") and obj.ViewObject:
        if not FreeCAD.GuiUp:
            raise ValueError("GUI not available - face highlighting requires GUI mode")
        face_count = len(obj.Shape.Faces)
        highlight_color = rgb_color(colors.get("face", [1.0, 0.85, 0.0]))
        if "base" in colors:
            diffuse = [rgb_color(colors["base"])] * face_count
        else:
            existing = list(getattr(obj.ViewObject, "DiffuseColor", []) or [])
            if len(existing) == face_count:
                diffuse = [rgb_color(c) for c in existing]
            else:
                default = rgb_color(getattr(obj.ViewObject, "ShapeColor", None))
                diffuse = [default] * face_count
        for ref in faces:
            idx = int(str(ref).replace("Face", "")) - 1
            if idx < 0 or idx >= len(diffuse):
                raise ValueError(f"Invalid face reference: {{ref}}")
            diffuse[idx] = highlight_color
        obj.ViewObject.DiffuseColor = diffuse

    for idx, point in enumerate(points, start=1):
        p = FreeCAD.Vector(float(point[0]), float(point[1]), float(point[2]))
        marker = doc.addObject("Part::Feature", f"AssemblyPointPreview{{idx}}")
        marker.Shape = Part.makeSphere(2.0, p)
        created.append(marker.Name)

    for idx, axis in enumerate(axes, start=1):
        origin = FreeCAD.Vector(*axis["origin"])
        direction = FreeCAD.Vector(*axis["direction"])
        direction.normalize()
        length = float(axis.get("length", max(obj.Shape.BoundBox.DiagonalLength * 0.12, 10.0)))
        marker = doc.addObject("Part::Feature", f"AssemblyAxisPreview{{idx}}")
        marker.Shape = Part.makeLine(origin, origin + scaled(direction, length))
        created.append(marker.Name)

    if FreeCAD.GuiUp:
        FreeCADGui.Selection.clearSelection()
        for ref in faces + edges:
            FreeCADGui.Selection.addSelection(doc.Name, obj.Name, ref)

    doc.recompute()
    if FreeCAD.GuiUp:
        FreeCADGui.updateGui()
    doc.commitTransaction()
except Exception:
    doc.abortTransaction()
    raise

_result_ = {{
    "success": True,
    "object_name": obj.Name,
    "highlighted_faces": faces,
    "highlighted_edges": edges,
    "created_preview_objects": created,
}}
"""
        result = await bridge.execute_python(code)
        return _raise_if_failed(result, "Preview or highlight references failed")
