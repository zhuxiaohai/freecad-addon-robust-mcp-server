"""Assembly semantics tools for deterministic FreeCAD operations.

These tools expose structured geometry queries and rigid alignment operations.
They intentionally avoid natural language parsing; callers translate intent into
explicit constraints and references before invoking these tools.
"""

from collections.abc import Awaitable, Callable
from typing import Any


def _raise_if_failed(result: Any, message: str) -> dict[str, Any]:
    """Return the bridge result payload or raise a useful error."""
    if result.success:
        return result.result
    raise ValueError(result.error_traceback or message)


def register_assembly_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register assembly-oriented tools with the Robust MCP Server.

    Args:
        mcp: The FastMCP instance.
        get_bridge: Async function to get the active bridge.
    """

    # Intentionally not exposed yet: the unfiltered topology payload can be very
    # large on real parts. Prefer find_faces_by_constraints as the first pass.
    # @mcp.tool()
    async def analyze_shape_topology(
        object_name: str,
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Analyze a shape for deterministic assembly reasoning.

        Args:
            object_name: Name of the object to inspect.
            doc_name: Document containing the object. Uses active document if None.

        Returns:
            Geometry summary including faces, cylinders, planar faces, edges,
            bounding box, and face adjacency.
        """
        bridge = await get_bridge()
        code = f"""
import math

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
tol = 1e-6

def vec(v):
    return [round(v.x, 6), round(v.y, 6), round(v.z, 6)]

def bbox(b):
    return [
        round(b.XMin, 6), round(b.YMin, 6), round(b.ZMin, 6),
        round(b.XMax, 6), round(b.YMax, 6), round(b.ZMax, 6),
    ]

def normal_at(face):
    try:
        pr = face.ParameterRange
        return face.normalAt((pr[0] + pr[1]) / 2, (pr[2] + pr[3]) / 2)
    except Exception:
        return None

def unit(vector):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.normalize()
    return result

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
    return result

def is_external(face, normal):
    if normal is None or not shape.Solids:
        return None
    try:
        probe = face.CenterOfMass + scaled(unit(normal), 0.05)
        return not shape.isInside(probe, 1e-5, True)
    except Exception:
        return None

def face_adjacency():
    adjacent = {{i: set() for i in range(1, len(shape.Faces) + 1)}}
    for i, f1 in enumerate(shape.Faces, start=1):
        for j in range(i + 1, len(shape.Faces) + 1):
            f2 = shape.Faces[j - 1]
            shared = False
            for e1 in f1.Edges:
                for e2 in f2.Edges:
                    if e1.isSame(e2):
                        shared = True
                        break
                if shared:
                    break
            if shared:
                adjacent[i].add(j)
                adjacent[j].add(i)
    return {{f"Face{{k}}": [f"Face{{v}}" for v in sorted(vals)] for k, vals in adjacent.items()}}

faces = []
planes = []
cylinders = []
for idx, face in enumerate(shape.Faces, start=1):
    surface = face.Surface
    surface_type = surface.__class__.__name__
    center = face.CenterOfMass
    fb = face.BoundBox
    normal = normal_at(face)
    item = {{
        "face": f"Face{{idx}}",
        "index": idx,
        "surface_type": surface_type,
        "area": face.Area,
        "center": vec(center),
        "bbox": bbox(fb),
        "bbox_size": [round(fb.XLength, 6), round(fb.YLength, 6), round(fb.ZLength, 6)],
        "normal": vec(normal) if normal is not None else None,
        "is_external": is_external(face, normal),
        "edge_count": len(face.Edges),
    }}
    if surface_type == "Cylinder":
        item.update({{
            "radius": surface.Radius,
            "axis": vec(surface.Axis),
            "axis_point": vec(surface.Center),
        }})
        cylinders.append(item)
    if surface_type == "Plane":
        planes.append(item)
    faces.append(item)

edges = []
for idx, edge in enumerate(shape.Edges, start=1):
    eb = edge.BoundBox
    curve_type = edge.Curve.__class__.__name__ if hasattr(edge, "Curve") else "Unknown"
    edges.append({{
        "edge": f"Edge{{idx}}",
        "index": idx,
        "curve_type": curve_type,
        "length": edge.Length,
        "center": vec(edge.CenterOfMass),
        "bbox": bbox(eb),
    }})

_result_ = {{
    "object_name": obj.Name,
    "label": obj.Label,
    "shape_type": shape.ShapeType,
    "is_valid": shape.isValid(),
    "is_closed": shape.isClosed(),
    "volume": shape.Volume if hasattr(shape, "Volume") else None,
    "area": shape.Area,
    "bbox": bbox(bb),
    "bbox_size": [bb.XLength, bb.YLength, bb.ZLength],
    "center_of_mass": vec(shape.CenterOfMass),
    "counts": {{
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "vertices": len(shape.Vertexes),
        "planes": len(planes),
        "cylinders": len(cylinders),
    }},
    "faces": faces,
    "planes": planes,
    "cylinders": cylinders,
    "edges": edges,
    "adjacency": face_adjacency(),
}}
"""
        result = await bridge.execute_python(code)
        return _raise_if_failed(result, "Analyze shape topology failed")

    @mcp.tool()
    async def find_faces_by_constraints(
        object_name: str,
        constraints: dict[str, Any],
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Find faces matching structured geometric constraints.

        Args:
            object_name: Name of the object to inspect.
            constraints: Structured constraints. Supported keys include:
                surface_type, normal_parallel_to, normal_same_direction_to,
                bbox_side, min_area, external_only, region_hint, contains_hole_axes,
                min_hole_count, max_hole_count, min_hole_radius, and max_hole_radius.
                Hole-radius bounds use each hole's largest cylindrical radius and
                combine with AND when both are set. Omit a bound to leave it open.
            doc_name: Document containing the object. Uses active document if None.

        Returns:
            Ranked candidate faces with scores and deterministic reasons.
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

def bbox_values(b):
    return [b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax]

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

def hole_key(surface, face, normal):
    center = projected_axis_point(surface, face, normal)
    axis = canonical_axis(surface.Axis)
    return (
        round(center.x, 3), round(center.y, 3), round(center.z, 3),
        round(axis.x, 3), round(axis.y, 3), round(axis.z, 3),
    )

def overlaps_face_region(surface_bbox, face_bbox, normal):
    # Ignore the axis normal to the mounting face. Hole cylinders/cones often
    # start behind a chamfer and do not touch the mounting face bbox in that axis.
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
        obb = other.BoundBox
        if overlaps_face_region(obb, face_bb, normal):
            face_name = f"Face{{cidx}}"
            matched_faces.append(face_name)
            key = hole_key(other.Surface, face, normal)
            projected = projected_axis_point(other.Surface, face, normal)
            hole = holes_by_key.setdefault(key, {{
                "axis_point": arr(projected),
                "axis": arr(canonical_axis(other.Surface.Axis)),
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
    holes.sort(key=lambda h: (h["axis_point"][0], h["axis_point"][1], h["axis_point"][2]))
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
        "center": arr(face.CenterOfMass),
        "normal": arr(normal) if normal is not None else None,
        "bbox": bbox_values(face.BoundBox),
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
        """Extract deterministic mounting features from a face.

        Args:
            object_name: Name of the object to inspect.
            face: Face reference such as "Face19".
            doc_name: Document containing the object. Uses active document if None.

        Returns:
            Face frame candidates, hole features, hole-array center, and useful axes.
        """
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

def normal_at(f):
    pr = f.ParameterRange
    result = f.normalAt((pr[0] + pr[1]) / 2, (pr[2] + pr[3]) / 2)
    result.normalize()
    return result

def unit(vector):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.normalize()
    return result

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
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

def hole_key(surface, face, normal):
    center = projected_axis_point(surface, face, normal)
    axis = canonical_axis(surface.Axis)
    return (
        round(center.x, 3), round(center.y, 3), round(center.z, 3),
        round(axis.x, 3), round(axis.y, 3), round(axis.z, 3),
    )

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

normal = normal_at(mount_face)
face_bb = mount_face.BoundBox

def projected_face_axis(vector, normal):
    axis = vector - scaled(normal, vector.dot(normal))
    if axis.Length <= 1e-9:
        return None
    axis.normalize()
    return axis

bbox_center_axis = projected_face_axis(mount_face.CenterOfMass - shape.BoundBox.Center, normal)
holes = []
for idx, f in enumerate(shape.Faces, start=1):
    surface_type = f.Surface.__class__.__name__
    if surface_type not in ["Cylinder", "Cone"]:
        continue
    axis = FreeCAD.Vector(f.Surface.Axis.x, f.Surface.Axis.y, f.Surface.Axis.z)
    axis.normalize()
    if abs(axis.dot(normal)) < 0.98:
        continue
    b = f.BoundBox
    if overlaps_face_region(b, face_bb, normal):
        canon_axis = canonical_axis(f.Surface.Axis)
        projected = projected_axis_point(f.Surface, mount_face, normal)
        holes.append({{
            "face": f"Face{{idx}}",
            "surface_type": surface_type,
            "radius": getattr(f.Surface, "Radius", 0.0),
            "axis": arr(axis),
            "canonical_axis": arr(canon_axis),
            "axis_point": arr(projected),
            "surface_axis_point": arr(f.Surface.Center),
            "center": arr(f.CenterOfMass),
            "bbox": [b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax],
        }})

unique_by_key = {{}}
for hole_face in holes:
    # Re-key by the already projected point and canonical axis from the face item.
    key = (
        round(hole_face["axis_point"][0], 3),
        round(hole_face["axis_point"][1], 3),
        round(hole_face["axis_point"][2], 3),
        round(hole_face["canonical_axis"][0], 3),
        round(hole_face["canonical_axis"][1], 3),
        round(hole_face["canonical_axis"][2], 3),
    )
    unique = unique_by_key.setdefault(key, {{
        "axis_point": hole_face["axis_point"],
        "axis": hole_face["canonical_axis"],
        "surface_types": [],
        "radii": [],
        "faces": [],
    }})
    unique["faces"].append(hole_face["face"])
    if hole_face["surface_type"] not in unique["surface_types"]:
        unique["surface_types"].append(hole_face["surface_type"])
    radius = round(hole_face["radius"], 6)
    if radius and radius not in unique["radii"]:
        unique["radii"].append(radius)
unique_holes = list(unique_by_key.values())
unique_holes.sort(key=lambda h: (h["axis_point"][0], h["axis_point"][1], h["axis_point"][2]))
for unique in unique_holes:
    unique["radii"].sort()

hole_array_center = None
if unique_holes:
    sx = sum(h["axis_point"][0] for h in unique_holes) / len(unique_holes)
    sy = sum(h["axis_point"][1] for h in unique_holes) / len(unique_holes)
    sz = sum(h["axis_point"][2] for h in unique_holes) / len(unique_holes)
    hole_array_center = [round(sx, 6), round(sy, 6), round(sz, 6)]

_result_ = {{
    "object_name": obj.Name,
    "face": face_name,
    "surface_type": mount_face.Surface.__class__.__name__,
    "face_center": arr(mount_face.CenterOfMass),
    "face_normal": arr(normal),
    "face_bbox": [face_bb.XMin, face_bb.YMin, face_bb.ZMin, face_bb.XMax, face_bb.YMax, face_bb.ZMax],
    "holes": holes,
    "cylindrical_face_count": len(holes),
    "unique_holes": unique_holes,
    "unique_hole_count": len(unique_holes),
    "hole_count": len(unique_holes),
    "hole_array_center": hole_array_center,
    "primary_axis_candidates": {{
        "face_normal": arr(normal),
    }},
    "tertiary_axis_candidates": {{
        "bbox_center_to_face_center": arr(bbox_center_axis) if bbox_center_axis is not None else None,
    }},
    "origin_candidates": {{
        "face_center": arr(mount_face.CenterOfMass),
        "hole_array_center": hole_array_center,
    }},
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
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a deterministic local coordinate system marker.

        Args:
            object_name: Reference object name.
            name: Name for the coordinate system marker object.
            origin: Explicit point or resolver dict.
            primary_axis: Explicit vector or resolver dict for local X.
            tertiary_axis: Optional explicit vector or resolver dict for local Z.
                Local Y is computed automatically as Z cross X.
            reference_face: Optional face reference for face-based resolvers.
            doc_name: Document containing the object. Uses active document if None.

        Returns:
            Created coordinate system marker and resolved axes.
        """
        bridge = await get_bridge()
        code = f"""
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

def arr(vec):
    return [round(vec.x, 6), round(vec.y, 6), round(vec.z, 6)]

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

def unit(vector):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.normalize()
    return result

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
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

origin_vec = resolve_origin(origin_spec)
primary = resolve_axis(primary_spec)
if primary is None or primary.Length <= 1e-9:
    raise ValueError("Primary axis must be non-zero")

tertiary = resolve_axis(tertiary_spec)
default_tertiary_face_ref = reference_face
if default_tertiary_face_ref is None and isinstance(primary_spec, dict):
    default_tertiary_face_ref = primary_spec.get("face")
if tertiary is None and default_tertiary_face_ref is not None:
    default_tertiary_face = face_from_ref(default_tertiary_face_ref)
    tertiary = face_orientation_reference(default_tertiary_face)
underconstrained = tertiary is None
if tertiary is not None:
    tertiary = tertiary - scaled(primary, primary.dot(tertiary))
    tertiary.normalize()
    if tertiary.Length <= 1e-9:
        raise ValueError("Tertiary axis cannot be parallel to primary axis")
    secondary = tertiary.cross(primary)
    secondary.normalize()
else:
    trial = FreeCAD.Vector(0, 0, 1)
    if abs(primary.dot(trial)) > 0.95:
        trial = FreeCAD.Vector(0, 1, 0)
    secondary = trial - scaled(primary, primary.dot(trial))
    secondary.normalize()

if tertiary is None:
    tertiary = primary.cross(secondary)
    tertiary.normalize()
axis_len = max(shape.BoundBox.DiagonalLength * 0.08, 10.0)
axes_shape = Part.makeCompound([
    Part.makeLine(origin_vec, origin_vec + scaled(primary, axis_len)),
    Part.makeLine(origin_vec, origin_vec + scaled(secondary, axis_len * 0.8)),
    Part.makeLine(origin_vec, origin_vec + scaled(tertiary, axis_len * 0.6)),
])

doc.openTransaction("Create Local Coordinate System")
try:
    csys = doc.addObject("Part::Feature", {name!r})
    csys.Shape = axes_shape
    csys.addProperty("App::PropertyString", "ReferenceObject", "Assembly")
    csys.addProperty("App::PropertyString", "ReferenceFace", "Assembly")
    csys.addProperty("App::PropertyVector", "Origin", "Assembly")
    csys.addProperty("App::PropertyVector", "PrimaryAxis", "Assembly")
    csys.addProperty("App::PropertyVector", "SecondaryAxis", "Assembly")
    csys.addProperty("App::PropertyVector", "TertiaryAxis", "Assembly")
    csys.addProperty("App::PropertyBool", "RotationUnderconstrained", "Assembly")
    csys.ReferenceObject = obj.Name
    csys.ReferenceFace = reference_face or ""
    csys.Origin = origin_vec
    csys.PrimaryAxis = primary
    csys.SecondaryAxis = secondary
    csys.TertiaryAxis = tertiary
    csys.RotationUnderconstrained = underconstrained
    doc.recompute()
    doc.commitTransaction()
except Exception:
    doc.abortTransaction()
    raise

_result_ = {{
    "name": csys.Name,
    "label": csys.Label,
    "reference_object": obj.Name,
    "reference_face": reference_face,
    "origin": arr(origin_vec),
    "primary_axis": arr(primary),
    "tertiary_axis": arr(tertiary),
    "secondary_axis": arr(secondary),
    "rotation_underconstrained": underconstrained,
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
        doc_name: str | None = None,
    ) -> dict[str, Any]:
        """Align a moving object by matching two local coordinate systems.

        Args:
            moving_object: Object to transform.
            moving_csys: Coordinate system attached to the moving object.
            fixed_object: Fixed reference object name.
            fixed_csys: Coordinate system attached to the fixed object.
            flip_primary: If False, align moving primary to opposite fixed primary.
                If True, align moving primary to the same fixed primary direction.
            preserve_offset: Optional offset applied in target coordinate axes.
            doc_name: Document containing the objects. Uses active document if None.

        Returns:
            Placement before/after, transform matrix, and residual alignment error.
        """
        bridge = await get_bridge()
        code = f"""
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
    raise ValueError(f"Moving coordinate system not found: {moving_csys!r}")
if fcs is None:
    raise ValueError(f"Fixed coordinate system not found: {fixed_csys!r}")

def arr(vec):
    return [round(vec.x, 6), round(vec.y, 6), round(vec.z, 6)]

def placement_info(obj):
    return {{
        "base": arr(obj.Placement.Base),
        "rotation": list(obj.Placement.Rotation.toEuler()),
    }}

def unit(vector):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.normalize()
    return result

def scaled(vector, factor):
    result = FreeCAD.Vector(vector.x, vector.y, vector.z)
    result.multiply(factor)
    return result

def basis_from_xz(primary, tertiary):
    x = unit(primary)
    z_raw = FreeCAD.Vector(tertiary)
    z = z_raw - scaled(x, z_raw.dot(x))
    if z.Length <= 1e-9:
        raise ValueError("Coordinate system tertiary axis is parallel to primary")
    z.normalize()
    y = z.cross(x)
    y.normalize()
    return x, y, z

def basis(csys):
    if not hasattr(csys, "TertiaryAxis"):
        raise ValueError("Coordinate system missing TertiaryAxis")
    return basis_from_xz(csys.PrimaryAxis, csys.TertiaryAxis)

def matrix_from_frame(origin, x, y, z):
    m = FreeCAD.Matrix()
    m.A11, m.A21, m.A31 = x.x, x.y, x.z
    m.A12, m.A22, m.A32 = y.x, y.y, y.z
    m.A13, m.A23, m.A33 = z.x, z.y, z.z
    m.A14, m.A24, m.A34 = origin.x, origin.y, origin.z
    return m

mx, my, mz = basis(mcs)
fx, fy, fz = basis(fcs)
target_x = fx if {flip_primary} else fx.negative()
target_z = fz
target_z = target_z - scaled(target_x, target_z.dot(target_x))
if target_z.Length <= 1e-9:
    raise ValueError("Target tertiary axis is parallel to target primary axis")
target_z.normalize()
target_y = target_z.cross(target_x)
target_y.normalize()
target_origin = FreeCAD.Vector(fcs.Origin)
offset = {preserve_offset!r}
if offset is not None:
    target_origin = (
        target_origin
        + scaled(target_x, float(offset[0]))
        + scaled(target_y, float(offset[1]))
        + scaled(target_z, float(offset[2]))
    )

moving_frame = matrix_from_frame(FreeCAD.Vector(mcs.Origin), mx, my, mz)
target_frame = matrix_from_frame(target_origin, target_x, target_y, target_z)
delta = FreeCAD.Placement(target_frame).multiply(FreeCAD.Placement(moving_frame).inverse())

before = placement_info(moving)
doc.openTransaction("Align Coordinate Systems")
try:
    moving.Placement = delta.multiply(moving.Placement)
    mcs.Placement = delta.multiply(mcs.Placement)
    mcs.Origin = delta.multVec(FreeCAD.Vector(mcs.Origin))
    mcs.PrimaryAxis = unit(delta.Rotation.multVec(FreeCAD.Vector(mcs.PrimaryAxis)))
    mcs.SecondaryAxis = unit(delta.Rotation.multVec(FreeCAD.Vector(mcs.SecondaryAxis)))
    if hasattr(mcs, "TertiaryAxis"):
        mcs.TertiaryAxis = unit(delta.Rotation.multVec(FreeCAD.Vector(mcs.TertiaryAxis)))
    doc.recompute()
    doc.commitTransaction()
except Exception:
    doc.abortTransaction()
    raise

after = placement_info(moving)
origin_error = (FreeCAD.Vector(mcs.Origin) - target_origin).Length
primary_error = 1.0 - abs(unit(FreeCAD.Vector(mcs.PrimaryAxis)).dot(target_x))
secondary_error = 1.0 - abs(unit(FreeCAD.Vector(mcs.SecondaryAxis)).dot(target_y))
if hasattr(mcs, "TertiaryAxis"):
    tertiary_error = 1.0 - abs(unit(FreeCAD.Vector(mcs.TertiaryAxis)).dot(target_z))
mat = delta.toMatrix()
_result_ = {{
    "moving_object": moving.Name,
    "fixed_object": fixed.Name,
    "moving_csys": mcs.Name,
    "fixed_csys": fcs.Name,
    "before": before,
    "after": after,
    "transform_matrix": [
        [mat.A11, mat.A12, mat.A13, mat.A14],
        [mat.A21, mat.A22, mat.A23, mat.A24],
        [mat.A31, mat.A32, mat.A33, mat.A34],
        [0.0, 0.0, 0.0, 1.0],
    ],
    "target_primary": arr(target_x),
    "target_secondary": arr(target_y),
    "target_tertiary": arr(target_z),
    "target_origin": arr(target_origin),
    "alignment_error": {{
        "origin": origin_error,
        "primary": primary_error,
        "secondary": secondary_error,
        "tertiary": tertiary_error,
    }},
}}
"""
        result = await bridge.execute_python(code)
        return _raise_if_failed(result, "Align coordinate systems failed")

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
        """Highlight faces/edges and create preview point/axis markers.

        Args:
            object_name: Object containing face/edge references.
            faces: Face references such as ["Face19"].
            edges: Edge references such as ["Edge3"].
            points: Preview points as [[x, y, z], ...].
            axes: Preview axes with origin, direction, and optional length.
            colors: Optional color map with face/edge/point/axis RGB values.
            doc_name: Document containing the object. Uses active document if None.

        Returns:
            Highlight result and created preview marker names.
        """
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
