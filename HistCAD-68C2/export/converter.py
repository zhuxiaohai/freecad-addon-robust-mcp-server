from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from scipy.spatial.transform import Rotation as R

PACKAGE_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class EntityRef:
    name: str


@dataclass(frozen=True)
class CoordinateSystem:
    euler_angles: tuple[float, float, float]
    translation_vector: tuple[float, float, float]

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> CoordinateSystem:
        payload = payload or {}
        euler_angles = payload.get("Euler Angles", [0.0, 0.0, 0.0])
        translation_vector = payload.get("Translation Vector", [0.0, 0.0, 0.0])
        return cls(
            tuple(float(value) for value in euler_angles),
            tuple(float(value) for value in translation_vector),
        )

    def to_payload(self) -> dict[str, list[float]]:
        return {
            "Euler Angles": [float(value) for value in self.euler_angles],
            "Translation Vector": [float(value) for value in self.translation_vector],
        }


@dataclass
class Command:
    method: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    capture: str | None = None
    register: dict[str, str] = field(default_factory=dict)
    label: str | None = None


def _serialize_script_value(value: Any) -> Any:
    if isinstance(value, EntityRef):
        return {"__entity_ref__": value.name}
    if isinstance(value, list):
        return [_serialize_script_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_script_value(item) for key, item in value.items()}
    return value


def _deserialize_script_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_deserialize_script_value(item) for item in value]
    if isinstance(value, dict):
        if set(value.keys()) == {"__entity_ref__"}:
            return EntityRef(str(value["__entity_ref__"]))
        return {key: _deserialize_script_value(item) for key, item in value.items()}
    return value


def _to_displayable_value(value: Any) -> Any:
    if isinstance(value, EntityRef):
        return value.name
    if isinstance(value, list):
        return [_to_displayable_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_displayable_value(item) for key, item in value.items()}
    return value


def _collect_entity_ref_names(value: Any) -> set[str]:
    if isinstance(value, EntityRef):
        return {value.name}
    if isinstance(value, list):
        names: set[str] = set()
        for item in value:
            names.update(_collect_entity_ref_names(item))
        return names
    if isinstance(value, dict):
        names: set[str] = set()
        for item in value.values():
            names.update(_collect_entity_ref_names(item))
        return names
    return set()


def _constraint_skip_summary(kwargs: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"type": str(kwargs.get("type", "constraint"))}
    entities = _to_displayable_value(kwargs.get("entities"))
    if entities not in (None, [], {}):
        summary["entities"] = entities
    if "value" in kwargs:
        summary["value"] = _to_displayable_value(kwargs.get("value"))
    extra = _to_displayable_value(kwargs.get("extra"))
    if extra not in (None, [], {}):
        summary["extra"] = extra
    return summary


def _format_constraint_skip_summary(kwargs: dict[str, Any]) -> str:
    return json.dumps(
        _constraint_skip_summary(kwargs), ensure_ascii=False, sort_keys=True
    )


def _xy_point(payload: Any) -> tuple[float, float] | None:
    if not isinstance(payload, (list, tuple)) or len(payload) < 2:
        return None
    try:
        return float(payload[0]), float(payload[1])
    except (TypeError, ValueError):
        return None


