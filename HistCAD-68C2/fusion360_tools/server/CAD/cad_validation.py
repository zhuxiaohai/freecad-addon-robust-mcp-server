import math
import os

import adsk.core
import adsk.fusion

from ..runtime import maybe_do_events


class CADValidation:
    def _health_state_name(self, health_state):
        mapping = {
            adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState: "HealthyFeatureHealthState",
            adsk.fusion.FeatureHealthStates.WarningFeatureHealthState: "WarningFeatureHealthState",
            adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState: "ErrorFeatureHealthState",
            adsk.fusion.FeatureHealthStates.SuppressedFeatureHealthState: "SuppressedFeatureHealthState",
            adsk.fusion.FeatureHealthStates.RolledBackFeatureHealthState: "RolledBackFeatureHealthState",
        }
        return mapping.get(health_state, f"Unknown({health_state})")

    def validate_step_file(self, filepath, min_bytes=1):
        if not isinstance(filepath, str) or not filepath.strip():
            raise ValueError("filepath must be a non-empty string.")
        abs_path = os.path.abspath(filepath)
        exists = os.path.exists(abs_path)
        size_bytes = os.path.getsize(abs_path) if exists else 0
        ok = exists and size_bytes >= int(min_bytes)
        return {
            "ok": ok,
            "filepath": abs_path,
            "exists": exists,
            "size_bytes": int(size_bytes),
            "min_bytes": int(min_bytes),
        }

    def _build_sketch_validation_payload(self, sketch, sketch_index):
        health_state = sketch.healthState
        health_state_name = self._health_state_name(health_state)
        message = sketch.errorOrWarningMessage or ""

        dimension_count = sketch.sketchDimensions.count
        geometric_constraint_count = sketch.geometricConstraints.count
        is_fully_constrained = bool(sketch.isFullyConstrained)

        # 过约束并不总是直接抛异常，这里结合 healthState + message 双重判断
        lower_message = message.lower()
        over_constrained = any(
            token in lower_message
            for token in ["over constrained", "over-constrained", "过约束"]
        )
        ok = (
            health_state != adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState
            and not over_constrained
        )
        return {
            "ok": ok,
            "sketch_num": sketch_index,
            "health_state": int(health_state),
            "health_state_name": health_state_name,
            "error_or_warning_message": message,
            "is_fully_constrained": is_fully_constrained,
            "over_constrained": over_constrained,
            "dimension_count": int(dimension_count),
            "geometric_constraint_count": int(geometric_constraint_count),
        }

    def validate_sketch_constraints(self, sketch_num=-1):
        sketch, sketch_index = self._get_sketch(sketch_num)
        return self._build_sketch_validation_payload(sketch, sketch_index)

    def validate_all_sketch_constraints(self):
        sketches = self._list_live_sketches()
        sketch_results = [
            self._build_sketch_validation_payload(sketch, idx)
            for idx, sketch in enumerate(sketches)
        ]

        failed_count = sum(1 for x in sketch_results if not bool(x.get("ok", False)))
        summary = {
            "total_sketches": len(sketch_results),
            "ok_sketches": len(sketch_results) - failed_count,
            "failed_sketches": failed_count,
        }
        return {
            "ok": failed_count == 0,
            "summary": summary,
            "sketch_results": sketch_results,
        }

    def check_constraint_satisfied(
        self,
        constraint_type,
        entities,
        value=None,
        sketch_num=-1,
        extra=None,
    ):
        if not isinstance(entities, list) or not entities:
            raise ValueError("entities must be a non-empty list of entity ids.")
        if not isinstance(constraint_type, str) or not constraint_type.strip():
            raise ValueError("constraint_type must be a non-empty string.")

        normalized_type = constraint_type.strip().lower()
        if normalized_type == "fix":
            normalized_type = "fixed"

        sketch, sketch_index, resolved = self._resolve_entities(entities, sketch_num)
        payload = {
            "constraint_type": str(constraint_type),
            "normalized_type": normalized_type,
            "entities": [str(entity_id) for entity_id in entities],
            "value": value,
            "sketch_num": int(sketch_index),
        }

        try:
            if normalized_type in self.DIMENSION_TYPES:
                result = self._check_dimension_constraint_satisfied(
                    normalized_type,
                    sketch,
                    sketch_index,
                    resolved,
                    value,
                    extra,
                )
            elif normalized_type in self.GEOMETRIC_TYPES:
                result = self._check_geometric_constraint_satisfied(
                    normalized_type,
                    sketch,
                    sketch_index,
                    resolved,
                    extra,
                )
            else:
                raise ValueError(f"Unsupported constraint type: {constraint_type}")
        except NotImplementedError as ex:
            payload.update(
                {
                    "supported": False,
                    "satisfied": None,
                    "reason": str(ex),
                    "details": [],
                }
            )
            return payload

        payload.update(result)
        return payload

    def _sketch_reference_length(self, sketch):
        bbox = getattr(sketch, "boundingBox", None)
        if bbox is None:
            return 1.0
        try:
            dx = float(bbox.maxPoint.x - bbox.minPoint.x)
            dy = float(bbox.maxPoint.y - bbox.minPoint.y)
            dz = float(bbox.maxPoint.z - bbox.minPoint.z)
            diagonal = math.sqrt(dx * dx + dy * dy + dz * dz)
        except Exception:
            diagonal = 0.0
        return max(1.0, float(diagonal))

    def _constraint_length_tolerance(self, sketch, *reference_values):
        scale = self._sketch_reference_length(sketch)
        for value in reference_values:
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                scale = max(scale, abs(float(value)))
        return max(1e-4, scale * 1e-4)

    def _constraint_angle_tolerance_degrees(self):
        return 0.1

    def _scalar_values_close(self, actual_value, target_value, tolerance):
        return abs(float(actual_value) - float(target_value)) <= float(tolerance)

    def _point_distance_xy(self, first_point, second_point):
        return math.hypot(
            float(first_point[0]) - float(second_point[0]),
            float(first_point[1]) - float(second_point[1]),
        )

    def _project_point_to_segment_parameter(
        self, line_start, line_end, point_xy, tol=1e-12
    ):
        dx = float(line_end[0] - line_start[0])
        dy = float(line_end[1] - line_start[1])
        denom = dx * dx + dy * dy
        if denom <= tol:
            return None
        return (
            (float(point_xy[0]) - float(line_start[0])) * dx
            + (float(point_xy[1]) - float(line_start[1])) * dy
        ) / denom

    def _point_line_distance(self, line_start, line_end, point_xy, tol=1e-12):
        dx = float(line_end[0] - line_start[0])
        dy = float(line_end[1] - line_start[1])
        denom = math.sqrt(dx * dx + dy * dy)
        if denom <= tol:
            return None
        numerator = abs(
            dx * (float(line_start[1]) - float(point_xy[1]))
            - (float(line_start[0]) - float(point_xy[0])) * dy
        )
        return numerator / denom

    def _point_segment_distance(self, line_start, line_end, point_xy, tol=1e-12):
        dx = float(line_end[0] - line_start[0])
        dy = float(line_end[1] - line_start[1])
        denom = dx * dx + dy * dy
        if denom <= tol:
            return self._point_distance_xy(line_start, point_xy)
        t = self._project_point_to_segment_parameter(
            line_start,
            line_end,
            point_xy,
            tol=tol,
        )
        if t is None:
            return self._point_distance_xy(line_start, point_xy)
        clamped = min(1.0, max(0.0, float(t)))
        projected = (
            float(line_start[0]) + dx * clamped,
            float(line_start[1]) + dy * clamped,
        )
        return self._point_distance_xy(projected, point_xy)

    def _projection_on_axis(self, point_xy, axis_xy):
        return float(point_xy[0]) * float(axis_xy[0]) + float(point_xy[1]) * float(
            axis_xy[1]
        )

    def _perpendicular_axis(self, axis_xy):
        return (-float(axis_xy[1]), float(axis_xy[0]))

    def _line_length_value(self, item):
        segment = self._line_segment_from_entity(item[1])
        if segment is None:
            return None
        return self._point_distance_xy(segment[0], segment[1])

    def _line_direction_from_item(self, item):
        segment = self._line_segment_from_entity(item[1])
        if segment is None:
            return None
        direction = (
            float(segment[1][0]) - float(segment[0][0]),
            float(segment[1][1]) - float(segment[0][1]),
        )
        return self._normalize_vector2(direction)

    def _directions_parallel(self, first_direction, second_direction):
        cross = abs(
            float(first_direction[0]) * float(second_direction[1])
            - float(first_direction[1]) * float(second_direction[0])
        )
        tolerance = math.sin(math.radians(self._constraint_angle_tolerance_degrees()))
        return cross <= tolerance

    def _directions_perpendicular(self, first_direction, second_direction):
        dot = abs(
            float(first_direction[0]) * float(second_direction[0])
            + float(first_direction[1]) * float(second_direction[1])
        )
        tolerance = math.sin(math.radians(self._constraint_angle_tolerance_degrees()))
        return dot <= tolerance

    def _point_xy_from_item(self, item):
        return self._point2_from_generic_point(item[1])

    def _entity_center_xy(self, item):
        entity_obj = item[1]
        kind = item[2].get("kind")
        if kind in {"circle", "arc"}:
            center_point = self._tangent_curve_center_point(entity_obj, item=item)
        else:
            center_point = getattr(entity_obj, "centerSketchPoint", None)
            if center_point is None:
                geometry = getattr(entity_obj, "geometry", None)
                center_point = getattr(geometry, "center", None)
        return self._point2_from_generic_point(center_point)

    def _ellipse_radius_value(self, item, radius_kind):
        if item[2].get("kind") not in {"ellipse", "elliptical_arc"}:
            return None
        entity_obj = item[1]
        geometry = getattr(entity_obj, "geometry", None)
        attr_name = "majorRadius" if radius_kind == "majorradius" else "minorRadius"
        value = getattr(entity_obj, attr_name, None)
        if value is None and geometry is not None:
            value = getattr(geometry, attr_name, None)
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _evaluate_dimension_target_value(self, constraint_type, value):
        if value is None:
            return None
        normalized_type = str(constraint_type or "").strip().lower()
        if normalized_type == "angle":
            return self._evaluate_angle_target_value(value)
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            return None
        evaluated = self._evaluate_length_expression_value(value)
        if evaluated is not None:
            return float(evaluated)
        normalized = self._normalize_dimension_expression(normalized_type, value)
        if normalized != value:
            evaluated = self._evaluate_length_expression_value(normalized)
            if evaluated is not None:
                return float(evaluated)
        try:
            return float(str(value).strip())
        except Exception:
            return None

    def _evaluate_angle_target_value(self, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            return None
        expression = value.strip()
        if not expression:
            return None
        lowered = expression.lower()
        if lowered.endswith("deg"):
            expression = expression[:-3].strip()
        elif lowered.endswith("°"):
            expression = expression[:-1].strip()
        try:
            return float(expression)
        except Exception:
            return None

    def _constraint_axis_in_sketch(
        self, sketch, sketch_index, extra, default_direction
    ):
        axis_xy = None
        if isinstance(extra, dict) and "local_axis_mode" in extra:
            axis_xy = self._local_axis_direction_in_sketch_space(
                sketch,
                extra,
                sketch_index=sketch_index,
            )
        if axis_xy is None:
            if default_direction == "horizontal":
                axis_xy = (1.0, 0.0)
            else:
                axis_xy = (0.0, 1.0)
        normalized = self._normalize_vector2(axis_xy)
        if normalized is None:
            raise NotImplementedError(
                "Failed to resolve a stable sketch-space axis direction."
            )
        return normalized

    def _check_geometric_constraint_satisfied(
        self,
        normalized_type,
        sketch,
        sketch_index,
        resolved,
        extra,
    ):
        if normalized_type in {"parallel", "perpendicular"}:
            details = []
            for pair_index, (left, right) in enumerate(self._anchor_pairs(resolved)):
                left_direction = self._line_direction_from_item(left)
                right_direction = self._line_direction_from_item(right)
                if left_direction is None or right_direction is None:
                    raise NotImplementedError(
                        f"{normalized_type} precheck currently supports line-line pairs only."
                    )
                if normalized_type == "parallel":
                    satisfied = self._directions_parallel(
                        left_direction, right_direction
                    )
                else:
                    satisfied = self._directions_perpendicular(
                        left_direction, right_direction
                    )
                details.append(
                    {
                        "pair_index": int(pair_index),
                        "left": str(left[0]),
                        "right": str(right[0]),
                        "satisfied": bool(satisfied),
                    }
                )
            return {
                "supported": True,
                "satisfied": all(item["satisfied"] for item in details),
                "details": details,
            }

        if normalized_type in {"horizontal", "vertical"}:
            kinds = self._normalize_kinds(resolved)
            axis = self._constraint_axis_in_sketch(
                sketch,
                sketch_index,
                extra,
                normalized_type,
            )
            details = []
            if all(kind == "line" for kind in kinds):
                for item in resolved:
                    direction = self._line_direction_from_item(item)
                    if direction is None:
                        raise NotImplementedError(
                            f"{normalized_type} precheck could not resolve line direction."
                        )
                    satisfied = self._directions_parallel(direction, axis)
                    details.append(
                        {
                            "entity": str(item[0]),
                            "satisfied": bool(satisfied),
                        }
                    )
                return {
                    "supported": True,
                    "satisfied": all(item["satisfied"] for item in details),
                    "details": details,
                }
            if all(kind == "point" for kind in kinds):
                if len(resolved) < 2:
                    raise NotImplementedError(
                        f"{normalized_type} precheck expects at least two points."
                    )
                perpendicular = self._perpendicular_axis(axis)
                anchor_xy = self._point_xy_from_item(resolved[0])
                if anchor_xy is None:
                    raise NotImplementedError(
                        f"{normalized_type} precheck could not resolve point geometry."
                    )
                anchor_projection = self._projection_on_axis(anchor_xy, perpendicular)
                tolerance = self._constraint_length_tolerance(sketch)
                for item in resolved:
                    point_xy = self._point_xy_from_item(item)
                    if point_xy is None:
                        raise NotImplementedError(
                            f"{normalized_type} precheck could not resolve point geometry."
                        )
                    current_projection = self._projection_on_axis(
                        point_xy, perpendicular
                    )
                    delta = abs(float(current_projection) - float(anchor_projection))
                    details.append(
                        {
                            "entity": str(item[0]),
                            "actual_delta": float(delta),
                            "satisfied": bool(delta <= tolerance),
                        }
                    )
                return {
                    "supported": True,
                    "satisfied": all(item["satisfied"] for item in details),
                    "details": details,
                }
            raise NotImplementedError(
                f"{normalized_type} precheck currently supports all-line or all-point entities only."
            )

        if normalized_type == "equal":
            details = []
            for pair_index, (left, right) in enumerate(self._anchor_pairs(resolved)):
                left_kind = left[2].get("kind")
                right_kind = right[2].get("kind")
                if left_kind == right_kind == "line":
                    left_value = self._line_length_value(left)
                    right_value = self._line_length_value(right)
                elif {left_kind, right_kind} <= {"arc", "circle"}:
                    left_value = self._curve_radius(left[1])
                    right_value = self._curve_radius(right[1])
                else:
                    raise NotImplementedError(
                        "equal precheck currently supports line-line and circle/arc radius pairs only."
                    )
                if left_value is None or right_value is None:
                    raise NotImplementedError(
                        "equal precheck could not resolve comparable geometry."
                    )
                tolerance = self._constraint_length_tolerance(
                    sketch, left_value, right_value
                )
                satisfied = self._scalar_values_close(
                    left_value, right_value, tolerance
                )
                details.append(
                    {
                        "pair_index": int(pair_index),
                        "left": str(left[0]),
                        "right": str(right[0]),
                        "left_value": float(left_value),
                        "right_value": float(right_value),
                        "tolerance": float(tolerance),
                        "satisfied": bool(satisfied),
                    }
                )
            return {
                "supported": True,
                "satisfied": all(item["satisfied"] for item in details),
                "details": details,
            }

        if normalized_type == "concentric":
            details = []
            for pair_index, (left, right) in enumerate(self._anchor_pairs(resolved)):
                left_center = self._entity_center_xy(left)
                right_center = self._entity_center_xy(right)
                if left_center is None or right_center is None:
                    raise NotImplementedError(
                        "concentric precheck currently supports entities with resolvable centers only."
                    )
                actual_distance = self._point_distance_xy(left_center, right_center)
                tolerance = self._constraint_length_tolerance(sketch, actual_distance)
                details.append(
                    {
                        "pair_index": int(pair_index),
                        "left": str(left[0]),
                        "right": str(right[0]),
                        "actual_center_distance": float(actual_distance),
                        "tolerance": float(tolerance),
                        "satisfied": bool(actual_distance <= tolerance),
                    }
                )
            return {
                "supported": True,
                "satisfied": all(item["satisfied"] for item in details),
                "details": details,
            }

        if normalized_type == "coincident":
            details = []
            for pair_index, (left, right) in enumerate(self._anchor_pairs(resolved)):
                left_kind = left[2].get("kind")
                right_kind = right[2].get("kind")
                if left_kind == "point" and right_kind == "point":
                    left_xy = self._point_xy_from_item(left)
                    right_xy = self._point_xy_from_item(right)
                    if left_xy is None or right_xy is None:
                        raise NotImplementedError(
                            "coincident precheck could not resolve point geometry."
                        )
                    actual_distance = self._point_distance_xy(left_xy, right_xy)
                elif left_kind == "point" and right_kind == "line":
                    left_xy = self._point_xy_from_item(left)
                    segment = self._line_segment_from_entity(right[1])
                    if left_xy is None or segment is None:
                        raise NotImplementedError(
                            "coincident precheck could not resolve point-line geometry."
                        )
                    actual_distance = self._point_segment_distance(
                        segment[0], segment[1], left_xy
                    )
                elif left_kind == "line" and right_kind == "point":
                    right_xy = self._point_xy_from_item(right)
                    segment = self._line_segment_from_entity(left[1])
                    if right_xy is None or segment is None:
                        raise NotImplementedError(
                            "coincident precheck could not resolve point-line geometry."
                        )
                    actual_distance = self._point_segment_distance(
                        segment[0], segment[1], right_xy
                    )
                elif left_kind == "point" and right_kind in {"circle", "arc"}:
                    point_xy = self._point_xy_from_item(left)
                    center_xy = self._entity_center_xy(right)
                    radius = self._curve_radius(right[1])
                    if point_xy is None or center_xy is None or radius is None:
                        raise NotImplementedError(
                            "coincident precheck could not resolve point-curve geometry."
                        )
                    actual_distance = abs(
                        self._point_distance_xy(point_xy, center_xy) - float(radius)
                    )
                    if right_kind == "arc" and not self._arc_support_point_is_on_entity(
                        right[1], point_xy, item=right
                    ):
                        actual_distance = math.inf
                elif left_kind in {"circle", "arc"} and right_kind == "point":
                    point_xy = self._point_xy_from_item(right)
                    center_xy = self._entity_center_xy(left)
                    radius = self._curve_radius(left[1])
                    if point_xy is None or center_xy is None or radius is None:
                        raise NotImplementedError(
                            "coincident precheck could not resolve point-curve geometry."
                        )
                    actual_distance = abs(
                        self._point_distance_xy(point_xy, center_xy) - float(radius)
                    )
                    if left_kind == "arc" and not self._arc_support_point_is_on_entity(
                        left[1], point_xy, item=left
                    ):
                        actual_distance = math.inf
                else:
                    raise NotImplementedError(
                        "coincident precheck currently supports point-point, point-line, and point-circle/arc pairs only."
                    )

                tolerance = self._constraint_length_tolerance(sketch)
                details.append(
                    {
                        "pair_index": int(pair_index),
                        "left": str(left[0]),
                        "right": str(right[0]),
                        "actual_distance": None
                        if not math.isfinite(actual_distance)
                        else float(actual_distance),
                        "tolerance": float(tolerance),
                        "satisfied": bool(
                            math.isfinite(actual_distance)
                            and actual_distance <= tolerance
                        ),
                    }
                )
            return {
                "supported": True,
                "satisfied": all(item["satisfied"] for item in details),
                "details": details,
            }

        if normalized_type == "tangent":
            details = []
            for pair_index, (left, right) in enumerate(self._anchor_pairs(resolved)):
                first_is_line = left[2].get("kind") == "line"
                second_is_line = right[2].get("kind") == "line"
                first_is_curve = left[2].get("kind") in {"circle", "arc"}
                second_is_curve = right[2].get("kind") in {"circle", "arc"}
                if first_is_line and second_is_curve:
                    choice = self._line_curve_tangent_choice(
                        left[1],
                        right[1],
                        None,
                        curve_item=right,
                    )
                elif second_is_line and first_is_curve:
                    choice = self._line_curve_tangent_choice(
                        right[1],
                        left[1],
                        None,
                        curve_item=left,
                    )
                elif first_is_curve and second_is_curve:
                    choice = self._curve_curve_tangent_choice(left[1], right[1], None)
                else:
                    raise NotImplementedError(
                        "tangent precheck currently supports line-circle/arc and circle/arc-circle/arc pairs only."
                    )
                if choice is None:
                    actual_distance = math.inf
                else:
                    actual_distance = float(choice.get("distance", math.inf))
                tolerance = self._constraint_length_tolerance(sketch, actual_distance)
                details.append(
                    {
                        "pair_index": int(pair_index),
                        "left": str(left[0]),
                        "right": str(right[0]),
                        "actual_gap": None
                        if not math.isfinite(actual_distance)
                        else float(actual_distance),
                        "tolerance": float(tolerance),
                        "satisfied": bool(
                            math.isfinite(actual_distance)
                            and actual_distance <= tolerance
                        ),
                    }
                )
            return {
                "supported": True,
                "satisfied": all(item["satisfied"] for item in details),
                "details": details,
            }

        if normalized_type == "normal":
            details = []
            for pair_index, (left, right) in enumerate(self._anchor_pairs(resolved)):
                pair_mode, line_item, other_item = self._resolve_normal_pair(
                    left, right
                )
                if pair_mode == "line_nurbs":
                    raise NotImplementedError(
                        "normal precheck does not currently support line-nurbs helper semantics."
                    )
                if pair_mode == "line_line":
                    line_direction = self._line_direction_from_item(line_item)
                    other_direction = self._line_direction_from_item(other_item)
                    if line_direction is None or other_direction is None:
                        raise NotImplementedError(
                            "normal precheck could not resolve line direction."
                        )
                    satisfied = self._directions_perpendicular(
                        line_direction, other_direction
                    )
                    details.append(
                        {
                            "pair_index": int(pair_index),
                            "left": str(left[0]),
                            "right": str(right[0]),
                            "satisfied": bool(satisfied),
                        }
                    )
                    continue

                center_xy = self._entity_center_xy(other_item)
                line_segment = self._line_segment_from_entity(line_item[1])
                if center_xy is None or line_segment is None:
                    raise NotImplementedError(
                        "normal precheck could not resolve line-curve geometry."
                    )
                actual_distance = self._point_segment_distance(
                    line_segment[0],
                    line_segment[1],
                    center_xy,
                )
                tolerance = self._constraint_length_tolerance(sketch, actual_distance)
                details.append(
                    {
                        "pair_index": int(pair_index),
                        "left": str(left[0]),
                        "right": str(right[0]),
                        "actual_distance": float(actual_distance),
                        "tolerance": float(tolerance),
                        "satisfied": bool(actual_distance <= tolerance),
                    }
                )
            return {
                "supported": True,
                "satisfied": all(item["satisfied"] for item in details),
                "details": details,
            }

        if normalized_type == "midpoint":
            kinds = self._normalize_kinds(resolved)
            if len(resolved) == 2:
                if kinds.count("point") != 1:
                    raise NotImplementedError(
                        "midpoint precheck with two entities expects [point, curve]."
                    )
                point_item = resolved[0] if kinds[0] == "point" else resolved[1]
                curve_item = resolved[1] if kinds[0] == "point" else resolved[0]
                point_xy = self._point_xy_from_item(point_item)
                if point_xy is None:
                    raise NotImplementedError(
                        "midpoint precheck could not resolve point geometry."
                    )
                if curve_item[2].get("kind") == "line":
                    segment = self._line_segment_from_entity(curve_item[1])
                    if segment is None:
                        raise NotImplementedError(
                            "midpoint precheck could not resolve line geometry."
                        )
                    midpoint = (
                        (float(segment[0][0]) + float(segment[1][0])) / 2.0,
                        (float(segment[0][1]) + float(segment[1][1])) / 2.0,
                    )
                elif curve_item[2].get("kind") == "arc":
                    midpoint = self._point2_from_generic_point(
                        getattr(curve_item[1], "midSketchPoint", None)
                    )
                    if midpoint is None:
                        raise NotImplementedError(
                            "midpoint precheck could not resolve arc midpoint geometry."
                        )
                else:
                    raise NotImplementedError(
                        "midpoint precheck currently supports point-line and point-arc only."
                    )
                actual_distance = self._point_distance_xy(point_xy, midpoint)
                tolerance = self._constraint_length_tolerance(sketch, actual_distance)
                return {
                    "supported": True,
                    "satisfied": bool(actual_distance <= tolerance),
                    "details": [
                        {
                            "entity": str(point_item[0]),
                            "curve": str(curve_item[0]),
                            "actual_distance": float(actual_distance),
                            "tolerance": float(tolerance),
                            "satisfied": bool(actual_distance <= tolerance),
                        }
                    ],
                }
            if len(resolved) == 3 and all(kind == "point" for kind in kinds):
                target_xy = self._point_xy_from_item(resolved[0])
                first_xy = self._point_xy_from_item(resolved[1])
                second_xy = self._point_xy_from_item(resolved[2])
                if target_xy is None or first_xy is None or second_xy is None:
                    raise NotImplementedError(
                        "midpoint precheck could not resolve point geometry."
                    )
                midpoint = (
                    (float(first_xy[0]) + float(second_xy[0])) / 2.0,
                    (float(first_xy[1]) + float(second_xy[1])) / 2.0,
                )
                actual_distance = self._point_distance_xy(target_xy, midpoint)
                tolerance = self._constraint_length_tolerance(sketch, actual_distance)
                return {
                    "supported": True,
                    "satisfied": bool(actual_distance <= tolerance),
                    "details": [
                        {
                            "entity": str(resolved[0][0]),
                            "actual_distance": float(actual_distance),
                            "tolerance": float(tolerance),
                            "satisfied": bool(actual_distance <= tolerance),
                        }
                    ],
                }
            raise NotImplementedError(
                "midpoint precheck currently supports either [point, curve] or [point, point, point]."
            )

        if normalized_type == "fixed":
            raise NotImplementedError(
                "fixed precheck is intentionally unsupported because it depends on state, not only geometry."
            )

        if normalized_type == "mirror":
            raise NotImplementedError(
                "mirror precheck is intentionally unsupported because it is a three-entity construction relation."
            )

        raise NotImplementedError(
            f"No geometric precheck is implemented for {normalized_type}."
        )

    def _check_dimension_constraint_satisfied(
        self,
        normalized_type,
        sketch,
        sketch_index,
        resolved,
        value,
        extra,
    ):
        if value is None:
            return {
                "supported": True,
                "satisfied": True,
                "details": [{"mode": "annotation_only", "satisfied": True}],
            }

        target_value = self._evaluate_dimension_target_value(normalized_type, value)
        if target_value is None:
            raise NotImplementedError(
                f"{normalized_type} precheck could not evaluate target value: {value!r}"
            )

        if normalized_type == "angle":
            if len(resolved) != 2 or not all(
                item[2].get("kind") == "line" for item in resolved
            ):
                raise NotImplementedError(
                    "angle precheck currently supports exactly two line entities."
                )
            line1 = self._line_segment_from_entity(resolved[0][1])
            line2 = self._line_segment_from_entity(resolved[1][1])
            if line1 is None or line2 is None:
                raise NotImplementedError(
                    "angle precheck could not resolve line geometry."
                )
            context = self._infer_line_angle_context(line1, line2)
            if context is None:
                raise NotImplementedError(
                    "angle precheck could not infer a stable line-angle context."
                )
            corner_angle = self._angle_between_rays_degrees(
                context["ray1"], context["ray2"]
            )
            if corner_angle is None:
                raise NotImplementedError(
                    "angle precheck could not evaluate the current angle."
                )
            supplementary_angle = 180.0 - float(corner_angle)
            actual_value = min(
                (float(corner_angle), supplementary_angle),
                key=lambda candidate: abs(float(candidate) - float(target_value)),
            )
            tolerance = self._constraint_angle_tolerance_degrees()
            satisfied = self._scalar_values_close(actual_value, target_value, tolerance)
            return {
                "supported": True,
                "satisfied": bool(satisfied),
                "details": [
                    {
                        "actual": float(actual_value),
                        "target": float(target_value),
                        "alternatives": [
                            float(corner_angle),
                            float(supplementary_angle),
                        ],
                        "tolerance": float(tolerance),
                        "satisfied": bool(satisfied),
                    }
                ],
            }

        if normalized_type == "diameter":
            if len(resolved) != 1 or resolved[0][2].get("kind") not in {
                "circle",
                "arc",
            }:
                raise NotImplementedError(
                    "diameter precheck currently supports one circle/arc."
                )
            radius = self._curve_radius(resolved[0][1])
            if radius is None:
                raise NotImplementedError("diameter precheck could not resolve radius.")
            actual_value = float(radius) * 2.0
        elif normalized_type == "radius":
            if len(resolved) != 1 or resolved[0][2].get("kind") not in {
                "circle",
                "arc",
            }:
                raise NotImplementedError(
                    "radius precheck currently supports one circle/arc."
                )
            radius = self._curve_radius(resolved[0][1])
            if radius is None:
                raise NotImplementedError("radius precheck could not resolve radius.")
            actual_value = float(radius)
        elif normalized_type in {"majorradius", "minorradius"}:
            if len(resolved) != 1:
                raise NotImplementedError(
                    f"{normalized_type} precheck currently supports one ellipse entity."
                )
            actual_value = self._ellipse_radius_value(resolved[0], normalized_type)
            if actual_value is None:
                raise NotImplementedError(
                    f"{normalized_type} precheck could not resolve ellipse radius."
                )
        elif normalized_type in {"length", "distance"}:
            actual_value, mode = self._measure_distance_like_value(
                normalized_type,
                sketch,
                sketch_index,
                resolved,
                target_value,
                extra,
            )
        else:
            raise NotImplementedError(
                f"No dimension precheck is implemented for {normalized_type}."
            )

        tolerance = self._constraint_length_tolerance(
            sketch, actual_value, target_value
        )
        satisfied = self._scalar_values_close(actual_value, target_value, tolerance)
        detail = {
            "actual": float(actual_value),
            "target": float(target_value),
            "tolerance": float(tolerance),
            "satisfied": bool(satisfied),
        }
        if normalized_type in {"length", "distance"}:
            detail["mode"] = mode
        return {
            "supported": True,
            "satisfied": bool(satisfied),
            "details": [detail],
        }

    def _measure_distance_like_value(
        self,
        normalized_type,
        sketch,
        sketch_index,
        resolved,
        target_value,
        extra,
    ):
        direction = self._normalize_distance_direction(extra)
        if len(resolved) == 1:
            if resolved[0][2].get("kind") != "line":
                raise NotImplementedError(
                    f"{normalized_type} precheck with one entity currently supports line only."
                )
            line_item = resolved[0]
            start_point, end_point = self._resolve_line_distance_points(line_item)
            start_xy = self._point2_from_generic_point(start_point)
            end_xy = self._point2_from_generic_point(end_point)
            if start_xy is None or end_xy is None:
                raise NotImplementedError(
                    f"{normalized_type} precheck could not resolve line endpoints."
                )
            if direction in {"HORIZONTAL", "VERTICAL"}:
                axis = self._constraint_axis_in_sketch(
                    sketch,
                    sketch_index,
                    extra,
                    "horizontal" if direction == "HORIZONTAL" else "vertical",
                )
                actual_value = abs(
                    self._projection_on_axis(end_xy, axis)
                    - self._projection_on_axis(start_xy, axis)
                )
                return float(actual_value), "line_directional_span"
            actual_value = self._point_distance_xy(start_xy, end_xy)
            return float(actual_value), "line_span"

        if len(resolved) != 2:
            raise NotImplementedError(
                f"{normalized_type} precheck currently supports one line or exactly two entities."
            )

        if direction in {"HORIZONTAL", "VERTICAL"}:
            axis = self._constraint_axis_in_sketch(
                sketch,
                sketch_index,
                extra,
                "horizontal" if direction == "HORIZONTAL" else "vertical",
            )
            first_anchor = self._resolve_directional_distance_anchor(
                sketch,
                resolved[0][1],
                direction,
                self._distance_halfspace(extra, "halfSpace0"),
                extra,
                sketch_index=sketch_index,
            )
            second_anchor = self._resolve_directional_distance_anchor(
                sketch,
                resolved[1][1],
                direction,
                self._distance_halfspace(extra, "halfSpace1"),
                extra,
                sketch_index=sketch_index,
            )
            first_xy = self._point2_from_generic_point(first_anchor)
            second_xy = self._point2_from_generic_point(second_anchor)
            if first_xy is None or second_xy is None:
                raise NotImplementedError(
                    "directional distance precheck could not resolve anchor points."
                )
            actual_value = abs(
                self._projection_on_axis(second_xy, axis)
                - self._projection_on_axis(first_xy, axis)
            )
            return float(actual_value), "directional_distance"

        first_is_line = self._entity_is_kind(
            resolved[0], resolved[0][1], "line", self._is_sketch_line
        )
        second_is_line = self._entity_is_kind(
            resolved[1], resolved[1][1], "line", self._is_sketch_line
        )
        first_is_curve = self._entity_is_tangent_curve(resolved[0], resolved[0][1])
        second_is_curve = self._entity_is_tangent_curve(resolved[1], resolved[1][1])

        if isinstance(extra, dict) and extra.get("concentric"):
            if not first_is_curve or not second_is_curve:
                raise NotImplementedError(
                    "concentric distance precheck expects two circular entities."
                )
            first_center = self._entity_center_xy(resolved[0])
            second_center = self._entity_center_xy(resolved[1])
            first_radius = self._curve_radius(resolved[0][1])
            second_radius = self._curve_radius(resolved[1][1])
            if (
                first_center is None
                or second_center is None
                or first_radius is None
                or second_radius is None
            ):
                raise NotImplementedError(
                    "concentric distance precheck could not resolve curve geometry."
                )
            center_distance = self._point_distance_xy(first_center, second_center)
            if center_distance > self._constraint_length_tolerance(
                sketch, center_distance
            ):
                return float(abs(center_distance)), "concentric_center_mismatch"
            return float(
                abs(float(first_radius) - float(second_radius))
            ), "concentric_curve_distance"

        if first_is_line and second_is_curve:
            choice = self._line_curve_tangent_choice(
                resolved[0][1],
                resolved[1][1],
                target_value,
                curve_item=resolved[1],
            )
            if choice is not None and choice.get("selected_support_on_curve") is False:
                choice = self._line_curve_tangent_choice(
                    resolved[0][1],
                    resolved[1][1],
                    None,
                    curve_item=resolved[1],
                )
            if choice is None:
                raise NotImplementedError(
                    "distance precheck could not resolve line-curve tangent choice."
                )
            return float(choice["distance"]), "line_curve_tangent_distance"

        if second_is_line and first_is_curve:
            choice = self._line_curve_tangent_choice(
                resolved[1][1],
                resolved[0][1],
                target_value,
                curve_item=resolved[0],
            )
            if choice is not None and choice.get("selected_support_on_curve") is False:
                choice = self._line_curve_tangent_choice(
                    resolved[1][1],
                    resolved[0][1],
                    None,
                    curve_item=resolved[0],
                )
            if choice is None:
                raise NotImplementedError(
                    "distance precheck could not resolve line-curve tangent choice."
                )
            return float(choice["distance"]), "curve_line_tangent_distance"

        if first_is_curve and second_is_curve:
            choice = self._curve_curve_tangent_choice(
                resolved[0][1],
                resolved[1][1],
                target_value,
            )
            if choice is None:
                raise NotImplementedError(
                    "distance precheck could not resolve curve-curve tangent choice."
                )
            return float(choice["distance"]), "curve_curve_tangent_distance"

        if first_is_line or second_is_line:
            line_item = resolved[0] if first_is_line else resolved[1]
            other_item = resolved[1] if first_is_line else resolved[0]
            actual_value = self._measure_offset_distance(line_item, other_item)
            if actual_value is None:
                raise NotImplementedError(
                    "distance precheck could not resolve offset distance geometry."
                )
            return float(actual_value), "offset_distance"

        if all(item[2].get("kind") == "point" for item in resolved):
            first_xy = self._point_xy_from_item(resolved[0])
            second_xy = self._point_xy_from_item(resolved[1])
            if first_xy is None or second_xy is None:
                raise NotImplementedError(
                    "distance precheck could not resolve point geometry."
                )
            return float(
                self._point_distance_xy(first_xy, second_xy)
            ), "point_point_distance"

        raise NotImplementedError(
            f"{normalized_type} precheck does not yet support entity kinds: "
            f"{resolved[0][2].get('kind')}, {resolved[1][2].get('kind')}"
        )

    def _measure_offset_distance(self, line_item, other_item):
        line_segment = self._line_segment_from_entity(line_item[1])
        if line_segment is None:
            return None

        other_kind = other_item[2].get("kind")
        if other_kind == "point":
            point_xy = self._point_xy_from_item(other_item)
            if point_xy is None:
                return None
            return self._point_line_distance(line_segment[0], line_segment[1], point_xy)

        if other_kind == "line":
            other_segment = self._line_segment_from_entity(other_item[1])
            if other_segment is None:
                return None
            first_direction = self._line_direction_from_item(line_item)
            second_direction = self._line_direction_from_item(other_item)
            if first_direction is None or second_direction is None:
                return None
            if self._directions_parallel(first_direction, second_direction):
                return self._point_line_distance(
                    line_segment[0],
                    line_segment[1],
                    other_segment[0],
                )
            intersection = self._infinite_line_intersection(line_segment, other_segment)
            return 0.0 if intersection is not None else None

        return None

    def inspect_entities(self, entity_ids):
        if not isinstance(entity_ids, list) or not entity_ids:
            raise ValueError("entity_ids must be a non-empty list.")

        entities = []
        for entity_id in entity_ids:
            if not isinstance(entity_id, str) or not entity_id.strip():
                raise ValueError("entity_ids must contain non-empty strings.")
            entity_obj, entry = self.resolve_entity(entity_id)
            geometry = self._inspect_entity_geometry(
                entity_obj,
                entry.get("kind"),
                entry.get("sketch_index"),
            )
            entities.append(
                {
                    "entity_id": entity_id,
                    "kind": entry.get("kind"),
                    "sketch_num": entry.get("sketch_index"),
                    "supported": geometry is not None,
                    "geometry": geometry,
                    "metadata": dict(entry.get("metadata") or {}),
                }
            )

        return {
            "count": len(entities),
            "entities": entities,
        }

    def inspect_sketch_profiles(self, sketch_num=-1):
        sketch, sketch_index = self._get_sketch(sketch_num)
        profiles = []
        for profile_index in range(int(sketch.profiles.count)):
            profile = sketch.profiles.item(profile_index)

            outer_loop_count = 0
            inner_loop_count = 0
            try:
                loops = profile.profileLoops
                for loop_index in range(int(loops.count)):
                    loop = loops.item(loop_index)
                    if bool(getattr(loop, "isOuter", False)):
                        outer_loop_count += 1
                    else:
                        inner_loop_count += 1
            except Exception:
                pass

            bbox_payload = None
            try:
                bbox = profile.boundingBox
                bbox_payload = {
                    "min": [
                        float(bbox.minPoint.x),
                        float(bbox.minPoint.y),
                        float(bbox.minPoint.z),
                    ],
                    "max": [
                        float(bbox.maxPoint.x),
                        float(bbox.maxPoint.y),
                        float(bbox.maxPoint.z),
                    ],
                }
            except Exception:
                pass

            area = None
            centroid_payload = None
            try:
                area_props = profile.areaProperties()
                area = float(getattr(area_props, "area", 0.0))
                centroid = getattr(area_props, "centroid", None)
                if centroid is not None:
                    centroid_payload = [
                        float(centroid.x),
                        float(centroid.y),
                        float(centroid.z),
                    ]
            except Exception:
                pass

            reference_point_payload = None
            try:
                reference_point = self._profile_reference_point(profile)
                reference_point_payload = [
                    float(reference_point.x),
                    float(reference_point.y),
                    float(reference_point.z),
                ]
            except Exception:
                pass

            profiles.append(
                {
                    "profile_index": profile_index,
                    "loop_count": int(outer_loop_count + inner_loop_count),
                    "outer_loop_count": int(outer_loop_count),
                    "inner_loop_count": int(inner_loop_count),
                    "has_inner_loops": bool(inner_loop_count > 0),
                    "area": area,
                    "bbox": bbox_payload,
                    "centroid": centroid_payload,
                    "reference_point": reference_point_payload,
                }
            )

        return {
            "sketch_num": int(sketch_index),
            "profile_count": len(profiles),
            "profiles": profiles,
        }

    def inspect_sketch_geometry(self, sketch_num=-1, exclude_helpers=True):
        sketch, sketch_index = self._get_sketch(sketch_num)
        entities = {}
        registry = getattr(self, "entity_registry", {})
        if isinstance(registry, dict):
            for entity_id, entry in registry.items():
                if not isinstance(entry, dict):
                    continue
                if entry.get("sketch_index") != sketch_index:
                    continue
                kind = str(entry.get("kind") or "")
                if kind in {"constraint", "dimension", "point"}:
                    continue
                metadata = entry.get("metadata")
                if (
                    exclude_helpers
                    and isinstance(metadata, dict)
                    and metadata.get("auto_created_for")
                ):
                    continue

                entity_obj = entry.get("obj")
                if entity_obj is None:
                    continue
                try:
                    is_valid = getattr(entity_obj, "isValid", True)
                except Exception:
                    is_valid = True
                if not is_valid:
                    continue

                geometry = self._inspect_entity_geometry(
                    entity_obj,
                    kind,
                    sketch_index,
                )
                if geometry is None:
                    continue
                bbox_payload = self._entity_bbox_payload(entity_obj)
                if bbox_payload is not None:
                    geometry = dict(geometry)
                    geometry.setdefault("bbox", bbox_payload)
                entities[str(entity_id)] = {
                    "kind": kind,
                    "geometry": geometry,
                }

        return {
            "sketch_num": int(sketch_index),
            "entity_count": len(entities),
            "entities": entities,
        }

    def probe_constraint_effect(
        self,
        constraint_type,
        entities,
        value=None,
        sketch_num=-1,
        text_point=None,
        is_driving=True,
        extra=None,
        follow_up_expression=None,
        displacement_tolerance=1e-6,
    ):
        sketch, sketch_index, _resolved = self._resolve_entities(entities, sketch_num)
        _ = sketch
        maybe_do_events(force=True)
        before_snapshot = self.inspect_sketch_geometry(sketch_index)
        response = self.add_constraint(
            constraint_type=constraint_type,
            entities=entities,
            value=value,
            sketch_num=sketch_index,
            text_point=text_point,
            is_driving=is_driving,
            extra=extra,
        )

        if follow_up_expression is not None:
            dimension_id = None
            if isinstance(response, dict):
                candidate_id = response.get("constraint_id")
                if isinstance(candidate_id, str) and candidate_id.strip():
                    dimension_id = candidate_id
            if not dimension_id:
                raise RuntimeError(
                    "Constraint probe follow-up expression requested but no dimension id was returned."
                )
            self.set_dimension_expression(dimension_id, follow_up_expression)

        maybe_do_events(force=True)
        after_snapshot = self.inspect_sketch_geometry(sketch_index)
        comparison = self._compare_sketch_geometry_snapshots(
            before_snapshot,
            after_snapshot,
            displacement_tolerance=displacement_tolerance,
        )
        return {
            "sketch_num": int(sketch_index),
            "response": response,
            "follow_up_expression": follow_up_expression,
            **comparison,
        }

    def _compare_sketch_geometry_snapshots(
        self,
        before_snapshot,
        after_snapshot,
        displacement_tolerance=1e-6,
    ):
        tolerance = max(0.0, float(displacement_tolerance))
        before_entities = (
            before_snapshot.get("entities")
            if isinstance(before_snapshot, dict)
            else None
        ) or {}
        after_entities = (
            after_snapshot.get("entities") if isinstance(after_snapshot, dict) else None
        ) or {}

        before_ids = set(before_entities.keys())
        after_ids = set(after_entities.keys())
        added_entity_ids = sorted(after_ids - before_ids)
        removed_entity_ids = sorted(before_ids - after_ids)

        changed_entities = []
        max_numeric_delta = 0.0
        infinite_delta = False

        for entity_id in sorted(before_ids & after_ids):
            before_entity = before_entities.get(entity_id) or {}
            after_entity = after_entities.get(entity_id) or {}
            before_kind = before_entity.get("kind")
            after_kind = after_entity.get("kind")
            if before_kind != after_kind:
                changed_entities.append(
                    {
                        "entity_id": entity_id,
                        "kind_before": before_kind,
                        "kind_after": after_kind,
                        "max_delta": None,
                    }
                )
                infinite_delta = True
                continue

            delta = self._snapshot_numeric_delta(
                before_entity.get("geometry"),
                after_entity.get("geometry"),
            )
            if math.isfinite(delta):
                max_numeric_delta = max(max_numeric_delta, float(delta))
            else:
                infinite_delta = True

            if (not math.isfinite(delta)) or float(delta) > tolerance:
                changed_entities.append(
                    {
                        "entity_id": entity_id,
                        "kind": before_kind,
                        "max_delta": None if not math.isfinite(delta) else float(delta),
                    }
                )

        significant_change = bool(
            added_entity_ids or removed_entity_ids or changed_entities
        )
        return {
            "significant_change": significant_change,
            "displacement_tolerance": tolerance,
            "max_numeric_delta": None if infinite_delta else float(max_numeric_delta),
            "changed_entity_count": len(changed_entities),
            "changed_entities": changed_entities,
            "added_entity_ids": added_entity_ids,
            "removed_entity_ids": removed_entity_ids,
        }

    def _snapshot_numeric_delta(self, before_value, after_value):
        if before_value is None or after_value is None:
            return 0.0 if before_value is after_value else math.inf

        if isinstance(before_value, bool) or isinstance(after_value, bool):
            return 0.0 if before_value == after_value else math.inf

        if isinstance(before_value, (int, float)) and isinstance(
            after_value, (int, float)
        ):
            before_float = float(before_value)
            after_float = float(after_value)
            if not math.isfinite(before_float) or not math.isfinite(after_float):
                return math.inf
            return abs(before_float - after_float)

        if isinstance(before_value, str) and isinstance(after_value, str):
            return 0.0 if before_value == after_value else math.inf

        if isinstance(before_value, (list, tuple)) and isinstance(
            after_value, (list, tuple)
        ):
            if len(before_value) != len(after_value):
                return math.inf
            max_delta = 0.0
            for before_item, after_item in zip(before_value, after_value):
                delta = self._snapshot_numeric_delta(before_item, after_item)
                if not math.isfinite(delta):
                    return math.inf
                max_delta = max(max_delta, float(delta))
            return max_delta

        if isinstance(before_value, dict) and isinstance(after_value, dict):
            if set(before_value.keys()) != set(after_value.keys()):
                return math.inf
            max_delta = 0.0
            for key in before_value:
                delta = self._snapshot_numeric_delta(
                    before_value[key],
                    after_value[key],
                )
                if not math.isfinite(delta):
                    return math.inf
                max_delta = max(max_delta, float(delta))
            return max_delta

        return 0.0 if before_value == after_value else math.inf

    def _canonicalize_direction2(self, vector, tol=1e-9):
        normalized = self._normalize_vector2(vector, tol=tol)
        if normalized is None:
            return None
        x_value = float(normalized[0])
        y_value = float(normalized[1])
        if abs(x_value) > tol:
            sign = 1.0 if x_value >= 0.0 else -1.0
        elif abs(y_value) > tol:
            sign = 1.0 if y_value >= 0.0 else -1.0
        else:
            sign = 1.0
        return [x_value * sign, y_value * sign]

    def _point2_from_generic_point(self, point_obj):
        if point_obj is None:
            return None
        point_xy = self._point2_from_sketch_point(point_obj)
        if point_xy is not None:
            return point_xy
        geometry = getattr(point_obj, "geometry", None)
        if geometry is None:
            geometry = point_obj
        if not hasattr(geometry, "x") or not hasattr(geometry, "y"):
            return None
        return (float(geometry.x), float(geometry.y))

    def _vector2_from_generic_vector(self, vector_obj):
        if vector_obj is None:
            return None
        if hasattr(vector_obj, "geometry"):
            vector_obj = vector_obj.geometry
        if not hasattr(vector_obj, "x") or not hasattr(vector_obj, "y"):
            return None
        return (float(vector_obj.x), float(vector_obj.y))

    def _entity_bbox_payload(self, entity_obj):
        bbox = getattr(entity_obj, "boundingBox", None)
        if bbox is None:
            return None
        min_point = getattr(bbox, "minPoint", None)
        max_point = getattr(bbox, "maxPoint", None)
        if min_point is None or max_point is None:
            return None
        try:
            return {
                "min": [float(min_point.x), float(min_point.y), float(min_point.z)],
                "max": [float(max_point.x), float(max_point.y), float(max_point.z)],
            }
        except Exception:
            return None

    def _ellipse_major_axis_payload(self, entity_obj, center_xy):
        if center_xy is None:
            return None

        major_axis_line = getattr(entity_obj, "majorAxisLine", None)
        if major_axis_line is not None:
            segment = self._line_segment_from_entity(major_axis_line)
            if segment is not None:
                candidate_vectors = [
                    (
                        float(point[0]) - float(center_xy[0]),
                        float(point[1]) - float(center_xy[1]),
                    )
                    for point in segment
                ]
                best_vector = max(
                    candidate_vectors,
                    key=lambda value: math.hypot(float(value[0]), float(value[1])),
                )
                canonical = self._canonicalize_direction2(best_vector)
                if canonical is not None:
                    return canonical

        geometry = getattr(entity_obj, "geometry", None)
        for attr_name in ("majorAxis", "majorAxisVector", "referenceVector"):
            vector_xy = self._vector2_from_generic_vector(
                getattr(entity_obj, attr_name, None)
            )
            if vector_xy is None and geometry is not None:
                vector_xy = self._vector2_from_generic_vector(
                    getattr(geometry, attr_name, None)
                )
            canonical = (
                self._canonicalize_direction2(vector_xy)
                if vector_xy is not None
                else None
            )
            if canonical is not None:
                return canonical
        return None

    def _collect_curve_point_payload(self, entity_obj, attr_name):
        collection = getattr(entity_obj, attr_name, None)
        if collection is None:
            return None

        try:
            count = int(collection.count)
        except Exception:
            return None

        points = []
        for index in range(count):
            try:
                point_obj = collection.item(index)
            except Exception:
                continue
            point_xy = self._point2_from_generic_point(point_obj)
            if point_xy is None:
                continue
            points.append([float(point_xy[0]), float(point_xy[1])])
        return points or None

    def _inspect_ellipse_geometry(self, entity_obj, include_endpoints=False):
        center_point = getattr(entity_obj, "centerSketchPoint", None)
        center_xy = self._point2_from_sketch_point(center_point)
        geometry = getattr(entity_obj, "geometry", None)

        major_radius = getattr(entity_obj, "majorRadius", None)
        if major_radius is None and geometry is not None:
            major_radius = getattr(geometry, "majorRadius", None)

        minor_radius = getattr(entity_obj, "minorRadius", None)
        if minor_radius is None and geometry is not None:
            minor_radius = getattr(geometry, "minorRadius", None)

        payload = {}
        if center_xy is not None:
            payload["center"] = [float(center_xy[0]), float(center_xy[1])]
        if major_radius is not None:
            payload["major_radius"] = float(major_radius)
        if minor_radius is not None:
            payload["minor_radius"] = float(minor_radius)

        major_axis = self._ellipse_major_axis_payload(entity_obj, center_xy)
        if major_axis is not None:
            payload["major_axis"] = major_axis

        if include_endpoints:
            start_point = getattr(entity_obj, "startSketchPoint", None)
            end_point = getattr(entity_obj, "endSketchPoint", None)
            start_xy = self._point2_from_sketch_point(start_point)
            end_xy = self._point2_from_sketch_point(end_point)
            if start_xy is not None:
                payload["start"] = [float(start_xy[0]), float(start_xy[1])]
            if end_xy is not None:
                payload["end"] = [float(end_xy[0]), float(end_xy[1])]

        return payload or None

    def _inspect_nurbs_geometry(self, entity_obj):
        payload = {}

        fit_points = self._collect_curve_point_payload(entity_obj, "fitPoints")
        if fit_points is not None:
            payload["fit_points"] = fit_points

        control_points = self._collect_curve_point_payload(entity_obj, "controlPoints")
        if control_points is not None:
            payload["control_points"] = control_points

        start_point, end_point = self._safe_get_curve_endpoints(entity_obj)
        start_xy = self._point2_from_sketch_point(start_point)
        end_xy = self._point2_from_sketch_point(end_point)
        if start_xy is not None:
            payload["start"] = [float(start_xy[0]), float(start_xy[1])]
        if end_xy is not None:
            payload["end"] = [float(end_xy[0]), float(end_xy[1])]

        return payload or None

    def _inspect_entity_geometry(self, entity_obj, kind, sketch_index):
        if kind == "point":
            point_xy = self._point2_from_sketch_point(entity_obj)
            if point_xy is None:
                return None
            return {
                "point": [float(point_xy[0]), float(point_xy[1])],
            }

        if kind == "line":
            segment = self._line_segment_from_entity(entity_obj)
            if segment is None:
                return None
            return {
                "start": [float(segment[0][0]), float(segment[0][1])],
                "end": [float(segment[1][0]), float(segment[1][1])],
            }

        if kind == "circle":
            center_point = getattr(entity_obj, "centerSketchPoint", None)
            center_xy = self._point2_from_sketch_point(center_point)
            radius = getattr(entity_obj, "radius", None)
            if center_xy is None or radius is None:
                return None
            return {
                "center": [float(center_xy[0]), float(center_xy[1])],
                "radius": float(radius),
            }

        if kind == "arc":
            start_point = getattr(entity_obj, "startSketchPoint", None)
            end_point = getattr(entity_obj, "endSketchPoint", None)
            center_point = getattr(entity_obj, "centerSketchPoint", None)
            start_xy = self._point2_from_sketch_point(start_point)
            end_xy = self._point2_from_sketch_point(end_point)
            center_xy = self._point2_from_sketch_point(center_point)
            radius = getattr(entity_obj, "radius", None)
            if (
                start_xy is None
                or end_xy is None
                or center_xy is None
                or radius is None
            ):
                return None
            return {
                "start": [float(start_xy[0]), float(start_xy[1])],
                "end": [float(end_xy[0]), float(end_xy[1])],
                "center": [float(center_xy[0]), float(center_xy[1])],
                "radius": float(radius),
            }

        if kind == "ellipse":
            return self._inspect_ellipse_geometry(entity_obj)

        if kind == "elliptical_arc":
            return self._inspect_ellipse_geometry(entity_obj, include_endpoints=True)

        if kind == "nurbs":
            return self._inspect_nurbs_geometry(entity_obj)

        if kind == "dimension":
            parameter = getattr(entity_obj, "parameter", None)
            if parameter is None:
                return None
            payload = {
                "expression": str(getattr(parameter, "expression", "")),
                "unit": str(getattr(parameter, "unit", "")),
            }
            try:
                payload["value"] = float(parameter.value)
            except Exception:
                pass
            if sketch_index is not None:
                payload["sketch_num"] = int(sketch_index)
            return payload

        return None

    def _find_parameter(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string.")
        parameter = self.design.allParameters.itemByName(name)
        if parameter is None:
            raise KeyError(f"Parameter not found: {name}")
        return parameter

    def create_user_parameter(self, name, expression, units="", comment=""):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string.")
        value_input = adsk.core.ValueInput.createByString(str(expression))
        user_parameter = self.design.userParameters.add(
            name.strip(),
            value_input,
            units if units is not None else "",
            comment if comment is not None else "",
        )
        if user_parameter is None:
            raise RuntimeError("Failed to create user parameter.")
        return {
            "name": user_parameter.name,
            "expression": user_parameter.expression,
            "unit": user_parameter.unit,
            "value": float(user_parameter.value),
        }

    def set_parameter_expression(self, name, expression):
        parameter = self._find_parameter(name)
        parameter.expression = str(expression)
        return {
            "name": parameter.name,
            "expression": parameter.expression,
            "unit": parameter.unit,
            "value": float(parameter.value),
        }

    def get_parameter(self, name):
        parameter = self._find_parameter(name)
        return {
            "name": parameter.name,
            "expression": parameter.expression,
            "unit": parameter.unit,
            "value": float(parameter.value),
        }

    def set_dimension_expression(self, dimension_id, expression):
        if not isinstance(dimension_id, str) or not dimension_id.strip():
            raise ValueError("dimension_id must be a non-empty string.")
        dimension_obj, entry = self.resolve_entity(
            dimension_id,
            expected_kinds={"dimension"},
        )
        if not hasattr(dimension_obj, "parameter") or dimension_obj.parameter is None:
            raise RuntimeError(f"Dimension does not expose parameter: {dimension_id}")

        # 尺寸联动的核心是编辑参数表达式，而不是直接写死数值
        dimension_obj.parameter.expression = str(expression)
        return {
            "dimension_id": dimension_id,
            "parameter_name": dimension_obj.parameter.name,
            "expression": dimension_obj.parameter.expression,
            "value": float(dimension_obj.parameter.value),
            "sketch_num": entry.get("sketch_index"),
        }

    def get_model_metrics(self):
        body_count = int(self.rootComp.bRepBodies.count)
        solid_body_count = 0
        total_volume = 0.0
        min_x = min_y = min_z = None
        max_x = max_y = max_z = None

        for i in range(body_count):
            body = self.rootComp.bRepBodies.item(i)
            if body is None or not body.isValid:
                continue
            if body.isSolid:
                solid_body_count += 1
                total_volume += float(body.volume)
            bbox = body.boundingBox
            if bbox is None:
                continue
            if min_x is None:
                min_x, min_y, min_z = bbox.minPoint.x, bbox.minPoint.y, bbox.minPoint.z
                max_x, max_y, max_z = bbox.maxPoint.x, bbox.maxPoint.y, bbox.maxPoint.z
            else:
                min_x = min(min_x, bbox.minPoint.x)
                min_y = min(min_y, bbox.minPoint.y)
                min_z = min(min_z, bbox.minPoint.z)
                max_x = max(max_x, bbox.maxPoint.x)
                max_y = max(max_y, bbox.maxPoint.y)
                max_z = max(max_z, bbox.maxPoint.z)

        bbox_dict = None
        if min_x is not None:
            bbox_dict = {
                "min": [float(min_x), float(min_y), float(min_z)],
                "max": [float(max_x), float(max_y), float(max_z)],
            }
        return {
            "body_count": body_count,
            "solid_body_count": int(solid_body_count),
            "total_volume": float(total_volume),
            "bounding_box": bbox_dict,
        }
