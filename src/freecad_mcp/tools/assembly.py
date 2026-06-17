"""Assembly connector-contract tools for deterministic FreeCAD operations.

The LLM supplies structured connector parameters. FreeCAD remains the
deterministic adapter that resolves current geometry and placements.
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

shape = obj.Shape
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

shape = obj.Shape
face_name = {face!r}
face_index = int(face_name.replace("Face", "")) - 1
if face_index < 0 or face_index >= len(shape.Faces):
    raise ValueError(f"Invalid face reference: {{face_name}}")
mount_face = shape.Faces[face_index]

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
bbox_center_axis = projected_face_axis(mount_face.CenterOfMass - shape.BoundBox.Center, normal)

holes_by_key = {{}}
hole_faces = []
for idx, f in enumerate(shape.Faces, start=1):
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
    async def create_local_coordinate_system(
        object_name: str,
        name: str,
        origin: list[float] | dict[str, Any],
        primary_axis: list[float] | dict[str, Any],
        tertiary_axis: list[float] | dict[str, Any] | None = None,
        reference_face: str | None = None,
        semantic_role: str = "connector",
        source_features: dict[str, Any] | None = None,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a local connector contract and derived marker.

        The persisted contract contains local frame fields only. The marker
        shape is resolved from the current object placement and is not truth.
        """
        bridge = await get_bridge()
        code = f"""
import json
import Part

doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No document found")
obj = doc.getObject({object_name!r})
if obj is None:
    raise ValueError(f"Object not found: {object_name!r}")
if not hasattr(obj, "Shape"):
    raise ValueError("Object has no shape")

shape = obj.Shape
origin_spec = {origin!r}
primary_spec = {primary_axis!r}
tertiary_spec = {tertiary_axis!r}
reference_face = {reference_face!r}
semantic_role = {semantic_role!r}
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

def object_placement(o):
    return o.getGlobalPlacement() if hasattr(o, "getGlobalPlacement") else o.Placement

def local_point_to_world(o, point):
    return object_placement(o).multVec(point)

def local_axis_to_world(o, axis):
    return object_placement(o).Rotation.multVec(axis)

def face_from_ref(ref):
    if not ref:
        return None
    idx = int(str(ref).replace("Face", "")) - 1
    if idx < 0 or idx >= len(shape.Faces):
        raise ValueError(f"Invalid face reference: {{ref}}")
    return shape.Faces[idx]

def normal_at(face):
    pr = face.ParameterRange
    result = face.normalAt((pr[0] + pr[1]) / 2, (pr[2] + pr[3]) / 2)
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

def hole_features_for_face(face):
    normal = normal_at(face)
    face_bb = face.BoundBox
    unique_by_key = {{}}
    for f in shape.Faces:
        surface_type = f.Surface.__class__.__name__
        if surface_type not in ["Cylinder", "Cone"]:
            continue
        axis = unit(f.Surface.Axis)
        if abs(axis.dot(normal)) <= 0.98:
            continue
        if not overlaps_face_region(f.BoundBox, face_bb, normal):
            continue
        projected = projected_axis_point(f.Surface, face, normal)
        canon_axis = canonical_axis(f.Surface.Axis)
        key = (
            round(projected.x, 3), round(projected.y, 3), round(projected.z, 3),
            round(canon_axis.x, 3), round(canon_axis.y, 3), round(canon_axis.z, 3),
        )
        unique_by_key.setdefault(key, {{
            "axis_point": projected,
            "axis": canon_axis,
        }})
    holes = list(unique_by_key.values())
    holes.sort(key=lambda h: (h["axis_point"].x, h["axis_point"].y, h["axis_point"].z))
    return holes

def projected_face_axis(vector, normal):
    axis = vector - scaled(normal, vector.dot(normal))
    if axis.Length <= 1e-9:
        return None
    axis.normalize()
    return axis

def face_orientation_reference(face):
    return projected_face_axis(face.CenterOfMass - shape.BoundBox.Center, normal_at(face))

def resolve_origin(spec):
    if isinstance(spec, (list, tuple)):
        return FreeCAD.Vector(float(spec[0]), float(spec[1]), float(spec[2]))
    kind = spec.get("type")
    face = face_from_ref(spec.get("face") or reference_face)
    if kind == "explicit":
        p = spec["point"]
        return FreeCAD.Vector(float(p[0]), float(p[1]), float(p[2]))
    if kind == "face_center":
        if face is None:
            raise ValueError("face_center origin requires a face")
        return face.CenterOfMass
    if kind == "bbox_center":
        return shape.BoundBox.Center
    if kind in ["hole_array_center", "hole_center"]:
        if face is None:
            raise ValueError(f"{{kind}} origin requires a face")
        holes = hole_features_for_face(face)
        if not holes:
            raise ValueError("No matching cylindrical hole axes found")
        if kind == "hole_center":
            return holes[int(spec.get("index", 0))]["axis_point"]
        return FreeCAD.Vector(
            sum(h["axis_point"].x for h in holes) / len(holes),
            sum(h["axis_point"].y for h in holes) / len(holes),
            sum(h["axis_point"].z for h in holes) / len(holes),
        )
    raise ValueError(f"Unsupported origin resolver: {{kind}}")

def resolve_axis(spec):
    if spec is None:
        return None
    if isinstance(spec, (list, tuple)):
        return unit(FreeCAD.Vector(float(spec[0]), float(spec[1]), float(spec[2])))
    kind = spec.get("type")
    face = face_from_ref(spec.get("face") or reference_face)
    if kind == "explicit":
        a = spec["vector"]
        return unit(FreeCAD.Vector(float(a[0]), float(a[1]), float(a[2])))
    if kind == "face_normal":
        if face is None:
            raise ValueError("face_normal axis requires a face")
        return normal_at(face)
    if kind == "hole_axis":
        if face is None:
            raise ValueError("hole_axis requires a face")
        holes = hole_features_for_face(face)
        if not holes:
            raise ValueError("No matching cylindrical hole axes found")
        return holes[int(spec.get("index", 0))]["axis"]
    if kind == "bbox_center_to_face_center":
        if face is None:
            raise ValueError("bbox_center_to_face_center axis requires a face")
        axis = face_orientation_reference(face)
        if axis is None:
            raise ValueError("bbox center to face center is parallel to primary axis")
        return axis
    raise ValueError(f"Unsupported axis resolver: {{kind}}")

def basis_from_primary_tertiary(primary, tertiary):
    primary = unit(primary)
    tertiary = tertiary - scaled(primary, primary.dot(tertiary))
    if tertiary.Length <= 1e-9:
        raise ValueError("Tertiary axis cannot be parallel to primary axis")
    tertiary.normalize()
    secondary = tertiary.cross(primary)
    secondary.normalize()
    return primary, secondary, tertiary

def connector_contract(connector):
    return {{
        "name": connector.Name,
        "reference_object": connector.ReferenceObject,
        "reference_face": connector.ReferenceFace,
        "origin_local": arr(connector.OriginLocal),
        "primary_axis_local": arr(connector.PrimaryAxisLocal),
        "secondary_axis_local": arr(connector.SecondaryAxisLocal),
        "tertiary_axis_local": arr(connector.TertiaryAxisLocal),
        "semantic_role": connector.SemanticRole,
        "source_features": json.loads(connector.SourceFeaturesJson or "{{}}"),
        "contract_version": connector.ContractVersion,
    }}

def placement_info(o):
    placement = object_placement(o)
    return {{
        "base": arr(placement.Base),
        "rotation_euler": [round(v, 6) for v in placement.Rotation.toEuler()],
    }}

def describe_assembly_object(o):
    connectors = []
    for candidate in doc.Objects:
        if getattr(candidate, "ReferenceObject", None) == o.Name and hasattr(candidate, "OriginLocal"):
            connectors.append(connector_contract(candidate))
    return {{
        "name": o.Name,
        "label": o.Label,
        "type_id": o.TypeId,
        "placement": placement_info(o),
        "connectors": connectors,
    }}

def build_observation(touched):
    return {{
        "document": doc.Name,
        "objects": [describe_assembly_object(o) for o in touched],
        "relationships": [],
        "warnings": [],
    }}

origin_local = resolve_origin(origin_spec)
primary_local = resolve_axis(primary_spec)
if primary_local is None or primary_local.Length <= 1e-9:
    raise ValueError("Primary axis must be non-zero")

tertiary_local = resolve_axis(tertiary_spec)
default_tertiary_face_ref = reference_face
if default_tertiary_face_ref is None and isinstance(primary_spec, dict):
    default_tertiary_face_ref = primary_spec.get("face")
if tertiary_local is None and default_tertiary_face_ref is not None:
    default_tertiary_face = face_from_ref(default_tertiary_face_ref)
    tertiary_local = face_orientation_reference(default_tertiary_face)
if tertiary_local is None:
    trial = FreeCAD.Vector(0, 0, 1)
    if abs(primary_local.dot(trial)) > 0.95:
        trial = FreeCAD.Vector(0, 1, 0)
    tertiary_local = projected_face_axis(trial, primary_local)
primary_local, secondary_local, tertiary_local = basis_from_primary_tertiary(primary_local, tertiary_local)

axis_len = max(shape.BoundBox.DiagonalLength * 0.08, 10.0)
marker_origin = local_point_to_world(obj, origin_local)
marker_primary = unit(local_axis_to_world(obj, primary_local))
marker_secondary = unit(local_axis_to_world(obj, secondary_local))
marker_tertiary = unit(local_axis_to_world(obj, tertiary_local))
axes_shape = Part.makeCompound([
    Part.makeLine(marker_origin, marker_origin + scaled(marker_primary, axis_len)),
    Part.makeLine(marker_origin, marker_origin + scaled(marker_secondary, axis_len * 0.8)),
    Part.makeLine(marker_origin, marker_origin + scaled(marker_tertiary, axis_len * 0.6)),
])

doc.openTransaction("Create Connector Contract")
try:
    connector = doc.addObject("Part::Feature", {name!r})
    connector.Shape = axes_shape
    connector.addProperty("App::PropertyString", "ReferenceObject", "Assembly")
    connector.addProperty("App::PropertyString", "ReferenceFace", "Assembly")
    connector.addProperty("App::PropertyVector", "OriginLocal", "Assembly")
    connector.addProperty("App::PropertyVector", "PrimaryAxisLocal", "Assembly")
    connector.addProperty("App::PropertyVector", "SecondaryAxisLocal", "Assembly")
    connector.addProperty("App::PropertyVector", "TertiaryAxisLocal", "Assembly")
    connector.addProperty("App::PropertyString", "SemanticRole", "Assembly")
    connector.addProperty("App::PropertyString", "SourceFeaturesJson", "Assembly")
    connector.addProperty("App::PropertyInteger", "ContractVersion", "Assembly")
    connector.ReferenceObject = obj.Name
    connector.ReferenceFace = reference_face or ""
    connector.OriginLocal = origin_local
    connector.PrimaryAxisLocal = primary_local
    connector.SecondaryAxisLocal = secondary_local
    connector.TertiaryAxisLocal = tertiary_local
    connector.SemanticRole = semantic_role
    connector.SourceFeaturesJson = json.dumps(source_features, sort_keys=True)
    connector.ContractVersion = 1
    doc.recompute()
    doc.commitTransaction()
except Exception:
    doc.abortTransaction()
    raise

contract = connector_contract(connector)
_result_ = {{
    "name": connector.Name,
    "label": connector.Label,
    "contract": contract,
    "observation": build_observation([obj]),
}}
"""
        result = await bridge.execute_python(code)
        return _raise_if_failed(result, "Create local coordinate system failed")

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
        """Align a moving object by matching two local connector contracts."""
        bridge = await get_bridge()
        code = f"""
import json
import Part

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
for connector in [mcs, fcs]:
    if not hasattr(connector, "OriginLocal"):
        raise ValueError(f"Connector {{connector.Name}} is missing local contract fields")

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

def placement_info(o):
    placement = object_placement(o)
    return {{
        "base": arr(placement.Base),
        "rotation_euler": [round(v, 6) for v in placement.Rotation.toEuler()],
    }}

def basis_from_primary_tertiary(primary, tertiary):
    x = unit(primary)
    z = FreeCAD.Vector(tertiary) - scaled(x, FreeCAD.Vector(tertiary).dot(x))
    if z.Length <= 1e-9:
        raise ValueError("Connector tertiary axis is parallel to primary axis")
    z.normalize()
    y = z.cross(x)
    y.normalize()
    return x, y, z

def matrix_from_frame(origin, x, y, z):
    m = FreeCAD.Matrix()
    m.A11, m.A21, m.A31 = x.x, x.y, x.z
    m.A12, m.A22, m.A32 = y.x, y.y, y.z
    m.A13, m.A23, m.A33 = z.x, z.y, z.z
    m.A14, m.A24, m.A34 = origin.x, origin.y, origin.z
    return m

def connector_contract(connector):
    return {{
        "name": connector.Name,
        "reference_object": connector.ReferenceObject,
        "reference_face": connector.ReferenceFace,
        "origin_local": arr(connector.OriginLocal),
        "primary_axis_local": arr(connector.PrimaryAxisLocal),
        "secondary_axis_local": arr(connector.SecondaryAxisLocal),
        "tertiary_axis_local": arr(connector.TertiaryAxisLocal),
        "semantic_role": connector.SemanticRole,
        "source_features": json.loads(connector.SourceFeaturesJson or "{{}}"),
        "contract_version": connector.ContractVersion,
    }}

def resolve_connector_world_frame(o, connector):
    origin = local_point_to_world(o, FreeCAD.Vector(connector.OriginLocal))
    primary = unit(local_axis_to_world(o, FreeCAD.Vector(connector.PrimaryAxisLocal)))
    tertiary = unit(local_axis_to_world(o, FreeCAD.Vector(connector.TertiaryAxisLocal)))
    x, y, z = basis_from_primary_tertiary(primary, tertiary)
    return {{"origin": origin, "x": x, "y": y, "z": z}}

def refresh_marker(o, connector):
    if not hasattr(o, "Shape"):
        return
    frame = resolve_connector_world_frame(o, connector)
    length = max(o.Shape.BoundBox.DiagonalLength * 0.08, 10.0)
    connector.Shape = Part.makeCompound([
        Part.makeLine(frame["origin"], frame["origin"] + scaled(frame["x"], length)),
        Part.makeLine(frame["origin"], frame["origin"] + scaled(frame["y"], length * 0.8)),
        Part.makeLine(frame["origin"], frame["origin"] + scaled(frame["z"], length * 0.6)),
    ])

def describe_assembly_object(o):
    connectors = []
    for candidate in doc.Objects:
        if getattr(candidate, "ReferenceObject", None) == o.Name and hasattr(candidate, "OriginLocal"):
            connectors.append(connector_contract(candidate))
    return {{
        "name": o.Name,
        "label": o.Label,
        "type_id": o.TypeId,
        "placement": placement_info(o),
        "connectors": connectors,
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
    doc.recompute()
    refresh_marker(moving, mcs)
    refresh_marker(fixed, fcs)
    doc.recompute()
    doc.commitTransaction()
except Exception:
    doc.abortTransaction()
    raise

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
    "objects": [describe_assembly_object(moving), describe_assembly_object(fixed)],
    "relationships": [relationship],
    "warnings": [],
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
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """List assembly objects, local connector contracts, and relationships."""
        bridge = await get_bridge()
        code = f"""
