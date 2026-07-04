import math
import traceback
from pathlib import Path

import adsk.core
import adsk.fusion
import adsk.sim

from ..logger import Logger
from ..runtime import maybe_do_events


class CADBase:
    _STANDARD_SKETCH_BASIS = {
        "xy": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "xz": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
        "yz": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
    }
    _STANDARD_PLANE_OFFSET_AXIS = {
        "xy": 2,
        "xz": 1,
        "yz": 0,
    }
    _FAST_CLEAR_FEATURE_COLLECTIONS = (
        "filletFeatures",
        "chamferFeatures",
        "combineFeatures",
        "sweepFeatures",
        "revolveFeatures",
        "extrudeFeatures",
        "loftFeatures",
        "patchFeatures",
        "stitchFeatures",
        "thickenFeatures",
        "shellFeatures",
        "holeFeatures",
        "moveFeatures",
        "scaleFeatures",
        "splitBodyFeatures",
        "mirrorFeatures",
        "rectangularPatternFeatures",
        "circularPatternFeatures",
        "pathPatternFeatures",
        "threadFeatures",
        "webFeatures",
        "ribFeatures",
        "embossFeatures",
        "baseFeatures",
        "removeFeatures",
    )

    def __init__(self):
        self.ui = None
        try:
            self.logger = Logger()
            self._refresh_design_context(create_if_missing=True)
        except:
            if self.ui:
                self.ui.messageBox(f"Failed:\n{traceback.format_exc()}")

    def _create_empty_design_document(self):
        document_types = getattr(adsk.core, "DocumentTypes", None)
        if document_types is None:
            raise RuntimeError("Fusion API is missing adsk.core.DocumentTypes.")

        for attr_name in ("FusionDesignDocumentType", "FusionDocumentType"):
            doc_type = getattr(document_types, attr_name, None)
            if doc_type is None:
                continue
            self.app.documents.add(doc_type)
            maybe_do_events(force=True)
            return

        raise RuntimeError(
            "No Fusion design document type is available in adsk.core.DocumentTypes."
        )

    def _new_design_context(self):
        """Create a fresh Fusion design so native assembly export starts at root."""
        self._create_empty_design_document()
        self._refresh_design_context(create_if_missing=False)
        try:
            self.design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        except Exception:
            pass
        self._enable_native_assembly_context()
        try:
            self.design.activateRootComponent()
        except Exception:
            pass
        maybe_do_events(force=True)

    def _enable_native_assembly_context(self):
        """Prefer a Fusion design intent that can own child components."""
        if self.design is None:
            return {"ok": False, "error": "no active design"}
        report = {"ok": True, "attempts": []}
        intent_types = getattr(adsk.fusion, "DesignIntentTypes", None)
        if intent_types is not None:
            for attr_name in ("HybridDesignIntentType", "AssemblyDesignIntentType"):
                value = getattr(intent_types, attr_name, None)
                if value is None:
                    continue
                attempt = {"property": "designIntent", "value": attr_name}
                try:
                    self.design.designIntent = value
                    maybe_do_events(force=True)
                    attempt["ok"] = True
                    report["attempts"].append(attempt)
                    break
                except Exception as exc:
                    attempt["ok"] = False
                    attempt["error"] = str(exc)
                    report["attempts"].append(attempt)
        try:
            self.design.isModelingInAssemblyEnabled = True
            report["modeling_in_assembly_enabled"] = True
        except Exception as exc:
            report["modeling_in_assembly_enabled"] = False
            report["modeling_in_assembly_error"] = str(exc)
        try:
            self.design.activateRootComponent()
        except Exception:
            pass
        self._refresh_design_context(create_if_missing=False)
        return report

    def _refresh_design_context(self, create_if_missing=False):
        self.app = adsk.core.Application.get()
        self.ui = self.app.userInterface
        self.design = adsk.fusion.Design.cast(self.app.activeProduct)

        if self.design is None and create_if_missing:
            self._create_empty_design_document()
            self.app = adsk.core.Application.get()
            self.ui = self.app.userInterface
            self.design = adsk.fusion.Design.cast(self.app.activeProduct)

        if self.design is None:
            raise RuntimeError(
                "No active Fusion design document. Open or create a design first."
            )

        self.rootComp = self.design.rootComponent
        self.sketches_class = self.rootComp.sketches
        self.extrudes_class = self.rootComp.features.extrudeFeatures
        self.revolves_class = self.rootComp.features.revolveFeatures
        self.sketches = []
        self.sketches_attitudes = []
        self._initialize_entity_registry()

    def dismiss_active_ui_state(self, cancel_attempts=3):
        try:
            self.app = adsk.core.Application.get()
        except Exception:
            return

        try:
            self.ui = self.app.userInterface
        except Exception:
            self.ui = None

        active_selections = getattr(self.ui, "activeSelections", None)
        if active_selections is not None:
            try:
                active_selections.clear()
            except Exception:
                pass

        execute_text_command = getattr(self.app, "executeTextCommand", None)
        if callable(execute_text_command):
            for _ in range(max(1, int(cancel_attempts))):
                try:
                    execute_text_command("NuCommands.CancelCmd")
                except Exception:
                    pass
                maybe_do_events(force=True)

        if active_selections is not None:
            try:
                active_selections.clear()
            except Exception:
                pass

        maybe_do_events(force=True)

    def _initialize_entity_registry(self):
        self.entity_registry = {}
        self.entity_counter = 0
        self._local_axis_helper_cache = {}
        self._active_sketch = None
        self._active_sketch_token = None

    def _collection_count(self, collection):
        if collection is None:
            return 0
        try:
            return int(collection.count)
        except Exception:
            return 0

    def _iter_collection_items_reverse(self, collection):
        count = self._collection_count(collection)
        for index in range(count - 1, -1, -1):
            try:
                item = collection.item(index)
            except Exception:
                continue
            if item is not None:
                yield item

    def _delete_collection_items(self, collection):
        deleted = 0
        for item in self._iter_collection_items_reverse(collection):
            try:
                item.deleteMe()
            except Exception:
                continue
            deleted += 1
        return deleted

    def _delete_feature_collections(self):
        deleted = 0
        features = getattr(self.rootComp, "features", None)
        if features is None:
            return deleted

        for attr_name in self._FAST_CLEAR_FEATURE_COLLECTIONS:
            deleted += self._delete_collection_items(getattr(features, attr_name, None))
        return deleted

    def _delete_user_parameters(self):
        return self._delete_collection_items(
            getattr(self.design, "userParameters", None)
        )

    def _residual_design_counts(self):
        return {
            "occurrences": self._collection_count(
                getattr(self.rootComp, "occurrences", None)
            ),
            "sketches": self._collection_count(
                getattr(self.rootComp, "sketches", None)
            ),
            "constructionPlanes": self._collection_count(
                getattr(self.rootComp, "constructionPlanes", None)
            ),
            "constructionAxes": self._collection_count(
                getattr(self.rootComp, "constructionAxes", None)
            ),
            "constructionPoints": self._collection_count(
                getattr(self.rootComp, "constructionPoints", None)
            ),
            "bRepBodies": self._collection_count(
                getattr(self.rootComp, "bRepBodies", None)
            ),
            "userParameters": self._collection_count(
                getattr(self.design, "userParameters", None)
            ),
        }

    def _fast_clear_active_design(self):
        maybe_do_events(force=True)

        deleted = 0
        deleted += self._delete_collection_items(
            getattr(self.rootComp, "occurrences", None)
        )
        deleted += self._delete_feature_collections()
        deleted += self._delete_collection_items(
            getattr(self.rootComp, "sketches", None)
        )
        deleted += self._delete_collection_items(
            getattr(self.rootComp, "constructionPoints", None)
        )
        deleted += self._delete_collection_items(
            getattr(self.rootComp, "constructionAxes", None)
        )
        deleted += self._delete_collection_items(
            getattr(self.rootComp, "constructionPlanes", None)
        )
        deleted += self._delete_collection_items(
            getattr(self.rootComp, "bRepBodies", None)
        )
        deleted += self._delete_user_parameters()

        maybe_do_events(force=True)
        self._refresh_design_context(create_if_missing=False)
        residual = self._residual_design_counts()
        if any(residual.values()):
            raise RuntimeError(f"residual design state after fast clear: {residual}")
        return deleted

    def _hard_clear(self):
        self.app = adsk.core.Application.get()
        for doc in self.app.documents:
            doc.close(False)
        self._refresh_design_context(create_if_missing=True)

    def _register_entity(self, entity_obj, kind, sketch_index=None, metadata=None):
        self.entity_counter += 1
        entity_id = f"{kind}_{self.entity_counter}"
        entry = {
            "id": entity_id,
            "kind": kind,
            "obj": entity_obj,
            "sketch_index": sketch_index,
        }
        if metadata is not None:
            entry["metadata"] = metadata
        self.entity_registry[entity_id] = entry
        return entity_id

    def _recover_missing_point_entry(self, entity_id):
        registry = getattr(self, "entity_registry", None)
        if not isinstance(registry, dict):
            return None

        attr_by_metadata_key = {
            "start_point_id": "startSketchPoint",
            "end_point_id": "endSketchPoint",
            "center_point_id": "centerSketchPoint",
            "mid_point_id": "midSketchPoint",
        }
        for parent_entry in list(registry.values()):
            if not isinstance(parent_entry, dict):
                continue
            metadata = parent_entry.get("metadata")
            if not isinstance(metadata, dict):
                continue

            point_attr = None
            for metadata_key, attr_name in attr_by_metadata_key.items():
                if metadata.get(metadata_key) == entity_id:
                    point_attr = attr_name
                    break
            if point_attr is None:
                continue

            parent_obj = parent_entry.get("obj")
            if parent_obj is None:
                continue
            point_obj = getattr(parent_obj, point_attr, None)
            if point_obj is None:
                continue

            recovered_entry = {
                "id": entity_id,
                "kind": "point",
                "obj": point_obj,
                "sketch_index": parent_entry.get("sketch_index"),
                "metadata": {
                    "recovered_from": parent_entry.get("id"),
                    "recovered_attr": point_attr,
                },
            }
            registry[entity_id] = recovered_entry
            return recovered_entry
        return None

    def resolve_entity(self, entity_id, expected_kinds=None):
        if entity_id not in self.entity_registry:
            recovered_entry = None
            if isinstance(entity_id, str) and entity_id.startswith("point_"):
                recovered_entry = self._recover_missing_point_entry(entity_id)
            if recovered_entry is None:
                raise KeyError(f"Unknown entity_id: {entity_id}")
        entry = self.entity_registry[entity_id]
        if expected_kinds is not None and entry["kind"] not in expected_kinds:
            raise ValueError(
                f"Entity kind mismatch for {entity_id}: "
                f"expected {expected_kinds}, got {entry['kind']}"
            )
        return entry["obj"], entry

    def _safe_get_entity_attr(self, entity_obj, attr_name, default=None):
        if entity_obj is None:
            return default
        try:
            return getattr(entity_obj, attr_name)
        except Exception:
            return default

    def _safe_get_curve_endpoints(self, curve_obj):
        return (
            self._safe_get_entity_attr(curve_obj, "startSketchPoint"),
            self._safe_get_entity_attr(curve_obj, "endSketchPoint"),
        )

    def _list_live_sketches(self):
        sketches = []
        root_comp = getattr(self, "rootComp", None)
        if root_comp is None:
            return sketches

        sketches_collection = getattr(root_comp, "sketches", None)
        if sketches_collection is None:
            return sketches

        try:
            sketch_count = int(sketches_collection.count)
        except Exception:
            return sketches

        for i in range(sketch_count):
            sketch = sketches_collection.item(i)
            if sketch is not None:
                sketches.append(sketch)
        return sketches

    def _entity_token_value(self, entity_obj):
        if entity_obj is None:
            return None
        try:
            token = getattr(entity_obj, "entityToken", None)
        except Exception:
            return None
        if token in (None, ""):
            return None
        return str(token)

    def _resolve_sketch_index(self, sketch_num=-1):
        sketches = self.sketches or self._list_live_sketches()
        if not sketches:
            try:
                maybe_do_events(force=True)
            except Exception:
                pass
            sketches = self.sketches or self._list_live_sketches()
        if not sketches:
            raise RuntimeError("No sketches available. Create a sketch first.")
        if sketch_num == -1:
            _active_sketch, active_index = self._find_active_sketch()
            if active_index is not None:
                return active_index
            return len(sketches) - 1
        if sketch_num < 0 or sketch_num >= len(sketches):
            raise IndexError(
                f"Invalid sketch_num: {sketch_num}, valid range: 0..{len(sketches) - 1}"
            )
        return sketch_num

    def _get_sketch(self, sketch_num=-1):
        sketches = self.sketches or self._list_live_sketches()
        sketch_index = self._resolve_sketch_index(sketch_num)
        return sketches[sketch_index], sketch_index

    def _coerce_point3d(self, point):
        if point is None:
            raise ValueError("point cannot be None")
        if hasattr(point, "x") and hasattr(point, "y"):
            return adsk.core.Point3D.create(
                float(point.x),
                float(point.y),
                float(getattr(point, "z", 0.0)),
            )
        if len(point) == 2:
            return adsk.core.Point3D.create(float(point[0]), float(point[1]), 0.0)
        if len(point) == 3:
            return adsk.core.Point3D.create(
                float(point[0]), float(point[1]), float(point[2])
            )
        raise ValueError(f"Point must have length 2 or 3, got: {point}")

    def _coerce_vector3d(self, vector):
        if vector is None:
            raise ValueError("vector cannot be None")
        if hasattr(vector, "x") and hasattr(vector, "y") and hasattr(vector, "z"):
            return adsk.core.Vector3D.create(
                float(vector.x), float(vector.y), float(vector.z)
            )
        if len(vector) == 2:
            return adsk.core.Vector3D.create(float(vector[0]), float(vector[1]), 0.0)
        if len(vector) == 3:
            return adsk.core.Vector3D.create(
                float(vector[0]), float(vector[1]), float(vector[2])
            )
        raise ValueError(f"Vector must have length 2 or 3, got: {vector}")

    def _normalize_vector3d(self, vector):
        vector_3d = self._coerce_vector3d(vector)
        length = math.sqrt(
            float(vector_3d.x) * float(vector_3d.x)
            + float(vector_3d.y) * float(vector_3d.y)
            + float(vector_3d.z) * float(vector_3d.z)
        )
        if length <= 1e-12:
            raise ValueError("vector length must be > 0")
        return adsk.core.Vector3D.create(
            float(vector_3d.x) / length,
            float(vector_3d.y) / length,
            float(vector_3d.z) / length,
        )

    def _vector_between_points(self, start_point, end_point):
        start = self._coerce_point3d(start_point)
        end = self._coerce_point3d(end_point)
        return adsk.core.Vector3D.create(
            float(end.x) - float(start.x),
            float(end.y) - float(start.y),
            float(end.z) - float(start.z),
        )

    def _ensure_sketch_attitudes(self):
        if not hasattr(self, "sketches_attitudes") or self.sketches_attitudes is None:
            self.sketches_attitudes = []
        while len(self.sketches_attitudes) < len(self.sketches):
            self.sketches_attitudes.append([])
        return self.sketches_attitudes

    def _set_active_sketch(self, sketch):
        self._active_sketch = sketch
        self._active_sketch_token = self._entity_token_value(sketch)

    def _find_active_sketch(self):
        active_sketch = getattr(self, "_active_sketch", None)
        active_token = getattr(self, "_active_sketch_token", None)
        sketches = self.sketches or self._list_live_sketches()
        for index, existing in enumerate(sketches):
            if existing is active_sketch:
                return existing, index
            if (
                active_token is not None
                and self._entity_token_value(existing) == active_token
            ):
                return existing, index
        return None, None

    def _store_sketch_attitude(self, sketch_index, attitude=None):
        self._ensure_sketch_attitudes()
        self.sketches_attitudes[sketch_index] = attitude or []

    def _get_sketch_attitude(self, sketch_index):
        self._ensure_sketch_attitudes()
        if sketch_index < 0 or sketch_index >= len(self.sketches_attitudes):
            return []
        return self.sketches_attitudes[sketch_index]

    def _find_sketch_index(self, sketch):
        sketch_token = self._entity_token_value(sketch)
        for index, existing in enumerate(getattr(self, "sketches", [])):
            if existing is sketch:
                return index
            if (
                sketch_token is not None
                and self._entity_token_value(existing) == sketch_token
            ):
                return index
        for index, existing in enumerate(self._list_live_sketches()):
            if existing is sketch:
                return index
            if (
                sketch_token is not None
                and self._entity_token_value(existing) == sketch_token
            ):
                return index
        return None

    def _resolve_reference_sketch(self, ref_sketch_id):
        if isinstance(ref_sketch_id, str):
            sketch, entry = self.resolve_entity(
                ref_sketch_id, expected_kinds={"sketch"}
            )
            sketch_index = entry.get("sketch_index")
            if sketch_index is None:
                raise ValueError(
                    f"Sketch entity {ref_sketch_id} is not attached to a sketch index."
                )
            return sketch, int(sketch_index)
        return self._get_sketch(int(ref_sketch_id))

    def _get_mapped_model_point(self, sketch_idx, local_coords):
        sketch = self.sketches[sketch_idx]
        attitude = self._get_sketch_attitude(sketch_idx)

        if attitude:
            if not isinstance(local_coords, (list, tuple)) or len(local_coords) < 2:
                raise ValueError(
                    f"Expected a sketch-local [u, v] point for attitude mapping, got: {local_coords}"
                )
            u = float(local_coords[0])
            v = float(local_coords[1])
            origin, u_dir, v_dir = attitude
            return adsk.core.Point3D.create(
                float(origin.x) + u * float(u_dir.x) + v * float(v_dir.x),
                float(origin.y) + u * float(u_dir.y) + v * float(v_dir.y),
                float(origin.z) + u * float(u_dir.z) + v * float(v_dir.z),
            )

        point = self._coerce_point3d(local_coords)
        if isinstance(local_coords, (list, tuple)) and len(local_coords) == 2:
            try:
                model_point = sketch.sketchToModelSpace(point)
                if model_point is not None:
                    return model_point
            except Exception:
                pass
        return point

    def _get_mapped_point(self, sketch_idx, local_coords):
        sketch = self.sketches[sketch_idx]
        if isinstance(local_coords, (list, tuple)) and len(local_coords) == 2:
            attitude = self._get_sketch_attitude(sketch_idx)
            if not attitude:
                return adsk.core.Point3D.create(
                    float(local_coords[0]), float(local_coords[1]), 0.0
                )
            model_point = self._get_mapped_model_point(sketch_idx, local_coords)
            try:
                sketch_point = sketch.modelToSketchSpace(model_point)
                if sketch_point is not None:
                    return sketch_point
            except Exception:
                pass
            return model_point

        point = self._coerce_point3d(local_coords)
        try:
            sketch_point = sketch.modelToSketchSpace(point)
            if sketch_point is not None:
                return sketch_point
        except Exception:
            pass
        return point

    def _get_mapped_vector(self, sketch_idx, local_vector, normalize=False):
        sketch = self.sketches[sketch_idx]
        if not isinstance(local_vector, (list, tuple)):
            raise ValueError(f"Vector must have length 2 or 3, got: {local_vector}")

        if len(local_vector) == 2:
            attitude = self._get_sketch_attitude(sketch_idx)
            if attitude:
                origin, u_dir, v_dir = attitude
                target_model = adsk.core.Point3D.create(
                    float(origin.x)
                    + float(local_vector[0]) * float(u_dir.x)
                    + float(local_vector[1]) * float(v_dir.x),
                    float(origin.y)
                    + float(local_vector[0]) * float(u_dir.y)
                    + float(local_vector[1]) * float(v_dir.y),
                    float(origin.z)
                    + float(local_vector[0]) * float(u_dir.z)
                    + float(local_vector[1]) * float(v_dir.z),
                )
                try:
                    sketch_origin = sketch.modelToSketchSpace(origin)
                    sketch_target = sketch.modelToSketchSpace(target_model)
                    vx = float(sketch_target.x) - float(sketch_origin.x)
                    vy = float(sketch_target.y) - float(sketch_origin.y)
                    vz = float(sketch_target.z) - float(sketch_origin.z)
                except Exception:
                    vx = float(target_model.x) - float(origin.x)
                    vy = float(target_model.y) - float(origin.y)
                    vz = float(target_model.z) - float(origin.z)
            else:
                vx = float(local_vector[0])
                vy = float(local_vector[1])
                vz = 0.0
        elif len(local_vector) == 3:
            attitude = self._get_sketch_attitude(sketch_idx)
            if attitude:
                _origin, u_dir, v_dir = attitude
                normal = self._normalize_vector3d(
                    adsk.core.Vector3D.create(
                        float(u_dir.y) * float(v_dir.z)
                        - float(u_dir.z) * float(v_dir.y),
                        float(u_dir.z) * float(v_dir.x)
                        - float(u_dir.x) * float(v_dir.z),
                        float(u_dir.x) * float(v_dir.y)
                        - float(u_dir.y) * float(v_dir.x),
                    )
                )
                world_vector = self._coerce_vector3d(local_vector)
                vx = (
                    float(world_vector.x) * float(u_dir.x)
                    + float(world_vector.y) * float(u_dir.y)
                    + float(world_vector.z) * float(u_dir.z)
                )
                vy = (
                    float(world_vector.x) * float(v_dir.x)
                    + float(world_vector.y) * float(v_dir.y)
                    + float(world_vector.z) * float(v_dir.z)
                )
                vz = (
                    float(world_vector.x) * float(normal.x)
                    + float(world_vector.y) * float(normal.y)
                    + float(world_vector.z) * float(normal.z)
                )
            else:
                origin_model = adsk.core.Point3D.create(0.0, 0.0, 0.0)
                target_model = adsk.core.Point3D.create(
                    float(local_vector[0]),
                    float(local_vector[1]),
                    float(local_vector[2]),
                )
                try:
                    sketch_origin = sketch.modelToSketchSpace(origin_model)
                    sketch_target = sketch.modelToSketchSpace(target_model)
                    vx = float(sketch_target.x) - float(sketch_origin.x)
                    vy = float(sketch_target.y) - float(sketch_origin.y)
                    vz = float(sketch_target.z) - float(sketch_origin.z)
                except Exception:
                    vx = float(local_vector[0])
                    vy = float(local_vector[1])
                    vz = float(local_vector[2])
        else:
            raise ValueError(f"Vector must have length 2 or 3, got: {local_vector}")

        if normalize:
            length = math.sqrt(vx * vx + vy * vy + vz * vz)
            if length <= 1e-12:
                raise ValueError("vector length must be > 0")
            vx /= length
            vy /= length
            vz /= length
        return adsk.core.Vector3D.create(vx, vy, vz)

    def _normalize_coordinate_system_payload(self, coordinate_system):
        if not isinstance(coordinate_system, dict):
            raise ValueError(
                f"coordinate_system must be a dict, got: {type(coordinate_system)!r}"
            )

        euler_angles = coordinate_system.get("Euler Angles")
        translation_vector = coordinate_system.get("Translation Vector")
        if not isinstance(euler_angles, (list, tuple)) or len(euler_angles) != 3:
            raise ValueError(
                f"'Euler Angles' must be a length-3 array, got: {euler_angles!r}"
            )
        if (
            not isinstance(translation_vector, (list, tuple))
            or len(translation_vector) != 3
        ):
            raise ValueError(
                f"'Translation Vector' must be a length-3 array, got: {translation_vector!r}"
            )

        return (
            [float(value) for value in euler_angles],
            [float(value) for value in translation_vector],
        )

    def _multiply_matrix3(self, left, right):
        return tuple(
            tuple(
                sum(float(left[i][k]) * float(right[k][j]) for k in range(3))
                for j in range(3)
            )
            for i in range(3)
        )

    def _coordinate_system_rotation_matrix(self, euler_angles):
        ax, ay, az = [math.radians(float(value)) for value in euler_angles]
        rx = (
            (1.0, 0.0, 0.0),
            (0.0, math.cos(ax), -math.sin(ax)),
            (0.0, math.sin(ax), math.cos(ax)),
        )
        ry = (
            (math.cos(ay), 0.0, math.sin(ay)),
            (0.0, 1.0, 0.0),
            (-math.sin(ay), 0.0, math.cos(ay)),
        )
        rz = (
            (math.cos(az), -math.sin(az), 0.0),
            (math.sin(az), math.cos(az), 0.0),
            (0.0, 0.0, 1.0),
        )
        return self._multiply_matrix3(self._multiply_matrix3(rx, ry), rz)

    def _transform_coordinate_system_point(
        self, euler_angles, translation_vector, point
    ):
        px, py, pz = [float(value) for value in point]
        matrix = self._coordinate_system_rotation_matrix(euler_angles)
        tx, ty, tz = [float(value) for value in translation_vector]
        return [
            matrix[0][0] * px + matrix[0][1] * py + matrix[0][2] * pz + tx,
            matrix[1][0] * px + matrix[1][1] * py + matrix[1][2] * pz + ty,
            matrix[2][0] * px + matrix[2][1] * py + matrix[2][2] * pz + tz,
        ]

    def _detect_principal_plane_from_points(self, point1, point2, point3, tol=1e-8):
        points = (point1, point2, point3)
        for plane_type, axis in (("yz", 0), ("xz", 1), ("xy", 2)):
            values = [float(point[axis]) for point in points]
            if max(values) - min(values) <= tol:
                return plane_type, sum(values) / len(values)
        return None

    def _dot_product3(self, left, right):
        return sum(float(a) * float(b) for a, b in zip(left, right))

    def _cross_product3(self, left, right):
        lx, ly, lz = [float(value) for value in left]
        rx, ry, rz = [float(value) for value in right]
        return [
            ly * rz - lz * ry,
            lz * rx - lx * rz,
            lx * ry - ly * rx,
        ]

    def _normalize_vector3(self, vector, tol=1e-9):
        vx, vy, vz = [float(value) for value in vector]
        length = math.sqrt(vx * vx + vy * vy + vz * vz)
        if length <= tol:
            raise ValueError(f"Cannot normalize near-zero vector: {vector!r}")
        return [vx / length, vy / length, vz / length]

    def _point3d_to_list(self, point):
        point_3d = self._coerce_point3d(point)
        return [float(point_3d.x), float(point_3d.y), float(point_3d.z)]

    def _vector3d_to_list(self, vector):
        vector_3d = self._coerce_vector3d(vector)
        return [float(vector_3d.x), float(vector_3d.y), float(vector_3d.z)]

    def _standard_sketch_basis(self, plane_type):
        basis = self._STANDARD_SKETCH_BASIS.get(str(plane_type).lower())
        if basis is None:
            raise ValueError(f"Unsupported standard plane_type: {plane_type}")
        u_axis, v_axis, normal = basis
        return list(u_axis), list(v_axis), list(normal)

    def _standard_plane_attitude(self, plane_type, offset):
        u_axis, v_axis, _normal = self._standard_sketch_basis(plane_type)
        axis = self._STANDARD_PLANE_OFFSET_AXIS[str(plane_type).lower()]
        origin = [0.0, 0.0, 0.0]
        origin[axis] = float(offset)
        return [
            self._coerce_point3d(origin),
            self._coerce_vector3d(u_axis),
            self._coerce_vector3d(v_axis),
        ]

    def _coordinate_system_basis_points(self, coordinate_system):
        euler_angles, translation_vector = self._normalize_coordinate_system_payload(
            coordinate_system
        )
        origin = self._transform_coordinate_system_point(
            euler_angles, translation_vector, [0.0, 0.0, 0.0]
        )
        x_axis_point = self._transform_coordinate_system_point(
            euler_angles, translation_vector, [1.0, 0.0, 0.0]
        )
        y_axis_point = self._transform_coordinate_system_point(
            euler_angles, translation_vector, [0.0, 1.0, 0.0]
        )
        return origin, x_axis_point, y_axis_point

    def _coordinate_system_attitude(self, coordinate_system):
        origin, x_axis_point, y_axis_point = self._coordinate_system_basis_points(
            coordinate_system
        )
        world_origin = self._coerce_point3d(origin)
        return [
            world_origin,
            self._normalize_vector3d(
                self._vector_between_points(world_origin, x_axis_point)
            ),
            self._normalize_vector3d(
                self._vector_between_points(world_origin, y_axis_point)
            ),
        ]

    def _coordinate_system_basis_vectors(self, coordinate_system):
        attitude = self._coordinate_system_attitude(coordinate_system)
        u_axis = self._vector3d_to_list(attitude[1])
        v_axis = self._vector3d_to_list(attitude[2])
        return (
            u_axis,
            v_axis,
            self._normalize_vector3(self._cross_product3(u_axis, v_axis)),
        )

    def _base_plane_candidates(self, origin_world):
        candidates = []
        for plane_type, axis in self._STANDARD_PLANE_OFFSET_AXIS.items():
            _u_axis, _v_axis, normal = self._standard_sketch_basis(plane_type)
            candidates.append((plane_type, float(origin_world[axis]), normal))
        return candidates

    def _choose_helper_base_plane(self, origin_world, target_y_world):
        best_candidate = None
        best_score = -1.0
        for plane_type, offset, normal in self._base_plane_candidates(origin_world):
            cross = self._cross_product3(target_y_world, normal)
            score = math.sqrt(self._dot_product3(cross, cross))
            if score > best_score:
                best_candidate = (plane_type, offset, normal)
                best_score = score
        if best_candidate is None or best_score <= 1e-8:
            raise ValueError(
                "Failed to choose a stable helper base plane "
                f"for origin={origin_world!r}, target_y={target_y_world!r}"
            )
        return best_candidate

    def _world_point_to_standard_sketch_uv(self, plane_type, point):
        u_axis, v_axis, _normal = self._standard_sketch_basis(plane_type)
        return [
            float(self._dot_product3(point, u_axis)),
            float(self._dot_product3(point, v_axis)),
        ]

    def _world_vector_to_standard_sketch_uv(self, plane_type, vector):
        u_axis, v_axis, _normal = self._standard_sketch_basis(plane_type)
        return [
            float(self._dot_product3(vector, u_axis)),
            float(self._dot_product3(vector, v_axis)),
        ]

    def _attitude_normal(self, attitude):
        _origin, u_dir, v_dir = attitude
        return self._normalize_vector3d(
            self._cross_product3(
                self._vector3d_to_list(u_dir),
                self._vector3d_to_list(v_dir),
            )
        )

    def _model_point_from_attitude(self, attitude, point):
        origin, u_dir, v_dir = attitude
        normal = self._attitude_normal(attitude)
        px, py, pz = [float(value) for value in point]
        return adsk.core.Point3D.create(
            float(origin.x)
            + px * float(u_dir.x)
            + py * float(v_dir.x)
            + pz * float(normal.x),
            float(origin.y)
            + px * float(u_dir.y)
            + py * float(v_dir.y)
            + pz * float(normal.y),
            float(origin.z)
            + px * float(u_dir.z)
            + py * float(v_dir.z)
            + pz * float(normal.z),
        )

    def _build_reference_edge_sketch(
        self, ref_sketch, p1_uv, p2_uv, op, to, ref_attitude=None
    ):
        origin_local = adsk.core.Point3D.create(
            float(p1_uv[0]), float(p1_uv[1]), float(op)
        )
        u_target_local = adsk.core.Point3D.create(
            float(p2_uv[0]), float(p2_uv[1]), float(op)
        )
        v_target_local = adsk.core.Point3D.create(
            float(p1_uv[0]), float(p1_uv[1]), float(to)
        )

        if ref_attitude:
            world_origin = self._model_point_from_attitude(
                ref_attitude, [p1_uv[0], p1_uv[1], op]
            )
            world_u_target = self._model_point_from_attitude(
                ref_attitude, [p2_uv[0], p2_uv[1], op]
            )
            world_v_target = self._model_point_from_attitude(
                ref_attitude, [p1_uv[0], p1_uv[1], to]
            )
            # A reference-edge sketch may carry a custom attitude that differs from
            # Fusion's underlying sketch basis, so convert back into the reference
            # sketch's actual space before anchoring the three-point plane.
            origin_sketch = ref_sketch.modelToSketchSpace(world_origin)
            u_target_sketch = ref_sketch.modelToSketchSpace(world_u_target)
            v_target_sketch = ref_sketch.modelToSketchSpace(world_v_target)
        else:
            world_origin = ref_sketch.sketchToModelSpace(origin_local)
            world_u_target = ref_sketch.sketchToModelSpace(u_target_local)
            world_v_target = ref_sketch.sketchToModelSpace(v_target_local)
            origin_sketch = origin_local
            u_target_sketch = u_target_local
            v_target_sketch = v_target_local

        point_one = ref_sketch.sketchPoints.add(origin_sketch)
        point_two = ref_sketch.sketchPoints.add(u_target_sketch)
        point_three = ref_sketch.sketchPoints.add(v_target_sketch)

        planes = self.rootComp.constructionPlanes
        plane_input = planes.createInput()
        plane_input.setByThreePoints(point_one, point_two, point_three)
        plane = planes.add(plane_input)
        sketch = self.sketches_class.add(plane)
        attitude = [
            world_origin,
            self._normalize_vector3d(
                self._vector_between_points(world_origin, world_u_target)
            ),
            self._normalize_vector3d(
                self._vector_between_points(world_origin, world_v_target)
            ),
        ]
        return sketch, attitude

    def _create_rotated_coordinate_system_sketch(self, coordinate_system):
        origin_world, _x_axis_point, _y_axis_point = (
            self._coordinate_system_basis_points(coordinate_system)
        )
        target_x_world, target_y_world, _target_z_world = (
            self._coordinate_system_basis_vectors(coordinate_system)
        )
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

    def create_plane_from_coordinate_system(self, coordinate_system, tol=1e-8):
        origin, x_axis_point, y_axis_point = self._coordinate_system_basis_points(
            coordinate_system
        )

        principal_plane = self._detect_principal_plane_from_points(
            origin,
            x_axis_point,
            y_axis_point,
            tol=tol,
        )
        if principal_plane is not None:
            plane_type, offset = principal_plane
            return self.create_plane_by_base(plane_type, offset)

        raise ValueError(
            "Rotated coordinate_system must be materialized via create_sketch(..., coordinate_system=...) "
            "or create_sketch_by_three_points(...); direct plane creation is only supported for principal planes."
        )

    def create_plane_by_base(self, plane_type: str, offset: float = 0.0):
        plane_type = plane_type.lower()
        if plane_type == "xy":
            base_plane = self.rootComp.xYConstructionPlane
        elif plane_type == "xz":
            base_plane = self.rootComp.xZConstructionPlane
        elif plane_type == "yz":
            base_plane = self.rootComp.yZConstructionPlane
        else:
            raise ValueError(f"Invalid plane_type: {plane_type}")

        if abs(offset) < 1e-9:
            return base_plane

        planes = self.rootComp.constructionPlanes
        plane_input = planes.createInput()
        offset_value = adsk.core.ValueInput.createByReal(offset)
        plane_input.setByOffset(base_plane, offset_value)
        return planes.add(plane_input)

    def create_sketch(
        self, plane_type: str = "xy", offset: float = 0.0, coordinate_system=None
    ):
        if coordinate_system is not None:
            origin, x_axis_point, y_axis_point = self._coordinate_system_basis_points(
                coordinate_system
            )
            principal_plane = self._detect_principal_plane_from_points(
                origin,
                x_axis_point,
                y_axis_point,
            )
            if principal_plane is not None:
                plane_type, offset = principal_plane
                plane = self.create_plane_by_base(plane_type, offset)
                sketch = self.sketches_class.add(plane)
                sketch_attitude = self._coordinate_system_attitude(coordinate_system)
            else:
                sketch, sketch_attitude = self._create_rotated_coordinate_system_sketch(
                    coordinate_system
                )
        else:
            plane = self.create_plane_by_base(plane_type, offset)
            sketch = self.sketches_class.add(plane)
            sketch_attitude = []
        maybe_do_events()
        self.sketches.append(sketch)
        sketch_index = len(self.sketches) - 1
        self._store_sketch_attitude(sketch_index, sketch_attitude)
        self._set_active_sketch(sketch)
        sketch_id = self._register_entity(sketch, "sketch", sketch_index)
        return {
            "sketch_id": sketch_id,
            "sketch_num": sketch_index,
        }

    def create_sketch_by_three_points(self, ref_sketch_id, p1_uv, p2_uv, to, op=0.0):
        if ref_sketch_id is None or p1_uv is None or p2_uv is None or to is None:
            raise ValueError(
                "create_sketch_by_three_points requires ref_sketch_id, p1_uv, p2_uv, and to."
            )
        ref_sketch, _ref_sketch_index = self._resolve_reference_sketch(ref_sketch_id)
        ref_attitude = self._get_sketch_attitude(_ref_sketch_index)
        sketch, sketch_attitude = self._build_reference_edge_sketch(
            ref_sketch=ref_sketch,
            p1_uv=p1_uv,
            p2_uv=p2_uv,
            op=0.0 if op is None else float(op),
            to=float(to),
            ref_attitude=ref_attitude or None,
        )
        maybe_do_events()
        self.sketches.append(sketch)
        sketch_index = len(self.sketches) - 1
        self._store_sketch_attitude(sketch_index, sketch_attitude)
        self._set_active_sketch(sketch)
        sketch_id = self._register_entity(sketch, "sketch", sketch_index)
        return {
            "sketch_id": sketch_id,
            "sketch_num": sketch_index,
        }

    def clear(self):
        self._refresh_design_context(create_if_missing=True)
        self.dismiss_active_ui_state()
        try:
            self._fast_clear_active_design()
        except Exception as ex:
            try:
                self.logger.log(f"fast clear fallback: {ex}")
            except Exception:
                pass
            self._hard_clear()

    def import_step(self, filepath):
        abs_path = str(Path(filepath).expanduser().resolve())
        if not Path(abs_path).exists():
            raise FileNotFoundError(f"STEP file does not exist: {abs_path}")

        import_manager = self.app.importManager
        step_options = import_manager.createSTEPImportOptions(abs_path)
        errors = []

        import_to_target2 = getattr(import_manager, "importToTarget2", None)
        if callable(import_to_target2):
            try:
                imported = import_to_target2(step_options, self.rootComp)
                maybe_do_events(force=True)
                self._refresh_design_context(create_if_missing=True)
                return imported
            except Exception as exc:
                errors.append(f"importToTarget2 failed: {exc}")

        try:
            imported = import_manager.importToTarget(step_options, self.rootComp)
            maybe_do_events(force=True)
            self._refresh_design_context(create_if_missing=True)
            return imported
        except Exception as exc:
            errors.append(f"importToTarget failed: {exc}")

        raise RuntimeError(
            f"Failed to import STEP file into Fusion target component: {abs_path}. "
            + " | ".join(errors)
        )

    def get_design_from_document(self, document):
        if document is None:
            raise RuntimeError("Document is required.")

        products = getattr(document, "products", None)
        if products is None:
            raise RuntimeError("Document does not expose products.")

        design_product = None
        try:
            design_product = products.itemByProductType("DesignProductType")
        except Exception:
            design_product = None

        design = (
            adsk.fusion.Design.cast(design_product)
            if design_product is not None
            else None
        )
        if design is not None:
            return design

        try:
            if not document.isActive:
                document.activate()
                maybe_do_events(force=True)
        except Exception:
            pass

        design = adsk.fusion.Design.cast(self.app.activeProduct)
        if design is None:
            raise RuntimeError(
                "Failed to resolve Fusion design from imported document."
            )
        return design

    def import_step_to_new_document(self, filepath):
        abs_path = str(Path(filepath).expanduser().resolve())
        if not Path(abs_path).exists():
            raise FileNotFoundError(f"STEP file does not exist: {abs_path}")

        import_manager = self.app.importManager
        step_options = import_manager.createSTEPImportOptions(abs_path)
        document = import_manager.importToNewDocument(step_options)
        if document is None:
            raise RuntimeError(
                f"importToNewDocument returned null for STEP file: {abs_path}"
            )

        maybe_do_events(force=True)
        design = self.get_design_from_document(document)
        return document, design

    def export_step(self, filepath):
        export_mgr = self.design.exportManager
        step_options = export_mgr.createSTEPExportOptions(filepath)
        export_mgr.execute(step_options)

    def export_f3d(self, filepath):
        export_mgr = self.design.exportManager
        abs_path = str(Path(filepath).expanduser().resolve())
        try:
            archive_options = export_mgr.createFusionArchiveExportOptions(abs_path)
        except TypeError:
            archive_options = export_mgr.createFusionArchiveExportOptions(
                abs_path, self.rootComp
            )
        export_mgr.execute(archive_options)

    def export_view(self, filepath, width=512, height=512, view_orientation="iso"):
        viewport = self.app.activeViewport
        camera = viewport.camera
        view_map = {
            "front": adsk.core.ViewOrientations.FrontViewOrientation,
            "back": adsk.core.ViewOrientations.BackViewOrientation,
            "left": adsk.core.ViewOrientations.LeftViewOrientation,
            "right": adsk.core.ViewOrientations.RightViewOrientation,
            "top": adsk.core.ViewOrientations.TopViewOrientation,
            "bottom": adsk.core.ViewOrientations.BottomViewOrientation,
            "iso": adsk.core.ViewOrientations.IsoTopRightViewOrientation,
            "iso_top_right": adsk.core.ViewOrientations.IsoTopRightViewOrientation,
            "iso_top_left": adsk.core.ViewOrientations.IsoTopLeftViewOrientation,
            "iso_bottom_right": adsk.core.ViewOrientations.IsoBottomRightViewOrientation,
            "iso_bottom_left": adsk.core.ViewOrientations.IsoBottomLeftViewOrientation,
        }

        orientation = view_map.get(
            str(view_orientation).lower(),
            adsk.core.ViewOrientations.IsoTopRightViewOrientation,
        )

        camera.viewOrientation = orientation
        camera.isPerspective = False
        camera.isFitView = True

        viewport.camera = camera
        viewport.refresh()
        viewport.saveAsImageFile(filepath, width, height)
