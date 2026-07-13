"""HistCAD-style sketch constraint editability evaluation for the MCP server.

Pure-Python geometric verification (no scipy / Fusion dependency).  Mirrors the
logic in ``HistCAD-68C2/editability/experiment.py`` but operates on HistCAD
sketch JSON (mm) produced by ``parse_freecad_sketch`` and constraint dicts
consumed by ``apply_sketch_constraints``.

Sketch coordinates in JSON are millimetres; internal evaluation uses centimetres
(0.1 scale) to match HistCAD / Fusion convention.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from typing import Any

# HistCAD/Fusion length scale: mm in JSON → cm for geometric checks.
MM_TO_CM = 0.1

DEFAULT_LENGTH_TOL_CM = 1e-3
DEFAULT_ANGLE_TOL_DEG = 0.5

UNIT_TO_MM = {
    "mm": 1.0,
    "millimeter": 1.0,
    "millimeters": 1.0,
    "cm": 10.0,
    "centimeter": 10.0,
    "centimeters": 10.0,
    "m": 1000.0,
    "meter": 1000.0,
    "meters": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
}


class UnsupportedConstraintError(RuntimeError):
    """Raised when a constraint type cannot be verified geometrically."""


@dataclass(frozen=True)
class ConstraintRecord:
    """One normalized HistCAD constraint entry."""

    constraint_type: str
    entry_index: int
    entities: tuple[str, ...]
    value: float | str | None
    extra: dict[str, Any] | None


def _constraint_type_key(value: str) -> str:
    return str(value).strip().lower()


def _format_mm_expression(value_mm: float) -> str:
    return f"{float(value_mm):.10g} mm"


def parse_dimension_value_mm(
    constraint_type: str,
    value: float | str | dict[str, Any] | None,
) -> float:
    """Parse a HistCAD dimension value to millimetres."""
    if value is None:
        raise ValueError(f"{constraint_type} is missing a value.")
    if isinstance(value, dict):
        length = value.get("length")
        if length is None:
            raise ValueError(f"{constraint_type} dict is missing 'length'.")
        return float(length)
    if isinstance(value, (int, float)):
        return float(value)

    normalized = str(value).strip()
    match = re.match(
        r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:\s*/\s*\d+)?)\s*([a-zA-Z]+)?$", normalized
    )
    if not match:
        raise ValueError(f"Cannot parse dimension expression: {value}")
    body, unit = match.group(1), match.group(2) or "mm"
    if "/" in body:
        num, den = body.split("/", 1)
        parsed = float(num.strip()) / float(den.strip())
    else:
        parsed = float(body)
    unit_key = unit.strip().lower()
    if unit_key not in UNIT_TO_MM:
        raise ValueError(f"Unsupported unit in expression: {value}")
    return parsed * UNIT_TO_MM[unit_key]


def _normalize_constraint_entries(raw_entries: Any) -> list[Any]:
    if raw_entries is None:
        return []
    if not isinstance(raw_entries, list):
        return [raw_entries]
    return list(raw_entries)


def _parse_constraint_entry(  # noqa: PLR0911, PLR0912
    constraint_type: str,
    entry: Any,
) -> tuple[tuple[str, ...], float | str | None, dict[str, Any] | None]:
    """Parse one HistCAD constraint entry into entities, value, and extra."""
    ctype = str(constraint_type).strip()
    if ctype in {
        "Coincident",
        "Tangent",
        "Parallel",
        "Perpendicular",
        "Equal",
        "Concentric",
        "Normal",
        "Mirror",
    }:
        if not isinstance(entry, (list, tuple)):
            raise ValueError(f"{ctype} entry must be a list: {entry!r}")
        return tuple(str(item) for item in entry), None, None

    if ctype in {"Horizontal", "Vertical", "Fix"}:
        return (str(entry),), None, None

    if ctype in {"Length", "Radius", "Diameter", "MajorRadius", "MinorRadius"}:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            raise ValueError(f"{ctype} entry must be [entity, value]: {entry!r}")
        return (str(entry[0]),), entry[1], None

    if ctype == "Distance":
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            raise ValueError(f"Distance entry must have at least two refs: {entry!r}")
        entities = (str(entry[0]), str(entry[1]))
        if len(entry) == 2:
            return entities, None, None
        third = entry[2]
        if isinstance(third, dict):
            return entities, third.get("length"), dict(third)
        return entities, third, None

    if ctype == "Angle":
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            raise ValueError(f"Angle entry must be [line_a, line_b, value]: {entry!r}")
        return (str(entry[0]), str(entry[1])), entry[2], None

    if ctype == "Midpoint":
        if not isinstance(entry, (list, tuple)):
            raise ValueError(f"Midpoint entry must be a list: {entry!r}")
        return tuple(str(item) for item in entry), None, None

    raise ValueError(f"Unsupported constraint type for parsing: {ctype}")


def normalize_constraint_records(constraints: dict[str, Any]) -> list[ConstraintRecord]:
    """Convert a HistCAD constraint dict into normalized records."""
    records: list[ConstraintRecord] = []
    for constraint_type, raw_entries in (constraints or {}).items():
        for entry_index, entry in enumerate(_normalize_constraint_entries(raw_entries)):
            entities, value, extra = _parse_constraint_entry(constraint_type, entry)
            records.append(
                ConstraintRecord(
                    constraint_type=str(constraint_type),
                    entry_index=int(entry_index),
                    entities=entities,
                    value=value,
                    extra=dict(extra) if isinstance(extra, dict) else extra,
                )
            )
    return records


def _arc_circle_from_points(
    start: tuple[float, float],
    middle: tuple[float, float],
    end: tuple[float, float],
) -> tuple[tuple[float, float], float] | None:
    x1, y1 = start
    x2, y2 = middle
    x3, y3 = end
    determinant = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(determinant) <= 1e-9:
        return None
    ux = (
        (x1 * x1 + y1 * y1) * (y2 - y3)
        + (x2 * x2 + y2 * y2) * (y3 - y1)
        + (x3 * x3 + y3 * y3) * (y1 - y2)
    ) / determinant
    uy = (
        (x1 * x1 + y1 * y1) * (x3 - x2)
        + (x2 * x2 + y2 * y2) * (x1 - x3)
        + (x3 * x3 + y3 * y3) * (x2 - x1)
    ) / determinant
    radius = math.hypot(x1 - ux, y1 - uy)
    return (ux, uy), radius


def build_live_refs_from_sketch(
    sketch: dict[str, Any],
    *,
    length_scale: float = MM_TO_CM,
) -> dict[str, dict[str, Any]]:
    """Build Fusion-style live ref payloads from HistCAD sketch JSON (mm)."""
    live_refs: dict[str, dict[str, Any]] = {}

    def _scale_xy(point: Any) -> tuple[float, float]:
        return float(point[0]) * length_scale, float(point[1]) * length_scale

    for primitive_name, primitive in sketch.items():
        if not isinstance(primitive, dict):
            continue
        if primitive_name.startswith("line_"):
            start = _scale_xy(primitive["start"])
            end = _scale_xy(primitive["end"])
            live_refs[primitive_name] = {
                "entity_id": primitive_name,
                "kind": "line",
                "geometry": {"start": [start[0], start[1]], "end": [end[0], end[1]]},
            }
            for suffix, point in (("start", start), ("end", end)):
                live_refs[f"{primitive_name}.{suffix}"] = {
                    "entity_id": f"{primitive_name}.{suffix}",
                    "kind": "point",
                    "geometry": {"point": [point[0], point[1]]},
                }
        elif primitive_name.startswith("circle_"):
            center = _scale_xy(primitive["center"])
            radius = float(primitive["radius"]) * length_scale
            live_refs[primitive_name] = {
                "entity_id": primitive_name,
                "kind": "circle",
                "geometry": {"center": [center[0], center[1]], "radius": radius},
            }
            live_refs[f"{primitive_name}.center"] = {
                "entity_id": f"{primitive_name}.center",
                "kind": "point",
                "geometry": {"point": [center[0], center[1]]},
            }
        elif primitive_name.startswith("arc_"):
            start = _scale_xy(primitive["start"])
            middle = _scale_xy(primitive["middle"])
            end = _scale_xy(primitive["end"])
            circle = _arc_circle_from_points(start, middle, end)
            if circle is None:
                continue
            center, radius = circle
            live_refs[primitive_name] = {
                "entity_id": primitive_name,
                "kind": "arc",
                "geometry": {
                    "start": [start[0], start[1]],
                    "end": [end[0], end[1]],
                    "center": [center[0], center[1]],
                    "radius": radius,
                },
            }
            for suffix, point in (("start", start), ("end", end), ("center", center)):
                live_refs[f"{primitive_name}.{suffix}"] = {
                    "entity_id": f"{primitive_name}.{suffix}",
                    "kind": "point",
                    "geometry": {"point": [point[0], point[1]]},
                }
    return live_refs


def _line_segment(
    geometry_payload: dict[str, Any],
) -> tuple[tuple[float, float], tuple[float, float]]:
    geometry = geometry_payload["geometry"] or {}
    start = geometry.get("start")
    end = geometry.get("end")
    if not isinstance(start, list) or not isinstance(end, list):
        raise UnsupportedConstraintError(
            f"Missing line geometry for {geometry_payload.get('entity_id')}"
        )
    return (float(start[0]), float(start[1])), (float(end[0]), float(end[1]))


def _point_xy(geometry_payload: dict[str, Any]) -> tuple[float, float]:
    geometry = geometry_payload["geometry"] or {}
    point = geometry.get("point")
    if not isinstance(point, list):
        raise UnsupportedConstraintError(
            f"Missing point geometry for {geometry_payload.get('entity_id')}"
        )
    return float(point[0]), float(point[1])


def _center_xy(geometry_payload: dict[str, Any]) -> tuple[float, float]:
    geometry = geometry_payload["geometry"] or {}
    center = geometry.get("center")
    if not isinstance(center, list):
        raise UnsupportedConstraintError(
            f"Missing center geometry for {geometry_payload.get('entity_id')}"
        )
    return float(center[0]), float(center[1])


def _radius_value(geometry_payload: dict[str, Any]) -> float:
    geometry = geometry_payload["geometry"] or {}
    radius = geometry.get("radius")
    if radius is None:
        raise UnsupportedConstraintError(
            f"Missing radius for {geometry_payload.get('entity_id')}"
        )
    return float(radius)


def _vector_from_segment(
    segment: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[float, float]:
    (x1, y1), (x2, y2) = segment
    return x2 - x1, y2 - y1


def _segment_length(segment: tuple[tuple[float, float], tuple[float, float]]) -> float:
    dx, dy = _vector_from_segment(segment)
    return math.hypot(dx, dy)


def _directed_angle_deg(
    vector_a: tuple[float, float], vector_b: tuple[float, float]
) -> float:
    cross = vector_a[0] * vector_b[1] - vector_a[1] * vector_b[0]
    dot = vector_a[0] * vector_b[0] + vector_a[1] * vector_b[1]
    angle = math.degrees(math.atan2(cross, dot))
    if angle < 0.0:
        angle += 360.0
    return angle


def _smallest_line_angle_delta_deg(actual: float, expected: float) -> float:
    delta = abs(float(actual) - float(expected)) % 180.0
    return min(delta, 180.0 - delta)


def _distance_point_point(
    point_a: tuple[float, float], point_b: tuple[float, float]
) -> float:
    return math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1])


def _distance_point_segment(
    point: tuple[float, float],
    segment: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    (x1, y1), (x2, y2) = segment
    px, py = point
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq
    t = min(1.0, max(0.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _segments_intersect(
    segment_a: tuple[tuple[float, float], tuple[float, float]],
    segment_b: tuple[tuple[float, float], tuple[float, float]],
    tol: float = 1e-9,
) -> bool:
    def _orientation(
        point_a: tuple[float, float],
        point_b: tuple[float, float],
        point_c: tuple[float, float],
    ) -> float:
        return (point_b[0] - point_a[0]) * (point_c[1] - point_a[1]) - (
            point_b[1] - point_a[1]
        ) * (point_c[0] - point_a[0])

    def _on_segment(
        point_a: tuple[float, float],
        point_b: tuple[float, float],
        point_c: tuple[float, float],
    ) -> bool:
        return (
            min(point_a[0], point_b[0]) - tol
            <= point_c[0]
            <= max(point_a[0], point_b[0]) + tol
            and min(point_a[1], point_b[1]) - tol
            <= point_c[1]
            <= max(point_a[1], point_b[1]) + tol
        )

    a1, a2 = segment_a
    b1, b2 = segment_b
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)
    if ((o1 > tol and o2 < -tol) or (o1 < -tol and o2 > tol)) and (
        (o3 > tol and o4 < -tol) or (o3 < -tol and o4 > tol)
    ):
        return True
    if abs(o1) <= tol and _on_segment(a1, a2, b1):
        return True
    if abs(o2) <= tol and _on_segment(a1, a2, b2):
        return True
    if abs(o3) <= tol and _on_segment(b1, b2, a1):
        return True
    return bool(abs(o4) <= tol and _on_segment(b1, b2, a2))


def _distance_segment_segment(
    segment_a: tuple[tuple[float, float], tuple[float, float]],
    segment_b: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    if _segments_intersect(segment_a, segment_b):
        return 0.0
    return min(
        _distance_point_segment(segment_a[0], segment_b),
        _distance_point_segment(segment_a[1], segment_b),
        _distance_point_segment(segment_b[0], segment_a),
        _distance_point_segment(segment_b[1], segment_a),
    )


def _actual_distance_for_record(
    record: ConstraintRecord,
    live_refs: dict[str, dict[str, Any]],
) -> float:
    direction = (
        str((record.extra or {}).get("direction", "MINIMUM")).strip().upper()
        or "MINIMUM"
    )
    refs = record.entities
    if len(refs) == 1:
        return _segment_length(_line_segment(live_refs[refs[0]]))
    if len(refs) != 2:
        raise UnsupportedConstraintError(f"Unsupported distance arity: {len(refs)}")

    left_ref, right_ref = refs
    left_is_point = "." in left_ref
    right_is_point = "." in right_ref

    if direction in {"HORIZONTAL", "VERTICAL"}:
        if not (left_is_point and right_is_point):
            raise UnsupportedConstraintError(
                "Directional distance currently supports point-point only."
            )
        point_a = _point_xy(live_refs[left_ref])
        point_b = _point_xy(live_refs[right_ref])
        axis = 0 if direction == "HORIZONTAL" else 1
        return abs(point_a[axis] - point_b[axis])

    if left_is_point and right_is_point:
        return _distance_point_point(
            _point_xy(live_refs[left_ref]), _point_xy(live_refs[right_ref])
        )
    if left_is_point and not right_is_point:
        return _distance_point_segment(
            _point_xy(live_refs[left_ref]), _line_segment(live_refs[right_ref])
        )
    if right_is_point and not left_is_point:
        return _distance_point_segment(
            _point_xy(live_refs[right_ref]), _line_segment(live_refs[left_ref])
        )
    return _distance_segment_segment(
        _line_segment(live_refs[left_ref]), _line_segment(live_refs[right_ref])
    )


def _tangent_deviation(
    left_payload: dict[str, Any],
    right_payload: dict[str, Any],
) -> float:
    left_kind = str(left_payload.get("kind"))
    right_kind = str(right_payload.get("kind"))
    if left_kind == "line" and right_kind in {"circle", "arc"}:
        center = _center_xy(right_payload)
        radius = _radius_value(right_payload)
        return abs(
            _distance_point_segment(center, _line_segment(left_payload)) - radius
        )
    if right_kind == "line" and left_kind in {"circle", "arc"}:
        center = _center_xy(left_payload)
        radius = _radius_value(left_payload)
        return abs(
            _distance_point_segment(center, _line_segment(right_payload)) - radius
        )
    if left_kind in {"circle", "arc"} and right_kind in {"circle", "arc"}:
        center_left = _center_xy(left_payload)
        radius_left = _radius_value(left_payload)
        center_right = _center_xy(right_payload)
        radius_right = _radius_value(right_payload)
        center_distance = _distance_point_point(center_left, center_right)
        return min(
            abs(center_distance - (radius_left + radius_right)),
            abs(center_distance - abs(radius_left - radius_right)),
        )
    raise UnsupportedConstraintError(
        f"Unsupported tangent pair: {left_kind}, {right_kind}"
    )


def evaluate_constraint_record(  # noqa: PLR0911, PLR0912
    record: ConstraintRecord,
    live_refs: dict[str, dict[str, Any]],
    *,
    length_tol_cm: float = DEFAULT_LENGTH_TOL_CM,
    angle_tol_deg: float = DEFAULT_ANGLE_TOL_DEG,
) -> dict[str, Any]:
    """Verify one constraint against solved sketch geometry."""
    normalized_type = _constraint_type_key(record.constraint_type)
    refs = record.entities

    if normalized_type == "coincident":
        base = _point_xy(live_refs[refs[0]])
        actual = (
            max(
                _distance_point_point(base, _point_xy(live_refs[ref]))
                for ref in refs[1:]
            )
            if len(refs) > 1
            else 0.0
        )
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "horizontal":
        if len(refs) == 1:
            start, end = _line_segment(live_refs[refs[0]])
            actual = abs(start[1] - end[1])
        else:
            anchor = _point_xy(live_refs[refs[0]])
            actual = max(
                abs(anchor[1] - _point_xy(live_refs[ref])[1]) for ref in refs[1:]
            )
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "vertical":
        if len(refs) == 1:
            start, end = _line_segment(live_refs[refs[0]])
            actual = abs(start[0] - end[0])
        else:
            anchor = _point_xy(live_refs[refs[0]])
            actual = max(
                abs(anchor[0] - _point_xy(live_refs[ref])[0]) for ref in refs[1:]
            )
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "parallel":
        seg_a = _line_segment(live_refs[refs[0]])
        deltas = []
        ok = True
        for ref in refs[1:]:
            angle = _directed_angle_deg(
                _vector_from_segment(seg_a),
                _vector_from_segment(_line_segment(live_refs[ref])),
            )
            delta = min(abs(angle), abs(angle - 180.0), abs(angle - 360.0))
            deltas.append(delta)
            ok = ok and delta <= angle_tol_deg
        return {"ok": ok, "actual": max(deltas) if deltas else 0.0}

    if normalized_type == "perpendicular":
        segment_a = _line_segment(live_refs[refs[0]])
        segment_b = _line_segment(live_refs[refs[1]])
        angle = _directed_angle_deg(
            _vector_from_segment(segment_a), _vector_from_segment(segment_b)
        )
        delta = min(abs(angle - 90.0), abs(angle - 270.0))
        return {"ok": delta <= angle_tol_deg, "actual": delta}

    if normalized_type == "tangent":
        delta = _tangent_deviation(live_refs[refs[0]], live_refs[refs[1]])
        return {"ok": delta <= length_tol_cm, "actual": delta}

    if normalized_type == "length":
        expected_cm = (
            parse_dimension_value_mm(record.constraint_type, record.value) * MM_TO_CM
        )
        actual = _segment_length(_line_segment(live_refs[refs[0]]))
        return {
            "ok": abs(actual - expected_cm) <= length_tol_cm,
            "actual": actual,
            "expected": expected_cm,
        }

    if normalized_type == "radius":
        expected_cm = (
            parse_dimension_value_mm(record.constraint_type, record.value) * MM_TO_CM
        )
        actual = _radius_value(live_refs[refs[0]])
        return {
            "ok": abs(actual - expected_cm) <= length_tol_cm,
            "actual": actual,
            "expected": expected_cm,
        }

    if normalized_type == "diameter":
        expected_cm = (
            parse_dimension_value_mm(record.constraint_type, record.value) * MM_TO_CM
        )
        actual = 2.0 * _radius_value(live_refs[refs[0]])
        return {
            "ok": abs(actual - expected_cm) <= length_tol_cm,
            "actual": actual,
            "expected": expected_cm,
        }

    if normalized_type == "equal":
        if live_refs[refs[0]]["kind"] == "line":
            base_len = _segment_length(_line_segment(live_refs[refs[0]]))
            actual = max(
                abs(base_len - _segment_length(_line_segment(live_refs[ref])))
                for ref in refs[1:]
            )
        else:
            base_radius = _radius_value(live_refs[refs[0]])
            actual = max(
                abs(base_radius - _radius_value(live_refs[ref])) for ref in refs[1:]
            )
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "concentric":
        center_a = _center_xy(live_refs[refs[0]])
        actual = max(
            _distance_point_point(center_a, _center_xy(live_refs[ref]))
            for ref in refs[1:]
        )
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "distance":
        if record.value is not None:
            expected_cm = (
                parse_dimension_value_mm(record.constraint_type, record.value)
                * MM_TO_CM
            )
        elif record.extra:
            expected_cm = (
                parse_dimension_value_mm(record.constraint_type, record.extra)
                * MM_TO_CM
            )
        else:
            raise ValueError("Distance constraint is missing a value.")
        actual = _actual_distance_for_record(record, live_refs)
        return {
            "ok": abs(actual - expected_cm) <= length_tol_cm,
            "actual": actual,
            "expected": expected_cm,
        }

    raise UnsupportedConstraintError(
        f"Unsupported constraint type: {record.constraint_type}"
    )


def evaluate_constraint_records(
    records: list[ConstraintRecord],
    live_refs: dict[str, dict[str, Any]],
    *,
    length_tol_cm: float = DEFAULT_LENGTH_TOL_CM,
    angle_tol_deg: float = DEFAULT_ANGLE_TOL_DEG,
) -> dict[str, Any]:
    """Evaluate multiple constraints; return satisfaction summary."""
    results: list[dict[str, Any]] = []
    supported_total = 0
    satisfied_total = 0
    unsupported_total = 0

    for record in records:
        missing = [ref for ref in record.entities if ref not in live_refs]
        if missing:
            unsupported_total += 1
            results.append(
                {
                    "constraint_type": record.constraint_type,
                    "entry_index": record.entry_index,
                    "entities": list(record.entities),
                    "ok": None,
                    "error": f"Missing live refs: {missing}",
                }
            )
            continue
        try:
            evaluated = evaluate_constraint_record(
                record,
                live_refs,
                length_tol_cm=length_tol_cm,
                angle_tol_deg=angle_tol_deg,
            )
            supported_total += 1
            if bool(evaluated.get("ok")):
                satisfied_total += 1
            results.append(
                {
                    "constraint_type": record.constraint_type,
                    "entry_index": record.entry_index,
                    "entities": list(record.entities),
                    "ok": bool(evaluated.get("ok")),
                    "actual": evaluated.get("actual"),
                    "expected": evaluated.get("expected"),
                    "delta": evaluated.get("delta"),
                }
            )
        except UnsupportedConstraintError as exc:
            unsupported_total += 1
            results.append(
                {
                    "constraint_type": record.constraint_type,
                    "entry_index": record.entry_index,
                    "entities": list(record.entities),
                    "ok": None,
                    "error": str(exc),
                }
            )

    satisfaction_rate = (
        float(satisfied_total) / float(supported_total) if supported_total > 0 else None
    )
    return {
        "records": results,
        "supported_constraints": supported_total,
        "unsupported_constraints": unsupported_total,
        "satisfied_constraints": satisfied_total,
        "total_constraints": len(records),
        "satisfaction_rate": satisfaction_rate,
        "all_satisfied": unsupported_total == 0 and satisfied_total == supported_total,
    }


def _record_matches_target(
    record: ConstraintRecord,
    *,
    constraint_type: str,
    entry_index: int,
    entities: tuple[str, ...] | None = None,
) -> bool:
    if _constraint_type_key(record.constraint_type) != _constraint_type_key(
        constraint_type
    ):
        return False
    if int(record.entry_index) != int(entry_index):
        return False
    return entities is None or tuple(record.entities) == tuple(entities)


def split_target_and_preserved(
    source_records: list[ConstraintRecord],
    *,
    constraint_type: str,
    entry_index: int,
    edited_value_mm: float,
    entities: tuple[str, ...] | None = None,
) -> tuple[ConstraintRecord, list[ConstraintRecord]]:
    """Split records into the edited target and preserved constraints."""
    target_record: ConstraintRecord | None = None
    preserved: list[ConstraintRecord] = []
    for record in source_records:
        if target_record is None and _record_matches_target(
            record,
            constraint_type=constraint_type,
            entry_index=entry_index,
            entities=entities,
        ):
            extra = (
                dict(record.extra) if isinstance(record.extra, dict) else record.extra
            )
            if extra and "length" in extra:
                extra = {**extra, "length": edited_value_mm}
            target_record = ConstraintRecord(
                constraint_type=record.constraint_type,
                entry_index=record.entry_index,
                entities=record.entities,
                value=_format_mm_expression(edited_value_mm),
                extra=extra,
            )
            continue
        preserved.append(record)
    if target_record is None:
        raise ValueError(
            f"Target constraint not found: {constraint_type}[{entry_index}]"
        )
    return target_record, preserved


def replace_constraint_value(
    constraints: dict[str, Any],
    *,
    constraint_type: str,
    entry_index: int,
    edited_value_mm: float,
    entities: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return a deep copy of ``constraints`` with one dimension entry updated.

    Use before ``apply_sketch_constraints`` when running a HistCAD-style edit.
    """
    updated = copy.deepcopy(constraints)
    entries = _normalize_constraint_entries(updated.get(constraint_type))
    if entry_index < 0 or entry_index >= len(entries):
        raise ValueError(
            f"entry_index {entry_index} out of range for {constraint_type}"
        )
    entry = entries[entry_index]
    ctype = str(constraint_type).strip()
    if ctype in {"Length", "Radius", "Diameter", "MajorRadius", "MinorRadius"}:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            raise ValueError(f"Cannot edit malformed {ctype} entry: {entry!r}")
        if entities is not None and str(entry[0]) != str(entities[0]):
            raise ValueError("entities do not match target entry")
        entries[entry_index] = [entry[0], _format_mm_expression(edited_value_mm)]
    elif ctype == "Distance":
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            raise ValueError(f"Cannot edit malformed Distance entry: {entry!r}")
        if entities is not None and tuple(str(entry[i]) for i in range(2)) != tuple(
            entities
        ):
            raise ValueError("entities do not match target entry")
        if len(entry) == 2:
            entries[entry_index] = [
                entry[0],
                entry[1],
                {"length": edited_value_mm, "direction": "MINIMUM"},
            ]
        elif isinstance(entry[2], dict):
            entries[entry_index] = [
                entry[0],
                entry[1],
                {**entry[2], "length": edited_value_mm},
            ]
        else:
            entries[entry_index] = [entry[0], entry[1], edited_value_mm]
    else:
        raise ValueError(f"Unsupported edit constraint type: {ctype}")
    updated[constraint_type] = entries
    return updated