import json

object_names = {object_names!r}
doc = FreeCAD.ActiveDocument if {doc_name!r} is None else FreeCAD.getDocument({doc_name!r})
if doc is None:
    raise ValueError("No document found")

def arr(vec):
    return [round(vec.x, 6), round(vec.y, 6), round(vec.z, 6)]

def object_placement(o):
    return o.getGlobalPlacement() if hasattr(o, "getGlobalPlacement") else o.Placement

def placement_info(o):
    placement = object_placement(o)
    return {{
        "base": arr(placement.Base),
        "rotation_euler": [round(v, 6) for v in placement.Rotation.toEuler()],
    }}

def connector_contract(connector):
    return {{
        "name": connector.Name,
        "reference_object": connector.ReferenceObject,
        "reference_face": connector.ReferenceFace,
        "origin_local": arr(connector.OriginLocal),
        "primary_axis_local": arr(connector.PrimaryAxisLocal),
        "secondary_axis_local": arr(connector.SecondaryAxisLocal),
        "tertiary_axis_local": arr(connector.TertiaryAxisLocal),
        "semantic_role": connector.SemanticRole,
        "source_features": json.loads(connector.SourceFeaturesJson or "{{}}"),
        "contract_version": connector.ContractVersion,
    }}

selected = set(object_names or [])
connectors_by_object = {{}}
warnings = []
for candidate in doc.Objects:
    if hasattr(candidate, "OriginLocal") and hasattr(candidate, "ReferenceObject"):
        ref = candidate.ReferenceObject
        if doc.getObject(ref) is None:
            warnings.append(f"Connector {{candidate.Name}} references missing object {{ref}}")
        connectors_by_object.setdefault(ref, []).append(candidate)
        if not selected and ref:
            selected.add(ref)

