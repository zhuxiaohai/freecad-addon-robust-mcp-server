import math
from collections import defaultdict

import adsk.core
import adsk.fusion


class CADChecklist:
    """Unified checklist checker for user-intent-driven validation.

    Purpose:
    - Accept a structured checklist from upstream intent parsing.
    - Extract model facts from Fusion entities.
    - Return per-item pass/fail/unknown with route hints for retry logic.
    """

    DEFAULT_LENGTH_TOL = 1e-4
    DEFAULT_ANGLE_TOL_DEG = 0.1

    FEATURE_ALIAS = {
        "extrude": "extrude",
        "extrusion": "extrude",
        "revolve": "revolve",
        "fillet": "fillet",
        "chamfer": "chamfer",
        "helix": "helix_sweep",
        "helix_sweep": "helix_sweep",
        "sweep": "helix_sweep",
        "hole": "hole",
    }

    CONSTRAINT_ALIAS = {
        "parallel": "parallel",
        "perpendicular": "perpendicular",
        "horizontal": "horizontal",
        "vertical": "vertical",
        "equal": "equal",
        "tangent": "tangent",
        "normal": "normal",
        "coincident": "coincident",
        "concentric": "concentric",
        "fixed": "fixed",
        "fix": "fixed",
        "midpoint": "midpoint",
        "angle": "angle",
        "diameter": "diameter",
        "radius": "radius",
        "distance": "distance",
    }

    RELATION_ALIAS = {
        "coaxial": "coaxial",
        "同轴": "coaxial",
        "concentric": "concentric",
        "同心": "concentric",
        "mirror_symmetry": "mirror_symmetry",
        "symmetry": "mirror_symmetry",
        "对称": "mirror_symmetry",
        "equiangular_distribution": "equiangular_distribution",
        "等角分布": "equiangular_distribution",
        "equidistant_distribution": "equidistant_distribution",
        "等距分布": "equidistant_distribution",
    }

    def check_model(self, checklist, options=None):
        """Evaluate all checklist items against current Fusion model.

        Inputs:
        - checklist: dict(list schema) or direct list of items.
        - options: optional tolerance and strategy settings.

        Output:
        - status: pass/fail/unknown (overall)
        - item_results: list with pass/fail/unknown per item
        - summary: aggregate counts
        - final_route_hint: routing hint for upper workflow
        - raw_extracts: extracted facts useful for debugging
        """
        options = options or {}
        length_tol = float(options.get("length_tolerance", self.DEFAULT_LENGTH_TOL))
        angle_tol_deg = float(
            options.get("angle_tolerance_deg", self.DEFAULT_ANGLE_TOL_DEG)
        )

        normalized = self._normalize_checklist(checklist)
        items = normalized.get("items", [])

        feature_inventory = self._extract_feature_inventory()
        constraint_inventory = self._extract_constraint_inventory()
        parameter_inventory = self._extract_parameter_inventory()
        geometry_cache = {}

        item_results = []
        for index, item in enumerate(items, start=1):
            result = self._evaluate_item(
                item=item,
                index=index,
                feature_inventory=feature_inventory,
                constraint_inventory=constraint_inventory,
                parameter_inventory=parameter_inventory,
                geometry_cache=geometry_cache,
                length_tol=length_tol,
                angle_tol_deg=angle_tol_deg,
            )
            item_results.append(result)

        summary = {
            "total": len(item_results),
            "pass": len([x for x in item_results if x["status"] == "pass"]),
            "fail": len([x for x in item_results if x["status"] == "fail"]),
            "unknown": len([x for x in item_results if x["status"] == "unknown"]),
        }

        if summary["fail"] > 0:
            status = "fail"
        elif summary["unknown"] > 0:
            status = "unknown"
        else:
            status = "pass"

        final_route_hint = self._final_route_hint(status, item_results)

        return {
            "status": status,
            "summary": summary,
            "item_results": item_results,
            "final_route_hint": final_route_hint,
            "raw_extracts": {
                "feature_inventory": feature_inventory,
                "constraint_inventory": constraint_inventory,
                "parameter_inventory": parameter_inventory,
                "geometry_cache": geometry_cache,
            },
        }

    def _normalize_checklist(self, checklist):
        if checklist is None:
            return {"version": "1.0", "source": "empty", "items": []}
        if isinstance(checklist, list):
            return {"version": "1.0", "source": "list", "items": checklist}
        if isinstance(checklist, dict):
            items = checklist.get("items", [])
            if not isinstance(items, list):
                items = []
            result = dict(checklist)
            result["items"] = items
            return result
        raise ValueError("checklist must be dict/list/None.")

    def _evaluate_item(
        self,
        item,
        index,
        feature_inventory,
        constraint_inventory,
        parameter_inventory,
        geometry_cache,
        length_tol,
        angle_tol_deg,
    ):
        item = item if isinstance(item, dict) else {}
        category = str(item.get("category", "")).strip().lower()
        item_id = str(item.get("id") or f"ITEM_{index:03d}")
        expected = item.get("expected", {})

        if category == "feature":
            status, reason_code, actual, route_hint = self._check_feature_item(
                item, feature_inventory, length_tol
            )
        elif category == "constraint_relation":
            status, reason_code, actual, route_hint = self._check_constraint_item(
                item, constraint_inventory, length_tol
            )
        elif category == "parameter":
            status, reason_code, actual, route_hint = self._check_parameter_item(
                item, parameter_inventory, length_tol
            )
        elif category == "geometry_relation":
            status, reason_code, actual, route_hint = (
                self._check_geometry_relation_item(
                    item, geometry_cache, length_tol, angle_tol_deg
                )
            )
        elif category == "unknown_candidate":
            status, reason_code, actual, route_hint = (
                "unknown",
                "UNKNOWN_CANDIDATE",
                {"topic": item.get("topic", "")},
                "to_visual_validation",
            )
        else:
            status, reason_code, actual, route_hint = (
                "unknown",
                "UNSUPPORTED_CATEGORY",
                {"category": category},
                "to_visual_validation",
            )

        return {
            "id": item_id,
            "category": category,
            "status": status,
            "reason_code": reason_code,
            "expected": expected if isinstance(expected, dict) else {},
            "actual": actual if isinstance(actual, dict) else {"value": actual},
            "route_hint": route_hint,
        }

    def _check_feature_item(self, item, feature_inventory, length_tol):
        feature_key = item.get("feature_type") or item.get("target") or item.get("name")
        feature_type = self._normalize_feature_type(feature_key)
        expected = (
            item.get("expected", {}) if isinstance(item.get("expected"), dict) else {}
        )
        counts = feature_inventory.get("counts", {})
        parameters = feature_inventory.get("parameters", {})
        count = int(counts.get(feature_type, 0))

        check_mode = str(item.get("check", "exists")).strip().lower()
        expected_exists = bool(expected.get("exists", True))
        expected_count = self._expected_count(expected)
        min_count = expected.get("min_count")

        if feature_type not in counts:
            return (
                "unknown",
                "FEATURE_TYPE_UNSUPPORTED",
                {"feature_type": feature_type, "known_types": list(counts.keys())},
                "to_visual_validation",
            )

        if check_mode in {"exists", "presence"}:
            if expected_exists and count <= 0:
                return (
                    "fail",
                    "FEATURE_MISSING",
                    {"feature_type": feature_type, "count": count},
                    "to_planning",
                )
            if not expected_exists and count > 0:
                return (
                    "fail",
                    "FEATURE_UNEXPECTED_PRESENT",
                    {"feature_type": feature_type, "count": count},
                    "to_planning",
                )
        else:
            if expected_count is not None and count != expected_count:
                return (
                    "fail",
                    "FEATURE_COUNT_MISMATCH",
                    {
                        "feature_type": feature_type,
                        "count": count,
                        "expected_count": expected_count,
                    },
                    "to_planning",
                )
            if min_count is not None and count < int(min_count):
                return (
                    "fail",
                    "FEATURE_COUNT_BELOW_MIN",
                    {
                        "feature_type": feature_type,
                        "count": count,
                        "min_count": int(min_count),
                    },
                    "to_planning",
                )

        expected_params = expected.get("parameters", {})
        if expected_params and isinstance(expected_params, dict):
            actual_param_bucket = parameters.get(feature_type, {})
            for key, target in expected_params.items():
                actual_values = actual_param_bucket.get(str(key), [])
                if not actual_values:
                    return (
                        "unknown",
                        "FEATURE_PARAMETER_UNMEASURABLE",
                        {"feature_type": feature_type, "parameter": key},
                        "to_visual_validation",
                    )
                if not self._has_close_value(actual_values, target, length_tol):
                    return (
                        "fail",
                        "FEATURE_PARAMETER_MISMATCH",
                        {
                            "feature_type": feature_type,
                            "parameter": key,
                            "expected_value": target,
                            "actual_values": actual_values,
                        },
                        "to_executable",
                    )

        return (
            "pass",
            "FEATURE_OK",
            {"feature_type": feature_type, "count": count},
            "none",
        )

    def _check_constraint_item(self, item, constraint_inventory, length_tol):
        constraint_key = (
            item.get("constraint_type") or item.get("target") or item.get("name")
        )
        constraint_type = self._normalize_constraint_type(constraint_key)
        expected = (
            item.get("expected", {}) if isinstance(item.get("expected"), dict) else {}
        )
        counts = constraint_inventory.get("counts", {})
        values = constraint_inventory.get("values", {})
        count = int(counts.get(constraint_type, 0))

        expected_exists = bool(expected.get("exists", True))
        expected_count = self._expected_count(expected)
        expected_value = expected.get("value")

        if constraint_type not in counts:
            return (
                "unknown",
                "CONSTRAINT_TYPE_UNSUPPORTED",
                {
                    "constraint_type": constraint_type,
                    "known_types": list(counts.keys()),
                },
                "to_visual_validation",
            )

        if expected_exists and count <= 0:
            return (
                "fail",
                "CONSTRAINT_MISSING",
                {"constraint_type": constraint_type, "count": count},
                "to_executable",
            )
        if (not expected_exists) and count > 0:
            return (
                "fail",
                "CONSTRAINT_UNEXPECTED_PRESENT",
                {"constraint_type": constraint_type, "count": count},
                "to_executable",
            )
        if expected_count is not None and count != expected_count:
            return (
                "fail",
                "CONSTRAINT_COUNT_MISMATCH",
                {
                    "constraint_type": constraint_type,
                    "count": count,
                    "expected_count": expected_count,
                },
                "to_executable",
            )

        if expected_value is not None:
            actual_values = values.get(constraint_type, [])
            if not actual_values:
                return (
                    "unknown",
                    "CONSTRAINT_VALUE_UNMEASURABLE",
                    {"constraint_type": constraint_type},
                    "to_visual_validation",
                )
            if not self._has_close_value(actual_values, expected_value, length_tol):
                return (
                    "fail",
                    "CONSTRAINT_VALUE_MISMATCH",
                    {
                        "constraint_type": constraint_type,
                        "expected_value": expected_value,
                        "actual_values": actual_values,
                    },
                    "to_executable",
                )

        return (
            "pass",
            "CONSTRAINT_OK",
            {"constraint_type": constraint_type, "count": count},
            "none",
        )

    def _check_parameter_item(self, item, parameter_inventory, length_tol):
        expected = (
            item.get("expected", {}) if isinstance(item.get("expected"), dict) else {}
        )
        parameter_key = item.get("parameter_key")
        param_name = (
            item.get("parameter_name")
            or parameter_key
            or expected.get("name")
            or item.get("name")
        )
        expected_value = expected.get("value", item.get("value"))
        expected_expression = expected.get("expression")

        items = parameter_inventory.get("items", [])
        by_name = parameter_inventory.get("by_name", {})

        actual = {}
        if param_name:
            key = str(param_name).strip().lower()
            matched = by_name.get(key)
            if matched is None:
                fuzzy = [p for p in items if key in p["name"].lower()]
                if len(fuzzy) == 1:
                    matched = fuzzy[0]
                elif len(fuzzy) > 1:
                    matched = fuzzy[0]
                    actual["fuzzy_candidates"] = [x["name"] for x in fuzzy]
            # Key fallback: intent often gives semantic keys (e.g. diameter/radius) while Fusion uses d1/d2 names.
            # If a numeric target exists, defer to value-based matching instead of hard-failing on missing name.
            if matched is None and expected_value is None:
                return (
                    "fail",
                    "PARAMETER_MISSING",
                    {"parameter_name": param_name},
                    "to_executable",
                )
            if matched is not None:
                actual.update(matched)
        else:
            matched = None

        if expected_expression is not None:
            expr_actual = "" if matched is None else str(matched.get("expression", ""))
            if str(expected_expression).strip() != expr_actual.strip():
                return (
                    "fail",
                    "PARAMETER_EXPRESSION_MISMATCH",
                    {
                        "parameter_name": param_name,
                        "expected_expression": expected_expression,
                        "actual_expression": expr_actual,
                    },
                    "to_executable",
                )

        if expected_value is not None:
            candidates = []
            if matched is not None:
                if "value" in matched:
                    candidates.append(float(matched["value"]))
            else:
                candidates.extend(parameter_inventory.get("all_values", []))
            if not candidates:
                return (
                    "unknown",
                    "PARAMETER_VALUE_UNMEASURABLE",
                    {"parameter_name": param_name},
                    "to_visual_validation",
                )
            if not self._has_close_value(candidates, expected_value, length_tol):
                return (
                    "fail",
                    "PARAMETER_VALUE_MISMATCH",
                    {
                        "parameter_name": param_name,
                        "parameter_key": parameter_key,
                        "expected_value": expected_value,
                        "actual_values": candidates,
                    },
                    "to_executable",
                )

        return (
            "pass",
            "PARAMETER_OK",
            {"parameter_name": param_name or "any"},
            "none",
        )

    def _check_geometry_relation_item(
        self, item, geometry_cache, length_tol, angle_tol_deg
    ):
        relation_key = (
            item.get("relation_type") or item.get("target") or item.get("name")
        )
        relation_type = self._normalize_relation_type(relation_key)
        expected = (
            item.get("expected", {}) if isinstance(item.get("expected"), dict) else {}
        )
        expected_exists = bool(expected.get("exists", True))

        if relation_type == "coaxial":
            probe = geometry_cache.get("coaxial")
            if probe is None:
                probe = self._detect_coaxial_relation(length_tol, angle_tol_deg)
                geometry_cache["coaxial"] = probe
        elif relation_type == "concentric":
            probe = geometry_cache.get("concentric")
            if probe is None:
                probe = self._detect_concentric_relation(length_tol, angle_tol_deg)
                geometry_cache["concentric"] = probe
        elif relation_type == "mirror_symmetry":
            probe = geometry_cache.get("mirror_symmetry")
            if probe is None:
                probe = self._detect_mirror_symmetry(length_tol)
                geometry_cache["mirror_symmetry"] = probe
        elif relation_type == "equiangular_distribution":
            probe = geometry_cache.get("distribution")
            if probe is None:
                probe = self._detect_distribution_relation(length_tol, angle_tol_deg)
                geometry_cache["distribution"] = probe
            probe = {
                "measurable": probe.get("measurable", False),
                "exists": probe.get("equiangular", False),
                "details": probe,
            }
        elif relation_type == "equidistant_distribution":
            probe = geometry_cache.get("distribution")
            if probe is None:
                probe = self._detect_distribution_relation(length_tol, angle_tol_deg)
                geometry_cache["distribution"] = probe
            probe = {
                "measurable": probe.get("measurable", False),
                "exists": probe.get("equidistant", False),
                "details": probe,
            }
        else:
            return (
                "unknown",
                "RELATION_UNSUPPORTED",
                {"relation_type": relation_type},
                "to_visual_validation",
            )

        if not probe.get("measurable", False):
            return (
                "unknown",
                "RELATION_UNMEASURABLE",
                {"relation_type": relation_type, "details": probe.get("details", {})},
                "to_visual_validation",
            )

        actual_exists = bool(probe.get("exists", False))
        if actual_exists != expected_exists:
            return (
                "fail",
                "RELATION_NOT_SATISFIED",
                {
                    "relation_type": relation_type,
                    "expected_exists": expected_exists,
                    "actual_exists": actual_exists,
                    "details": probe.get("details", {}),
                },
                "to_planning",
            )

        return (
            "pass",
            "RELATION_OK",
            {"relation_type": relation_type, "details": probe.get("details", {})},
            "none",
        )

    def _extract_feature_inventory(self):
        features = self.rootComp.features
        hole_feature_count = self._safe_count(getattr(features, "holeFeatures", None))
        hole_like_count = self._estimate_hole_like_count()
        counts = {
            "extrude": self._safe_count(getattr(features, "extrudeFeatures", None)),
            "revolve": self._safe_count(getattr(features, "revolveFeatures", None)),
            "fillet": self._safe_count(getattr(features, "filletFeatures", None)),
            "chamfer": self._safe_count(getattr(features, "chamferFeatures", None)),
            "helix_sweep": self._safe_count(getattr(features, "sweepFeatures", None)),
            "hole": max(hole_feature_count, hole_like_count),
        }
        parameters = {
            "extrude": self._extract_extrude_parameters(
                getattr(features, "extrudeFeatures", None)
            ),
            "revolve": self._extract_revolve_parameters(
                getattr(features, "revolveFeatures", None)
            ),
            "fillet": self._extract_fillet_parameters(
                getattr(features, "filletFeatures", None)
            ),
            "chamfer": self._extract_chamfer_parameters(
                getattr(features, "chamferFeatures", None)
            ),
            "hole": self._extract_hole_parameters(
                getattr(features, "holeFeatures", None)
            ),
            "helix_sweep": {},
        }
        return {
            "counts": counts,
            "parameters": parameters,
        }

    def _estimate_hole_like_count(self):
        # Fallback for hole detection when model uses cut features instead of HoleFeatures.
        cylindrical_faces = 0
        bodies = self.rootComp.bRepBodies
        for i in range(int(bodies.count)):
            body = bodies.item(i)
            if body is None:
                continue
            faces = body.faces
            for j in range(int(faces.count)):
                face = faces.item(j)
                geometry = getattr(face, "geometry", None)
                object_type = str(getattr(geometry, "objectType", "")).lower()
                if "cylinder" in object_type:
                    cylindrical_faces += 1
        return int(cylindrical_faces // 2)

    def _extract_constraint_inventory(self):
        counts = defaultdict(int)
        values = defaultdict(list)
        sketches = self._all_sketches()

        for sketch in sketches:
            geometric_constraints = getattr(sketch, "geometricConstraints", None)
            if geometric_constraints is not None:
                for i in range(self._safe_count(geometric_constraints)):
                    gc = geometric_constraints.item(i)
                    normalized = self._normalize_constraint_object_type(
                        getattr(gc, "objectType", "")
                    )
                    counts[normalized] += 1

            sketch_dimensions = getattr(sketch, "sketchDimensions", None)
            if sketch_dimensions is not None:
                for i in range(self._safe_count(sketch_dimensions)):
                    dim = sketch_dimensions.item(i)
                    dim_type = self._normalize_dimension_object_type(
                        getattr(dim, "objectType", "")
                    )
                    counts[dim_type] += 1
                    parameter = getattr(dim, "parameter", None)
                    if parameter is None:
                        continue
                    try:
                        values[dim_type].append(float(parameter.value))
                    except Exception:
                        pass

        for key in self.CONSTRAINT_ALIAS.values():
            counts.setdefault(key, 0)
            values.setdefault(key, [])

        return {
            "counts": dict(counts),
            "values": dict(values),
        }

    def _extract_parameter_inventory(self):
        items = []
        by_name = {}
        all_values = []
        parameters = self.design.allParameters
        for i in range(int(parameters.count)):
            parameter = parameters.item(i)
            entry = {
                "name": str(parameter.name),
                "expression": str(parameter.expression),
                "unit": str(parameter.unit),
            }
            try:
                entry["value"] = float(parameter.value)
                all_values.append(entry["value"])
            except Exception:
                pass
            items.append(entry)
            by_name[entry["name"].lower()] = entry
        return {
            "items": items,
            "by_name": by_name,
            "all_values": all_values,
        }

    def _detect_coaxial_relation(self, length_tol, angle_tol_deg):
        axes = self._collect_cylindrical_axes()
        if len(axes) < 2:
            return {
                "measurable": False,
                "exists": False,
                "details": {"axis_count": len(axes)},
            }

        angle_tol_rad = math.radians(float(angle_tol_deg))
        pair_count = 0
        for i in range(len(axes)):
            for j in range(i + 1, len(axes)):
                if self._axes_are_colinear(
                    axes[i], axes[j], length_tol * 10.0, angle_tol_rad
                ):
                    pair_count += 1
        return {
            "measurable": True,
            "exists": pair_count > 0,
            "details": {"axis_count": len(axes), "coaxial_pairs": pair_count},
        }

    def _detect_concentric_relation(self, length_tol, angle_tol_deg):
        circle_centers = self._collect_circle_centers()
        center_pairs = 0
        for i in range(len(circle_centers)):
            for j in range(i + 1, len(circle_centers)):
                if (
                    self._distance(circle_centers[i], circle_centers[j])
                    <= length_tol * 10.0
                ):
                    center_pairs += 1

        if center_pairs > 0:
            return {
                "measurable": True,
                "exists": True,
                "details": {"center_pairs": center_pairs, "source": "sketch_circles"},
            }

        coaxial_probe = self._detect_coaxial_relation(length_tol, angle_tol_deg)
        if not coaxial_probe.get("measurable", False):
            return {
                "measurable": False,
                "exists": False,
                "details": {"center_pairs": 0},
            }
        return {
            "measurable": True,
            "exists": bool(coaxial_probe.get("exists", False)),
            "details": {
                "center_pairs": center_pairs,
                "coaxial_pairs": coaxial_probe.get("details", {}).get(
                    "coaxial_pairs", 0
                ),
                "source": "coaxial_fallback",
            },
        }

    def _detect_mirror_symmetry(self, length_tol):
        vertices = self._collect_vertex_points()
        if len(vertices) < 4:
            return {
                "measurable": False,
                "exists": False,
                "details": {"vertex_count": len(vertices)},
            }

        center = (
            sum(p[0] for p in vertices) / len(vertices),
            sum(p[1] for p in vertices) / len(vertices),
            sum(p[2] for p in vertices) / len(vertices),
        )

        plane_candidates = [
            ("YZ", 0, center[0]),
            ("XZ", 1, center[1]),
            ("XY", 2, center[2]),
        ]

        best_plane = None
        best_score = None
        for plane_name, axis_idx, axis_val in plane_candidates:
            mismatch = self._mirror_mismatch_score(vertices, axis_idx, axis_val)
            if best_score is None or mismatch < best_score:
                best_score = mismatch
                best_plane = plane_name

        threshold = max(length_tol * 10.0, 1e-6)
        return {
            "measurable": True,
            "exists": bool(best_score is not None and best_score <= threshold),
            "details": {
                "best_plane": best_plane,
                "mismatch_score": float(best_score if best_score is not None else 1e9),
                "threshold": float(threshold),
            },
        }

    def _detect_distribution_relation(self, length_tol, angle_tol_deg):
        clouds = self._collect_circle_centers_grouped_by_sketch()
        if not clouds:
            return {
                "measurable": False,
                "equiangular": False,
                "equidistant": False,
                "details": {"reason": "no_circle_clusters"},
            }

        # Core heuristic: choose the richest circle set to evaluate distribution.
        target = max(clouds, key=lambda x: len(x["centers"]))
        points = target["centers"]
        if len(points) < 3:
            return {
                "measurable": False,
                "equiangular": False,
                "equidistant": False,
                "details": {"reason": "insufficient_points", "count": len(points)},
            }

        centroid = (
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
            sum(p[2] for p in points) / len(points),
        )
        radii = [self._distance(p, centroid) for p in points]
        equidistant = (max(radii) - min(radii)) <= max(length_tol * 10.0, 1e-6)

        angles = []
        for p in points:
            angles.append(math.atan2(p[1] - centroid[1], p[0] - centroid[0]))
        angles.sort()
        angle_diffs = []
        for i in range(len(angles)):
            nxt = angles[(i + 1) % len(angles)]
            cur = angles[i]
            diff = (nxt - cur) % (2.0 * math.pi)
            angle_diffs.append(diff)
        target_step = (2.0 * math.pi) / len(angles)
        angle_tol = max(math.radians(angle_tol_deg), 1e-6)
        equiangular = max(abs(x - target_step) for x in angle_diffs) <= angle_tol

        return {
            "measurable": True,
            "equiangular": bool(equiangular),
            "equidistant": bool(equidistant),
            "details": {
                "count": len(points),
                "sketch_index": target["sketch_index"],
                "radius_span": float(max(radii) - min(radii)),
                "max_angle_error": float(
                    max(abs(x - target_step) for x in angle_diffs)
                ),
            },
        }

    def _collect_cylindrical_axes(self):
        axes = []
        bodies = self.rootComp.bRepBodies
        for i in range(int(bodies.count)):
            body = bodies.item(i)
            if body is None:
                continue
            faces = body.faces
            for j in range(int(faces.count)):
                face = faces.item(j)
                geometry = getattr(face, "geometry", None)
                if geometry is None:
                    continue
                object_type = str(getattr(geometry, "objectType", "")).lower()
                if "cylinder" not in object_type:
                    continue
                origin = getattr(geometry, "origin", None)
                axis = getattr(geometry, "axis", None)
                if origin is None or axis is None:
                    continue
                axis_vec = self._vector_tuple(axis)
                if axis_vec is None:
                    continue
                axes.append(
                    {
                        "point": self._point_tuple(origin),
                        "direction": axis_vec,
                    }
                )
        return axes

    def _collect_circle_centers(self):
        points = []
        for sketch in self._all_sketches():
            circles = getattr(
                getattr(sketch, "sketchCurves", None), "sketchCircles", None
            )
            if circles is None:
                continue
            for i in range(self._safe_count(circles)):
                circle = circles.item(i)
                center = getattr(circle, "centerSketchPoint", None)
                if center is None:
                    continue
                geometry = getattr(center, "geometry", None)
                if geometry is None:
                    continue
                points.append(self._point_tuple(geometry))
        return points

    def _collect_circle_centers_grouped_by_sketch(self):
        clusters = []
        sketches = self._all_sketches()
        for idx, sketch in enumerate(sketches):
            centers = []
            circles = getattr(
                getattr(sketch, "sketchCurves", None), "sketchCircles", None
            )
            if circles is None:
                continue
            for i in range(self._safe_count(circles)):
                circle = circles.item(i)
                center = getattr(circle, "centerSketchPoint", None)
                if center is None:
                    continue
                geometry = getattr(center, "geometry", None)
                if geometry is None:
                    continue
                centers.append(self._point_tuple(geometry))
            if centers:
                clusters.append({"sketch_index": idx, "centers": centers})
        return clusters

    def _collect_vertex_points(self):
        vertices = []
        bodies = self.rootComp.bRepBodies
        for i in range(int(bodies.count)):
            body = bodies.item(i)
            if body is None:
                continue
            vtx = body.vertices
            for j in range(int(vtx.count)):
                point = getattr(vtx.item(j), "geometry", None)
                if point is None:
                    continue
                vertices.append(self._point_tuple(point))
        return vertices

    def _mirror_mismatch_score(self, points, axis_idx, axis_val):
        score_sum = 0.0
        for p in points:
            mirrored = [p[0], p[1], p[2]]
            mirrored[axis_idx] = 2.0 * axis_val - mirrored[axis_idx]
            best = None
            for q in points:
                d = self._distance(tuple(mirrored), q)
                if best is None or d < best:
                    best = d
            score_sum += 0.0 if best is None else best
        return score_sum / max(len(points), 1)

    def _axes_are_colinear(self, axis_a, axis_b, length_tol, angle_tol_rad):
        da = axis_a["direction"]
        db = axis_b["direction"]
        cross_mag = self._norm(self._cross(da, db))
        if cross_mag > math.sin(angle_tol_rad):
            return False
        delta = self._sub(axis_b["point"], axis_a["point"])
        line_dist = self._norm(self._cross(delta, da))
        return line_dist <= length_tol

    def _extract_extrude_parameters(self, collection):
        params = {"distance": [], "operation": []}
        if collection is None:
            return params
        for i in range(self._safe_count(collection)):
            feature = collection.item(i)
            params["operation"].append(
                self._feature_operation_name(getattr(feature, "operation", None))
            )
            extent_one = getattr(feature, "extentOne", None)
            if extent_one is None:
                continue
            distance = getattr(extent_one, "distance", None)
            if distance is None:
                continue
            value = getattr(distance, "value", None)
            if value is None:
                continue
            try:
                params["distance"].append(float(value))
            except Exception:
                pass
        return params

    def _extract_revolve_parameters(self, collection):
        params = {"angle": [], "operation": []}
        if collection is None:
            return params
        for i in range(self._safe_count(collection)):
            feature = collection.item(i)
            params["operation"].append(
                self._feature_operation_name(getattr(feature, "operation", None))
            )
            extent = getattr(feature, "extentDefinition", None)
            if extent is None:
                continue
            angle = getattr(extent, "angle", None)
            if angle is None:
                continue
            value = getattr(angle, "value", None)
            if value is None:
                continue
            try:
                params["angle"].append(float(value))
            except Exception:
                pass
        return params

    def _extract_fillet_parameters(self, collection):
        params = {"radius": []}
        if collection is None:
            return params
        for i in range(self._safe_count(collection)):
            feature = collection.item(i)
            edge_sets = getattr(feature, "edgeSets", None)
            if edge_sets is None:
                continue
            for j in range(self._safe_count(edge_sets)):
                edge_set = edge_sets.item(j)
                radius = getattr(edge_set, "radius", None)
                if radius is None:
                    continue
                value = getattr(radius, "value", None)
                if value is None:
                    continue
                try:
                    params["radius"].append(float(value))
                except Exception:
                    pass
        return params

    def _extract_chamfer_parameters(self, collection):
        params = {"distance": []}
        if collection is None:
            return params
        for i in range(self._safe_count(collection)):
            feature = collection.item(i)
            edge_sets = getattr(feature, "edgeSets", None)
            if edge_sets is None:
                continue
            for j in range(self._safe_count(edge_sets)):
                edge_set = edge_sets.item(j)
                distance = getattr(edge_set, "distance", None)
                if distance is None:
                    continue
                value = getattr(distance, "value", None)
                if value is None:
                    continue
                try:
                    params["distance"].append(float(value))
                except Exception:
                    pass
        return params

    def _extract_hole_parameters(self, collection):
        params = {"diameter": []}
        if collection is None:
            return params
        for i in range(self._safe_count(collection)):
            feature = collection.item(i)
            hole_diameter = getattr(feature, "holeDiameter", None)
            if hole_diameter is None:
                continue
            value = getattr(hole_diameter, "value", None)
            if value is None:
                continue
            try:
                params["diameter"].append(float(value))
            except Exception:
                pass
        return params

    def _feature_operation_name(self, operation):
        mapping = {
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation: "NewBody",
            adsk.fusion.FeatureOperations.JoinFeatureOperation: "Join",
            adsk.fusion.FeatureOperations.CutFeatureOperation: "Cut",
            adsk.fusion.FeatureOperations.IntersectFeatureOperation: "Intersect",
        }
        return mapping.get(operation, f"Unknown({operation})")

    def _normalize_feature_type(self, raw):
        key = str(raw or "").strip().lower()
        return self.FEATURE_ALIAS.get(key, key)

    def _normalize_constraint_type(self, raw):
        key = str(raw or "").strip().lower()
        return self.CONSTRAINT_ALIAS.get(key, key)

    def _normalize_relation_type(self, raw):
        key = str(raw or "").strip().lower()
        return self.RELATION_ALIAS.get(key, key)

    def _normalize_constraint_object_type(self, object_type):
        lower = str(object_type or "").lower()
        mapping = {
            "parallelconstraint": "parallel",
            "perpendicularconstraint": "perpendicular",
            "horizontalconstraint": "horizontal",
            "verticalconstraint": "vertical",
            "horizontalpointsconstraint": "horizontal",
            "verticalpointsconstraint": "vertical",
            "equalconstraint": "equal",
            "tangentconstraint": "tangent",
            "normalconstraint": "normal",
            "coincidentconstraint": "coincident",
            "concentriccircleconstraint": "concentric",
            "midpointconstraint": "midpoint",
            "fixedconstraint": "fixed",
        }
        for token, normalized in mapping.items():
            if token in lower:
                return normalized
        return "unknown"

    def _normalize_dimension_object_type(self, object_type):
        lower = str(object_type or "").lower()
        if "angulardimension" in lower:
            return "angle"
        if "diameterdimension" in lower:
            return "diameter"
        if "radialdimension" in lower:
            return "radius"
        if "distancedimension" in lower or "offsetdimension" in lower:
            return "distance"
        return "unknown"

    def _final_route_hint(self, status, item_results):
        if status == "fail":
            fail_items = [x for x in item_results if x.get("status") == "fail"]
            if any(x.get("route_hint") == "to_planning" for x in fail_items):
                return "to_planning"
            if any(x.get("route_hint") == "to_executable" for x in fail_items):
                return "to_executable"
            return "to_planning"
        if status == "unknown":
            return "to_visual_validation"
        return "none"

    def _expected_count(self, expected):
        if "count" in expected:
            try:
                return int(expected["count"])
            except Exception:
                return None
        if "expected_count" in expected:
            try:
                return int(expected["expected_count"])
            except Exception:
                return None
        return None

    def _has_close_value(self, candidates, target, tol):
        try:
            target_val = float(target)
        except Exception:
            return False
        for candidate in candidates:
            try:
                if abs(float(candidate) - target_val) <= float(tol):
                    return True
            except Exception:
                continue
        return False

    def _safe_count(self, collection):
        try:
            return int(collection.count)
        except Exception:
            return 0

    def _all_sketches(self):
        if getattr(self, "sketches", None):
            return list(self.sketches)
        sketches = []
        root_sketches = self.rootComp.sketches
        for i in range(int(root_sketches.count)):
            sketches.append(root_sketches.item(i))
        return sketches

    def _point_tuple(self, point_like):
        return (
            float(getattr(point_like, "x", 0.0)),
            float(getattr(point_like, "y", 0.0)),
            float(getattr(point_like, "z", 0.0)),
        )

    def _vector_tuple(self, vector_like):
        vx = float(getattr(vector_like, "x", 0.0))
        vy = float(getattr(vector_like, "y", 0.0))
        vz = float(getattr(vector_like, "z", 0.0))
        length = math.sqrt(vx * vx + vy * vy + vz * vz)
        if length <= 1e-12:
            return None
        return (vx / length, vy / length, vz / length)

    def _distance(self, p1, p2):
        dx = float(p1[0] - p2[0])
        dy = float(p1[1] - p2[1])
        dz = float(p1[2] - p2[2])
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _sub(self, a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def _cross(self, a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def _norm(self, a):
        return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
