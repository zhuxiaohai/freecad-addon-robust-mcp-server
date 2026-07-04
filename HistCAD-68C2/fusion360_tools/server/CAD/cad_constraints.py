import ast
import math
import re
from fractions import Fraction

import adsk.core
import adsk.fusion


class CADConstraints:
    _DIMENSION_UNIT_SUFFIX_RE = re.compile(
        r"^(?P<body>.+?)(?:\s*\*\s*|\s+)(?P<unit>[A-Za-z\"]+)$"
    )
    _COMPACT_DIMENSION_UNIT_SUFFIX_RE = re.compile(
        r"^(?P<body>[-+()./\d\s]+?)(?P<unit>[A-Za-z\"]+)$"
    )
    _MIXED_FRACTION_RE = re.compile(
        r"(?<![\w.)])(?P<sign>[+-]?)(?P<whole>\d+)\s+(?P<num>\d+)\s*/\s*(?P<den>\d+)(?![\w(/])"
    )
    _HYPHENATED_MIXED_FRACTION_RE = re.compile(
        r"(?<![\w.)])(?P<sign>[+-]?)(?P<whole>\d+)-\s*(?P<num>\d+)\s*/\s*(?P<den>\d+)(?![\w(/])"
    )
    _INLINE_DIMENSION_UNIT_TOKEN_RE = re.compile(
        r"(?P<anchor>(?:\d|\)))\s*(?P<unit>[A-Za-z\"]+)"
    )
    _INLINE_DIMENSION_ALLOWED_BODY_RE = re.compile(r"[-+*/().\d\seE]+")
    _DIMENSION_NUMBER_TOKEN_RE = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
    _DIMENSION_NAME_TOKEN_RE = re.compile(r"[A-Za-z\"]+")
    _DIMENSION_LENGTH_UNITS_CM = {
        "mm": 0.1,
        "millimeter": 0.1,
        "millimeters": 0.1,
        "cm": 1.0,
        "centimeter": 1.0,
        "centimeters": 1.0,
        "m": 100.0,
        "meter": 100.0,
        "meters": 100.0,
        "in": 2.54,
        "inch": 2.54,
        "inches": 2.54,
        "ft": 30.48,
        "foot": 30.48,
        "feet": 30.48,
    }
    _CANONICAL_DIMENSION_UNIT_SYMBOLS = {
        "mm": "mm",
        "millimeter": "mm",
        "millimeters": "mm",
        "cm": "cm",
        "centimeter": "cm",
        "centimeters": "cm",
        "m": "m",
        "meter": "m",
        "meters": "m",
        "inch": "in",
        "inches": "in",
        "ft": "ft",
        "foot": "ft",
        "feet": "ft",
    }
    _DIMENSION_CONSTANTS = {
        "pi": math.pi,
        "tau": math.tau,
        "e": math.e,
    }
    _DIMENSION_FUNCTIONS = {
        "sqrt": math.sqrt,
    }
    GEOMETRIC_TYPES = {
        "perpendicular",
        "parallel",
        "horizontal",
        "vertical",
        "equal",
        "tangent",
        "normal",
        "coincident",
        "concentric",
        "fixed",
        "midpoint",
        "mirror",
    }

    DIMENSION_TYPES = {
        "angle",
        "diameter",
        "radius",
        "distance",
        "length",
        "majorradius",
        "minorradius",
    }

    def add_constraint(
        self,
        constraint_type,
        entities,
        value=None,
        sketch_num=-1,
        text_point=None,
        is_driving=True,
        extra=None,
    ):
        if not isinstance(entities, list) or not entities:
            raise ValueError("entities must be a non-empty list of entity_id strings.")

        if not isinstance(constraint_type, str) or not constraint_type.strip():
            raise ValueError("constraint_type must be a non-empty string.")

        normalized_type = constraint_type.strip().lower()
        if normalized_type == "fix":
            normalized_type = "fixed"

        if normalized_type in self.DIMENSION_TYPES:
            return self._add_dimension_constraint(
                normalized_type,
                entities,
                value,
                sketch_num,
                text_point,
                is_driving,
                extra,
            )
        if normalized_type in self.GEOMETRIC_TYPES:
            return self._add_geometric_constraint(
                normalized_type, entities, sketch_num, extra=extra
            )

        raise ValueError(f"Unsupported constraint type: {constraint_type}")

    def add_raw_tangent_distance_dimension(
        self,
        first_entity_id,
        first_close_to_other,
        second_entity_id,
        second_close_to_other,
        value=None,
        sketch_num=-1,
        text_point=None,
        is_driving=True,
    ):
        if not isinstance(first_entity_id, str) or not first_entity_id.strip():
            raise ValueError("first_entity_id must be a non-empty string.")
        if not isinstance(second_entity_id, str) or not second_entity_id.strip():
            raise ValueError("second_entity_id must be a non-empty string.")

        sketch, sketch_index = self._get_sketch(sketch_num)
        first_entity, first_entry = self.resolve_entity(first_entity_id)
        second_entity, second_entry = self.resolve_entity(second_entity_id)
        resolved = [
            (first_entity_id, first_entity, first_entry),
            (second_entity_id, second_entity, second_entry),
        ]
        text_point_3d = self._build_text_point(
            sketch,
            text_point,
            resolved,
            constraint_type="distance",
            extra={"direction": "MINIMUM"},
            runtime_value=value,
        )

        dimension = sketch.sketchDimensions.addTangentDistanceDimension(
            first_entity,
            bool(first_close_to_other),
            second_entity,
            bool(second_close_to_other),
            text_point_3d,
            bool(is_driving),
        )
        dimension_id = self._register_entity(
            dimension,
            "dimension",
            sketch_index,
            metadata={
                "type": "raw_tangent_distance",
                "first_entity_id": first_entity_id,
                "second_entity_id": second_entity_id,
                "first_close_to_other": bool(first_close_to_other),
                "second_close_to_other": bool(second_close_to_other),
            },
        )

        if value is not None:
            parameter = getattr(dimension, "parameter", None)
            if parameter is not None:
                if isinstance(value, str):
                    parameter.expression = value
                else:
                    parameter.value = float(value)

        return {
            "dimension_id": dimension_id,
            "sketch_num": sketch_index,
            "first_entity_id": first_entity_id,
            "second_entity_id": second_entity_id,
            "first_close_to_other": bool(first_close_to_other),
            "second_close_to_other": bool(second_close_to_other),
        }

    def debug_distance_resolution(
        self,
        entities,
        value=None,
        sketch_num=-1,
        extra=None,
    ):
        sketch, sketch_index, resolved = self._resolve_entities(entities, sketch_num)
        if len(resolved) != 2:
            raise ValueError("debug_distance_resolution expects exactly two entities.")

        first_item = resolved[0]
        second_item = resolved[1]
        first_entity = first_item[1]
        second_entity = second_item[1]
        first_is_line = self._entity_is_kind(
            first_item, first_entity, "line", self._is_sketch_line
        )
        second_is_line = self._entity_is_kind(
            second_item, second_entity, "line", self._is_sketch_line
        )
        first_is_tangent_curve = self._entity_is_tangent_curve(first_item, first_entity)
        second_is_tangent_curve = self._entity_is_tangent_curve(
            second_item, second_entity
        )
        normalized_value = None
        if isinstance(value, str):
            normalized_value = self._normalize_dimension_expression("distance", value)
        target_magnitude = self._distance_target_magnitude(value)
        raw_curve_preference = self._resolve_tangent_distance_preference(
            first_entity,
            second_entity,
            value,
            first_item=first_item,
            second_item=second_item,
        )
        curve_preference = raw_curve_preference
        selected_support_on_curve = None
        if isinstance(curve_preference, dict):
            geometry = curve_preference.get("geometry")
            if isinstance(geometry, dict):
                selected_support_on_curve = geometry.get("selected_support_on_curve")
                if selected_support_on_curve is False:
                    curve_preference = None

        raw_call = None
        if first_is_tangent_curve or second_is_tangent_curve:
            if second_is_tangent_curve:
                raw_call = {
                    "first_entity_id": first_item[0],
                    "first_close_to_other": curve_preference.get(
                        "first_close_to_other", True
                    )
                    if curve_preference
                    else True,
                    "second_entity_id": second_item[0],
                    "second_close_to_other": curve_preference.get(
                        "second_close_to_other", True
                    )
                    if curve_preference
                    else self._prefer_closest_tangent_side(extra),
                }
            else:
                raw_call = {
                    "first_entity_id": second_item[0],
                    "first_close_to_other": curve_preference.get(
                        "second_close_to_other", True
                    )
                    if curve_preference
                    else True,
                    "second_entity_id": first_item[0],
                    "second_close_to_other": curve_preference.get(
                        "first_close_to_other", True
                    )
                    if curve_preference
                    else self._prefer_closest_tangent_side(extra),
                }

        return {
            "sketch_num": sketch_index,
            "resolved_entity_ids": [first_item[0], second_item[0]],
            "resolved_kinds": [
                self._entity_kind(first_item),
                self._entity_kind(second_item),
            ],
            "first_is_line": bool(first_is_line),
            "second_is_line": bool(second_is_line),
            "first_is_tangent_curve": bool(first_is_tangent_curve),
            "second_is_tangent_curve": bool(second_is_tangent_curve),
            "value": value,
            "normalized_value": normalized_value,
            "target_magnitude": target_magnitude,
            "selected_support_on_curve": selected_support_on_curve,
            "raw_curve_preference": raw_curve_preference,
            "curve_preference": curve_preference,
            "raw_call": raw_call,
        }

    def auto_close_loop(self, sketch_num=-1, tolerance=None):
        sketch, sketch_index = self._get_sketch(sketch_num)
        pre_profile_count = int(sketch.profiles.count)
        tolerance_used = self._adaptive_close_tolerance(sketch, tolerance)

        endpoints = self._collect_open_curve_endpoints(sketch)
        candidate_count = len(endpoints)
        if candidate_count < 2:
            post_profile_count = int(sketch.profiles.count)
            return {
                "ok": True,
                "sketch_num": sketch_index,
                "performed": True,
                "tolerance_used": float(tolerance_used),
                "pre_profile_count": pre_profile_count,
                "post_profile_count": post_profile_count,
                "candidate_point_count": candidate_count,
                "cluster_count": 0,
                "clusters": [],
                "applied_count": 0,
                "skipped_count": 0,
                "constraint_ids": [],
            }

        parents = list(range(candidate_count))

        def _find(i):
            while parents[i] != i:
                parents[i] = parents[parents[i]]
                i = parents[i]
            return i

        def _union(i, j):
            ri = _find(i)
            rj = _find(j)
            if ri != rj:
                parents[rj] = ri

        # 鍏抽敭閫昏緫锛氭寜鈥滆窛绂?<= 瀹瑰樊鈥濊仛绫荤鐐癸紝鍚庣画姣忕皣鐢?anchor-first 鏂藉姞 Coincident銆?
        # 璇ョ瓥鐣ユ瘮鍏ㄨ繛鎺ユ洿绋冲仴锛屽彲鍑忓皯閲嶅绾︽潫鍜岃繃绾︽潫椋庨櫓銆?
        for i in range(candidate_count):
            for j in range(i + 1, candidate_count):
                left = endpoints[i]
                right = endpoints[j]
                if left["curve_key"] == right["curve_key"]:
                    continue
                if (
                    self._sketch_point_distance(left["point"], right["point"])
                    <= tolerance_used
                ):
                    _union(i, j)

        groups = {}
        for idx in range(candidate_count):
            root = _find(idx)
            groups.setdefault(root, []).append(idx)

        constraint_ids = []
        applied_count = 0
        skipped_count = 0
        clusters = []
        geometric_constraints = sketch.geometricConstraints

        for member_indexes in groups.values():
            if len(member_indexes) < 2:
                continue

            anchor_idx = member_indexes[0]
            anchor = endpoints[anchor_idx]
            cluster_detail = {
                "size": len(member_indexes),
                "members": [
                    f"{endpoints[i]['curve_key']}::{endpoints[i]['point_role']}"
                    for i in member_indexes
                ],
                "applied_pairs": 0,
                "skipped_pairs": 0,
            }

            for idx in member_indexes[1:]:
                current = endpoints[idx]
                if current["curve_key"] == anchor["curve_key"]:
                    skipped_count += 1
                    cluster_detail["skipped_pairs"] += 1
                    continue
                try:
                    applied = geometric_constraints.addCoincident(
                        anchor["point"], current["point"]
                    )
                except Exception:
                    skipped_count += 1
                    cluster_detail["skipped_pairs"] += 1
                    continue

                constraint_id = self._register_entity(
                    applied,
                    "constraint",
                    sketch_index,
                    metadata={
                        "type": "coincident",
                        "auto_created_for": "auto_close_loop",
                    },
                )
                constraint_ids.append(constraint_id)
                applied_count += 1
                cluster_detail["applied_pairs"] += 1

            clusters.append(cluster_detail)

        post_profile_count = int(sketch.profiles.count)
        return {
            "ok": True,
            "sketch_num": sketch_index,
            "performed": True,
            "tolerance_used": float(tolerance_used),
            "pre_profile_count": pre_profile_count,
            "post_profile_count": post_profile_count,
            "candidate_point_count": candidate_count,
            "cluster_count": len(clusters),
            "clusters": clusters,
            "applied_count": int(applied_count),
            "skipped_count": int(skipped_count),
            "constraint_ids": constraint_ids,
        }

    def _collect_open_curve_endpoints(self, sketch):
        curve_collections = [
            "sketchLines",
            "sketchArcs",
            "sketchEllipticalArcs",
            "sketchFittedSplines",
            "sketchFixedSplines",
            "sketchControlPointSplines",
        ]
        endpoints = []
        sketch_curves = sketch.sketchCurves

        for collection_name in curve_collections:
            collection = getattr(sketch_curves, collection_name, None)
            if collection is None:
                continue
            try:
                count = int(collection.count)
            except Exception:
                continue
            for i in range(count):
                curve_obj = collection.item(i)
                if curve_obj is None:
                    continue
                start_point, end_point = self._safe_get_curve_endpoints(curve_obj)
                if start_point is None or end_point is None:
                    continue
                curve_key = f"{collection_name}[{i}]"
                endpoints.append(
                    {
                        "curve_key": curve_key,
                        "point_role": "start",
                        "point": start_point,
                    }
                )
                endpoints.append(
                    {
                        "curve_key": curve_key,
                        "point_role": "end",
                        "point": end_point,
                    }
                )
        return endpoints

    def _adaptive_close_tolerance(self, sketch, explicit_tolerance):
        if explicit_tolerance is not None:
            tol = float(explicit_tolerance)
            if tol <= 0:
                raise ValueError(f"tolerance must be > 0, got: {explicit_tolerance}")
            return tol

        bbox = sketch.boundingBox
        dx = float(bbox.maxPoint.x - bbox.minPoint.x)
        dy = float(bbox.maxPoint.y - bbox.minPoint.y)
        dz = float(bbox.maxPoint.z - bbox.minPoint.z)
        diag = math.sqrt(dx * dx + dy * dy + dz * dz)

        # 鍏抽敭娉ㄩ噴锛氬宸殢鑽夊浘灏哄害鑷€傚簲锛屽啀鍋氫笂涓嬮檺閽冲埗銆?
        # 涓嬮檺閬垮厤灏忔ā鍨嬫暟鍊煎櫔澹板鑷粹€滄案杩滆ˉ涓嶄笂鈥濓紱涓婇檺閬垮厤澶фā鍨嬫椂璇繛杩滅鐐广€?
        scaled = diag * 5e-3
        return max(1e-3, min(0.2, scaled if scaled > 0 else 1e-3))

    def _sketch_point_distance(self, point_a, point_b):
        dx = float(point_a.geometry.x - point_b.geometry.x)
        dy = float(point_a.geometry.y - point_b.geometry.y)
        dz = float(point_a.geometry.z - point_b.geometry.z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _resolve_entities(self, entity_ids, sketch_num):
        target_sketch_index = None
        if sketch_num != -1:
            target_sketch_index = self._resolve_sketch_index(sketch_num)

        resolved = []
        for entity_id in entity_ids:
            entity_obj, entry = self.resolve_entity(entity_id)
            sketch_index = entry.get("sketch_index")
            if sketch_index is None:
                raise ValueError(f"Entity {entity_id} is not attached to any sketch.")
            if target_sketch_index is None:
                target_sketch_index = sketch_index
            elif target_sketch_index != sketch_index:
                raise ValueError("All entities must belong to the same sketch.")
            resolved.append((entity_id, entity_obj, entry))

        if target_sketch_index is None:
            target_sketch_index = self._resolve_sketch_index(-1)
        sketch, target_sketch_index = self._get_sketch(target_sketch_index)
        return sketch, target_sketch_index, resolved

    def _anchor_pairs(self, resolved):
        if len(resolved) < 2:
            raise ValueError("At least two entities are required.")
        anchor = resolved[0]
        return [(anchor, item) for item in resolved[1:]]

    def _normalize_kinds(self, resolved):
        return [entry["kind"] for _, _, entry in resolved]

    def _find_curve_size_dimension_entry(self, entity_id, sketch_index=None):
        registry = getattr(self, "entity_registry", {})
        if not isinstance(registry, dict) or not entity_id:
            return None
        for entry in reversed(list(registry.values())):
            if not isinstance(entry, dict) or entry.get("kind") != "dimension":
                continue
            if sketch_index is not None and entry.get("sketch_index") != sketch_index:
                continue
            metadata = entry.get("metadata")
            if not isinstance(metadata, dict):
                continue
            if metadata.get("type") not in {"radius", "diameter"}:
                continue
            target_entities = metadata.get("target_entities")
            if isinstance(target_entities, list) and entity_id in target_entities:
                return entry
        return None

    def _curve_size_dimension_record(self, entry, created=False):
        dimension = entry.get("obj")
        parameter = getattr(dimension, "parameter", None)
        parameter_name = str(getattr(parameter, "name", "") or "")
        if parameter is None or not parameter_name:
            raise RuntimeError(
                "Curve size dimension does not expose a named parameter."
            )
        metadata = (
            entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        )
        measure = str(metadata.get("type") or "").strip().lower()
        if measure not in {"radius", "diameter"}:
            raise RuntimeError(
                f"Unsupported curve size dimension type: {measure or '<missing>'}"
            )
        return {
            "dimension": dimension,
            "dimension_id": entry.get("id"),
            "parameter": parameter,
            "parameter_name": parameter_name,
            "measure": measure,
            "created": bool(created),
        }

    def _create_curve_size_dimension(self, sketch, sketch_index, item, measure):
        entity_id, entity_obj, _entry = item
        text_point_3d = self._build_text_point(sketch, None, [item])
        if measure == "diameter":
            dimension = sketch.sketchDimensions.addDiameterDimension(
                entity_obj, text_point_3d, True
            )
        else:
            dimension = sketch.sketchDimensions.addRadialDimension(
                entity_obj, text_point_3d, True
            )
        metadata = {
            "type": measure,
            "target_entities": [entity_id],
            "auto_created_for": "equal_curve_radius",
        }
        dimension_id = self._register_entity(
            dimension,
            "dimension",
            sketch_index,
            metadata=metadata,
        )
        return self._curve_size_dimension_record(
            {
                "id": dimension_id,
                "obj": dimension,
                "metadata": metadata,
            },
            created=True,
        )

    def _get_or_create_curve_size_dimension(
        self,
        sketch,
        sketch_index,
        item,
        dimension_cache,
        preferred_measure=None,
    ):
        entity_id = item[0]
        cache_key = (int(sketch_index), str(entity_id))
        cached = dimension_cache.get(cache_key)
        if cached is not None:
            return cached

        existing_entry = self._find_curve_size_dimension_entry(
            entity_id, sketch_index=sketch_index
        )
        if existing_entry is not None:
            record = self._curve_size_dimension_record(existing_entry, created=False)
            dimension_cache[cache_key] = record
            return record

        measure = "diameter" if preferred_measure == "diameter" else "radius"
        record = self._create_curve_size_dimension(sketch, sketch_index, item, measure)
        dimension_cache[cache_key] = record
        return record

    def _build_curve_equal_expression(
        self, source_parameter_name, source_measure, target_measure
    ):
        if target_measure == source_measure:
            return source_parameter_name
        if source_measure == "diameter" and target_measure == "radius":
            return f"{source_parameter_name} / 2"
        if source_measure == "radius" and target_measure == "diameter":
            return f"{source_parameter_name} * 2"
        raise ValueError(
            f"Unsupported curve equal dimension conversion: {source_measure} -> {target_measure}"
        )

    def _apply_mixed_curve_equal_constraint(
        self,
        sketch,
        sketch_index,
        left,
        right,
        dimension_cache,
    ):
        source_dimension = self._get_or_create_curve_size_dimension(
            sketch,
            sketch_index,
            left,
            dimension_cache,
        )
        target_dimension = self._get_or_create_curve_size_dimension(
            sketch,
            sketch_index,
            right,
            dimension_cache,
            preferred_measure=source_dimension["measure"],
        )

        source_parameter_name = source_dimension["parameter_name"]
        target_parameter = target_dimension["parameter"]
        expression = self._build_curve_equal_expression(
            source_parameter_name,
            source_dimension["measure"],
            target_dimension["measure"],
        )
        if str(getattr(target_parameter, "name", "") or "") != source_parameter_name:
            target_parameter.expression = expression

        created_ids = []
        if source_dimension["created"]:
            created_ids.append(source_dimension["dimension_id"])
        if target_dimension["created"] or target_dimension["dimension_id"] is not None:
            created_ids.append(target_dimension["dimension_id"])
        return created_ids

    def _apply_equal_constraint(
        self,
        sketch,
        sketch_index,
        geometric_constraints,
        left,
        right,
        dimension_cache,
    ):
        left_kind = left[2]["kind"]
        right_kind = right[2]["kind"]
        if {left_kind, right_kind} <= {"arc", "circle"}:
            return self._apply_mixed_curve_equal_constraint(
                sketch,
                sketch_index,
                left,
                right,
                dimension_cache,
            )

        if left_kind == right_kind:
            applied = geometric_constraints.addEqual(left[1], right[1])
            constraint_id = self._register_entity(
                applied,
                "constraint",
                sketch_index,
                metadata={"type": "equal"},
            )
            return [constraint_id]

        raise ValueError(
            "equal expects same-kind entities, except circle/arc pairs use shared radius semantics. "
            f"got kinds: {left_kind}, {right_kind}"
        )

    def _add_geometric_constraint(
        self, constraint_type, entities, sketch_num, extra=None
    ):
        sketch, sketch_index, resolved = self._resolve_entities(entities, sketch_num)
        geometric_constraints = sketch.geometricConstraints
        applied_constraints = []
        helper_entities = []
        equal_dimension_cache = {}

        if constraint_type in {
            "parallel",
            "perpendicular",
            "equal",
            "tangent",
            "concentric",
        }:
            pairs = self._anchor_pairs(resolved)
            for left, right in pairs:
                if constraint_type == "parallel":
                    applied = geometric_constraints.addParallel(left[1], right[1])
                elif constraint_type == "perpendicular":
                    applied = geometric_constraints.addPerpendicular(left[1], right[1])
                elif constraint_type == "equal":
                    for constraint_id in self._apply_equal_constraint(
                        sketch,
                        sketch_index,
                        geometric_constraints,
                        left,
                        right,
                        equal_dimension_cache,
                    ):
                        if constraint_id not in applied_constraints:
                            applied_constraints.append(constraint_id)
                    continue
                elif constraint_type == "tangent":
                    applied = geometric_constraints.addTangent(left[1], right[1])
                else:
                    applied = geometric_constraints.addConcentric(left[1], right[1])
                constraint_id = self._register_entity(
                    applied,
                    "constraint",
                    sketch_index,
                    metadata={"type": constraint_type},
                )
                applied_constraints.append(constraint_id)

        elif constraint_type == "normal":
            pairs = self._anchor_pairs(resolved)
            for left, right in pairs:
                pair_mode, line_item, other_item = self._resolve_normal_pair(
                    left, right
                )
                if pair_mode == "line_nurbs":
                    constraint_ids, helper_ids = (
                        self._apply_line_nurbs_normal_constraint(
                            sketch,
                            sketch_index,
                            geometric_constraints,
                            line_item,
                            other_item,
                        )
                    )
                    for helper_id in helper_ids:
                        self._append_helper_entity(helper_entities, helper_id)
                    for constraint_id in constraint_ids:
                        if constraint_id not in applied_constraints:
                            applied_constraints.append(constraint_id)
                    continue
                if pair_mode == "line_line":
                    applied = geometric_constraints.addPerpendicular(
                        line_item[1], other_item[1]
                    )
                    # 鍏煎鏃ц涓猴細涓ゆ潯绾挎椂 Normal 閫€鍖栦负鍨傜洿绾︽潫
                    applied = geometric_constraints.addPerpendicular(
                        line_item[1], other_item[1]
                    )
                    metadata = {
                        "type": constraint_type,
                        "mode": "line_line_perpendicular",
                    }
                else:
                    # 鏍稿績淇锛欶usion 鏃?line-circle normal 鐩存帴 API銆?
                    # 鐢ㄢ€滃渾/鍦嗗姬涓績鐐硅惤鍦ㄧ嚎涓娾€濊〃杈炬硶绾垮叧绯伙紙寰勫悜鏂瑰悜锛夈€?
                    center_point = getattr(other_item[1], "centerSketchPoint", None)
                    if center_point is None:
                        raise ValueError(
                            f"normal expects circle/arc with centerSketchPoint, got kind={other_item[2]['kind']}."
                        )
                    applied = geometric_constraints.addCoincident(
                        center_point, line_item[1]
                    )
                    metadata = {
                        "type": constraint_type,
                        "mode": "line_curve_center_on_line",
                    }

                constraint_id = self._register_entity(
                    applied,
                    "constraint",
                    sketch_index,
                    metadata=metadata,
                )
                applied_constraints.append(constraint_id)

        elif constraint_type in {"horizontal", "vertical"}:
            kinds = self._normalize_kinds(resolved)
            if self._has_local_axis_helper(extra):
                if all(kind == "line" for kind in kinds):
                    self._apply_local_axis_line_constraint(
                        sketch,
                        sketch_index,
                        resolved,
                        geometric_constraints,
                        constraint_type,
                        extra,
                        applied_constraints,
                        helper_entities,
                    )
                elif all(kind == "point" for kind in kinds):
                    self._apply_local_axis_point_constraint(
                        sketch,
                        sketch_index,
                        resolved,
                        geometric_constraints,
                        constraint_type,
                        extra,
                        applied_constraints,
                        helper_entities,
                    )
                else:
                    raise ValueError(
                        f"{constraint_type} only supports all-line or all-point entities."
                    )
            elif all(kind == "line" for kind in kinds):
                for _, entity_obj, _ in resolved:
                    if constraint_type == "horizontal":
                        applied = geometric_constraints.addHorizontal(entity_obj)
                    else:
                        applied = geometric_constraints.addVertical(entity_obj)
                    constraint_id = self._register_entity(
                        applied,
                        "constraint",
                        sketch_index,
                        metadata={"type": constraint_type},
                    )
                    applied_constraints.append(constraint_id)
            elif all(kind == "point" for kind in kinds):
                self._apply_axis_aligned_point_constraint(
                    sketch,
                    sketch_index,
                    resolved,
                    geometric_constraints,
                    constraint_type,
                    applied_constraints,
                    helper_entities,
                )
            else:
                raise ValueError(
                    f"{constraint_type} only supports all-line or all-point entities."
                )

        elif constraint_type == "coincident":
            pairs = self._anchor_pairs(resolved)
            for left, right in pairs:
                applied = geometric_constraints.addCoincident(left[1], right[1])
                constraint_id = self._register_entity(
                    applied,
                    "constraint",
                    sketch_index,
                    metadata={"type": constraint_type},
                )
                applied_constraints.append(constraint_id)

        elif constraint_type == "fixed":
            for entity_id, entity_obj, _ in resolved:
                if not hasattr(entity_obj, "isFixed"):
                    raise ValueError(
                        f"Entity {entity_id} does not support fixed state."
                    )
                entity_obj.isFixed = True
                constraint_id = self._register_entity(
                    entity_obj,
                    "constraint",
                    sketch_index,
                    metadata={"type": constraint_type, "target": entity_id},
                )
                applied_constraints.append(constraint_id)

        elif constraint_type == "midpoint":
            kinds = self._normalize_kinds(resolved)
            if len(resolved) == 2:
                if kinds.count("point") != 1:
                    raise ValueError("midpoint with 2 entities expects [point, curve].")
                point_item = resolved[0] if kinds[0] == "point" else resolved[1]
                curve_item = resolved[1] if kinds[0] == "point" else resolved[0]
                applied = geometric_constraints.addMidPoint(
                    point_item[1], curve_item[1]
                )
                constraint_id = self._register_entity(
                    applied,
                    "constraint",
                    sketch_index,
                    metadata={"type": constraint_type},
                )
                applied_constraints.append(constraint_id)
            elif len(resolved) == 3:
                # [target, p1, p2]
                if not all(kind == "point" for kind in kinds):
                    raise ValueError(
                        "midpoint [target, p1, p2] expects all point entities."
                    )
                target = resolved[0][1]
                point1 = resolved[1][1]
                point2 = resolved[2][1]
                helper_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
                    point1, point2
                )
                helper_line.isConstruction = True
                helper_line_id = self._register_entity(
                    helper_line,
                    "line",
                    sketch_index,
                    metadata={"auto_created_for": "midpoint"},
                )
                helper_entities.append(helper_line_id)
                applied = geometric_constraints.addMidPoint(target, helper_line)
                constraint_id = self._register_entity(
                    applied,
                    "constraint",
                    sketch_index,
                    metadata={"type": constraint_type},
                )
                applied_constraints.append(constraint_id)
            else:
                raise ValueError(
                    "midpoint expects either 2 entities or 3 point entities."
                )

        elif constraint_type == "mirror":
            if len(resolved) != 3:
                raise ValueError(
                    "mirror expects exactly three entities: [source, axis_line, target]."
                )
            source_item, axis_item, target_item = resolved
            if axis_item[2]["kind"] != "line":
                raise ValueError("mirror expects the second entity to be a line axis.")
            applied = geometric_constraints.addSymmetry(
                source_item[1],
                target_item[1],
                axis_item[1],
            )
            constraint_id = self._register_entity(
                applied,
                "constraint",
                sketch_index,
                metadata={"type": constraint_type},
            )
            applied_constraints.append(constraint_id)

        else:
            raise ValueError(
                f"Unsupported geometric constraint type: {constraint_type}"
            )

        return {
            "constraint_type": constraint_type,
            "constraint_ids": applied_constraints,
            "helper_entity_ids": helper_entities,
            "applied_count": len(applied_constraints),
        }

    def _has_local_axis_helper(self, extra):
        return (
            isinstance(extra, dict)
            and extra.get("local_axis_mode") == "parallel_helper"
        )

    def _has_local_axis_directional_dimension(self, extra):
        return (
            isinstance(extra, dict)
            and extra.get("local_axis_mode") == "directional_dimension_helper"
        )

    def _extract_local_axis_frame(self, extra):
        if not isinstance(extra, dict) or "local_axis_mode" not in extra:
            return None
        origin = extra.get("local_axis_origin")
        direction = extra.get("local_axis_direction")
        if not self._is_vector3_payload(origin) or not self._is_vector3_payload(
            direction
        ):
            raise ValueError("local axis helper requires 3D origin and direction.")
        return [float(value) for value in origin], self._normalize_vector_payload(
            direction
        )

    def _extract_local_axis_helper(self, extra):
        if not self._has_local_axis_helper(extra):
            return None
        return self._extract_local_axis_frame(extra)

    def _is_vector3_payload(self, value):
        return (
            isinstance(value, list)
            and len(value) == 3
            and all(isinstance(item, (int, float)) for item in value)
        )

    def _normalize_vector_payload(self, value):
        direction = [float(item) for item in value]
        length = math.sqrt(sum(component * component for component in direction))
        if length <= 1e-12:
            raise ValueError("local axis direction length must be > 0.")
        return [component / length for component in direction]

    def _local_axis_cache_key(self, sketch_index, origin, direction):
        rounded_origin = tuple(round(float(value), 9) for value in origin)
        rounded_direction = tuple(round(float(value), 9) for value in direction)
        return ("local_axis_reference", sketch_index, rounded_origin, rounded_direction)

    def _get_or_create_local_axis_reference_line(
        self, sketch, sketch_index, origin, direction
    ):
        cache_key = self._local_axis_cache_key(sketch_index, origin, direction)
        cached_id = self._local_axis_helper_cache.get(cache_key)
        if cached_id is not None:
            cached_obj, _ = self.resolve_entity(cached_id, expected_kinds={"line"})
            return cached_obj, cached_id

        start_model = adsk.core.Point3D.create(
            float(origin[0]), float(origin[1]), float(origin[2])
        )
        end_model = adsk.core.Point3D.create(
            float(origin[0]) + float(direction[0]) * 10.0,
            float(origin[1]) + float(direction[1]) * 10.0,
            float(origin[2]) + float(direction[2]) * 10.0,
        )
        start_point = self._to_sketch_point3d(
            sketch, [start_model.x, start_model.y, start_model.z]
        )
        end_point = self._to_sketch_point3d(
            sketch, [end_model.x, end_model.y, end_model.z]
        )
        helper_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            start_point, end_point
        )
        helper_line.isConstruction = True
        if hasattr(helper_line, "isFixed"):
            helper_line.isFixed = True
        helper_id = self._register_entity(
            helper_line,
            "line",
            sketch_index,
            metadata={"auto_created_for": "local_axis_reference"},
        )
        self._local_axis_helper_cache[cache_key] = helper_id
        return helper_line, helper_id

    def _append_helper_entity(self, helper_entities, entity_id):
        if entity_id not in helper_entities:
            helper_entities.append(entity_id)

    def _register_local_axis_constraint(
        self, applied, sketch_index, constraint_type, mode
    ):
        return self._register_entity(
            applied,
            "constraint",
            sketch_index,
            metadata={"type": constraint_type, "mode": mode},
        )

    def _apply_local_axis_line_constraint(
        self,
        sketch,
        sketch_index,
        resolved,
        geometric_constraints,
        constraint_type,
        extra,
        applied_constraints,
        helper_entities,
    ):
        origin, direction = self._extract_local_axis_helper(extra)
        reference_line, reference_line_id = (
            self._get_or_create_local_axis_reference_line(
                sketch,
                sketch_index,
                origin,
                direction,
            )
        )
        self._append_helper_entity(helper_entities, reference_line_id)
        for _, entity_obj, _ in resolved:
            applied = geometric_constraints.addParallel(entity_obj, reference_line)
            applied_constraints.append(
                self._register_local_axis_constraint(
                    applied,
                    sketch_index,
                    constraint_type,
                    "local_axis_parallel_helper",
                )
            )

    def _apply_local_axis_point_constraint(
        self,
        sketch,
        sketch_index,
        resolved,
        geometric_constraints,
        constraint_type,
        extra,
        applied_constraints,
        helper_entities,
    ):
        if len(resolved) < 2:
            raise ValueError(f"{constraint_type} expects at least two point entities.")

        origin, direction = self._extract_local_axis_helper(extra)
        reference_line, reference_line_id = (
            self._get_or_create_local_axis_reference_line(
                sketch,
                sketch_index,
                origin,
                direction,
            )
        )
        self._append_helper_entity(helper_entities, reference_line_id)

        anchor_point = resolved[0][1]
        anchor_model = self._sketch_point_to_model_space(sketch, anchor_point)
        helper_end_model = adsk.core.Point3D.create(
            float(anchor_model.x) + float(direction[0]) * 10.0,
            float(anchor_model.y) + float(direction[1]) * 10.0,
            float(anchor_model.z) + float(direction[2]) * 10.0,
        )
        anchor_start = self._to_sketch_point3d(
            sketch, [anchor_model.x, anchor_model.y, anchor_model.z]
        )
        anchor_end = self._to_sketch_point3d(
            sketch,
            [helper_end_model.x, helper_end_model.y, helper_end_model.z],
        )
        anchor_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            anchor_start, anchor_end
        )
        anchor_line.isConstruction = True
        anchor_line_id = self._register_entity(
            anchor_line,
            "line",
            sketch_index,
            metadata={"auto_created_for": "local_axis_anchor"},
        )
        self._append_helper_entity(helper_entities, anchor_line_id)

        parallel = geometric_constraints.addParallel(anchor_line, reference_line)
        applied_constraints.append(
            self._register_local_axis_constraint(
                parallel,
                sketch_index,
                constraint_type,
                "local_axis_anchor_parallel",
            )
        )

        for _, entity_obj, _ in resolved[1:]:
            applied = geometric_constraints.addCoincident(entity_obj, anchor_line)
            applied_constraints.append(
                self._register_local_axis_constraint(
                    applied,
                    sketch_index,
                    constraint_type,
                    "local_axis_point_on_helper",
                )
            )

    def _axis_aligned_point_helper_half_span(self, sketch):
        bbox = getattr(sketch, "boundingBox", None)
        if bbox is not None:
            try:
                dx = float(bbox.maxPoint.x - bbox.minPoint.x)
                dy = float(bbox.maxPoint.y - bbox.minPoint.y)
                diag = math.hypot(dx, dy)
                if diag > 1e-9:
                    return max(10.0, diag * 0.75)
            except Exception:
                pass
        return 10.0

    def _create_axis_aligned_point_helper_line(
        self,
        sketch,
        sketch_index,
        anchor_point,
        constraint_type,
    ):
        anchor_geometry = getattr(anchor_point, "geometry", None)
        if anchor_geometry is None:
            raise ValueError("Axis-aligned point helper requires point geometry.")

        half_span = self._axis_aligned_point_helper_half_span(sketch)
        anchor_x = float(anchor_geometry.x)
        anchor_y = float(anchor_geometry.y)
        anchor_z = float(getattr(anchor_geometry, "z", 0.0))
        if constraint_type == "horizontal":
            start_point = adsk.core.Point3D.create(
                anchor_x - half_span, anchor_y, anchor_z
            )
            end_point = adsk.core.Point3D.create(
                anchor_x + half_span, anchor_y, anchor_z
            )
        else:
            start_point = adsk.core.Point3D.create(
                anchor_x, anchor_y - half_span, anchor_z
            )
            end_point = adsk.core.Point3D.create(
                anchor_x, anchor_y + half_span, anchor_z
            )

        helper_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            start_point, end_point
        )
        helper_line.isConstruction = True
        helper_id = self._register_entity(
            helper_line,
            "line",
            sketch_index,
            metadata={"auto_created_for": f"{constraint_type}_points_helper"},
        )
        return helper_line, helper_id

    def _apply_axis_aligned_point_constraint(
        self,
        sketch,
        sketch_index,
        resolved,
        geometric_constraints,
        constraint_type,
        applied_constraints,
        helper_entities,
    ):
        if len(resolved) < 2:
            raise ValueError(f"{constraint_type} expects at least two point entities.")

        anchor_point = resolved[0][1]
        helper_line, helper_id = self._create_axis_aligned_point_helper_line(
            sketch,
            sketch_index,
            anchor_point,
            constraint_type,
        )
        self._append_helper_entity(helper_entities, helper_id)

        if constraint_type == "horizontal":
            orientation = geometric_constraints.addHorizontal(helper_line)
        else:
            orientation = geometric_constraints.addVertical(helper_line)
        applied_constraints.append(
            self._register_entity(
                orientation,
                "constraint",
                sketch_index,
                metadata={
                    "type": constraint_type,
                    "mode": "point_alignment_helper_orientation",
                },
            )
        )

        anchor_constraint = geometric_constraints.addCoincident(
            anchor_point, helper_line
        )
        applied_constraints.append(
            self._register_entity(
                anchor_constraint,
                "constraint",
                sketch_index,
                metadata={
                    "type": constraint_type,
                    "mode": "point_alignment_helper_anchor",
                },
            )
        )

        for _, entity_obj, _ in resolved[1:]:
            applied = geometric_constraints.addCoincident(entity_obj, helper_line)
            applied_constraints.append(
                self._register_entity(
                    applied,
                    "constraint",
                    sketch_index,
                    metadata={
                        "type": constraint_type,
                        "mode": "point_alignment_helper_point_on_line",
                    },
                )
            )

    def _sketch_point_to_model_space(self, sketch, sketch_point):
        world_geometry = getattr(sketch_point, "worldGeometry", None)
        if world_geometry is not None:
            return world_geometry

        geometry = getattr(sketch_point, "geometry", None)
        if geometry is not None:
            try:
                model_point = sketch.sketchToModelSpace(geometry)
                if model_point is not None:
                    return model_point
            except Exception:
                pass
            if hasattr(geometry, "x") and hasattr(geometry, "y"):
                z = geometry.z if hasattr(geometry, "z") else 0.0
                return adsk.core.Point3D.create(
                    float(geometry.x), float(geometry.y), float(z)
                )

        raise ValueError("Failed to resolve sketch point model-space position.")

    def _resolve_normal_pair(self, left, right):
        left_kind = left[2]["kind"]
        right_kind = right[2]["kind"]
        left_is_line = left_kind == "line"
        right_is_line = right_kind == "line"
        right_is_curve = right_kind in {"circle", "arc"}
        left_is_curve = left_kind in {"circle", "arc"}
        right_is_nurbs = right_kind == "nurbs"
        left_is_nurbs = left_kind == "nurbs"

        if left_is_line and right_is_line:
            return "line_line", left, right
        if left_is_line and right_is_curve:
            return "line_curve", left, right
        if right_is_line and left_is_curve:
            return "line_curve", right, left
        if left_is_line and right_is_nurbs:
            return "line_nurbs", left, right
        if right_is_line and left_is_nurbs:
            return "line_nurbs", right, left
        raise ValueError(
            "normal expects [line, line], [line, circle/arc], or [line, nurbs]. "
            f"got kinds: {left_kind}, {right_kind}"
        )

    def _apply_line_nurbs_normal_constraint(
        self,
        sketch,
        sketch_index,
        geometric_constraints,
        line_item,
        curve_item,
    ):
        anchor = self._select_line_curve_endpoint_anchor(line_item, curve_item)
        tangent_direction = self._curve_endpoint_tangent_direction_in_sketch(
            sketch,
            curve_item[1],
            anchor["curve_role"],
        )
        helper_line, helper_id = self._create_anchor_helper_line(
            sketch,
            sketch_index,
            anchor["curve_point"],
            tangent_direction,
            "normal_curve_tangent_helper",
        )

        tangent = geometric_constraints.addTangent(helper_line, curve_item[1])
        tangent_id = self._register_entity(
            tangent,
            "constraint",
            sketch_index,
            metadata={
                "type": "normal",
                "mode": "line_nurbs_helper_tangent",
                "line_entity": line_item[0],
                "curve_entity": curve_item[0],
                "curve_endpoint_role": anchor["curve_role"],
            },
        )

        perpendicular = geometric_constraints.addPerpendicular(
            line_item[1], helper_line
        )
        perpendicular_id = self._register_entity(
            perpendicular,
            "constraint",
            sketch_index,
            metadata={
                "type": "normal",
                "mode": "line_nurbs_helper_perpendicular",
                "line_entity": line_item[0],
                "curve_entity": curve_item[0],
                "curve_endpoint_role": anchor["curve_role"],
            },
        )
        return [tangent_id, perpendicular_id], [helper_id]

    def _curve_endpoint_from_item(self, curve_item, role):
        _entity_id, curve_obj, entry = curve_item
        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        if isinstance(metadata, dict):
            point_id = metadata.get(f"{role}_point_id")
            if point_id:
                try:
                    point_obj, _ = self.resolve_entity(
                        point_id, expected_kinds={"point"}
                    )
                    if point_obj is not None:
                        return point_obj
                except Exception:
                    pass
        return getattr(curve_obj, f"{role}SketchPoint", None)

    def _select_line_curve_endpoint_anchor(self, line_item, curve_item, tol=1e-3):
        best = None
        for line_role in ("start", "end"):
            line_point = self._curve_endpoint_from_item(line_item, line_role)
            if line_point is None:
                continue
            line_xy = self._point2_from_sketch_point(line_point)
            if line_xy is None:
                continue
            for curve_role in ("start", "end"):
                curve_point = self._curve_endpoint_from_item(curve_item, curve_role)
                if curve_point is None:
                    continue
                curve_xy = self._point2_from_sketch_point(curve_point)
                if curve_xy is None:
                    continue
                distance = math.hypot(
                    line_xy[0] - curve_xy[0], line_xy[1] - curve_xy[1]
                )
                if best is None or distance < best["distance"]:
                    best = {
                        "distance": float(distance),
                        "line_role": line_role,
                        "line_point": line_point,
                        "curve_role": curve_role,
                        "curve_point": curve_point,
                    }

        if best is None:
            raise ValueError(
                "normal line-nurbs helper could not resolve line/curve endpoints."
            )
        if best["distance"] > tol:
            raise ValueError(
                "normal line-nurbs helper expects the line to meet a spline endpoint. "
                f"closest endpoint gap={best['distance']:.6g}"
            )
        return best

    def _curve_endpoint_tangent_direction_in_sketch(
        self, sketch, curve_obj, endpoint_role, tol=1e-9
    ):
        evaluator, coordinate_space = self._curve_evaluator_with_space(curve_obj)
        if evaluator is None:
            raise ValueError(
                "Curve does not expose an evaluator for spline normal helper."
            )

        success, min_param, max_param = evaluator.getParameterExtents()
        if not success:
            raise ValueError(
                "Failed to query spline parameter range for normal helper."
            )

        endpoint_param = (
            float(min_param) if endpoint_role == "start" else float(max_param)
        )
        tangent = None
        try:
            success, derivative = evaluator.getFirstDerivative(endpoint_param)
            if success:
                tangent = self._curve_derivative_to_sketch_xy(
                    sketch,
                    derivative,
                    coordinate_space,
                )
        except Exception:
            tangent = None

        tangent = (
            self._normalize_vector2(tangent, tol=tol) if tangent is not None else None
        )
        if tangent is not None:
            return tangent

        sampled = self._curve_endpoint_tangent_by_sampling(
            sketch,
            evaluator,
            coordinate_space,
            float(min_param),
            float(max_param),
            endpoint_role,
            tol=tol,
        )
        tangent = (
            self._normalize_vector2(sampled, tol=tol) if sampled is not None else None
        )
        if tangent is None:
            raise ValueError(
                "Failed to evaluate spline endpoint tangent for normal helper."
            )
        return tangent

    def _curve_evaluator_with_space(self, curve_obj):
        for attr_name, coordinate_space in (
            ("worldGeometry", "world"),
            ("geometry", "sketch"),
        ):
            geometry = getattr(curve_obj, attr_name, None)
            evaluator = getattr(geometry, "evaluator", None)
            if evaluator is not None:
                return evaluator, coordinate_space
        return None, None

    def _curve_derivative_to_sketch_xy(self, sketch, derivative, coordinate_space):
        if (
            derivative is None
            or not hasattr(derivative, "x")
            or not hasattr(derivative, "y")
        ):
            return None
        if coordinate_space == "world":
            try:
                sketch_vector = self._to_sketch_vector3d(
                    sketch,
                    [
                        float(derivative.x),
                        float(derivative.y),
                        float(getattr(derivative, "z", 0.0)),
                    ],
                    normalize=False,
                )
                return (float(sketch_vector.x), float(sketch_vector.y))
            except Exception:
                return None
        return (float(derivative.x), float(derivative.y))

    def _curve_endpoint_tangent_by_sampling(
        self,
        sketch,
        evaluator,
        coordinate_space,
        min_param,
        max_param,
        endpoint_role,
        tol=1e-9,
    ):
        span = float(max_param) - float(min_param)
        if abs(span) <= tol:
            return None
        delta = max(abs(span) * 1e-4, 1e-6)

        if endpoint_role == "start":
            anchor_param = float(min_param)
            sample_param = min(float(max_param), anchor_param + delta)
        else:
            anchor_param = float(max_param)
            sample_param = max(float(min_param), anchor_param - delta)
        if abs(sample_param - anchor_param) <= tol:
            return None

        success_anchor, anchor_point = evaluator.getPointAtParameter(anchor_param)
        success_sample, sample_point = evaluator.getPointAtParameter(sample_param)
        if not success_anchor or not success_sample:
            return None

        anchor_xy = self._curve_eval_point_to_sketch_xy(
            sketch, anchor_point, coordinate_space
        )
        sample_xy = self._curve_eval_point_to_sketch_xy(
            sketch, sample_point, coordinate_space
        )
        if anchor_xy is None or sample_xy is None:
            return None

        if endpoint_role == "start":
            return (sample_xy[0] - anchor_xy[0], sample_xy[1] - anchor_xy[1])
        return (anchor_xy[0] - sample_xy[0], anchor_xy[1] - sample_xy[1])

    def _curve_eval_point_to_sketch_xy(self, sketch, point, coordinate_space):
        if point is None or not hasattr(point, "x") or not hasattr(point, "y"):
            return None
        if coordinate_space == "world":
            try:
                sketch_point = sketch.modelToSketchSpace(
                    adsk.core.Point3D.create(
                        float(point.x),
                        float(point.y),
                        float(getattr(point, "z", 0.0)),
                    )
                )
                if (
                    sketch_point is not None
                    and hasattr(sketch_point, "x")
                    and hasattr(sketch_point, "y")
                ):
                    return (float(sketch_point.x), float(sketch_point.y))
            except Exception:
                pass
        return (float(point.x), float(point.y))

    def _add_dimension_constraint(
        self,
        constraint_type,
        entities,
        value,
        sketch_num,
        text_point,
        is_driving,
        extra=None,
    ):
        sketch, sketch_index, resolved = self._resolve_entities(entities, sketch_num)
        sketch_dimensions = sketch.sketchDimensions
        entity_kinds = self._normalize_kinds(resolved)
        runtime_value = value
        post_create_expression = None
        if (
            constraint_type == "distance"
            and self._normalize_distance_direction(extra) == "MINIMUM"
        ):
            runtime_value = self._normalize_minimum_distance_value(runtime_value)
            if isinstance(runtime_value, str):
                normalized_runtime_value = self._normalize_dimension_expression(
                    constraint_type,
                    runtime_value,
                )
                if (
                    isinstance(normalized_runtime_value, str)
                    and normalized_runtime_value.strip()
                ):
                    runtime_value = normalized_runtime_value
            if (
                isinstance(value, str)
                and isinstance(runtime_value, str)
                and value.strip() != runtime_value.strip()
            ):
                post_create_expression = runtime_value

        if constraint_type in {"radius", "diameter"}:
            if len(resolved) != 1 or entity_kinds[0] not in {"circle", "arc"}:
                raise ValueError(
                    f"{constraint_type} expects exactly one circle/arc entity."
                )
            existing_entry = self._find_curve_size_dimension_entry(
                resolved[0][0],
                sketch_index=sketch_index,
            )
            if existing_entry is not None:
                existing_record = self._curve_size_dimension_record(
                    existing_entry, created=False
                )
                if existing_record["measure"] == constraint_type:
                    dimension = existing_record["dimension"]
                    if runtime_value is not None:
                        self._apply_dimension_value(
                            dimension, constraint_type, runtime_value
                        )
                    return {
                        "constraint_type": constraint_type,
                        "constraint_id": existing_record["dimension_id"],
                        "constraint_ids": [existing_record["dimension_id"]],
                        "applied_count": 1,
                    }

        text_point_3d = self._build_text_point(
            sketch,
            text_point,
            resolved,
            constraint_type=constraint_type,
            extra=extra,
            runtime_value=runtime_value,
        )

        if constraint_type == "angle":
            if len(resolved) != 2 or not all(kind == "line" for kind in entity_kinds):
                raise ValueError("angle expects exactly two line entities.")
            if text_point is None and isinstance(value, (int, float)):
                runtime_angle = self._infer_runtime_angle_adjustment(
                    resolved[0][1],
                    resolved[1][1],
                )
                if runtime_angle is not None:
                    text_point_3d = runtime_angle["text_point"]
                    runtime_value = runtime_angle["value"]
            dimension = sketch_dimensions.addAngularDimension(
                resolved[0][1], resolved[1][1], text_point_3d, is_driving
            )

        elif constraint_type == "diameter":
            dimension = sketch_dimensions.addDiameterDimension(
                resolved[0][1], text_point_3d, is_driving
            )

        elif constraint_type == "radius":
            dimension = sketch_dimensions.addRadialDimension(
                resolved[0][1], text_point_3d, is_driving
            )

        elif constraint_type == "majorradius":
            if len(resolved) != 1 or entity_kinds[0] not in {
                "ellipse",
                "elliptical_arc",
            }:
                raise ValueError(
                    "majorradius expects exactly one ellipse/elliptical_arc entity."
                )
            dimension = sketch_dimensions.addEllipseMajorRadiusDimension(
                resolved[0][1], text_point_3d, is_driving
            )

        elif constraint_type == "minorradius":
            if len(resolved) != 1 or entity_kinds[0] not in {
                "ellipse",
                "elliptical_arc",
            }:
                raise ValueError(
                    "minorradius expects exactly one ellipse/elliptical_arc entity."
                )
            dimension = sketch_dimensions.addEllipseMinorRadiusDimension(
                resolved[0][1], text_point_3d, is_driving
            )

        elif constraint_type == "length":
            if len(resolved) != 1 or entity_kinds[0] != "line":
                raise ValueError("length expects exactly one line entity.")
            line_item = resolved[0]
            start_point, end_point = self._resolve_line_distance_points(line_item)
            if self._has_local_axis_directional_dimension(extra):
                dimension = self._create_local_axis_distance_dimension(
                    sketch,
                    sketch_index,
                    sketch_dimensions,
                    start_point,
                    end_point,
                    text_point_3d,
                    is_driving,
                    extra,
                )
            else:
                orientation = self._resolve_distance_orientation(extra)
                dimension = sketch_dimensions.addDistanceDimension(
                    start_point,
                    end_point,
                    orientation,
                    text_point_3d,
                    is_driving,
                )

        elif constraint_type == "distance":
            if len(resolved) == 1 and entity_kinds[0] == "line":
                # 鍗曟潯绾挎锛氱害鏉熺嚎闀匡紙閫氳繃涓ょ鐐硅窛绂诲疄鐜帮級
                line_item = resolved[0]
                start_point, end_point = self._resolve_line_distance_points(line_item)
                if self._has_local_axis_directional_dimension(extra):
                    dimension = self._create_local_axis_distance_dimension(
                        sketch,
                        sketch_index,
                        sketch_dimensions,
                        start_point,
                        end_point,
                        text_point_3d,
                        is_driving,
                        extra,
                    )
                else:
                    orientation = self._resolve_distance_orientation(extra)
                    dimension = sketch_dimensions.addDistanceDimension(
                        start_point,
                        end_point,
                        orientation,
                        text_point_3d,
                        is_driving,
                    )
            elif len(resolved) == 2:
                dimension = self._create_distance_dimension(
                    sketch,
                    sketch_index,
                    sketch_dimensions,
                    resolved[0][1],
                    resolved[1][1],
                    text_point_3d,
                    is_driving,
                    extra,
                    first_item=resolved[0],
                    second_item=resolved[1],
                    target_value=runtime_value,
                )
            else:
                raise ValueError(
                    "distance expects one line entity or exactly two entities."
                )

        else:
            raise ValueError(
                f"Unsupported dimension constraint type: {constraint_type}"
            )

        if runtime_value is not None:
            self._apply_dimension_value(dimension, constraint_type, runtime_value)

        dimension_id = self._register_entity(
            dimension,
            "dimension",
            sketch_index,
            metadata={
                "type": constraint_type,
                "target_entities": list(entities),
            },
        )
        if post_create_expression and hasattr(self, "set_dimension_expression"):
            try:
                do_events = getattr(adsk, "doEvents", None)
                if callable(do_events):
                    for _ in range(3):
                        do_events()
                validate = getattr(self, "validate_sketch_constraints", None)
                if callable(validate):
                    validate(sketch_num=sketch_num)
                if callable(do_events):
                    for _ in range(3):
                        do_events()
                self.set_dimension_expression(dimension_id, post_create_expression)
                if callable(validate):
                    validate(sketch_num=sketch_num)
            except Exception:
                pass
        return {
            "constraint_type": constraint_type,
            "constraint_id": dimension_id,
            "constraint_ids": [dimension_id],
            "applied_count": 1,
        }

    def _resolve_line_distance_points(self, line_item):
        _entity_id, line_obj, line_entry = line_item
        metadata = line_entry.get("metadata") if isinstance(line_entry, dict) else None

        if isinstance(metadata, dict):
            start_point_id = metadata.get("start_point_id")
            end_point_id = metadata.get("end_point_id")
            if start_point_id and end_point_id:
                try:
                    start_point, _ = self.resolve_entity(
                        start_point_id, expected_kinds={"point"}
                    )
                    end_point, _ = self.resolve_entity(
                        end_point_id, expected_kinds={"point"}
                    )
                    if start_point is not None and end_point is not None:
                        return start_point, end_point
                except Exception:
                    # Fall back to live line endpoints if registry references are stale.
                    pass

        start_point = getattr(line_obj, "startSketchPoint", None)
        end_point = getattr(line_obj, "endSketchPoint", None)
        if start_point is None or end_point is None:
            raise ValueError("distance expects a line with resolvable endpoints.")
        return start_point, end_point

    def _infer_runtime_angle_adjustment(self, first_line, second_line, tol=1e-6):
        line1 = self._line_segment_from_entity(first_line)
        line2 = self._line_segment_from_entity(second_line)
        if line1 is None or line2 is None:
            return None

        angle_context = self._infer_line_angle_context(line1, line2, tol=tol)
        if angle_context is None:
            return None

        angle_value = self._angle_between_rays_degrees(
            angle_context["ray1"],
            angle_context["ray2"],
            tol=tol,
        )
        if angle_value is None:
            return None

        bisector = self._angle_text_bisector(
            angle_context["ray1"],
            angle_context["ray2"],
            angle_value,
            tol=tol,
        )
        if bisector is None:
            return None

        scale = angle_context["scale"]
        shared = angle_context["shared"]
        return {
            "value": float(angle_value),
            "text_point": adsk.core.Point3D.create(
                float(shared[0] + bisector[0] * scale),
                float(shared[1] + bisector[1] * scale),
                0.0,
            ),
        }

    def _line_segment_from_entity(self, line_obj):
        start_point = getattr(line_obj, "startSketchPoint", None)
        end_point = getattr(line_obj, "endSketchPoint", None)
        if start_point is None or end_point is None:
            return None
        start = self._point2_from_sketch_point(start_point)
        end = self._point2_from_sketch_point(end_point)
        if start is None or end is None:
            return None
        return start, end

    def _point2_from_sketch_point(self, sketch_point):
        parent_sketch = getattr(sketch_point, "parentSketch", None)
        world_geometry = getattr(sketch_point, "worldGeometry", None)
        if parent_sketch is not None and world_geometry is not None:
            try:
                local_point = parent_sketch.modelToSketchSpace(world_geometry)
                if (
                    local_point is not None
                    and hasattr(local_point, "x")
                    and hasattr(local_point, "y")
                ):
                    return (float(local_point.x), float(local_point.y))
            except Exception:
                pass

        geometry = getattr(sketch_point, "geometry", None)
        if (
            geometry is None
            and hasattr(sketch_point, "x")
            and hasattr(sketch_point, "y")
        ):
            geometry = sketch_point
        if geometry is None or not hasattr(geometry, "x") or not hasattr(geometry, "y"):
            return None
        return (float(geometry.x), float(geometry.y))

    def _infer_line_angle_context(
        self,
        line1,
        line2,
        tol=1e-6,
    ):
        intersection = self._segment_line_intersection(line1, line2, tol=tol)
        if intersection is None:
            intersection = self._infinite_line_intersection(line1, line2, tol=tol)
        if intersection is None:
            return None

        ray1 = self._select_line_intersection_ray(line1, intersection["t1"], tol=tol)
        ray2 = self._select_line_intersection_ray(line2, intersection["t2"], tol=tol)
        if ray1 is None or ray2 is None:
            return None

        len1 = math.hypot(ray1[0], ray1[1])
        len2 = math.hypot(ray2[0], ray2[1])
        if len1 <= tol or len2 <= tol:
            return None

        return {
            "shared": intersection["point"],
            "ray1": ray1,
            "ray2": ray2,
            "scale": max(1.0, min(len1, len2) * 0.35),
        }

    def _segment_line_intersection(self, line1, line2, tol=1e-6):
        s1, e1 = line1
        s2, e2 = line2
        r = (e1[0] - s1[0], e1[1] - s1[1])
        s = (e2[0] - s2[0], e2[1] - s2[1])
        denom = self._cross_product2(r, s)
        if abs(denom) <= tol:
            return None

        len1 = math.hypot(r[0], r[1])
        len2 = math.hypot(s[0], s[1])
        if len1 <= tol or len2 <= tol:
            return None

        delta = (s2[0] - s1[0], s2[1] - s1[1])
        t1 = self._cross_product2(delta, s) / denom
        t2 = self._cross_product2(delta, r) / denom

        param_tol1 = tol / len1
        param_tol2 = tol / len2
        if not (
            -param_tol1 <= t1 <= 1.0 + param_tol1
            and -param_tol2 <= t2 <= 1.0 + param_tol2
        ):
            return None

        t1 = min(1.0, max(0.0, t1))
        t2 = min(1.0, max(0.0, t2))
        return {
            "point": (s1[0] + r[0] * t1, s1[1] + r[1] * t1),
            "t1": t1,
            "t2": t2,
        }

    def _infinite_line_intersection(self, line1, line2, tol=1e-6):
        s1, e1 = line1
        s2, e2 = line2
        r = (e1[0] - s1[0], e1[1] - s1[1])
        s = (e2[0] - s2[0], e2[1] - s2[1])
        denom = self._cross_product2(r, s)
        if abs(denom) <= tol:
            return None

        len1 = math.hypot(r[0], r[1])
        len2 = math.hypot(s[0], s[1])
        if len1 <= tol or len2 <= tol:
            return None

        delta = (s2[0] - s1[0], s2[1] - s1[1])
        t1 = self._cross_product2(delta, s) / denom
        t2 = self._cross_product2(delta, r) / denom
        return {
            "point": (s1[0] + r[0] * t1, s1[1] + r[1] * t1),
            "t1": t1,
            "t2": t2,
        }

    def _select_line_intersection_ray(self, line, t, tol=1e-6):
        start, end = line
        direction = (end[0] - start[0], end[1] - start[1])
        length = math.hypot(direction[0], direction[1])
        if length <= tol:
            return None

        point = (
            start[0] + direction[0] * t,
            start[1] + direction[1] * t,
        )
        param_tol = tol / length
        if t < -param_tol:
            return (start[0] - point[0], start[1] - point[1])
        if t <= 1.0 - param_tol:
            return (end[0] - point[0], end[1] - point[1])
        if t <= 1.0 + param_tol:
            return (start[0] - point[0], start[1] - point[1])
        return (end[0] - point[0], end[1] - point[1])

    def _cross_product2(self, left, right):
        return left[0] * right[1] - left[1] * right[0]

    def _normalize_vector2(self, vector, tol=1e-9):
        length = math.hypot(vector[0], vector[1])
        if length <= tol:
            return None
        return (vector[0] / length, vector[1] / length)

    def _angle_between_rays_degrees(self, ray1, ray2, tol=1e-9):
        unit1 = self._normalize_vector2(ray1, tol=tol)
        unit2 = self._normalize_vector2(ray2, tol=tol)
        if unit1 is None or unit2 is None:
            return None
        dot = max(-1.0, min(1.0, unit1[0] * unit2[0] + unit1[1] * unit2[1]))
        return math.degrees(math.acos(dot))

    def _angle_text_bisector(self, ray1, ray2, target_angle_deg, tol=1e-6):
        unit1 = self._normalize_vector2(ray1, tol=tol)
        unit2 = self._normalize_vector2(ray2, tol=tol)
        if unit1 is None or unit2 is None:
            return None

        corner_angle = self._angle_between_rays_degrees(ray1, ray2, tol=tol)
        if corner_angle is None:
            return None
        supplementary_angle = 180.0 - corner_angle

        if abs(target_angle_deg - corner_angle) <= abs(
            target_angle_deg - supplementary_angle
        ):
            candidate = (unit1[0] + unit2[0], unit1[1] + unit2[1])
        else:
            candidate = (unit1[0] - unit2[0], unit1[1] - unit2[1])
            if math.hypot(candidate[0], candidate[1]) <= tol:
                candidate = (unit2[0] - unit1[0], unit2[1] - unit1[1])

        bisector = self._normalize_vector2(candidate, tol=tol)
        if bisector is not None:
            return bisector

        perpendicular = (-unit1[1], unit1[0])
        return self._normalize_vector2(perpendicular, tol=tol)

    def _create_distance_dimension(
        self,
        sketch,
        sketch_index,
        sketch_dimensions,
        first_entity,
        second_entity,
        text_point_3d,
        is_driving,
        extra=None,
        first_item=None,
        second_item=None,
        target_value=None,
    ):
        first_is_line = self._entity_is_kind(
            first_item, first_entity, "line", self._is_sketch_line
        )
        second_is_line = self._entity_is_kind(
            second_item, second_entity, "line", self._is_sketch_line
        )
        first_is_point = self._entity_is_kind(
            first_item, first_entity, "point", self._is_sketch_point
        )
        second_is_point = self._entity_is_kind(
            second_item, second_entity, "point", self._is_sketch_point
        )
        direction = self._normalize_distance_direction(extra)

        if direction in {"HORIZONTAL", "VERTICAL"}:
            directional_dimension = self._create_directional_distance_dimension(
                sketch,
                sketch_index,
                sketch_dimensions,
                first_entity,
                second_entity,
                text_point_3d,
                is_driving,
                direction,
                extra,
            )
            if directional_dimension is not None:
                return directional_dimension

        tangent_dimension = self._create_tangent_distance_dimension(
            sketch,
            sketch_index,
            sketch_dimensions,
            first_entity,
            second_entity,
            text_point_3d,
            is_driving,
            extra,
            first_item=first_item,
            second_item=second_item,
            target_value=target_value,
        )
        if tangent_dimension is not None:
            return tangent_dimension

        if first_is_line:
            return sketch_dimensions.addOffsetDimension(
                first_entity, second_entity, text_point_3d, is_driving
            )
        if second_is_line:
            return sketch_dimensions.addOffsetDimension(
                second_entity, first_entity, text_point_3d, is_driving
            )

        if first_is_point and second_is_point:
            orientation = adsk.fusion.DimensionOrientations.AlignedDimensionOrientation
            return sketch_dimensions.addDistanceDimension(
                first_entity,
                second_entity,
                orientation,
                text_point_3d,
                is_driving,
            )

        try:
            return sketch_dimensions.addOffsetDimension(
                first_entity, second_entity, text_point_3d, is_driving
            )
        except Exception:
            orientation = adsk.fusion.DimensionOrientations.AlignedDimensionOrientation
            return sketch_dimensions.addDistanceDimension(
                first_entity,
                second_entity,
                orientation,
                text_point_3d,
                is_driving,
            )

    def _create_tangent_distance_dimension(
        self,
        sketch,
        sketch_index,
        sketch_dimensions,
        first_entity,
        second_entity,
        text_point_3d,
        is_driving,
        extra=None,
        first_item=None,
        second_item=None,
        target_value=None,
    ):
        first_is_tangent_curve = self._entity_is_tangent_curve(first_item, first_entity)
        second_is_tangent_curve = self._entity_is_tangent_curve(
            second_item, second_entity
        )
        if isinstance(extra, dict) and extra.get("concentric"):
            if not first_is_tangent_curve or not second_is_tangent_curve:
                return None
            helper_dimension = self._create_concentric_curve_distance_dimension(
                sketch,
                sketch_index,
                sketch_dimensions,
                first_entity,
                second_entity,
                text_point_3d,
                is_driving,
                extra,
                first_item=first_item,
                second_item=second_item,
            )
            if helper_dimension is not None:
                return helper_dimension
            return sketch_dimensions.addConcentricCircleDimension(
                first_entity,
                second_entity,
                text_point_3d,
                is_driving,
            )
        if not first_is_tangent_curve and not second_is_tangent_curve:
            return None
        if self._are_concentric_tangent_curves(
            first_entity,
            second_entity,
            first_item=first_item,
            second_item=second_item,
        ):
            return sketch_dimensions.addConcentricCircleDimension(
                first_entity,
                second_entity,
                text_point_3d,
                is_driving,
            )
        curve_preference = self._resolve_tangent_distance_preference(
            first_entity,
            second_entity,
            target_value,
            first_item=first_item,
            second_item=second_item,
        )
        if curve_preference is not None:
            geometry = (
                curve_preference.get("geometry")
                if isinstance(curve_preference, dict)
                else None
            )
            if (
                isinstance(geometry, dict)
                and geometry.get("selected_support_on_curve") is False
            ):
                curve_preference = None
        if second_is_tangent_curve:
            return sketch_dimensions.addTangentDistanceDimension(
                first_entity,
                curve_preference.get("first_close_to_other", True)
                if curve_preference
                else True,
                second_entity,
                curve_preference.get("second_close_to_other", True)
                if curve_preference
                else self._prefer_closest_tangent_side(extra),
                text_point_3d,
                is_driving,
            )
        return sketch_dimensions.addTangentDistanceDimension(
            second_entity,
            curve_preference.get("second_close_to_other", True)
            if curve_preference
            else True,
            first_entity,
            curve_preference.get("first_close_to_other", True)
            if curve_preference
            else self._prefer_closest_tangent_side(extra),
            text_point_3d,
            is_driving,
        )

    def _prefer_closest_tangent_side(self, extra):
        _direction = self._normalize_distance_direction(extra)
        return True

    def _create_concentric_curve_distance_dimension(
        self,
        sketch,
        sketch_index,
        sketch_dimensions,
        first_entity,
        second_entity,
        text_point_3d,
        is_driving,
        extra,
        first_item=None,
        second_item=None,
    ):
        first_kind = self._entity_kind(first_item)
        second_kind = self._entity_kind(second_item)
        if first_kind == "circle" and second_kind == "circle":
            return None

        center_point = self._tangent_curve_center_point(first_entity, item=first_item)
        if center_point is None:
            center_point = self._tangent_curve_center_point(
                second_entity, item=second_item
            )
        center_xy = self._point2_from_sketch_point(center_point)
        if center_point is None or center_xy is None:
            return None

        first_radius = self._curve_radius(first_entity)
        second_radius = self._curve_radius(second_entity)
        if first_radius is None or second_radius is None:
            return None

        direction = self._concentric_curve_dimension_direction(
            extra, first_kind, second_kind
        )
        if direction is None:
            return None

        center_geometry = getattr(center_point, "geometry", center_point)
        center_z = float(getattr(center_geometry, "z", 0.0))
        extent = max(float(first_radius), float(second_radius), 1.0) * 1.5
        ref_target = adsk.core.Point3D.create(
            float(center_xy[0] + direction[0] * extent),
            float(center_xy[1] + direction[1] * extent),
            center_z,
        )
        reference_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            center_point, ref_target
        )
        reference_line.isConstruction = True
        if hasattr(reference_line, "isFixed"):
            reference_line.isFixed = True

        first_point = sketch.sketchPoints.add(
            adsk.core.Point3D.create(
                float(center_xy[0] + direction[0] * float(first_radius)),
                float(center_xy[1] + direction[1] * float(first_radius)),
                center_z,
            )
        )
        second_point = sketch.sketchPoints.add(
            adsk.core.Point3D.create(
                float(center_xy[0] + direction[0] * float(second_radius)),
                float(center_xy[1] + direction[1] * float(second_radius)),
                center_z,
            )
        )

        geometric_constraints = sketch.geometricConstraints
        geometric_constraints.addCoincident(first_point, reference_line)
        geometric_constraints.addCoincident(second_point, reference_line)
        geometric_constraints.addCoincident(first_point, first_entity)
        geometric_constraints.addCoincident(second_point, second_entity)

        orientation = adsk.fusion.DimensionOrientations.AlignedDimensionOrientation
        return sketch_dimensions.addDistanceDimension(
            first_point,
            second_point,
            orientation,
            text_point_3d,
            is_driving,
        )

    def _concentric_curve_dimension_direction(self, extra, first_kind, second_kind):
        preferred_halfspace = None
        if first_kind == "arc":
            preferred_halfspace = self._distance_halfspace(extra, "halfSpace0")
        elif second_kind == "arc":
            preferred_halfspace = self._distance_halfspace(extra, "halfSpace1")
        if preferred_halfspace is None:
            preferred_halfspace = self._distance_halfspace(extra, "halfSpace0")
        if preferred_halfspace is None:
            preferred_halfspace = self._distance_halfspace(extra, "halfSpace1")

        if preferred_halfspace == "LEFT":
            return (-1.0, 0.0)
        if preferred_halfspace == "RIGHT":
            return (1.0, 0.0)
        if preferred_halfspace in {"TOP", "UP"}:
            return (0.0, 1.0)
        if preferred_halfspace in {"BOTTOM", "DOWN"}:
            return (0.0, -1.0)
        return (1.0, 0.0)

    def _are_concentric_tangent_curves(
        self,
        first_entity,
        second_entity,
        first_item=None,
        second_item=None,
        tol=1e-4,
    ):
        first_center = self._tangent_curve_center_point(first_entity, item=first_item)
        second_center = self._tangent_curve_center_point(
            second_entity, item=second_item
        )
        if first_center is None or second_center is None:
            return False
        first_xy = self._point2_from_sketch_point(first_center)
        second_xy = self._point2_from_sketch_point(second_center)
        if first_xy is None or second_xy is None:
            return False
        return math.hypot(first_xy[0] - second_xy[0], first_xy[1] - second_xy[1]) <= tol

    def _tangent_curve_center_point(self, entity_obj, item=None):
        if item is not None:
            _entity_id, _entity_obj, entry = item
            metadata = entry.get("metadata") if isinstance(entry, dict) else None
            if isinstance(metadata, dict):
                center_point_id = metadata.get("center_point_id")
                if center_point_id:
                    try:
                        center_point, _ = self.resolve_entity(
                            center_point_id, expected_kinds={"point"}
                        )
                        if center_point is not None:
                            return center_point
                    except Exception:
                        pass
        center_point = getattr(entity_obj, "centerSketchPoint", None)
        if center_point is not None:
            return center_point
        geometry = getattr(entity_obj, "geometry", None)
        center = getattr(geometry, "center", None)
        if center is not None:
            return center
        return None

    def _entity_kind(self, item):
        if item is None:
            return None
        try:
            return item[2].get("kind")
        except Exception:
            return None

    def _entity_is_kind(self, item, entity_obj, expected_kind, predicate):
        kind = self._entity_kind(item)
        if kind is not None:
            return kind == expected_kind
        return predicate(entity_obj)

    def _entity_is_tangent_curve(self, item, entity_obj):
        kind = self._entity_kind(item)
        if kind is not None:
            return kind in {"arc", "circle"}
        return self._is_tangent_distance_curve(entity_obj)

    def _curve_radius(self, entity_obj):
        geometry = getattr(entity_obj, "geometry", None)
        radius = getattr(geometry, "radius", None)
        if radius is not None:
            try:
                return float(radius)
            except Exception:
                pass
        center_point = getattr(entity_obj, "centerSketchPoint", None)
        start_point = getattr(entity_obj, "startSketchPoint", None)
        center_xy = self._point2_from_sketch_point(center_point)
        start_xy = self._point2_from_sketch_point(start_point)
        if center_xy is None or start_xy is None:
            return None
        return math.hypot(start_xy[0] - center_xy[0], start_xy[1] - center_xy[1])

    def _apply_dimension_value(self, dimension, constraint_type, value):
        if not hasattr(dimension, "parameter") or dimension.parameter is None:
            raise RuntimeError(
                f"{constraint_type} does not expose a writable parameter."
            )
        if isinstance(value, str):
            expression = value.strip()
            if not expression:
                return
            normalized_expression = self._normalize_dimension_expression(
                constraint_type,
                expression,
            )
            try:
                dimension.parameter.expression = expression
                return
            except Exception:
                if normalized_expression != expression:
                    try:
                        dimension.parameter.expression = normalized_expression
                        return
                    except Exception:
                        pass
                try:
                    dimension.parameter.value = float(expression)
                    return
                except Exception as error:
                    evaluated_value = self._evaluate_length_expression_value(expression)
                    if evaluated_value is None and normalized_expression != expression:
                        evaluated_value = self._evaluate_length_expression_value(
                            normalized_expression
                        )
                    if evaluated_value is not None:
                        try:
                            dimension.parameter.value = evaluated_value
                            return
                        except Exception:
                            pass
                    raise RuntimeError(
                        self._build_dimension_value_error_message(
                            constraint_type,
                            expression,
                            normalized_expression,
                        )
                    ) from error
        if constraint_type == "angle":
            try:
                dimension.parameter.expression = f"{float(value)} deg"
                return
            except Exception as error:
                raise RuntimeError(
                    f"Failed to apply degree value for angle: {value}"
                ) from error
        dimension.parameter.value = float(value)

    def _build_dimension_value_error_message(
        self,
        constraint_type,
        expression,
        normalized_expression,
    ):
        if normalized_expression and normalized_expression != expression:
            return (
                f"Failed to apply expression for {constraint_type}: {expression} "
                f"(normalized to {normalized_expression})"
            )
        return f"Failed to apply expression for {constraint_type}: {expression}"

    def _normalize_dimension_expression(self, constraint_type, expression):
        normalized_type = (constraint_type or "").strip().lower()
        if normalized_type == "angle":
            return expression
        stripped = str(expression).strip()
        if not stripped:
            return stripped
        body, unit = self._split_dimension_unit_suffix(stripped)
        canonical_body = self._canonicalize_fractional_body(body)
        value = self._evaluate_fractional_literal(canonical_body)
        if value is not None:
            numeric_text = self._format_fractional_literal(value)
            if unit:
                return f"{numeric_text} {self._canonical_dimension_unit_symbol(unit)}"
            return numeric_text
        inline_unit_expression = self._normalize_inline_unit_expression(stripped)
        if inline_unit_expression is not None:
            return inline_unit_expression
        complex_length_expression = self._normalize_complex_length_expression(stripped)
        if complex_length_expression is not None:
            return complex_length_expression
        return stripped

    def _split_dimension_unit_suffix(self, expression):
        match = self._DIMENSION_UNIT_SUFFIX_RE.fullmatch(expression)
        if match:
            return match.group("body").strip(), match.group("unit").strip()
        match = self._COMPACT_DIMENSION_UNIT_SUFFIX_RE.fullmatch(expression)
        if match:
            return match.group("body").strip(), match.group("unit").strip()
        return expression, None

    def _canonicalize_fractional_body(self, body):
        normalized = self._MIXED_FRACTION_RE.sub(
            self._mixed_fraction_replacement,
            body,
        )
        return self._HYPHENATED_MIXED_FRACTION_RE.sub(
            self._mixed_fraction_replacement,
            normalized,
        )

    def _mixed_fraction_replacement(self, match):
        sign = match.group("sign") or ""
        base = f"{match.group('whole')}+{match.group('num')}/{match.group('den')}"
        if sign == "-":
            return f"-({base})"
        if sign == "+":
            return f"+({base})"
        return base

    def _normalize_inline_unit_expression(self, expression):
        units = []

        def _strip_unit(match):
            units.append(match.group("unit").strip())
            return match.group("anchor")

        body = self._INLINE_DIMENSION_UNIT_TOKEN_RE.sub(_strip_unit, expression)
        unique_units = {unit for unit in units if unit}
        if len(unique_units) != 1:
            return None
        if not self._INLINE_DIMENSION_ALLOWED_BODY_RE.fullmatch(body):
            return None
        canonical_body = self._canonicalize_fractional_body(body)
        value = self._evaluate_fractional_literal(canonical_body)
        if value is None:
            return None
        numeric_text = self._format_fractional_literal(value)
        return f"{numeric_text} {self._canonical_dimension_unit_symbol(next(iter(unique_units)))}"

    def _normalize_complex_length_expression(self, expression):
        evaluation = self._evaluate_length_expression_cm(expression)
        if evaluation is None:
            return None
        value_cm, unit_names, has_constant, has_function = evaluation
        if len(unit_names) <= 1 and not has_constant and not has_function:
            return None
        return f"{self._format_dimension_scalar(value_cm)} cm"

    def _evaluate_length_expression_value(self, expression):
        evaluation = self._evaluate_length_expression_cm(expression)
        if evaluation is None:
            return None
        return evaluation[0]

    def _evaluate_length_expression_cm(self, expression):
        rewritten_tokens = []
        unit_names = set()
        has_constant = False
        has_function = False
        prev_token_type = None
        canonical_expression = self._canonicalize_fractional_body(
            str(expression).strip()
        )
        position = 0

        while position < len(canonical_expression):
            current = canonical_expression[position]
            if current.isspace():
                position += 1
                continue
            number_match = self._DIMENSION_NUMBER_TOKEN_RE.match(
                canonical_expression, position
            )
            if number_match is not None:
                token = number_match.group(0)
                token_type = "value"
                position = number_match.end()
            else:
                name_match = self._DIMENSION_NAME_TOKEN_RE.match(
                    canonical_expression, position
                )
                if name_match is not None:
                    raw_name = name_match.group(0)
                    normalized_name = self._normalize_dimension_name(raw_name)
                    next_position = name_match.end()
                    next_non_space = next_position
                    while (
                        next_non_space < len(canonical_expression)
                        and canonical_expression[next_non_space].isspace()
                    ):
                        next_non_space += 1
                    if (
                        normalized_name in self._DIMENSION_FUNCTIONS
                        and next_non_space < len(canonical_expression)
                        and canonical_expression[next_non_space] == "("
                    ):
                        has_function = True
                        token_type = "function"
                    elif normalized_name in self._DIMENSION_LENGTH_UNITS_CM:
                        unit_names.add(normalized_name)
                        token_type = "value"
                    elif normalized_name in self._DIMENSION_CONSTANTS:
                        has_constant = True
                        token_type = "value"
                    else:
                        return None
                    token = normalized_name
                    position = name_match.end()
                elif current in "+-*/()":
                    token = current
                    token_type = (
                        "close"
                        if current == ")"
                        else "open"
                        if current == "("
                        else "operator"
                    )
                    position += 1
                else:
                    return None

            if token_type in {"value", "open", "function"} and prev_token_type in {
                "value",
                "close",
            }:
                rewritten_tokens.append("*")
            rewritten_tokens.append(token)
            prev_token_type = token_type

        if not unit_names:
            return None
        try:
            parsed = ast.parse("".join(rewritten_tokens), mode="eval")
        except SyntaxError:
            return None
        try:
            value_cm = self._evaluate_dimension_ast(parsed.body)
        except Exception:
            return None
        if not math.isfinite(value_cm):
            return None
        return float(value_cm), unit_names, has_constant, has_function

    def _normalize_dimension_name(self, name):
        normalized = name.strip().lower()
        if normalized in {'"', "in"}:
            return "inch"
        return normalized

    def _canonical_dimension_unit_symbol(self, name):
        normalized = self._normalize_dimension_name(name)
        return self._CANONICAL_DIMENSION_UNIT_SYMBOLS.get(normalized, normalized)

    def _evaluate_dimension_ast(self, node):
        if isinstance(node, ast.BinOp):
            left = self._evaluate_dimension_ast(node.left)
            right = self._evaluate_dimension_ast(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            raise ValueError(
                f"Unsupported arithmetic operator: {type(node.op).__name__}"
            )
        if isinstance(node, ast.UnaryOp):
            operand = self._evaluate_dimension_ast(node.operand)
            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Num) and isinstance(node.n, (int, float)):
            return float(node.n)
        if isinstance(node, ast.Name):
            symbol = node.id.lower()
            if symbol in self._DIMENSION_LENGTH_UNITS_CM:
                return float(self._DIMENSION_LENGTH_UNITS_CM[symbol])
            if symbol in self._DIMENSION_CONSTANTS:
                return float(self._DIMENSION_CONSTANTS[symbol])
            raise ValueError(f"Unsupported identifier: {node.id}")
        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.keywords
                or len(node.args) != 1
            ):
                raise ValueError("Unsupported function call signature.")
            function_name = node.func.id.lower()
            function = self._DIMENSION_FUNCTIONS.get(function_name)
            if function is None:
                raise ValueError(f"Unsupported function: {node.func.id}")
            argument = self._evaluate_dimension_ast(node.args[0])
            result = float(function(argument))
            if not math.isfinite(result):
                raise ValueError("Non-finite function result is not supported.")
            return result
        raise ValueError(f"Unsupported expression node: {type(node).__name__}")

    def _evaluate_fractional_literal(self, body):
        if not self._INLINE_DIMENSION_ALLOWED_BODY_RE.fullmatch(body):
            return None
        try:
            parsed = ast.parse(body, mode="eval")
        except SyntaxError:
            return None
        try:
            return self._evaluate_fractional_ast(parsed.body)
        except Exception:
            return None

    def _evaluate_fractional_ast(self, node):
        if isinstance(node, ast.BinOp):
            left = self._evaluate_fractional_ast(node.left)
            right = self._evaluate_fractional_ast(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            raise ValueError(
                f"Unsupported arithmetic operator: {type(node.op).__name__}"
            )
        if isinstance(node, ast.UnaryOp):
            operand = self._evaluate_fractional_ast(node.operand)
            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise ValueError("Non-finite numeric literal is not supported.")
            return Fraction(str(node.value))
        if isinstance(node, ast.Num) and isinstance(node.n, (int, float)):
            if isinstance(node.n, float) and not math.isfinite(node.n):
                raise ValueError("Non-finite numeric literal is not supported.")
            return Fraction(str(node.n))
        raise ValueError(f"Unsupported expression node: {type(node).__name__}")

    def _format_fractional_literal(self, value):
        return f"{float(value):.15g}"

    def _format_dimension_scalar(self, value):
        return f"{float(value):.15g}"

    def _normalize_distance_direction(self, extra):
        if not isinstance(extra, dict):
            return None
        direction = extra.get("direction")
        if not isinstance(direction, str):
            return None
        normalized = direction.strip().upper()
        return normalized or None

    def _resolve_distance_orientation(self, extra):
        direction = self._normalize_distance_direction(extra)
        if direction == "HORIZONTAL":
            return adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation
        if direction == "VERTICAL":
            return adsk.fusion.DimensionOrientations.VerticalDimensionOrientation
        return adsk.fusion.DimensionOrientations.AlignedDimensionOrientation

    def _create_directional_distance_dimension(
        self,
        sketch,
        sketch_index,
        sketch_dimensions,
        first_entity,
        second_entity,
        text_point_3d,
        is_driving,
        direction,
        extra,
    ):
        first_anchor = self._resolve_directional_distance_anchor(
            sketch,
            first_entity,
            direction,
            self._distance_halfspace(extra, "halfSpace0"),
            extra,
            sketch_index=sketch_index,
        )
        second_anchor = self._resolve_directional_distance_anchor(
            sketch,
            second_entity,
            direction,
            self._distance_halfspace(extra, "halfSpace1"),
            extra,
            sketch_index=sketch_index,
        )
        if first_anchor is None or second_anchor is None:
            return None
        if self._has_local_axis_directional_dimension(extra):
            return self._create_local_axis_distance_dimension(
                sketch,
                sketch_index,
                sketch_dimensions,
                first_anchor,
                second_anchor,
                text_point_3d,
                is_driving,
                extra,
            )
        orientation = self._resolve_distance_orientation(extra)
        return sketch_dimensions.addDistanceDimension(
            first_anchor,
            second_anchor,
            orientation,
            text_point_3d,
            is_driving,
        )

    def _distance_halfspace(self, extra, key):
        if not isinstance(extra, dict):
            return None
        value = extra.get(key)
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        return normalized or None

    def _normalize_minimum_distance_value(self, value):
        if isinstance(value, (int, float)):
            return abs(float(value))
        if not isinstance(value, str):
            return value
        expression = value.strip()
        if not expression:
            return value
        evaluated_value = self._evaluate_length_expression_value(expression)
        if evaluated_value is None or evaluated_value >= 0:
            return value
        if expression.startswith("-"):
            return expression[1:].lstrip()
        normalized_expression = self._normalize_dimension_expression(
            "distance", expression
        )
        if isinstance(normalized_expression, str):
            normalized_expression = normalized_expression.strip()
            if normalized_expression.startswith("-"):
                return normalized_expression[1:].lstrip()
        return abs(float(evaluated_value))

    def _distance_target_magnitude(self, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return abs(float(value))
        if not isinstance(value, str):
            return None
        evaluated_value = self._evaluate_length_expression_value(value)
        if evaluated_value is None:
            normalized_value = self._normalize_dimension_expression("distance", value)
            if isinstance(normalized_value, str) and normalized_value.strip():
                evaluated_value = self._evaluate_length_expression_value(
                    normalized_value
                )
                if evaluated_value is None:
                    body, unit = self._split_dimension_unit_suffix(normalized_value)
                    scalar_value = self._evaluate_fractional_literal(
                        self._canonicalize_fractional_body(body.strip())
                    )
                    unit_key = self._normalize_dimension_name(unit) if unit else None
                    scale_cm = self._DIMENSION_LENGTH_UNITS_CM.get(unit_key)
                    if scalar_value is not None and scale_cm is not None:
                        evaluated_value = float(scalar_value) * float(scale_cm)
        if evaluated_value is None:
            return None
        return abs(float(evaluated_value))

    def _normalize_angle_positive(self, angle):
        full_turn = math.tau if hasattr(math, "tau") else math.pi * 2.0
        normalized = math.fmod(float(angle), full_turn)
        if normalized < 0.0:
            normalized += full_turn
        return normalized

    def _angle_on_ccw_sweep(self, start_angle, end_angle, test_angle, tol=1e-9):
        full_turn = math.tau if hasattr(math, "tau") else math.pi * 2.0
        start = self._normalize_angle_positive(start_angle)
        end = self._normalize_angle_positive(end_angle)
        test = self._normalize_angle_positive(test_angle)
        if end < start - tol:
            end += full_turn
        if test < start - tol:
            test += full_turn
        return start - tol <= test <= end + tol

    def _curve_point_from_item_metadata(
        self, entity_obj, item, metadata_key, attr_name
    ):
        if item is not None:
            try:
                metadata = item[2].get("metadata")
            except Exception:
                metadata = None
            if isinstance(metadata, dict):
                point_id = metadata.get(metadata_key)
                if point_id:
                    try:
                        point_obj, _ = self.resolve_entity(
                            point_id, expected_kinds={"point"}
                        )
                        if point_obj is not None:
                            return point_obj
                    except Exception:
                        pass
        return getattr(entity_obj, attr_name, None)

    def _arc_support_point_is_on_entity(
        self, entity_obj, support_point_xy, item=None, tol=1e-6
    ):
        if not self._entity_is_kind(item, entity_obj, "arc", self._is_sketch_arc):
            return True

        center_point = self._tangent_curve_center_point(entity_obj, item=item)
        start_point = self._curve_point_from_item_metadata(
            entity_obj,
            item,
            "start_point_id",
            "startSketchPoint",
        )
        end_point = self._curve_point_from_item_metadata(
            entity_obj,
            item,
            "end_point_id",
            "endSketchPoint",
        )
        mid_point = self._curve_point_from_item_metadata(
            entity_obj,
            item,
            "mid_point_id",
            "midSketchPoint",
        )
        center_xy = self._point2_from_sketch_point(center_point)
        start_xy = self._point2_from_sketch_point(start_point)
        end_xy = self._point2_from_sketch_point(end_point)
        mid_xy = self._point2_from_sketch_point(mid_point)
        if (
            center_xy is None
            or start_xy is None
            or end_xy is None
            or mid_xy is None
            or support_point_xy is None
        ):
            return True

        start_angle = math.atan2(start_xy[1] - center_xy[1], start_xy[0] - center_xy[0])
        end_angle = math.atan2(end_xy[1] - center_xy[1], end_xy[0] - center_xy[0])
        mid_angle = math.atan2(mid_xy[1] - center_xy[1], mid_xy[0] - center_xy[0])
        support_angle = math.atan2(
            float(support_point_xy[1]) - center_xy[1],
            float(support_point_xy[0]) - center_xy[0],
        )
        if self._angle_on_ccw_sweep(start_angle, end_angle, mid_angle, tol=tol):
            return self._angle_on_ccw_sweep(
                start_angle, end_angle, support_angle, tol=tol
            )
        return self._angle_on_ccw_sweep(end_angle, start_angle, support_angle, tol=tol)

    def _project_point_to_line(self, line_start, line_end, point_xy, tol=1e-9):
        dx = float(line_end[0] - line_start[0])
        dy = float(line_end[1] - line_start[1])
        denom = dx * dx + dy * dy
        if denom <= tol:
            return None
        t = (
            (point_xy[0] - line_start[0]) * dx + (point_xy[1] - line_start[1]) * dy
        ) / denom
        return (line_start[0] + dx * t, line_start[1] + dy * t)

    def _line_curve_tangent_choice(
        self, line_entity, curve_entity, target_value, curve_item=None
    ):
        line_segment = self._line_segment_from_entity(line_entity)
        center_point = self._tangent_curve_center_point(curve_entity)
        center_xy = self._point2_from_sketch_point(center_point)
        curve_radius = self._curve_radius(curve_entity)
        if line_segment is None or center_xy is None or curve_radius is None:
            return None

        line_start, line_end = line_segment
        direction = self._normalize_vector2(
            (line_end[0] - line_start[0], line_end[1] - line_start[1])
        )
        if direction is None:
            return None
        normal_left = (-direction[1], direction[0])
        center_offset = (center_xy[0] - line_start[0]) * normal_left[0] + (
            center_xy[1] - line_start[1]
        ) * normal_left[1]
        if abs(center_offset) <= 1e-9:
            return None

        center_sign = 1.0 if center_offset >= 0.0 else -1.0
        normal_to_curve = (normal_left[0] * center_sign, normal_left[1] * center_sign)
        near_distance = abs(abs(center_offset) - float(curve_radius))
        far_distance = abs(abs(center_offset) + float(curve_radius))
        target_magnitude = self._distance_target_magnitude(target_value)
        use_near_side = True
        if target_magnitude is not None:
            use_near_side = abs(target_magnitude - near_distance) <= abs(
                target_magnitude - far_distance
            )
        projected_center = self._project_point_to_line(line_start, line_end, center_xy)
        if projected_center is None:
            return None
        near_support = (
            float(center_xy[0]) - normal_to_curve[0] * float(curve_radius),
            float(center_xy[1]) - normal_to_curve[1] * float(curve_radius),
        )
        far_support = (
            float(center_xy[0]) + normal_to_curve[0] * float(curve_radius),
            float(center_xy[1]) + normal_to_curve[1] * float(curve_radius),
        )
        selected_support = near_support if use_near_side else far_support
        return {
            "close_to_other": bool(use_near_side),
            "distance": near_distance if use_near_side else far_distance,
            "normal": normal_to_curve
            if use_near_side
            else (-normal_to_curve[0], -normal_to_curve[1]),
            "line_direction": direction,
            "line_intersects_support_circle": bool(
                abs(center_offset) < float(curve_radius) - 1e-9
            ),
            "line_projection": projected_center,
            "radius": float(curve_radius),
            "curve_support_point": selected_support,
            "selected_support_on_curve": self._arc_support_point_is_on_entity(
                curve_entity,
                selected_support,
                item=curve_item,
            ),
        }

    def _curve_curve_tangent_choice(self, first_entity, second_entity, target_value):
        first_center = self._tangent_curve_center_point(first_entity)
        second_center = self._tangent_curve_center_point(second_entity)
        first_xy = self._point2_from_sketch_point(first_center)
        second_xy = self._point2_from_sketch_point(second_center)
        first_radius = self._curve_radius(first_entity)
        second_radius = self._curve_radius(second_entity)
        if (
            first_xy is None
            or second_xy is None
            or first_radius is None
            or second_radius is None
        ):
            return None

        axis = (float(second_xy[0] - first_xy[0]), float(second_xy[1] - first_xy[1]))
        axis_unit = self._normalize_vector2(axis)
        if axis_unit is None:
            return None
        center_distance = math.hypot(axis[0], axis[1])
        target_magnitude = self._distance_target_magnitude(target_value)
        candidates = [
            {
                "first_close_to_other": True,
                "second_close_to_other": True,
                "distance": abs(
                    center_distance - float(first_radius) - float(second_radius)
                ),
            },
            {
                "first_close_to_other": True,
                "second_close_to_other": False,
                "distance": abs(
                    center_distance - float(first_radius) + float(second_radius)
                ),
            },
            {
                "first_close_to_other": False,
                "second_close_to_other": True,
                "distance": abs(
                    center_distance + float(first_radius) - float(second_radius)
                ),
            },
            {
                "first_close_to_other": False,
                "second_close_to_other": False,
                "distance": abs(
                    center_distance + float(first_radius) + float(second_radius)
                ),
            },
        ]
        if target_magnitude is None:
            chosen = min(candidates, key=lambda item: item["distance"])
        else:
            chosen = min(
                candidates,
                key=lambda item: abs(float(item["distance"]) - target_magnitude),
            )
        chosen = dict(chosen)
        chosen.update(
            {
                "axis_unit": axis_unit,
                "first_center_xy": first_xy,
                "second_center_xy": second_xy,
                "first_radius": float(first_radius),
                "second_radius": float(second_radius),
            }
        )
        return chosen

    def _resolve_tangent_distance_preference(
        self,
        first_entity,
        second_entity,
        target_value,
        first_item=None,
        second_item=None,
    ):
        first_is_line = self._entity_is_kind(
            first_item, first_entity, "line", self._is_sketch_line
        )
        second_is_line = self._entity_is_kind(
            second_item, second_entity, "line", self._is_sketch_line
        )
        first_is_curve = self._entity_is_tangent_curve(first_item, first_entity)
        second_is_curve = self._entity_is_tangent_curve(second_item, second_entity)

        if first_is_line and second_is_curve:
            choice = self._line_curve_tangent_choice(
                first_entity,
                second_entity,
                target_value,
                curve_item=second_item,
            )
            if choice is None:
                return None
            return {
                "first_close_to_other": True,
                "second_close_to_other": choice["close_to_other"],
                "mode": "line_curve",
                "geometry": choice,
            }
        if second_is_line and first_is_curve:
            choice = self._line_curve_tangent_choice(
                second_entity,
                first_entity,
                target_value,
                curve_item=first_item,
            )
            if choice is None:
                return None
            return {
                "first_close_to_other": choice["close_to_other"],
                "second_close_to_other": True,
                "mode": "curve_line",
                "geometry": choice,
            }
        if first_is_curve and second_is_curve:
            choice = self._curve_curve_tangent_choice(
                first_entity, second_entity, target_value
            )
            if choice is None:
                return None
            return {
                "first_close_to_other": choice["first_close_to_other"],
                "second_close_to_other": choice["second_close_to_other"],
                "mode": "curve_curve",
                "geometry": choice,
            }
        return None

    def _create_line_curve_parallel_tangent_helper_dimension(
        self,
        sketch,
        sketch_index,
        sketch_dimensions,
        line_entity,
        curve_entity,
        text_point_3d,
        is_driving,
        curve_preference,
    ):
        if sketch is None or getattr(sketch, "geometricConstraints", None) is None:
            return None
        sketch_lines = getattr(
            getattr(sketch, "sketchCurves", None), "sketchLines", None
        )
        if sketch_lines is None:
            return None

        geometry = (
            curve_preference.get("geometry")
            if isinstance(curve_preference, dict)
            else None
        )
        if not isinstance(geometry, dict):
            return None

        support_point = geometry.get("curve_support_point")
        line_direction = geometry.get("line_direction")
        curve_radius = geometry.get("radius")
        if (
            not isinstance(support_point, (list, tuple))
            or len(support_point) < 2
            or not isinstance(line_direction, (list, tuple))
            or len(line_direction) < 2
        ):
            return None

        direction_x = float(line_direction[0])
        direction_y = float(line_direction[1])
        direction_length = math.hypot(direction_x, direction_y)
        if direction_length <= 1e-9:
            return None
        direction_x /= direction_length
        direction_y /= direction_length

        line_segment = self._line_segment_from_entity(line_entity)
        if line_segment is None:
            return None
        line_start, line_end = line_segment
        line_length = math.hypot(
            float(line_end[0]) - float(line_start[0]),
            float(line_end[1]) - float(line_start[1]),
        )
        helper_half_span = max(
            line_length * 0.75, float(curve_radius or 0.0) * 4.0, 1.0
        )
        support_x = float(support_point[0])
        support_y = float(support_point[1])
        helper_start = adsk.core.Point3D.create(
            support_x - direction_x * helper_half_span,
            support_y - direction_y * helper_half_span,
            0.0,
        )
        helper_end = adsk.core.Point3D.create(
            support_x + direction_x * helper_half_span,
            support_y + direction_y * helper_half_span,
            0.0,
        )

        helper_line = sketch_lines.addByTwoPoints(helper_start, helper_end)
        helper_line.isConstruction = True
        self._register_entity(
            helper_line,
            "line",
            sketch_index,
            metadata={"auto_created_for": "line_curve_tangent_offset_helper"},
        )

        geometric_constraints = sketch.geometricConstraints
        parallel_constraint = geometric_constraints.addParallel(
            helper_line, line_entity
        )
        self._register_entity(
            parallel_constraint,
            "constraint",
            sketch_index,
            metadata={
                "type": "distance",
                "mode": "line_curve_tangent_offset_helper_parallel",
            },
        )
        tangent_constraint = geometric_constraints.addTangent(helper_line, curve_entity)
        self._register_entity(
            tangent_constraint,
            "constraint",
            sketch_index,
            metadata={
                "type": "distance",
                "mode": "line_curve_tangent_offset_helper_tangent",
            },
        )
        return sketch_dimensions.addOffsetDimension(
            line_entity,
            helper_line,
            text_point_3d,
            is_driving,
        )

    def _resolve_directional_distance_anchor(
        self,
        sketch,
        entity_obj,
        direction,
        halfspace=None,
        extra=None,
        sketch_index=None,
    ):
        if self._is_sketch_point(entity_obj):
            return entity_obj

        center_point = getattr(entity_obj, "centerSketchPoint", None)
        if center_point is not None:
            return center_point

        if self._is_sketch_line(entity_obj):
            start_point = getattr(entity_obj, "startSketchPoint", None)
            end_point = getattr(entity_obj, "endSketchPoint", None)
            if start_point is None or end_point is None:
                return None
            if self._has_local_axis_directional_dimension(extra):
                axis_sketch = self._local_axis_direction_in_sketch_space(
                    sketch,
                    extra,
                    sketch_index=sketch_index,
                )
                return self._select_line_endpoint_for_local_axis(
                    start_point,
                    end_point,
                    axis_sketch,
                    direction,
                    halfspace,
                )
            return self._select_line_endpoint_for_direction(
                start_point,
                end_point,
                direction,
                halfspace,
            )
        return None

    def _local_axis_direction_in_sketch_space(self, sketch, extra, sketch_index=None):
        frame = self._extract_local_axis_frame(extra)
        if frame is None:
            raise ValueError("Missing local axis frame for directional dimension.")
        origin, direction = frame
        projected = self._project_model_direction_to_sketch_space(
            sketch,
            origin,
            direction,
        )
        if projected is not None:
            return projected
        if sketch_index is not None:
            projected = self._project_world_direction_onto_attitude(
                sketch_index,
                direction,
            )
            if projected is not None:
                return projected
        raise ValueError("Local axis direction collapses in sketch space.")

    def _project_model_direction_to_sketch_space(self, sketch, origin, direction):
        origin_model = adsk.core.Point3D.create(
            float(origin[0]),
            float(origin[1]),
            float(origin[2]),
        )
        target_model = adsk.core.Point3D.create(
            float(origin[0]) + float(direction[0]),
            float(origin[1]) + float(direction[1]),
            float(origin[2]) + float(direction[2]),
        )
        try:
            sketch_origin = sketch.modelToSketchSpace(origin_model)
            sketch_target = sketch.modelToSketchSpace(target_model)
        except Exception:
            return None
        vx = float(sketch_target.x) - float(sketch_origin.x)
        vy = float(sketch_target.y) - float(sketch_origin.y)
        length_xy = math.sqrt(vx * vx + vy * vy)
        if length_xy <= 1e-12:
            return None
        return (vx / length_xy, vy / length_xy)

    def _project_world_direction_onto_attitude(self, sketch_index, direction):
        attitude = self._get_sketch_attitude(sketch_index)
        if not attitude:
            return None
        _origin_point, u_dir, v_dir = attitude
        world_direction = self._normalize_vector3d(direction)
        proj_u = (
            float(world_direction.x) * float(u_dir.x)
            + float(world_direction.y) * float(u_dir.y)
            + float(world_direction.z) * float(u_dir.z)
        )
        proj_v = (
            float(world_direction.x) * float(v_dir.x)
            + float(world_direction.y) * float(v_dir.y)
            + float(world_direction.z) * float(v_dir.z)
        )
        length_xy = math.sqrt(proj_u * proj_u + proj_v * proj_v)
        if length_xy <= 1e-12:
            return None
        return (proj_u / length_xy, proj_v / length_xy)

    def _select_line_endpoint_for_local_axis(
        self, start_point, end_point, axis_sketch, direction, halfspace=None
    ):
        start_geometry = getattr(start_point, "geometry", None)
        end_geometry = getattr(end_point, "geometry", None)
        if start_geometry is None or end_geometry is None:
            return start_point

        start_proj = (
            float(start_geometry.x) * axis_sketch[0]
            + float(start_geometry.y) * axis_sketch[1]
        )
        end_proj = (
            float(end_geometry.x) * axis_sketch[0]
            + float(end_geometry.y) * axis_sketch[1]
        )
        choose_max = False
        if direction == "HORIZONTAL" and halfspace == "RIGHT":
            choose_max = True
        if direction == "VERTICAL" and halfspace in {"TOP", "UP"}:
            choose_max = True
        if choose_max:
            return start_point if start_proj >= end_proj else end_point
        return start_point if start_proj <= end_proj else end_point

    def _sketch_space_perpendicular(self, axis_sketch):
        px = -float(axis_sketch[1])
        py = float(axis_sketch[0])
        length = math.sqrt(px * px + py * py)
        if length <= 1e-12:
            raise ValueError("Failed to compute perpendicular sketch direction.")
        return (px / length, py / length)

    def _local_axis_reference_cache_key(self, sketch_index, axis_sketch, tag):
        return (
            tag,
            sketch_index,
            round(float(axis_sketch[0]), 9),
            round(float(axis_sketch[1]), 9),
        )

    def _get_or_create_sketch_space_reference_line(
        self, sketch, sketch_index, axis_sketch, tag
    ):
        cache_key = self._local_axis_reference_cache_key(sketch_index, axis_sketch, tag)
        cached_id = self._local_axis_helper_cache.get(cache_key)
        if cached_id is not None:
            cached_obj, _ = self.resolve_entity(cached_id, expected_kinds={"line"})
            return cached_obj, cached_id

        start_point = adsk.core.Point3D.create(0.0, 0.0, 0.0)
        end_point = adsk.core.Point3D.create(
            float(axis_sketch[0]) * 10.0, float(axis_sketch[1]) * 10.0, 0.0
        )
        helper_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            start_point, end_point
        )
        helper_line.isConstruction = True
        if hasattr(helper_line, "isFixed"):
            helper_line.isFixed = True
        helper_id = self._register_entity(
            helper_line,
            "line",
            sketch_index,
            metadata={"auto_created_for": tag},
        )
        self._local_axis_helper_cache[cache_key] = helper_id
        return helper_line, helper_id

    def _create_anchor_helper_line(
        self, sketch, sketch_index, anchor_point, direction_xy, tag
    ):
        anchor_geometry = getattr(anchor_point, "geometry", None)
        if anchor_geometry is None:
            raise ValueError("Anchor point does not expose sketch geometry.")
        helper_end = adsk.core.Point3D.create(
            float(anchor_geometry.x) + float(direction_xy[0]) * 10.0,
            float(anchor_geometry.y) + float(direction_xy[1]) * 10.0,
            float(anchor_geometry.z if hasattr(anchor_geometry, "z") else 0.0),
        )
        helper_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            anchor_point, helper_end
        )
        helper_line.isConstruction = True
        helper_id = self._register_entity(
            helper_line,
            "line",
            sketch_index,
            metadata={"auto_created_for": tag},
        )
        return helper_line, helper_id

    def _create_local_axis_distance_dimension(
        self,
        sketch,
        sketch_index,
        sketch_dimensions,
        first_anchor,
        second_anchor,
        text_point_3d,
        is_driving,
        extra,
    ):
        axis_sketch = self._local_axis_direction_in_sketch_space(
            sketch,
            extra,
            sketch_index=sketch_index,
        )
        perp_sketch = self._sketch_space_perpendicular(axis_sketch)
        reference_line, _reference_id = self._get_or_create_sketch_space_reference_line(
            sketch,
            sketch_index,
            perp_sketch,
            "directional_dimension_reference",
        )
        first_helper, _first_helper_id = self._create_anchor_helper_line(
            sketch,
            sketch_index,
            first_anchor,
            perp_sketch,
            "directional_dimension_anchor",
        )
        second_helper, _second_helper_id = self._create_anchor_helper_line(
            sketch,
            sketch_index,
            second_anchor,
            perp_sketch,
            "directional_dimension_anchor",
        )
        sketch.geometricConstraints.addParallel(first_helper, reference_line)
        sketch.geometricConstraints.addParallel(second_helper, reference_line)
        return sketch_dimensions.addOffsetDimension(
            first_helper,
            second_helper,
            text_point_3d,
            is_driving,
        )

    def _select_line_endpoint_for_direction(
        self, start_point, end_point, direction, halfspace=None
    ):
        start_geometry = getattr(start_point, "geometry", None)
        end_geometry = getattr(end_point, "geometry", None)
        if start_geometry is None or end_geometry is None:
            return start_point

        if direction == "HORIZONTAL":
            if halfspace == "RIGHT":
                return start_point if start_geometry.x >= end_geometry.x else end_point
            return start_point if start_geometry.x <= end_geometry.x else end_point

        if direction == "VERTICAL":
            if halfspace in {"TOP", "UP"}:
                return start_point if start_geometry.y >= end_geometry.y else end_point
            return start_point if start_geometry.y <= end_geometry.y else end_point

        return start_point

    def _object_type_matches(self, entity_obj, adsk_type_name):
        object_type = getattr(entity_obj, "objectType", None)
        if not isinstance(object_type, str):
            return False
        if object_type == adsk_type_name:
            return True
        return (
            object_type.endswith(f"::{adsk_type_name}") or adsk_type_name in object_type
        )

    def _is_sketch_line(self, entity_obj):
        class_type = getattr(
            getattr(adsk.fusion, "SketchLine", None), "classType", None
        )
        if class_type is not None and hasattr(entity_obj, "objectType"):
            try:
                if entity_obj.objectType == class_type():
                    return True
            except Exception:
                pass
        return self._object_type_matches(entity_obj, "SketchLine")

    def _is_sketch_point(self, entity_obj):
        class_type = getattr(
            getattr(adsk.fusion, "SketchPoint", None), "classType", None
        )
        if class_type is not None and hasattr(entity_obj, "objectType"):
            try:
                if entity_obj.objectType == class_type():
                    return True
            except Exception:
                pass
        return self._object_type_matches(entity_obj, "SketchPoint")

    def _is_sketch_arc(self, entity_obj):
        class_type = getattr(getattr(adsk.fusion, "SketchArc", None), "classType", None)
        if class_type is not None and hasattr(entity_obj, "objectType"):
            try:
                if entity_obj.objectType == class_type():
                    return True
            except Exception:
                pass
        return self._object_type_matches(entity_obj, "SketchArc")

    def _is_sketch_circle(self, entity_obj):
        class_type = getattr(
            getattr(adsk.fusion, "SketchCircle", None), "classType", None
        )
        if class_type is not None and hasattr(entity_obj, "objectType"):
            try:
                if entity_obj.objectType == class_type():
                    return True
            except Exception:
                pass
        return self._object_type_matches(entity_obj, "SketchCircle")

    def _is_tangent_distance_curve(self, entity_obj):
        return self._is_sketch_arc(entity_obj) or self._is_sketch_circle(entity_obj)

    def _build_text_point(
        self,
        sketch,
        text_point,
        resolved_entities,
        constraint_type=None,
        extra=None,
        runtime_value=None,
    ):
        if text_point is not None:
            return self._to_sketch_point3d(sketch, text_point)

        if constraint_type == "distance":
            distance_point = self._build_distance_text_point(
                resolved_entities,
                extra,
                runtime_value,
            )
            if distance_point is not None:
                return distance_point

        ref_points = [
            self._entity_reference_point(entity_obj)
            for _, entity_obj, _ in resolved_entities
        ]
        if not ref_points:
            return adsk.core.Point3D.create(1.0, 1.0, 0.0)

        avg_x = sum(point.x for point in ref_points) / len(ref_points)
        avg_y = sum(point.y for point in ref_points) / len(ref_points)
        avg_z = sum(point.z for point in ref_points) / len(ref_points)
        return adsk.core.Point3D.create(avg_x + 1.0, avg_y + 1.0, avg_z)

    def _build_distance_text_point(self, resolved_entities, extra, runtime_value):
        if len(resolved_entities) != 2:
            return None
        if self._normalize_distance_direction(extra) != "MINIMUM":
            return None

        first_item, second_item = resolved_entities
        first_entity = first_item[1]
        second_entity = second_item[1]
        first_is_line = self._entity_is_kind(
            first_item, first_entity, "line", self._is_sketch_line
        )
        second_is_line = self._entity_is_kind(
            second_item, second_entity, "line", self._is_sketch_line
        )

        if first_is_line and second_is_line:
            return self._build_minimum_line_line_text_point(first_entity, second_entity)

        preference = self._resolve_tangent_distance_preference(
            first_entity,
            second_entity,
            runtime_value,
            first_item=first_item,
            second_item=second_item,
        )
        if preference is None:
            return None

        geometry = preference.get("geometry") if isinstance(preference, dict) else None
        if not isinstance(geometry, dict):
            return None

        if preference.get("mode") in {"line_curve", "curve_line"}:
            if geometry.get("selected_support_on_curve") is False:
                return None
            projection = geometry.get("line_projection")
            support_point = geometry.get("curve_support_point")
            if projection is not None and support_point is not None:
                try:
                    return adsk.core.Point3D.create(
                        (float(projection[0]) + float(support_point[0])) * 0.5,
                        (float(projection[1]) + float(support_point[1])) * 0.5,
                        0.0,
                    )
                except Exception:
                    pass
            normal = geometry.get("normal")
            if projection is None or normal is None:
                return None
            distance = float(geometry.get("distance") or 0.0)
            if distance <= 1e-9:
                distance = max(0.05, float(geometry.get("radius") or 0.0) * 0.25)
            return adsk.core.Point3D.create(
                float(projection[0]) + float(normal[0]) * distance * 0.5,
                float(projection[1]) + float(normal[1]) * distance * 0.5,
                0.0,
            )

        if preference.get("mode") == "curve_curve":
            axis = geometry.get("axis_unit")
            first_center = geometry.get("first_center_xy")
            second_center = geometry.get("second_center_xy")
            if axis is None or first_center is None or second_center is None:
                return None
            first_radius = float(geometry.get("first_radius") or 0.0)
            second_radius = float(geometry.get("second_radius") or 0.0)
            first_sign = 1.0 if preference.get("first_close_to_other", True) else -1.0
            second_sign = -1.0 if preference.get("second_close_to_other", True) else 1.0
            first_support = (
                float(first_center[0]) + float(axis[0]) * first_radius * first_sign,
                float(first_center[1]) + float(axis[1]) * first_radius * first_sign,
            )
            second_support = (
                float(second_center[0]) + float(axis[0]) * second_radius * second_sign,
                float(second_center[1]) + float(axis[1]) * second_radius * second_sign,
            )
            return adsk.core.Point3D.create(
                (first_support[0] + second_support[0]) * 0.5,
                (first_support[1] + second_support[1]) * 0.5,
                0.0,
            )

        return None

    def _build_minimum_line_line_text_point(self, first_line, second_line):
        first_segment = self._line_segment_from_entity(first_line)
        second_segment = self._line_segment_from_entity(second_line)
        if first_segment is None or second_segment is None:
            return None
        first_start, first_end = first_segment
        second_start, second_end = second_segment
        second_mid = (
            (second_start[0] + second_end[0]) * 0.5,
            (second_start[1] + second_end[1]) * 0.5,
        )
        projected = self._project_point_to_line(first_start, first_end, second_mid)
        if projected is None:
            return None
        gap_x = second_mid[0] - projected[0]
        gap_y = second_mid[1] - projected[1]
        if math.hypot(gap_x, gap_y) <= 1e-9:
            direction = self._normalize_vector2(
                (first_end[0] - first_start[0], first_end[1] - first_start[1])
            )
            if direction is None:
                return None
            gap_x = -direction[1] * 0.05
            gap_y = direction[0] * 0.05
        return adsk.core.Point3D.create(
            float(projected[0]) + gap_x * 0.5,
            float(projected[1]) + gap_y * 0.5,
            0.0,
        )

    def _entity_reference_point(self, entity_obj):
        try:
            bbox = getattr(entity_obj, "boundingBox", None)
            if bbox is not None:
                min_point = bbox.minPoint
                max_point = bbox.maxPoint
                return adsk.core.Point3D.create(
                    (min_point.x + max_point.x) * 0.5,
                    (min_point.y + max_point.y) * 0.5,
                    (min_point.z + max_point.z) * 0.5,
                )
        except Exception:
            pass

        geometry = getattr(entity_obj, "geometry", None)
        if geometry is not None:
            if hasattr(geometry, "x") and hasattr(geometry, "y"):
                z = geometry.z if hasattr(geometry, "z") else 0.0
                return adsk.core.Point3D.create(geometry.x, geometry.y, z)
            if hasattr(geometry, "center"):
                center = geometry.center
                return adsk.core.Point3D.create(center.x, center.y, center.z)

        return adsk.core.Point3D.create(0.0, 0.0, 0.0)

    def _to_point3d(self, point):
        if len(point) == 2:
            return adsk.core.Point3D.create(point[0], point[1], 0.0)
        if len(point) == 3:
            return adsk.core.Point3D.create(point[0], point[1], point[2])
        raise ValueError(f"Point must have length 2 or 3, got: {point}")
