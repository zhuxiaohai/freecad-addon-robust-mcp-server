import math

import adsk.core
import adsk.fusion

from ..runtime import maybe_do_events


class CADOperations:
    def _body_volume(self, body):
        if body is None:
            return 0.0
        direct_volume = None
        try:
            direct_volume = float(body.volume)
            if math.isfinite(direct_volume) and direct_volume > 0.0:
                return direct_volume
        except Exception:
            direct_volume = None
        props_volume = None
        try:
            props_volume = float(body.physicalProperties.volume)
            if math.isfinite(props_volume) and props_volume > 0.0:
                return props_volume
        except Exception:
            props_volume = None
        fallback = direct_volume if direct_volume is not None else props_volume
        if fallback is None or not math.isfinite(fallback) or fallback < 0.0:
            return 0.0
        return fallback

    def _body_bbox(self, body):
        if body is None:
            return None
        try:
            bbox = body.boundingBox
        except Exception:
            return None
        if bbox is None:
            return None
        try:
            return {
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
            return None

    def _body_signature(self, body):
        signature = {
            "bbox": self._body_bbox(body),
            "volume": self._body_volume(body),
        }
        if body is None:
            return signature
        try:
            props = body.physicalProperties
        except Exception:
            props = None
        if props is not None:
            try:
                center = props.centerOfMass
                signature["centroid"] = [
                    float(center.x),
                    float(center.y),
                    float(center.z),
                ]
            except Exception:
                pass
            try:
                signature["area"] = float(props.area)
            except Exception:
                pass
        return signature

    def calculate_step_physical_properties(self, items):
        """Read STEP B-Rep volume/centroid facts for physical calibration."""
        if not isinstance(items, list):
            raise ValueError("items must be a list of STEP body records.")

        opened_documents = []

        def _close_document(document):
            if document is None:
                return
            try:
                document.close(False)
            except Exception:
                pass

        def _close_opened_documents():
            for document in reversed(opened_documents):
                _close_document(document)
            opened_documents.clear()
            self._refresh_design_context(create_if_missing=True)

        def _safe_float(value):
            try:
                number = float(value)
                return number if math.isfinite(number) else None
            except Exception:
                return None

        def _safe_props_float(props, name):
            if props is None:
                return None
            try:
                return _safe_float(getattr(props, name))
            except Exception:
                return None

        def _safe_inertia_diag(props):
            if props is None:
                return None
            for method_name in (
                "getXYZMomentsOfInertia",
                "getPrincipalMomentsOfInertia",
            ):
                try:
                    result = getattr(props, method_name)()
                except Exception:
                    continue
                values = result if isinstance(result, (list, tuple)) else [result]
                numbers = []
                for value in values:
                    if isinstance(value, bool):
                        continue
                    number = _safe_float(value)
                    if number is not None:
                        numbers.append(number)
                if len(numbers) >= 3:
                    return numbers[:3]
            for attr_name in ("principalMomentsOfInertia", "momentsOfInertia"):
                try:
                    value = getattr(props, attr_name)
                except Exception:
                    continue
                if isinstance(value, (list, tuple)):
                    numbers = [_safe_float(item) for item in value]
                    if len(numbers) >= 3 and all(
                        item is not None for item in numbers[:3]
                    ):
                        return numbers[:3]
            return None

        def _merge_bbox(acc, bbox):
            if bbox is None:
                return acc
            if acc is None:
                return {"min": list(bbox["min"]), "max": list(bbox["max"])}
            for axis in range(3):
                acc["min"][axis] = min(acc["min"][axis], bbox["min"][axis])
                acc["max"][axis] = max(acc["max"][axis], bbox["max"][axis])
            return acc

        def _finish_bbox(bbox):
            if bbox is None:
                return None
            return {
                "min": list(bbox["min"]),
                "max": list(bbox["max"]),
                "center": [
                    (bbox["min"][axis] + bbox["max"][axis]) * 0.5 for axis in range(3)
                ],
                "size": [
                    abs(bbox["max"][axis] - bbox["min"][axis]) for axis in range(3)
                ],
            }

        parts = {}
        try:
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError(f"items[{index}] must be an object.")
                part_id = str(
                    item.get("part") or item.get("part_id") or f"part_{index}"
                )
                step_file = item.get("step_file") or item.get("path")
                if not step_file:
                    parts[part_id] = {"status": "error", "error": "missing step_file"}
                    continue
                try:
                    document, design = self.import_step_to_new_document(step_file)
                    opened_documents.append(document)
                    bodies = self._collect_imported_bodies_for_iou(design.rootComponent)
                    total_volume = 0.0
                    weighted_centroid = [0.0, 0.0, 0.0]
                    total_mass = 0.0
                    total_inertia = [0.0, 0.0, 0.0]
                    inertia_count = 0
                    merged_bbox = None
                    body_records = []
                    for body_index, body in enumerate(bodies):
                        signature = self._body_signature(body)
                        volume = _safe_float(signature.get("volume")) or 0.0
                        centroid = signature.get("centroid")
                        props = None
                        try:
                            props = body.physicalProperties
                        except Exception:
                            props = None
                        mass = _safe_props_float(props, "mass")
                        inertia_diag = _safe_inertia_diag(props)
                        if (
                            volume > 0.0
                            and isinstance(centroid, list)
                            and len(centroid) == 3
                        ):
                            for axis in range(3):
                                weighted_centroid[axis] += (
                                    float(centroid[axis]) * volume
                                )
                            total_volume += volume
                        if mass is not None and mass > 0.0:
                            total_mass += mass
                        if inertia_diag is not None:
                            for axis in range(3):
                                total_inertia[axis] += float(inertia_diag[axis])
                            inertia_count += 1
                        merged_bbox = _merge_bbox(merged_bbox, signature.get("bbox"))
                        body_records.append(
                            {
                                "index": body_index,
                                "volume_cm3": volume,
                                "center_of_mass_cm": centroid,
                                "bbox_cm": _finish_bbox(signature.get("bbox")),
                                "area_cm2": signature.get("area"),
                                "mass_kg": mass,
                                "inertia_diag_kg_cm2": inertia_diag,
                            }
                        )
                    center = None
                    if total_volume > 0.0:
                        center = [value / total_volume for value in weighted_centroid]
                    parts[part_id] = {
                        "status": "ok",
                        "step_file": str(step_file),
                        "length_units": "cm",
                        "volume_units": "cm^3",
                        "area_units": "cm^2",
                        "volume_cm3": total_volume,
                        "center_of_mass_cm": center,
                        "mass_kg": total_mass if total_mass > 0.0 else None,
                        "inertia_diag_kg_cm2": total_inertia
                        if inertia_count == len(bodies) and bodies
                        else None,
                        "inertia_units": "kg*cm^2",
                        "bbox_cm": _finish_bbox(merged_bbox),
                        "body_count": len(bodies),
                        "bodies": body_records,
                    }
                except Exception as exc:
                    parts[part_id] = {
                        "status": "error",
                        "step_file": str(step_file),
                        "error": str(exc),
                    }
            return {
                "method": "fusion360_step_physical_properties",
                "length_units": "cm",
                "volume_units": "cm^3",
                "parts": parts,
            }
        finally:
            _close_opened_documents()

    def _iter_iou_solid_bodies(self, body_collection):
        if body_collection is None:
            return
        body_count = getattr(body_collection, "count", 0)
        for body_index in range(body_count):
            body = body_collection.item(body_index)
            if body is None:
                continue
            try:
                if not body.isSolid:
                    continue
            except Exception:
                pass
            yield body

    def _iou_body_dedupe_key(self, body):
        if body is None:
            return None

        native = getattr(body, "nativeObject", None)
        if native is not None:
            body = native

        token = getattr(body, "entityToken", None)
        if token:
            return ("token", str(token))

        temp_id = getattr(body, "tempId", None)
        if temp_id is not None:
            return ("temp_id", str(temp_id))

        return ("object", id(body))

    def _collect_imported_bodies_for_iou(self, root_comp):
        bodies = []
        seen_keys = set()

        def _append_unique(body_collection):
            for body in self._iter_iou_solid_bodies(body_collection):
                dedupe_key = self._iou_body_dedupe_key(body)
                if dedupe_key is not None and dedupe_key in seen_keys:
                    continue
                if dedupe_key is not None:
                    seen_keys.add(dedupe_key)
                bodies.append(body)

        _append_unique(getattr(root_comp, "bRepBodies", None))

        all_occurrences = getattr(root_comp, "allOccurrences", None)
        if all_occurrences is not None:
            for occ_index in range(all_occurrences.count):
                occ = all_occurrences.item(occ_index)
                _append_unique(getattr(occ, "bRepBodies", None))

        if not bodies:
            raise RuntimeError("No solid body found after STEP import.")
        return bodies

    def _frame_to_matrix(self, frame):
        frame = frame or {}
        origin = frame.get("origin") or frame.get("origin_mm") or [0.0, 0.0, 0.0]
        x_axis = frame.get("x_axis") or [1.0, 0.0, 0.0]
        y_axis = frame.get("y_axis") or [0.0, 1.0, 0.0]
        z_axis = frame.get("z_axis") or [0.0, 0.0, 1.0]
        matrix = adsk.core.Matrix3D.create()
        matrix.setWithCoordinateSystem(
            self._to_point3d(origin),
            self._to_vector3d(x_axis, normalize=True),
            self._to_vector3d(y_axis, normalize=True),
            self._to_vector3d(z_axis, normalize=True),
        )
        return matrix

    def _transform_temp_body_by_frame(self, temp_body, frame, temp_brep):
        if temp_body is None:
            return temp_body
        matrix = self._frame_to_matrix(frame)
        try:
            transformed = temp_brep.transform(temp_body, matrix)
            if transformed is not False:
                return temp_body
        except Exception:
            pass
        try:
            temp_body.transform(matrix)
            return temp_body
        except Exception:
            pass
        raise RuntimeError(
            "Failed to transform temporary B-Rep body into assembly frame."
        )

    def _move_body_by_frame(self, body, frame):
        if body is None:
            return body
        matrix = self._frame_to_matrix(frame)
        try:
            body.transform(matrix)
            return body
        except Exception:
            pass
        collection = adsk.core.ObjectCollection.create()
        collection.add(body)
        move_input = self.rootComp.features.moveFeatures.createInput2(collection)
        move_input.transform = matrix
        self.rootComp.features.moveFeatures.add(move_input)
        return body

    def _bounding_boxes_overlap(self, body_one, body_two, tolerance=1e-6):
        try:
            bbox_one = body_one.boundingBox
            bbox_two = body_two.boundingBox
        except Exception:
            return True
        return not (
            float(bbox_one.maxPoint.x) < float(bbox_two.minPoint.x) - tolerance
            or float(bbox_two.maxPoint.x) < float(bbox_one.minPoint.x) - tolerance
            or float(bbox_one.maxPoint.y) < float(bbox_two.minPoint.y) - tolerance
            or float(bbox_two.maxPoint.y) < float(bbox_one.minPoint.y) - tolerance
            or float(bbox_one.maxPoint.z) < float(bbox_two.minPoint.z) - tolerance
            or float(bbox_two.maxPoint.z) < float(bbox_one.minPoint.z) - tolerance
        )

    def _intersection_volume_for_bodies(
        self, body_one, body_two, temp_brep, tolerance=1e-6
    ):
        if not self._bounding_boxes_overlap(body_one, body_two, tolerance=tolerance):
            return 0.0

        volume_one = self._body_volume(body_one)
        volume_two = self._body_volume(body_two)
        attempt_pairs = ((body_one, body_two), (body_two, body_one))
        for target_source, tool_source in attempt_pairs:
            intersection_body = temp_brep.copy(target_source)
            tool_body = temp_brep.copy(tool_source)
            if intersection_body is None or tool_body is None:
                continue
            try:
                if not temp_brep.booleanOperation(
                    intersection_body,
                    tool_body,
                    adsk.fusion.BooleanTypes.IntersectionBooleanType,
                ):
                    continue
            except Exception:
                continue
            volume_piece = self._body_volume(intersection_body)
            if volume_piece > tolerance:
                return volume_piece

        for target_source, tool_source in attempt_pairs:
            union_body = temp_brep.copy(target_source)
            tool_body = temp_brep.copy(tool_source)
            if union_body is None or tool_body is None:
                continue
            try:
                if not temp_brep.booleanOperation(
                    union_body,
                    tool_body,
                    adsk.fusion.BooleanTypes.UnionBooleanType,
                ):
                    continue
            except Exception:
                continue
            union_volume = self._body_volume(union_body)
            inferred = volume_one + volume_two - union_volume
            if inferred > tolerance:
                return max(0.0, min(min(volume_one, volume_two), inferred))
        return 0.0

    def _bbox_close(self, left, right, tol=1e-4):
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        left_min = left.get("min")
        left_max = left.get("max")
        right_min = right.get("min")
        right_max = right.get("max")
        if not all(
            isinstance(item, list) and len(item) == 3
            for item in (left_min, left_max, right_min, right_max)
        ):
            return False
        for a, b in zip(left_min + left_max, right_min + right_max):
            if abs(float(a) - float(b)) > tol:
                return False
        return True

    def _vector_close(self, left, right, tol=1e-4):
        if not (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
        ):
            return False
        return all(abs(float(a) - float(b)) <= tol for a, b in zip(left, right))

    def _relative_close(self, left, right, tol=1e-5, abs_tol=1e-8):
        left_value = float(left)
        right_value = float(right)
        scale = max(abs(left_value), abs(right_value), abs_tol)
        return abs(left_value - right_value) <= max(abs_tol, scale * tol)

    def _body_signatures_are_nearly_identical(self, left, right):
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        if not self._relative_close(
            left.get("volume", 0.0),
            right.get("volume", 0.0),
            tol=1e-4,
        ):
            return False
        if not self._bbox_close(left.get("bbox"), right.get("bbox"), tol=2e-4):
            return False

        left_centroid = left.get("centroid")
        right_centroid = right.get("centroid")
        if left_centroid is not None or right_centroid is not None:
            if not self._vector_close(left_centroid, right_centroid, tol=1e-4):
                return False

        left_area = left.get("area")
        right_area = right.get("area")
        if left_area is not None or right_area is not None:
            # Imported STEP faces can be split slightly differently even when
            # bbox/volume/centroid already agree, so area needs a looser guard.
            if not self._relative_close(left_area or 0.0, right_area or 0.0, tol=2e-3):
                return False

        return True

    def _body_signature_sort_key(self, signature):
        if not isinstance(signature, dict):
            return (float("inf"), float("inf"), float("inf"), float("inf"))

        bbox = signature.get("bbox") or {}
        mins = bbox.get("min") if isinstance(bbox, dict) else None
        maxs = bbox.get("max") if isinstance(bbox, dict) else None

        def _vec(values):
            if isinstance(values, list) and len(values) == 3:
                return tuple(float(v) for v in values)
            return (float("inf"), float("inf"), float("inf"))

        return (
            float(signature.get("volume", 0.0)),
            *_vec(mins),
            *_vec(maxs),
            float(signature.get("area", 0.0)),
        )

    def _body_signature_lists_are_nearly_identical(self, left_list, right_list):
        if not isinstance(left_list, list) or not isinstance(right_list, list):
            return False
        if not left_list or len(left_list) != len(right_list):
            return False

        left_sorted = sorted(left_list, key=self._body_signature_sort_key)
        right_sorted = sorted(right_list, key=self._body_signature_sort_key)
        return all(
            self._body_signatures_are_nearly_identical(left, right)
            for left, right in zip(left_sorted, right_sorted)
        )

    def _attitude_normal_sign_in_sketch_space(self, sketch, sketch_index=None):
        if sketch_index is None:
            find_sketch_index = getattr(self, "_find_sketch_index", None)
            if callable(find_sketch_index):
                sketch_index = find_sketch_index(sketch)
        if sketch_index is None:
            return 1.0

        get_sketch_attitude = getattr(self, "_get_sketch_attitude", None)
        if not callable(get_sketch_attitude):
            return 1.0
        attitude = get_sketch_attitude(sketch_index)
        if not attitude:
            return 1.0

        origin, u_dir, v_dir = attitude
        normal = self._to_vector3d(
            [
                float(u_dir.y) * float(v_dir.z) - float(u_dir.z) * float(v_dir.y),
                float(u_dir.z) * float(v_dir.x) - float(u_dir.x) * float(v_dir.z),
                float(u_dir.x) * float(v_dir.y) - float(u_dir.y) * float(v_dir.x),
            ],
            normalize=True,
        )
        probe_distance = 1e-3
        probe_world = adsk.core.Point3D.create(
            float(origin.x) + float(normal.x) * probe_distance,
            float(origin.y) + float(normal.y) * probe_distance,
            float(origin.z) + float(normal.z) * probe_distance,
        )
        try:
            probe_sketch = sketch.modelToSketchSpace(probe_world)
        except Exception:
            return 1.0
        if probe_sketch is None:
            return 1.0

        z_value = float(getattr(probe_sketch, "z", 0.0))
        if z_value < -1e-9:
            return -1.0
        return 1.0

    def _align_extrude_distance_with_sketch_attitude(
        self, sketch, distance, sketch_index=None
    ):
        sign = self._attitude_normal_sign_in_sketch_space(
            sketch, sketch_index=sketch_index
        )
        return float(distance) * sign

    def _normalize_operation(self, operation):
        if operation is None:
            return "NewBody"
        op_key = str(operation).strip().lower()
        mapping = {
            "new": "NewBody",
            "newbody": "NewBody",
            "join": "Join",
            "cut": "Cut",
            "intersect": "Intersect",
        }
        if op_key not in mapping:
            raise ValueError(f"Unsupported operation: {operation}")
        return mapping[op_key]

    def _feature_operation_enum(self, canonical_operation):
        if canonical_operation == "NewBody":
            return adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        if canonical_operation == "Join":
            return adsk.fusion.FeatureOperations.JoinFeatureOperation
        if canonical_operation == "Cut":
            return adsk.fusion.FeatureOperations.CutFeatureOperation
        if canonical_operation == "Intersect":
            return adsk.fusion.FeatureOperations.IntersectFeatureOperation
        raise ValueError(f"Unsupported operation enum mapping: {canonical_operation}")

    def _combine_operation_enum(self, canonical_operation):
        if canonical_operation == "Join":
            return adsk.fusion.FeatureOperations.JoinFeatureOperation
        if canonical_operation == "Cut":
            return adsk.fusion.FeatureOperations.CutFeatureOperation
        if canonical_operation == "Intersect":
            return adsk.fusion.FeatureOperations.IntersectFeatureOperation
        raise ValueError(f"Unsupported combine operation: {canonical_operation}")

    def _to_point3d(self, point):
        if point is None:
            raise ValueError("point cannot be None")
        if len(point) == 2:
            return adsk.core.Point3D.create(float(point[0]), float(point[1]), 0.0)
        if len(point) == 3:
            return adsk.core.Point3D.create(
                float(point[0]), float(point[1]), float(point[2])
            )
        raise ValueError(f"Point must have length 2 or 3, got: {point}")

    def _to_vector3d(self, vector, normalize=False):
        if vector is None or len(vector) != 3:
            raise ValueError(f"vector must have length 3, got: {vector}")
        vx = float(vector[0])
        vy = float(vector[1])
        vz = float(vector[2])
        length = math.sqrt(vx * vx + vy * vy + vz * vz)
        if length <= 1e-12:
            raise ValueError("vector length must be > 0")
        if normalize:
            vx /= length
            vy /= length
            vz /= length
        return adsk.core.Vector3D.create(vx, vy, vz)

    def _select_profile(self, sketch, profile_point=None):
        profiles = sketch.profiles
        profile_count = profiles.count
        if profile_count == 0:
            raise RuntimeError("No profiles in sketch.")

        if profile_point is not None:
            return self._select_profile_by_point(sketch, profile_point)

        if profile_count == 1:
            return profiles.item(0)

        candidates = [profiles.item(i) for i in range(profile_count)]
        grouped_profiles = self._select_primary_profiles_by_component(candidates)
        if len(grouped_profiles) == 1:
            return grouped_profiles[0]

        all_profiles = adsk.core.ObjectCollection.create()
        for profile in grouped_profiles:
            all_profiles.add(profile)
        return all_profiles

    def _select_primary_profiles_by_component(self, profiles):
        groups = self._group_profiles_by_component(profiles)

        selected = []
        for group_profiles in groups:
            selected.append(self._primary_profile_in_group(group_profiles))
        return selected

    def _group_profiles_by_component(self, profiles):
        profiles = list(profiles)
        if len(profiles) <= 1:
            return [profiles] if profiles else []

        bboxes = [self._safe_bounding_box(profile) for profile in profiles]
        parents = list(range(len(profiles)))

        def find(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left, right):
            root_left = find(left)
            root_right = find(right)
            if root_left != root_right:
                parents[root_right] = root_left

        # Group only nested/containing profiles together so disjoint profiles with
        # overlapping AABBs (for example diagonally offset holes) are not collapsed.
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                if self._profile_bboxes_in_same_component(bboxes[i], bboxes[j]):
                    union(i, j)

        groups = {}
        for index, profile in enumerate(profiles):
            groups.setdefault(find(index), []).append((index, profile))

        return [
            [
                profile
                for _index, profile in sorted(group_profiles, key=lambda item: item[0])
            ]
            for group_profiles in groups.values()
        ]

    def _count_containing_profile_bboxes(self, profile, group_profiles, tol=1e-6):
        profile_bbox = self._safe_bounding_box(profile)
        if profile_bbox is None:
            return 0

        count = 0
        for other in group_profiles:
            if other is profile:
                continue
            other_bbox = self._safe_bounding_box(other)
            if self._bbox_contains_bbox(other_bbox, profile_bbox, tol=tol):
                count += 1
        return count

    def _profile_group_containment_depths(self, group_profiles):
        return {
            id(profile): self._count_containing_profile_bboxes(profile, group_profiles)
            for profile in group_profiles
        }

    def _profile_group_containment_depth(self, depths, profile):
        return int(depths.get(id(profile), 0))

    def _profile_group_is_strict_containment_chain(self, group_profiles):
        if len(group_profiles) <= 1:
            return True
        depths = self._profile_group_containment_depths(group_profiles)
        unique_depths = sorted(set(int(depth) for depth in depths.values()))
        if unique_depths != list(range(len(group_profiles))):
            return False
        depth_counts = {}
        for depth in depths.values():
            depth_counts[int(depth)] = depth_counts.get(int(depth), 0) + 1
        return all(count == 1 for count in depth_counts.values())

    def _sorted_profiles_by_group_depth(self, group_profiles):
        depths = self._profile_group_containment_depths(group_profiles)
        return sorted(
            group_profiles,
            key=lambda profile: (
                self._profile_group_containment_depth(depths, profile),
                -self._profile_planar_size(profile),
            ),
        )

    def _representative_profile_for_depth_bucket(self, depth_profiles):
        if not depth_profiles:
            return None
        return max(
            depth_profiles,
            key=lambda profile: (
                1 if self._profile_has_inner_loops(profile) else 0,
                self._profile_planar_size(profile),
            ),
        )

    def _select_alternating_profiles_from_depth_buckets(self, group_profiles):
        if len(group_profiles) <= 1:
            return list(group_profiles)

        depths = self._profile_group_containment_depths(group_profiles)
        depth_buckets = {}
        for profile in group_profiles:
            depth = self._profile_group_containment_depth(depths, profile)
            depth_buckets.setdefault(depth, []).append(profile)

        ordered_depths = sorted(depth_buckets)
        if not ordered_depths or ordered_depths[0] != 0:
            return None
        if ordered_depths != list(range(ordered_depths[-1] + 1)):
            return None

        representative_chain = []
        for depth in ordered_depths:
            representative = self._representative_profile_for_depth_bucket(
                depth_buckets[depth]
            )
            if representative is None:
                return None
            representative_chain.append(representative)

        ring_count = sum(
            1
            for profile in representative_chain
            if self._profile_has_inner_loops(profile)
        )
        if ring_count < 2:
            return None

        for outer_profile, inner_profile in zip(
            representative_chain, representative_chain[1:]
        ):
            if not self._bbox_contains_bbox(
                self._safe_bounding_box(outer_profile),
                self._safe_bounding_box(inner_profile),
                tol=1e-6,
            ):
                return None

        return [
            profile
            for depth_index, profile in enumerate(representative_chain)
            if depth_index % 2 == 0
        ]

    def _select_outer_prefix_excluding_innermost_from_depth_buckets(
        self, group_profiles
    ):
        if len(group_profiles) <= 1:
            return list(group_profiles)

        depths = self._profile_group_containment_depths(group_profiles)
        depth_buckets = {}
        for profile in group_profiles:
            depth = self._profile_group_containment_depth(depths, profile)
            depth_buckets.setdefault(depth, []).append(profile)

        ordered_depths = sorted(depth_buckets)
        if not ordered_depths or ordered_depths[0] != 0:
            return None
        if ordered_depths != list(range(ordered_depths[-1] + 1)):
            return None

        representative_chain = []
        for depth in ordered_depths:
            representative = self._representative_profile_for_depth_bucket(
                depth_buckets[depth]
            )
            if representative is None:
                return None
            representative_chain.append(representative)

        if len(representative_chain) < 2:
            return None
        if not all(
            self._profile_has_inner_loops(profile)
            for profile in representative_chain[:-1]
        ):
            return None
        if self._profile_has_inner_loops(representative_chain[-1]):
            return None

        for outer_profile, inner_profile in zip(
            representative_chain, representative_chain[1:]
        ):
            if not self._bbox_contains_bbox(
                self._safe_bounding_box(outer_profile),
                self._safe_bounding_box(inner_profile),
                tol=1e-6,
            ):
                return None

        return list(representative_chain[:-1])

    def _select_extrude_profiles(self, profiles, profile_mode=None):
        profiles = list(profiles)
        if profile_mode == "outer_prefix_excluding_innermost":
            explicit_profiles = (
                self._select_outer_prefix_excluding_innermost_profiles_by_component(
                    profiles
                )
            )
            if explicit_profiles:
                return explicit_profiles
        return self._select_extrude_profiles_by_component(profiles)

    def _select_outer_prefix_excluding_innermost_profiles_by_component(self, profiles):
        profiles = list(profiles)
        if len(profiles) <= 1:
            return profiles

        grouped_profiles = self._group_profiles_by_component(profiles)
        selected = []
        for group_profiles in grouped_profiles:
            group_selection = (
                self._select_outer_prefix_excluding_innermost_from_depth_buckets(
                    group_profiles
                )
            )
            if group_selection is None:
                return None
            selected.extend(group_selection)

        filtered = self._postprocess_selected_extrude_profiles(selected)
        extrudable = [
            profile
            for profile in filtered
            if self._profile_is_extrudable_region(profile)
        ]
        return extrudable if extrudable else filtered

    def _select_extrude_profiles_by_component(self, profiles):
        profiles = list(profiles)
        if len(profiles) <= 1:
            return profiles

        grouped_profiles = self._group_profiles_by_component(profiles)
        selected = []
        for group_profiles in grouped_profiles:
            group_ring_profiles = [
                profile
                for profile in group_profiles
                if self._profile_has_inner_loops(profile)
            ]
            alternating_profiles = self._select_alternating_profiles_from_depth_buckets(
                group_profiles
            )
            if alternating_profiles is not None:
                selected.extend(alternating_profiles)
            elif len(group_ring_profiles) == 0:
                # Without any band/annulus profile, each simple region is material that
                # should be kept rather than collapsing back to a single outer profile.
                selected.extend(self._sorted_profiles_by_group_depth(group_profiles))
            else:
                top_level_ring_profiles = (
                    self._select_top_level_non_chain_ring_profiles(group_profiles)
                )
                if top_level_ring_profiles is not None:
                    selected.extend(top_level_ring_profiles)
                else:
                    selected.append(self._primary_profile_in_group(group_profiles))

        filtered = self._postprocess_selected_extrude_profiles(selected)
        extrudable = [
            profile
            for profile in filtered
            if self._profile_is_extrudable_region(profile)
        ]
        return extrudable if extrudable else filtered

    def _safe_bounding_box(self, profile):
        try:
            return profile.boundingBox
        except Exception:
            return None

    def _profile_bboxes_overlap(self, left_bbox, right_bbox, tol=1e-6):
        if left_bbox is None or right_bbox is None:
            return False
        return (
            self._intervals_overlap(
                left_bbox.minPoint.x,
                left_bbox.maxPoint.x,
                right_bbox.minPoint.x,
                right_bbox.maxPoint.x,
                tol,
            )
            and self._intervals_overlap(
                left_bbox.minPoint.y,
                left_bbox.maxPoint.y,
                right_bbox.minPoint.y,
                right_bbox.maxPoint.y,
                tol,
            )
            and self._intervals_overlap(
                left_bbox.minPoint.z,
                left_bbox.maxPoint.z,
                right_bbox.minPoint.z,
                right_bbox.maxPoint.z,
                tol,
            )
        )

    def _profile_bboxes_in_same_component(self, left_bbox, right_bbox, tol=1e-6):
        if left_bbox is None or right_bbox is None:
            return False
        return self._bbox_contains_bbox(
            left_bbox, right_bbox, tol=tol
        ) or self._bbox_contains_bbox(right_bbox, left_bbox, tol=tol)

    def _bbox_contains_bbox(self, outer_bbox, inner_bbox, tol=1e-6):
        if outer_bbox is None or inner_bbox is None:
            return False
        return (
            float(outer_bbox.minPoint.x) <= float(inner_bbox.minPoint.x) + tol
            and float(outer_bbox.maxPoint.x) >= float(inner_bbox.maxPoint.x) - tol
            and float(outer_bbox.minPoint.y) <= float(inner_bbox.minPoint.y) + tol
            and float(outer_bbox.maxPoint.y) >= float(inner_bbox.maxPoint.y) - tol
            and float(outer_bbox.minPoint.z) <= float(inner_bbox.minPoint.z) + tol
            and float(outer_bbox.maxPoint.z) >= float(inner_bbox.maxPoint.z) - tol
        )

    def _bbox_contains_point(self, bbox, point, tol=1e-6):
        if bbox is None or point is None:
            return False
        return (
            float(bbox.minPoint.x) - tol
            <= float(point.x)
            <= float(bbox.maxPoint.x) + tol
            and float(bbox.minPoint.y) - tol
            <= float(point.y)
            <= float(bbox.maxPoint.y) + tol
            and float(bbox.minPoint.z) - tol
            <= float(point.z)
            <= float(bbox.maxPoint.z) + tol
        )

    def _intervals_overlap(self, left_min, left_max, right_min, right_max, tol):
        return not (
            float(left_max) < float(right_min) - tol
            or float(right_max) < float(left_min) - tol
        )

    def _primary_profile_in_group(self, group_profiles):
        if len(group_profiles) == 1:
            return group_profiles[0]
        return max(
            group_profiles,
            key=lambda profile: (
                1 if self._profile_has_inner_loops(profile) else 0,
                self._count_nested_profile_bboxes(profile, group_profiles),
                self._profile_planar_size(profile),
            ),
        )

    def _count_nested_profile_bboxes(self, profile, group_profiles, tol=1e-6):
        profile_bbox = self._safe_bounding_box(profile)
        if profile_bbox is None:
            return 0

        count = 0
        for other in group_profiles:
            if other is profile:
                continue
            other_bbox = self._safe_bounding_box(other)
            if self._bbox_contains_bbox(profile_bbox, other_bbox, tol=tol):
                count += 1
        return count

    def _select_profile_by_point(self, sketch, profile_point):
        sketch_index = None
        find_sketch_index = getattr(self, "_find_sketch_index", None)
        map_model_point = getattr(self, "_get_mapped_model_point", None)
        if callable(find_sketch_index):
            sketch_index = find_sketch_index(sketch)
        if (
            sketch_index is not None
            and callable(map_model_point)
            and isinstance(profile_point, (list, tuple))
            and len(profile_point) == 2
        ):
            target_point = map_model_point(sketch_index, profile_point)
        else:
            target_point = self._to_point3d(profile_point)
        matches = []
        for index in range(int(sketch.profiles.count)):
            profile = sketch.profiles.item(index)
            if self._bbox_contains_point(
                self._safe_bounding_box(profile), target_point
            ):
                matches.append(profile)

        if not matches:
            raise RuntimeError(f"No sketch profile found near point {profile_point}.")

        return min(
            matches,
            key=lambda profile: (
                self._profile_bbox_planar_size(profile),
                self._profile_planar_size(profile),
            ),
        )

    def _profile_has_inner_loops(self, profile):
        try:
            loops = profile.profileLoops
            has_outer = False
            has_inner = False
            for index in range(int(loops.count)):
                loop = loops.item(index)
                if loop.isOuter:
                    has_outer = True
                else:
                    has_inner = True
            return has_outer and has_inner
        except Exception:
            return False

    def _profile_inner_loop_count(self, profile):
        try:
            loops = profile.profileLoops
            count = 0
            for index in range(int(loops.count)):
                loop = loops.item(index)
                if not bool(getattr(loop, "isOuter", False)):
                    count += 1
            return int(count)
        except Exception:
            return 0

    def _profile_outer_loop_count(self, profile):
        try:
            loops = profile.profileLoops
            count = 0
            for index in range(int(loops.count)):
                loop = loops.item(index)
                if bool(getattr(loop, "isOuter", False)):
                    count += 1
            return int(count)
        except Exception:
            return 0

    def _profile_is_extrudable_region(self, profile, area_tol=1e-8):
        if self._profile_outer_loop_count(profile) <= 0:
            return False
        return self._profile_planar_size(profile) > float(area_tol)

    def _profile_planar_size(self, profile):
        try:
            props = profile.areaProperties()
            area = float(getattr(props, "area", 0.0))
            if area > 0.0:
                return area
        except Exception:
            pass

        bbox = self._safe_bounding_box(profile)
        if bbox is None:
            return 0.0
        spans = sorted(
            [
                abs(float(bbox.maxPoint.x - bbox.minPoint.x)),
                abs(float(bbox.maxPoint.y - bbox.minPoint.y)),
                abs(float(bbox.maxPoint.z - bbox.minPoint.z)),
            ],
            reverse=True,
        )
        if len(spans) < 2:
            return 0.0
        return spans[0] * spans[1]

    def _profile_bbox_planar_size(self, profile):
        bbox = self._safe_bounding_box(profile)
        if bbox is None:
            return 0.0
        spans = sorted(
            [
                abs(float(bbox.maxPoint.x - bbox.minPoint.x)),
                abs(float(bbox.maxPoint.y - bbox.minPoint.y)),
                abs(float(bbox.maxPoint.z - bbox.minPoint.z)),
            ],
            reverse=True,
        )
        if len(spans) < 2:
            return 0.0
        return spans[0] * spans[1]

    def _all_sketch_profiles(self, sketch):
        return [sketch.profiles.item(i) for i in range(int(sketch.profiles.count))]

    def _profile_list_from_selection(self, selection):
        if selection is None:
            return []
        if hasattr(selection, "profileLoops"):
            return [selection]
        if hasattr(selection, "count") and hasattr(selection, "item"):
            return [selection.item(i) for i in range(int(selection.count))]
        return [selection]

    def _profile_signature(self, profiles):
        return tuple(id(profile) for profile in profiles)

    def _profile_selection_from_list(self, profiles, prefer_collection=False):
        if not profiles:
            raise RuntimeError("No profiles available for extrude.")
        if len(profiles) == 1 and not prefer_collection:
            return profiles[0]
        collection = adsk.core.ObjectCollection.create()
        for profile in profiles:
            collection.add(profile)
        return collection

    def _select_top_level_non_chain_ring_profiles(self, group_profiles):
        ring_profiles = [
            profile
            for profile in group_profiles
            if self._profile_has_inner_loops(profile)
        ]
        if len(ring_profiles) <= 1:
            return None

        top_level = []
        for profile in ring_profiles:
            profile_bbox = self._safe_bounding_box(profile)
            contained_by_other_ring = False
            for other in ring_profiles:
                if other is profile:
                    continue
                if self._bbox_contains_bbox(
                    self._safe_bounding_box(other),
                    profile_bbox,
                    tol=1e-6,
                ):
                    contained_by_other_ring = True
                    break
            if not contained_by_other_ring:
                top_level.append(profile)

        if len(top_level) <= 1:
            return None

        return sorted(
            top_level, key=lambda profile: -self._profile_planar_size(profile)
        )

    def _postprocess_selected_extrude_profiles(self, profiles):
        profiles = list(profiles)
        if len(profiles) <= 1:
            return profiles

        ring_profiles = [
            profile for profile in profiles if self._profile_has_inner_loops(profile)
        ]
        if len(ring_profiles) != 1:
            return profiles

        dominant_ring = ring_profiles[0]
        if self._profile_inner_loop_count(dominant_ring) < 2:
            return profiles

        dominant_bbox = self._safe_bounding_box(dominant_ring)
        if dominant_bbox is None:
            return profiles

        filtered = [dominant_ring]
        removed_any = False
        for profile in profiles:
            if profile is dominant_ring:
                continue
            if self._profile_has_inner_loops(profile):
                filtered.append(profile)
                continue
            if self._profile_bboxes_overlap(
                dominant_bbox,
                self._safe_bounding_box(profile),
                tol=1e-6,
            ):
                removed_any = True
                continue
            filtered.append(profile)

        return filtered if removed_any else profiles

    def _profiles_need_sequential_new_body_union(self, profiles):
        profiles = list(profiles)
        if len(profiles) <= 1:
            return False
        if any(self._profile_has_inner_loops(profile) for profile in profiles):
            return False

        bboxes = [self._safe_bounding_box(profile) for profile in profiles]
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                if self._bbox_contains_bbox(
                    bboxes[i], bboxes[j], tol=1e-6
                ) or self._bbox_contains_bbox(
                    bboxes[j],
                    bboxes[i],
                    tol=1e-6,
                ):
                    return True
        return False

    def _extrude_new_body_per_profile_union(self, profiles, distance):
        profiles = list(profiles)
        if not profiles:
            raise RuntimeError("No profiles available for sequential NewBody extrude.")

        feature = None
        target_body = None
        for profile_index, profile in enumerate(profiles):
            pre_count = self.rootComp.bRepBodies.count
            feature = self._add_extrude_feature(
                profile,
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
                distance,
            )
            new_body = self._extract_new_body(pre_count, "extrude")
            if profile_index == 0:
                target_body = new_body
                continue
            try:
                self._combine_body(target_body, new_body, "Join")
            except Exception:
                self._delete_body_if_possible(new_body)
                raise
        return feature

    def _extrude_add_failed_due_to_missing_target(self, error):
        message = str(error)
        markers = (
            "未找到要剪切或相交的目标实体",
            "\\u672a\\u627e\\u5230\\u8981\\u526a\\u5207\\u6216\\u76f8\\u4ea4\\u7684\\u76ee\\u6807\\u5b9e\\u4f53",
            "no target body",
            "target body to cut or intersect",
        )
        candidates = [message.lower()]
        try:
            candidates.append(message.encode("utf-8").decode("unicode_escape").lower())
        except Exception:
            pass
        return any(
            marker.lower() in candidate
            for candidate in candidates
            for marker in markers
        )

    def _extrude_add_failed_due_to_boolean_geometry(self, error):
        message = str(error)
        markers = (
            "ASM_",
            "compute failed",
            "计算失败",
            "无法执行布尔运算",
            "合并在一起时发生问题",
            "combine",
            "feature_failed_to_create",
            "无法创建特征",
            "boolean operation",
        )
        candidates = [message.lower()]
        try:
            candidates.append(message.encode("utf-8").decode("unicode_escape").lower())
        except Exception:
            pass
        return any(
            marker.lower() in candidate
            for candidate in candidates
            for marker in markers
        )

    def _add_extrude_feature(self, profile_selection, feature_operation, distance):
        extrude_input = self.extrudes_class.createInput(
            profile_selection, feature_operation
        )
        extrude_input.setDistanceExtent(
            False, adsk.core.ValueInput.createByReal(float(distance))
        )
        return self.extrudes_class.add(extrude_input)

    def _resolve_extrude_profile_inputs(
        self, sketch, profile_point=None, profile_mode=None
    ):
        sketch_profiles = None
        if profile_point is None:
            sketch_profiles = self._all_sketch_profiles(sketch)
            selected_profiles = self._select_extrude_profiles(
                sketch_profiles,
                profile_mode=profile_mode,
            )
        else:
            selected_profiles = self._profile_list_from_selection(
                self._select_profile_by_point(sketch, profile_point)
            )

        prefer_collection = (
            profile_point is None
            and len(selected_profiles) == 1
            and sketch_profiles is not None
            and len(sketch_profiles) > 1
            and self._profile_inner_loop_count(selected_profiles[0]) >= 2
        )
        return sketch_profiles, selected_profiles, prefer_collection

    def _create_new_body_extrude_tool(
        self,
        sketch,
        distance,
        profile_point=None,
        profile_mode=None,
    ):
        _sketch_profiles, selected_profiles, prefer_collection = (
            self._resolve_extrude_profile_inputs(
                sketch,
                profile_point=profile_point,
                profile_mode=profile_mode,
            )
        )
        pre_count = self.rootComp.bRepBodies.count
        if profile_point is None and self._profiles_need_sequential_new_body_union(
            selected_profiles
        ):
            feature = self._extrude_new_body_per_profile_union(
                selected_profiles,
                distance,
            )
        else:
            profile_selection = self._profile_selection_from_list(
                selected_profiles,
                prefer_collection=prefer_collection,
            )
            feature = self._add_extrude_feature(
                profile_selection,
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
                distance,
            )
        new_body = self._extract_new_body(pre_count, "extrude")
        return feature, new_body

    def _build_bidirectional_extrude_tool_body(
        self,
        sketch,
        distance,
        opposite_distance,
        profile_point=None,
        profile_mode=None,
    ):
        if abs(distance) <= 1e-9 and abs(opposite_distance) <= 1e-9:
            raise ValueError("Bidirectional extrude requires a non-zero extent.")

        tool_body = None
        last_feature = None
        try:
            for side_distance in (distance, opposite_distance):
                if abs(side_distance) <= 1e-9:
                    continue
                last_feature, side_body = self._create_new_body_extrude_tool(
                    sketch,
                    side_distance,
                    profile_point=profile_point,
                    profile_mode=profile_mode,
                )
                if tool_body is None:
                    tool_body = side_body
                    continue
                try:
                    self._combine_body(tool_body, side_body, "Join")
                except Exception:
                    self._delete_body_if_possible(side_body)
                    raise

            if tool_body is None:
                raise RuntimeError("Bidirectional extrude did not create a tool body.")
            return last_feature, tool_body
        except Exception:
            if tool_body is not None:
                self._delete_body_if_possible(tool_body)
            raise

    def _delete_body_if_possible(self, body):
        try:
            body.deleteMe()
        except Exception:
            pass

    def _extrude_via_new_body_combine(
        self, profiles, canonical_operation, distance, target_body_index=None
    ):
        pre_count = self.rootComp.bRepBodies.count
        pre_bodies = [self.rootComp.bRepBodies.item(i) for i in range(pre_count)]
        feature = self._add_extrude_feature(
            self._profile_selection_from_list(profiles),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
            distance,
        )
        new_body = self._extract_new_body(pre_count, "extrude")
        target_body = self._resolve_target_body(pre_bodies, target_body_index)
        try:
            self._combine_body(target_body, new_body, canonical_operation)
        except Exception:
            self._delete_body_if_possible(new_body)
            raise
        return feature

    def _extrude_boolean_per_profile(
        self, profiles, canonical_operation, distance, target_body_index=None
    ):
        if not profiles:
            raise RuntimeError("No profiles available for sequential boolean extrude.")
        pre_bodies = [
            self.rootComp.bRepBodies.item(i)
            for i in range(self.rootComp.bRepBodies.count)
        ]
        target_body = self._resolve_target_body(pre_bodies, target_body_index)
        feature = None
        for profile in profiles:
            pre_count = self.rootComp.bRepBodies.count
            feature = self._add_extrude_feature(
                profile,
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
                distance,
            )
            new_body = self._extract_new_body(pre_count, "extrude")
            try:
                self._combine_body(target_body, new_body, canonical_operation)
            except Exception:
                self._delete_body_if_possible(new_body)
                raise
        return feature

    def _extract_new_body(self, before_count, feature_name):
        after_count = self.rootComp.bRepBodies.count
        if after_count <= before_count:
            raise RuntimeError(f"{feature_name} did not create a new body.")
        return self.rootComp.bRepBodies.item(after_count - 1)

    def _body_is_boolean_compatible(self, body):
        if body is None:
            return False
        try:
            faces = getattr(body, "faces", None)
            if faces is not None and int(faces.count) > 0:
                return True
        except Exception:
            pass
        return False

    def _resolve_target_body(self, pre_bodies, target_body_index):
        if not pre_bodies:
            raise RuntimeError(
                "No target body available for Join/Cut/Intersect operation."
            )

        if target_body_index is None:
            for body in reversed(pre_bodies):
                if self._body_is_boolean_compatible(body):
                    return body
            return pre_bodies[-1]

        index = int(target_body_index)
        if index < 0 or index >= len(pre_bodies):
            raise IndexError(
                f"target_body_index out of range: {index}, valid range: 0..{len(pre_bodies) - 1}"
            )
        return pre_bodies[index]

    def _combine_body(self, target_body, tool_body, canonical_operation):
        tools = adsk.core.ObjectCollection.create()
        tools.add(tool_body)
        combine_input = self.rootComp.features.combineFeatures.createInput(
            target_body, tools
        )
        combine_input.operation = self._combine_operation_enum(canonical_operation)
        combine_input.isKeepToolBodies = False
        return self.rootComp.features.combineFeatures.add(combine_input)

    def _create_axis_line(self, sketch, axis_point_1, axis_point_2):
        axis_lines = sketch.sketchCurves.sketchLines
        axis_line = axis_lines.addByTwoPoints(axis_point_1, axis_point_2)
        axis_line.isConstruction = True
        return axis_line

    def _projected_axis_is_degenerate(
        self, sketch, axis_point_1, axis_point_2, tol=1e-7
    ):
        try:
            p1 = sketch.modelToSketchSpace(axis_point_1)
            p2 = sketch.modelToSketchSpace(axis_point_2)
            dx = p2.x - p1.x
            dy = p2.y - p1.y
            # modelToSketchSpace uses z as distance-to-plane; revolve axis validity depends on in-plane projection.
            return (dx * dx + dy * dy) <= (tol * tol)
        except Exception:
            # If projection introspection is unavailable, do not block sketch-axis fallback.
            return False

    def _try_get_origin_principal_axis(self, axis_point_1, axis_point_2, tol=1e-6):
        dx = float(axis_point_2.x - axis_point_1.x)
        dy = float(axis_point_2.y - axis_point_1.y)
        dz = float(axis_point_2.z - axis_point_1.z)
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length <= tol:
            return None

        ux = dx / length
        uy = dy / length
        uz = dz / length

        # Only reuse global origin axes when the requested line is the same infinite line.
        cx = float(axis_point_1.y) * uz - float(axis_point_1.z) * uy
        cy = float(axis_point_1.z) * ux - float(axis_point_1.x) * uz
        cz = float(axis_point_1.x) * uy - float(axis_point_1.y) * ux
        if math.sqrt(cx * cx + cy * cy + cz * cz) > tol:
            return None

        ax = abs(ux)
        ay = abs(uy)
        az = abs(uz)
        if ax >= ay and ax >= az and ay <= tol and az <= tol:
            return ("origin_x_axis", self.rootComp.xConstructionAxis)
        if ay >= ax and ay >= az and ax <= tol and az <= tol:
            return ("origin_y_axis", self.rootComp.yConstructionAxis)
        if az >= ax and az >= ay and ax <= tol and ay <= tol:
            return ("origin_z_axis", self.rootComp.zConstructionAxis)
        return None

    def _world_point_from_sketch_point(self, sketch, sketch_point):
        if sketch_point is None:
            return None

        world_geometry = getattr(sketch_point, "worldGeometry", None)
        if world_geometry is not None:
            return adsk.core.Point3D.create(
                float(world_geometry.x),
                float(world_geometry.y),
                float(getattr(world_geometry, "z", 0.0)),
            )

        geometry = getattr(sketch_point, "geometry", None)
        if geometry is None:
            return None

        try:
            return sketch.sketchToModelSpace(geometry)
        except Exception:
            return adsk.core.Point3D.create(
                float(geometry.x),
                float(geometry.y),
                float(getattr(geometry, "z", 0.0)),
            )

    def _curve_midpoint_world(self, sketch, sketch_curve):
        mid_sketch_point = getattr(sketch_curve, "midSketchPoint", None)
        mid_world = self._world_point_from_sketch_point(sketch, mid_sketch_point)
        if mid_world is not None:
            return mid_world

        try:
            evaluator = sketch_curve.geometry.evaluator
            success, min_param, max_param = evaluator.getParameterExtents()
            if success:
                success_point, midpoint = evaluator.getPointAtParameter(
                    (min_param + max_param) * 0.5
                )
                if success_point:
                    return adsk.core.Point3D.create(
                        float(midpoint.x),
                        float(midpoint.y),
                        float(getattr(midpoint, "z", 0.0)),
                    )
        except Exception:
            pass
        return None

    def _iter_copyable_sketch_curves(self, sketch):
        curves = getattr(sketch, "sketchCurves", None)
        if curves is None:
            return

        for attr_name in ("sketchLines", "sketchArcs", "sketchCircles"):
            collection = getattr(curves, attr_name, None)
            if collection is None:
                continue
            for index in range(int(collection.count)):
                curve = collection.item(index)
                if bool(getattr(curve, "isConstruction", False)):
                    continue
                yield curve

    def _sketch_non_construction_curves(self, sketch, attr_name):
        curves = getattr(getattr(sketch, "sketchCurves", None), attr_name, None)
        if curves is None:
            return []

        items = []
        for index in range(int(curves.count)):
            curve = curves.item(index)
            if bool(getattr(curve, "isConstruction", False)):
                continue
            items.append(curve)
        return items

    def _copy_curve_to_sketch(self, source_sketch, target_sketch, sketch_curve):
        start_point = getattr(sketch_curve, "startSketchPoint", None)
        end_point = getattr(sketch_curve, "endSketchPoint", None)
        if start_point is not None and end_point is not None:
            start_world = self._world_point_from_sketch_point(
                source_sketch, start_point
            )
            end_world = self._world_point_from_sketch_point(source_sketch, end_point)
            if start_world is None or end_world is None:
                return False

            midpoint_world = self._curve_midpoint_world(source_sketch, sketch_curve)
            if (
                midpoint_world is not None
                and getattr(sketch_curve, "centerSketchPoint", None) is not None
            ):
                target_sketch.sketchCurves.sketchArcs.addByThreePoints(
                    target_sketch.modelToSketchSpace(start_world),
                    target_sketch.modelToSketchSpace(midpoint_world),
                    target_sketch.modelToSketchSpace(end_world),
                )
                return True

            target_sketch.sketchCurves.sketchLines.addByTwoPoints(
                target_sketch.modelToSketchSpace(start_world),
                target_sketch.modelToSketchSpace(end_world),
            )
            return True

        center_point = getattr(sketch_curve, "centerSketchPoint", None)
        radius = getattr(sketch_curve, "radius", None)
        if center_point is not None and radius is not None:
            center_world = self._world_point_from_sketch_point(
                source_sketch, center_point
            )
            if center_world is None:
                return False
            target_sketch.sketchCurves.sketchCircles.addByCenterRadius(
                target_sketch.modelToSketchSpace(center_world),
                abs(float(radius)),
            )
            return True

        return False

    def _build_profile_helper_sketch(self, source_sketch):
        sketch_index = self._find_sketch_index(source_sketch)
        if sketch_index is None:
            return None

        attitude = self._get_sketch_attitude(sketch_index)
        if not attitude or len(attitude) < 3:
            return None

        helper_sketch = self._create_sketch_from_world_basis(
            attitude[0],
            attitude[1],
            attitude[2],
        )

        copied_curve_count = 0
        for sketch_curve in self._iter_copyable_sketch_curves(source_sketch):
            if self._copy_curve_to_sketch(source_sketch, helper_sketch, sketch_curve):
                copied_curve_count += 1

        if copied_curve_count > 0:
            try:
                if int(helper_sketch.profiles.count) > 0:
                    return helper_sketch
            except Exception:
                pass

        return self._try_build_slot_profile_helper_sketch(source_sketch, attitude)

    def _try_build_slot_profile_helper_sketch(self, source_sketch, attitude, tol=1e-6):
        source_lines = self._sketch_non_construction_curves(
            source_sketch, "sketchLines"
        )
        source_arcs = self._sketch_non_construction_curves(source_sketch, "sketchArcs")
        source_circles = self._sketch_non_construction_curves(
            source_sketch, "sketchCircles"
        )
        if len(source_lines) != 2 or len(source_arcs) != 2 or source_circles:
            return None

        radii = [abs(float(getattr(arc, "radius", 0.0))) for arc in source_arcs]
        if min(radii) <= tol or (max(radii) - min(radii)) > max(tol, min(radii) * 1e-4):
            return None

        centers_world = [
            self._world_point_from_sketch_point(
                source_sketch,
                getattr(arc, "centerSketchPoint", None),
            )
            for arc in source_arcs
        ]
        if any(center is None for center in centers_world):
            return None

        helper_sketch = self._create_sketch_from_world_basis(
            attitude[0],
            attitude[1],
            attitude[2],
        )
        center_points = [
            helper_sketch.modelToSketchSpace(center_world)
            for center_world in centers_world
        ]

        dx = float(center_points[1].x) - float(center_points[0].x)
        dy = float(center_points[1].y) - float(center_points[0].y)
        center_distance = math.sqrt(dx * dx + dy * dy)
        if center_distance <= tol:
            return None

        ux = dx / center_distance
        uy = dy / center_distance
        px = -uy
        py = ux
        radius = sum(radii) * 0.5

        def _pt(center_point, ax, ay):
            return adsk.core.Point3D.create(
                float(center_point.x) + ax,
                float(center_point.y) + ay,
                0.0,
            )

        top_start = _pt(center_points[0], px * radius, py * radius)
        top_end = _pt(center_points[1], px * radius, py * radius)
        bottom_end = _pt(center_points[1], -px * radius, -py * radius)
        bottom_start = _pt(center_points[0], -px * radius, -py * radius)
        right_mid = _pt(center_points[1], ux * radius, uy * radius)
        left_mid = _pt(center_points[0], -ux * radius, -uy * radius)

        helper_curves = helper_sketch.sketchCurves
        helper_curves.sketchLines.addByTwoPoints(top_start, top_end)
        helper_curves.sketchArcs.addByThreePoints(top_end, right_mid, bottom_end)
        helper_curves.sketchLines.addByTwoPoints(bottom_end, bottom_start)
        helper_curves.sketchArcs.addByThreePoints(bottom_start, left_mid, top_start)

        try:
            if int(helper_sketch.profiles.count) <= 0:
                return None
        except Exception:
            return None
        return helper_sketch

    def _select_revolve_profile_sketch(self, sketch):
        try:
            return sketch, self._select_profile(sketch)
        except RuntimeError as ex:
            if "No profiles in sketch" not in str(ex):
                raise

        helper_sketch = self._build_profile_helper_sketch(sketch)
        if helper_sketch is None:
            raise RuntimeError("No profiles in sketch.")
        return helper_sketch, self._select_profile(helper_sketch)

    def _point_distance_to_line(self, point, line_origin, line_direction):
        vx = float(point.x) - float(line_origin.x)
        vy = float(point.y) - float(line_origin.y)
        vz = float(point.z) - float(line_origin.z)

        cross_x = vy * float(line_direction.z) - vz * float(line_direction.y)
        cross_y = vz * float(line_direction.x) - vx * float(line_direction.z)
        cross_z = vx * float(line_direction.y) - vy * float(line_direction.x)
        return math.sqrt(cross_x * cross_x + cross_y * cross_y + cross_z * cross_z)

    def _point_distance(self, point_a, point_b):
        dx = float(point_a.x) - float(point_b.x)
        dy = float(point_a.y) - float(point_b.y)
        dz = float(point_a.z) - float(point_b.z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _revolve_axis_line_tolerance(
        self,
        start_world,
        end_world,
        abs_tol=1e-4,
        relative_tol=1e-4,
    ):
        line_length = self._point_distance(start_world, end_world)
        return max(float(abs_tol), float(line_length) * float(relative_tol))

    def _is_invalid_revolve_profile_error(self, error):
        message = str(error).lower()
        if "invalid profile" in message and "revolve" in message:
            return True
        # Some full-sweep revolves that touch the axis fail with Fusion's
        # InternalValidationError rather than the clearer "invalid profile"
        # wording. Treat these the same so axis-relief fallback can run.
        if "internalvalidationerror" in message and "bset" in message:
            return True
        return False

    def _source_line_on_revolve_axis(
        self,
        sketch,
        axis_point_1,
        axis_point_2,
        abs_tol=1e-4,
        relative_tol=1e-4,
    ):
        axis_direction = adsk.core.Vector3D.create(
            float(axis_point_2.x) - float(axis_point_1.x),
            float(axis_point_2.y) - float(axis_point_1.y),
            float(axis_point_2.z) - float(axis_point_1.z),
        )
        axis_length = math.sqrt(
            float(axis_direction.x) * float(axis_direction.x)
            + float(axis_direction.y) * float(axis_direction.y)
            + float(axis_direction.z) * float(axis_direction.z)
        )
        if axis_length <= float(abs_tol):
            return False

        axis_direction = adsk.core.Vector3D.create(
            float(axis_direction.x) / axis_length,
            float(axis_direction.y) / axis_length,
            float(axis_direction.z) / axis_length,
        )

        for line in self._sketch_non_construction_curves(sketch, "sketchLines"):
            start_world = self._world_point_from_sketch_point(
                sketch,
                getattr(line, "startSketchPoint", None),
            )
            end_world = self._world_point_from_sketch_point(
                sketch,
                getattr(line, "endSketchPoint", None),
            )
            if start_world is None or end_world is None:
                continue
            line_tol = self._revolve_axis_line_tolerance(
                start_world,
                end_world,
                abs_tol=abs_tol,
                relative_tol=relative_tol,
            )
            if (
                self._point_distance_to_line(start_world, axis_point_1, axis_direction)
                <= line_tol
                and self._point_distance_to_line(
                    end_world, axis_point_1, axis_direction
                )
                <= line_tol
            ):
                return True
        return False

    def _build_axis_relief_profile_helper_sketch(
        self,
        source_sketch,
        axis_point_1,
        axis_point_2,
        relief=1e-2,
        axis_tol=1e-3,
    ):
        sketch_index = self._find_sketch_index(source_sketch)
        if sketch_index is None:
            return None

        attitude = self._get_sketch_attitude(sketch_index)
        if not attitude or len(attitude) < 3:
            return None

        helper_sketch = self._create_sketch_from_world_basis(
            attitude[0],
            attitude[1],
            attitude[2],
        )

        axis_origin_local = helper_sketch.modelToSketchSpace(axis_point_1)
        axis_end_local = helper_sketch.modelToSketchSpace(axis_point_2)
        axis_dx = float(axis_end_local.x) - float(axis_origin_local.x)
        axis_dy = float(axis_end_local.y) - float(axis_origin_local.y)
        axis_length = math.sqrt(axis_dx * axis_dx + axis_dy * axis_dy)
        if axis_length <= axis_tol:
            return None

        axis_ux = axis_dx / axis_length
        axis_uy = axis_dy / axis_length
        perp_x = -axis_uy
        perp_y = axis_ux

        side_sign = None
        for sketch_curve in self._iter_copyable_sketch_curves(source_sketch):
            for sketch_point in (
                getattr(sketch_curve, "startSketchPoint", None),
                getattr(sketch_curve, "endSketchPoint", None),
                getattr(sketch_curve, "midSketchPoint", None),
                getattr(sketch_curve, "centerSketchPoint", None),
            ):
                world_point = self._world_point_from_sketch_point(
                    source_sketch, sketch_point
                )
                if world_point is None:
                    continue
                local_point = helper_sketch.modelToSketchSpace(world_point)
                rx = float(local_point.x) - float(axis_origin_local.x)
                ry = float(local_point.y) - float(axis_origin_local.y)
                signed_distance = rx * axis_uy - ry * axis_ux
                if abs(signed_distance) > axis_tol:
                    side_sign = 1.0 if signed_distance > 0.0 else -1.0
                    break
            if side_sign is not None:
                break
        if side_sign is None:
            side_sign = 1.0

        def _adjust_local_point(local_point):
            rx = float(local_point.x) - float(axis_origin_local.x)
            ry = float(local_point.y) - float(axis_origin_local.y)
            signed_distance = rx * axis_uy - ry * axis_ux
            if abs(signed_distance) <= axis_tol:
                return adsk.core.Point3D.create(
                    float(local_point.x) + perp_x * relief * side_sign,
                    float(local_point.y) + perp_y * relief * side_sign,
                    0.0,
                )
            return adsk.core.Point3D.create(
                float(local_point.x), float(local_point.y), 0.0
            )

        copied_curve_count = 0
        for sketch_curve in self._iter_copyable_sketch_curves(source_sketch):
            start_point = getattr(sketch_curve, "startSketchPoint", None)
            end_point = getattr(sketch_curve, "endSketchPoint", None)
            if start_point is not None and end_point is not None:
                start_world = self._world_point_from_sketch_point(
                    source_sketch, start_point
                )
                end_world = self._world_point_from_sketch_point(
                    source_sketch, end_point
                )
                if start_world is None or end_world is None:
                    continue
                start_local = _adjust_local_point(
                    helper_sketch.modelToSketchSpace(start_world)
                )
                end_local = _adjust_local_point(
                    helper_sketch.modelToSketchSpace(end_world)
                )
                midpoint_world = self._curve_midpoint_world(source_sketch, sketch_curve)
                if (
                    midpoint_world is not None
                    and getattr(sketch_curve, "centerSketchPoint", None) is not None
                ):
                    midpoint_local = _adjust_local_point(
                        helper_sketch.modelToSketchSpace(midpoint_world)
                    )
                    helper_sketch.sketchCurves.sketchArcs.addByThreePoints(
                        start_local,
                        midpoint_local,
                        end_local,
                    )
                else:
                    helper_sketch.sketchCurves.sketchLines.addByTwoPoints(
                        start_local, end_local
                    )
                copied_curve_count += 1
                continue

            center_point = getattr(sketch_curve, "centerSketchPoint", None)
            radius = getattr(sketch_curve, "radius", None)
            if center_point is None or radius is None:
                continue
            center_world = self._world_point_from_sketch_point(
                source_sketch, center_point
            )
            if center_world is None:
                continue
            center_local = _adjust_local_point(
                helper_sketch.modelToSketchSpace(center_world)
            )
            helper_sketch.sketchCurves.sketchCircles.addByCenterRadius(
                center_local,
                abs(float(radius)),
            )
            copied_curve_count += 1

        if copied_curve_count == 0:
            return None

        try:
            if int(helper_sketch.profiles.count) <= 0:
                return None
        except Exception:
            return None
        return helper_sketch

    def _try_find_matching_sketch_axis_line(
        self, sketch, axis_point_1, axis_point_2, tol=1e-6
    ):
        curves = getattr(sketch, "sketchCurves", None)
        if curves is None:
            return None

        sketch_lines = getattr(curves, "sketchLines", None)
        if sketch_lines is None:
            return None

        axis_direction = adsk.core.Vector3D.create(
            float(axis_point_2.x) - float(axis_point_1.x),
            float(axis_point_2.y) - float(axis_point_1.y),
            float(axis_point_2.z) - float(axis_point_1.z),
        )
        axis_length = math.sqrt(
            float(axis_direction.x) * float(axis_direction.x)
            + float(axis_direction.y) * float(axis_direction.y)
            + float(axis_direction.z) * float(axis_direction.z)
        )
        if axis_length <= tol:
            return None

        axis_direction = adsk.core.Vector3D.create(
            float(axis_direction.x) / axis_length,
            float(axis_direction.y) / axis_length,
            float(axis_direction.z) / axis_length,
        )

        best_match = None
        best_length = -1.0
        for index in range(int(sketch_lines.count)):
            line = sketch_lines.item(index)
            start_world = self._world_point_from_sketch_point(
                sketch,
                getattr(line, "startSketchPoint", None),
            )
            end_world = self._world_point_from_sketch_point(
                sketch,
                getattr(line, "endSketchPoint", None),
            )
            if start_world is None or end_world is None:
                continue

            if (
                self._point_distance_to_line(start_world, axis_point_1, axis_direction)
                > tol
                or self._point_distance_to_line(end_world, axis_point_1, axis_direction)
                > tol
            ):
                continue

            dx = float(end_world.x) - float(start_world.x)
            dy = float(end_world.y) - float(start_world.y)
            dz = float(end_world.z) - float(start_world.z)
            line_length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if line_length <= tol:
                continue

            if line_length > best_length:
                best_match = line
                best_length = line_length

        return best_match

    def _axis_entities_for_revolve(self, sketch, axis_point_1, axis_point_2):
        axis_entities = []

        origin_axis = self._try_get_origin_principal_axis(axis_point_1, axis_point_2)
        if origin_axis is not None:
            axis_entities.append(origin_axis)

        # Keep this fallback for direct modeling mode where setByLine(InfiniteLine3D) can work.
        construction_axis = self._try_create_construction_axis(
            axis_point_1, axis_point_2
        )
        if construction_axis is not None:
            axis_entities.append(("construction_axis", construction_axis))
        else:
            construction_axis = self._try_create_construction_axis_by_two_points(
                sketch,
                axis_point_1,
                axis_point_2,
            )
            if construction_axis is not None:
                axis_entities.append(("construction_axis_by_points", construction_axis))

        existing_sketch_axis = self._try_find_matching_sketch_axis_line(
            sketch,
            axis_point_1,
            axis_point_2,
        )
        if existing_sketch_axis is not None:
            axis_entities.append(("existing_sketch_axis", existing_sketch_axis))
            return axis_entities

        # Revolve in this workflow requires an axis that has a non-zero projection on sketch plane.
        if self._projected_axis_is_degenerate(sketch, axis_point_1, axis_point_2):
            raise ValueError(
                "Invalid revolve axis for this sketch: projected axis collapses to a point. "
                "Use an axis line that lies in the sketch plane."
            )

        try:
            sketch_axis = self._create_axis_line(sketch, axis_point_1, axis_point_2)
            axis_entities.append(("sketch_axis", sketch_axis))
        except Exception:
            pass

        if not axis_entities:
            raise RuntimeError("Failed to create revolve axis entity.")
        return axis_entities

    def _try_create_construction_axis(self, axis_point_1, axis_point_2):
        # ?????? ConstructionAxis?????????????????????
        direction = adsk.core.Vector3D.create(
            axis_point_2.x - axis_point_1.x,
            axis_point_2.y - axis_point_1.y,
            axis_point_2.z - axis_point_1.z,
        )
        if direction.length <= 1e-12:
            return None

        try:
            axis_input = self.rootComp.constructionAxes.createInput()
            inf_line = adsk.core.InfiniteLine3D.create(axis_point_1, direction)
            if not axis_input.setByLine(inf_line):
                return None
            return self.rootComp.constructionAxes.add(axis_input)
        except Exception:
            return None

    def _try_create_construction_axis_by_two_points(
        self, sketch, axis_point_1, axis_point_2
    ):
        sketch_points = getattr(sketch, "sketchPoints", None)
        root_comp = getattr(self, "rootComp", None)
        construction_axes = getattr(root_comp, "constructionAxes", None)
        if sketch_points is None or construction_axes is None:
            return None

        try:
            point_one = sketch_points.add(axis_point_1)
            point_two = sketch_points.add(axis_point_2)
            axis_input = construction_axes.createInput()
            if not axis_input.setByTwoPoints(point_one, point_two):
                return None
            return construction_axes.add(axis_input)
        except Exception:
            return None

    def _resolve_revolve_axis_points(
        self, axis, axisPoint1, axisPoint2, sketch=None, sketch_index=None
    ):
        if axis is not None:
            if not isinstance(axis, list) or len(axis) != 2:
                raise ValueError("axis must be [[x,y,z],[dx,dy,dz]].")
            axis_origin = self._to_point3d(axis[0])
            direction = self._to_vector3d(axis[1], normalize=True)
            axis_end = adsk.core.Point3D.create(
                axis_origin.x + direction.x,
                axis_origin.y + direction.y,
                axis_origin.z + direction.z,
            )
            return axis_origin, axis_end

        if axisPoint1 is None or axisPoint2 is None:
            raise ValueError("Either axis or axisPoint1/axisPoint2 must be provided.")

        map_model_point = getattr(self, "_get_mapped_model_point", None)
        if (
            sketch is not None
            and sketch_index is not None
            and callable(map_model_point)
            and isinstance(axisPoint1, (list, tuple))
            and isinstance(axisPoint2, (list, tuple))
            and len(axisPoint1) == 2
            and len(axisPoint2) == 2
        ):
            return (
                map_model_point(sketch_index, axisPoint1),
                map_model_point(sketch_index, axisPoint2),
            )

        return self._to_point3d(axisPoint1), self._to_point3d(axisPoint2)

    def _rotate_body_around_axis(self, body, axis_entity, angle_rad):
        entities = adsk.core.ObjectCollection.create()
        entities.add(body)
        move_input = self.rootComp.features.moveFeatures.createInput2(entities)
        move_input.defineAsRotate(
            axis_entity, adsk.core.ValueInput.createByReal(angle_rad)
        )
        return self.rootComp.features.moveFeatures.add(move_input)

    def _angle_input_from_radians(self, angle_rad):
        # ????????????? createByReal ????????????
        return adsk.core.ValueInput.createByString(
            f"{math.degrees(float(angle_rad))} deg"
        )

    def _iter_chamfer_angle_inputs(self, angle_value):
        angle_float = float(angle_value)
        candidates: list[tuple[str, adsk.core.ValueInput]] = []
        seen_labels: set[str] = set()

        def _append(label, value_input):
            if label in seen_labels:
                return
            seen_labels.add(label)
            candidates.append((label, value_input))

        # HistCAD chamfer payloads are mixed: some angles are emitted in degrees
        # (for example 45.0), while many others are already in radians
        # (for example 0.7854, 0.0349). Prefer the unit that matches the value
        # range, then fall back to the alternate interpretation if Fusion rejects
        # the first attempt.
        if math.isfinite(angle_float) and abs(angle_float) <= (2.0 * math.pi + 1e-6):
            _append("radian_string", self._angle_input_from_radians(angle_float))
            _append(
                "degree_string",
                adsk.core.ValueInput.createByString(f"{angle_float} deg"),
            )
        else:
            _append(
                "degree_string",
                adsk.core.ValueInput.createByString(f"{angle_float} deg"),
            )

        return candidates

    def _iter_revolve_profile_candidates(self, selected_profile):
        if (
            hasattr(selected_profile, "count")
            and hasattr(selected_profile, "item")
            and not hasattr(selected_profile, "profileLoops")
        ):
            for i in range(int(selected_profile.count)):
                candidate = selected_profile.item(i)
                if candidate is not None:
                    yield candidate
            return

        yield selected_profile
        if hasattr(selected_profile, "face"):
            try:
                face = selected_profile.face
                if face is not None:
                    yield face
            except Exception:
                pass

    def _build_revolve_angle_input(self, sweep_rad, mode):
        if mode == "radian_real":
            return adsk.core.ValueInput.createByReal(float(sweep_rad))
        return self._angle_input_from_radians(sweep_rad)

    def _add_revolve_with_fallback_profiles(
        self, selected_profile, axis_entities, operation_enum, sweep_rad
    ):
        last_error = None
        attempt_errors = []
        for profile_candidate in self._iter_revolve_profile_candidates(
            selected_profile
        ):
            for axis_name, axis_entity in axis_entities:
                for project_axis in (False, True):
                    for angle_mode in ("radian_real", "degree_string"):
                        try:
                            revolve_input = self.revolves_class.createInput(
                                profile_candidate,
                                axis_entity,
                                operation_enum,
                            )
                            try:
                                revolve_input.isProjectAxis = project_axis
                            except Exception:
                                pass

                            angle_input = self._build_revolve_angle_input(
                                sweep_rad, angle_mode
                            )
                            revolve_input.setAngleExtent(False, angle_input)
                            return self.revolves_class.add(revolve_input)
                        except Exception as ex:
                            last_error = ex
                            attempt_errors.append(
                                f"axis={axis_name}, project_axis={project_axis}, angle_mode={angle_mode}, err={ex}"
                            )

        attempts_tail = "; ".join(attempt_errors[-6:])
        raise RuntimeError(
            f"Failed to create revolve feature: {last_error}; attempts_tail={attempts_tail}"
        )

    def _is_full_revolve_sweep(self, sweep_rad, tol=1e-6):
        return abs(abs(float(sweep_rad)) - (2.0 * math.pi)) <= tol

    def _build_segmented_revolve_tool_body(
        self,
        selected_profile,
        axis_entities,
        rotation_axis_entity,
        full_sweep_rad,
        segment_count,
    ):
        if int(segment_count) <= 1:
            raise ValueError(
                "segment_count must be > 1 for segmented revolve fallback."
            )

        segment_sweep = float(full_sweep_rad) / float(segment_count)
        feature = None
        accumulated_body = None
        created_bodies = []
        try:
            for segment_index in range(int(segment_count)):
                pre_count = self.rootComp.bRepBodies.count
                feature = self._add_revolve_with_fallback_profiles(
                    selected_profile,
                    axis_entities,
                    adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
                    segment_sweep,
                )
                new_body = self._extract_new_body(pre_count, "revolve")
                created_bodies.append(new_body)
                if segment_index > 0:
                    self._rotate_body_around_axis(
                        new_body,
                        rotation_axis_entity,
                        segment_sweep * float(segment_index),
                    )

                if accumulated_body is None:
                    accumulated_body = new_body
                    continue

                self._combine_body(accumulated_body, new_body, "Join")

            if accumulated_body is None:
                raise RuntimeError(
                    "Segmented revolve fallback did not create a tool body."
                )
            return feature, accumulated_body
        except Exception:
            for body in reversed(created_bodies):
                self._delete_body_if_possible(body)
            if accumulated_body is not None:
                self._delete_body_if_possible(accumulated_body)
            raise

    def _try_segmented_full_revolve(
        self,
        selected_profile,
        axis_entities,
        rotation_axis_entity,
        canonical_operation,
        full_sweep_rad,
        target_body_index=None,
        segment_counts=(2, 4),
    ):
        if canonical_operation not in {"NewBody", "Join", "Cut"}:
            raise RuntimeError(
                "Segmented full revolve fallback only supports NewBody, Join, and Cut operations."
            )
        if not self._is_full_revolve_sweep(full_sweep_rad):
            raise RuntimeError(
                "Segmented full revolve fallback expects a 360-degree sweep."
            )

        last_error = None
        for segment_count in segment_counts:
            pre_target_bodies = [
                self.rootComp.bRepBodies.item(i)
                for i in range(self.rootComp.bRepBodies.count)
            ]
            feature = None
            tool_body = None
            try:
                feature, tool_body = self._build_segmented_revolve_tool_body(
                    selected_profile,
                    axis_entities,
                    rotation_axis_entity,
                    full_sweep_rad,
                    segment_count,
                )
                if canonical_operation == "NewBody":
                    return feature

                target_body = self._resolve_target_body(
                    pre_target_bodies, target_body_index
                )
                self._combine_body(target_body, tool_body, canonical_operation)
                return feature
            except Exception as ex:
                last_error = ex
                if tool_body is not None:
                    self._delete_body_if_possible(tool_body)

        if last_error is None:
            raise RuntimeError("Segmented full revolve fallback did not run.")
        raise last_error

    def _build_axis_touching_revolve_plan(
        self, sketch, axis_point_1, axis_point_2, tol=1e-4
    ):
        lines = self._sketch_non_construction_curves(sketch, "sketchLines")
        arcs = self._sketch_non_construction_curves(sketch, "sketchArcs")
        circles = self._sketch_non_construction_curves(sketch, "sketchCircles")
        if not lines and not arcs:
            return None
        if circles:
            return None

        axis_origin_local = sketch.modelToSketchSpace(axis_point_1)
        axis_end_local = sketch.modelToSketchSpace(axis_point_2)
        axis_dx = float(axis_end_local.x) - float(axis_origin_local.x)
        axis_dy = float(axis_end_local.y) - float(axis_origin_local.y)
        axis_length_local = math.sqrt(axis_dx * axis_dx + axis_dy * axis_dy)
        if axis_length_local <= tol:
            return None

        axis_ux = axis_dx / axis_length_local
        axis_uy = axis_dy / axis_length_local
        perp_x = -axis_uy
        perp_y = axis_ux

        axis_direction = adsk.core.Vector3D.create(
            float(axis_point_2.x) - float(axis_point_1.x),
            float(axis_point_2.y) - float(axis_point_1.y),
            float(axis_point_2.z) - float(axis_point_1.z),
        )
        axis_length_world = math.sqrt(
            float(axis_direction.x) * float(axis_direction.x)
            + float(axis_direction.y) * float(axis_direction.y)
            + float(axis_direction.z) * float(axis_direction.z)
        )
        if axis_length_world <= tol:
            return None
        axis_direction = adsk.core.Vector3D.create(
            float(axis_direction.x) / axis_length_world,
            float(axis_direction.y) / axis_length_world,
            float(axis_direction.z) / axis_length_world,
        )

        node_tol = max(float(tol), float(axis_length_local) * 1e-6)

        def _point_data(world_point):
            local_point = sketch.modelToSketchSpace(world_point)
            rx = float(local_point.x) - float(axis_origin_local.x)
            ry = float(local_point.y) - float(axis_origin_local.y)
            axial = rx * axis_ux + ry * axis_uy
            signed_radius = rx * perp_x + ry * perp_y
            return {
                "world": world_point,
                "local": local_point,
                "axial": axial,
                "signed_radius": signed_radius,
                "radius": abs(signed_radius),
                "on_axis": abs(signed_radius) <= node_tol,
            }

        nodes = []

        def _find_or_add_node(point_data):
            for index, node in enumerate(nodes):
                dx = float(node["local"].x) - float(point_data["local"].x)
                dy = float(node["local"].y) - float(point_data["local"].y)
                if math.sqrt(dx * dx + dy * dy) <= node_tol:
                    return index
            node = dict(point_data)
            node["curve_indices"] = []
            nodes.append(node)
            return len(nodes) - 1

        curve_entries = []

        def _add_curve(curve, kind):
            start_world = self._world_point_from_sketch_point(
                sketch,
                getattr(curve, "startSketchPoint", None),
            )
            end_world = self._world_point_from_sketch_point(
                sketch,
                getattr(curve, "endSketchPoint", None),
            )
            if start_world is None or end_world is None:
                return False
            start_idx = _find_or_add_node(_point_data(start_world))
            end_idx = _find_or_add_node(_point_data(end_world))
            entry = {
                "curve": curve,
                "kind": kind,
                "start_node": start_idx,
                "end_node": end_idx,
            }
            entry["is_axis"] = (
                kind == "line"
                and nodes[start_idx]["on_axis"]
                and nodes[end_idx]["on_axis"]
            )
            curve_entries.append(entry)
            curve_index = len(curve_entries) - 1
            nodes[start_idx]["curve_indices"].append(curve_index)
            nodes[end_idx]["curve_indices"].append(curve_index)
            return True

        for line in lines:
            _add_curve(line, "line")
        for arc in arcs:
            _add_curve(arc, "arc")

        if not curve_entries:
            return None

        axis_curve_indices = [
            index for index, entry in enumerate(curve_entries) if entry["is_axis"]
        ]
        non_axis_curve_indices = [
            index for index, entry in enumerate(curve_entries) if not entry["is_axis"]
        ]
        if not axis_curve_indices or not non_axis_curve_indices:
            return None

        axis_contact_nodes = [
            index
            for index, node in enumerate(nodes)
            if node["on_axis"]
            and any(
                not curve_entries[curve_index]["is_axis"]
                for curve_index in node["curve_indices"]
            )
        ]
        if len(axis_contact_nodes) != 2:
            return None

        def _trace_boundary_chain(start_node):
            current_node = start_node
            previous_curve = None
            used_non_axis = set()
            chain = []
            chain_nodes = [start_node]

            while True:
                if current_node != start_node and current_node in axis_contact_nodes:
                    break

                next_curves = [
                    curve_index
                    for curve_index in nodes[current_node]["curve_indices"]
                    if not curve_entries[curve_index]["is_axis"]
                    and curve_index != previous_curve
                ]
                if len(next_curves) != 1:
                    return None

                curve_index = next_curves[0]
                if curve_index in used_non_axis:
                    return None

                curve_entry = curve_entries[curve_index]
                if curve_entry["start_node"] == current_node:
                    next_node = curve_entry["end_node"]
                elif curve_entry["end_node"] == current_node:
                    next_node = curve_entry["start_node"]
                else:
                    return None

                chain.append(
                    {
                        "curve": curve_entry["curve"],
                        "kind": curve_entry["kind"],
                        "start_node": current_node,
                        "end_node": next_node,
                    }
                )
                used_non_axis.add(curve_index)
                previous_curve = curve_index
                current_node = next_node
                chain_nodes.append(current_node)

            if current_node == start_node:
                return None
            if used_non_axis != set(non_axis_curve_indices):
                return None
            return chain, chain_nodes

        traced = _trace_boundary_chain(axis_contact_nodes[0])
        if traced is None:
            traced = _trace_boundary_chain(axis_contact_nodes[1])
        if traced is None:
            return None

        chain, chain_nodes = traced
        if not chain:
            return None

        signed_radii = [
            nodes[node_index]["signed_radius"]
            for node_index in chain_nodes
            if abs(nodes[node_index]["signed_radius"]) > node_tol
        ]
        if signed_radii:
            first_sign = 1.0 if signed_radii[0] > 0.0 else -1.0
            if any((radius > 0.0) != (first_sign > 0.0) for radius in signed_radii):
                return None

        axial_values = [nodes[node_index]["axial"] for node_index in chain_nodes]
        axial_deltas = [
            axial_values[index + 1] - axial_values[index]
            for index in range(len(axial_values) - 1)
            if abs(axial_values[index + 1] - axial_values[index]) > node_tol
        ]
        if axial_deltas and not (
            all(delta >= -node_tol for delta in axial_deltas)
            or all(delta <= node_tol for delta in axial_deltas)
        ):
            return None

        def _axis_world_point(axial):
            return adsk.core.Point3D.create(
                float(axis_point_1.x) + float(axis_direction.x) * float(axial),
                float(axis_point_1.y) + float(axis_direction.y) * float(axial),
                float(axis_point_1.z) + float(axis_direction.z) * float(axial),
            )

        if len(chain) == 1 and chain[0]["kind"] == "arc":
            arc_curve = chain[0]["curve"]
            center_world = self._world_point_from_sketch_point(
                sketch,
                getattr(arc_curve, "centerSketchPoint", None),
            )
            midpoint_world = self._curve_midpoint_world(sketch, arc_curve)
            if center_world is None or midpoint_world is None:
                return None

            center_data = _point_data(center_world)
            midpoint_data = _point_data(midpoint_world)
            start_node = nodes[chain[0]["start_node"]]
            end_node = nodes[chain[0]["end_node"]]
            radius = abs(float(getattr(arc_curve, "radius", 0.0)))
            radius_tol = max(node_tol, radius * 1e-4)
            if radius <= radius_tol:
                return None
            if abs(center_data["signed_radius"]) > radius_tol:
                return None
            if abs(abs(midpoint_data["signed_radius"]) - radius) > radius_tol:
                return None
            if (
                abs(start_node["radius"]) > radius_tol
                or abs(end_node["radius"]) > radius_tol
            ):
                return None
            if (
                abs(abs(start_node["axial"] - center_data["axial"]) - radius)
                > radius_tol
            ):
                return None
            if abs(abs(end_node["axial"] - center_data["axial"]) - radius) > radius_tol:
                return None
            return {
                "kind": "sphere",
                "center": center_world,
                "radius": radius,
            }

        if any(item["kind"] != "line" for item in chain):
            return None

        segments = []
        for item in chain:
            start_node = nodes[item["start_node"]]
            end_node = nodes[item["end_node"]]
            if abs(end_node["axial"] - start_node["axial"]) <= node_tol:
                continue
            if start_node["radius"] <= node_tol and end_node["radius"] <= node_tol:
                continue
            segments.append(
                {
                    "point_one": _axis_world_point(start_node["axial"]),
                    "radius_one": float(start_node["radius"]),
                    "point_two": _axis_world_point(end_node["axial"]),
                    "radius_two": float(end_node["radius"]),
                }
            )

        if not segments:
            return None
        return {
            "kind": "segment_stack",
            "segments": segments,
        }

    def _build_axis_touching_revolve_temp_body(
        self, sketch, axis_point_1, axis_point_2
    ):
        plan = self._build_axis_touching_revolve_plan(
            sketch, axis_point_1, axis_point_2
        )
        if plan is None:
            return None

        temp_brep = adsk.fusion.TemporaryBRepManager.get()
        if plan["kind"] == "sphere":
            temp_body = temp_brep.createSphere(plan["center"], float(plan["radius"]))
            if temp_body is None:
                raise RuntimeError(
                    "Failed to create sphere for axis-touching revolve fallback."
                )
            return temp_body

        if plan["kind"] != "segment_stack":
            raise RuntimeError(
                f"Unsupported axis-touching revolve fallback kind: {plan['kind']}"
            )

        accumulated_body = None
        for segment in plan["segments"]:
            primitive_body = temp_brep.createCylinderOrCone(
                segment["point_one"],
                float(segment["radius_one"]),
                segment["point_two"],
                float(segment["radius_two"]),
            )
            if primitive_body is None:
                raise RuntimeError(
                    "Failed to create cylinder/cone for axis-touching revolve fallback."
                )
            if accumulated_body is None:
                accumulated_body = primitive_body
                continue
            if not temp_brep.booleanOperation(
                accumulated_body,
                primitive_body,
                adsk.fusion.BooleanTypes.UnionBooleanType,
            ):
                raise RuntimeError(
                    "Failed to union axis-touching revolve fallback segments."
                )

        return accumulated_body

    def _persist_temp_body(self, temp_body, body_name="temporary body"):
        if temp_body is None:
            raise RuntimeError(f"{body_name} is empty.")

        base_features = getattr(
            getattr(self.rootComp, "features", None), "baseFeatures", None
        )
        if base_features is None:
            persisted = self.rootComp.bRepBodies.add(temp_body)
            if persisted is None:
                raise RuntimeError(f"Failed to persist {body_name}.")
            return persisted

        base_feature = base_features.add()
        base_feature.startEdit()
        try:
            persisted = self.rootComp.bRepBodies.add(temp_body, base_feature)
        finally:
            base_feature.finishEdit()
        if persisted is None:
            raise RuntimeError(f"Failed to persist {body_name}.")
        return persisted

    def _temporary_boolean_enum(self, canonical_operation):
        if canonical_operation == "Join":
            return adsk.fusion.BooleanTypes.UnionBooleanType
        if canonical_operation == "Cut":
            return adsk.fusion.BooleanTypes.DifferenceBooleanType
        if canonical_operation == "Intersect":
            return adsk.fusion.BooleanTypes.IntersectionBooleanType
        raise ValueError(
            f"Unsupported temporary boolean operation: {canonical_operation}"
        )

    def _try_axis_touching_full_revolve_primitive_fallback(
        self,
        sketch,
        axis_point_1,
        axis_point_2,
        canonical_operation,
        target_body_index=None,
    ):
        temp_body = self._build_axis_touching_revolve_temp_body(
            sketch, axis_point_1, axis_point_2
        )
        if temp_body is None:
            raise RuntimeError(
                "Axis-touching revolve primitive fallback is not applicable."
            )

        if canonical_operation == "NewBody":
            return self._persist_temp_body(
                temp_body,
                body_name="axis-touching revolve fallback body",
            )

        pre_target_bodies = [
            self.rootComp.bRepBodies.item(i)
            for i in range(self.rootComp.bRepBodies.count)
        ]
        target_body = self._resolve_target_body(pre_target_bodies, target_body_index)
        temp_brep = adsk.fusion.TemporaryBRepManager.get()
        temp_target_body = temp_brep.copy(target_body)
        if temp_target_body is None:
            raise RuntimeError(
                "Failed to copy target body for axis-touching revolve fallback."
            )
        if not temp_brep.booleanOperation(
            temp_target_body,
            temp_body,
            self._temporary_boolean_enum(canonical_operation),
        ):
            raise RuntimeError(
                f"Temporary boolean {canonical_operation} failed for axis-touching revolve fallback."
            )

        persisted_result = self._persist_temp_body(
            temp_target_body,
            body_name="axis-touching revolve boolean result",
        )
        self._delete_body_if_possible(target_body)
        return persisted_result

    def _profile_reference_point(self, profile):
        bbox = profile.boundingBox
        return adsk.core.Point3D.create(
            (bbox.minPoint.x + bbox.maxPoint.x) * 0.5,
            (bbox.minPoint.y + bbox.maxPoint.y) * 0.5,
            (bbox.minPoint.z + bbox.maxPoint.z) * 0.5,
        )

    def _primary_profile(self, selected_profile, sketch):
        if hasattr(selected_profile, "boundingBox"):
            return selected_profile
        if hasattr(selected_profile, "count") and selected_profile.count > 0:
            return selected_profile.item(0)
        return sketch.profiles.item(0)

    def _project_point_onto_axis(self, point, axis_point, axis_vector):
        px = point.x - axis_point.x
        py = point.y - axis_point.y
        pz = point.z - axis_point.z

        t = px * axis_vector.x + py * axis_vector.y + pz * axis_vector.z
        return adsk.core.Point3D.create(
            axis_point.x + t * axis_vector.x,
            axis_point.y + t * axis_vector.y,
            axis_point.z + t * axis_vector.z,
        )

    def _rotate_vector_about_axis(self, vector, axis_vector, angle_rad):
        axis = self._normalize_vector3(self._vector3d_to_list(axis_vector))
        vx, vy, vz = self._vector3d_to_list(vector)
        cos_angle = math.cos(float(angle_rad))
        sin_angle = math.sin(float(angle_rad))
        dot = axis[0] * vx + axis[1] * vy + axis[2] * vz
        cross = [
            axis[1] * vz - axis[2] * vy,
            axis[2] * vx - axis[0] * vz,
            axis[0] * vy - axis[1] * vx,
        ]
        return adsk.core.Vector3D.create(
            vx * cos_angle + cross[0] * sin_angle + axis[0] * dot * (1.0 - cos_angle),
            vy * cos_angle + cross[1] * sin_angle + axis[1] * dot * (1.0 - cos_angle),
            vz * cos_angle + cross[2] * sin_angle + axis[2] * dot * (1.0 - cos_angle),
        )

    def _rotate_point_about_axis(self, point, axis_point, axis_vector, angle_rad):
        relative = adsk.core.Vector3D.create(
            float(point.x) - float(axis_point.x),
            float(point.y) - float(axis_point.y),
            float(point.z) - float(axis_point.z),
        )
        rotated = self._rotate_vector_about_axis(relative, axis_vector, angle_rad)
        return adsk.core.Point3D.create(
            float(axis_point.x) + float(rotated.x),
            float(axis_point.y) + float(rotated.y),
            float(axis_point.z) + float(rotated.z),
        )

    def _screw_transform_point(
        self, point, axis_point, axis_vector, angle_rad, axial_offset
    ):
        rotated = self._rotate_point_about_axis(
            point, axis_point, axis_vector, angle_rad
        )
        return adsk.core.Point3D.create(
            float(rotated.x) + float(axis_vector.x) * float(axial_offset),
            float(rotated.y) + float(axis_vector.y) * float(axial_offset),
            float(rotated.z) + float(axis_vector.z) * float(axial_offset),
        )

    def _screw_transform_vector(self, vector, axis_vector, angle_rad):
        return self._rotate_vector_about_axis(vector, axis_vector, angle_rad)

    def _sketch_attitude_contains_axis(
        self, attitude, axis_point, axis_vector, tol=1e-6
    ):
        if not attitude or len(attitude) < 3:
            return False
        origin, u_dir, v_dir = attitude
        normal = self._normalize_vector3(
            self._cross_product3(
                self._vector3d_to_list(u_dir),
                self._vector3d_to_list(v_dir),
            )
        )
        axis_offset = [
            float(axis_point.x) - float(origin.x),
            float(axis_point.y) - float(origin.y),
            float(axis_point.z) - float(origin.z),
        ]
        distance = abs(self._dot_product3(axis_offset, normal))
        axis_in_plane = abs(
            self._dot_product3(self._vector3d_to_list(axis_vector), normal)
        )
        return distance <= tol and axis_in_plane <= tol

    def _supports_axial_helix_profile_copy(self, sketch):
        curves = getattr(sketch, "sketchCurves", None)
        if curves is None:
            return False
        unsupported_collections = (
            "sketchArcs",
            "sketchEllipses",
            "sketchFittedSplines",
            "sketchControlPointSplines",
            "sketchFixedSplines",
            "sketchConics",
            "sketchEllipticalArcs",
        )
        for attr_name in unsupported_collections:
            collection = getattr(curves, attr_name, None)
            if collection is None:
                continue
            try:
                if int(collection.count) != 0:
                    return False
            except Exception:
                return False
        return True

    def _create_sketch_from_world_basis(self, origin_point, u_dir, v_dir):
        origin_world = self._coerce_point3d(origin_point)
        u_world = self._normalize_vector3d(u_dir)
        v_world = self._normalize_vector3d(v_dir)
        u_target = adsk.core.Point3D.create(
            float(origin_world.x) + float(u_world.x),
            float(origin_world.y) + float(u_world.y),
            float(origin_world.z) + float(u_world.z),
        )
        v_target = adsk.core.Point3D.create(
            float(origin_world.x) + float(v_world.x),
            float(origin_world.y) + float(v_world.y),
            float(origin_world.z) + float(v_world.z),
        )

        base_plane_type, base_offset, _base_normal = self._choose_helper_base_plane(
            self._point3d_to_list(origin_world),
            self._vector3d_to_list(v_world),
        )
        base_plane = self.create_plane_by_base(base_plane_type, base_offset)
        base_sketch = self.sketches_class.add(base_plane)

        point_one = base_sketch.sketchPoints.add(
            base_sketch.modelToSketchSpace(origin_world)
        )
        point_two = base_sketch.sketchPoints.add(
            base_sketch.modelToSketchSpace(u_target)
        )
        point_three = base_sketch.sketchPoints.add(
            base_sketch.modelToSketchSpace(v_target)
        )

        plane_input = self.rootComp.constructionPlanes.createInput()
        plane_input.setByThreePoints(point_one, point_two, point_three)
        plane = self.rootComp.constructionPlanes.add(plane_input)
        return self.sketches_class.add(plane)

    def _sketch_entity_identity(self, sketch_entity):
        try:
            token = getattr(sketch_entity, "entityToken", None)
            if token:
                return ("token", token)
        except Exception:
            pass
        return ("id", id(sketch_entity))

    def _profile_sketch_entities(self, selected_profile):
        profile_loops = getattr(selected_profile, "profileLoops", None)
        if profile_loops is None:
            return None

        entities = []
        seen = set()
        for loop_index in range(int(profile_loops.count)):
            profile_loop = profile_loops.item(loop_index)
            profile_curves = getattr(profile_loop, "profileCurves", None)
            if profile_curves is None:
                continue
            for curve_index in range(int(profile_curves.count)):
                profile_curve = profile_curves.item(curve_index)
                sketch_entity = getattr(profile_curve, "sketchEntity", None)
                if sketch_entity is None:
                    continue
                entity_key = self._sketch_entity_identity(sketch_entity)
                if entity_key in seen:
                    continue
                seen.add(entity_key)
                entities.append(sketch_entity)
        return entities

    def _iter_axial_helix_profile_entities(self, source_sketch, selected_profile=None):
        selected_entities = self._profile_sketch_entities(selected_profile)
        if selected_entities is not None:
            for sketch_entity in selected_entities:
                if bool(getattr(sketch_entity, "isConstruction", False)):
                    continue
                yield sketch_entity
            return

        curves = getattr(source_sketch, "sketchCurves", None)
        if curves is None:
            return

        for attr_name in ("sketchLines", "sketchCircles"):
            collection = getattr(curves, attr_name, None)
            if collection is None:
                continue
            for index in range(int(collection.count)):
                sketch_entity = collection.item(index)
                if bool(getattr(sketch_entity, "isConstruction", False)):
                    continue
                yield sketch_entity

    def _copy_axial_helix_profile_to_sketch(
        self,
        source_sketch,
        target_sketch,
        axis_point,
        axis_vector,
        angle_rad,
        axial_offset,
        selected_profile=None,
    ):
        if getattr(source_sketch, "sketchCurves", None) is None:
            raise RuntimeError("Source sketch has no sketchCurves.")

        for sketch_entity in self._iter_axial_helix_profile_entities(
            source_sketch,
            selected_profile=selected_profile,
        ):
            start_geometry = getattr(
                getattr(sketch_entity, "startSketchPoint", None),
                "geometry",
                None,
            )
            end_geometry = getattr(
                getattr(sketch_entity, "endSketchPoint", None),
                "geometry",
                None,
            )
            if start_geometry is not None and end_geometry is not None:
                start_world = source_sketch.sketchToModelSpace(start_geometry)
                end_world = source_sketch.sketchToModelSpace(end_geometry)
                start_transformed = self._screw_transform_point(
                    start_world,
                    axis_point,
                    axis_vector,
                    angle_rad,
                    axial_offset,
                )
                end_transformed = self._screw_transform_point(
                    end_world,
                    axis_point,
                    axis_vector,
                    angle_rad,
                    axial_offset,
                )
                start_local = target_sketch.modelToSketchSpace(start_transformed)
                end_local = target_sketch.modelToSketchSpace(end_transformed)
                target_sketch.sketchCurves.sketchLines.addByTwoPoints(
                    start_local, end_local
                )
                continue

            center_geometry = getattr(
                getattr(sketch_entity, "centerSketchPoint", None),
                "geometry",
                None,
            )
            if center_geometry is None:
                continue
            center_world = source_sketch.sketchToModelSpace(center_geometry)
            center_transformed = self._screw_transform_point(
                center_world,
                axis_point,
                axis_vector,
                angle_rad,
                axial_offset,
            )
            center_local = target_sketch.modelToSketchSpace(center_transformed)
            target_sketch.sketchCurves.sketchCircles.addByCenterRadius(
                center_local,
                abs(float(sketch_entity.radius)),
            )

    def _axial_helix_rail_points(
        self,
        sketch,
        axis_point,
        axis_vector,
        selected_profile=None,
        tol=1e-6,
    ):
        rail_points = []
        seen_keys = set()
        scale = 1.0 / float(tol)
        for line in self._iter_axial_helix_profile_entities(
            sketch,
            selected_profile=selected_profile,
        ):
            if not hasattr(line, "startSketchPoint") or not hasattr(
                line, "endSketchPoint"
            ):
                continue
            for attr_name in ("startSketchPoint", "endSketchPoint"):
                sketch_point = getattr(line, attr_name, None)
                geometry = getattr(sketch_point, "geometry", None)
                if geometry is None:
                    continue
                world_point = sketch.sketchToModelSpace(geometry)
                axis_foot = self._project_point_onto_axis(
                    world_point, axis_point, axis_vector
                )
                radial_distance = math.sqrt(
                    float(world_point.x - axis_foot.x)
                    * float(world_point.x - axis_foot.x)
                    + float(world_point.y - axis_foot.y)
                    * float(world_point.y - axis_foot.y)
                    + float(world_point.z - axis_foot.z)
                    * float(world_point.z - axis_foot.z)
                )
                if radial_distance <= tol:
                    continue
                point_key = (
                    int(round(float(world_point.x) * scale)),
                    int(round(float(world_point.y) * scale)),
                    int(round(float(world_point.z) * scale)),
                )
                if point_key in seen_keys:
                    continue
                seen_keys.add(point_key)
                rail_points.append(world_point)
        return rail_points

    def _axial_helix_profile_attitude(self, sketch, axis_point, axis_vector):
        if not hasattr(self, "_find_sketch_index") or not hasattr(
            self, "_get_sketch_attitude"
        ):
            return None
        sketch_index = self._find_sketch_index(sketch)
        if sketch_index is None:
            return None
        attitude = self._get_sketch_attitude(sketch_index)
        if not self._sketch_attitude_contains_axis(attitude, axis_point, axis_vector):
            return None
        if not self._supports_axial_helix_profile_copy(sketch):
            return None
        return attitude

    def _should_use_axial_helix_guide_rails(self, selected_profile, rail_points):
        if len(rail_points) < 2:
            return False
        return len(self._profile_list_from_selection(selected_profile)) == 1

    def _profile_reference_radius(self, profile, axis_point, axis_vector):
        profile_center = self._profile_reference_point(profile)
        axis_foot = self._project_point_onto_axis(
            profile_center, axis_point, axis_vector
        )
        rx = float(profile_center.x - axis_foot.x)
        ry = float(profile_center.y - axis_foot.y)
        rz = float(profile_center.z - axis_foot.z)
        return math.sqrt(rx * rx + ry * ry + rz * rz)

    def _should_use_axial_helix_centerline(
        self, profile, axis_point, axis_vector, tol=1e-6
    ):
        return self._profile_reference_radius(profile, axis_point, axis_vector) > float(
            tol
        )

    def _is_axial_helix_guide_intersection_error(self, error):
        markers = (
            "asm_guide_not_intersect",
            "guide_not_intersect",
            "轨道未与所有轮廓相交",
            "所有轨道必须与每个轮廓相交",
        )
        candidates = [str(error).lower()]
        try:
            decoded = str(error).encode("utf-8").decode("unicode_escape").lower()
            candidates.append(decoded)
        except Exception:
            pass
        return any(
            marker in candidate for candidate in candidates for marker in markers
        )

    def _is_axial_helix_centerline_intersection_error(self, error):
        markers = (
            "asm_centerline_section_doesnt_cut_path",
            "centerline_section_doesnt_cut_path",
            "中心线未与所有轮廓平面相交",
        )
        candidates = [str(error).lower()]
        try:
            decoded = str(error).encode("utf-8").decode("unicode_escape").lower()
            candidates.append(decoded)
        except Exception:
            pass
        return any(
            marker in candidate for candidate in candidates for marker in markers
        )

    def _axial_helix_chunk_limits(self, total_turns, use_guide_rails, use_centerline):
        if use_guide_rails:
            default_chunk_turns = 1.0
            min_chunk_turns = 0.125
        elif use_centerline:
            default_chunk_turns = 0.03125
            min_chunk_turns = 0.03125
        else:
            default_chunk_turns = 0.25
            min_chunk_turns = 0.0625
        default_chunk_turns = min(float(total_turns), float(default_chunk_turns))
        min_chunk_turns = min(default_chunk_turns, float(min_chunk_turns))
        return default_chunk_turns, min_chunk_turns

    def _build_axial_profile_helix_body(
        self,
        sketch,
        selected_profile,
        axis_point,
        axis_vector,
        pitch,
        turns,
        handed,
    ):
        attitude = self._axial_helix_profile_attitude(sketch, axis_point, axis_vector)
        if attitude is None:
            raise RuntimeError("Sketch is not eligible for axial-profile helix loft.")

        profile_ref = self._primary_profile(selected_profile, sketch)
        centerline_point = self._infer_helix_start_point(
            profile_ref, axis_point, axis_vector
        )
        signed_pitch = float(pitch) if handed == "right" else -float(pitch)
        total_turns = float(turns)
        rail_points = self._axial_helix_rail_points(
            sketch,
            axis_point,
            axis_vector,
            selected_profile=selected_profile,
        )
        use_guide_rails = self._should_use_axial_helix_guide_rails(
            selected_profile, rail_points
        )
        use_centerline = self._should_use_axial_helix_centerline(
            profile_ref,
            axis_point,
            axis_vector,
        )
        default_chunk_turns, min_chunk_turns = self._axial_helix_chunk_limits(
            total_turns,
            use_guide_rails,
            use_centerline,
        )
        temp_brep = adsk.fusion.TemporaryBRepManager.get()
        loft_features = self.rootComp.features.loftFeatures

        helix_body = None
        last_feature = None
        current_turn = 0.0
        chunk_turns = default_chunk_turns
        current_section = selected_profile

        while current_turn < total_turns - 1e-9:
            remaining_turns = total_turns - current_turn
            current_chunk_turns = min(chunk_turns, remaining_turns)
            end_turn = current_turn + current_chunk_turns
            end_angle = 2.0 * math.pi * end_turn
            end_offset = signed_pitch * end_turn
            new_body = None

            try:
                origin, u_dir, v_dir = attitude
                end_sketch = self._create_sketch_from_world_basis(
                    self._screw_transform_point(
                        origin,
                        axis_point,
                        axis_vector,
                        end_angle,
                        end_offset,
                    ),
                    self._screw_transform_vector(u_dir, axis_vector, end_angle),
                    self._screw_transform_vector(v_dir, axis_vector, end_angle),
                )
                self._copy_axial_helix_profile_to_sketch(
                    sketch,
                    end_sketch,
                    axis_point,
                    axis_vector,
                    end_angle,
                    end_offset,
                    selected_profile=selected_profile,
                )
                end_section = self._select_profile(end_sketch)

                pre_count = self.rootComp.bRepBodies.count
                loft_input = loft_features.createInput(
                    adsk.fusion.FeatureOperations.NewBodyFeatureOperation
                )
                loft_input.isSolid = True
                loft_input.loftSections.add(current_section)
                loft_input.loftSections.add(end_section)

                if use_guide_rails:
                    start_angle = 2.0 * math.pi * current_turn
                    start_offset = signed_pitch * current_turn
                    for rail_point in rail_points:
                        rail_start = self._screw_transform_point(
                            rail_point,
                            axis_point,
                            axis_vector,
                            start_angle,
                            start_offset,
                        )
                        rail_axis_point = self._project_point_onto_axis(
                            rail_start,
                            axis_point,
                            axis_vector,
                        )
                        rail_wire = temp_brep.createHelixWire(
                            rail_axis_point,
                            axis_vector,
                            rail_start,
                            signed_pitch,
                            current_chunk_turns,
                            0.0,
                        )
                        if rail_wire is None or rail_wire.edges.count == 0:
                            raise RuntimeError(
                                "Failed to create axial helix guide rail."
                            )
                        rail_path = self._create_path_from_helix_wire(rail_wire)
                        loft_input.centerLineOrRails.addRail(rail_path)
                elif use_centerline:
                    centerline_start = self._screw_transform_point(
                        centerline_point,
                        axis_point,
                        axis_vector,
                        2.0 * math.pi * current_turn,
                        signed_pitch * current_turn,
                    )
                    helix_axis_point = self._project_point_onto_axis(
                        centerline_start,
                        axis_point,
                        axis_vector,
                    )
                    helix_wire = temp_brep.createHelixWire(
                        helix_axis_point,
                        axis_vector,
                        centerline_start,
                        signed_pitch,
                        current_chunk_turns,
                        0.0,
                    )
                    if helix_wire is None or helix_wire.edges.count == 0:
                        raise RuntimeError("Failed to create axial helix centerline.")
                    centerline_path = self._create_path_from_helix_wire(helix_wire)
                    try:
                        loft_input.centerLineOrRails.addCenterLine(centerline_path)
                    except Exception:
                        pass

                feature = loft_features.add(loft_input)
                new_body = self._extract_new_body(pre_count, "axial helix loft")
                if helix_body is None:
                    helix_body = new_body
                else:
                    self._combine_body(helix_body, new_body, "Join")
                last_feature = feature
                current_section = end_section
                current_turn = end_turn
            except Exception as ex:
                if new_body is not None and new_body is not helix_body:
                    self._delete_body_if_possible(new_body)
                if (
                    use_guide_rails
                    and helix_body is None
                    and current_turn <= 1e-9
                    and self._is_axial_helix_guide_intersection_error(ex)
                ):
                    use_guide_rails = False
                    chunk_turns, min_chunk_turns = self._axial_helix_chunk_limits(
                        total_turns,
                        use_guide_rails,
                        use_centerline,
                    )
                    continue
                if (
                    not use_guide_rails
                    and use_centerline
                    and helix_body is None
                    and current_turn <= 1e-9
                    and self._is_axial_helix_centerline_intersection_error(ex)
                ):
                    use_centerline = False
                    chunk_turns, min_chunk_turns = self._axial_helix_chunk_limits(
                        total_turns,
                        use_guide_rails,
                        use_centerline,
                    )
                    continue
                if current_chunk_turns <= min_chunk_turns + 1e-9:
                    raise RuntimeError(
                        "Axial helix loft failed "
                        f"near turn={current_turn:.4f} with chunk_turns={current_chunk_turns:.4f}: {ex}"
                    )
                chunk_turns = max(min_chunk_turns, current_chunk_turns * 0.5)

        if helix_body is None:
            raise RuntimeError("Axial helix loft did not create a body.")

        return helix_body, last_feature

    def _create_axial_profile_helix_loft(
        self,
        sketch,
        selected_profile,
        axis_point,
        axis_vector,
        pitch,
        turns,
        handed,
        canonical_operation,
        pre_target_bodies,
        target_body_index=None,
    ):
        helix_body, last_feature = self._build_axial_profile_helix_body(
            sketch,
            selected_profile,
            axis_point,
            axis_vector,
            pitch,
            turns,
            handed,
        )

        if canonical_operation != "NewBody":
            target_body = self._resolve_target_body(
                pre_target_bodies, target_body_index
            )
            try:
                return self._combine_body(target_body, helix_body, canonical_operation)
            except Exception:
                self._delete_body_if_possible(helix_body)
                raise

        return last_feature if last_feature is not None else helix_body

    def _create_multi_profile_axial_helix_loft(
        self,
        sketch,
        selected_profile,
        axis_point,
        axis_vector,
        pitch,
        turns,
        handed,
        canonical_operation,
        pre_target_bodies,
        target_body_index=None,
    ):
        selected_profiles = self._profile_list_from_selection(selected_profile)
        if not selected_profiles:
            raise RuntimeError(
                "No profiles available for multi-profile axial helix loft."
            )
        if len(selected_profiles) == 1:
            return self._create_axial_profile_helix_loft(
                sketch,
                selected_profiles[0],
                axis_point,
                axis_vector,
                pitch,
                turns,
                handed,
                canonical_operation,
                pre_target_bodies,
                target_body_index=target_body_index,
            )

        helix_body = None
        last_feature = None
        for profile in selected_profiles:
            new_body, feature = self._build_axial_profile_helix_body(
                sketch,
                profile,
                axis_point,
                axis_vector,
                pitch,
                turns,
                handed,
            )
            if helix_body is None:
                helix_body = new_body
            else:
                try:
                    self._combine_body(helix_body, new_body, "Join")
                except Exception:
                    self._delete_body_if_possible(new_body)
                    raise
            if feature is not None:
                last_feature = feature

        if helix_body is None:
            raise RuntimeError("Multi-profile axial helix loft did not create a body.")

        if canonical_operation != "NewBody":
            target_body = self._resolve_target_body(
                pre_target_bodies, target_body_index
            )
            try:
                return self._combine_body(target_body, helix_body, canonical_operation)
            except Exception:
                self._delete_body_if_possible(helix_body)
                raise

        return last_feature if last_feature is not None else helix_body

    def _infer_helix_start_point(self, profile, axis_point, axis_vector):
        profile_center = self._profile_reference_point(profile)
        foot = self._project_point_onto_axis(profile_center, axis_point, axis_vector)

        rx = profile_center.x - foot.x
        ry = profile_center.y - foot.y
        rz = profile_center.z - foot.z
        radius = math.sqrt(rx * rx + ry * ry + rz * rz)

        if radius <= 1e-8:
            # ???????????????????????????????????
            if abs(axis_vector.x) < 0.9:
                ref = adsk.core.Vector3D.create(1.0, 0.0, 0.0)
            else:
                ref = adsk.core.Vector3D.create(0.0, 1.0, 0.0)
            radial = axis_vector.crossProduct(ref)
            radial.normalize()
            radius = 1e-3
            return adsk.core.Point3D.create(
                foot.x + radial.x * radius,
                foot.y + radial.y * radius,
                foot.z + radial.z * radius,
            )

        inv_radius = 1.0 / radius
        return adsk.core.Point3D.create(
            foot.x + rx * inv_radius * radius,
            foot.y + ry * inv_radius * radius,
            foot.z + rz * inv_radius * radius,
        )

    def _create_path_from_helix_wire(self, helix_wire):
        helix_edge = helix_wire.edges.item(0)
        try:
            return self.rootComp.features.createPath(helix_edge, False)
        except Exception:
            pass

        # ???????????????? BaseFeature ??? edge ?? Path?
        base_feature = self.rootComp.features.baseFeatures.add()
        base_feature.startEdit()
        persisted_wire = self.rootComp.bRepBodies.add(helix_wire, base_feature)
        base_feature.finishEdit()
        if persisted_wire is None or persisted_wire.edges.count == 0:
            raise RuntimeError("Failed to persist helix wire for sweep path.")
        return self.rootComp.features.createPath(persisted_wire.edges.item(0), False)

    def _infer_profile_diameter(self, profile):
        bbox = profile.boundingBox
        dx = abs(bbox.maxPoint.x - bbox.minPoint.x)
        dy = abs(bbox.maxPoint.y - bbox.minPoint.y)
        candidates = [v for v in (dx, dy) if v > 1e-8]
        if not candidates:
            raise RuntimeError("Cannot infer profile diameter for helix fallback.")
        return min(candidates)

    def _create_pipe_from_path(
        self,
        path,
        diameter,
        feature_operation=adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
    ):
        pipe_features = self.rootComp.features.pipeFeatures
        pipe_input = pipe_features.createInput(
            path,
            feature_operation,
        )
        pipe_input.sectionType = adsk.fusion.PipeSectionTypes.CircularPipeSectionType
        pipe_input.sectionSize = adsk.core.ValueInput.createByReal(float(diameter))
        return pipe_features.add(pipe_input)

    def _infer_circular_helix_profile(self, sketch, axis_point, axis_vector, tol=1e-6):
        curves = getattr(sketch, "sketchCurves", None)
        if curves is None:
            return None

        sketch_circles = getattr(curves, "sketchCircles", None)
        if sketch_circles is None:
            return None

        try:
            if int(sketch_circles.count) != 1:
                return None
        except Exception:
            return None

        for attr_name in (
            "sketchLines",
            "sketchArcs",
            "sketchEllipses",
            "sketchFittedSplines",
            "sketchControlPointSplines",
        ):
            collection = getattr(curves, attr_name, None)
            if collection is None:
                continue
            try:
                if int(collection.count) != 0:
                    return None
            except Exception:
                return None

        circle = sketch_circles.item(0)
        center_sketch_point = getattr(circle, "centerSketchPoint", None)
        center_geometry = getattr(center_sketch_point, "geometry", None)
        if center_geometry is None:
            return None

        sketch_index = self._find_sketch_index(sketch)
        if sketch_index is not None:
            center_model = self._get_mapped_model_point(
                sketch_index,
                [float(center_geometry.x), float(center_geometry.y)],
            )
        else:
            try:
                center_model = sketch.sketchToModelSpace(center_geometry)
            except Exception:
                center_model = self._to_point3d(
                    [center_geometry.x, center_geometry.y, 0.0]
                )

        profile_radius = abs(float(getattr(circle, "radius", 0.0)))
        if profile_radius <= tol:
            return None

        axis_foot = self._project_point_onto_axis(center_model, axis_point, axis_vector)

        rx = float(center_model.x - axis_foot.x)
        ry = float(center_model.y - axis_foot.y)
        rz = float(center_model.z - axis_foot.z)
        centerline_radius = math.sqrt(rx * rx + ry * ry + rz * rz)
        if centerline_radius <= tol:
            return None

        radial_dir = adsk.core.Vector3D.create(
            rx / centerline_radius,
            ry / centerline_radius,
            rz / centerline_radius,
        )
        tangent_dir = axis_vector.crossProduct(radial_dir)
        tangent_length = math.sqrt(
            float(tangent_dir.x) * float(tangent_dir.x)
            + float(tangent_dir.y) * float(tangent_dir.y)
            + float(tangent_dir.z) * float(tangent_dir.z)
        )
        if tangent_length <= tol:
            return None
        tangent_dir = adsk.core.Vector3D.create(
            float(tangent_dir.x) / tangent_length,
            float(tangent_dir.y) / tangent_length,
            float(tangent_dir.z) / tangent_length,
        )

        return {
            "circle": circle,
            "center_model": center_model,
            "axis_foot": axis_foot,
            "profile_radius": profile_radius,
            "profile_diameter": profile_radius * 2.0,
            "centerline_radius": centerline_radius,
            "u_dir": radial_dir,
            "v_dir": tangent_dir,
        }

    def _create_temp_sketch_from_basis(self, origin_point, u_dir, v_dir):
        origin_world = self._point3d_to_list(origin_point)
        target_x_world = self._normalize_vector3(self._vector3d_to_list(u_dir))
        target_y_world = self._normalize_vector3(self._vector3d_to_list(v_dir))

        base_plane_type, base_offset, base_normal = self._choose_helper_base_plane(
            origin_world,
            target_y_world,
        )
        base_plane = self.create_plane_by_base(base_plane_type, base_offset)
        base_sketch = self.sketches_class.add(base_plane)
        base_attitude = self._standard_plane_attitude(base_plane_type, base_offset)

        stage1_normal = self._normalize_vector3(
            self._cross_product3(target_y_world, base_normal)
        )
        stage1_u_world = self._normalize_vector3(
            self._cross_product3(base_normal, stage1_normal)
        )
        stage2_u_world = self._normalize_vector3(
            self._cross_product3(stage1_normal, target_y_world)
        )

        base_origin_uv = self._world_point_to_standard_sketch_uv(
            base_plane_type, origin_world
        )
        base_stage1_delta_uv = self._world_vector_to_standard_sketch_uv(
            base_plane_type, stage1_u_world
        )
        stage1_target_uv = [
            float(base_origin_uv[0]) + float(base_stage1_delta_uv[0]),
            float(base_origin_uv[1]) + float(base_stage1_delta_uv[1]),
        ]
        stage1_sketch, stage1_attitude = self._build_reference_edge_sketch(
            ref_sketch=base_sketch,
            p1_uv=base_origin_uv,
            p2_uv=stage1_target_uv,
            op=0.0,
            to=1.0,
            ref_attitude=base_attitude,
        )

        stage2_u_local = [
            self._dot_product3(stage2_u_world, stage1_u_world),
            self._dot_product3(stage2_u_world, base_normal),
        ]
        stage2_sketch, stage2_attitude = self._build_reference_edge_sketch(
            ref_sketch=stage1_sketch,
            p1_uv=[0.0, 0.0],
            p2_uv=stage2_u_local,
            op=0.0,
            to=1.0,
            ref_attitude=stage1_attitude,
        )

        target_x_local = [
            self._dot_product3(target_x_world, stage2_u_world),
            self._dot_product3(target_x_world, stage1_normal),
        ]
        return self._build_reference_edge_sketch(
            ref_sketch=stage2_sketch,
            p1_uv=[0.0, 0.0],
            p2_uv=target_x_local,
            op=0.0,
            to=1.0,
            ref_attitude=stage2_attitude,
        )

    def _create_helix_coil_from_profile(
        self,
        circle_profile,
        pitch,
        turns,
        handed,
        canonical_operation,
        pre_target_bodies,
        target_body_index=None,
    ):
        temp_sketch, _temp_attitude = self._create_temp_sketch_from_basis(
            circle_profile["axis_foot"],
            circle_profile["u_dir"],
            circle_profile["v_dir"],
        )
        temp_circle = temp_sketch.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(0.0, 0.0, 0.0),
            float(circle_profile["centerline_radius"]),
        )

        active_selections = self.ui.activeSelections
        pre_body_count = self.rootComp.bRepBodies.count
        coil_features = getattr(self.rootComp.features, "coilFeatures", None)
        try:
            pre_coil_count = (
                int(coil_features.count) if coil_features is not None else 0
            )
        except Exception:
            pre_coil_count = 0

        try:
            active_selections.clear()
            active_selections.add(temp_circle)
            self.app.executeTextCommand("Commands.Start Coil")
            maybe_do_events(force=True)
            self.app.executeTextCommand(
                "Commands.SetString infoBooleanType infoNewBodyType"
            )
            self.app.executeTextCommand(
                "Commands.SetString infoSectionType infoCircular"
            )
            self.app.executeTextCommand(
                "Commands.SetString infoSectionPosition infoOnCenter"
            )
            self.app.executeTextCommand(
                "Commands.SetString infoSizeType infoRevolutionAndPitch"
            )
            self.app.executeTextCommand(
                f"Commands.SetDouble CoilRevolutions {float(turns)}"
            )
            self.app.executeTextCommand(f"Commands.SetDouble CoilPitch {float(pitch)}")
            self.app.executeTextCommand(
                f"Commands.SetDouble SectionSize {float(circle_profile['profile_diameter'])}"
            )
            self.app.executeTextCommand(
                f"Commands.SetBool CoilFlipRotation {1 if handed == 'left' else 0}"
            )
            self.app.executeTextCommand("NuCommands.CommitCmd")
            maybe_do_events(force=True)
        except Exception:
            try:
                self.app.executeTextCommand("NuCommands.CancelCmd")
            except Exception:
                pass
            raise
        finally:
            try:
                active_selections.clear()
            except Exception:
                pass

        new_body = self._extract_new_body(pre_body_count, "coil")
        if canonical_operation != "NewBody":
            target_body = self._resolve_target_body(
                pre_target_bodies, target_body_index
            )
            try:
                self._combine_body(target_body, new_body, canonical_operation)
            except Exception:
                self._delete_body_if_possible(new_body)
                raise

        if coil_features is not None:
            try:
                if int(coil_features.count) > pre_coil_count:
                    return coil_features.item(int(coil_features.count) - 1)
            except Exception:
                pass
        return new_body

    def _recommended_helix_chunk_turns(self, pitch, diameter):
        pitch_value = abs(float(pitch))
        diameter_value = abs(float(diameter))
        if diameter_value <= 1e-8:
            return 0.5
        return max(0.125, min(0.4, pitch_value / (2.0 * diameter_value)))

    def _helix_centerline_point(
        self, circle_profile, axis_vector, signed_pitch, turn_value
    ):
        angle = 2.0 * math.pi * float(turn_value)
        cos_angle = math.cos(angle)
        sin_angle = math.sin(angle)
        axis_offset = float(signed_pitch) * float(turn_value)
        radius = float(circle_profile["centerline_radius"])
        axis_foot = circle_profile["axis_foot"]
        u_dir = circle_profile["u_dir"]
        v_dir = circle_profile["v_dir"]
        return adsk.core.Point3D.create(
            float(axis_foot.x)
            + float(axis_vector.x) * axis_offset
            + radius * (float(u_dir.x) * cos_angle + float(v_dir.x) * sin_angle),
            float(axis_foot.y)
            + float(axis_vector.y) * axis_offset
            + radius * (float(u_dir.y) * cos_angle + float(v_dir.y) * sin_angle),
            float(axis_foot.z)
            + float(axis_vector.z) * axis_offset
            + radius * (float(u_dir.z) * cos_angle + float(v_dir.z) * sin_angle),
        )

    def _create_chunked_helix_pipe_from_profile(
        self,
        circle_profile,
        axis_point,
        axis_vector,
        pitch,
        turns,
        handed,
        canonical_operation,
        pre_target_bodies,
        target_body_index=None,
    ):
        diameter = float(circle_profile["profile_diameter"])
        total_turns = float(turns)
        if total_turns <= 0.0:
            raise ValueError(f"turns must be > 0, got: {turns}")

        signed_pitch = float(pitch) if handed == "right" else -float(pitch)
        default_chunk_turns = min(
            total_turns,
            self._recommended_helix_chunk_turns(pitch, diameter),
        )
        min_chunk_turns = min(default_chunk_turns, 0.0625)
        temp_brep = adsk.fusion.TemporaryBRepManager.get()

        helix_body = None
        last_feature = None
        start_turn = 0.0
        chunk_turns = default_chunk_turns

        while start_turn < total_turns - 1e-9:
            remaining_turns = total_turns - start_turn
            current_chunk_turns = min(chunk_turns, remaining_turns)
            overlap_turns = (
                0.0 if helix_body is None else min(0.02, current_chunk_turns * 0.1)
            )
            segment_start_turn = max(0.0, start_turn - overlap_turns)
            segment_turns = min(
                current_chunk_turns + overlap_turns,
                total_turns - segment_start_turn,
            )
            new_body = None

            try:
                start_point = self._helix_centerline_point(
                    circle_profile,
                    axis_vector,
                    signed_pitch,
                    segment_start_turn,
                )
                helix_axis_point = self._project_point_onto_axis(
                    start_point, axis_point, axis_vector
                )
                helix_wire = temp_brep.createHelixWire(
                    helix_axis_point,
                    axis_vector,
                    start_point,
                    signed_pitch,
                    segment_turns,
                    0.0,
                )
                if helix_wire is None or helix_wire.edges.count == 0:
                    raise RuntimeError("Failed to create chunk helix wire.")

                path = self._create_path_from_helix_wire(helix_wire)
                pre_count = self.rootComp.bRepBodies.count
                feature = self._create_pipe_from_path(
                    path,
                    diameter,
                    adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
                )
                new_body = self._extract_new_body(pre_count, "chunked helix pipe")
                if helix_body is None:
                    helix_body = new_body
                else:
                    self._combine_body(helix_body, new_body, "Join")
                last_feature = feature
                start_turn += current_chunk_turns
            except Exception as ex:
                if new_body is not None and new_body is not helix_body:
                    self._delete_body_if_possible(new_body)
                if current_chunk_turns <= min_chunk_turns + 1e-9:
                    raise RuntimeError(
                        "Chunked helix pipe fallback failed "
                        f"near turn={start_turn:.4f} with chunk_turns={current_chunk_turns:.4f}: {ex}"
                    )
                chunk_turns = max(min_chunk_turns, current_chunk_turns * 0.5)

        if helix_body is None:
            raise RuntimeError("Chunked helix pipe fallback did not create a body.")

        if canonical_operation != "NewBody":
            target_body = self._resolve_target_body(
                pre_target_bodies, target_body_index
            )
            try:
                return self._combine_body(target_body, helix_body, canonical_operation)
            except Exception:
                self._delete_body_if_possible(helix_body)
                raise

        return last_feature if last_feature is not None else helix_body

    def create_extrude(
        self,
        distance,
        sketch_num=-1,
        operation="new",
        profile_point=None,
        profile_mode=None,
        opposite_distance=None,
        target_body_index=None,
    ):
        sketch, sketch_index = self._get_sketch(sketch_num)
        sketch_profiles, selected_profiles, prefer_collection = (
            self._resolve_extrude_profile_inputs(
                sketch,
                profile_point=profile_point,
                profile_mode=profile_mode,
            )
        )
        aligned_distance = self._align_extrude_distance_with_sketch_attitude(
            sketch,
            distance,
            sketch_index=sketch_index,
        )
        aligned_opposite_distance = None
        if opposite_distance is not None:
            raw_opposite_distance = float(opposite_distance)
            if abs(raw_opposite_distance) > 1e-9:
                aligned_opposite_distance = (
                    self._align_extrude_distance_with_sketch_attitude(
                        sketch,
                        -raw_opposite_distance,
                        sketch_index=sketch_index,
                    )
                )

        canonical_operation = self._normalize_operation(operation)
        feature_operation = self._feature_operation_enum(canonical_operation)
        profile_selection = self._profile_selection_from_list(
            selected_profiles,
            prefer_collection=prefer_collection,
        )

        if (
            aligned_opposite_distance is not None
            and abs(aligned_opposite_distance) > 1e-9
        ):
            target_body = None
            if canonical_operation != "NewBody":
                pre_bodies = [
                    self.rootComp.bRepBodies.item(i)
                    for i in range(self.rootComp.bRepBodies.count)
                ]
                target_body = self._resolve_target_body(pre_bodies, target_body_index)
            feature, tool_body = self._build_bidirectional_extrude_tool_body(
                sketch,
                aligned_distance,
                aligned_opposite_distance,
                profile_point=profile_point,
                profile_mode=profile_mode,
            )
            if canonical_operation == "NewBody":
                return feature
            try:
                return self._combine_body(target_body, tool_body, canonical_operation)
            except Exception:
                self._delete_body_if_possible(tool_body)
                raise

        attempt_errors = []
        original_error = None
        last_error = None
        retryable_failure = False

        if (
            canonical_operation == "NewBody"
            and profile_point is None
            and self._profiles_need_sequential_new_body_union(selected_profiles)
        ):
            try:
                return self._extrude_new_body_per_profile_union(
                    selected_profiles,
                    aligned_distance,
                )
            except Exception as ex:
                original_error = ex
                last_error = ex
                attempt_errors.append(f"selected:per_profile_new_body:{ex}")

        try:
            return self._add_extrude_feature(
                profile_selection,
                feature_operation,
                aligned_distance,
            )
        except Exception as ex:
            original_error = ex
            last_error = ex
            attempt_errors.append(f"selected:direct:{ex}")
            if self._extrude_add_failed_due_to_missing_target(ex):
                raise
            if not self._extrude_add_failed_due_to_boolean_geometry(ex):
                raise
            retryable_failure = True

        if retryable_failure and canonical_operation != "NewBody":
            try:
                return self._extrude_via_new_body_combine(
                    selected_profiles,
                    canonical_operation,
                    aligned_distance,
                )
            except Exception as ex:
                last_error = ex
                attempt_errors.append(f"selected:new_body_combine:{ex}")

            if profile_point is None and len(selected_profiles) > 1:
                try:
                    return self._extrude_boolean_per_profile(
                        selected_profiles,
                        canonical_operation,
                        aligned_distance,
                    )
                except Exception as ex:
                    last_error = ex
                    attempt_errors.append(f"per_profile:new_body_combine:{ex}")

        attempts_tail = "; ".join(attempt_errors[-6:])
        raise RuntimeError(
            f"Failed to create extrude after fallback attempts. "
            f"original={original_error}; last={last_error}; attempts_tail={attempts_tail}"
        )

    def revolve(
        self,
        axisPoint1=None,
        axisPoint2=None,
        angle=2 * math.pi,
        sketch_num=-1,
        operation="new",
        axis=None,
        start=None,
        end=None,
        target_body_index=None,
    ):
        sketch, sketch_index = self._get_sketch(sketch_num)
        axis_point_1, axis_point_2 = self._resolve_revolve_axis_points(
            axis,
            axisPoint1,
            axisPoint2,
            sketch=sketch,
            sketch_index=sketch_index,
        )
        profile_sketch, _selected_profile = self._select_revolve_profile_sketch(sketch)
        axis_entities = self._axis_entities_for_revolve(
            profile_sketch, axis_point_1, axis_point_2
        )
        # Axis helper creation can add sketch points / construction lines and
        # invalidate previously captured Profile handles, so always resolve the
        # profile after axis entities are finalized.
        selected_profile = self._select_profile(profile_sketch)
        rotation_axis_entity = axis_entities[0][1]

        # ???????
        # start=0 ?????? operation ????
        # start!=0 ???? NewBody????? start????????
        if start is None and end is None:
            start_rad = 0.0
            sweep_rad = float(angle)
        else:
            start_deg = 0.0 if start is None else float(start)
            end_deg = (
                (start_deg + math.degrees(float(angle))) if end is None else float(end)
            )
            start_rad = math.radians(start_deg)
            sweep_rad = math.radians(end_deg - start_deg)

        if abs(sweep_rad) <= 1e-9:
            raise ValueError("revolve sweep angle is too small.")

        canonical_operation = self._normalize_operation(operation)

        if abs(start_rad) <= 1e-9:
            try:
                return self._add_revolve_with_fallback_profiles(
                    selected_profile,
                    axis_entities,
                    self._feature_operation_enum(canonical_operation),
                    sweep_rad,
                )
            except Exception as ex:
                if not self._is_invalid_revolve_profile_error(
                    ex
                ) or not self._source_line_on_revolve_axis(
                    sketch, axis_point_1, axis_point_2
                ):
                    raise

                profile_point = None
                try:
                    reference_profile = self._primary_profile(
                        selected_profile, profile_sketch
                    )
                    reference_point = self._profile_reference_point(reference_profile)
                    profile_point = [
                        float(reference_point.x),
                        float(reference_point.y),
                        float(reference_point.z),
                    ]
                except Exception:
                    pass

                relief_error = ex
                if self._is_full_revolve_sweep(sweep_rad):
                    try:
                        return self._try_axis_touching_full_revolve_primitive_fallback(
                            profile_sketch,
                            axis_point_1,
                            axis_point_2,
                            canonical_operation,
                            target_body_index=target_body_index,
                        )
                    except Exception as primitive_ex:
                        relief_error = primitive_ex

                segmented_attempts = [
                    (selected_profile, axis_entities, rotation_axis_entity),
                ]
                for relief in (1e-2, 5e-2, 1e-1, 2.5e-1, 5e-1, 1.0):
                    relief_sketch = self._build_axis_relief_profile_helper_sketch(
                        sketch,
                        axis_point_1,
                        axis_point_2,
                        relief=relief,
                    )
                    if relief_sketch is None:
                        continue

                    try:
                        relief_profile = self._select_profile(
                            relief_sketch,
                            profile_point=profile_point,
                        )
                    except Exception:
                        relief_profile = self._select_profile(relief_sketch)

                    relief_axis_entities = self._axis_entities_for_revolve(
                        relief_sketch,
                        axis_point_1,
                        axis_point_2,
                    )
                    segmented_attempts.append(
                        (
                            relief_profile,
                            relief_axis_entities,
                            relief_axis_entities[0][1],
                        )
                    )
                    try:
                        return self._add_revolve_with_fallback_profiles(
                            relief_profile,
                            relief_axis_entities,
                            self._feature_operation_enum(canonical_operation),
                            sweep_rad,
                        )
                    except Exception as relief_ex:
                        relief_error = relief_ex
                        if not self._is_invalid_revolve_profile_error(relief_ex):
                            raise

                if self._is_full_revolve_sweep(sweep_rad):
                    for (
                        segment_profile,
                        segment_axis_entities,
                        segment_rotation_axis,
                    ) in segmented_attempts:
                        try:
                            return self._try_segmented_full_revolve(
                                segment_profile,
                                segment_axis_entities,
                                segment_rotation_axis,
                                canonical_operation,
                                sweep_rad,
                                target_body_index=target_body_index,
                            )
                        except Exception as segment_ex:
                            relief_error = segment_ex
                            if not self._is_invalid_revolve_profile_error(segment_ex):
                                raise

                raise relief_error

        pre_count = self.rootComp.bRepBodies.count
        pre_bodies = [self.rootComp.bRepBodies.item(i) for i in range(pre_count)]

        revolve_feature = self._add_revolve_with_fallback_profiles(
            selected_profile,
            axis_entities,
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
            sweep_rad,
        )
        new_body = self._extract_new_body(pre_count, "revolve")
        self._rotate_body_around_axis(new_body, rotation_axis_entity, start_rad)

        if canonical_operation == "NewBody":
            return revolve_feature

        target_body = self._resolve_target_body(pre_bodies, target_body_index)
        return self._combine_body(target_body, new_body, canonical_operation)

    def create_helix_sweep(
        self,
        axis,
        pitch,
        turns,
        handedness="Right",
        sketch_num=-1,
        operation="NewBody",
        target_body_index=None,
    ):
        sketch, _ = self._get_sketch(sketch_num)
        selected_profile = self._select_profile(sketch)

        if not isinstance(axis, list) or len(axis) != 2:
            raise ValueError("axis must be [[x,y,z],[dx,dy,dz]].")

        axis_point = self._to_point3d(axis[0])
        axis_vector = self._to_vector3d(axis[1], normalize=True)

        pitch = float(pitch)
        turns = float(turns)
        if pitch <= 0:
            raise ValueError(f"pitch must be > 0, got: {pitch}")
        if turns <= 0:
            raise ValueError(f"turns must be > 0, got: {turns}")

        handed = str(handedness).strip().lower()
        if handed not in {"right", "left"}:
            raise ValueError(f"handedness must be Right/Left, got: {handedness}")

        # createHelixWire ?? turns ??????? pitch ????????
        signed_pitch = pitch if handed == "right" else -pitch
        signed_turns = turns
        canonical_operation = self._normalize_operation(operation)
        pre_target_bodies = [
            self.rootComp.bRepBodies.item(i)
            for i in range(self.rootComp.bRepBodies.count)
        ]
        selected_profiles = self._profile_list_from_selection(selected_profile)

        circular_profile = self._infer_circular_helix_profile(
            sketch, axis_point, axis_vector
        )
        if circular_profile is not None:
            try:
                return self._create_helix_coil_from_profile(
                    circular_profile,
                    pitch,
                    turns,
                    handed,
                    canonical_operation,
                    pre_target_bodies,
                    target_body_index=target_body_index,
                )
            except Exception:
                return self._create_chunked_helix_pipe_from_profile(
                    circular_profile,
                    axis_point,
                    axis_vector,
                    pitch,
                    turns,
                    handed,
                    canonical_operation,
                    pre_target_bodies,
                    target_body_index=target_body_index,
                )

        if (
            self._axial_helix_profile_attitude(sketch, axis_point, axis_vector)
            is not None
        ):
            if len(selected_profiles) > 1:
                return self._create_multi_profile_axial_helix_loft(
                    sketch,
                    selected_profile,
                    axis_point,
                    axis_vector,
                    pitch,
                    turns,
                    handed,
                    canonical_operation,
                    pre_target_bodies,
                    target_body_index=target_body_index,
                )
            return self._create_axial_profile_helix_loft(
                sketch,
                selected_profile,
                axis_point,
                axis_vector,
                pitch,
                turns,
                handed,
                canonical_operation,
                pre_target_bodies,
                target_body_index=target_body_index,
            )

        profile_ref = self._primary_profile(selected_profile, sketch)
        start_point = self._infer_helix_start_point(
            profile_ref, axis_point, axis_vector
        )
        helix_axis_point = self._project_point_onto_axis(
            start_point, axis_point, axis_vector
        )

        temp_brep = adsk.fusion.TemporaryBRepManager.get()
        helix_wire = temp_brep.createHelixWire(
            helix_axis_point,
            axis_vector,
            start_point,
            signed_pitch,
            signed_turns,
            0.0,
        )
        if helix_wire is None or helix_wire.edges.count == 0:
            raise RuntimeError("Failed to create helix wire.")

        path = self._create_path_from_helix_wire(helix_wire)

        pre_feature_count = self.rootComp.bRepBodies.count
        try:
            sweep_input = self.rootComp.features.sweepFeatures.createInput(
                selected_profile,
                path,
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
            )
            sweep_input.orientation = (
                adsk.fusion.SweepOrientationTypes.PerpendicularOrientationType
            )
            feature = self.rootComp.features.sweepFeatures.add(sweep_input)
        except Exception:
            diameter = self._infer_profile_diameter(profile_ref)
            feature = self._create_pipe_from_path(path, diameter)

        new_body = self._extract_new_body(pre_feature_count, "helix sweep")
        if canonical_operation == "NewBody":
            return feature

        target_body = self._resolve_target_body(pre_target_bodies, target_body_index)
        return self._combine_body(target_body, new_body, canonical_operation)

    def _body_pick_tolerance(self, body, default_value=0.5):
        try:
            bbox = body.boundingBox
            dx = float(bbox.maxPoint.x - bbox.minPoint.x)
            dy = float(bbox.maxPoint.y - bbox.minPoint.y)
            dz = float(bbox.maxPoint.z - bbox.minPoint.z)
            diag = math.sqrt(dx * dx + dy * dy + dz * dz)
            return max(
                0.05, min(2.0, diag * 0.02 if diag > 0 else float(default_value))
            )
        except Exception:
            return float(default_value)

    def _edge_sample_points(self, edge):
        points = []
        for attr in ("startVertex", "endVertex"):
            vertex = getattr(edge, attr, None)
            geometry = getattr(vertex, "geometry", None)
            if geometry is not None:
                points.append(geometry)
        try:
            evaluator = edge.geometry.evaluator
            success, min_param, max_param = evaluator.getParameterExtents()
            if success:
                for param in (min_param, (min_param + max_param) * 0.5, max_param):
                    success_point, sample_point = evaluator.getPointAtParameter(param)
                    if success_point:
                        points.append(sample_point)
        except Exception:
            pass
        return points

    def _vector3_tuple(self, value):
        return float(value.x), float(value.y), float(value.z)

    def _normalize_direction(self, direction):
        if direction is None:
            return None
        if len(direction) != 3:
            raise ValueError(f"Expected a 3D direction, got: {direction}")
        dx = float(direction[0])
        dy = float(direction[1])
        dz = float(direction[2])
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length <= 1e-12:
            raise ValueError(f"Direction vector must be non-zero, got: {direction}")
        return (dx / length, dy / length, dz / length)

    def _dot_product(self, left, right):
        return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]

    def _edge_midpoint(self, edge):
        try:
            evaluator = edge.geometry.evaluator
            success, min_param, max_param = evaluator.getParameterExtents()
            if success:
                success_point, midpoint = evaluator.getPointAtParameter(
                    (min_param + max_param) * 0.5
                )
                if success_point:
                    return midpoint
        except Exception:
            pass

        start_geometry = getattr(getattr(edge, "startVertex", None), "geometry", None)
        end_geometry = getattr(getattr(edge, "endVertex", None), "geometry", None)
        if start_geometry is not None and end_geometry is not None:
            return adsk.core.Point3D.create(
                (start_geometry.x + end_geometry.x) * 0.5,
                (start_geometry.y + end_geometry.y) * 0.5,
                (start_geometry.z + end_geometry.z) * 0.5,
            )
        return None

    def _face_normal_alignment(self, edge, plane_normal):
        if plane_normal is None:
            return None

        best_alignment = None
        try:
            faces = edge.faces
        except Exception:
            return None

        for index in range(faces.count):
            face = faces.item(index)
            try:
                success, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
                if not success:
                    continue
                alignment = self._dot_product(
                    self._normalize_direction(self._vector3_tuple(normal)),
                    plane_normal,
                )
                if best_alignment is None or alignment > best_alignment:
                    best_alignment = alignment
            except Exception:
                continue
        return best_alignment

    def _normalize_feature_points(self, point=None, points=None):
        if points is None:
            if point is None:
                raise ValueError("point(s) are required")
            points = [point]
        if not isinstance(points, list) or not points:
            raise ValueError(f"points must be a non-empty list, got: {points}")
        return [self._to_point3d(item) for item in points]

    def _normalize_feature_values(
        self, count, value=None, values=None, field_name="value"
    ):
        if values is None:
            if value is None:
                raise ValueError(f"{field_name}(s) are required")
            values = [value] * count
        if not isinstance(values, list) or len(values) != count:
            raise ValueError(f"{field_name}s length must match points length")
        return [float(item) for item in values]

    def _normalize_optional_feature_values(
        self, count, value=None, values=None, field_name="value"
    ):
        if values is None:
            if value is None:
                return [None] * count
            values = [value] * count
        if not isinstance(values, list) or len(values) != count:
            raise ValueError(f"{field_name}s length must match points length")
        return [None if item is None else float(item) for item in values]

    def _normalize_optional_planes(self, count, plane=None, planes=None):
        if planes is None:
            if plane is None:
                return [None] * count
            planes = [plane] * count
        if not isinstance(planes, list) or len(planes) != count:
            raise ValueError("planes length must match points length")
        normalized_planes = []
        for item in planes:
            if item is None:
                normalized_planes.append(None)
                continue
            try:
                normalized_planes.append(self._normalize_direction(item))
            except ValueError as ex:
                if "Direction vector must be non-zero" not in str(ex):
                    raise
                # A zero plane-normal provides no usable disambiguation; fall
                # back to the default edge search instead of failing the whole
                # chamfer operation.
                normalized_planes.append(None)
        return normalized_planes

    def _collect_target_edges(self, body, point_objects, plane_normals=None):
        if plane_normals is None:
            plane_normals = [None] * len(point_objects)
        target_edges = []
        for index, (point_3d, plane_normal) in enumerate(
            zip(point_objects, plane_normals), start=1
        ):
            target_edge = self._find_edge_near_point(
                body,
                point_3d,
                plane_normal=plane_normal,
                allow_global_fallback=True,
            )
            if target_edge is None:
                continue
            target_edges.append(target_edge)
        return target_edges

    def _find_edge_near_point(
        self,
        body,
        point_3D,
        exact_tolerance=1e-3,
        fallback_tolerance=None,
        plane_normal=None,
        allow_global_fallback=False,
    ):
        edges = body.edges
        if fallback_tolerance is None:
            fallback_tolerance = self._body_pick_tolerance(body)
        candidates = []
        global_candidates = []

        for i in range(edges.count):
            edge = edges.item(i)
            midpoint = self._edge_midpoint(edge)
            midpoint_distance = (
                point_3D.distanceTo(midpoint) if midpoint is not None else None
            )
            closest_distance = midpoint_distance
            try:
                evaluator = edge.geometry.evaluator
                success, param = evaluator.getParameterAtPoint(point_3D)
                if success:
                    success_extents, min_param, max_param = (
                        evaluator.getParameterExtents()
                    )
                    if success_extents and min_param <= param <= max_param:
                        success_point, closest_pt = evaluator.getPointAtParameter(param)
                        if success_point:
                            point_distance = point_3D.distanceTo(closest_pt)
                            if (
                                closest_distance is None
                                or point_distance < closest_distance
                            ):
                                closest_distance = point_distance
            except Exception:
                pass

            for sample_point in self._edge_sample_points(edge):
                point_distance = point_3D.distanceTo(sample_point)
                if closest_distance is None or point_distance < closest_distance:
                    closest_distance = point_distance

            if midpoint_distance is None and closest_distance is not None:
                midpoint_distance = closest_distance
            if midpoint_distance is None or closest_distance is None:
                continue

            candidate = {
                "edge": edge,
                "midpoint_distance": midpoint_distance,
                "closest_distance": closest_distance,
                "plane_alignment": self._face_normal_alignment(edge, plane_normal),
            }
            global_candidates.append(candidate)

            if min(midpoint_distance, closest_distance) <= fallback_tolerance:
                candidates.append(candidate)

        # if not candidates:
        #     if not allow_global_fallback:
        #         return None
        #     candidates = global_candidates

        if not candidates:
            return None

        if plane_normal is not None:
            nearest_midpoint = min(
                candidate["midpoint_distance"] for candidate in candidates
            )
            midpoint_window = max(exact_tolerance * 10.0, fallback_tolerance * 0.25)
            nearby_candidates = [
                candidate
                for candidate in candidates
                if candidate["midpoint_distance"] <= nearest_midpoint + midpoint_window
            ]

            def plane_sort_key(candidate):
                alignment = candidate["plane_alignment"]
                if alignment is None:
                    alignment = -2.0
                return (
                    -alignment,
                    candidate["midpoint_distance"],
                    candidate["closest_distance"],
                )

            return min(nearby_candidates, key=plane_sort_key)["edge"]

        return min(
            candidates,
            key=lambda candidate: (
                candidate["midpoint_distance"],
                candidate["closest_distance"],
            ),
        )["edge"]

    def create_fillet(self, point=None, radius=None, points=None, radii=None):
        point_objects = self._normalize_feature_points(point=point, points=points)
        radii = self._normalize_feature_values(
            len(point_objects), value=radius, values=radii, field_name="radius"
        )
        body = self.rootComp.bRepBodies.item(0)
        target_edges = self._collect_target_edges(body, point_objects)
        if not target_edges:
            raise RuntimeError("No edge found at given point for edge feature.")

        fillets = self.rootComp.features.filletFeatures
        fillet_input = fillets.createInput()
        grouped_edges = {}
        grouped_tokens = {}
        for edge, edge_radius in zip(target_edges, radii):
            edge_token = None
            try:
                edge_token = edge.entityToken
            except Exception:
                edge_token = None
            token_key = edge_token if edge_token is not None else id(edge)
            grouped_edges.setdefault(edge_radius, [])
            grouped_tokens.setdefault(edge_radius, set())
            if token_key in grouped_tokens[edge_radius]:
                continue
            grouped_tokens[edge_radius].add(token_key)
            grouped_edges[edge_radius].append(edge)

        for edge_radius, edges in grouped_edges.items():
            edge_set = adsk.core.ObjectCollection.create()
            for edge in edges:
                edge_set.add(edge)
            fillet_input.addConstantRadiusEdgeSet(
                edge_set,
                adsk.core.ValueInput.createByReal(edge_radius),
                True,
            )
        return fillets.add(fillet_input)

    def create_chamfer(
        self,
        point=None,
        distance=None,
        angle=None,
        plane=None,
        points=None,
        distances=None,
        angles=None,
        planes=None,
    ):
        point_objects = self._normalize_feature_points(point=point, points=points)
        distance_values = self._normalize_feature_values(
            len(point_objects),
            value=distance,
            values=distances,
            field_name="distance",
        )
        angle_values = self._normalize_optional_feature_values(
            len(point_objects),
            value=angle,
            values=angles,
            field_name="angle",
        )
        plane_normals = self._normalize_optional_planes(
            len(point_objects),
            plane=plane,
            planes=planes,
        )
        body = self.rootComp.bRepBodies.item(0)
        target_edges = self._collect_target_edges(
            body, point_objects, plane_normals=plane_normals
        )
        if not target_edges:
            raise RuntimeError("No edge found at given point for edge feature.")
        grouped_edges = {}
        grouped_tokens = {}
        for edge, edge_distance, edge_angle in zip(
            target_edges,
            distance_values,
            angle_values,
        ):
            group_key = (edge_distance, edge_angle)
            edge_token = None
            try:
                edge_token = edge.entityToken
            except Exception:
                edge_token = None
            token_key = edge_token if edge_token is not None else id(edge)
            grouped_edges.setdefault(group_key, [])
            grouped_tokens.setdefault(group_key, set())
            if token_key in grouped_tokens[group_key]:
                continue
            grouped_tokens[group_key].add(token_key)
            grouped_edges[group_key].append(edge)

        last_feature = None
        chamfers = self.rootComp.features.chamferFeatures
        for (edge_distance, edge_angle), edges in grouped_edges.items():
            edge_set = adsk.core.ObjectCollection.create()
            for edge in edges:
                edge_set.add(edge)
            if edge_angle is None:
                chamfer_input = chamfers.createInput(edge_set, True)
                distance_value = adsk.core.ValueInput.createByReal(edge_distance)
                chamfer_input.setToEqualDistance(distance_value)
                last_feature = chamfers.add(chamfer_input)
                continue

            last_error = None
            attempt_errors = []
            for angle_mode, angle_input in self._iter_chamfer_angle_inputs(edge_angle):
                try:
                    chamfer_input = chamfers.createInput(edge_set, True)
                    distance_value = adsk.core.ValueInput.createByReal(edge_distance)
                    chamfer_input.setToDistanceAndAngle(distance_value, angle_input)
                    last_feature = chamfers.add(chamfer_input)
                    break
                except Exception as ex:
                    last_error = ex
                    attempt_errors.append(f"mode={angle_mode}, err={ex}")
            else:
                attempts_tail = "; ".join(attempt_errors[-4:])
                raise RuntimeError(
                    "Failed to create chamfer feature: "
                    f"{last_error}; angle={edge_angle}; attempts_tail={attempts_tail}"
                )

        return last_feature

    def calculate_iou(self, step_file1, step_file2):
        """Calculate IoU (intersection over union) for two STEP models.

        Returns:
            {
                "iou": float,
                "volume_intersection": float,
                "volume_union": float,
                "volume_body1": float,
                "volume_body2": float,
            }
        """
        opened_documents = []
        temp_brep = adsk.fusion.TemporaryBRepManager.get()
        if temp_brep is None:
            raise RuntimeError("TemporaryBRepManager is unavailable.")

        def _close_document(document):
            if document is None:
                return
            try:
                document.close(False)
            except Exception:
                pass

        def _close_opened_documents():
            for document in reversed(opened_documents):
                _close_document(document)
            opened_documents.clear()
            self._refresh_design_context(create_if_missing=True)

        def _body_volume(body):
            return self._body_volume(body)

        def _body_bbox(body):
            return self._body_bbox(body)

        def _body_signature(body):
            return self._body_signature(body)

        def _bbox_close(left, right, tol=1e-4):
            return self._bbox_close(left, right, tol=tol)

        def _vector_close(left, right, tol=1e-4):
            return self._vector_close(left, right, tol=tol)

        def _relative_close(left, right, tol=1e-5, abs_tol=1e-8):
            return self._relative_close(left, right, tol=tol, abs_tol=abs_tol)

        def _signatures_are_nearly_identical(left, right):
            return self._body_signatures_are_nearly_identical(left, right)

        def _copy_body(body, context):
            copied = temp_brep.copy(body)
            if copied is None:
                raise RuntimeError(f"Failed to copy {context} into temporary BRep.")
            return copied

        def _merge_temp_bodies(temp_bodies):
            if not temp_bodies:
                raise RuntimeError(
                    "No temporary bodies are available for boolean merging."
                )

            merged = list(temp_bodies)
            changed = True
            while changed and len(merged) > 1:
                changed = False
                next_merged = []
                used = [False] * len(merged)
                for body_index, body in enumerate(merged):
                    if used[body_index]:
                        continue
                    current = body
                    used[body_index] = True
                    retry = True
                    while retry:
                        retry = False
                        for other_index, other in enumerate(merged):
                            if used[other_index]:
                                continue
                            candidate = _copy_body(current, "temporary union candidate")
                            if temp_brep.booleanOperation(
                                candidate,
                                other,
                                adsk.fusion.BooleanTypes.UnionBooleanType,
                            ):
                                current = candidate
                                used[other_index] = True
                                retry = True
                                changed = True
                    next_merged.append(current)
                merged = next_merged
            return merged

        def _bounding_boxes_overlap(body_one, body_two, tolerance=1e-6):
            return self._bounding_boxes_overlap(body_one, body_two, tolerance=tolerance)

        def _intersection_volume(body_one, body_two):
            if not _bounding_boxes_overlap(body_one, body_two):
                return 0.0

            volume_one = _body_volume(body_one)
            volume_two = _body_volume(body_two)

            attempt_pairs = (
                (body_one, body_two),
                (body_two, body_one),
            )
            last_error = None
            for target_source, tool_source in attempt_pairs:
                intersection_body = _copy_body(
                    target_source, "intersection target body"
                )
                tool_body = _copy_body(tool_source, "intersection tool body")
                try:
                    if not temp_brep.booleanOperation(
                        intersection_body,
                        tool_body,
                        adsk.fusion.BooleanTypes.IntersectionBooleanType,
                    ):
                        continue
                except Exception as exc:
                    last_error = exc
                    continue

                volume_piece = _body_volume(intersection_body)
                if volume_piece > 0.0:
                    return volume_piece
                break

            union_error = None
            for target_source, tool_source in attempt_pairs:
                union_body = _copy_body(target_source, "union target body")
                tool_body = _copy_body(tool_source, "union tool body")
                try:
                    if not temp_brep.booleanOperation(
                        union_body,
                        tool_body,
                        adsk.fusion.BooleanTypes.UnionBooleanType,
                    ):
                        continue
                except Exception as exc:
                    union_error = exc
                    continue

                union_volume = _body_volume(union_body)
                if union_volume <= 0.0:
                    continue
                inferred_intersection = volume_one + volume_two - union_volume
                max_intersection = min(volume_one, volume_two)
                if inferred_intersection > 0.0:
                    return max(0.0, min(max_intersection, inferred_intersection))

            if last_error is not None:
                try:
                    self.logger.log(
                        f"temporary intersection fallback to zero volume: {last_error}"
                    )
                except Exception:
                    pass
            if union_error is not None:
                try:
                    self.logger.log(
                        "temporary union fallback to zero inferred intersection: "
                        f"{union_error}"
                    )
                except Exception:
                    pass
            return 0.0

        def _load_step_temp_bodies(step_file):
            document, design = self.import_step_to_new_document(step_file)
            opened_documents.append(document)
            root_comp = design.rootComponent
            source_bodies = self._collect_imported_bodies_for_iou(root_comp)
            source_signatures = [_body_signature(body) for body in source_bodies]
            temp_bodies = [
                _copy_body(body, f"imported STEP body from {step_file}")
                for body in source_bodies
            ]
            return _merge_temp_bodies(temp_bodies), source_signatures

        try:
            step1_bodies, step1_source_signatures = _load_step_temp_bodies(step_file1)
            step2_bodies, step2_source_signatures = _load_step_temp_bodies(step_file2)

            volume_body1 = sum(_body_volume(body) for body in step1_bodies)
            volume_body2 = sum(_body_volume(body) for body in step2_bodies)

            volume_intersection = 0.0
            for body1 in step1_bodies:
                for body2 in step2_bodies:
                    volume_piece = _intersection_volume(body1, body2)
                    if volume_piece > 0.0:
                        volume_intersection += volume_piece

            if (
                volume_intersection <= 0.0
                and len(step1_bodies) == 1
                and len(step2_bodies) == 1
                and self._body_signature_lists_are_nearly_identical(
                    step1_source_signatures,
                    step2_source_signatures,
                )
            ):
                volume_intersection = min(volume_body1, volume_body2)

            volume_body1 = max(0.0, float(volume_body1))
            volume_body2 = max(0.0, float(volume_body2))
            max_intersection = min(volume_body1, volume_body2)
            if volume_intersection < 0.0:
                volume_intersection = 0.0
            elif volume_intersection > max_intersection:
                volume_intersection = max_intersection

            volume_union = volume_body1 + volume_body2 - volume_intersection
            volume_union = max(volume_union, max(volume_body1, volume_body2))
            iou = 0.0 if volume_union <= 0 else (volume_intersection / volume_union)
            iou = max(0.0, min(1.0, float(iou)))

            return {
                "iou": iou,
                "volume_intersection": volume_intersection,
                "volume_union": volume_union,
                "volume_body1": volume_body1,
                "volume_body2": volume_body2,
                "body_count1": len(step1_bodies),
                "body_count2": len(step2_bodies),
                "body1_bounding_boxes": [_body_bbox(body) for body in step1_bodies],
                "body2_bounding_boxes": [_body_bbox(body) for body in step2_bodies],
                "body1_signatures": [_body_signature(body) for body in step1_bodies],
                "body2_signatures": [_body_signature(body) for body in step2_bodies],
                "body1_source_signatures": step1_source_signatures,
                "body2_source_signatures": step2_source_signatures,
            }
        finally:
            _close_opened_documents()

    def calculate_assembly_collisions(self, items, tolerance=1e-6):
        """Calculate exact B-Rep intersection volumes for positioned STEP bodies."""
        if not isinstance(items, list):
            raise ValueError("items must be a list of assembly body records.")

        temp_brep = adsk.fusion.TemporaryBRepManager.get()
        if temp_brep is None:
            raise RuntimeError("TemporaryBRepManager is unavailable.")

        tolerance = float(tolerance)
        opened_documents = []

        def _close_document(document):
            if document is None:
                return
            try:
                document.close(False)
            except Exception:
                pass

        def _close_opened_documents():
            for document in reversed(opened_documents):
                _close_document(document)
            opened_documents.clear()
            self._refresh_design_context(create_if_missing=True)

        imported = []
        try:
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError(f"items[{index}] must be an object.")
                step_file = item.get("step_file") or item.get("path")
                if not step_file:
                    raise ValueError(f"items[{index}] has no step_file.")
                frame = item.get("frame") or {}
                document, design = self.import_step_to_new_document(step_file)
                opened_documents.append(document)
                source_bodies = self._collect_imported_bodies_for_iou(
                    design.rootComponent
                )
                bodies = []
                for body_index, body in enumerate(source_bodies):
                    copied = temp_brep.copy(body)
                    if copied is None:
                        raise RuntimeError(
                            f"Failed to copy STEP body {body_index} for assembly collision item {index}: {step_file}"
                        )
                    bodies.append(
                        self._transform_temp_body_by_frame(copied, frame, temp_brep)
                    )
                imported.append(
                    {
                        "instance": str(
                            item.get("instance")
                            or item.get("instance_id")
                            or f"item_{index}"
                        ),
                        "part": str(item.get("part") or item.get("part_id") or ""),
                        "step_file": str(step_file),
                        "bodies": bodies,
                    }
                )

            pairs = []
            for left_index, left in enumerate(imported):
                for right in imported[left_index + 1 :]:
                    volume = 0.0
                    for body_left in left["bodies"]:
                        for body_right in right["bodies"]:
                            volume += self._intersection_volume_for_bodies(
                                body_left,
                                body_right,
                                temp_brep,
                                tolerance=tolerance,
                            )
                    pairs.append(
                        {
                            "instance_a": left["instance"],
                            "part_a": left["part"],
                            "instance_b": right["instance"],
                            "part_b": right["part"],
                            "volume_intersection": float(volume),
                            "status": "overlap" if volume > tolerance else "ok",
                        }
                    )
            return {
                "method": "fusion360_temporary_brep_intersection",
                "tolerance": tolerance,
                "length_units": "cm",
                "volume_units": "cm^3",
                "item_count": len(imported),
                "pairs": pairs,
            }
        finally:
            _close_opened_documents()