def _analyze_three_point_arc_payload(
    payload: dict[str, Any],
    *,
    point_tol: float = 1e-8,
    relative_height_tol: float = 1e-4,
    absolute_height_tol: float = 1e-5,
    tiny_chord_tol: float = 0.1,
    tiny_relative_height_tol: float = 2e-3,
    tiny_absolute_height_tol: float = 1e-4,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    start = _xy_point(payload.get("start"))
    middle = _xy_point(payload.get("middle"))
    end = _xy_point(payload.get("end"))
    if start is None or middle is None or end is None:
        return None

    x1, y1 = start
    x2, y2 = middle
    x3, y3 = end
    start_to_middle = math.hypot(x2 - x1, y2 - y1)
    middle_to_end = math.hypot(x3 - x2, y3 - y2)
    chord = math.hypot(x3 - x1, y3 - y1)

    reason: str | None = None
    height = 0.0
    if min(start_to_middle, middle_to_end, chord) <= point_tol:
        reason = "duplicate_or_zero_length_points"
    else:
        double_area = abs((x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1))
        height = double_area / chord
        if height <= max(absolute_height_tol, chord * relative_height_tol):
            reason = "nearly_collinear_three_point_arc"
        elif chord <= tiny_chord_tol and height <= max(
            tiny_absolute_height_tol, chord * tiny_relative_height_tol
        ):
            reason = "tiny_shallow_three_point_arc"

    if reason is None:
        return None

    height_ratio = 0.0 if chord <= point_tol else float(height / chord)
    return {
        "reason": reason,
        "start": list(payload.get("start") or []),
        "middle": list(payload.get("middle") or []),
        "end": list(payload.get("end") or []),
        "chord": chord,
        "height": height,
        "height_ratio": height_ratio,
    }


def _is_degenerate_three_point_arc_issue(issue: dict[str, Any] | None) -> bool:
    return isinstance(issue, dict) and issue.get("reason") in {
        "nearly_collinear_three_point_arc",
        "tiny_shallow_three_point_arc",
    }


def serialize_commands(commands: list[Command]) -> list[dict[str, Any]]:
    return [
        {
            "method": command.method,
            "kwargs": _serialize_script_value(command.kwargs),
            "capture": command.capture,
            "register": dict(command.register),
            "label": command.label,
        }
        for command in commands
    ]


def deserialize_commands(payload: list[dict[str, Any]]) -> list[Command]:
    return [
        Command(
            method=str(item["method"]),
            kwargs=_deserialize_script_value(item.get("kwargs", {})),
            capture=item.get("capture"),
            register=dict(item.get("register", {})),
            label=item.get("label"),
        )
        for item in payload
    ]


def run_serialized_commands(
    serialized_commands: list[dict[str, Any]], host: str = "127.0.0.1", port: int = 8080
) -> dict[str, Any]:
    converter = JsonToFusionConverter(host=host, port=port)
    return converter.execute_commands(deserialize_commands(serialized_commands))


class FusionScriptSession:
    _LOCAL_POINT_FIELDS_BY_METHOD = {
        "add_line": ("startPoint", "endPoint"),
        "add_circle": ("centerPoint",),
        "add_arc": ("startPoint", "alongPoint", "endPoint"),
        "add_ellipse": ("centerPoint", "majorAxisPoint", "passPoint"),
        "add_elliptical_arc": ("startPoint", "endPoint", "centerPoint"),
        "add_constraint": ("text_point",),
    }
    _LOCAL_POINT_LIST_FIELDS_BY_METHOD = {
        "add_nurbs": ("controls",),
    }
    _LOCAL_VECTOR_FIELDS_BY_METHOD = {
        "add_elliptical_arc": ("majorAxisVector", "minorAxisVector"),
    }

    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        from fusion360_tools.client.fusion360_client import Fusion360Client

        self._client = _ResponseCompatClient(Fusion360Client(f"http://{host}:{port}"))
        self._script_sketch_coordinate_systems: list[CoordinateSystem | None] = []

    def _rotation_matrix(
        self, coord: CoordinateSystem
    ) -> tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]:
        angles_rad = [math.radians(v) for v in coord.euler_angles]
        mat = R.from_euler("XYZ", angles_rad).as_matrix()
        return (
            (float(mat[0, 0]), float(mat[0, 1]), float(mat[0, 2])),
            (float(mat[1, 0]), float(mat[1, 1]), float(mat[1, 2])),
            (float(mat[2, 0]), float(mat[2, 1]), float(mat[2, 2])),
        )

    def _transform_point(
        self, coord: CoordinateSystem, point: list[float] | tuple[float, ...]
    ) -> list[float]:
        local_x = float(point[0])
        local_y = float(point[1])
        local_z = float(point[2]) if len(point) >= 3 else 0.0
        matrix = self._rotation_matrix(coord)
        world_x = (
            matrix[0][0] * local_x
            + matrix[0][1] * local_y
            + matrix[0][2] * local_z
            + coord.translation_vector[0]
        )
        world_y = (
            matrix[1][0] * local_x
            + matrix[1][1] * local_y
            + matrix[1][2] * local_z
            + coord.translation_vector[1]
        )
        world_z = (
            matrix[2][0] * local_x
            + matrix[2][1] * local_y
            + matrix[2][2] * local_z
            + coord.translation_vector[2]
        )
        return [round(world_x, 10), round(world_y, 10), round(world_z, 10)]

    def _transform_vector(
        self, coord: CoordinateSystem, vector: list[float] | tuple[float, ...]
    ) -> list[float]:
        local_x = float(vector[0])
        local_y = float(vector[1])
        local_z = float(vector[2]) if len(vector) >= 3 else 0.0
        matrix = self._rotation_matrix(coord)
        world_x = (
            matrix[0][0] * local_x + matrix[0][1] * local_y + matrix[0][2] * local_z
        )
        world_y = (
            matrix[1][0] * local_x + matrix[1][1] * local_y + matrix[1][2] * local_z
        )
        world_z = (
            matrix[2][0] * local_x + matrix[2][1] * local_y + matrix[2][2] * local_z
        )
        return [round(world_x, 10), round(world_y, 10), round(world_z, 10)]

    def _set_script_sketch_coordinate_system(
        self,
        sketch_index: int,
        coordinate_system: CoordinateSystem | None,
    ) -> None:
        while len(self._script_sketch_coordinate_systems) <= sketch_index:
            self._script_sketch_coordinate_systems.append(None)
        self._script_sketch_coordinate_systems[sketch_index] = coordinate_system

    def _record_created_sketch(self, kwargs: dict[str, Any], response: Any) -> None:
        coordinate_system = None
        raw_payload = kwargs.get("coordinate_system")
        if isinstance(raw_payload, dict):
            coordinate_system = CoordinateSystem.from_payload(raw_payload)

        if isinstance(response, dict) and isinstance(response.get("sketch_num"), int):
            sketch_index = int(response["sketch_num"])
            self._set_script_sketch_coordinate_system(sketch_index, coordinate_system)
            return

        self._script_sketch_coordinate_systems.append(coordinate_system)

    def _target_sketch_coordinate_system(
        self, kwargs: dict[str, Any]
    ) -> CoordinateSystem | None:
        if not self._script_sketch_coordinate_systems:
            return None

        sketch_num = kwargs.get("sketch_num", -1)
        try:
            sketch_index = int(sketch_num)
        except Exception:
            sketch_index = -1

        if sketch_index == -1:
            return self._script_sketch_coordinate_systems[-1]
        if 0 <= sketch_index < len(self._script_sketch_coordinate_systems):
            return self._script_sketch_coordinate_systems[sketch_index]
        return None

    def _rewrite_local_sketch_kwargs(
        self, method: str, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        coordinate_system = self._target_sketch_coordinate_system(kwargs)
        if coordinate_system is None:
            return kwargs

        rewritten = dict(kwargs)
        for field_name in self._LOCAL_POINT_FIELDS_BY_METHOD.get(method, ()):
            value = rewritten.get(field_name)
            if isinstance(value, (list, tuple)) and len(value) == 2:
                rewritten[field_name] = self._transform_point(coordinate_system, value)

        for field_name in self._LOCAL_POINT_LIST_FIELDS_BY_METHOD.get(method, ()):
            value = rewritten.get(field_name)
            if isinstance(value, list):
                rewritten[field_name] = [
                    self._transform_point(coordinate_system, item)
                    if isinstance(item, (list, tuple)) and len(item) == 2
                    else item
                    for item in value
                ]

        for field_name in self._LOCAL_VECTOR_FIELDS_BY_METHOD.get(method, ()):
            value = rewritten.get(field_name)
            if isinstance(value, (list, tuple)) and len(value) == 2:
                rewritten[field_name] = self._transform_vector(coordinate_system, value)

        return rewritten

    def _invoke(self, method: str, kwargs: dict[str, Any]) -> Any:
        if method == "clear":
            self._script_sketch_coordinate_systems = []
        runtime_kwargs = self._rewrite_local_sketch_kwargs(method, kwargs)
        target = getattr(self._client, method)
        try:
            response = _normalize_response(target(**runtime_kwargs))
        except Exception as ex:
            if method == "add_constraint" and _is_sketch_overconstraint_error(ex):
                print(
                    f"[skip] over-constrained constraint ignored: {_format_constraint_skip_summary(runtime_kwargs)}"
                )
                return {}
            if method == "add_constraint" and _is_invalid_constraint_reference_error(
                ex
            ):
                print(
                    f"[skip] invalid constraint reference ignored: {_format_constraint_skip_summary(runtime_kwargs)}"
                )
                return {}
            if method == "add_nurbs" and _is_invalid_sketch_primitive_error(ex):
                print(f"[skip] invalid sketch primitive ignored: {method}")
                return {}
            if method in {"create_fillet", "create_chamfer"} and (
                _is_missing_edge_feature_error(ex)
                or _is_edge_feature_geometry_failure_error(ex)
            ):
                print(f"[skip] unresolved edge feature ignored: {method}")
                return {}
            if (
                method == "create_extrude"
                and runtime_kwargs.get("operation") in {"Join", "Cut", "Intersect"}
                and (
                    _is_missing_boolean_target_error(ex)
                    or _is_boolean_geometry_failure_error(ex)
                    or _is_extrude_creation_failure_error(ex)
                )
            ):
                print(
                    f"[skip] recoverable boolean extrude ignored: "
                    f"{runtime_kwargs.get('operation', 'extrude')}"
                )
                return {}
            if method == "create_extrude" and _is_extrude_creation_failure_error(ex):
                print(
                    f"[skip] extrude geometry failure ignored: "
                    f"{runtime_kwargs.get('operation', 'extrude')}"
                )
                return {}
            if method == "create_extrude" and _is_empty_sketch_profile_error(ex):
                print(
                    f"[skip] empty sketch extrude ignored: {runtime_kwargs.get('operation', 'extrude')}"
                )
                return {}
            raise
        if method in {"create_sketch", "create_sketch_by_three_points"}:
            self._record_created_sketch(kwargs, response)
        return response

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if not callable(target):
            return target

        def _wrapped(**kwargs: Any) -> Any:
            return self._invoke(name, kwargs)

        return _wrapped


SCRIPT_COMMANDS_PREFIX = "COMMANDS = json.loads(r'''"
SCRIPT_COMMANDS_SUFFIX = "''')"
SCRIPT_MODEL_PREFIX = "MODEL = json.loads(r'''"
SCRIPT_MODEL_SUFFIX = "''')"


def dumps_serialized_commands(serialized_commands: list[dict[str, Any]]) -> str:
    return json.dumps(serialized_commands, ensure_ascii=False, indent=2)


def extract_serialized_commands_from_script_text(
    script_text: str,
) -> list[dict[str, Any]]:
    pattern = (
        re.escape(SCRIPT_COMMANDS_PREFIX)
        + r"\s*(.*?)\s*"
        + re.escape(SCRIPT_COMMANDS_SUFFIX)
    )
    match = re.search(pattern, script_text, flags=re.DOTALL)
    if match is None:
        raise ValueError(
            "Generated script does not contain an embedded COMMANDS JSON block."
        )
    return json.loads(match.group(1))


def extract_serialized_commands_from_script(
    script_path: str | Path,
) -> list[dict[str, Any]]:
    return extract_serialized_commands_from_script_text(
        Path(script_path).read_text(encoding="utf-8")
    )


def render_script_from_serialized_commands(
    serialized_commands: list[dict[str, Any]], host: str = "127.0.0.1", port: int = 8080
) -> str:
    payload = dumps_serialized_commands(serialized_commands)
    lines = [
        "import json",
        "",
        "from export.converter import run_serialized_commands",
        "",
        "COMMANDS = json.loads(r'''",
        payload,
        "''')",
        "",
        f"run_serialized_commands(COMMANDS, host={host!r}, port={int(port)})",
    ]
    return "\n".join(lines)


def run_histcad_payload(
    payload: list[dict[str, Any]],
    step_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> dict[str, Any]:
    converter = JsonToFusionConverter(host=host, port=port)
    commands = converter.build_commands(payload, step_path)
    return converter.execute_commands(commands)


def extract_histcad_json_from_script_text(script_text: str) -> list[dict[str, Any]]:
    pattern = (
        re.escape(SCRIPT_MODEL_PREFIX) + r"\s*(.*?)\s*" + re.escape(SCRIPT_MODEL_SUFFIX)
    )
    match = re.search(pattern, script_text, flags=re.DOTALL)
    if match is None:
        raise ValueError(
            "Generated script does not contain an embedded MODEL JSON block."
        )
    return json.loads(match.group(1))


def extract_histcad_json_from_script(script_path: str | Path) -> list[dict[str, Any]]:
    return extract_histcad_json_from_script_text(
        Path(script_path).read_text(encoding="utf-8")
    )


def render_payload_script(
    payload: list[dict[str, Any]],
    step_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> str:
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    step_path = str(Path(step_path).resolve())
    lines = [
        "import json",
        "",
        "from export.converter import run_histcad_payload",
        "",
        "MODEL = json.loads(r'''",
        payload_text,
        "''')",
        f"STEP_PATH = {step_path!r}",
        "",
        f"run_histcad_payload(MODEL, STEP_PATH, host={host!r}, port={int(port)})",
    ]
    return "\n".join(lines)


def _normalize_response(response: Any) -> Any:
    if not isinstance(response, dict):
        return response
    data = response.get("data")
    if not isinstance(data, dict):
        return response
    merged = dict(response)
    for key, value in data.items():
        if key not in merged:
            merged[key] = value
    return merged


def _is_batch_unsupported_error(error: Any) -> bool:
    message = str(error).lower()
    return "run_batch" in message and (
        "unknown command" in message
        or "no attribute 'run_batch'" in message
        or 'no attribute "run_batch"' in message
    )


def _is_sketch_overconstraint_error(error: Any) -> bool:
    message = str(error)
    markers = (
        "VCS_SKETCH_OVER_CONSTRAINTS",
        "VCS_SKETCH_SOLVING_FAILED",
        "already has the same dimension",
        "already has same dimension",
        "same dimension",
        "over constraint",
        "over-constrained",
        "over constrained",
        "constraint solve failed",
        "unable to solve",
        "constraint has already been applied to the selected sketch object",
        "failed to create offset: constraint has already been applied",
        "约束已应用到选定的草图对象",
        "failed to create offset:",
        "invalid set operation for fixed entity",
        "过约束",
        "相同尺寸",
        "无法求解",
    )
    candidates = [message.lower()]
    try:
        decoded = message.encode("utf-8").decode("unicode_escape")
        candidates.append(decoded.lower())
    except Exception:
        pass
    return any(
        marker.lower() in candidate for candidate in candidates for marker in markers
    )


def _is_invalid_constraint_reference_error(error: Any) -> bool:
    message = str(error)
    markers = (
        "Unknown entity_id:",
        "All entities must belong to the same sketch",
        "is not attached to any sketch",
        "Local axis direction collapses in sketch space",
        "expects exactly one circle/arc entity",
        "expects exactly one ellipse/elliptical_arc entity",
        "expects exactly one line entity",
        "expects one line entity or exactly two entities",
        "expects exactly two line entities",
        "internalvalidationerror : rdim",
        "failed to apply expression for",
        "expression is invalid",
        "only supports all-line or all-point entities",
        "invalid argument entityone",
        "invalid argument entitytwo",
        "findpointoncircleforpointcircdim",
        "wrong number or type of arguments for overloaded function "
        "'sketchdimensions_addoffsetdimension'",
        "wrong number or type of arguments for overloaded function "
        "'sketchdimensions_adddistancedimension'",
        "wrong number or type of arguments for overloaded function "
        "'sketchdimensions_addconcentriccircledimension'",
    )
    candidates = [message.lower()]
    try:
        decoded = message.encode("utf-8").decode("unicode_escape")
        candidates.append(decoded.lower())
    except Exception:
        pass
    return any(
        marker.lower() in candidate for candidate in candidates for marker in markers
    )


def _is_invalid_sketch_primitive_error(error: Any) -> bool:
    message = str(error)
    markers = (
        "controls length must be >= degree+1",
        "knots is required when periodic=true",
        "knots length must be >= control_count + degree + 1",
        "weights length must equal controls length",
        "nurbs endpoint repair collapsed the curve to fewer than 2 controls",
        "nurbs endpoint repair produced an invalid degree",
        "failed to create nurbscurve3d",
        "invalid argument knots",
        "invalid argument controlpoints",
    )
    candidates = [message.lower()]
    try:
        decoded = message.encode("utf-8").decode("unicode_escape")
        candidates.append(decoded.lower())
    except Exception:
        pass
    return any(
        marker.lower() in candidate for candidate in candidates for marker in markers
    )


def _is_missing_edge_feature_error(error: Any) -> bool:
    message = str(error)
    markers = (
        "No edge found at given point",
        "No edge found at given point for edge feature",
        "Bad index parameter",
        "FILLET_NO_EDGE_FOUND",
        "CHAMFER_NO_EDGE_FOUND",
        "\u672a\u627e\u5230\u5706\u89d2\u8fb9",
        "\\u672a\\u627e\\u5230\\u5706\\u89d2\\u8fb9",
        "\u672a\u627e\u5230\u5012\u89d2\u8fb9",
        "\\u672a\\u627e\\u5230\\u5012\\u89d2\\u8fb9",
    )
    lowered = message.lower()
    return any(marker.lower() in lowered for marker in markers)


def _is_edge_feature_geometry_failure_error(error: Any) -> bool:
    message = str(error)
    markers = (
        "ASM_",
        "compute failed",
        "failed to create fillet",
        "failed to create chamfer",
        "invalid input parameter",
        "one of the input parameters is invalid",
        "unable to create the requested fillet/chamfer size",
        "输入参数无效",
        "无法以请求的大小创建圆角/倒角",
        "圆角",
        "倒角",
        "计算失败",
    )
    candidates = [message.lower()]
    try:
        decoded = message.encode("utf-8").decode("unicode_escape")
        candidates.append(decoded.lower())
    except Exception:
        pass
    return any(
        marker.lower() in candidate for candidate in candidates for marker in markers
    )


def _is_missing_boolean_target_error(error: Any) -> bool:
    message = str(error)
    markers = (
        "未找到要剪切或相交的目标实体",
        "\\u672a\\u627e\\u5230\\u8981\\u526a\\u5207\\u6216\\u76f8\\u4ea4\\u7684\\u76ee\\u6807\\u5b9e\\u4f53",
        "No target body",
        "target body to cut or intersect",
    )
    candidates = [message.lower()]
    try:
        decoded = message.encode("utf-8").decode("unicode_escape")
        candidates.append(decoded.lower())
    except Exception:
        pass
    return any(
        marker.lower() in candidate for candidate in candidates for marker in markers
    )


def _is_boolean_geometry_failure_error(error: Any) -> bool:
    message = str(error)
    markers = (
        "ASM_",
        "compute failed",
        "计算失败",
        "boolean operation",
        "combine",
        "feature_failed_to_create",
        "无法创建特征",
        "无法执行布尔运算",
        "\\u65e0\\u6cd5\\u6267\\u884c\\u5e03\\u5c14\\u8fd0\\u7b97",
        "\\u65e0\\u6cd5\\u521b\\u5efa\\u7279\\u5f81",
    )
    candidates = [message.lower()]
    try:
        decoded = message.encode("utf-8").decode("unicode_escape")
        candidates.append(decoded.lower())
    except Exception:
        pass
    return any(
        marker.lower() in candidate for candidate in candidates for marker in markers
    )


def _is_extrude_creation_failure_error(error: Any) -> bool:
    message = str(error)
    markers = (
        "EXTRUDE_CREATION_FAIL_ERROR",
        "Cannot complete extrusion",
        "The extrude profile is not valid",
        "The extrude could not create a valid body",
        "extrude profile is not valid",
        "extrude could not create a valid body",
    )
    candidates = [message.lower()]
    try:
        decoded = message.encode("utf-8").decode("unicode_escape")
        candidates.append(decoded.lower())
    except Exception:
        pass
    return any(
        marker.lower() in candidate for candidate in candidates for marker in markers
    )


def _is_empty_sketch_profile_error(error: Any) -> bool:
    message = str(error)
    markers = (
        "No profiles in sketch",
        "No profiles available for extrude",
        "No profiles available for sequential boolean extrude",
    )
    lowered = message.lower()
    return any(marker.lower() in lowered for marker in markers)


class _ResponseCompatClient:
    def __init__(self, raw_client: Any):
        self._raw_client = raw_client

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._raw_client, name)
        if not callable(target):
            return target

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            return _normalize_response(target(*args, **kwargs))

        return _wrapped


class JsonToFusionConverter:
    UNITLESS_LENGTH_SCALE = (
        0.1  # HistCAD JSON uses mm; Fusion geometry APIs consume cm.
    )
    DIMENSION_TYPES = {
        "angle",
        "diameter",
        "radius",
        "distance",
        "length",
        "majorradius",
        "minorradius",
    }
    SKETCH_AXIS_CONSTRAINT_TYPES = {"horizontal", "vertical"}
    VALUE_KEYS = {
        "angle": ("angle", "value"),
        "diameter": ("diameter", "value"),
        "radius": ("radius", "value"),
        "distance": ("length", "distance", "value"),
        "length": ("length", "value"),
        "majorradius": ("majorRadius", "major_radius", "major", "radius", "value"),
        "minorradius": ("minorRadius", "minor_radius", "minor", "radius", "value"),
    }
    LOCAL_POINT_FIELDS_BY_METHOD = {
        "add_line": ("startPoint", "endPoint"),
        "add_circle": ("centerPoint",),
        "add_arc": ("startPoint", "alongPoint", "endPoint"),
        "add_ellipse": ("centerPoint", "majorAxisPoint", "passPoint"),
        "add_elliptical_arc": ("startPoint", "endPoint", "centerPoint"),
        "add_constraint": ("text_point",),
    }
    LOCAL_POINT_LIST_FIELDS_BY_METHOD = {
        "add_nurbs": ("controls",),
    }
    LOCAL_VECTOR_FIELDS_BY_METHOD = {
        "add_elliptical_arc": ("majorAxisVector", "minorAxisVector"),
    }
    FUSION_POINT_FIELDS_BY_METHOD = {
        **LOCAL_POINT_FIELDS_BY_METHOD,
        "create_extrude": ("profile_point",),
        "create_sketch_by_three_points": ("p1_uv", "p2_uv"),
    }
    FUSION_POINT_LIST_FIELDS_BY_METHOD = {
        **LOCAL_POINT_LIST_FIELDS_BY_METHOD,
        "create_fillet": ("points",),
        "create_chamfer": ("points",),
    }
    FUSION_LENGTH_FIELDS_BY_METHOD = {
        "add_circle": ("radius",),
        "add_ellipse": ("major", "minor"),
        "add_elliptical_arc": ("major", "minor"),
        "create_sketch": ("offset",),
        "create_sketch_by_three_points": ("op", "to"),
        "create_extrude": ("distance",),
        "create_fillet": ("radii",),
        "create_chamfer": ("distances",),
        "create_helix_sweep": ("pitch",),
    }
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
        '"': 2.54,
        "ft": 30.48,
        "foot": 30.48,
        "feet": 30.48,
    }
    _DIMENSION_CONSTANTS = {
        "pi": math.pi,
        "tau": math.tau,
        "e": math.e,
    }
    _DIMENSION_FUNCTIONS = {
        "sqrt": math.sqrt,
    }

    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        self.host = host
        self.port = port
        self._runtime_client: Any | None = None

    def load_json(self, json_path: str | Path) -> list[dict[str, Any]]:
        path = Path(json_path)
        return json.loads(path.read_text(encoding="utf-8"))

    def _get_runtime_client(self) -> Any:
        if self._runtime_client is None:
            from fusion360_tools.client.fusion360_client import Fusion360Client

            self._runtime_client = _ResponseCompatClient(
                Fusion360Client(f"http://{self.host}:{self.port}")
            )
        return self._runtime_client

    def close_runtime_client(self) -> None:
        client = self._runtime_client
        self._runtime_client = None
        if client is None:
            return
        try:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

    def find_nearly_collinear_three_point_arcs(
        self,
        source: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for op_index, item in enumerate(source):
            sketch = item.get("sketch") or {}
            if not isinstance(sketch, dict):
                continue
            for primitive_name, primitive_payload in sketch.items():
                if not (
                    isinstance(primitive_name, str)
                    and primitive_name.startswith("arc_")
                    and isinstance(primitive_payload, dict)
                ):
                    continue
                issue = _analyze_three_point_arc_payload(primitive_payload)
                if not _is_degenerate_three_point_arc_issue(issue):
                    continue
                issues.append(
                    {
                        "kind": "degenerate_three_point_arc",
                        "op_index": op_index,
                        "primitive": primitive_name,
                        **issue,
                    }
                )
        return issues

    def _normalized_line_name_for_arc(
        self,
        primitive_name: str,
        used_names: set[str],
    ) -> str:
        suffix = (
            primitive_name.split("_", 1)[1] if "_" in primitive_name else primitive_name
        )
        candidates = [f"line_{suffix}", f"line_{suffix}_norm"]
        for candidate in candidates:
            if candidate not in used_names:
                return candidate
        index = 2
        while True:
            candidate = f"line_{suffix}_norm_{index}"
            if candidate not in used_names:
                return candidate
            index += 1

    def _rewrite_normalized_arc_entity_reference(
        self,
        entity_name: str,
        arc_renames: dict[str, str],
    ) -> tuple[str | None, str | None]:
        base_name, dot, suffix = str(entity_name).partition(".")
        replacement = arc_renames.get(base_name)
        if replacement is None:
            return str(entity_name), None
        if dot and suffix not in {"start", "end"}:
            return None, base_name
        rewritten = replacement if not dot else f"{replacement}.{suffix}"
        return rewritten, base_name

    def _drop_reason_for_normalized_arc_constraint(
        self,
        normalized_type: str,
        touched_primitives: set[str],
        entities: list[str],
    ) -> str | None:
        if not touched_primitives:
            return None
        if any(
            str(entity).partition(".")[0] in touched_primitives
            and "." in str(entity)
            and str(entity).partition(".")[2] not in {"start", "end"}
            for entity in entities
        ):
            return "normalized_line_does_not_expose_requested_point"
        if normalized_type in {
            "diameter",
            "radius",
            "majorradius",
            "minorradius",
            "equal",
            "tangent",
            "concentric",
            "normal",
            "midpoint",
        }:
            return f"{normalized_type}_unsupported_after_arc_to_line_normalization"
        return None

    def _rebuild_constraint_entry(
        self,
        constraint_type: str,
        entities: list[str],
        value: float | str | None,
        extra: dict[str, Any] | None,
    ) -> Any:
        normalized_type = str(constraint_type).strip().lower()
        if value is None and not extra:
            if len(entities) == 1:
                return entities[0]
            return list(entities)

        if extra:
            metadata = dict(extra)
            if value is not None:
                value_key = self.VALUE_KEYS.get(normalized_type, ("value",))[0]
                metadata[value_key] = value
            return [*entities, metadata]

        return [*entities, value]

    def _denormalize_constraint_entries(self, entries: list[Any]) -> Any:
        if not entries:
            return None
        if len(entries) == 1:
            return entries[0]
        return entries

    def _rewrite_constraints_for_normalized_arcs(
        self,
        constraints: dict[str, Any] | None,
        arc_renames: dict[str, str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not isinstance(constraints, dict) or not constraints:
            return {}, []

        rewritten_constraints: dict[str, Any] = {}
        dropped_constraints: list[dict[str, Any]] = []
        for constraint_type, raw_entries in constraints.items():
            normalized_type = str(constraint_type).strip().lower()
            normalized_entries = self._normalize_constraint_entries(
                raw_entries,
                constraint_type=constraint_type,
            )
            rebuilt_entries: list[Any] = []

            for entry in normalized_entries:
                try:
                    entities, value, extra = self._parse_constraint_entry(
                        constraint_type,
                        entry,
                    )
                except Exception:
                    rebuilt_entries.append(entry)
                    continue

                rewritten_entities: list[str] = []
                touched_primitives: set[str] = set()
                unsupported_reference = False
                for entity_name in entities:
                    rewritten, touched_primitive = (
                        self._rewrite_normalized_arc_entity_reference(
                            entity_name,
                            arc_renames,
                        )
                    )
                    if touched_primitive is not None:
                        touched_primitives.add(touched_primitive)
                    if rewritten is None:
                        unsupported_reference = True
                        break
                    rewritten_entities.append(rewritten)

                if unsupported_reference:
                    dropped_constraints.append(
                        {
                            "constraint_type": str(constraint_type),
                            "entities": list(entities),
                            "reason": "normalized_line_does_not_expose_requested_point",
                            "affected_primitives": sorted(touched_primitives),
                        }
                    )
                    continue

                drop_reason = self._drop_reason_for_normalized_arc_constraint(
                    normalized_type,
                    touched_primitives,
                    entities,
                )
                if drop_reason is not None:
                    dropped_constraints.append(
                        {
                            "constraint_type": str(constraint_type),
                            "entities": list(entities),
                            "reason": drop_reason,
                            "affected_primitives": sorted(touched_primitives),
                        }
                    )
                    continue

                rebuilt_entries.append(
                    self._rebuild_constraint_entry(
                        constraint_type,
                        rewritten_entities,
                        value,
                        extra,
                    )
                )

            rebuilt_value = self._denormalize_constraint_entries(rebuilt_entries)
            if rebuilt_value is not None:
                rewritten_constraints[str(constraint_type)] = rebuilt_value

        return rewritten_constraints, dropped_constraints

    def normalize_degenerate_three_point_arcs(
        self,
        source: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        normalized_source: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []

        for op_index, item in enumerate(source):
            sketch = item.get("sketch") or {}
            if not isinstance(sketch, dict) or not sketch:
                normalized_source.append(item)
                continue

            used_names = {str(name) for name in sketch.keys()}
            normalized_sketch: dict[str, Any] = {}
            arc_renames: dict[str, str] = {}
            op_records: list[dict[str, Any]] = []

            for primitive_name, primitive_payload in sketch.items():
                if not (
                    isinstance(primitive_name, str)
                    and primitive_name.startswith("arc_")
                    and isinstance(primitive_payload, dict)
                ):
                    normalized_sketch[primitive_name] = primitive_payload
                    continue

                issue = _analyze_three_point_arc_payload(primitive_payload)
                if not _is_degenerate_three_point_arc_issue(issue):
                    normalized_sketch[primitive_name] = primitive_payload
                    continue

                normalized_name = self._normalized_line_name_for_arc(
                    primitive_name,
                    used_names,
                )
                used_names.add(normalized_name)
                replacement = {
                    "start": list(issue.get("start") or []),
                    "end": list(issue.get("end") or []),
                }
                normalized_sketch[normalized_name] = replacement
                arc_renames[primitive_name] = normalized_name
                op_records.append(
                    {
                        "kind": "degenerate_three_point_arc",
                        "op_index": op_index,
                        "primitive": primitive_name,
                        "normalized_to": normalized_name,
                        "replacement": replacement,
                        "dropped_constraints": [],
                        **issue,
                    }
                )

            if not arc_renames:
                normalized_source.append(item)
                continue

            normalized_item = dict(item)
            normalized_item["sketch"] = normalized_sketch
            rewritten_constraints, dropped_constraints = (
                self._rewrite_constraints_for_normalized_arcs(
                    item.get("constraints"),
                    arc_renames,
                )
            )
            if "constraints" in item or rewritten_constraints:
                normalized_item["constraints"] = rewritten_constraints

            if dropped_constraints:
                for dropped in dropped_constraints:
                    affected = set(dropped.get("affected_primitives") or [])
                    for record in op_records:
                        if record["primitive"] in affected:
                            record["dropped_constraints"].append(
                                {
                                    "constraint_type": dropped["constraint_type"],
                                    "entities": list(dropped["entities"]),
                                    "reason": dropped["reason"],
                                }
                            )

            normalized_source.append(normalized_item)
            records.extend(op_records)

        return normalized_source, records

    def build_export_commands(
        self,
        payload: list[dict[str, Any]],
        output_path: str | Path,
        *,
        export_method: str = "export_step",
        export_label: str | None = None,
    ) -> list[Command]:
        if export_method not in {"export_step", "export_f3d"}:
            raise ValueError(f"Unsupported export method: {export_method}")
        commands: list[Command] = [Command(method="clear", label="clear")]
        for index, item in enumerate(payload, start=1):
            commands.extend(self._build_operation(item, index, payload=payload))
        commands.append(
            Command(
                method=export_method,
                kwargs={"filepath": str(Path(output_path).resolve())},
                label=export_label or export_method,
            )
        )
        return commands

    def build_commands(
        self, payload: list[dict[str, Any]], step_path: str | Path
    ) -> list[Command]:
        return self.build_export_commands(
            payload,
            step_path,
            export_method="export_step",
            export_label="export_step",
        )

    def render_script(self, commands: list[Command], step_path: str | Path) -> str:
        _ = str(Path(step_path).resolve())
        lines = [
            "from export.converter import FusionScriptSession",
            "",
            f"cad = FusionScriptSession(host={self._render_literal(self.host)}, port={int(self.port)})",
            "refs = {}",
            "",
        ]
        for command in commands:
            lines.extend(self._render_modeling_command(command))
        return "\n".join(lines)

    def render_local_sketch_script(
        self,
        payload: list[dict[str, Any]],
        step_path: str | Path,
    ) -> str:
        commands: list[Command] = [Command(method="clear", label="clear")]
        for index, item in enumerate(payload, start=1):
            commands.extend(
                self._build_local_script_operation(item, index, payload=payload)
            )
        commands.append(
            Command(
                method="export_step",
                kwargs={"filepath": str(Path(step_path).resolve())},
                label="export_step",
            )
        )
        lines = [
            "from export.converter import FusionScriptSession",
            "",
            f"cad = FusionScriptSession(host={self._render_literal(self.host)}, port={int(self.port)})",
            "refs = {}",
            "",
        ]
        for command in commands:
            lines.extend(self._render_modeling_command(command))
        return "\n".join(lines)

    def validate_local_sketch_operations(
        self,
        payload: list[dict[str, Any]],
        selected_operations: set[int] | None = None,
        tol: float = 1e-8,
    ) -> list[dict[str, Any]]:
        selected = set(selected_operations) if selected_operations is not None else None
        checks: list[dict[str, Any]] = []
        seen: set[int] = set()

        for index, item in enumerate(payload, start=1):
            if selected is not None and index not in selected:
                continue
            seen.add(index)

            op_name = str(item.get("operation", "")).strip()
            if op_name in {"Fillet", "Chamfer"}:
                checks.append(
                    {
                        "operation_index": index,
                        "operation": op_name,
                        "checked": False,
                        "ok": True,
                        "issues": [],
                        "skip_reason": "Operation does not create a sketch.",
                    }
                )
                continue

            coord = CoordinateSystem.from_payload(item.get("coordinate_system"))
            issues: list[dict[str, Any]] = []
            sketch_commands = self._build_sketch_commands(coord, index)
            for sketch_command in sketch_commands:
                issues.extend(
                    self._validate_sketch_command_consistency(
                        coord, sketch_command, tol=tol
                    )
                )

            sketch_payload = item.get("sketch") or {}
            for primitive_name, primitive_payload in sketch_payload.items():
                world_command = self._build_primitive_command(
                    coord, primitive_name, primitive_payload
                )
                local_command = self._build_local_primitive_command(
                    coord, primitive_name, primitive_payload
                )
                issues.extend(
                    self._validate_command_coordinate_consistency(
                        coord=coord,
                        world_command=world_command,
                        local_command=local_command,
                        context=primitive_name,
                        tol=tol,
                    )
                )

            constraints = item.get("constraints") or {}
            world_constraints = self._build_constraint_commands(
                constraints, coord, sketch_payload
            )
            local_constraints = self._build_local_constraint_commands(
                constraints, coord, sketch_payload
            )
            if len(world_constraints) != len(local_constraints):
                issues.append(
                    {
                        "scope": "constraints",
                        "message": (
                            "Constraint command count mismatch between world and local render paths: "
                            f"{len(world_constraints)} vs {len(local_constraints)}"
                        ),
                    }
                )
            else:
                for constraint_index, (world_command, local_command) in enumerate(
                    zip(world_constraints, local_constraints),
                    start=1,
                ):
                    issues.extend(
                        self._validate_command_coordinate_consistency(
                            coord=coord,
                            world_command=world_command,
                            local_command=local_command,
                            context=f"constraint_{constraint_index}",
                            tol=tol,
                        )
                    )

            checks.append(
                {
                    "operation_index": index,
                    "operation": op_name,
                    "checked": True,
                    "ok": not issues,
                    "issues": issues,
                }
            )

        if selected is not None:
            for missing_index in sorted(selected - seen):
                checks.append(
                    {
                        "operation_index": missing_index,
                        "operation": None,
                        "checked": False,
                        "ok": False,
                        "issues": [
                            {
                                "scope": "operation",
                                "message": "Operation index is out of range for the cleaned JSON payload.",
                            }
                        ],
                    }
                )

        return sorted(checks, key=lambda item: int(item["operation_index"]))

    def _build_batch_commands_payload(
        self, commands: list[Command]
    ) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for command in commands:
            payload.append(
                {
                    "method": command.method,
                    "kwargs": _serialize_script_value(
                        self._normalize_fusion_kwargs(command.method, command.kwargs)
                    ),
                    "capture": command.capture,
                    "register": dict(command.register),
                    "label": command.label,
                }
            )
        return payload

    def _missing_runtime_dependencies(
        self,
        command: Command,
        missing_entity_refs: set[str],
    ) -> list[str]:
        if not missing_entity_refs:
            return []
        return sorted(
            name
            for name in _collect_entity_ref_names(command.kwargs)
            if name in missing_entity_refs
        )

    def _append_missing_dependency_skip(
        self,
        *,
        command: Command,
        missing_entities: list[str],
        skipped_constraints: list[dict[str, Any]],
        skip_prefix: str,
    ) -> None:
        skip_record = {
            "kind": "missing_runtime_dependency",
            "label": command.label,
            "method": command.method,
            "missing_entities": missing_entities,
            "kwargs": _serialize_script_value(command.kwargs),
            "error": "Skipped because a required runtime entity was not created.",
        }
        skipped_constraints.append(skip_record)
        command_name = command.label or command.method
        print(
            f"{skip_prefix} command ignored after earlier recoverable skip: "
            f"{command_name} depends on {missing_entities}"
        )

    def _record_recoverable_execute_error(
        self,
        *,
        command: Command,
        kwargs: dict[str, Any],
        error: Exception,
        skipped_constraints: list[dict[str, Any]],
        skip_prefix: str,
    ) -> bool:
        display_kwargs = _to_displayable_value(kwargs)
        if command.method == "add_constraint" and _is_sketch_overconstraint_error(
            error
        ):
            skip_record = {
                "kind": "over_constrained_constraint",
                "label": command.label,
                "summary": _constraint_skip_summary(command.kwargs),
                "kwargs": _serialize_script_value(command.kwargs),
                "resolved_kwargs": display_kwargs,
                "error": str(error),
            }
            skipped_constraints.append(skip_record)
            print(
                f"{skip_prefix} over-constrained constraint ignored: "
                f"{json.dumps(skip_record['summary'], ensure_ascii=False, sort_keys=True)}"
            )
            return True
        if (
            command.method == "add_constraint"
            and _is_invalid_constraint_reference_error(error)
        ):
            skip_record = {
                "kind": "invalid_constraint_reference",
                "label": command.label,
                "summary": _constraint_skip_summary(command.kwargs),
                "kwargs": _serialize_script_value(command.kwargs),
                "resolved_kwargs": display_kwargs,
                "error": str(error),
            }
            skipped_constraints.append(skip_record)
            print(
                f"{skip_prefix} invalid constraint reference ignored: "
                f"{json.dumps(skip_record['summary'], ensure_ascii=False, sort_keys=True)}"
            )
            return True
        if command.method == "add_nurbs" and _is_invalid_sketch_primitive_error(error):
            skipped_constraints.append(
                {
                    "kind": "invalid_sketch_primitive",
                    "label": command.label,
                    "kwargs": _serialize_script_value(command.kwargs),
                    "resolved_kwargs": display_kwargs,
                    "error": str(error),
                }
            )
            print(f"{skip_prefix} invalid sketch primitive ignored: {command.label}")
            return True
        if command.method in {"create_fillet", "create_chamfer"} and (
            _is_missing_edge_feature_error(error)
            or _is_edge_feature_geometry_failure_error(error)
        ):
            skipped_constraints.append(
                {
                    "kind": "unresolved_edge_feature",
                    "label": command.label,
                    "kwargs": display_kwargs,
                    "error": str(error),
                }
            )
            print(f"{skip_prefix} unresolved edge feature ignored: {command.label}")
            return True
        if (
            command.method == "create_extrude"
            and kwargs.get("operation") in {"Join", "Cut", "Intersect"}
            and (
                _is_missing_boolean_target_error(error)
                or _is_boolean_geometry_failure_error(error)
            )
        ):
            kind = (
                "non_intersecting_boolean_extrude"
                if _is_missing_boolean_target_error(error)
                else "failed_boolean_extrude"
            )
            skipped_constraints.append(
                {
                    "kind": kind,
                    "label": command.label,
                    "kwargs": display_kwargs,
                    "error": str(error),
                }
            )
            print(f"{skip_prefix} recoverable boolean extrude ignored: {command.label}")
            return True
        if command.method == "create_extrude" and _is_empty_sketch_profile_error(error):
            skipped_constraints.append(
                {
                    "kind": "empty_sketch_extrude",
                    "label": command.label,
                    "kwargs": display_kwargs,
                    "error": str(error),
                }
            )
            print(f"{skip_prefix} empty sketch extrude ignored: {command.label}")
            return True
        return False

    def _register_command_entities(
        self,
        *,
        command: Command,
        last_response: Any,
        entities: dict[str, Any],
        missing_entity_refs: set[str],
    ) -> None:
        if not command.capture or not isinstance(last_response, dict):
            return
        for key, response_key in command.register.items():
            if response_key in last_response:
                entities[key] = last_response[response_key]
                missing_entity_refs.discard(key)

    def _execute_commands_individually(
        self,
        commands: list[Command],
        *,
        context: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_runtime_client()
        entities: dict[str, Any] = {}
        last_response: Any = None
        skipped_constraints: list[dict[str, Any]] = []
        missing_entity_refs: set[str] = set()
        skip_prefix = f"[skip][{context}]" if context else "[skip]"

        for command in commands:
            missing_entities = self._missing_runtime_dependencies(
                command, missing_entity_refs
            )
            if missing_entities:
                self._append_missing_dependency_skip(
                    command=command,
                    missing_entities=missing_entities,
                    skipped_constraints=skipped_constraints,
                    skip_prefix=skip_prefix,
                )
                continue

            kwargs = self._resolve_runtime_value(command.kwargs, entities)
            kwargs = self._normalize_fusion_kwargs(command.method, kwargs)
            target = getattr(client, command.method)
            try:
                last_response = _normalize_response(target(**kwargs))
            except Exception as ex:
                if self._record_recoverable_execute_error(
                    command=command,
                    kwargs=kwargs,
                    error=ex,
                    skipped_constraints=skipped_constraints,
                    skip_prefix=skip_prefix,
                ):
                    missing_entity_refs.update(command.register.keys())
                    continue
                raise
            self._register_command_entities(
                command=command,
                last_response=last_response,
                entities=entities,
                missing_entity_refs=missing_entity_refs,
            )

        return {
            "entities": entities,
            "last_response": last_response,
            "skipped_constraints": skipped_constraints,
        }

    def _execute_commands_batched(
        self,
        commands: list[Command],
        *,
        context: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_runtime_client()
        entities: dict[str, Any] = {}
        last_response: Any = None
        skipped_constraints: list[dict[str, Any]] = []
        missing_entity_refs: set[str] = set()
        skip_prefix = f"[skip][{context}]" if context else "[skip]"
        next_index = 0

        while next_index < len(commands):
            while next_index < len(commands):
                missing_entities = self._missing_runtime_dependencies(
                    commands[next_index],
                    missing_entity_refs,
                )
                if not missing_entities:
                    break
                self._append_missing_dependency_skip(
                    command=commands[next_index],
                    missing_entities=missing_entities,
                    skipped_constraints=skipped_constraints,
                    skip_prefix=skip_prefix,
                )
                next_index += 1

            if next_index >= len(commands):
                break

            batch_payload = self._build_batch_commands_payload(commands[next_index:])
            batch_response = _normalize_response(
                client.run_batch(batch_payload, entities=dict(entities))
            )
            if not isinstance(batch_response, dict):
                raise RuntimeError("Fusion run_batch returned a non-dict response.")

            returned_entities = batch_response.get("entities")
            if isinstance(returned_entities, dict):
                entities = dict(returned_entities)
            if "last_response" in batch_response:
                last_response = batch_response.get("last_response")

            if batch_response.get("ok", True):
                return {
                    "entities": entities,
                    "last_response": last_response,
                    "skipped_constraints": skipped_constraints,
                }

            failed_index = batch_response.get("failed_index")
            if not isinstance(failed_index, int) or failed_index < 0:
                raise RuntimeError(
                    f"Fusion run_batch returned an invalid failed_index: {failed_index!r}"
                )

            absolute_index = next_index + failed_index
            if absolute_index >= len(commands):
                raise RuntimeError(
                    f"Fusion run_batch failed at out-of-range index {absolute_index}."
                )

            command = commands[absolute_index]
            resolved_kwargs = batch_response.get("resolved_kwargs")
            if not isinstance(resolved_kwargs, dict):
                resolved_kwargs = {}
            error = RuntimeError(
                str(batch_response.get("error") or "Fusion batch execution failed.")
            )

            if self._record_recoverable_execute_error(
                command=command,
                kwargs=resolved_kwargs,
                error=error,
                skipped_constraints=skipped_constraints,
                skip_prefix=skip_prefix,
            ):
                missing_entity_refs.update(command.register.keys())
                next_index = absolute_index + 1
                continue

            raise error

        return {
            "entities": entities,
            "last_response": last_response,
            "skipped_constraints": skipped_constraints,
        }

    def execute_commands(
        self, commands: list[Command], context: str | None = None
    ) -> dict[str, Any]:
        try:
            return self._execute_commands_batched(commands, context=context)
        except Exception as ex:
            if _is_batch_unsupported_error(ex):
                return self._execute_commands_individually(commands, context=context)
            raise

    def convert_file(
        self,
        json_path: str | Path,
        script_path: str | Path,
        step_path: str | Path,
        execute: bool = False,
        execute_context: str | None = None,
    ) -> dict[str, Any]:
        payload = self.load_json(json_path)
        commands = self.build_commands(payload, step_path)
        script_text = self.render_script(commands, step_path)
        script_path = Path(script_path)
        step_path = Path(step_path)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        step_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_text, encoding="utf-8")
        result = {
            "json_path": str(Path(json_path).resolve()),
            "script_path": str(script_path.resolve()),
            "step_path": str(step_path.resolve()),
            "command_count": len(commands),
        }
        if execute:
            result["execution"] = self.execute_commands(
                commands, context=execute_context or Path(json_path).name
            )
        return result

    def _build_local_script_operation(
        self,
        item: dict[str, Any],
        index: int,
        payload: list[dict[str, Any]] | None = None,
    ) -> list[Command]:
        op_name = str(item.get("operation", "")).strip()
        if not op_name:
            raise ValueError(f"Operation #{index} is missing the operation field.")
        if op_name in {"Fillet", "Chamfer"}:
            return self._build_edge_feature(item, index)

        coord = CoordinateSystem.from_payload(item.get("coordinate_system"))
        commands: list[Command] = list(self._build_sketch_commands(coord, index))

        sketch_payload = item.get("sketch") or {}
        for primitive_name, primitive_payload in sketch_payload.items():
            commands.append(
                self._build_local_primitive_command(
                    coord, primitive_name, primitive_payload
                )
            )

        constraints = item.get("constraints") or {}
        commands.extend(
            self._build_local_constraint_commands(constraints, coord, sketch_payload)
        )
        commands.extend(
            self._build_solid_operation(item, op_name, index, payload=payload)
        )
        return commands

    def _build_operation(
        self,
        item: dict[str, Any],
        index: int,
        payload: list[dict[str, Any]] | None = None,
    ) -> list[Command]:
        op_name = str(item.get("operation", "")).strip()
        if not op_name:
            raise ValueError(f"Operation #{index} is missing the operation field.")
        if op_name in {"Fillet", "Chamfer"}:
            return self._build_edge_feature(item, index)

        coord = CoordinateSystem.from_payload(item.get("coordinate_system"))
        commands: list[Command] = list(self._build_sketch_commands(coord, index))

        sketch_payload = item.get("sketch") or {}
        for primitive_name, primitive_payload in sketch_payload.items():
            commands.append(
                self._build_primitive_command(coord, primitive_name, primitive_payload)
            )

        constraints = item.get("constraints") or {}
        commands.extend(
            self._build_constraint_commands(constraints, coord, sketch_payload)
        )
        commands.extend(
            self._build_solid_operation(item, op_name, index, payload=payload)
        )
        return commands

    def _build_primitive_command(
        self,
        coord: CoordinateSystem,
        primitive_name: str,
        payload: dict[str, Any],
    ) -> Command:
        primitive_kind = self._primitive_kind(primitive_name)
        capture = f"resp_{primitive_name}"
        if primitive_kind == "line":
            kwargs = {
                "startPoint": self._transform_point(coord, payload["start"]),
                "endPoint": self._transform_point(coord, payload["end"]),
            }
            register = {
                primitive_name: "line_id",
                f"{primitive_name}.start": "start_point_id",
                f"{primitive_name}.end": "end_point_id",
            }
            return Command(
                "add_line",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "circle":
            kwargs = {
                "centerPoint": self._transform_point(coord, payload["center"]),
                "radius": float(payload["radius"]),
            }
            register = {
                primitive_name: "circle_id",
                f"{primitive_name}.center": "center_point_id",
            }
            return Command(
                "add_circle",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "arc":
            middle = payload.get("middle")
            if middle is None:
                raise ValueError(f"{primitive_name} is missing the middle point.")
            kwargs = {
                "startPoint": self._transform_point(coord, payload["start"]),
                "alongPoint": self._transform_point(coord, middle),
                "endPoint": self._transform_point(coord, payload["end"]),
            }
            register = {
                primitive_name: "arc_id",
                f"{primitive_name}.start": "start_point_id",
                f"{primitive_name}.end": "end_point_id",
                f"{primitive_name}.center": "center_point_id",
                f"{primitive_name}.middle": "mid_point_id",
            }
            return Command(
                "add_arc",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "ellipse":
            center = payload["center"]
            major = float(payload["major"])
            minor = float(payload["minor"])
            angle = float(payload["angle"])
            major_axis_point_local = self._ellipse_major_axis_point(
                center, major, angle
            )
            pass_point_local = self._ellipse_pass_point(center, minor, angle)
            kwargs = {
                "centerPoint": self._transform_point(coord, center),
                "major": major,
                "minor": minor,
                "angle": angle,
                "majorAxisPoint": self._transform_point(coord, major_axis_point_local),
                "passPoint": self._transform_point(coord, pass_point_local),
            }
            register = {
                primitive_name: "ellipse_id",
                f"{primitive_name}.center": "center_point_id",
            }
            return Command(
                "add_ellipse",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "elliptical_arc":
            major = float(payload["major"])
            minor = float(payload["minor"])
            angle = float(payload["angle"])
            solution = self._solve_elliptical_arc(
                start_xy=self._ensure_point2d(payload["start"]),
                end_xy=self._ensure_point2d(payload["end"]),
                major=major,
                minor=minor,
                angle_deg=angle,
                large_arc=bool(payload.get("large_arc", False)),
                sweep=bool(payload.get("sweep", True)),
            )
            kwargs = {
                "startPoint": self._transform_point(coord, payload["start"]),
                "endPoint": self._transform_point(coord, payload["end"]),
                "major": major,
                "minor": minor,
                "angle": angle,
                "large_arc": bool(payload.get("large_arc", False)),
                "sweep": bool(payload.get("sweep", True)),
                "centerPoint": self._transform_point(coord, solution["center_local"]),
                "majorAxisVector": self._transform_vector(
                    coord, solution["major_axis_local"]
                ),
                "minorAxisVector": self._transform_vector(
                    coord, solution["minor_axis_local"]
                ),
                "startAngle": solution["theta_start"],
                "sweepAngle": solution["sweep_angle"],
            }
            register = {
                primitive_name: "elliptical_arc_id",
                f"{primitive_name}.start": "start_point_id",
                f"{primitive_name}.end": "end_point_id",
                f"{primitive_name}.center": "center_point_id",
            }
            return Command(
                "add_elliptical_arc",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "nurbs":
            controls = [
                self._transform_point(coord, point) for point in payload["controls"]
            ]
            periodic = bool(payload.get("periodic", payload.get("isPeriodic", False)))
            weights = payload.get("weights")
            knots = payload.get("knots")
            kwargs = {
                "degree": int(payload["degree"]),
                "periodic": periodic,
                "controls": controls,
            }
            if weights is not None:
                kwargs["weights"] = [float(value) for value in weights]
            if knots is not None:
                kwargs["knots"] = [float(value) for value in knots]
            register = {
                primitive_name: "nurbs_id",
                f"{primitive_name}.start": "start_point_id",
                f"{primitive_name}.end": "end_point_id",
            }
            return Command(
                "add_nurbs",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        raise ValueError(f"Unsupported primitive type: {primitive_kind}")

    def _build_local_primitive_command(
        self,
        coord: CoordinateSystem,
        primitive_name: str,
        payload: dict[str, Any],
    ) -> Command:
        primitive_kind = self._primitive_kind(primitive_name)
        capture = f"resp_{primitive_name}"
        if primitive_kind == "line":
            kwargs = {
                "startPoint": self._script_local_point(payload["start"]),
                "endPoint": self._script_local_point(payload["end"]),
            }
            register = {
                primitive_name: "line_id",
                f"{primitive_name}.start": "start_point_id",
                f"{primitive_name}.end": "end_point_id",
            }
            return Command(
                "add_line",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "circle":
            kwargs = {
                "centerPoint": self._script_local_point(payload["center"]),
                "radius": float(payload["radius"]),
            }
            register = {
                primitive_name: "circle_id",
                f"{primitive_name}.center": "center_point_id",
            }
            return Command(
                "add_circle",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "arc":
            middle = payload.get("middle")
            if middle is None:
                raise ValueError(f"{primitive_name} is missing the middle point.")
            kwargs = {
                "startPoint": self._script_local_point(payload["start"]),
                "alongPoint": self._script_local_point(middle),
                "endPoint": self._script_local_point(payload["end"]),
            }
            register = {
                primitive_name: "arc_id",
                f"{primitive_name}.start": "start_point_id",
                f"{primitive_name}.end": "end_point_id",
                f"{primitive_name}.center": "center_point_id",
                f"{primitive_name}.middle": "mid_point_id",
            }
            return Command(
                "add_arc",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "ellipse":
            center = payload["center"]
            major = float(payload["major"])
            minor = float(payload["minor"])
            angle = float(payload["angle"])
            major_axis_point_local = self._ellipse_major_axis_point(
                center, major, angle
            )
            pass_point_local = self._ellipse_pass_point(center, minor, angle)
            kwargs = {
                "centerPoint": self._script_local_point(center),
                "major": major,
                "minor": minor,
                "angle": angle,
                "majorAxisPoint": self._script_local_point(major_axis_point_local),
                "passPoint": self._script_local_point(pass_point_local),
            }
            register = {
                primitive_name: "ellipse_id",
                f"{primitive_name}.center": "center_point_id",
            }
            return Command(
                "add_ellipse",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "elliptical_arc":
            major = float(payload["major"])
            minor = float(payload["minor"])
            angle = float(payload["angle"])
            solution = self._solve_elliptical_arc(
                start_xy=self._ensure_point2d(payload["start"]),
                end_xy=self._ensure_point2d(payload["end"]),
                major=major,
                minor=minor,
                angle_deg=angle,
                large_arc=bool(payload.get("large_arc", False)),
                sweep=bool(payload.get("sweep", True)),
            )
            kwargs = {
                "startPoint": self._script_local_point(payload["start"]),
                "endPoint": self._script_local_point(payload["end"]),
                "major": major,
                "minor": minor,
                "angle": angle,
                "large_arc": bool(payload.get("large_arc", False)),
                "sweep": bool(payload.get("sweep", True)),
                "centerPoint": self._script_local_point(solution["center_local"]),
                "majorAxisVector": self._script_local_vector(
                    solution["major_axis_local"]
                ),
                "minorAxisVector": self._script_local_vector(
                    solution["minor_axis_local"]
                ),
                "startAngle": solution["theta_start"],
                "sweepAngle": solution["sweep_angle"],
            }
            register = {
                primitive_name: "elliptical_arc_id",
                f"{primitive_name}.start": "start_point_id",
                f"{primitive_name}.end": "end_point_id",
                f"{primitive_name}.center": "center_point_id",
            }
            return Command(
                "add_elliptical_arc",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        if primitive_kind == "nurbs":
            controls = [
                self._script_local_point(point) for point in payload["controls"]
            ]
            periodic = bool(payload.get("periodic", payload.get("isPeriodic", False)))
            weights = payload.get("weights")
            knots = payload.get("knots")
            kwargs = {
                "degree": int(payload["degree"]),
                "periodic": periodic,
                "controls": controls,
            }
            if weights is not None:
                kwargs["weights"] = [float(value) for value in weights]
            if knots is not None:
                kwargs["knots"] = [float(value) for value in knots]
            register = {
                primitive_name: "nurbs_id",
                f"{primitive_name}.start": "start_point_id",
                f"{primitive_name}.end": "end_point_id",
            }
            return Command(
                "add_nurbs",
                kwargs=kwargs,
                capture=capture,
                register=register,
                label=primitive_name,
            )

        raise ValueError(f"Unsupported primitive type: {primitive_kind}")

    def _build_constraint_commands(
        self,
        constraints: dict[str, Any],
        coord: CoordinateSystem | None = None,
        sketch_payload: dict[str, Any] | None = None,
    ) -> list[Command]:
        commands: list[Command] = []
        records = self._collect_constraint_records(
            constraints,
            coord=coord,
            sketch_payload=sketch_payload,
        )
        for sequence_index, record in enumerate(records, start=1):
            commands.extend(
                self._build_constraint_command_sequence(
                    constraint_type=record["constraint_type"],
                    normalized_type=record["normalized_type"],
                    entities=record["entities"],
                    value=record["value"],
                    extra=record["extra"],
                    sequence_index=sequence_index,
                )
            )
        return commands

    def _build_local_constraint_commands(
        self,
        constraints: dict[str, Any],
        coord: CoordinateSystem | None = None,
        sketch_payload: dict[str, Any] | None = None,
    ) -> list[Command]:
        commands: list[Command] = []
        records = self._collect_constraint_records(
            constraints,
            coord=coord,
            sketch_payload=sketch_payload,
        )
        for sequence_index, record in enumerate(records, start=1):
            commands.extend(
                self._build_constraint_command_sequence(
                    constraint_type=record["constraint_type"],
                    normalized_type=record["normalized_type"],
                    entities=record["entities"],
                    value=record["value"],
                    extra=record["extra"],
                    sequence_index=sequence_index,
                )
            )
        return commands

    def _collect_constraint_records(
        self,
        constraints: dict[str, Any],
        coord: CoordinateSystem | None = None,
        sketch_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for constraint_type, raw_entries in constraints.items():
            normalized_type = constraint_type.strip().lower()
            for entry in self._normalize_constraint_entries(
                raw_entries, constraint_type=constraint_type
            ):
                entities, value, extra = self._parse_constraint_entry(
                    constraint_type, entry
                )
                extra = self._augment_constraint_extra_for_local_axes(
                    normalized_type,
                    coord,
                    extra,
                )
                extra = self._augment_distance_extra_for_concentric_curves(
                    normalized_type,
                    entities,
                    constraints,
                    sketch_payload,
                    extra,
                )
                for entity_group in self._split_axis_line_constraint_entities(
                    normalized_type,
                    entities,
                ):
                    records.append(
                        {
                            "constraint_type": constraint_type,
                            "normalized_type": normalized_type,
                            "entities": entity_group,
                            "value": value,
                            "extra": extra,
                        }
                    )
        return self._reorder_curve_size_resolvers_before_minimum_distances(
            records,
            sketch_payload=sketch_payload,
        )

    def _reorder_curve_size_resolvers_before_minimum_distances(
        self,
        records: list[dict[str, Any]],
        *,
        sketch_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if len(records) <= 1:
            return list(records)

        reordered: list[dict[str, Any]] = []
        pending_minimum_distances: list[dict[str, Any]] = []
        pending_curve_size_resolvers: list[dict[str, Any]] = []

        for record in records:
            if self._constraint_record_is_curve_tangent_minimum_distance(
                record,
                sketch_payload=sketch_payload,
            ):
                pending_minimum_distances.append(record)
                continue

            if (
                pending_minimum_distances
                and self._constraint_record_is_curve_size_resolver(
                    record,
                    sketch_payload=sketch_payload,
                )
            ):
                pending_curve_size_resolvers.append(record)
                continue

            if pending_minimum_distances:
                reordered.extend(pending_curve_size_resolvers)
                reordered.extend(pending_minimum_distances)
                pending_minimum_distances = []
                pending_curve_size_resolvers = []

            reordered.append(record)

        if pending_minimum_distances:
            reordered.extend(pending_curve_size_resolvers)
            reordered.extend(pending_minimum_distances)

        return reordered

    def _constraint_record_is_curve_tangent_minimum_distance(
        self,
        record: dict[str, Any],
        *,
        sketch_payload: dict[str, Any] | None = None,
    ) -> bool:
        if str(record.get("normalized_type") or "").strip().lower() != "distance":
            return False
        if self._normalize_distance_direction(record.get("extra")) != "MINIMUM":
            return False
        entities = record.get("entities")
        if not isinstance(entities, list) or len(entities) != 2:
            return False
        return any(
            self._entity_name_is_arc_or_circle(name, sketch_payload)
            for name in entities
        )

    def _constraint_record_is_curve_size_resolver(
        self,
        record: dict[str, Any],
        *,
        sketch_payload: dict[str, Any] | None = None,
    ) -> bool:
        normalized_type = str(record.get("normalized_type") or "").strip().lower()
        entities = record.get("entities")
        if not isinstance(entities, list) or not entities:
            return False

        if normalized_type in {"diameter", "radius"} and len(entities) == 1:
            return self._entity_name_is_arc_or_circle(entities[0], sketch_payload)

        if normalized_type == "equal" and len(entities) == 2:
            return all(
                self._entity_name_is_arc_or_circle(name, sketch_payload)
                for name in entities
            )

        return False

    def _build_constraint_command_sequence(
        self,
        *,
        constraint_type: str,
        normalized_type: str,
        entities: list[str],
        value: Any,
        extra: dict[str, Any] | None,
        sequence_index: int,
    ) -> list[Command]:
        kwargs: dict[str, Any] = {
            "type": constraint_type,
            "entities": [EntityRef(entity_name) for entity_name in entities],
        }
        if value is not None:
            kwargs["value"] = value
        if extra:
            kwargs["extra"] = extra

        follow_up_expression = self._minimum_distance_follow_up_expression(
            normalized_type=normalized_type,
            value=value,
            extra=extra,
        )
        if follow_up_expression is None:
            return [
                Command(method="add_constraint", kwargs=kwargs, label=constraint_type)
            ]

        capture = f"resp_constraint_{normalized_type}_{sequence_index}"
        dimension_ref = f"__constraint_id_{normalized_type}_{sequence_index}"
        return [
            Command(
                method="add_constraint",
                kwargs=kwargs,
                capture=capture,
                register={dimension_ref: "constraint_id"},
                label=constraint_type,
            ),
            Command(
                method="set_dimension_expression",
                kwargs={
                    "dimension_id": EntityRef(dimension_ref),
                    "expression": follow_up_expression,
                },
                label=f"{constraint_type}_post_create_expression",
            ),
        ]

    def _minimum_distance_follow_up_expression(
        self,
        *,
        normalized_type: str,
        value: Any,
        extra: dict[str, Any] | None,
    ) -> str | None:
        if normalized_type != "distance":
            return None
        if self._normalize_distance_direction(extra) != "MINIMUM":
            return None
        return self._normalize_negative_minimum_distance_expression(value)

    def _normalize_distance_direction(self, extra: dict[str, Any] | None) -> str | None:
        if not isinstance(extra, dict):
            return None
        direction = extra.get("direction")
        if not isinstance(direction, str):
            return None
        normalized = direction.strip().upper()
        return normalized or None

    def _normalize_negative_minimum_distance_expression(self, value: Any) -> str | None:
        if isinstance(value, (int, float)):
            numeric_value = float(value)
            if numeric_value >= 0:
                return None
            return str(
                self._normalize_constraint_value_for_fusion(
                    "distance", abs(numeric_value)
                )
            )

        if not isinstance(value, str):
            return None
        expression = value.strip()
        if not expression:
            return None

        evaluation = self._evaluate_length_expression_cm(expression)
        if evaluation is None:
            return None
        value_cm = float(evaluation[0])
        if value_cm >= 0:
            return None

        if expression.startswith("-"):
            positive_expression = expression[1:].lstrip()
        else:
            normalized_expression = self._normalize_dimension_expression(
                "distance", expression
            )
            normalized_expression = (
                normalized_expression.strip()
                if isinstance(normalized_expression, str)
                else str(normalized_expression)
            )
            if normalized_expression.startswith("-"):
                positive_expression = normalized_expression[1:].lstrip()
            else:
                positive_expression = (
                    f"{self._format_dimension_scalar(abs(value_cm))} cm"
                )

        return str(
            self._normalize_constraint_value_for_fusion("distance", positive_expression)
        )

    def _split_axis_line_constraint_entities(
        self,
        normalized_type: str,
        entities: list[str],
    ) -> list[list[str]]:
        """Split multi-line Horizontal/Vertical entries into per-line commands.

        HistCAD sometimes stores many horizontal or vertical lines in a single
        constraint entry. Fusion applies those line constraints independently,
        so a single over-constrained line should not cause the whole source
        entry to be removed during cleanup.
        """
        if normalized_type not in {"horizontal", "vertical"}:
            return [entities]
        if len(entities) <= 1:
            return [entities]
        if not all(
            isinstance(entity, str) and "." not in entity and entity.startswith("line_")
            for entity in entities
        ):
            return [entities]
        return [[entity] for entity in entities]

    def _build_solid_operation(
        self,
        item: dict[str, Any],
        op_name: str,
        index: int,
        payload: list[dict[str, Any]] | None = None,
    ) -> list[Command]:
        if "pitch" in item and "turns" in item:
            return [
                Command(
                    method="create_helix_sweep",
                    kwargs={
                        "axis": self._normalize_axis(item["axis"]),
                        "pitch": float(item["pitch"]),
                        "turns": float(item["turns"]),
                        "handedness": str(item.get("handedness", "Right")),
                        "operation": op_name,
                    },
                    label=f"helix_{index}",
                )
            ]

        if "axis" in item and any(key in item for key in ("start", "end", "angle")):
            kwargs: dict[str, Any] = {
                "axis": self._normalize_axis(item["axis"]),
                "operation": op_name,
            }
            if "start" in item:
                kwargs["start"] = float(item["start"])
            if "end" in item:
                kwargs["end"] = float(item["end"])
            if "angle" in item and "start" not in item and "end" not in item:
                kwargs["angle"] = float(item["angle"])
            return [Command(method="revolve", kwargs=kwargs, label=f"revolve_{index}")]

        towards = float(item.get("towards", 0.0) or 0.0)
        opposite = float(item.get("opposite", 0.0) or 0.0)
        profile_mode = self._infer_extrude_profile_mode(
            payload,
            current_index=index,
            item=item,
        )
        return self._build_extrude_commands(
            towards,
            opposite,
            op_name,
            index,
            profile_mode=profile_mode,
        )

    def _build_extrude_commands(
        self,
        towards: float,
        opposite: float,
        op_name: str,
        index: int,
        profile_mode: str | None = None,
    ) -> list[Command]:
        commands: list[Command] = []
        if abs(towards) <= 1e-9 and abs(opposite) <= 1e-9:
            raise ValueError(
                f"Extrude operation #{index} has zero towards and opposite distances."
            )

        def _kwargs(distance: float, operation: str) -> dict[str, Any]:
            kwargs: dict[str, Any] = {"distance": distance, "operation": operation}
            if profile_mode is not None:
                kwargs["profile_mode"] = profile_mode
            return kwargs

        if abs(towards) > 1e-9 and abs(opposite) > 1e-9:
            if op_name == "Intersect":
                kwargs = _kwargs(towards, op_name)
                kwargs["opposite_distance"] = opposite
                return [
                    Command(
                        method="create_extrude",
                        kwargs=kwargs,
                        label=f"extrude_{index}",
                    )
                ]
            commands.append(
                Command(
                    method="create_extrude",
                    kwargs=_kwargs(towards, op_name),
                    label=f"extrude_{index}_towards",
                )
            )
            second_operation = "Join" if op_name == "NewBody" else op_name
            commands.append(
                Command(
                    method="create_extrude",
                    kwargs=_kwargs(-opposite, second_operation),
                    label=f"extrude_{index}_opposite",
                )
            )
            return commands

        if abs(towards) > 1e-9:
            return [
                Command(
                    method="create_extrude",
                    kwargs=_kwargs(towards, op_name),
                    label=f"extrude_{index}",
                )
            ]

        return [
            Command(
                method="create_extrude",
                kwargs=_kwargs(-opposite, op_name),
                label=f"extrude_{index}",
            )
        ]

    def _infer_extrude_profile_mode(
        self,
        payload: list[dict[str, Any]] | None,
        *,
        current_index: int,
        item: dict[str, Any],
    ) -> str | None:
        if not payload:
            return None
        signature = self._concentric_circle_sketch_signature(item)
        if signature is None or len(signature["radii"]) < 4:
            return None

        current_radii = set(signature["radii"])
        for later_item in payload[current_index:]:
            later_signature = self._concentric_circle_sketch_signature(later_item)
            if later_signature is None:
                continue
            if later_signature["coord_key"] != signature["coord_key"]:
                continue
            if later_signature["center"] != signature["center"]:
                continue
            later_radii = set(later_signature["radii"])
            if later_radii and later_radii < current_radii:
                return "outer_prefix_excluding_innermost"
        return None

    def _concentric_circle_sketch_signature(
        self, item: dict[str, Any]
    ) -> dict[str, Any] | None:
        sketch_payload = item.get("sketch")
        if not isinstance(sketch_payload, dict) or not sketch_payload:
            return None
        if not all(
            isinstance(name, str)
            and name.startswith("circle_")
            and isinstance(primitive, dict)
            for name, primitive in sketch_payload.items()
        ):
            return None

        centers: list[tuple[float, float]] = []
        radii: list[float] = []
        for primitive in sketch_payload.values():
            center = primitive.get("center")
            radius = primitive.get("radius")
            if not (
                isinstance(center, list)
                and len(center) >= 2
                and isinstance(radius, (int, float))
            ):
                return None
            centers.append((round(float(center[0]), 6), round(float(center[1]), 6)))
            radii.append(round(float(radius), 6))

        first_center = centers[0]
        if any(center != first_center for center in centers[1:]):
            return None

        coord = CoordinateSystem.from_payload(item.get("coordinate_system"))
        coord_key = (
            tuple(round(float(value), 6) for value in coord.euler_angles),
            tuple(round(float(value), 6) for value in coord.translation_vector),
        )
        return {
            "coord_key": coord_key,
            "center": first_center,
            "radii": tuple(sorted(radii, reverse=True)),
        }

    def _build_edge_feature(self, item: dict[str, Any], index: int) -> list[Command]:
        op_name = str(item["operation"])
        points = self._normalize_near_points(item.get("near_points"))
        if op_name == "Fillet":
            radii = self._expand_numeric_field(item["radius"], len(points), "radius")
            return [
                Command(
                    method="create_fillet",
                    kwargs={"points": points, "radii": radii},
                    label=f"fillet_{index}",
                )
            ]

        distance = item.get("distance", item.get("dist"))
        if distance is None:
            raise ValueError(f"Chamfer operation #{index} is missing dist/distance.")
        distances = self._expand_numeric_field(distance, len(points), "dist/distance")
        angles = self._expand_optional_numeric_field(
            item.get("angle"), len(points), "angle"
        )
        planes = self._expand_optional_vector3_field(
            item.get("plane"), len(points), "plane"
        )
        kwargs: dict[str, Any] = {"points": points, "distances": distances}
        if any(angle is not None for angle in angles):
            kwargs["angles"] = angles
        if any(plane is not None for plane in planes):
            kwargs["planes"] = planes
        return [
            Command(method="create_chamfer", kwargs=kwargs, label=f"chamfer_{index}")
        ]

    def _normalize_constraint_entries(
        self,
        raw_entries: Any,
        constraint_type: str | None = None,
    ) -> list[Any]:
        if isinstance(raw_entries, list):
            if not raw_entries:
                return []
            if self._looks_like_typed_single_constraint_entry(
                constraint_type, raw_entries
            ):
                return [raw_entries]
            if self._looks_like_single_constraint_entry(raw_entries):
                return [raw_entries]
            return list(raw_entries)
        return [raw_entries]

    def _parse_constraint_entry(
        self, constraint_type: str, entry: Any
    ) -> tuple[list[str], float | str | None, dict[str, Any] | None]:
        if isinstance(entry, str):
            return [entry], None, None
        if not isinstance(entry, list) or not entry:
            raise ValueError(f"Invalid constraint entry for {constraint_type}: {entry}")

        values = list(entry)
        normalized_type = constraint_type.strip().lower()
        metadata: dict[str, Any] | None = None
        trailing_value: float | str | None = None
        if isinstance(values[-1], dict):
            metadata = dict(values.pop())
        elif self._has_inline_dimension_value(normalized_type, values):
            trailing_value = self._coerce_constraint_value(values.pop())

        if normalized_type == "midpoint":
            values = self._expand_midpoint_entities(values)

        if not values or not all(isinstance(value, str) for value in values):
            raise ValueError(
                f"Constraint entities for {constraint_type} must be strings: {entry}"
            )

        value = trailing_value
        extra: dict[str, Any] | None = None
        if metadata is not None:
            value, extra = self._extract_constraint_value(constraint_type, metadata)
        return values, value, extra

    def _has_inline_dimension_value(
        self, normalized_type: str, values: list[Any]
    ) -> bool:
        if normalized_type not in self.DIMENSION_TYPES or not values:
            return False
        if not isinstance(values[-1], (int, float, str)):
            return False
        if normalized_type == "angle":
            return len(values) >= 3
        if normalized_type == "distance":
            return len(values) >= 3
        return len(values) >= 2

    def _coerce_constraint_value(self, value: Any) -> float | str | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        raise ValueError(f"Unsupported constraint value: {value}")

    def _extract_constraint_value(
        self, constraint_type: str, metadata: dict[str, Any]
    ) -> tuple[float | str | None, dict[str, Any] | None]:
        normalized_type = constraint_type.strip().lower()
        value = None
        keys = self.VALUE_KEYS.get(normalized_type, ())
        consumed_key = None
        for key in keys:
            if key in metadata:
                value = self._coerce_constraint_value(metadata[key])
                consumed_key = key
                break
        extra = {key: item for key, item in metadata.items() if key != consumed_key}
        return value, extra or None

    def _expand_midpoint_entities(self, values: list[Any]) -> list[Any]:
        if len(values) != 2 or not isinstance(values[0], str):
            return values
        trailing_points = values[1]
        if not (
            isinstance(trailing_points, list)
            and len(trailing_points) == 2
            and all(isinstance(item, str) for item in trailing_points)
        ):
            return values
        return [values[0], trailing_points[0], trailing_points[1]]

    def _primitive_kind(self, primitive_name: str) -> str:
        for prefix in ("elliptical_arc", "ellipse", "circle", "arc", "nurbs", "line"):
            if primitive_name.startswith(f"{prefix}_"):
                return prefix
        raise ValueError(f"Unsupported primitive name: {primitive_name}")

    # Fusion 360's sketch Y-axis direction for each standard plane type.
    # Vertical/Horizontal constraints reference the sketch's Y/X axis.
    _FUSION_PLANE_SKETCH_Y: dict[str, tuple[float, float, float]] = {
        "xy": (0.0, 1.0, 0.0),
        "xz": (0.0, 0.0, 1.0),
        "yz": (0.0, 1.0, 0.0),
    }
    _STANDARD_SKETCH_BASIS: dict[
        str,
        tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ],
    ] = {
        "xy": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "xz": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
        "yz": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
    }
    _STANDARD_PLANE_OFFSET_AXIS: dict[str, int] = {
        "xy": 2,
        "xz": 1,
        "yz": 0,
    }

    def _principal_plane_from_coord(
        self, coord: CoordinateSystem
    ) -> tuple[str, float] | None:
        point1 = self._transform_point(coord, [0.0, 0.0, 0.0])
        point2 = self._transform_point(coord, [1.0, 0.0, 0.0])
        point3 = self._transform_point(coord, [0.0, 1.0, 0.0])
        return self._detect_principal_plane(point1, point2, point3)

    def _normalize_vector3(
        self, vector: list[float] | tuple[float, float, float], tol: float = 1e-9
    ) -> list[float]:
        length = math.sqrt(
            sum(float(component) * float(component) for component in vector)
        )
        if length <= tol:
            raise ValueError(f"Degenerate vector: {vector}")
        return [float(component) / length for component in vector]

    def _cross_product3(
        self,
        left: list[float] | tuple[float, float, float],
        right: list[float] | tuple[float, float, float],
    ) -> list[float]:
        return [
            float(left[1]) * float(right[2]) - float(left[2]) * float(right[1]),
            float(left[2]) * float(right[0]) - float(left[0]) * float(right[2]),
            float(left[0]) * float(right[1]) - float(left[1]) * float(right[0]),
        ]

    def _dot_product3(
        self,
        left: list[float] | tuple[float, float, float],
        right: list[float] | tuple[float, float, float],
    ) -> float:
        return sum(float(a) * float(b) for a, b in zip(left, right))

    def _vector_add3(
        self,
        left: list[float] | tuple[float, float, float],
        right: list[float] | tuple[float, float, float],
    ) -> list[float]:
        return [float(a) + float(b) for a, b in zip(left, right)]

    def _vector_scale3(
        self,
        vector: list[float] | tuple[float, float, float],
        scale: float,
    ) -> list[float]:
        return [float(component) * float(scale) for component in vector]

    def _coord_basis_vectors(
        self, coord: CoordinateSystem
    ) -> tuple[list[float], list[float], list[float]]:
        matrix = self._rotation_matrix(coord)
        u_axis = self._normalize_vector3([matrix[0][0], matrix[1][0], matrix[2][0]])
        v_axis = self._normalize_vector3([matrix[0][1], matrix[1][1], matrix[2][1]])
        w_axis = self._normalize_vector3([matrix[0][2], matrix[1][2], matrix[2][2]])
        return u_axis, v_axis, w_axis

    def _standard_sketch_basis(
        self, plane_type: str
    ) -> tuple[list[float], list[float], list[float]]:
        basis = self._STANDARD_SKETCH_BASIS.get(plane_type)
        if basis is None:
            raise ValueError(f"Unsupported standard plane_type: {plane_type}")
        u_axis, v_axis, normal = basis
        return list(u_axis), list(v_axis), list(normal)

    def _base_plane_candidates(
        self, origin_world: list[float]
    ) -> list[tuple[str, float, list[float]]]:
        candidates: list[tuple[str, float, list[float]]] = []
        for plane_type, offset_axis in self._STANDARD_PLANE_OFFSET_AXIS.items():
            _u_axis, _v_axis, normal = self._standard_sketch_basis(plane_type)
            candidates.append((plane_type, float(origin_world[offset_axis]), normal))
        return candidates

    def _choose_helper_base_plane(
        self,
        origin_world: list[float],
        target_y_world: list[float],
    ) -> tuple[str, float, list[float]]:
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

    def _world_point_to_standard_sketch_uv(
        self, plane_type: str, point: list[float]
    ) -> list[float]:
        u_axis, v_axis, _normal = self._standard_sketch_basis(plane_type)
        return [
            float(self._dot_product3(point, u_axis)),
            float(self._dot_product3(point, v_axis)),
        ]

    def _world_vector_to_standard_sketch_uv(
        self, plane_type: str, vector: list[float]
    ) -> list[float]:
        u_axis, v_axis, _normal = self._standard_sketch_basis(plane_type)
        return [
            float(self._dot_product3(vector, u_axis)),
            float(self._dot_product3(vector, v_axis)),
        ]

    def _helper_sketch_ref_name(self, index: int, stage: str) -> str:
        return f"__helper_sketch_{index}_{stage}"

    def _build_sketch_commands(
        self, coord: CoordinateSystem, index: int
    ) -> list[Command]:
        return [
            Command(
                method="create_sketch",
                kwargs={"coordinate_system": coord.to_payload()},
                capture=f"sketch_{index}",
                label=f"sketch_{index}",
            ),
        ]

    def _build_sketch_command(self, coord: CoordinateSystem, index: int) -> Command:
        return self._build_sketch_commands(coord, index)[-1]

    def _sketch_supports_axis_constraints(self, coord: CoordinateSystem) -> bool:
        plane = self._principal_plane_from_coord(coord)
        if plane is None:
            return False
        plane_type, _ = plane
        return self._local_y_aligns_with_fusion_sketch_y(coord, plane_type)

    def _augment_constraint_extra_for_local_axes(
        self,
        normalized_type: str,
        coord: CoordinateSystem | None,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if coord is None:
            return extra
        if self._sketch_supports_axis_constraints(coord):
            return extra

        axis_vector, mode = self._local_axis_payload_for_constraint(
            normalized_type, extra
        )
        if axis_vector is None or mode is None:
            return extra
        merged_extra = dict(extra or {})
        merged_extra.update(
            {
                "local_axis_mode": mode,
                "local_axis_origin": self._transform_point(coord, [0.0, 0.0, 0.0]),
                "local_axis_direction": self._transform_local_axis_direction(
                    coord, axis_vector
                ),
            }
        )
        return merged_extra

    def _local_axis_payload_for_constraint(
        self,
        normalized_type: str,
        extra: dict[str, Any] | None,
    ) -> tuple[list[float] | None, str | None]:
        if normalized_type == "horizontal":
            return [1.0, 0.0, 0.0], "parallel_helper"
        if normalized_type == "vertical":
            return [0.0, 1.0, 0.0], "parallel_helper"
        if normalized_type in {"distance", "length"}:
            direction = self._constraint_direction(extra)
            if direction == "HORIZONTAL":
                return [1.0, 0.0, 0.0], "directional_dimension_helper"
            if direction == "VERTICAL":
                return [0.0, 1.0, 0.0], "directional_dimension_helper"
        return None, None

    def _constraint_direction(self, extra: dict[str, Any] | None) -> str | None:
        if not isinstance(extra, dict):
            return None
        direction = extra.get("direction")
        if not isinstance(direction, str):
            return None
        normalized = direction.strip().upper()
        return normalized or None

    def _augment_distance_extra_for_concentric_curves(
        self,
        normalized_type: str,
        entities: list[str],
        constraints: dict[str, Any],
        sketch_payload: dict[str, Any] | None,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if normalized_type != "distance" or len(entities) != 2:
            return extra
        if not all(
            self._entity_name_is_arc_or_circle(name, sketch_payload)
            for name in entities
        ):
            return extra
        if not self._has_concentric_center_constraint(
            entities[0],
            entities[1],
            constraints,
            sketch_payload,
        ):
            return extra
        merged = dict(extra or {})
        merged["concentric"] = True
        return merged

    def _entity_name_is_arc_or_circle(
        self,
        entity_name: str,
        sketch_payload: dict[str, Any] | None = None,
    ) -> bool:
        if not isinstance(entity_name, str) or not entity_name:
            return False
        if "." in entity_name:
            return False
        primitive_name = entity_name.split(".", 1)[0]
        return primitive_name.startswith("arc_") or primitive_name.startswith("circle_")

    def _has_concentric_center_constraint(
        self,
        first_entity: str,
        second_entity: str,
        constraints: dict[str, Any],
        sketch_payload: dict[str, Any] | None = None,
    ) -> bool:
        if isinstance(constraints, dict):
            coincident_entries = constraints.get("Coincident")
            if coincident_entries is not None:
                target_pair = frozenset(
                    (f"{first_entity}.center", f"{second_entity}.center")
                )
                for entry in self._normalize_constraint_entries(
                    coincident_entries,
                    constraint_type="Coincident",
                ):
                    try:
                        coincident_entities, _value, _extra = (
                            self._parse_constraint_entry("Coincident", entry)
                        )
                    except Exception:
                        continue
                    if len(coincident_entities) != 2:
                        continue
                    if frozenset(coincident_entities) == target_pair:
                        return True
        first_center = self._curve_center_from_sketch_payload(
            first_entity, sketch_payload
        )
        second_center = self._curve_center_from_sketch_payload(
            second_entity, sketch_payload
        )
        if first_center is None or second_center is None:
            return False
        dx = float(first_center[0]) - float(second_center[0])
        dy = float(first_center[1]) - float(second_center[1])
        return math.hypot(dx, dy) <= 1e-4

    def _curve_center_from_sketch_payload(
        self,
        entity_name: str,
        sketch_payload: dict[str, Any] | None,
    ) -> tuple[float, float] | None:
        if not isinstance(sketch_payload, dict):
            return None
        primitive_name = entity_name.split(".", 1)[0]
        primitive = sketch_payload.get(primitive_name)
        if not isinstance(primitive, dict):
            return None
        if primitive_name.startswith("circle_"):
            center = primitive.get("center")
            if isinstance(center, list) and len(center) >= 2:
                return float(center[0]), float(center[1])
            return None
        if primitive_name.startswith("arc_"):
            start = primitive.get("start")
            middle = primitive.get("middle")
            end = primitive.get("end")
            if not all(
                isinstance(point, list) and len(point) >= 2
                for point in (start, middle, end)
            ):
                return None
            return self._arc_center_from_three_points(
                (float(start[0]), float(start[1])),
                (float(middle[0]), float(middle[1])),
                (float(end[0]), float(end[1])),
            )
        return None

    def _arc_center_from_three_points(
        self,
        start: tuple[float, float],
        middle: tuple[float, float],
        end: tuple[float, float],
    ) -> tuple[float, float] | None:
        x1, y1 = start
        x2, y2 = middle
        x3, y3 = end
        denom = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(denom) <= 1e-12:
            return None
        ux = (
            (x1 * x1 + y1 * y1) * (y2 - y3)
            + (x2 * x2 + y2 * y2) * (y3 - y1)
            + (x3 * x3 + y3 * y3) * (y1 - y2)
        ) / denom
        uy = (
            (x1 * x1 + y1 * y1) * (x3 - x2)
            + (x2 * x2 + y2 * y2) * (x1 - x3)
            + (x3 * x3 + y3 * y3) * (x2 - x1)
        ) / denom
        return float(ux), float(uy)

    def _local_y_aligns_with_fusion_sketch_y(
        self, coord: CoordinateSystem, plane_type: str, tol: float = 1e-6
    ) -> bool:
        """Return True when local Y is parallel to Fusion 360's native sketch Y-axis.

        When False, Horizontal/Vertical must be lowered to explicit helper geometry that
        encodes the HistCAD local axis direction.
        """
        fusion_sketch_y = self._FUSION_PLANE_SKETCH_Y.get(plane_type)
        if fusion_sketch_y is None:
            return True
        local_y_world = self._transform_vector(coord, [0.0, 1.0, 0.0])
        dot = sum(a * b for a, b in zip(local_y_world, fusion_sketch_y))
        return abs(dot) > 1.0 - tol

    def _detect_principal_plane(
        self,
        point1: list[float],
        point2: list[float],
        point3: list[float],
        tol: float = 1e-8,
    ) -> tuple[str, float] | None:
        points = (point1, point2, point3)
        for plane_type, axis in (("yz", 0), ("xz", 1), ("xy", 2)):
            values = [float(point[axis]) for point in points]
            if max(values) - min(values) <= tol:
                return plane_type, round(sum(values) / len(values), 10)
        return None

    def _rotation_matrix(
        self, coord: CoordinateSystem
    ) -> tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]:
        angles_rad = [math.radians(v) for v in coord.euler_angles]
        mat = R.from_euler("XYZ", angles_rad).as_matrix()
        return (
            (float(mat[0, 0]), float(mat[0, 1]), float(mat[0, 2])),
            (float(mat[1, 0]), float(mat[1, 1]), float(mat[1, 2])),
            (float(mat[2, 0]), float(mat[2, 1]), float(mat[2, 2])),
        )

    def _transform_local_axis_direction(
        self,
        coord: CoordinateSystem,
        vector: list[float] | tuple[float, ...],
    ) -> list[float]:
        return self._transform_vector(coord, vector)

    def _transform_point(
        self, coord: CoordinateSystem, point: list[float] | tuple[float, ...]
    ) -> list[float]:
        local_x = float(point[0])
        local_y = float(point[1])
        local_z = float(point[2]) if len(point) >= 3 else 0.0
        matrix = self._rotation_matrix(coord)
        world_x = (
            matrix[0][0] * local_x
            + matrix[0][1] * local_y
            + matrix[0][2] * local_z
            + coord.translation_vector[0]
        )
        world_y = (
            matrix[1][0] * local_x
            + matrix[1][1] * local_y
            + matrix[1][2] * local_z
            + coord.translation_vector[1]
        )
        world_z = (
            matrix[2][0] * local_x
            + matrix[2][1] * local_y
            + matrix[2][2] * local_z
            + coord.translation_vector[2]
        )
        return [round(world_x, 10), round(world_y, 10), round(world_z, 10)]

    def _transform_vector(
        self, coord: CoordinateSystem, vector: list[float] | tuple[float, ...]
    ) -> list[float]:
        local_x = float(vector[0])
        local_y = float(vector[1])
        local_z = float(vector[2])
        matrix = self._rotation_matrix(coord)
        world_x = (
            matrix[0][0] * local_x + matrix[0][1] * local_y + matrix[0][2] * local_z
        )
        world_y = (
            matrix[1][0] * local_x + matrix[1][1] * local_y + matrix[1][2] * local_z
        )
        world_z = (
            matrix[2][0] * local_x + matrix[2][1] * local_y + matrix[2][2] * local_z
        )
        return [round(world_x, 10), round(world_y, 10), round(world_z, 10)]

    def _ellipse_major_axis_point(
        self, center: list[float], major: float, angle_deg: float
    ) -> list[float]:
        angle = math.radians(angle_deg)
        return [
            float(center[0]) + major * math.cos(angle),
            float(center[1]) + major * math.sin(angle),
            0.0,
        ]

    def _ellipse_pass_point(
        self, center: list[float], minor: float, angle_deg: float
    ) -> list[float]:
        angle = math.radians(angle_deg)
        return [
            float(center[0]) - minor * math.sin(angle),
            float(center[1]) + minor * math.cos(angle),
            0.0,
        ]

    def _solve_elliptical_arc(
        self,
        start_xy: tuple[float, float],
        end_xy: tuple[float, float],
        major: float,
        minor: float,
        angle_deg: float,
        large_arc: bool,
        sweep: bool,
    ) -> dict[str, Any]:
        angle_rad = math.radians(angle_deg)
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
            raise ValueError("elliptical arc start and end are too close")
        if chord > 2.0 + 1e-9:
            raise ValueError(
                "No valid ellipse center for the given start/end/major/minor"
            )
        chord = min(chord, 2.0)
        mx = (s1x + s2x) * 0.5
        my = (s1y + s2y) * 0.5
        ux = dx / chord
        uy = dy / chord
        nx = -uy
        ny = ux
        h = math.sqrt(max(0.0, 1.0 - (chord * 0.5) ** 2))

        candidates: list[tuple[dict[str, Any], float]] = []
        for sign in (1.0, -1.0):
            tx = mx + sign * nx * h
            ty = my + sign * ny * h
            center_local_x = tx * major
            center_local_y = ty * minor
            q1x = s1x - tx
            q1y = s1y - ty
            q2x = s2x - tx
            q2y = s2y - ty
            theta_start = math.atan2(q1y, q1x)
            theta_end = math.atan2(q2y, q2x)
            delta_ccw = (theta_end - theta_start) % (2.0 * math.pi)
            delta_cw = (2.0 * math.pi - delta_ccw) % (2.0 * math.pi)
            sweep_angle = delta_ccw if sweep else -delta_cw
            if abs(sweep_angle) <= 1e-9:
                continue
            candidates.append(
                (
                    {
                        "center_local": [center_local_x, center_local_y, 0.0],
                        "theta_start": theta_start,
                        "major_axis_local": [
                            math.cos(angle_rad) * major,
                            math.sin(angle_rad) * major,
                            0.0,
                        ],
                        "minor_axis_local": [
                            -math.sin(angle_rad) * minor,
                            math.cos(angle_rad) * minor,
                            0.0,
                        ],
                    },
                    sweep_angle,
                )
            )

        matching = []
        for candidate, sweep_angle in candidates:
            is_large = abs(sweep_angle) > math.pi + 1e-9
            if is_large == bool(large_arc):
                candidate = dict(candidate)
                candidate["sweep_angle"] = sweep_angle
                return candidate
            matching.append((candidate, sweep_angle))

        if not matching:
            raise RuntimeError("Failed to resolve elliptical arc candidate")
        chooser = max if large_arc else min
        candidate, sweep_angle = chooser(matching, key=lambda item: abs(item[1]))
        candidate = dict(candidate)
        candidate["sweep_angle"] = sweep_angle
        return candidate

    def _rotate_to_local(
        self, x: float, y: float, angle_rad: float
    ) -> tuple[float, float]:
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        return cos_a * x + sin_a * y, -sin_a * x + cos_a * y

    def _normalize_axis(self, axis: Any) -> list[list[float]]:
        if not isinstance(axis, list) or len(axis) != 2:
            raise ValueError(f"axis must be [[x,y,z],[dx,dy,dz]], got: {axis}")
        origin = [float(value) for value in axis[0]]
        direction = [float(value) for value in axis[1]]
        return [origin, direction]

    def _normalize_near_points(self, payload: Any) -> list[list[float]]:
        if payload is None:
            raise ValueError("near_points is required for Fillet/Chamfer")
        if self._is_number_list(payload):
            return [[float(value) for value in payload]]
        if isinstance(payload, list) and all(
            self._is_number_list(point) for point in payload
        ):
            return [[float(value) for value in point] for point in payload]
        raise ValueError(f"Invalid near_points payload: {payload}")

    def _expand_numeric_field(
        self, payload: Any, count: int, field_name: str
    ) -> list[float]:
        if isinstance(payload, (int, float)):
            return [float(payload)] * count
        if (
            isinstance(payload, list)
            and len(payload) == count
            and all(isinstance(item, (int, float)) for item in payload)
        ):
            return [float(item) for item in payload]
        raise ValueError(f"Invalid {field_name} payload: {payload}")

    def _expand_optional_numeric_field(
        self, payload: Any, count: int, field_name: str
    ) -> list[float | None]:
        if payload is None:
            return [None] * count
        return self._expand_numeric_field(payload, count, field_name)

    def _expand_optional_vector3_field(
        self, payload: Any, count: int, field_name: str
    ) -> list[list[float] | None]:
        if payload is None:
            return [None] * count
        if self._is_vector3(payload):
            vector = [float(value) for value in payload]
            return [vector[:] for _ in range(count)]
        if (
            isinstance(payload, list)
            and len(payload) == count
            and all(self._is_vector3(item) for item in payload)
        ):
            return [[float(value) for value in item] for item in payload]
        raise ValueError(f"Invalid {field_name} payload: {payload}")

    def _ensure_point2d(self, value: Any) -> tuple[float, float]:
        if not isinstance(value, list) or len(value) < 2:
            raise ValueError(f"Expected a 2D point, got: {value}")
        return float(value[0]), float(value[1])

    def _script_local_point(self, value: Any) -> list[float]:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            raise ValueError(f"Expected a sketch-local point payload, got: {value}")
        if len(value) >= 3 and abs(float(value[2])) > 1e-9:
            raise ValueError(
                f"Sketch-local script rendering expects z=0 in local coordinates, got: {value}"
            )
        return [float(value[0]), float(value[1])]

    def _script_local_vector(self, value: Any) -> list[float]:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            raise ValueError(f"Expected a sketch-local vector payload, got: {value}")
        if len(value) >= 3 and abs(float(value[2])) > 1e-9:
            raise ValueError(
                f"Sketch-local script rendering expects z=0 for in-sketch vectors, got: {value}"
            )
        return [float(value[0]), float(value[1])]

    def _to_vector3(self, value: Any) -> list[float]:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            raise ValueError(f"Expected a vector payload, got: {value}")
        if len(value) == 2:
            return [float(value[0]), float(value[1]), 0.0]
        return [float(value[0]), float(value[1]), float(value[2])]

    def _validate_sketch_command_consistency(
        self,
        coord: CoordinateSystem,
        sketch_command: Command,
        tol: float = 1e-8,
    ) -> list[dict[str, Any]]:
        if sketch_command.method == "create_sketch":
            capture_name = sketch_command.capture or ""
            if not re.fullmatch(r"sketch_\d+", capture_name):
                return []
            issues: list[dict[str, Any]] = []
            actual_coordinate_system = sketch_command.kwargs.get("coordinate_system")
            if actual_coordinate_system is not None:
                expected_coordinate_system = coord.to_payload()
                if actual_coordinate_system != expected_coordinate_system:
                    issues.append(
                        {
                            "scope": "sketch",
                            "field": "coordinate_system",
                            "expected": expected_coordinate_system,
                            "actual": actual_coordinate_system,
                        }
                    )
                return issues

            expected_plane = self._principal_plane_from_coord(coord)
            if expected_plane is None:
                issues.append(
                    {
                        "scope": "sketch",
                        "field": "coordinate_system",
                        "expected": coord.to_payload(),
                        "actual": None,
                    }
                )
                return issues

            expected_plane_type, expected_offset = expected_plane
            if sketch_command.kwargs.get("plane_type") != expected_plane_type:
                issues.append(
                    {
                        "scope": "sketch",
                        "field": "plane_type",
                        "expected": expected_plane_type,
                        "actual": sketch_command.kwargs.get("plane_type"),
                    }
                )
            actual_offset = sketch_command.kwargs.get("offset")
            if (
                actual_offset is None
                or abs(float(actual_offset) - float(expected_offset)) > tol
            ):
                issues.append(
                    {
                        "scope": "sketch",
                        "field": "offset",
                        "expected": expected_offset,
                        "actual": actual_offset,
                    }
                )
            return issues

        if sketch_command.method != "create_sketch_by_three_points":
            return []

        issues: list[dict[str, Any]] = []
        legacy_fields = {"point1", "point2", "point3"}
        if legacy_fields.intersection(sketch_command.kwargs):
            issues.append(
                {
                    "scope": "sketch",
                    "message": "Legacy point1/point2/point3 sketch commands are no longer supported.",
                }
            )

        required_fields = ("ref_sketch_id", "p1_uv", "p2_uv", "to")
        missing_fields = [
            field_name
            for field_name in required_fields
            if field_name not in sketch_command.kwargs
        ]
        if missing_fields:
            issues.append(
                {
                    "scope": "sketch",
                    "message": (
                        "Reference-edge sketch command is missing required fields: "
                        + ", ".join(missing_fields)
                    ),
                }
            )
            return issues

        for field_name in ("p1_uv", "p2_uv"):
            value = sketch_command.kwargs.get(field_name)
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                issues.append(
                    {
                        "scope": "sketch",
                        "field": field_name,
                        "message": f"Expected a 2D sketch-local point for {field_name}, got: {value!r}",
                    }
                )
        return issues

    def _validate_command_coordinate_consistency(
        self,
        coord: CoordinateSystem,
        world_command: Command,
        local_command: Command,
        context: str,
        tol: float = 1e-8,
    ) -> list[dict[str, Any]]:
        if world_command.method != local_command.method:
            return [
                {
                    "scope": context,
                    "message": (
                        "World/local command method mismatch: "
                        f"{world_command.method} vs {local_command.method}"
                    ),
                }
            ]

        issues: list[dict[str, Any]] = []
        point_fields = self.LOCAL_POINT_FIELDS_BY_METHOD.get(local_command.method, ())
        for field_name in point_fields:
            if (
                field_name not in local_command.kwargs
                or field_name not in world_command.kwargs
            ):
                continue
            transformed = self._transform_point(coord, local_command.kwargs[field_name])
            expected = world_command.kwargs[field_name]
            if not self._lists_close(transformed, expected, tol=tol):
                issues.append(
                    {
                        "scope": context,
                        "field": field_name,
                        "local_value": local_command.kwargs[field_name],
                        "transformed_world": transformed,
                        "expected_world": expected,
                    }
                )

        vector_fields = self.LOCAL_VECTOR_FIELDS_BY_METHOD.get(local_command.method, ())
        for field_name in vector_fields:
            if (
                field_name not in local_command.kwargs
                or field_name not in world_command.kwargs
            ):
                continue
            transformed = self._transform_vector(
                coord, self._to_vector3(local_command.kwargs[field_name])
            )
            expected = world_command.kwargs[field_name]
            if not self._lists_close(transformed, expected, tol=tol):
                issues.append(
                    {
                        "scope": context,
                        "field": field_name,
                        "local_value": local_command.kwargs[field_name],
                        "transformed_world": transformed,
                        "expected_world": expected,
                    }
                )

        point_list_fields = self.LOCAL_POINT_LIST_FIELDS_BY_METHOD.get(
            local_command.method, ()
        )
        for field_name in point_list_fields:
            if (
                field_name not in local_command.kwargs
                or field_name not in world_command.kwargs
            ):
                continue
            local_values = local_command.kwargs[field_name]
            expected_values = world_command.kwargs[field_name]
            if len(local_values) != len(expected_values):
                issues.append(
                    {
                        "scope": context,
                        "field": field_name,
                        "message": (
                            "Point list length mismatch after local rendering: "
                            f"{len(local_values)} vs {len(expected_values)}"
                        ),
                    }
                )
                continue
            for item_index, (local_value, expected) in enumerate(
                zip(local_values, expected_values), start=1
            ):
                transformed = self._transform_point(coord, local_value)
                if not self._lists_close(transformed, expected, tol=tol):
                    issues.append(
                        {
                            "scope": context,
                            "field": f"{field_name}[{item_index}]",
                            "local_value": local_value,
                            "transformed_world": transformed,
                            "expected_world": expected,
                        }
                    )
        return issues

    def _lists_close(self, left: Any, right: Any, tol: float = 1e-8) -> bool:
        if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)):
            return False
        if len(left) != len(right):
            return False
        return all(abs(float(lhs) - float(rhs)) <= tol for lhs, rhs in zip(left, right))

    def _is_flat_entity_ref_list(self, value: list[Any]) -> bool:
        return bool(value) and all(isinstance(item, str) for item in value)

    def _looks_like_single_constraint_entry(self, value: list[Any]) -> bool:
        if self._is_flat_entity_ref_list(value):
            return True
        return bool(value) and not any(isinstance(item, list) for item in value)

    def _looks_like_typed_single_constraint_entry(
        self,
        constraint_type: str | None,
        value: list[Any],
    ) -> bool:
        normalized_type = (constraint_type or "").strip().lower()
        if normalized_type != "midpoint":
            return False
        if len(value) not in {2, 3} or not isinstance(value[0], str):
            return False
        point_pair = value[1]
        if not (
            isinstance(point_pair, list)
            and len(point_pair) == 2
            and all(isinstance(item, str) for item in point_pair)
        ):
            return False
        return len(value) == 2 or isinstance(value[2], dict)

    def _is_number_list(self, value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) in {2, 3}
            and all(isinstance(item, (int, float)) for item in value)
        )

    def _is_vector3(self, value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) == 3
            and all(isinstance(item, (int, float)) for item in value)
        )

    def _scale_unitless_length(self, value: float) -> float:
        return float(value) * self.UNITLESS_LENGTH_SCALE

    def _format_mm_expression(self, value: float) -> str:
        return f"{float(value):.10g} mm"

    def _scale_point_payload(self, point: list[Any]) -> list[float]:
        if len(point) not in {2, 3}:
            raise ValueError(f"Point must have length 2 or 3, got: {point!r}")
        return [self._scale_unitless_length(float(item)) for item in point]

    def _normalize_coordinate_system_for_fusion(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        translation = payload.get("Translation Vector")
        if not self._is_vector3(translation):
            return payload
        normalized = dict(payload)
        normalized["Translation Vector"] = self._scale_point_payload(list(translation))
        return normalized

    def _normalize_axis_for_fusion(self, axis: Any) -> Any:
        if not (
            isinstance(axis, list)
            and len(axis) == 2
            and self._is_vector3(axis[0])
            and self._is_vector3(axis[1])
        ):
            return axis
        return [
            self._scale_point_payload(list(axis[0])),
            [float(item) for item in axis[1]],
        ]

    def _normalize_constraint_value_for_fusion(
        self,
        constraint_type: str | None,
        value: Any,
    ) -> Any:
        normalized_type = (constraint_type or "").strip().lower()
        if isinstance(value, (int, float)):
            if normalized_type == "angle":
                return float(value)
            return self._format_mm_expression(float(value))
        if isinstance(value, str):
            stripped = value.strip()
            if (
                normalized_type != "angle"
                and stripped
                and re.fullmatch(
                    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", stripped
                )
            ):
                return self._format_mm_expression(float(stripped))
            normalized_expression = self._normalize_dimension_expression(
                constraint_type,
                stripped,
            )
            if normalized_expression != stripped:
                return normalized_expression
        return value

    def _normalize_dimension_expression(
        self,
        constraint_type: str | None,
        expression: str,
    ) -> str:
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
                return f"{numeric_text} {unit}"
            return numeric_text
        inline_unit_expression = self._normalize_inline_unit_expression(stripped)
        if inline_unit_expression is not None:
            return inline_unit_expression
        complex_length_expression = self._normalize_complex_length_expression(stripped)
        if complex_length_expression is not None:
            return complex_length_expression
        return stripped

    def _split_dimension_unit_suffix(self, expression: str) -> tuple[str, str | None]:
        match = self._DIMENSION_UNIT_SUFFIX_RE.fullmatch(expression)
        if match:
            return match.group("body").strip(), match.group("unit").strip()
        match = self._COMPACT_DIMENSION_UNIT_SUFFIX_RE.fullmatch(expression)
        if match:
            return match.group("body").strip(), match.group("unit").strip()
        return expression, None

    def _canonicalize_fractional_body(self, body: str) -> str:
        normalized = self._MIXED_FRACTION_RE.sub(
            self._mixed_fraction_replacement,
            body,
        )
        return self._HYPHENATED_MIXED_FRACTION_RE.sub(
            self._mixed_fraction_replacement,
            normalized,
        )

    def _mixed_fraction_replacement(self, match: re.Match[str]) -> str:
        sign = match.group("sign") or ""
        base = f"{match.group('whole')}+{match.group('num')}/{match.group('den')}"
        if sign == "-":
            return f"-({base})"
        if sign == "+":
            return f"+({base})"
        return base

    def _normalize_inline_unit_expression(self, expression: str) -> str | None:
        units: list[str] = []

        def _strip_unit(match: re.Match[str]) -> str:
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
        return f"{numeric_text} {next(iter(unique_units))}"

    def _evaluate_fractional_literal(self, body: str) -> Fraction | None:
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

    def _evaluate_fractional_ast(self, node: ast.AST) -> Fraction:
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

    def _format_fractional_literal(self, value: Fraction) -> str:
        return f"{float(value):.15g}"

    def _normalize_complex_length_expression(self, expression: str) -> str | None:
        evaluation = self._evaluate_length_expression_cm(expression)
        if evaluation is None:
            return None
        value_cm, unit_names, has_constant, has_function = evaluation
        if len(unit_names) <= 1 and not has_constant and not has_function:
            return None
        return f"{self._format_dimension_scalar(value_cm)} cm"

    def _evaluate_length_expression_cm(
        self,
        expression: str,
    ) -> tuple[float, set[str], bool, bool] | None:
        rewritten_tokens: list[str] = []
        unit_names: set[str] = set()
        has_constant = False
        has_function = False
        prev_token_type: str | None = None
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

    def _normalize_dimension_name(self, name: str) -> str:
        normalized = name.strip().lower()
        if normalized in {'"', "in"}:
            return "inch"
        return normalized

    def _evaluate_dimension_ast(self, node: ast.AST) -> float:
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

    def _format_dimension_scalar(self, value: float) -> str:
        return f"{float(value):.15g}"

    def _normalize_constraint_extra_for_fusion(self, extra: Any) -> Any:
        if not isinstance(extra, dict):
            return extra
        normalized = dict(extra)
        origin = normalized.get("local_axis_origin")
        if self._is_vector3(origin):
            normalized["local_axis_origin"] = self._scale_point_payload(list(origin))
        return normalized

    def _normalize_fusion_kwargs(
        self, method: str, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        normalized = dict(kwargs)

        for field_name in self.FUSION_POINT_FIELDS_BY_METHOD.get(method, ()):
            value = normalized.get(field_name)
            if self._is_number_list(value):
                normalized[field_name] = self._scale_point_payload(list(value))

        for field_name in self.FUSION_POINT_LIST_FIELDS_BY_METHOD.get(method, ()):
            value = normalized.get(field_name)
            if isinstance(value, list):
                normalized[field_name] = [
                    self._scale_point_payload(list(item))
                    if self._is_number_list(item)
                    else item
                    for item in value
                ]

        for field_name in self.FUSION_LENGTH_FIELDS_BY_METHOD.get(method, ()):
            value = normalized.get(field_name)
            if isinstance(value, (int, float)):
                normalized[field_name] = self._scale_unitless_length(float(value))
            elif isinstance(value, list):
                normalized[field_name] = [
                    self._scale_unitless_length(float(item))
                    if isinstance(item, (int, float))
                    else item
                    for item in value
                ]

        if method == "create_sketch" and "coordinate_system" in normalized:
            normalized["coordinate_system"] = (
                self._normalize_coordinate_system_for_fusion(
                    normalized["coordinate_system"]
                )
            )

        if method in {"revolve", "create_helix_sweep"} and "axis" in normalized:
            normalized["axis"] = self._normalize_axis_for_fusion(normalized["axis"])

        if method == "add_constraint":
            if "value" in normalized:
                normalized["value"] = self._normalize_constraint_value_for_fusion(
                    normalized.get("type"),
                    normalized["value"],
                )
            if "extra" in normalized:
                normalized["extra"] = self._normalize_constraint_extra_for_fusion(
                    normalized["extra"]
                )

        return normalized

    def _resolve_runtime_value(self, value: Any, entities: dict[str, Any]) -> Any:
        if isinstance(value, EntityRef):
            return entities[value.name]
        if isinstance(value, list):
            return [self._resolve_runtime_value(item, entities) for item in value]
        if isinstance(value, dict):
            return {
                key: self._resolve_runtime_value(item, entities)
                for key, item in value.items()
            }
        return value

    def _render_modeling_command(self, command: Command) -> list[str]:
        normalized_kwargs = self._normalize_fusion_kwargs(
            command.method, command.kwargs
        )
        rendered_kwargs = ", ".join(
            f"{key}={self._render_script_literal(value)}"
            for key, value in normalized_kwargs.items()
        )
        call = f"cad.{command.method}({rendered_kwargs})"
        if command.capture:
            lines = [f"{command.capture} = {call}"]
            for entity_name, response_key in command.register.items():
                lines.append(
                    f"if {self._render_literal(response_key)} in {command.capture}:"
                )
                lines.append(
                    f"    refs[{self._render_literal(entity_name)}] = "
                    f"{command.capture}[{self._render_literal(response_key)}]"
                )
            lines.append("")
            return lines
        return [call, ""]

    def _render_script_literal(self, value: Any) -> str:
        if isinstance(value, EntityRef):
            return f"refs[{self._render_literal(value.name)}]"
        if isinstance(value, str):
            return repr(value)
        if isinstance(value, bool):
            return "True" if value else "False"
        if value is None:
            return "None"
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, list):
            return (
                "["
                + ", ".join(self._render_script_literal(item) for item in value)
                + "]"
            )
        if isinstance(value, dict):
            parts = [
                f"{self._render_literal(key)}: {self._render_script_literal(item)}"
                for key, item in value.items()
            ]
            return "{" + ", ".join(parts) + "}"
        raise TypeError(f"Unsupported script literal value: {value!r}")

    def _render_command(self, command: Command) -> list[str]:
        normalized_kwargs = self._normalize_fusion_kwargs(
            command.method, command.kwargs
        )
        rendered_kwargs = ", ".join(
            f"{key}={self._render_literal(value)}"
            for key, value in normalized_kwargs.items()
        )
        call = f"client.{command.method}({rendered_kwargs})"
        lines: list[str] = []
        if command.capture:
            lines.append(f"        {command.capture} = _call(lambda: {call})")
            for entity_name, response_key in command.register.items():
                lines.append(
                    f"        if {self._render_literal(response_key)} in {command.capture}:"
                )
                lines.append(
                    f"            entities[{self._render_literal(entity_name)}] = {command.capture}[{self._render_literal(response_key)}]"
                )
            return lines
        if command.method == "add_constraint":
            lines.append(
                f"        _call(lambda: {call}, 'constraint', {self._render_literal(command.label or 'constraint')})"
            )
            return lines
        if command.method in {"create_fillet", "create_chamfer"}:
            lines.append(
                f"        _call(lambda: {call}, 'edge', {self._render_literal(command.label or 'edge_feature')})"
            )
            return lines
        if command.method == "create_extrude" and command.kwargs.get("operation") in {
            "Cut",
            "Intersect",
        }:
            lines.append(
                f"        _call(lambda: {call}, 'boolean', {self._render_literal(command.label or 'extrude')})"
            )
            return lines
        lines.append(f"        _call(lambda: {call})")
        return lines

    def _render_literal(self, value: Any) -> str:
        if isinstance(value, EntityRef):
            return f"entities[{self._render_literal(value.name)}]"
        if isinstance(value, str):
            return repr(value)
        if isinstance(value, bool):
            return "True" if value else "False"
        if value is None:
            return "None"
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, list):
            return "[" + ", ".join(self._render_literal(item) for item in value) + "]"
        if isinstance(value, dict):
            parts = [
                f"{self._render_literal(key)}: {self._render_literal(item)}"
                for key, item in value.items()
            ]
            return "{" + ", ".join(parts) + "}"
        raise TypeError(f"Unsupported literal value: {value!r}")
