import math

import adsk.core
import adsk.fusion


class CADPrimitives:
    def _to_point3d(self, point):
        if len(point) == 2:
            return adsk.core.Point3D.create(point[0], point[1], 0.0)
        if len(point) == 3:
            return adsk.core.Point3D.create(point[0], point[1], point[2])
        raise ValueError(f"Point must have length 2 or 3, got: {point}")

    def _to_sketch_point3d(self, sketch, point):
        sketch_index = self._find_sketch_index(sketch)
        if sketch_index is not None and isinstance(point, (list, tuple)):
            return self._get_mapped_point(sketch_index, point)
        point_3d = self._to_point3d(point)
        if len(point) == 3:
            try:
                sketch_point = sketch.modelToSketchSpace(point_3d)
                if sketch_point is not None:
                    return sketch_point
            except Exception:
                pass
        return point_3d

    def _to_sketch_vector3d(self, sketch, vector, normalize=False):
        sketch_index = self._find_sketch_index(sketch)
        if sketch_index is not None and isinstance(vector, (list, tuple)):
            return self._get_mapped_vector(sketch_index, vector, normalize=normalize)
        if len(vector) == 2:
            vx = float(vector[0])
            vy = float(vector[1])
            vz = 0.0
        elif len(vector) == 3:
            origin = adsk.core.Point3D.create(0.0, 0.0, 0.0)
            target = adsk.core.Point3D.create(
                float(vector[0]), float(vector[1]), float(vector[2])
            )
            try:
                sketch_origin = sketch.modelToSketchSpace(origin)
                sketch_target = sketch.modelToSketchSpace(target)
                vx = float(sketch_target.x - sketch_origin.x)
                vy = float(sketch_target.y - sketch_origin.y)
                vz = float(sketch_target.z - sketch_origin.z)
            except Exception:
                vx = float(vector[0])
                vy = float(vector[1])
                vz = float(vector[2])
        else:
            raise ValueError(f"Vector must have length 2 or 3, got: {vector}")

        if normalize:
            length = math.sqrt(vx * vx + vy * vy + vz * vz)
            if length <= 1e-12:
                raise ValueError("vector length must be > 0")
            vx /= length
            vy /= length
            vz /= length
        return adsk.core.Vector3D.create(vx, vy, vz)

    def _to_xy(self, point):
        if len(point) < 2:
            raise ValueError(f"Point must have at least 2 values, got: {point}")
        return float(point[0]), float(point[1])

    def _point_components(self, point):
        if point is None:
            raise ValueError("Point cannot be None.")
        if isinstance(point, (list, tuple)):
            values = [float(value) for value in point]
            if len(values) == 2:
                values.append(0.0)
            if len(values) != 3:
                raise ValueError(f"Point must have length 2 or 3, got: {point}")
            return tuple(values)
        if hasattr(point, "x") and hasattr(point, "y"):
            return (
                float(point.x),
                float(point.y),
                float(getattr(point, "z", 0.0)),
            )
        raise TypeError(f"Unsupported point type: {type(point)!r}")

    def _distance_squared(self, left, right):
        left_x, left_y, left_z = self._point_components(left)
        right_x, right_y, right_z = self._point_components(right)
        dx = left_x - right_x
        dy = left_y - right_y
        dz = left_z - right_z
        return dx * dx + dy * dy + dz * dz

    def _analyze_three_point_arc_points(
        self,
        start_point,
        along_point,
        end_point,
        *,
        point_tol=1e-8,
        relative_height_tol=1e-4,
        absolute_height_tol=1e-5,
    ):
        x1, y1, _z1 = self._point_components(start_point)
        x2, y2, _z2 = self._point_components(along_point)
        x3, y3, _z3 = self._point_components(end_point)

        start_to_middle = math.hypot(x2 - x1, y2 - y1)
        middle_to_end = math.hypot(x3 - x2, y3 - y2)
        chord = math.hypot(x3 - x1, y3 - y1)
        if min(start_to_middle, middle_to_end, chord) <= point_tol:
            return {
                "reason": "duplicate_or_zero_length_points",
                "chord": chord,
                "height": 0.0,
            }

        double_area = abs((x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1))
        height = double_area / chord
        if height <= max(absolute_height_tol, chord * relative_height_tol):
            return {
                "reason": "nearly_collinear_three_point_arc",
                "chord": chord,
                "height": height,
            }
        return None

    def _point_match_distance_squared(self, sketch_point, *candidates):
        comparisons = []
        for attr_name in ("worldGeometry", "geometry"):
            geometry = getattr(sketch_point, attr_name, None)
            if geometry is not None:
                comparisons.append(geometry)
        if not comparisons:
            comparisons.append(sketch_point)

        distances = []
        for comparison in comparisons:
            for candidate in candidates:
                if candidate is None:
                    continue
                distances.append(self._distance_squared(comparison, candidate))
        if not distances:
            raise ValueError("At least one candidate point is required.")
        return min(distances)

    def _order_curve_endpoints(
        self,
        curve_start_point,
        curve_end_point,
        requested_start_point,
        requested_end_point,
        requested_start_world=None,
        requested_end_world=None,
    ):
        direct_score = self._point_match_distance_squared(
            curve_start_point,
            requested_start_point,
            requested_start_world,
        ) + self._point_match_distance_squared(
            curve_end_point,
            requested_end_point,
            requested_end_world,
        )
        swapped_score = self._point_match_distance_squared(
            curve_start_point,
            requested_end_point,
            requested_end_world,
        ) + self._point_match_distance_squared(
            curve_end_point,
            requested_start_point,
            requested_start_world,
        )
        if direct_score <= swapped_score:
            return curve_start_point, curve_end_point
        return curve_end_point, curve_start_point

    def _deg_to_rad(self, degrees):
        return math.radians(float(degrees))

    def _rotate_to_local(self, x, y, angle_rad):
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        return cos_a * x + sin_a * y, -sin_a * x + cos_a * y

    def _rotate_to_world(self, x, y, angle_rad):
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        return cos_a * x - sin_a * y, sin_a * x + cos_a * y

    def _default_knots(self, control_count, degree, periodic):
        if periodic:
            # Periodic NURBS need an explicit knot vector; clamped defaults are only valid for open curves.
            raise ValueError("knots is required when periodic=True.")

        interior_count = control_count - degree - 1
        knots = [0.0] * (degree + 1)
        if interior_count > 0:
            step = 1.0 / (interior_count + 1)
            knots.extend(step * i for i in range(1, interior_count + 1))
        knots.extend([1.0] * (degree + 1))
        return knots

    def _controls_are_equivalent(self, left, right, tolerance_squared=1e-18):
        return self._distance_squared(left, right) <= float(tolerance_squared)

    def _repair_nurbs_for_fusion(self, degree, periodic, controls, weights, knots):
        degree = int(degree)
        if bool(periodic):
            return degree, controls, weights, knots, None
        if not isinstance(controls, list) or len(controls) < 2:
            return degree, controls, weights, knots, None

        # Fusion can accept an open spline whose endpoint controls collapse after rounding,
        # but then fail to form a stable sketch profile from it. Trim only duplicate runs
        # at the open curve boundaries and rebuild a simple clamped knot vector.
        start_keep = 0
        while start_keep + 1 < len(controls) and self._controls_are_equivalent(
            controls[start_keep],
            controls[start_keep + 1],
        ):
            start_keep += 1

        end_keep = len(controls) - 1
        while end_keep - 1 >= start_keep and self._controls_are_equivalent(
            controls[end_keep],
            controls[end_keep - 1],
        ):
            end_keep -= 1

        if start_keep == 0 and end_keep == len(controls) - 1:
            return degree, controls, weights, knots, None

        repaired_controls = list(controls[start_keep : end_keep + 1])
        if len(repaired_controls) < 2:
            raise ValueError(
                "NURBS endpoint repair collapsed the curve to fewer than 2 controls."
            )

        repaired_degree = min(degree, len(repaired_controls) - 1)
        if repaired_degree < 1:
            raise ValueError("NURBS endpoint repair produced an invalid degree.")

        repaired_weights = weights
        if weights is not None and len(weights) > 0:
            if len(weights) != len(controls):
                raise ValueError(
                    f"weights length must equal controls length ({len(controls)}), got: {len(weights)}"
                )
            repaired_weights = list(weights[start_keep : end_keep + 1])

        repaired_knots = self._default_knots(
            control_count=len(repaired_controls),
            degree=repaired_degree,
            periodic=False,
        )
        repair_info = {
            "trimmed_duplicate_start_controls": int(start_keep),
            "trimmed_duplicate_end_controls": int(len(controls) - 1 - end_keep),
            "original_degree": int(degree),
            "repaired_degree": int(repaired_degree),
            "original_control_count": len(controls),
            "repaired_control_count": len(repaired_controls),
            "rebuilt_knots": True,
        }
        return (
            repaired_degree,
            repaired_controls,
            repaired_weights,
            repaired_knots,
            repair_info,
        )

    def _is_invalid_periodic_nurbs_error(self, error):
        message = str(error).lower()
        return (
            "invalid argument knots" in message
            or "invalid argument controlpoints" in message
        )

    def _periodic_nurbs_endpoint_gap_is_small(
        self,
        controls,
        abs_tol=0.25,
        relative_tol=2e-2,
    ):
        if not isinstance(controls, list) or len(controls) < 2:
            return False

        min_x = max_x = float(controls[0][0])
        min_y = max_y = float(controls[0][1])
        min_z = max_z = float(controls[0][2]) if len(controls[0]) >= 3 else 0.0
        for point in controls[1:]:
            px = float(point[0])
            py = float(point[1])
            pz = float(point[2]) if len(point) >= 3 else 0.0
            min_x = min(min_x, px)
            max_x = max(max_x, px)
            min_y = min(min_y, py)
            max_y = max(max_y, py)
            min_z = min(min_z, pz)
            max_z = max(max_z, pz)

        dx = max_x - min_x
        dy = max_y - min_y
        dz = max_z - min_z
        extent = math.sqrt(dx * dx + dy * dy + dz * dz)
        tolerance = max(float(abs_tol), extent * float(relative_tol))
        return (
            self._distance_squared(controls[0], controls[-1]) <= tolerance * tolerance
        )

    def _periodic_nurbs_fallback_variants(self, degree, controls, weights, knots):
        degree = int(degree)
        if not isinstance(controls, list) or not isinstance(knots, list):
            return []

        variants = [
            {
                "degree": degree,
                "periodic": False,
                "controls": list(controls),
                "weights": weights,
                "knots": list(knots),
                "repair_info": {
                    "periodic_fallback": "reuse_knots_as_nonperiodic",
                    "original_periodic": True,
                    "repaired_periodic": False,
                    "original_control_count": len(controls),
                    "repaired_control_count": len(controls),
                    "original_knot_count": len(knots),
                    "repaired_knot_count": len(knots),
                },
            }
        ]

        wrap_count = max(0, degree - 1)
        if wrap_count > 0 and len(knots) == len(controls) + 2 * degree:
            repaired_controls = list(controls) + list(controls[:wrap_count])
            repaired_weights = weights
            if weights is not None and len(weights) > 0:
                if len(weights) != len(controls):
                    raise ValueError(
                        f"weights length must equal controls length ({len(controls)}), got: {len(weights)}"
                    )
                repaired_weights = list(weights) + list(weights[:wrap_count])
            variants.append(
                {
                    "degree": degree,
                    "periodic": False,
                    "controls": repaired_controls,
                    "weights": repaired_weights,
                    "knots": list(knots),
                    "repair_info": {
                        "periodic_fallback": "wrap_controls_and_use_nonperiodic_knots",
                        "original_periodic": True,
                        "repaired_periodic": False,
                        "wrapped_control_count": int(wrap_count),
                        "original_control_count": len(controls),
                        "repaired_control_count": len(repaired_controls),
                        "original_knot_count": len(knots),
                        "repaired_knot_count": len(knots),
                    },
                }
            )

        trimmed_knots = list(knots[1:-1])
        minimum_knot_count = len(controls) + degree + 1
        if len(trimmed_knots) >= minimum_knot_count:
            variants.append(
                {
                    "degree": degree,
                    "periodic": False,
                    "controls": list(controls),
                    "weights": weights,
                    "knots": trimmed_knots,
                    "repair_info": {
                        "periodic_fallback": "trim_terminal_knots_and_use_nonperiodic",
                        "original_periodic": True,
                        "repaired_periodic": False,
                        "trimmed_terminal_knot_count": 2,
                        "original_control_count": len(controls),
                        "repaired_control_count": len(controls),
                        "original_knot_count": len(knots),
                        "repaired_knot_count": len(trimmed_knots),
                    },
                }
            )

        variants.append(
            {
                "degree": degree,
                "periodic": False,
                "controls": list(controls),
                "weights": list(weights) if isinstance(weights, list) else weights,
                "knots": self._default_knots(
                    control_count=len(controls),
                    degree=degree,
                    periodic=False,
                ),
                "repair_info": {
                    "periodic_fallback": "rebuild_clamped_knots_as_nonperiodic",
                    "original_periodic": True,
                    "repaired_periodic": False,
                    "original_control_count": len(controls),
                    "repaired_control_count": len(controls),
                    "original_knot_count": len(knots),
                    "repaired_knot_count": int(len(controls) + degree + 1),
                },
            }
        )

        if len(knots) >= len(controls) + degree + 2:
            appended_controls = list(controls) + [list(controls[0])]
            appended_weights = weights
            if weights is not None and len(weights) > 0:
                if len(weights) != len(controls):
                    raise ValueError(
                        f"weights length must equal controls length ({len(controls)}), got: {len(weights)}"
                    )
                appended_weights = list(weights) + [weights[0]]

            variants.append(
                {
                    "degree": degree,
                    "periodic": False,
                    "controls": appended_controls,
                    "weights": appended_weights,
                    "knots": list(knots),
                    "repair_info": {
                        "periodic_fallback": "append_first_control_and_use_nonperiodic_knots",
                        "original_periodic": True,
                        "repaired_periodic": False,
                        "appended_first_control": True,
                        "original_control_count": len(controls),
                        "repaired_control_count": len(appended_controls),
                        "original_knot_count": len(knots),
                        "repaired_knot_count": len(knots),
                    },
                }
            )

        if self._periodic_nurbs_endpoint_gap_is_small(controls):
            snapped_controls = list(controls)
            snapped_controls[-1] = list(controls[0])
            variants.append(
                {
                    "degree": degree,
                    "periodic": False,
                    "controls": snapped_controls,
                    "weights": list(weights) if isinstance(weights, list) else weights,
                    "knots": self._default_knots(
                        control_count=len(snapped_controls),
                        degree=degree,
                        periodic=False,
                    ),
                    "repair_info": {
                        "periodic_fallback": "snap_terminal_control_and_rebuild_clamped_knots",
                        "original_periodic": True,
                        "repaired_periodic": False,
                        "snapped_terminal_control": True,
                        "original_control_count": len(controls),
                        "repaired_control_count": len(snapped_controls),
                        "original_knot_count": len(knots),
                        "repaired_knot_count": int(len(snapped_controls) + degree + 1),
                    },
                }
            )

        if wrap_count > 0:
            rebuilt_controls = list(controls) + list(controls[:wrap_count])
            rebuilt_weights = weights
            if weights is not None and len(weights) > 0:
                if len(weights) != len(controls):
                    raise ValueError(
                        f"weights length must equal controls length ({len(controls)}), got: {len(weights)}"
                    )
                rebuilt_weights = list(weights) + list(weights[:wrap_count])
            variants.append(
                {
                    "degree": degree,
                    "periodic": False,
                    "controls": rebuilt_controls,
                    "weights": rebuilt_weights,
                    "knots": self._default_knots(
                        control_count=len(rebuilt_controls),
                        degree=degree,
                        periodic=False,
                    ),
                    "repair_info": {
                        "periodic_fallback": "wrap_controls_and_rebuild_clamped_knots",
                        "original_periodic": True,
                        "repaired_periodic": False,
                        "wrapped_control_count": int(wrap_count),
                        "original_control_count": len(controls),
                        "repaired_control_count": len(rebuilt_controls),
                        "original_knot_count": len(knots),
                        "repaired_knot_count": int(len(rebuilt_controls) + degree + 1),
                    },
                }
            )

        return variants

    def _build_nurbs_curve(
        self, degree, periodic, controls, weights, knots, sketch=None
    ):
        degree = int(degree)
        if degree < 1:
            raise ValueError(f"degree must be >= 1, got: {degree}")

        if not isinstance(controls, list) or len(controls) < degree + 1:
            raise ValueError(
                f"controls length must be >= degree+1 ({degree + 1}), got: {len(controls) if isinstance(controls, list) else controls}"
            )

        if sketch is None:
            control_points = [self._to_point3d(point) for point in controls]
        else:
            control_points = [
                self._to_sketch_point3d(sketch, point) for point in controls
            ]
        control_count = len(control_points)

        if knots is None or len(knots) == 0:
            knots = self._default_knots(control_count, degree, bool(periodic))
        else:
            knots = [float(k) for k in knots]

        minimum_knot_count = control_count + degree + 1
        if len(knots) < minimum_knot_count:
            raise ValueError(
                f"knots length must be >= control_count + degree + 1 ({minimum_knot_count}), got: {len(knots)}"
            )

        if weights is None or len(weights) == 0:
            nurbs_curve = adsk.core.NurbsCurve3D.createNonRational(
                control_points, degree, knots, bool(periodic)
            )
        else:
            if len(weights) != control_count:
                raise ValueError(
                    f"weights length must equal controls length ({control_count}), got: {len(weights)}"
                )
            rational_weights = [float(w) for w in weights]
            nurbs_curve = adsk.core.NurbsCurve3D.createRational(
                control_points, degree, knots, rational_weights, bool(periodic)
            )

        if nurbs_curve is None:
            raise RuntimeError(
                "Failed to create NurbsCurve3D. Check controls/knots/weights validity."
            )
        return nurbs_curve

    def _solve_elliptical_arc_candidates(
        self, start_xy, end_xy, major, minor, angle_rad
    ):
        sx, sy = start_xy
        ex, ey = end_xy

        sx_local, sy_local = self._rotate_to_local(sx, sy, angle_rad)
        ex_local, ey_local = self._rotate_to_local(ex, ey, angle_rad)

        s1x, s1y = sx_local / major, sy_local / minor
        s2x, s2y = ex_local / major, ey_local / minor

        dx = s2x - s1x
        dy = s2y - s1y
        chord = math.hypot(dx, dy)

        if chord < 1e-9:
            raise ValueError("elliptical arc start and end are too close.")
        if chord > 2.0 + 1e-9:
            raise ValueError(
                "No valid ellipse center for the given start/end/major/minor."
            )

        chord = min(chord, 2.0)
        mx = (s1x + s2x) * 0.5
        my = (s1y + s2y) * 0.5
        ux = dx / chord
        uy = dy / chord
        nx = -uy
        ny = ux
        h_sq = max(0.0, 1.0 - (chord * 0.5) ** 2)
        h = math.sqrt(h_sq)

        candidates = []
        for sign in (1.0, -1.0):
            tx = mx + sign * nx * h
            ty = my + sign * ny * h

            center_local_x = tx * major
            center_local_y = ty * minor
            center_world_x, center_world_y = self._rotate_to_world(
                center_local_x, center_local_y, angle_rad
            )

            q1x = s1x - tx
            q1y = s1y - ty
            q2x = s2x - tx
            q2y = s2y - ty

            theta_start = math.atan2(q1y, q1x)
            theta_end = math.atan2(q2y, q2x)
            delta_ccw = (theta_end - theta_start) % (2.0 * math.pi)
            delta_cw = (2.0 * math.pi - delta_ccw) % (2.0 * math.pi)

            candidates.append(
                {
                    "center_x": center_world_x,
                    "center_y": center_world_y,
                    "theta_start": theta_start,
                    "delta_ccw": delta_ccw,
                    "delta_cw": delta_cw,
                }
            )

        return candidates

    def _select_elliptical_arc_solution(self, candidates, large_arc, sweep):
        desired_large = bool(large_arc)
        desired_ccw = bool(sweep)
        filtered = []

        for candidate in candidates:
            if desired_ccw:
                sweep_angle = candidate["delta_ccw"]
                if sweep_angle <= 1e-9:
                    continue
            else:
                sweep_angle = -candidate["delta_cw"]
                if abs(sweep_angle) <= 1e-9:
                    continue

            is_large = abs(sweep_angle) > math.pi + 1e-9
            if is_large == desired_large:
                return candidate, sweep_angle
            filtered.append((candidate, sweep_angle))

        if not filtered:
            raise RuntimeError("Failed to choose elliptical arc candidate.")

        if desired_large:
            return max(filtered, key=lambda item: abs(item[1]))
        return min(filtered, key=lambda item: abs(item[1]))

    def add_line(self, startPoint, endPoint, sketch_num=-1):
        sketch, sketch_index = self._get_sketch(sketch_num)
        startPoint_3D = self._to_sketch_point3d(sketch, startPoint)
        endPoint_3D = self._to_sketch_point3d(sketch, endPoint)
        lines = sketch.sketchCurves.sketchLines
        line = lines.addByTwoPoints(startPoint_3D, endPoint_3D)

        line_id = self._register_entity(line, "line", sketch_index)
        start_point_id = self._register_entity(
            line.startSketchPoint, "point", sketch_index
        )
        end_point_id = self._register_entity(line.endSketchPoint, "point", sketch_index)
        line_entry = self.entity_registry.get(line_id)
        if line_entry is not None:
            metadata = line_entry.get("metadata", {})
            metadata.update(
                {
                    "start_point_id": start_point_id,
                    "end_point_id": end_point_id,
                }
            )
            line_entry["metadata"] = metadata
        return {
            "line_id": line_id,
            "start_point_id": start_point_id,
            "end_point_id": end_point_id,
            "sketch_num": sketch_index,
        }

    def add_circle(self, centerPoint, radius, sketch_num=-1):
        sketch, sketch_index = self._get_sketch(sketch_num)
        centerPoint_3D = self._to_sketch_point3d(sketch, centerPoint)
        circles = sketch.sketchCurves.sketchCircles
        circle = circles.addByCenterRadius(centerPoint_3D, radius)

        circle_id = self._register_entity(circle, "circle", sketch_index)
        center_point_id = self._register_entity(
            circle.centerSketchPoint, "point", sketch_index
        )
        circle_entry = self.entity_registry.get(circle_id)
        if circle_entry is not None:
            metadata = dict(circle_entry.get("metadata") or {})
            metadata["center_point_id"] = center_point_id
            circle_entry["metadata"] = metadata
        return {
            "circle_id": circle_id,
            "center_point_id": center_point_id,
            "sketch_num": sketch_index,
        }

    def add_arc(self, startPoint, alongPoint, endPoint, sketch_num=-1):
        sketch, sketch_index = self._get_sketch(sketch_num)
        startPoint_world = self._to_point3d(startPoint)
        startPoint_3D = self._to_sketch_point3d(sketch, startPoint)
        alongPoint_3D = self._to_sketch_point3d(sketch, alongPoint)
        endPoint_world = self._to_point3d(endPoint)
        endPoint_3D = self._to_sketch_point3d(sketch, endPoint)

        degeneracy = self._analyze_three_point_arc_points(
            startPoint_3D,
            alongPoint_3D,
            endPoint_3D,
        )
        if (
            degeneracy is not None
            and degeneracy.get("reason") == "nearly_collinear_three_point_arc"
        ):
            lines = sketch.sketchCurves.sketchLines
            line = lines.addByTwoPoints(startPoint_3D, endPoint_3D)
            line_id = self._register_entity(line, "line", sketch_index)
            start_point_id = self._register_entity(
                line.startSketchPoint, "point", sketch_index
            )
            end_point_id = self._register_entity(
                line.endSketchPoint, "point", sketch_index
            )

            response = {
                "arc_id": line_id,
                "start_point_id": start_point_id,
                "end_point_id": end_point_id,
                "sketch_num": sketch_index,
            }

            mid_point = sketch.sketchPoints.add(alongPoint_3D)
            sketch.geometricConstraints.addCoincident(mid_point, line)
            mid_point_id = self._register_entity(mid_point, "point", sketch_index)
            response["mid_point_id"] = mid_point_id

            line_entry = self.entity_registry.get(line_id)
            if line_entry is not None:
                metadata = dict(line_entry.get("metadata") or {})
                metadata.update(
                    {
                        "start_point_id": start_point_id,
                        "end_point_id": end_point_id,
                        "mid_point_id": mid_point_id,
                        "degenerated_from": "arc",
                        "degenerate_reason": degeneracy.get("reason"),
                    }
                )
                line_entry["metadata"] = metadata

            return response

        arcs = sketch.sketchCurves.sketchArcs
        arc = arcs.addByThreePoints(startPoint_3D, alongPoint_3D, endPoint_3D)

        arc_id = self._register_entity(arc, "arc", sketch_index)

        fused_start, fused_end = self._order_curve_endpoints(
            arc.startSketchPoint,
            arc.endSketchPoint,
            startPoint_3D,
            endPoint_3D,
            requested_start_world=startPoint_world,
            requested_end_world=endPoint_world,
        )
        start_point_id = self._register_entity(fused_start, "point", sketch_index)
        end_point_id = self._register_entity(fused_end, "point", sketch_index)

        response = {
            "arc_id": arc_id,
            "start_point_id": start_point_id,
            "end_point_id": end_point_id,
            "sketch_num": sketch_index,
        }

        if hasattr(arc, "centerSketchPoint") and arc.centerSketchPoint is not None:
            center_point_id = self._register_entity(
                arc.centerSketchPoint, "point", sketch_index
            )
            response["center_point_id"] = center_point_id

        mid_point = getattr(arc, "midSketchPoint", None)
        if mid_point is None:
            mid_point = sketch.sketchPoints.add(alongPoint_3D)
            sketch.geometricConstraints.addCoincident(mid_point, arc)

        mid_point_id = self._register_entity(mid_point, "point", sketch_index)
        response["mid_point_id"] = mid_point_id

        arc_entry = self.entity_registry.get(arc_id)
        if arc_entry is not None:
            metadata = dict(arc_entry.get("metadata") or {})
            metadata.update(
                {
                    "start_point_id": start_point_id,
                    "end_point_id": end_point_id,
                    "mid_point_id": mid_point_id,
                }
            )
            center_point_id = response.get("center_point_id")
            if center_point_id is not None:
                metadata["center_point_id"] = center_point_id
            arc_entry["metadata"] = metadata

        return response

    def add_ellipse(
        self,
        centerPoint,
        major,
        minor,
        angle,
        sketch_num=-1,
        majorAxisPoint=None,
        passPoint=None,
    ):
        sketch, sketch_index = self._get_sketch(sketch_num)

        major = float(major)
        minor = float(minor)
        if major <= 0 or minor <= 0:
            raise ValueError(
                f"major/minor must be positive. got major={major}, minor={minor}"
            )

        center = self._to_sketch_point3d(sketch, centerPoint)
        if majorAxisPoint is not None and passPoint is not None:
            major_axis_point = self._to_sketch_point3d(sketch, majorAxisPoint)
            pass_point = self._to_sketch_point3d(sketch, passPoint)
        else:
            angle_rad = self._deg_to_rad(angle)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)
            major_axis_point = adsk.core.Point3D.create(
                center.x + major * cos_a, center.y + major * sin_a, center.z
            )
            pass_point = adsk.core.Point3D.create(
                center.x - minor * sin_a, center.y + minor * cos_a, center.z
            )

        ellipse = sketch.sketchCurves.sketchEllipses.add(
            center, major_axis_point, pass_point
        )
        ellipse_id = self._register_entity(ellipse, "ellipse", sketch_index)
        center_point_id = self._register_entity(
            ellipse.centerSketchPoint, "point", sketch_index
        )

        return {
            "ellipse_id": ellipse_id,
            "center_point_id": center_point_id,
            "sketch_num": sketch_index,
        }

    def add_elliptical_arc(
        self,
        startPoint,
        endPoint,
        major,
        minor,
        angle,
        large_arc=False,
        sweep=True,
        sketch_num=-1,
        centerPoint=None,
        majorAxisVector=None,
        minorAxisVector=None,
        startAngle=None,
        sweepAngle=None,
    ):
        sketch, sketch_index = self._get_sketch(sketch_num)
        startPoint_world = self._to_point3d(startPoint)
        startPoint_3D = self._to_sketch_point3d(sketch, startPoint)
        endPoint_world = self._to_point3d(endPoint)
        endPoint_3D = self._to_sketch_point3d(sketch, endPoint)

        major = float(major)
        minor = float(minor)
        if major <= 0 or minor <= 0:
            raise ValueError(
                f"major/minor must be positive. got major={major}, minor={minor}"
            )

        if all(
            value is not None
            for value in (
                centerPoint,
                majorAxisVector,
                minorAxisVector,
                startAngle,
                sweepAngle,
            )
        ):
            center_point = self._to_sketch_point3d(sketch, centerPoint)
            major_axis = self._to_sketch_vector3d(
                sketch, majorAxisVector, normalize=False
            )
            minor_axis = self._to_sketch_vector3d(
                sketch, minorAxisVector, normalize=False
            )
            theta_start = float(startAngle)
            sweep_angle = float(sweepAngle)
        else:
            start_xy = self._to_xy(startPoint)
            end_xy = self._to_xy(endPoint)
            angle_rad = self._deg_to_rad(angle)
            candidates = self._solve_elliptical_arc_candidates(
                start_xy=start_xy,
                end_xy=end_xy,
                major=major,
                minor=minor,
                angle_rad=angle_rad,
            )
            selected, sweep_angle = self._select_elliptical_arc_solution(
                candidates=candidates,
                large_arc=large_arc,
                sweep=sweep,
            )
            center_point = adsk.core.Point3D.create(
                selected["center_x"], selected["center_y"], 0.0
            )
            major_axis = adsk.core.Vector3D.create(
                math.cos(angle_rad) * major, math.sin(angle_rad) * major, 0.0
            )
            minor_axis = adsk.core.Vector3D.create(
                -math.sin(angle_rad) * minor, math.cos(angle_rad) * minor, 0.0
            )
            theta_start = selected["theta_start"]

        elliptical_arc = sketch.sketchCurves.sketchEllipticalArcs.addByAngle(
            center_point,
            major_axis,
            minor_axis,
            theta_start,
            sweep_angle,
        )

        arc_id = self._register_entity(elliptical_arc, "elliptical_arc", sketch_index)
        fused_start, fused_end = self._order_curve_endpoints(
            elliptical_arc.startSketchPoint,
            elliptical_arc.endSketchPoint,
            startPoint_3D,
            endPoint_3D,
            requested_start_world=startPoint_world,
            requested_end_world=endPoint_world,
        )
        start_point_id = self._register_entity(fused_start, "point", sketch_index)
        end_point_id = self._register_entity(fused_end, "point", sketch_index)
        center_point_id = self._register_entity(
            elliptical_arc.centerSketchPoint, "point", sketch_index
        )

        return {
            "elliptical_arc_id": arc_id,
            "start_point_id": start_point_id,
            "end_point_id": end_point_id,
            "center_point_id": center_point_id,
            "sketch_num": sketch_index,
        }

    def add_nurbs(
        self,
        degree,
        periodic=False,
        controls=None,
        weights=None,
        knots=None,
        sketch_num=-1,
        closed=None,
    ):
        sketch, sketch_index = self._get_sketch(sketch_num)
        is_periodic = bool(periodic) if periodic is not None else bool(closed)
        degree, controls, weights, knots, repair_info = self._repair_nurbs_for_fusion(
            degree=degree,
            periodic=is_periodic,
            controls=controls,
            weights=weights,
            knots=knots,
        )

        build_periodic = is_periodic
        try:
            nurbs_curve = self._build_nurbs_curve(
                degree=degree,
                periodic=build_periodic,
                controls=controls,
                weights=weights,
                knots=knots,
                sketch=sketch,
            )
        except RuntimeError as ex:
            if not (is_periodic and self._is_invalid_periodic_nurbs_error(ex)):
                raise

            last_error = ex
            for variant in self._periodic_nurbs_fallback_variants(
                degree=degree,
                controls=controls,
                weights=weights,
                knots=knots,
            ):
                try:
                    nurbs_curve = self._build_nurbs_curve(
                        degree=variant["degree"],
                        periodic=variant["periodic"],
                        controls=variant["controls"],
                        weights=variant["weights"],
                        knots=variant["knots"],
                        sketch=sketch,
                    )
                    degree = variant["degree"]
                    build_periodic = variant["periodic"]
                    controls = variant["controls"]
                    weights = variant["weights"]
                    knots = variant["knots"]
                    combined_info = dict(repair_info or {})
                    combined_info.update(variant["repair_info"])
                    repair_info = combined_info
                    break
                except Exception as candidate_ex:
                    last_error = candidate_ex
            else:
                raise last_error
        sketch_spline = sketch.sketchCurves.sketchFixedSplines.addByNurbsCurve(
            nurbs_curve
        )

        nurbs_id = self._register_entity(sketch_spline, "nurbs", sketch_index)
        response = {"nurbs_id": nurbs_id, "sketch_num": sketch_index}

        start_point, end_point = self._safe_get_curve_endpoints(sketch_spline)
        if start_point is not None:
            response["start_point_id"] = self._register_entity(
                start_point, "point", sketch_index
            )
        if end_point is not None:
            response["end_point_id"] = self._register_entity(
                end_point, "point", sketch_index
            )

        nurbs_entry = self.entity_registry.get(nurbs_id)
        if nurbs_entry is not None:
            metadata = nurbs_entry.get("metadata", {})
            if "start_point_id" in response:
                metadata["start_point_id"] = response["start_point_id"]
            if "end_point_id" in response:
                metadata["end_point_id"] = response["end_point_id"]
            if repair_info is not None:
                metadata["repair_info"] = repair_info
            nurbs_entry["metadata"] = metadata

        return response