objects = []
for name in sorted(selected):
    obj = doc.getObject(name)
    if obj is None:
        warnings.append(f"Assembly object not found: {{name}}")
        continue
    objects.append({{
        "name": obj.Name,
        "label": obj.Label,
        "type_id": obj.TypeId,
        "placement": placement_info(obj),
        "connectors": [connector_contract(c) for c in connectors_by_object.get(obj.Name, [])],
    }})

relationships = []
for candidate in doc.Objects:
    if hasattr(candidate, "RelationshipJson"):
        try:
            rel = json.loads(candidate.RelationshipJson or "{{}}")
        except Exception:
            warnings.append(f"Relationship {{candidate.Name}} has invalid JSON")
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

def rgba(values, default_alpha=0.0):
    vals = list(values)
    if len(vals) == 3:
        vals.append(default_alpha)
    return tuple(vals[:4])

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
    return result

face_color = rgba(colors.get("face", [1.0, 0.85, 0.0]), 0.0)
created = []

doc.openTransaction("Preview Assembly References")
try:
    if hasattr(obj, "ViewObject"):
        face_count = len(obj.Shape.Faces)
        if "base" in colors:
            diffuse = [rgba(colors["base"], 0.0)] * face_count
        else:
            existing = list(getattr(obj.ViewObject, "DiffuseColor", []) or [])
            if len(existing) == face_count:
                diffuse = [rgba(color, 0.0) for color in existing]
            else:
                shape_color = getattr(obj.ViewObject, "ShapeColor", (0.8, 0.8, 0.8, 0.0))
                diffuse = [rgba(shape_color, 0.0)] * face_count
        for ref in faces:
            idx = int(str(ref).replace("Face", "")) - 1
            if idx < 0 or idx >= len(diffuse):
                raise ValueError(f"Invalid face reference: {{ref}}")
            diffuse[idx] = face_color
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