def evaluate_sketch_editability(
    *,
    live_sketch: dict[str, Any],
    reference_constraints: dict[str, Any],
    constraint_type: str,
    entry_index: int,
    edited_value_mm: float,
    entities: tuple[str, ...] | None = None,
    length_tol_mm: float = 0.01,
    angle_tol_deg: float = DEFAULT_ANGLE_TOL_DEG,
) -> dict[str, Any]:
    """Compute HistCAD ER / cPCSR / OES from solved sketch geometry.

    Args:
        live_sketch: HistCAD entity dict after solve (mm), from
            ``parse_freecad_sketch`` or ``create_sketch_geometry``.
        reference_constraints: Original HistCAD constraint dict (before edit).
        constraint_type: Edited constraint type (e.g. ``Distance``, ``Diameter``).
        entry_index: Zero-based index within that constraint type list.
        edited_value_mm: New dimension value in millimetres.
        entities: Optional entity tuple to disambiguate the target entry.
        length_tol_mm: Length tolerance in millimetres.
        angle_tol_deg: Angle tolerance in degrees.

    Returns:
        Metrics dict with ``ER``, ``cPCSR``, ``OES``, target/preserved details.
    """
    length_tol_cm = float(length_tol_mm) * MM_TO_CM
    source_records = normalize_constraint_records(reference_constraints)
    target_record, preserved_records = split_target_and_preserved(
        source_records,
        constraint_type=constraint_type,
        entry_index=entry_index,
        edited_value_mm=edited_value_mm,
        entities=entities,
    )
    live_refs = build_live_refs_from_sketch(live_sketch)

    target_eval = evaluate_constraint_records(
        [target_record],
        live_refs,
        length_tol_cm=length_tol_cm,
        angle_tol_deg=angle_tol_deg,
    )
    preserved_eval = evaluate_constraint_records(
        preserved_records,
        live_refs,
        length_tol_cm=length_tol_cm,
        angle_tol_deg=angle_tol_deg,
    )

    target_row = target_eval["records"][0] if target_eval["records"] else {}
    target_hit = target_row.get("ok") is True
    cpcsr = float(preserved_eval["satisfaction_rate"] or 0.0)
    er = target_hit
    oes = er and preserved_eval.get("all_satisfied") is True

    failed_preserved = [
        row for row in preserved_eval["records"] if row.get("ok") is False
    ]
    return {
        "target_constraint_type": constraint_type,
        "target_entry_index": entry_index,
        "target_entities": list(target_record.entities),
        "edited_value_mm": edited_value_mm,
        "target_hit": target_hit,
        "target_actual_cm": target_row.get("actual"),
        "target_expected_cm": target_row.get("expected"),
        "target_evaluation": target_row,
        "preserved_records": preserved_eval["records"],
        "preserved_supported_constraints": preserved_eval["supported_constraints"],
        "preserved_satisfied_constraints": preserved_eval["satisfied_constraints"],
        "preserved_total_constraints": preserved_eval["total_constraints"],
        "preserved_constraint_satisfaction_rate": cpcsr,
        "preserved_constraints_all_satisfied": preserved_eval["all_satisfied"],
        "failed_preserved": failed_preserved,
        "ER": er,
        "cPCSR": cpcsr,
        "OES": oes,
        "reward": float(oes),
    }
