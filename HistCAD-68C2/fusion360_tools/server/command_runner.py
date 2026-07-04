import math
import traceback

from .CAD.CAD_Create import CAD_Create
from .runtime import maybe_do_events


class CommandRunner:
    """Dispatch commands needed for HistCAD JSON export and editability checks."""

    def __init__(self):
        self.cad = CAD_Create()
        self.last_command = ""
        self._handlers = {
            "ping": self._cmd_ping,
            "run_batch": self._cmd_run_batch,
            "create_sketch": self._cmd_create_sketch,
            "create_sketch_by_three_points": self._cmd_create_sketch_by_three_points,
            "add_line": self._cmd_add_line,
            "add_circle": self._cmd_add_circle,
            "add_arc": self._cmd_add_arc,
            "add_ellipse": self._cmd_add_ellipse,
            "add_elliptical_arc": self._cmd_add_elliptical_arc,
            "add_nurbs": self._cmd_add_nurbs,
            "add_constraint": self._cmd_add_constraint,
            "add_raw_tangent_distance_dimension": self._cmd_add_raw_tangent_distance_dimension,
            "debug_distance_resolution": self._cmd_debug_distance_resolution,
            "auto_close_loop": self._cmd_auto_close_loop,
            "validate_step_file": self._cmd_validate_step_file,
            "validate_sketch_constraints": self._cmd_validate_sketch_constraints,
            "validate_all_sketch_constraints": self._cmd_validate_all_sketch_constraints,
            "check_constraint_satisfied": self._cmd_check_constraint_satisfied,
            "probe_constraint_effect": self._cmd_probe_constraint_effect,
            "inspect_entities": self._cmd_inspect_entities,
            "inspect_sketch_profiles": self._cmd_inspect_sketch_profiles,
            "create_user_parameter": self._cmd_create_user_parameter,
            "set_parameter_expression": self._cmd_set_parameter_expression,
            "get_parameter": self._cmd_get_parameter,
            "set_dimension_expression": self._cmd_set_dimension_expression,
            "get_model_metrics": self._cmd_get_model_metrics,
            "check_model": self._cmd_check_model,
            "create_extrude": self._cmd_create_extrude,
            "revolve": self._cmd_revolve,
            "create_helix_sweep": self._cmd_create_helix_sweep,
            "create_fillet": self._cmd_create_fillet,
            "create_chamfer": self._cmd_create_chamfer,
            "calculate_iou": self._cmd_calculate_iou,
            "calculate_assembly_collisions": self._cmd_calculate_assembly_collisions,
            "calculate_step_physical_properties": self._cmd_calculate_step_physical_properties,
            "clear": self._cmd_clear,
            "export_step": self._cmd_export_step,
            "export_f3d": self._cmd_export_f3d,
            "export_view": self._cmd_export_view,
            "detach": self._cmd_detach,
        }

    def run_command(self, command, data=None):
        try:
            self.last_command = command
            handler = self._handlers.get(command)
            if handler is None:
                return self.return_failure("Unknown command")
            result_data = handler(data or {})
            return self.return_success(result_data)
        except Exception as ex:
            try:
                self.cad.dismiss_active_ui_state()
            except Exception:
                pass
            return self.return_exception(ex)
        finally:
            maybe_do_events(force=True)

    def set_logger(self, logger):
        self.logger = logger

    def _cmd_ping(self, data):
        return {"runner_version": "export_fusion_server_0_1_0"}

    def _cmd_run_batch(self, data):
        return self._run_batch(data)

    def _cmd_create_sketch(self, data):
        return self.cad.create_sketch(
            plane_type=data.get("plane_type", "xy"),
            offset=data.get("offset", 0.0),
            coordinate_system=data.get("coordinate_system"),
        )

    def _cmd_create_sketch_by_three_points(self, data):
        return self.cad.create_sketch_by_three_points(
            ref_sketch_id=data.get("ref_sketch_id"),
            p1_uv=data.get("p1_uv"),
            p2_uv=data.get("p2_uv"),
            op=data.get("op"),
            to=data.get("to"),
        )

    def _cmd_add_line(self, data):
        return self.cad.add_line(
            data["startPoint"], data["endPoint"], data.get("sketch_num", -1)
        )

    def _cmd_add_circle(self, data):
        return self.cad.add_circle(
            data["centerPoint"], data["radius"], data.get("sketch_num", -1)
        )

    def _cmd_add_arc(self, data):
        return self.cad.add_arc(
            data["startPoint"],
            data.get("alongPoint", data.get("middle")),
            data["endPoint"],
            data.get("sketch_num", -1),
        )

    def _cmd_add_ellipse(self, data):
        return self.cad.add_ellipse(
            centerPoint=data["centerPoint"],
            major=data["major"],
            minor=data["minor"],
            angle=data["angle"],
            sketch_num=data.get("sketch_num", -1),
            majorAxisPoint=data.get("majorAxisPoint"),
            passPoint=data.get("passPoint"),
        )

    def _cmd_add_elliptical_arc(self, data):
        return self.cad.add_elliptical_arc(
            startPoint=data["startPoint"],
            endPoint=data["endPoint"],
            major=data["major"],
            minor=data["minor"],
            angle=data["angle"],
            large_arc=data.get("large_arc", False),
            sweep=data.get("sweep", True),
            sketch_num=data.get("sketch_num", -1),
            centerPoint=data.get("centerPoint"),
            majorAxisVector=data.get("majorAxisVector"),
            minorAxisVector=data.get("minorAxisVector"),
            startAngle=data.get("startAngle"),
            sweepAngle=data.get("sweepAngle"),
        )

    def _cmd_add_nurbs(self, data):
        return self.cad.add_nurbs(
            degree=data["degree"],
            periodic=data.get("periodic", data.get("closed", False)),
            controls=data["controls"],
            weights=data.get("weights"),
            knots=data.get("knots"),
            sketch_num=data.get("sketch_num", -1),
        )

    def _cmd_add_constraint(self, data):
        return self.cad.add_constraint(
            constraint_type=data["type"],
            entities=data["entities"],
            value=data.get("value"),
            sketch_num=data.get("sketch_num", -1),
            text_point=data.get("text_point"),
            is_driving=data.get("is_driving", True),
            extra=data.get("extra"),
        )

    def _cmd_add_raw_tangent_distance_dimension(self, data):
        return self.cad.add_raw_tangent_distance_dimension(
            first_entity_id=data["first_entity_id"],
            first_close_to_other=data.get("first_close_to_other", True),
            second_entity_id=data["second_entity_id"],
            second_close_to_other=data.get("second_close_to_other", True),
            value=data.get("value"),
            sketch_num=data.get("sketch_num", -1),
            text_point=data.get("text_point"),
            is_driving=data.get("is_driving", True),
        )

    def _cmd_debug_distance_resolution(self, data):
        return self.cad.debug_distance_resolution(
            entities=data["entities"],
            value=data.get("value"),
            sketch_num=data.get("sketch_num", -1),
            extra=data.get("extra"),
        )

    def _cmd_auto_close_loop(self, data):
        return self.cad.auto_close_loop(
            sketch_num=data.get("sketch_num", -1),
            tolerance=data.get("tolerance"),
        )

    def _cmd_validate_step_file(self, data):
        return self.cad.validate_step_file(
            filepath=data["filepath"],
            min_bytes=data.get("min_bytes", 1),
        )

    def _cmd_validate_sketch_constraints(self, data):
        return self.cad.validate_sketch_constraints(data.get("sketch_num", -1))

    def _cmd_validate_all_sketch_constraints(self, data):
        return self.cad.validate_all_sketch_constraints()

    def _cmd_check_constraint_satisfied(self, data):
        return self.cad.check_constraint_satisfied(
            constraint_type=data["type"],
            entities=data["entities"],
            value=data.get("value"),
            sketch_num=data.get("sketch_num", -1),
            extra=data.get("extra"),
        )

    def _cmd_probe_constraint_effect(self, data):
        return self.cad.probe_constraint_effect(
            constraint_type=data["type"],
            entities=data["entities"],
            value=data.get("value"),
            sketch_num=data.get("sketch_num", -1),
            text_point=data.get("text_point"),
            is_driving=data.get("is_driving", True),
            extra=data.get("extra"),
            follow_up_expression=data.get("follow_up_expression"),
            displacement_tolerance=data.get("displacement_tolerance", 1e-6),
        )

    def _cmd_inspect_entities(self, data):
        return self.cad.inspect_entities(data["entity_ids"])

    def _cmd_inspect_sketch_profiles(self, data):
        return self.cad.inspect_sketch_profiles(data.get("sketch_num", -1))

    def _cmd_create_user_parameter(self, data):
        return self.cad.create_user_parameter(
            data["name"],
            data["expression"],
            data.get("units", ""),
            data.get("comment", ""),
        )

    def _cmd_set_parameter_expression(self, data):
        return self.cad.set_parameter_expression(data["name"], data["expression"])

    def _cmd_get_parameter(self, data):
        return self.cad.get_parameter(data["name"])

    def _cmd_set_dimension_expression(self, data):
        return self.cad.set_dimension_expression(
            data["dimension_id"], data["expression"]
        )

    def _cmd_get_model_metrics(self, data):
        return self.cad.get_model_metrics()

    def _cmd_check_model(self, data):
        return self.cad.check_model(
            checklist=data.get("checklist"),
            options=data.get("options"),
        )

    def _cmd_create_extrude(self, data):
        kwargs = {"profile_point": data.get("profile_point")}
        for name in ("profile_mode", "opposite_distance", "target_body_index"):
            if data.get(name) is not None:
                kwargs[name] = data.get(name)
        self.cad.create_extrude(
            data["distance"],
            data.get("sketch_num", -1),
            data.get("operation", "NewBody"),
            **kwargs,
        )

    def _cmd_revolve(self, data):
        self.cad.revolve(
            axisPoint1=data.get("axisPoint1"),
            axisPoint2=data.get("axisPoint2"),
            angle=data.get("angle", 2 * math.pi),
            sketch_num=data.get("sketch_num", -1),
            operation=data.get("operation", "NewBody"),
            axis=data.get("axis"),
            start=data.get("start"),
            end=data.get("end"),
            target_body_index=data.get("target_body_index"),
        )

    def _cmd_create_helix_sweep(self, data):
        self.cad.create_helix_sweep(
            axis=data["axis"],
            pitch=data["pitch"],
            turns=data["turns"],
            handedness=data.get("handedness", "Right"),
            sketch_num=data.get("sketch_num", -1),
            operation=data.get("operation", "NewBody"),
            target_body_index=data.get("target_body_index"),
        )

    def _cmd_create_fillet(self, data):
        if data.get("points") is not None:
            self.cad.create_fillet(points=data.get("points"), radii=data.get("radii"))
        else:
            self.cad.create_fillet(data["point"], data["radius"])

    def _cmd_create_chamfer(self, data):
        if data.get("points") is not None:
            self.cad.create_chamfer(
                points=data.get("points"),
                distances=data.get("distances"),
                angles=data.get("angles"),
                planes=data.get("planes"),
            )
        else:
            self.cad.create_chamfer(
                data["point"],
                data["distance"],
                angle=data.get("angle"),
                plane=data.get("plane"),
            )

    def _cmd_calculate_iou(self, data):
        return self.cad.calculate_iou(data["step_file1"], data["step_file2"])

    def _cmd_calculate_assembly_collisions(self, data):
        return self.cad.calculate_assembly_collisions(
            data.get("items") or [],
            tolerance=data.get("tolerance", 1e-6),
        )

    def _cmd_calculate_step_physical_properties(self, data):
        return self.cad.calculate_step_physical_properties(data.get("items") or [])

    def _cmd_clear(self, data):
        self.cad.clear()

    def _cmd_export_step(self, data):
        self.cad.export_step(data["filepath"])

    def _cmd_export_f3d(self, data):
        self.cad.export_f3d(data["filepath"])

    def _cmd_export_view(self, data):
        self.cad.export_view(
            data["filepath"],
            data.get("w", data.get("width", 512)),
            data.get("h", data.get("height", 512)),
            data.get("view_orientation", "iso"),
        )

    def _cmd_detach(self, data):
        return None

    def _run_batch(self, data=None):
        payload = data or {}
        commands = payload.get("commands")
        if not isinstance(commands, list):
            raise ValueError("run_batch expects a 'commands' list.")

        initial_entities = payload.get("entities") or {}
        if not isinstance(initial_entities, dict):
            raise ValueError("run_batch expects 'entities' to be a dict when provided.")

        entities = dict(initial_entities)
        last_response = None

        for index, item in enumerate(commands):
            if not isinstance(item, dict):
                return self._batch_failure(
                    index,
                    item,
                    None,
                    "Batch item must be a dict.",
                    entities,
                    last_response,
                )

            method = item.get("method")
            if not isinstance(method, str) or not method:
                return self._batch_failure(
                    index,
                    item,
                    None,
                    "Batch item is missing a valid method.",
                    entities,
                    last_response,
                )
            if method == "run_batch":
                return self._batch_failure(
                    index,
                    item,
                    None,
                    "Nested run_batch commands are not supported.",
                    entities,
                    last_response,
                )

            raw_kwargs = item.get("kwargs", {})
            if not isinstance(raw_kwargs, dict):
                return self._batch_failure(
                    index,
                    item,
                    None,
                    "Batch item has non-dict kwargs.",
                    entities,
                    last_response,
                )

            try:
                resolved_kwargs = self._resolve_batch_value(raw_kwargs, entities)
            except Exception as ex:
                return self._batch_failure(
                    index, item, None, str(ex), entities, last_response
                )

            status_code, message, return_data = self.run_command(
                method, resolved_kwargs
            )
            if status_code != 200:
                return self._batch_failure(
                    index, item, resolved_kwargs, message, entities, last_response
                )

            last_response = return_data
            register = item.get("register") or {}
            if isinstance(register, dict) and isinstance(return_data, dict):
                for key, response_key in register.items():
                    if response_key in return_data:
                        entities[str(key)] = return_data[response_key]

        return {
            "ok": True,
            "failed_index": None,
            "failed_command": None,
            "resolved_kwargs": None,
            "error": None,
            "entities": entities,
            "last_response": last_response,
            "completed_count": len(commands),
        }

    def _batch_failure(
        self, index, command, resolved_kwargs, error, entities, last_response
    ):
        return {
            "ok": False,
            "failed_index": index,
            "failed_command": command,
            "resolved_kwargs": resolved_kwargs,
            "error": error,
            "entities": entities,
            "last_response": last_response,
        }

    def _resolve_batch_value(self, value, entities):
        if isinstance(value, list):
            return [self._resolve_batch_value(item, entities) for item in value]
        if isinstance(value, dict):
            if set(value.keys()) == {"__entity_ref__"}:
                entity_name = str(value["__entity_ref__"])
                if entity_name not in entities:
                    raise KeyError(f"Missing batch entity ref: {entity_name}")
                return entities[entity_name]
            return {
                key: self._resolve_batch_value(item, entities)
                for key, item in value.items()
            }
        return value

    def return_success(self, data=None):
        message = f"Success processing {self.last_command} command"
        return 200, message, data

    def return_failure(self, reason):
        message = f"Failed processing {self.last_command} command due to {reason}"
        return 500, message, None

    def return_exception(self, ex):
        message = f"""Error processing {self.last_command} command

                        Exception of type {type(ex)} with args: {ex.args}

                        {traceback.format_exc()}"""
        return 500, message, None
