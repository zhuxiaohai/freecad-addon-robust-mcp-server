"""Run the constraint editability experiment on HistCAD JSON files.

The experiment samples files in random order, generates edit intents on demand,
and stops once each enabled intent family reaches its own quota. The same edit
intent is replayed on both the `constrained` and `closure_only` branches.

The implementation still supports the full supported intent space:

- `modify_dimension`
- `add_dimension`
- `add_geometric`

The default CLI configuration enables the two local dimensional edit families:

- `modify_dimension`
- `add_dimension`

Older result consumers are still supported through a few `*_length_mm`
compatibility aliases in the manifest/CSV outputs.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from export.converter import JsonToFusionConverter  # noqa: E402
from fusion360_tools.client.fusion360_client import Fusion360Client  # noqa: E402
from fusion360_tools.server.launch import Launcher, create_launch_json  # noqa: E402

SUPPORTED_CONSTRAINT_TYPES = {
    "angle",
    "coincident",
    "concentric",
    "diameter",
    "distance",
    "equal",
    "horizontal",
    "length",
    "midpoint",
    "normal",
    "parallel",
    "perpendicular",
    "radius",
    "tangent",
    "vertical",
}

SUPPORTED_PRIMITIVE_KINDS = {
    "arc",
    "circle",
    "line",
}

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
    "ft": 304.8,
    "foot": 304.8,
    "feet": 304.8,
}

DEFAULT_LENGTH_TOL_CM = 1e-3
DEFAULT_ANGLE_TOL_DEG = 0.5
DEFAULT_EDIT_SCALE_MIN = 0.5
DEFAULT_EDIT_SCALE_MAX = 2.0
DEFAULT_EDIT_SCALE_NEUTRAL_HOLE_MIN = 0.9
DEFAULT_EDIT_SCALE_NEUTRAL_HOLE_MAX = 1.1
DEFAULT_ANGLE_DELTA_MIN_DEG = 5.0
DEFAULT_ANGLE_DELTA_MAX_DEG = 20.0
DEFAULT_MAX_POINT_NEIGHBORS = 8
DEFAULT_MAX_POINT_LINE_NEIGHBORS = 4
DEFAULT_MAX_MIDPOINT_NEIGHBORS = 4
DEFAULT_MAX_SAMPLES_PER_FILE_PER_FAMILY = 1
DEFAULT_MAX_PRECHECKS_PER_FILE = 6
DEFAULT_TARGET_VALUE_RETRY_ATTEMPTS = 2
DEFAULT_CLI_DATASET_ROOT = Path("data") / "histcad0409"
DEFAULT_CLI_OUTPUT_ROOT = Path("outputs") / "constraint_editability"
DEFAULT_CLI_OUTPUT_DIR = DEFAULT_CLI_OUTPUT_ROOT / time.strftime("%Y%m%d_%H%M%S")
DEFAULT_CLI_TEST_UIDS_JSON: Path | None = None
DEFAULT_CLI_SAMPLE_SIZE = 20
DEFAULT_CLI_SAMPLE_SIZE_PER_SOURCE_SUBSET: int | None = None
DEFAULT_CLI_SEED = 42
DEFAULT_CLI_HOST = "127.0.0.1"
DEFAULT_CLI_PORT = 8080
DEFAULT_CLI_RESTART_EVERY = 500
DEFAULT_CLI_RESTART_TIMEOUT_SECONDS = 180.0
DEFAULT_CLI_RESTART_POLL_SECONDS = 1.0
DEFAULT_CLI_HEALTH_TIMEOUT_SECONDS = 10.0
DEFAULT_CLI_RESTART_RETRY_ATTEMPTS = 3
DEFAULT_CLI_RESTART_RETRY_BACKOFF_SECONDS = 5.0
DEFAULT_CLI_VISUALIZATION_SELECTION = "contrast"
DEFAULT_CLI_VISUALIZATION_MAX_SAMPLES = 6
DEFAULT_CLI_VISUALIZATION_IMAGE_SIZE = 420
DEFAULT_CLI_VISUALIZATION_VIEW_ORIENTATION = "iso"
DEFAULT_CLI_VISUALIZATION_GALLERY_COLUMNS = 2
DEFAULT_FUSION_TRANSPORT_RETRY_ATTEMPTS = 2
BRANCH_CONSTRAINED = "constrained"
BRANCH_CLOSURE_ONLY = "closure_only"
CLOSURE_ONLY_CONSTRAINT_TYPES = frozenset({"coincident"})
INTENT_FAMILY_MODIFY_DIMENSION = "modify_dimension"
INTENT_FAMILY_ADD_DIMENSION = "add_dimension"
INTENT_FAMILY_ADD_GEOMETRIC = "add_geometric"
SOURCE_SUBSET_HISTCAD_DEEPCAD = "HistCAD-DeepCAD"
SOURCE_SUBSET_HISTCAD_FUSION360 = "HistCAD-Fusion360"
SOURCE_SUBSET_HISTCAD_INDUSTRIAL = "HistCAD-Industrial"
SOURCE_SUBSET_HISTCAD_ACADEMIC = "HistCAD-Academic"
KNOWN_SOURCE_SUBSETS = (
    SOURCE_SUBSET_HISTCAD_DEEPCAD,
    SOURCE_SUBSET_HISTCAD_FUSION360,
    SOURCE_SUBSET_HISTCAD_INDUSTRIAL,
)
SUPPORTED_EDIT_DIMENSION_TYPES = frozenset(
    {"angle", "diameter", "distance", "length", "radius"}
)
ANGLE_DIMENSION_TYPES = frozenset({"angle"})
SUPPORTED_ADD_GEOMETRIC_TYPES = frozenset(
    {
        "coincident",
        "concentric",
        "equal",
        "horizontal",
        "midpoint",
        "normal",
        "parallel",
        "perpendicular",
        "tangent",
        "vertical",
    }
)
SUPPORTED_EXPERIMENT_INTENT_FAMILIES = (
    INTENT_FAMILY_MODIFY_DIMENSION,
    INTENT_FAMILY_ADD_DIMENSION,
    INTENT_FAMILY_ADD_GEOMETRIC,
)
DEFAULT_DIMENSIONAL_INTENT_FAMILIES = (
    INTENT_FAMILY_MODIFY_DIMENSION,
    INTENT_FAMILY_ADD_DIMENSION,
)
DEFAULT_INTENT_FAMILIES = DEFAULT_DIMENSIONAL_INTENT_FAMILIES
SUPPORTED_INTENT_FAMILIES = frozenset(SUPPORTED_EXPERIMENT_INTENT_FAMILIES)
CHECKPOINT_FILENAME = ".constraint_editability_checkpoint.json"
CHECKPOINT_VERSION = 1
SOURCE_PAYLOAD_CACHE_MAX_ENTRIES = 32
RESULTS_CSV_FIELDNAMES = [
    "sample_id",
    "case_id",
    "branch",
    "json_path",
    "source_subset",
    "source_domain",
    "op_index",
    "sketch_index",
    "intent_family",
    "target_constraint_type",
    "target_value_domain",
    "target_entry_index",
    "target_entities",
    "line_name",
    "primary_entity",
    "original_target_value_numeric",
    "edited_target_value_numeric",
    "original_length_mm",
    "edited_length_mm",
    "edit_scale",
    "edit_delta_deg",
    "source_constraint_count",
    "preserved_constraint_count",
    "target_hit",
    "target_actual",
    "target_expected",
    "target_delta",
    "target_error",
    "preserved_supported_constraints",
    "preserved_satisfied_constraints",
    "preserved_unsupported_constraints",
    "preserved_total_constraints",
    "preserved_constraint_satisfaction_rate",
    "preserved_constraints_all_satisfied",
    "skip_count",
    "rebuild_success",
    "validation_ok",
    "validation_fully_constrained",
    "validation_over_constrained",
    "missing_ref_count",
    "overall_editable_success",
    "failure_reason",
    "exception",
]
RESULTS_CSV_INTEGER_FIELDS = frozenset(
    {
        "sample_id",
        "op_index",
        "sketch_index",
        "target_entry_index",
        "source_constraint_count",
        "preserved_constraint_count",
        "preserved_supported_constraints",
        "preserved_satisfied_constraints",
        "preserved_unsupported_constraints",
        "preserved_total_constraints",
        "skip_count",
        "missing_ref_count",
    }
)
RESULTS_CSV_FLOAT_FIELDS = frozenset(
    {
        "original_target_value_numeric",
        "edited_target_value_numeric",
        "original_length_mm",
        "edited_length_mm",
        "edit_scale",
        "edit_delta_deg",
        "target_actual",
        "target_expected",
        "target_delta",
        "preserved_constraint_satisfaction_rate",
    }
)
RESULTS_CSV_BOOLEAN_FIELDS = frozenset(
    {
        "target_hit",
        "preserved_constraints_all_satisfied",
        "rebuild_success",
        "validation_ok",
        "validation_fully_constrained",
        "validation_over_constrained",
        "overall_editable_success",
    }
)

T = TypeVar("T")


class UnsupportedConstraintError(RuntimeError):
    pass


class IntentInfeasibleError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConstraintRecord:
    constraint_type: str
    entry_index: int
    entities: tuple[str, ...]
    value: float | str | None
    extra: dict[str, Any] | None


@dataclass(frozen=True)
class LengthEditCandidate:
    """Legacy name kept for downstream compatibility.

    The candidate now represents one editable existing dimension record, not
    just `Length`.
    """

    json_path: Path
    op_index: int
    sketch_index: int
    constraint_type: str
    entry_index: int | None
    entities: tuple[str, ...]
    line_name: str
    original_value_expr: float | str | None
    original_value_mm: float | None
    intent_family: str = INTENT_FAMILY_MODIFY_DIMENSION
    sketch_constraint_count: int = 0
    value_domain: str = "length"
    extra: dict[str, Any] | None = None


def _constraint_type_key(value: str) -> str:
    return str(value).strip().lower()


def _normalize_intent_families(
    intent_families: tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    raw_values = intent_families or DEFAULT_INTENT_FAMILIES
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        for part in str(raw_value).split(","):
            family = part.strip()
            if not family or family in seen:
                continue
            if family not in SUPPORTED_INTENT_FAMILIES:
                raise ValueError(
                    "Unsupported intent family: "
                    f"{family}. Expected one of {sorted(SUPPORTED_INTENT_FAMILIES)}."
                )
            normalized.append(family)
            seen.add(family)
    if not normalized:
        raise ValueError("At least one intent family must be enabled.")
    return tuple(normalized)


def _primitive_name_from_ref(ref: str) -> str:
    return ref.split(".", 1)[0]


def _sketch_index_for_op(payload: list[dict[str, Any]], op_index: int) -> int:
    sketch_index = -1
    for index, item in enumerate(payload):
        if str(item.get("operation", "")).strip() not in {"Fillet", "Chamfer"}:
            sketch_index += 1
        if index == op_index:
            return sketch_index
    raise IndexError(f"Operation index out of range: {op_index}")


def _iter_json_files(dataset_root: Path) -> list[Path]:
    return sorted(path for path in dataset_root.rglob("*.json") if path.is_file())


def _load_test_uid_stems(test_uids_json: Path) -> set[str]:
    payload = json.loads(test_uids_json.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON list in {test_uids_json}")
    return {str(item).strip() for item in payload if str(item).strip()}


def _prioritize_json_paths_by_stem(
    json_paths: list[Path],
    prioritized_sample_stems: set[str] | None,
) -> list[Path]:
    prioritized_sample_stems = set(prioritized_sample_stems or ())
    if not prioritized_sample_stems:
        return list(json_paths)
    prioritized_paths: list[Path] = []
    remaining_paths: list[Path] = []
    for json_path in json_paths:
        if json_path.stem in prioritized_sample_stems:
            prioritized_paths.append(json_path)
        else:
            remaining_paths.append(json_path)
    return prioritized_paths + remaining_paths


def _source_subset_from_directory_token(token: str) -> str | None:
    stripped = str(token).strip()
    if not (len(stripped) == 4 and stripped.isdigit()):
        return None
    numeric_id = int(stripped)
    if 1 <= numeric_id <= 99:
        return SOURCE_SUBSET_HISTCAD_DEEPCAD
    if numeric_id == 100:
        return SOURCE_SUBSET_HISTCAD_FUSION360
    if numeric_id == 101:
        return SOURCE_SUBSET_HISTCAD_INDUSTRIAL
    return None


def _infer_source_subset(json_path: Path | str) -> str:
    normalized_path = str(json_path).replace("\\", "/").lower()
    path_obj = Path(str(json_path))
    for part in path_obj.parts:
        mapped = _source_subset_from_directory_token(part)
        if mapped is not None:
            return mapped
    if "industrial" in normalized_path:
        return SOURCE_SUBSET_HISTCAD_INDUSTRIAL
    if (
        "fusion360" in normalized_path
        or "fusion_360" in normalized_path
        or "fusion-360" in normalized_path
    ):
        return SOURCE_SUBSET_HISTCAD_FUSION360
    if "deepcad" in normalized_path:
        return SOURCE_SUBSET_HISTCAD_DEEPCAD
    if "academic" in normalized_path:
        return SOURCE_SUBSET_HISTCAD_ACADEMIC
    return "unknown"


def _infer_source_domain(source_subset: str) -> str:
    normalized_subset = str(source_subset).strip().lower()
    if normalized_subset == SOURCE_SUBSET_HISTCAD_INDUSTRIAL.lower():
        return "industrial"
    if normalized_subset in {
        SOURCE_SUBSET_HISTCAD_ACADEMIC.lower(),
        SOURCE_SUBSET_HISTCAD_DEEPCAD.lower(),
        SOURCE_SUBSET_HISTCAD_FUSION360.lower(),
    }:
        return "academic"
    return "unknown"


def _normalized_constraint_records(
    converter: JsonToFusionConverter,
    constraints: dict[str, Any],
) -> list[ConstraintRecord]:
    records: list[ConstraintRecord] = []
    for constraint_type, raw_entries in (constraints or {}).items():
        entries = converter._normalize_constraint_entries(
            raw_entries,
            constraint_type=constraint_type,
        )
        for entry_index, entry in enumerate(entries):
            entities, value, extra = converter._parse_constraint_entry(
                constraint_type, entry
            )
            records.append(
                ConstraintRecord(
                    constraint_type=str(constraint_type),
                    entry_index=int(entry_index),
                    entities=tuple(str(entity) for entity in entities),
                    value=value,
                    extra=dict(extra) if isinstance(extra, dict) else extra,
                )
            )
    return records


def _parse_dimension_value_mm(
    converter: JsonToFusionConverter,
    constraint_type: str,
    value: float | str | None,
) -> float:
    if value is None:
        raise ValueError(f"{constraint_type} is missing a value.")
    if isinstance(value, (int, float)):
        return float(value)

    normalized = converter._normalize_dimension_expression(constraint_type, str(value))
    evaluation = converter._evaluate_length_expression_cm(normalized)
    if evaluation is not None:
        return float(evaluation[0]) / float(converter.UNITLESS_LENGTH_SCALE)
    body, unit = converter._split_dimension_unit_suffix(normalized)
    canonical_body = converter._canonicalize_fractional_body(body)
    parsed = converter._evaluate_fractional_literal(canonical_body)
    if parsed is None:
        parsed_value = float(body)
    else:
        parsed_value = float(parsed)
    unit_key = (unit or "mm").strip().lower()
    if unit_key not in UNIT_TO_MM:
        raise ValueError(f"Unsupported unit in expression: {value}")
    return parsed_value * UNIT_TO_MM[unit_key]


def _format_mm_expression(value_mm: float) -> str:
    return f"{float(value_mm):.10g} mm"


def _parse_angle_value_deg(value: float | str | None) -> float:
    if value is None:
        raise ValueError("angle is missing a value.")
    if isinstance(value, (int, float)):
        return float(value)
    normalized = str(value).strip().lower()
    for suffix in ("degrees", "degree", "deg"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
            break
    return float(normalized)


def _constraint_value_domain(constraint_type: str) -> str:
    normalized_type = _constraint_type_key(constraint_type)
    if normalized_type in ANGLE_DIMENSION_TYPES:
        return "angle"
    if normalized_type in SUPPORTED_EDIT_DIMENSION_TYPES:
        return "length"
    raise ValueError(f"Unsupported edit dimension type: {constraint_type}")


def _parse_candidate_value_numeric(
    converter: JsonToFusionConverter,
    constraint_type: str,
    value: float | str | None,
) -> float:
    if _constraint_value_domain(constraint_type) == "angle":
        return _parse_angle_value_deg(value)
    return _parse_dimension_value_mm(converter, constraint_type, value)


def _format_constraint_value(
    constraint_type: str,
    numeric_value: float,
) -> float | str:
    if _constraint_value_domain(constraint_type) == "angle":
        return round(float(numeric_value), 10)
    return _format_mm_expression(numeric_value)


def _manifest_value_repr(value: float | str) -> float | str:
    return float(value) if isinstance(value, (int, float)) else str(value)


def _cm_to_mm(converter: JsonToFusionConverter, value_cm: float) -> float:
    return float(value_cm) / float(converter.UNITLESS_LENGTH_SCALE)


def _normalize_angle_target_deg(value: float) -> float:
    normalized = float(value) % 360.0
    if math.isclose(normalized, 0.0, abs_tol=1e-9) and not math.isclose(
        float(value), 0.0, abs_tol=1e-9
    ):
        return 360.0
    return normalized


def _sample_length_edit_scale(
    rng: random.Random,
    *,
    scale_min: float,
    scale_max: float,
) -> float:
    if scale_min <= 0 or scale_max <= 0:
        raise ValueError("Length edit scales must be positive.")
    if scale_max < scale_min:
        raise ValueError("edit_scale_max must be >= edit_scale_min.")

    intervals: list[tuple[float, float]] = []
    lower_upper = min(scale_max, DEFAULT_EDIT_SCALE_NEUTRAL_HOLE_MIN)
    if scale_min < lower_upper:
        intervals.append((scale_min, lower_upper))
    upper_lower = max(scale_min, DEFAULT_EDIT_SCALE_NEUTRAL_HOLE_MAX)
    if upper_lower < scale_max:
        intervals.append((upper_lower, scale_max))

    if not intervals:
        raise ValueError(
            "Length edit scale range leaves no valid values after excluding scales near 1.0."
        )

    if len(intervals) == 1:
        lower, upper = intervals[0]
        return rng.uniform(lower, upper)

    total_width = sum(upper - lower for lower, upper in intervals)
    offset = rng.uniform(0.0, total_width)
    traversed = 0.0
    for lower, upper in intervals:
        width = upper - lower
        if offset <= traversed + width:
            return lower + (offset - traversed)
        traversed += width

    return intervals[-1][1]


def _value_key_for_constraint(
    converter: JsonToFusionConverter,
    constraint_type: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    normalized_type = _constraint_type_key(constraint_type)
    candidate_keys = converter.VALUE_KEYS.get(normalized_type, ())
    if metadata:
        for key in candidate_keys:
            if key in metadata:
                return key
    if normalized_type == "distance":
        return "length"
    if candidate_keys:
        return str(candidate_keys[0])
    return "value"


def _normalize_entity_signature(
    constraint_type: str,
    entities: tuple[str, ...],
    extra: dict[str, Any] | None = None,
) -> tuple[Any, ...]:
    normalized_type = _constraint_type_key(constraint_type)
    if normalized_type == "midpoint":
        if len(entities) == 2:
            return (normalized_type, entities[0], entities[1])
        if len(entities) == 3:
            return (normalized_type, entities[0], tuple(sorted(entities[1:])))
    if normalized_type == "distance":
        direction = (
            str((extra or {}).get("direction", "MINIMUM")).strip().upper() or "MINIMUM"
        )
        return (normalized_type, direction, tuple(sorted(entities)))
    if normalized_type in {
        "angle",
        "coincident",
        "concentric",
        "equal",
        "horizontal",
        "normal",
        "parallel",
        "perpendicular",
        "tangent",
        "vertical",
    }:
        if len(entities) <= 1:
            return (normalized_type, tuple(entities))
        return (normalized_type, tuple(sorted(entities)))
    return (normalized_type, tuple(entities))


def _record_signature(record: ConstraintRecord) -> tuple[Any, ...]:
    return _normalize_entity_signature(
        record.constraint_type, record.entities, record.extra
    )


def _candidate_identity(candidate: LengthEditCandidate) -> tuple[Any, ...]:
    return (
        str(candidate.json_path),
        int(candidate.op_index),
        int(candidate.sketch_index),
        str(candidate.intent_family),
        _constraint_type_key(candidate.constraint_type),
        candidate.entry_index,
        _normalize_entity_signature(
            candidate.constraint_type, candidate.entities, candidate.extra
        ),
    )


def _source_json_key(json_path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(json_path)))


def _candidate_source_json_key(candidate: LengthEditCandidate) -> str:
    return _source_json_key(candidate.json_path)


def _json_lists_to_tuples(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_json_lists_to_tuples(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _json_lists_to_tuples(item) for key, item in value.items()}
    return value


def _stable_uid_from_value(value: Any, *, prefix: str = "uid", length: int = 12) -> str:
    serialized = json.dumps(
        _json_lists_to_tuples(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def _source_families_from_reserved_candidate_keys(
    reserved_candidate_keys: set[tuple[Any, ...]],
) -> dict[str, set[str]]:
    reserved_source_families: dict[str, set[str]] = {}
    for candidate_key in reserved_candidate_keys:
        if len(candidate_key) < 4:
            continue
        source_key = _source_json_key(candidate_key[0])
        family = str(candidate_key[3]).strip()
        if not family:
            continue
        reserved_source_families.setdefault(source_key, set()).add(family)
    return reserved_source_families


def _source_keys_from_reserved_candidate_keys(
    reserved_candidate_keys: set[tuple[Any, ...]],
) -> set[str]:
    source_keys: set[str] = set()
    for candidate_key in reserved_candidate_keys:
        if not candidate_key:
            continue
        source_keys.add(_source_json_key(candidate_key[0]))
    return source_keys


def _sample_uid_from_candidate(candidate: LengthEditCandidate) -> str:
    return _stable_uid_from_value(_candidate_identity(candidate))


def _sample_uid_from_manifest_entry(entry: dict[str, Any]) -> str:
    raw_uid = str(entry.get("sample_uid") or "").strip()
    if raw_uid:
        return raw_uid
    return _stable_uid_from_value(_reserved_candidate_key_from_manifest_entry(entry))


def _sanitize_visualization_dir_component(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in str(value).strip()
    )
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("._")
    if not cleaned:
        return "source"
    return cleaned[:80]


def _sample_source_name_from_json_path(json_path: Path | str) -> str:
    return _sanitize_visualization_dir_component(Path(str(json_path)).stem)


def _sample_source_name_from_candidate(candidate: LengthEditCandidate) -> str:
    return _sample_source_name_from_json_path(candidate.json_path)


def _sample_source_name_from_manifest_entry(entry: dict[str, Any]) -> str:
    return _sample_source_name_from_json_path(entry.get("json_path") or "source")


def _visualization_sample_dir_name(sample_id: int, sample_name: str) -> str:
    normalized_name = _sanitize_visualization_dir_component(sample_name)
    return f"sample_{sample_id:03d}_{normalized_name}"


def _serialize_candidate(candidate: LengthEditCandidate) -> dict[str, Any]:
    return {
        "json_path": str(candidate.json_path),
        "op_index": int(candidate.op_index),
        "sketch_index": int(candidate.sketch_index),
        "constraint_type": str(candidate.constraint_type),
        "entry_index": int(candidate.entry_index)
        if candidate.entry_index is not None
        else None,
        "entities": list(candidate.entities),
        "line_name": str(candidate.line_name),
        "original_value_expr": candidate.original_value_expr,
        "original_value_mm": candidate.original_value_mm,
        "intent_family": str(candidate.intent_family),
        "sketch_constraint_count": int(candidate.sketch_constraint_count),
        "value_domain": str(candidate.value_domain),
        "extra": dict(candidate.extra)
        if isinstance(candidate.extra, dict)
        else candidate.extra,
    }


def _deserialize_candidate(payload: dict[str, Any]) -> LengthEditCandidate:
    raw_extra = payload.get("extra")
    extra = dict(raw_extra) if isinstance(raw_extra, dict) else raw_extra
    return LengthEditCandidate(
        json_path=Path(str(payload["json_path"])),
        op_index=int(payload["op_index"]),
        sketch_index=int(payload["sketch_index"]),
        constraint_type=str(payload["constraint_type"]),
        entry_index=(
            int(payload["entry_index"])
            if payload.get("entry_index") is not None
            else None
        ),
        entities=tuple(str(entity) for entity in payload.get("entities", [])),
        line_name=str(payload["line_name"]),
        original_value_expr=payload.get("original_value_expr"),
        original_value_mm=(
            float(payload["original_value_mm"])
            if payload.get("original_value_mm") is not None
            else None
        ),
        intent_family=str(
            payload.get("intent_family") or INTENT_FAMILY_MODIFY_DIMENSION
        ),
        sketch_constraint_count=int(payload.get("sketch_constraint_count", 0)),
        value_domain=str(payload.get("value_domain") or "length"),
        extra=extra,
    )


def _file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_run_checkpoint_config(
    *,
    dataset_root: Path,
    output_dir: Path,
    sample_size: int,
    sample_size_per_source_subset: int | None,
    test_uids_json: Path | None,
    seed: int,
    normalized_intent_families: tuple[str, ...],
    edit_scale_min: float,
    edit_scale_max: float,
    angle_delta_min_deg: float,
    angle_delta_max_deg: float,
    host: str,
    port: int,
    length_tol_cm: float,
    angle_tol_deg: float,
    max_samples_per_file_per_family: int,
    target_value_retry_attempts: int,
    restart_every: int,
    restart_timeout_seconds: float,
    restart_poll_seconds: float,
    health_timeout_seconds: float,
    restart_retry_attempts: int,
    restart_retry_backoff_seconds: float,
    restart_kill_existing: bool,
    client_timeout_seconds: float | None,
    visualize: bool,
    render_visualization_assets_during_run: bool,
    visualization_output_dir: Path | None,
    visualization_selection_mode: str,
    visualization_max_samples: int,
    visualization_sample_ids: list[int] | None,
    visualization_image_size: int,
    visualization_view_orientation: str,
    visualization_gallery_columns: int,
) -> dict[str, Any]:
    return {
        "dataset_root": str(dataset_root.resolve()),
        "output_dir": str(output_dir.resolve()),
        "sample_size": int(sample_size),
        "sample_size_per_source_subset": (
            int(sample_size_per_source_subset)
            if sample_size_per_source_subset is not None
            else None
        ),
        "test_uids_json": (
            str(test_uids_json.resolve()) if test_uids_json is not None else None
        ),
        "test_uids_json_sha1": (
            _file_sha1(test_uids_json)
            if test_uids_json is not None and test_uids_json.is_file()
            else None
        ),
        "seed": int(seed),
        "intent_families": list(normalized_intent_families),
        "edit_scale_min": float(edit_scale_min),
        "edit_scale_max": float(edit_scale_max),
        "edit_scale_neutral_hole_min": float(DEFAULT_EDIT_SCALE_NEUTRAL_HOLE_MIN),
        "edit_scale_neutral_hole_max": float(DEFAULT_EDIT_SCALE_NEUTRAL_HOLE_MAX),
        "angle_delta_min_deg": float(angle_delta_min_deg),
        "angle_delta_max_deg": float(angle_delta_max_deg),
        "host": str(host),
        "port": int(port),
        "length_tol_cm": float(length_tol_cm),
        "angle_tol_deg": float(angle_tol_deg),
        "max_samples_per_file_per_family": int(max_samples_per_file_per_family),
        "target_value_retry_attempts": int(target_value_retry_attempts),
        "restart_every": int(restart_every),
        "restart_timeout_seconds": float(restart_timeout_seconds),
        "restart_poll_seconds": float(restart_poll_seconds),
        "health_timeout_seconds": float(health_timeout_seconds),
        "restart_retry_attempts": int(restart_retry_attempts),
        "restart_retry_backoff_seconds": float(restart_retry_backoff_seconds),
        "restart_kill_existing": bool(restart_kill_existing),
        "client_timeout_seconds": (
            float(client_timeout_seconds)
            if client_timeout_seconds is not None
            else None
        ),
        "visualize": bool(visualize),
        "render_visualization_assets_during_run": bool(
            render_visualization_assets_during_run
        ),
        "visualization_output_dir": (
            str(visualization_output_dir.resolve())
            if visualization_output_dir is not None
            else None
        ),
        "visualization_selection_mode": str(visualization_selection_mode),
        "visualization_max_samples": int(visualization_max_samples),
        "visualization_sample_ids": (
            [int(sample_id) for sample_id in visualization_sample_ids]
            if visualization_sample_ids is not None
            else None
        ),
        "visualization_image_size": int(visualization_image_size),
        "visualization_view_orientation": str(visualization_view_orientation),
        "visualization_gallery_columns": int(visualization_gallery_columns),
    }


def _validate_checkpoint_config(
    saved_config: dict[str, Any],
    current_config: dict[str, Any],
    *,
    allow_expanded_source_subset_sample_size: bool = False,
) -> None:
    def _field_is_compatible(key: str) -> bool:
        saved_value = saved_config.get(key)
        current_value = current_config.get(key)
        if saved_value == current_value:
            return True
        if (
            key == "sample_size_per_source_subset"
            and allow_expanded_source_subset_sample_size
            and saved_value is not None
            and current_value is not None
        ):
            # A running checkpoint can drain its saved queue and then backfill to the higher quota.
            return int(current_value) >= int(saved_value)
        return False

    mismatched_keys = [
        key for key in sorted(current_config) if not _field_is_compatible(key)
    ]
    if mismatched_keys:
        mismatch_text = ", ".join(mismatched_keys)
        raise ValueError(
            "Existing checkpoint is incompatible with the current run configuration. "
            f"Mismatched fields: {mismatch_text}."
        )


def _is_source_subset_sample_size_expansion(
    saved_config: dict[str, Any],
    current_config: dict[str, Any],
) -> bool:
    saved_value = saved_config.get("sample_size_per_source_subset")
    current_value = current_config.get("sample_size_per_source_subset")
    if saved_value is None or current_value is None:
        return False
    return int(current_value) > int(saved_value)


def _deserialize_results_csv_value(fieldname: str, raw_value: str) -> Any:
    if fieldname in RESULTS_CSV_BOOLEAN_FIELDS:
        normalized = str(raw_value).strip().lower()
        if not normalized:
            return None
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError(
            f"Unsupported boolean value in results CSV for {fieldname}: {raw_value}"
        )
    if fieldname in RESULTS_CSV_INTEGER_FIELDS:
        stripped = str(raw_value).strip()
        return int(stripped) if stripped else None
    if fieldname in RESULTS_CSV_FLOAT_FIELDS:
        stripped = str(raw_value).strip()
        return float(stripped) if stripped else None
    return raw_value


def _load_results_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {
                fieldname: _deserialize_results_csv_value(
                    fieldname, row.get(fieldname, "")
                )
                for fieldname in RESULTS_CSV_FIELDNAMES
            }
            for row in reader
        ]


def _reserved_candidate_key_from_manifest_entry(
    entry: dict[str, Any],
) -> tuple[Any, ...]:
    constraint_type = str(
        entry.get("target_constraint_type") or entry.get("constraint_type") or ""
    )
    raw_entities = entry.get("target_entities")
    if raw_entities is None:
        raw_entities = entry.get("entities") or []
    entities = tuple(str(entity) for entity in raw_entities)
    raw_extra = entry.get("target_extra")
    extra = dict(raw_extra) if isinstance(raw_extra, dict) else raw_extra
    raw_entry_index = entry.get("target_entry_index")
    entry_index = int(raw_entry_index) if raw_entry_index is not None else None
    return (
        str(entry["json_path"]),
        int(entry["op_index"]),
        int(entry["sketch_index"]),
        str(entry["intent_family"]),
        _constraint_type_key(constraint_type),
        entry_index,
        _normalize_entity_signature(constraint_type, entities, extra),
    )


def _rebuild_sampling_stats_from_completed_summary(
    summary: dict[str, Any],
    *,
    intent_families: tuple[str, ...],
    using_source_subset_sampling: bool,
) -> dict[str, Any]:
    selected_by_family = summary.get(
        "selected_candidate_counts_by_intent_family_pre_reference_target_validation",
        {},
    )
    sampling_stats: dict[str, Any] = {
        "files_total": int(summary.get("files_total", 0)),
        "files_scanned": int(summary.get("files_scanned", 0)),
        "files_with_candidates": int(summary.get("files_with_candidates", 0)),
        "stopped_early": bool(summary.get("stopped_early_after_quota", False)),
        "observed_candidate_count": int(summary.get("eligible_candidate_count", 0)),
        "observed_candidate_count_by_intent_family": {
            family: int(
                (
                    summary.get("observed_candidate_count_by_intent_family", {}) or {}
                ).get(family, 0)
            )
            for family in intent_families
        },
        "assigned_file_count_by_intent_family": {
            family: int(
                (summary.get("assigned_file_count_by_intent_family", {}) or {}).get(
                    family, 0
                )
            )
            for family in intent_families
        },
        "sample_counts_by_intent_family": {
            family: int((selected_by_family or {}).get(family, 0))
            for family in intent_families
        },
    }
    if using_source_subset_sampling:
        selected_by_subset = summary.get(
            "selected_candidate_counts_by_source_subset_pre_reference_target_validation",
            {},
        )
        selected_by_subset_and_family = summary.get(
            "selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation",
            {},
        )
        sampling_stats.update(
            {
                "files_skipped_outside_target_subsets": int(
                    summary.get("files_skipped_outside_target_subsets", 0)
                ),
                "observed_candidate_count_by_source_subset": {
                    subset: int(
                        (
                            summary.get("observed_candidate_count_by_source_subset", {})
                            or {}
                        ).get(subset, 0)
                    )
                    for subset in KNOWN_SOURCE_SUBSETS
                },
                "assigned_file_count_by_source_subset": {
                    subset: int(
                        (
                            summary.get("assigned_file_count_by_source_subset", {})
                            or {}
                        ).get(subset, 0)
                    )
                    for subset in KNOWN_SOURCE_SUBSETS
                },
                "sample_counts_by_source_subset": {
                    subset: int((selected_by_subset or {}).get(subset, 0))
                    for subset in KNOWN_SOURCE_SUBSETS
                },
                "sample_counts_by_source_subset_and_intent_family": {
                    subset: {
                        family: int(
                            (selected_by_subset_and_family.get(subset, {}) or {}).get(
                                family, 0
                            )
                        )
                        for family in intent_families
                    }
                    for subset in KNOWN_SOURCE_SUBSETS
                },
            }
        )
    return sampling_stats


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(temp_handle.name)
    try:
        with temp_handle:
            temp_handle.write(text)
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _write_json_atomically(path: Path, payload: Any) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_results_csv_atomically(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(temp_handle.name)
    try:
        with temp_handle as handle:
            writer = csv.DictWriter(handle, fieldnames=RESULTS_CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _persist_experiment_progress(
    *,
    checkpoint_path: Path,
    manifest_path: Path,
    csv_path: Path,
    details_path: Path,
    checkpoint_payload: dict[str, Any],
    manifest: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    detailed_cases: list[dict[str, Any]],
) -> None:
    _write_json_atomically(checkpoint_path, checkpoint_payload)
    _write_json_atomically(manifest_path, manifest)
    _write_results_csv_atomically(csv_path, rows)
    _write_json_atomically(details_path, detailed_cases)


def _build_running_checkpoint_payload(
    *,
    run_config: dict[str, Any],
    sampling_stats: dict[str, Any],
    initial_selected_candidate_count: int,
    candidate_queue: list[LengthEditCandidate],
    manifest: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    detailed_cases: list[dict[str, Any]],
    skipped_reference_infeasible_by_intent_family: dict[str, int],
    skipped_transport_timeout_by_intent_family: dict[str, int],
    selected_candidate_count_pre_reference_target_validation: int,
    selected_candidate_counts_by_intent_family_pre_reference_target_validation: dict[
        str, int
    ],
    selected_candidate_counts_by_source_subset_pre_reference_target_validation: dict[
        str, int
    ],
    selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation: dict[
        str, dict[str, int]
    ],
    replacement_sampling_batch_count: int,
    replacement_sampling_call_count: int,
    accepted_counts_by_intent_family: dict[str, int],
    accepted_counts_by_source_subset_and_intent_family: dict[str, dict[str, int]],
    reserved_candidate_keys: set[tuple[Any, ...]],
    rng: random.Random,
) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "status": "running",
        "run_config": run_config,
        "sampling_stats": sampling_stats,
        "initial_selected_candidate_count": int(initial_selected_candidate_count),
        "candidate_queue": [
            _serialize_candidate(candidate) for candidate in candidate_queue
        ],
        "manifest": manifest,
        "rows": rows,
        "detailed_cases": detailed_cases,
        "skipped_reference_infeasible_by_intent_family": skipped_reference_infeasible_by_intent_family,
        "skipped_transport_timeout_by_intent_family": skipped_transport_timeout_by_intent_family,
        "selected_candidate_count_pre_reference_target_validation": int(
            selected_candidate_count_pre_reference_target_validation
        ),
        "selected_candidate_counts_by_intent_family_pre_reference_target_validation": (
            selected_candidate_counts_by_intent_family_pre_reference_target_validation
        ),
        "selected_candidate_counts_by_source_subset_pre_reference_target_validation": (
            selected_candidate_counts_by_source_subset_pre_reference_target_validation
        ),
        "selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation": (
            selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation
        ),
        "replacement_sampling_batch_count": int(replacement_sampling_batch_count),
        "replacement_sampling_call_count": int(replacement_sampling_call_count),
        "accepted_counts_by_intent_family": accepted_counts_by_intent_family,
        "accepted_counts_by_source_subset_and_intent_family": (
            accepted_counts_by_source_subset_and_intent_family
        ),
        "reserved_candidate_keys": list(reserved_candidate_keys),
        "rng_state": rng.getstate(),
    }


def _load_checkpoint_payload(checkpoint_path: Path) -> dict[str, Any] | None:
    if not checkpoint_path.is_file():
        return None
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if int(payload.get("version", 0)) != CHECKPOINT_VERSION:
        raise ValueError(
            f"Unsupported checkpoint version in {checkpoint_path}: {payload.get('version')}"
        )
    return payload


def _load_source_payload_cached(
    *,
    converter: JsonToFusionConverter,
    json_path: Path,
    cache: OrderedDict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    cache_key = str(json_path)
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        cache.move_to_end(cache_key)
        return cached_payload

    payload = converter.load_json(json_path)
    cache[cache_key] = payload
    if len(cache) > SOURCE_PAYLOAD_CACHE_MAX_ENTRIES:
        cache.popitem(last=False)
    return payload


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


def _build_sketch_live_refs(
    sketch: dict[str, Any],
    converter: JsonToFusionConverter,
) -> dict[str, dict[str, Any]]:
    live_refs: dict[str, dict[str, Any]] = {}
    # Source HistCAD sketches are stored in mm, while the evaluator and Fusion
    # runtime geometry both operate in cm.
    length_scale = float(converter.UNITLESS_LENGTH_SCALE)

    def _scale_xy_pair(point: Any) -> tuple[float, float]:
        return (
            float(point[0]) * length_scale,
            float(point[1]) * length_scale,
        )

    for primitive_name, primitive in sketch.items():
        if primitive_name.startswith("line_"):
            start = _scale_xy_pair(primitive["start"])
            end = _scale_xy_pair(primitive["end"])
            live_refs[primitive_name] = {
                "entity_id": primitive_name,
                "kind": "line",
                "geometry": {"start": [start[0], start[1]], "end": [end[0], end[1]]},
            }
            live_refs[f"{primitive_name}.start"] = {
                "entity_id": f"{primitive_name}.start",
                "kind": "point",
                "geometry": {"point": [start[0], start[1]]},
            }
            live_refs[f"{primitive_name}.end"] = {
                "entity_id": f"{primitive_name}.end",
                "kind": "point",
                "geometry": {"point": [end[0], end[1]]},
            }
        elif primitive_name.startswith("circle_"):
            center = _scale_xy_pair(primitive["center"])
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
            start = _scale_xy_pair(primitive["start"])
            middle = _scale_xy_pair(primitive["middle"])
            end = _scale_xy_pair(primitive["end"])
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


def _ordered_primitive_names(
    sketch: dict[str, Any], prefixes: tuple[str, ...]
) -> list[str]:
    return [
        primitive_name
        for primitive_name in sorted(sketch)
        if any(primitive_name.startswith(f"{prefix}_") for prefix in prefixes)
    ]


def _point_refs_from_sketch_live_refs(
    live_refs: dict[str, dict[str, Any]],
) -> list[str]:
    return sorted(
        ref for ref, payload in live_refs.items() if payload.get("kind") == "point"
    )


def _point_refs_by_nearest_neighbors(
    point_refs: list[str],
    sketch_live_refs: dict[str, dict[str, Any]],
    *,
    max_neighbors: int,
) -> list[tuple[str, str]]:
    if max_neighbors <= 0:
        return []
    point_positions: dict[str, tuple[float, float]] = {}
    for point_ref in point_refs:
        try:
            point_positions[point_ref] = _point_xy(sketch_live_refs[point_ref])
        except Exception:
            continue

    candidate_pairs: set[tuple[str, str]] = set()
    for point_ref in point_refs:
        point_xy = point_positions.get(point_ref)
        if point_xy is None:
            continue
        primitive_name = _primitive_name_from_ref(point_ref)
        distances: list[tuple[float, str]] = []
        for other_ref in point_refs:
            if other_ref == point_ref:
                continue
            if _primitive_name_from_ref(other_ref) == primitive_name:
                continue
            other_xy = point_positions.get(other_ref)
            if other_xy is None:
                continue
            distances.append(
                (
                    math.hypot(point_xy[0] - other_xy[0], point_xy[1] - other_xy[1]),
                    other_ref,
                )
            )
        distances.sort(key=lambda item: (item[0], item[1]))
        for _distance, other_ref in distances[:max_neighbors]:
            candidate_pairs.add(tuple(sorted((point_ref, other_ref))))
    return sorted(candidate_pairs)


def _point_line_nearest_candidates(
    point_refs: list[str],
    line_names: list[str],
    sketch_live_refs: dict[str, dict[str, Any]],
    *,
    max_neighbors: int,
) -> list[tuple[str, str]]:
    if max_neighbors <= 0:
        return []
    candidates: set[tuple[str, str]] = set()
    line_segments: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {}
    point_positions: dict[str, tuple[float, float]] = {}

    for line_name in line_names:
        try:
            line_segments[line_name] = _line_segment(sketch_live_refs[line_name])
        except Exception:
            continue
    for point_ref in point_refs:
        try:
            point_positions[point_ref] = _point_xy(sketch_live_refs[point_ref])
        except Exception:
            continue

    for point_ref in point_refs:
        point_xy = point_positions.get(point_ref)
        if point_xy is None:
            continue
        primitive_name = _primitive_name_from_ref(point_ref)
        distances: list[tuple[float, str]] = []
        for line_name in line_names:
            if line_name == primitive_name:
                continue
            segment = line_segments.get(line_name)
            if segment is None:
                continue
            distances.append((_point_to_segment_distance(point_xy, segment), line_name))
        distances.sort(key=lambda item: (item[0], item[1]))
        for _distance, line_name in distances[:max_neighbors]:
            candidates.add((point_ref, line_name))
    return sorted(candidates)


def _midpoint_point_line_candidates(
    point_refs: list[str],
    line_names: list[str],
    sketch_live_refs: dict[str, dict[str, Any]],
    *,
    max_neighbors: int,
) -> list[tuple[str, str]]:
    if max_neighbors <= 0:
        return []
    candidates: set[tuple[str, str]] = set()
    point_positions: dict[str, tuple[float, float]] = {}
    line_midpoints: dict[str, tuple[float, float]] = {}

    for point_ref in point_refs:
        try:
            point_positions[point_ref] = _point_xy(sketch_live_refs[point_ref])
        except Exception:
            continue
    for line_name in line_names:
        try:
            start, end = _line_segment(sketch_live_refs[line_name])
        except Exception:
            continue
        line_midpoints[line_name] = (
            (start[0] + end[0]) * 0.5,
            (start[1] + end[1]) * 0.5,
        )

    for line_name in line_names:
        midpoint = line_midpoints.get(line_name)
        if midpoint is None:
            continue
        distances: list[tuple[float, str]] = []
        for point_ref in point_refs:
            if _primitive_name_from_ref(point_ref) == line_name:
                continue
            point_xy = point_positions.get(point_ref)
            if point_xy is None:
                continue
            distances.append(
                (
                    math.hypot(point_xy[0] - midpoint[0], point_xy[1] - midpoint[1]),
                    point_ref,
                )
            )
        distances.sort(key=lambda item: (item[0], item[1]))
        for _distance, point_ref in distances[:max_neighbors]:
            candidates.add((point_ref, line_name))
    return sorted(candidates)


def _measure_constraint_record_from_sketch(
    converter: JsonToFusionConverter,
    record: ConstraintRecord,
    sketch_live_refs: dict[str, dict[str, Any]],
    *,
    length_tol_cm: float,
    angle_tol_deg: float,
) -> dict[str, Any]:
    probe_record = record
    if (
        record.value is None
        and _constraint_type_key(record.constraint_type)
        in SUPPORTED_EDIT_DIMENSION_TYPES
    ):
        placeholder_value: float | str = (
            0.0
            if _constraint_value_domain(record.constraint_type) == "angle"
            else "0 mm"
        )
        probe_record = ConstraintRecord(
            constraint_type=record.constraint_type,
            entry_index=record.entry_index,
            entities=record.entities,
            value=placeholder_value,
            extra=record.extra,
        )
    return _evaluate_constraint_record(
        converter,
        probe_record,
        sketch_live_refs,
        length_tol_cm=length_tol_cm,
        angle_tol_deg=angle_tol_deg,
    )


def _constraint_record_supported(
    sketch: dict[str, Any],
    record: ConstraintRecord,
) -> bool:
    normalized_type = record.constraint_type.strip().lower()
    if normalized_type not in SUPPORTED_CONSTRAINT_TYPES:
        return False

    entity_kinds: list[tuple[str, str | None]] = []
    for entity_ref in record.entities:
        primitive_name = _primitive_name_from_ref(entity_ref)
        primitive_payload = sketch.get(primitive_name)
        if primitive_payload is None:
            return False
        primitive_kind = None
        for prefix in SUPPORTED_PRIMITIVE_KINDS:
            if primitive_name.startswith(f"{prefix}_"):
                primitive_kind = prefix
                break
        if primitive_kind is None:
            return False
        entity_kinds.append((entity_ref, primitive_kind))

    kinds = [kind for _entity_ref, kind in entity_kinds]

    if normalized_type in {"parallel", "perpendicular", "angle"}:
        return all(kind == "line" for kind in kinds)
    if normalized_type == "tangent":
        if len(kinds) != 2:
            return False
        return set(kinds).issubset({"line", "circle", "arc"}) and not all(
            kind == "line" for kind in kinds
        )
    if normalized_type == "normal":
        if len(kinds) != 2:
            return False
        return all(kind == "line" for kind in kinds) or (
            len([kind for kind in kinds if kind == "line"]) == 1
            and set(kinds).issubset({"line", "circle", "arc"})
        )
    if normalized_type == "length":
        return len(kinds) == 1 and kinds[0] == "line"
    if normalized_type in {"radius", "diameter"}:
        return len(kinds) == 1 and kinds[0] in {"circle", "arc"}
    if normalized_type == "equal":
        return len(set(kinds)) == 1 and kinds[0] in {"line", "circle", "arc"}
    if normalized_type == "concentric":
        return all(kind in {"circle", "arc"} for kind in kinds)
    if normalized_type == "distance":
        direction = (
            str((record.extra or {}).get("direction", "MINIMUM")).strip().upper()
        )
        if len(kinds) == 1:
            return kinds[0] == "line"
        if len(kinds) != 2:
            return False
        entity_refs = record.entities
        point_count = sum("." in ref for ref in entity_refs)
        if direction in {"MINIMUM", ""}:
            if point_count == 2:
                return True
            if point_count == 1:
                return set(kinds).issubset({"line"})
            return all(kind == "line" for kind in kinds)
        if direction in {"HORIZONTAL", "VERTICAL"}:
            return point_count == 2
        return False
    if normalized_type == "midpoint":
        if len(record.entities) == 2:
            return "." in record.entities[0] and kinds[1] == "line"
        if len(record.entities) == 3:
            return all("." in ref for ref in record.entities)
        return False
    if normalized_type == "coincident":
        return all("." in ref for ref in record.entities)
    if normalized_type in {"horizontal", "vertical"}:
        if len(record.entities) == 1:
            return kinds[0] == "line"
        return all("." in ref for ref in record.entities)
    return True


def _supported_constraint_records_for_sketch(
    sketch: dict[str, Any],
    records: list[ConstraintRecord],
) -> list[ConstraintRecord]:
    if not isinstance(sketch, dict) or not sketch:
        return []
    return [
        record for record in records if _constraint_record_supported(sketch, record)
    ]


def _supported_source_records_for_candidate(
    source_payload: list[dict[str, Any]],
    candidate: LengthEditCandidate,
    converter: JsonToFusionConverter,
) -> list[ConstraintRecord]:
    item = source_payload[candidate.op_index]
    sketch = item.get("sketch")
    constraints = item.get("constraints") or {}
    if not isinstance(sketch, dict) or not sketch or not isinstance(constraints, dict):
        return []
    records = _normalized_constraint_records(converter, constraints)
    return _supported_constraint_records_for_sketch(sketch, records)


def _candidate_primary_entity(entities: tuple[str, ...]) -> str:
    if not entities:
        return ""
    return _primitive_name_from_ref(entities[0])


def _make_dimension_candidate(
    *,
    intent_family: str,
    json_path: Path,
    op_index: int,
    sketch_index: int,
    constraint_type: str,
    entry_index: int | None,
    entities: tuple[str, ...],
    value_numeric: float,
    sketch_constraint_count: int,
    extra: dict[str, Any] | None = None,
) -> LengthEditCandidate:
    return LengthEditCandidate(
        json_path=json_path,
        op_index=op_index,
        sketch_index=sketch_index,
        constraint_type=constraint_type,
        entry_index=entry_index,
        entities=entities,
        line_name=_candidate_primary_entity(entities),
        original_value_expr=_format_constraint_value(constraint_type, value_numeric),
        original_value_mm=value_numeric,
        intent_family=intent_family,
        sketch_constraint_count=sketch_constraint_count,
        value_domain=_constraint_value_domain(constraint_type),
        extra=dict(extra) if isinstance(extra, dict) else extra,
    )


def _make_geometric_candidate(
    *,
    json_path: Path,
    op_index: int,
    sketch_index: int,
    constraint_type: str,
    entities: tuple[str, ...],
    sketch_constraint_count: int,
    extra: dict[str, Any] | None = None,
) -> LengthEditCandidate:
    return LengthEditCandidate(
        json_path=json_path,
        op_index=op_index,
        sketch_index=sketch_index,
        constraint_type=constraint_type,
        entry_index=None,
        entities=entities,
        line_name=_candidate_primary_entity(entities),
        original_value_expr=None,
        original_value_mm=None,
        intent_family=INTENT_FAMILY_ADD_GEOMETRIC,
        sketch_constraint_count=sketch_constraint_count,
        value_domain="relation",
        extra=dict(extra) if isinstance(extra, dict) else extra,
    )


def _collect_existing_signatures(
    records: list[ConstraintRecord],
) -> set[tuple[Any, ...]]:
    return {_record_signature(record) for record in records}


def _measure_dimension_candidate_numeric(
    converter: JsonToFusionConverter,
    constraint_type: str,
    entities: tuple[str, ...],
    sketch_live_refs: dict[str, dict[str, Any]],
    *,
    extra: dict[str, Any] | None,
    length_tol_cm: float,
    angle_tol_deg: float,
) -> float | None:
    placeholder_value: float | str = (
        0.0 if _constraint_value_domain(constraint_type) == "angle" else "0 mm"
    )
    try:
        measurement = _measure_constraint_record_from_sketch(
            converter,
            ConstraintRecord(
                constraint_type=constraint_type,
                entry_index=-1,
                entities=entities,
                value=placeholder_value,
                extra=extra,
            ),
            sketch_live_refs,
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
        )
    except UnsupportedConstraintError:
        return None
    actual = measurement.get("actual")
    if actual is None:
        return None
    if _constraint_value_domain(constraint_type) == "angle":
        return float(actual)
    return _cm_to_mm(converter, float(actual))


def _is_record_currently_satisfied(
    converter: JsonToFusionConverter,
    record: ConstraintRecord,
    sketch_live_refs: dict[str, dict[str, Any]],
    *,
    length_tol_cm: float,
    angle_tol_deg: float,
) -> bool:
    try:
        result = _measure_constraint_record_from_sketch(
            converter,
            record,
            sketch_live_refs,
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
        )
    except UnsupportedConstraintError:
        return False
    return bool(result.get("ok"))


def _collect_modify_dimension_candidates_for_sketch(
    *,
    json_path: Path,
    op_index: int,
    sketch_index: int,
    records: list[ConstraintRecord],
    converter: JsonToFusionConverter,
    sketch_constraint_count: int | None = None,
) -> list[LengthEditCandidate]:
    candidates: list[LengthEditCandidate] = []
    total_constraint_count = (
        len(records)
        if sketch_constraint_count is None
        else int(sketch_constraint_count)
    )
    for record in records:
        normalized_type = _constraint_type_key(record.constraint_type)
        if normalized_type not in SUPPORTED_EDIT_DIMENSION_TYPES:
            continue
        try:
            value_numeric = _parse_candidate_value_numeric(
                converter,
                record.constraint_type,
                record.value,
            )
        except Exception:
            continue
        candidates.append(
            LengthEditCandidate(
                json_path=json_path,
                op_index=op_index,
                sketch_index=sketch_index,
                constraint_type=record.constraint_type,
                entry_index=record.entry_index,
                entities=record.entities,
                line_name=_candidate_primary_entity(record.entities),
                original_value_expr=record.value,
                original_value_mm=value_numeric,
                intent_family=INTENT_FAMILY_MODIFY_DIMENSION,
                sketch_constraint_count=total_constraint_count,
                value_domain=_constraint_value_domain(record.constraint_type),
                extra=dict(record.extra)
                if isinstance(record.extra, dict)
                else record.extra,
            )
        )
    return candidates


def _collect_add_dimension_candidates_for_sketch(
    *,
    json_path: Path,
    op_index: int,
    sketch_index: int,
    sketch: dict[str, Any],
    records: list[ConstraintRecord],
    converter: JsonToFusionConverter,
    length_tol_cm: float,
    angle_tol_deg: float,
) -> list[LengthEditCandidate]:
    candidates: list[LengthEditCandidate] = []
    signatures = _collect_existing_signatures(records)
    sketch_live_refs = _build_sketch_live_refs(sketch, converter)
    line_names = _ordered_primitive_names(sketch, ("line",))
    curve_names = _ordered_primitive_names(sketch, ("circle", "arc"))
    point_refs = _point_refs_from_sketch_live_refs(sketch_live_refs)

    for line_name in line_names:
        signature = _normalize_entity_signature("length", (line_name,))
        if signature in signatures:
            continue
        value_numeric = _measure_dimension_candidate_numeric(
            converter,
            "Length",
            (line_name,),
            sketch_live_refs,
            extra=None,
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
        )
        if value_numeric is None or value_numeric <= 1e-6:
            continue
        candidates.append(
            _make_dimension_candidate(
                intent_family=INTENT_FAMILY_ADD_DIMENSION,
                json_path=json_path,
                op_index=op_index,
                sketch_index=sketch_index,
                constraint_type="Length",
                entry_index=None,
                entities=(line_name,),
                value_numeric=value_numeric,
                sketch_constraint_count=len(records),
            )
        )

    for curve_name in curve_names:
        for constraint_type in ("Radius", "Diameter"):
            signature = _normalize_entity_signature(constraint_type, (curve_name,))
            if signature in signatures:
                continue
            value_numeric = _measure_dimension_candidate_numeric(
                converter,
                constraint_type,
                (curve_name,),
                sketch_live_refs,
                extra=None,
                length_tol_cm=length_tol_cm,
                angle_tol_deg=angle_tol_deg,
            )
            if value_numeric is None or value_numeric <= 1e-6:
                continue
            candidates.append(
                _make_dimension_candidate(
                    intent_family=INTENT_FAMILY_ADD_DIMENSION,
                    json_path=json_path,
                    op_index=op_index,
                    sketch_index=sketch_index,
                    constraint_type=constraint_type,
                    entry_index=None,
                    entities=(curve_name,),
                    value_numeric=value_numeric,
                    sketch_constraint_count=len(records),
                )
            )

    for index, left in enumerate(line_names):
        for right in line_names[index + 1 :]:
            signature = _normalize_entity_signature("Angle", (left, right))
            if signature in signatures:
                continue
            value_numeric = _measure_dimension_candidate_numeric(
                converter,
                "Angle",
                (left, right),
                sketch_live_refs,
                extra=None,
                length_tol_cm=length_tol_cm,
                angle_tol_deg=angle_tol_deg,
            )
            if value_numeric is None:
                continue
            try:
                deviation = _measure_constraint_record_from_sketch(
                    converter,
                    ConstraintRecord("Parallel", -1, (left, right), None, None),
                    sketch_live_refs,
                    length_tol_cm=length_tol_cm,
                    angle_tol_deg=angle_tol_deg,
                )
            except UnsupportedConstraintError:
                deviation = {"ok": False}
            if bool(deviation.get("ok")):
                continue
            candidates.append(
                _make_dimension_candidate(
                    intent_family=INTENT_FAMILY_ADD_DIMENSION,
                    json_path=json_path,
                    op_index=op_index,
                    sketch_index=sketch_index,
                    constraint_type="Angle",
                    entry_index=None,
                    entities=(left, right),
                    value_numeric=value_numeric,
                    sketch_constraint_count=len(records),
                )
            )

    point_pairs = _point_refs_by_nearest_neighbors(
        point_refs,
        sketch_live_refs,
        max_neighbors=DEFAULT_MAX_POINT_NEIGHBORS,
    )
    for left, right in point_pairs:
        for direction in ("MINIMUM", "HORIZONTAL", "VERTICAL"):
            extra = {"direction": direction}
            if _distance_candidate_redundant_with_length(
                (left, right),
                extra,
                sketch_live_refs,
                line_names,
            ):
                continue
            signature = _normalize_entity_signature("Distance", (left, right), extra)
            if signature in signatures:
                continue
            value_numeric = _measure_dimension_candidate_numeric(
                converter,
                "Distance",
                (left, right),
                sketch_live_refs,
                extra=extra,
                length_tol_cm=length_tol_cm,
                angle_tol_deg=angle_tol_deg,
            )
            if value_numeric is None or value_numeric <= 1e-6:
                continue
            candidates.append(
                _make_dimension_candidate(
                    intent_family=INTENT_FAMILY_ADD_DIMENSION,
                    json_path=json_path,
                    op_index=op_index,
                    sketch_index=sketch_index,
                    constraint_type="Distance",
                    entry_index=None,
                    entities=(left, right),
                    value_numeric=value_numeric,
                    sketch_constraint_count=len(records),
                    extra=extra,
                )
            )

    for point_ref, line_name in _point_line_nearest_candidates(
        point_refs,
        line_names,
        sketch_live_refs,
        max_neighbors=DEFAULT_MAX_POINT_LINE_NEIGHBORS,
    ):
        signature = _normalize_entity_signature(
            "Distance", (point_ref, line_name), {"direction": "MINIMUM"}
        )
        if signature in signatures:
            continue
        value_numeric = _measure_dimension_candidate_numeric(
            converter,
            "Distance",
            (point_ref, line_name),
            sketch_live_refs,
            extra={"direction": "MINIMUM"},
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
        )
        if value_numeric is None or value_numeric <= 1e-6:
            continue
        candidates.append(
            _make_dimension_candidate(
                intent_family=INTENT_FAMILY_ADD_DIMENSION,
                json_path=json_path,
                op_index=op_index,
                sketch_index=sketch_index,
                constraint_type="Distance",
                entry_index=None,
                entities=(point_ref, line_name),
                value_numeric=value_numeric,
                sketch_constraint_count=len(records),
                extra={"direction": "MINIMUM"},
            )
        )

    return candidates


def _collect_add_geometric_candidates_for_sketch(
    *,
    json_path: Path,
    op_index: int,
    sketch_index: int,
    sketch: dict[str, Any],
    records: list[ConstraintRecord],
    converter: JsonToFusionConverter,
    length_tol_cm: float,
    angle_tol_deg: float,
) -> list[LengthEditCandidate]:
    candidates: list[LengthEditCandidate] = []
    signatures = _collect_existing_signatures(records)
    sketch_live_refs = _build_sketch_live_refs(sketch, converter)
    line_names = _ordered_primitive_names(sketch, ("line",))
    circle_names = _ordered_primitive_names(sketch, ("circle",))
    arc_names = _ordered_primitive_names(sketch, ("arc",))
    curve_names = circle_names + arc_names
    point_refs = _point_refs_from_sketch_live_refs(sketch_live_refs)

    def _append_if_new(
        constraint_type: str,
        entities: tuple[str, ...],
        extra: dict[str, Any] | None = None,
    ) -> None:
        signature = _normalize_entity_signature(constraint_type, entities, extra)
        if signature in signatures:
            return
        record = ConstraintRecord(constraint_type, -1, entities, None, extra)
        if _is_record_currently_satisfied(
            converter,
            record,
            sketch_live_refs,
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
        ):
            return
        candidates.append(
            _make_geometric_candidate(
                json_path=json_path,
                op_index=op_index,
                sketch_index=sketch_index,
                constraint_type=constraint_type,
                entities=entities,
                sketch_constraint_count=len(records),
                extra=extra,
            )
        )

    for line_name in line_names:
        _append_if_new("Horizontal", (line_name,))
        _append_if_new("Vertical", (line_name,))

    for left, right in _point_refs_by_nearest_neighbors(
        point_refs,
        sketch_live_refs,
        max_neighbors=DEFAULT_MAX_POINT_NEIGHBORS,
    ):
        _append_if_new("Coincident", (left, right))
        _append_if_new("Horizontal", (left, right))
        _append_if_new("Vertical", (left, right))

    for point_ref, line_name in _midpoint_point_line_candidates(
        point_refs,
        line_names,
        sketch_live_refs,
        max_neighbors=DEFAULT_MAX_MIDPOINT_NEIGHBORS,
    ):
        _append_if_new("Midpoint", (point_ref, line_name))

    for index, left in enumerate(line_names):
        for right in line_names[index + 1 :]:
            _append_if_new("Parallel", (left, right))
            _append_if_new("Perpendicular", (left, right))
            _append_if_new("Equal", (left, right))

    for collection in (circle_names, arc_names):
        for index, left in enumerate(collection):
            for right in collection[index + 1 :]:
                _append_if_new("Equal", (left, right))

    for index, left in enumerate(curve_names):
        for right in curve_names[index + 1 :]:
            _append_if_new("Concentric", (left, right))
            _append_if_new("Tangent", (left, right))

    for line_name in line_names:
        for curve_name in curve_names:
            _append_if_new("Tangent", (line_name, curve_name))
            _append_if_new("Normal", (line_name, curve_name))

    return candidates


def _collect_eligible_candidates_for_payload(
    *,
    json_path: Path,
    payload: list[dict[str, Any]],
    converter: JsonToFusionConverter,
    intent_families: tuple[str, ...],
    length_tol_cm: float,
    angle_tol_deg: float,
) -> list[LengthEditCandidate]:
    file_candidates: list[LengthEditCandidate] = []
    for op_index, item in enumerate(payload):
        sketch = item.get("sketch")
        constraints = item.get("constraints") or {}
        if (
            not isinstance(sketch, dict)
            or not sketch
            or not isinstance(constraints, dict)
            or not constraints
        ):
            continue
        records = _normalized_constraint_records(converter, constraints)
        if not records:
            continue
        supported_records = _supported_constraint_records_for_sketch(sketch, records)
        sketch_index = _sketch_index_for_op(payload, op_index)
        if INTENT_FAMILY_MODIFY_DIMENSION in intent_families and supported_records:
            file_candidates.extend(
                _collect_modify_dimension_candidates_for_sketch(
                    json_path=json_path,
                    op_index=op_index,
                    sketch_index=sketch_index,
                    records=supported_records,
                    converter=converter,
                    sketch_constraint_count=len(records),
                )
            )
        if INTENT_FAMILY_ADD_DIMENSION in intent_families:
            file_candidates.extend(
                _collect_add_dimension_candidates_for_sketch(
                    json_path=json_path,
                    op_index=op_index,
                    sketch_index=sketch_index,
                    sketch=sketch,
                    records=records,
                    converter=converter,
                    length_tol_cm=length_tol_cm,
                    angle_tol_deg=angle_tol_deg,
                )
            )
        if INTENT_FAMILY_ADD_GEOMETRIC in intent_families:
            file_candidates.extend(
                _collect_add_geometric_candidates_for_sketch(
                    json_path=json_path,
                    op_index=op_index,
                    sketch_index=sketch_index,
                    sketch=sketch,
                    records=records,
                    converter=converter,
                    length_tol_cm=length_tol_cm,
                    angle_tol_deg=angle_tol_deg,
                )
            )
    return file_candidates


def _collect_eligible_candidates_for_file(
    json_path: Path,
    converter: JsonToFusionConverter,
    *,
    intent_families: tuple[str, ...],
    length_tol_cm: float,
    angle_tol_deg: float,
) -> list[LengthEditCandidate]:
    try:
        payload = converter.load_json(json_path)
    except Exception:
        return []
    return _collect_eligible_candidates_for_payload(
        json_path=json_path,
        payload=payload,
        converter=converter,
        intent_families=intent_families,
        length_tol_cm=length_tol_cm,
        angle_tol_deg=angle_tol_deg,
    )


def _sample_candidates_by_family(
    dataset_root: Path,
    converter: JsonToFusionConverter,
    *,
    client: Fusion360Client | None = None,
    rng: random.Random,
    sample_size: int,
    intent_families: tuple[str, ...],
    length_tol_cm: float,
    angle_tol_deg: float,
    prioritized_sample_stems: set[str] | None = None,
    max_samples_per_file_per_family: int = DEFAULT_MAX_SAMPLES_PER_FILE_PER_FAMILY,
    excluded_candidate_keys: set[tuple[Any, ...]] | None = None,
    reserved_source_families: dict[str, set[str]] | None = None,
) -> tuple[list[LengthEditCandidate], dict[str, Any]]:
    if sample_size <= 0:
        raise ValueError("sample_size must be positive.")
    if max_samples_per_file_per_family <= 0:
        raise ValueError("max_samples_per_file_per_family must be positive.")

    json_paths = _iter_json_files(dataset_root)
    if not json_paths:
        raise ValueError(f"No JSON files found under: {dataset_root}")
    rng.shuffle(json_paths)
    json_paths = _prioritize_json_paths_by_stem(json_paths, prioritized_sample_stems)
    excluded_candidate_keys = set(excluded_candidate_keys or ())
    local_reserved_source_families = {
        source_key: set(families)
        for source_key, families in (reserved_source_families or {}).items()
    }

    selected_by_family: dict[str, list[LengthEditCandidate]] = {
        family: [] for family in intent_families
    }
    selected_keys_by_family: dict[str, set[tuple[Any, ...]]] = {
        family: set() for family in intent_families
    }
    assigned_file_count_by_family: dict[str, int] = dict.fromkeys(intent_families, 0)
    observed_candidate_count_by_family: dict[str, int] = dict.fromkeys(
        intent_families, 0
    )
    files_scanned = 0
    files_with_candidates = 0

    for json_path in json_paths:
        if all(
            len(selected_by_family[family]) >= sample_size for family in intent_families
        ):
            break
        files_scanned += 1
        source_key = _source_json_key(json_path)
        locked_families = {
            family
            for family in local_reserved_source_families.get(source_key, set())
            if family
        }
        if locked_families:
            continue
        file_candidates = _collect_eligible_candidates_for_file(
            json_path,
            converter,
            intent_families=intent_families,
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
        )
        if not file_candidates:
            continue
        files_with_candidates += 1

        file_candidates_by_family: dict[str, list[LengthEditCandidate]] = {
            family: [] for family in intent_families
        }
        for candidate in file_candidates:
            family = str(candidate.intent_family)
            if family not in selected_by_family:
                continue
            observed_candidate_count_by_family[family] += 1
            if len(selected_by_family[family]) >= sample_size:
                continue
            candidate_key = _candidate_identity(candidate)
            if candidate_key in excluded_candidate_keys:
                continue
            if candidate_key in selected_keys_by_family[family]:
                continue
            file_candidates_by_family[family].append(candidate)

        eligible_families = [
            family
            for family in intent_families
            if (sample_size - len(selected_by_family[family])) > 0
            and file_candidates_by_family[family]
        ]
        if not eligible_families:
            continue

        remaining_by_family = {
            family: sample_size - len(selected_by_family[family])
            for family in eligible_families
        }
        max_remaining = max(remaining_by_family.values())
        tied_families = [
            family
            for family in eligible_families
            if remaining_by_family[family] == max_remaining
        ]
        assigned_family = rng.choice(tied_families)
        assigned_file_count_by_family[assigned_family] += 1

        family_candidates = file_candidates_by_family[assigned_family]
        remaining = sample_size - len(selected_by_family[assigned_family])
        source_payload: list[dict[str, Any]] | None = None
        rng.shuffle(family_candidates)
        accepted_from_file = 0
        limit_per_file = min(1, remaining, max_samples_per_file_per_family)
        precheck_attempts = 0
        for candidate in family_candidates:
            if accepted_from_file >= limit_per_file:
                break
            candidate_key = _candidate_identity(candidate)
            if candidate_key in excluded_candidate_keys:
                continue
            if candidate_key in selected_keys_by_family[assigned_family]:
                continue
            if (
                client is not None
                and candidate.intent_family != INTENT_FAMILY_MODIFY_DIMENSION
            ):
                if precheck_attempts >= DEFAULT_MAX_PRECHECKS_PER_FILE:
                    break
                precheck_attempts += 1
                if source_payload is None:
                    try:
                        source_payload = converter.load_json(json_path)
                    except Exception:
                        break
                if not _candidate_is_constrained_feasible(
                    converter=converter,
                    client=client,
                    source_payload=source_payload,
                    candidate=candidate,
                    length_tol_cm=length_tol_cm,
                    angle_tol_deg=angle_tol_deg,
                    case_name=(
                        f"candidate_precheck_{assigned_family}_{files_scanned}_"
                        f"{accepted_from_file + 1}"
                    ),
                ):
                    continue
            selected_by_family[assigned_family].append(candidate)
            selected_keys_by_family[assigned_family].add(candidate_key)
            local_reserved_source_families.setdefault(source_key, set()).add(
                assigned_family
            )
            accepted_from_file += 1

    selected_candidates = [
        candidate
        for family in intent_families
        for candidate in selected_by_family[family]
    ]
    if not selected_candidates:
        raise ValueError(f"No eligible editable sketches found under: {dataset_root}")

    return selected_candidates, {
        "files_total": len(json_paths),
        "files_scanned": files_scanned,
        "files_with_candidates": files_with_candidates,
        "stopped_early": files_scanned < len(json_paths),
        "observed_candidate_count": sum(observed_candidate_count_by_family.values()),
        "observed_candidate_count_by_intent_family": observed_candidate_count_by_family,
        "assigned_file_count_by_intent_family": assigned_file_count_by_family,
        "sample_counts_by_intent_family": {
            family: len(selected_by_family[family]) for family in intent_families
        },
        "quota_met_by_intent_family": {
            family: len(selected_by_family[family]) >= sample_size
            for family in intent_families
        },
        "remaining_quota_by_intent_family": {
            family: max(0, sample_size - len(selected_by_family[family]))
            for family in intent_families
        },
    }


def _sample_candidates_by_source_subset(
    dataset_root: Path,
    converter: JsonToFusionConverter,
    *,
    client: Fusion360Client | None = None,
    rng: random.Random,
    sample_size_per_source_subset: int,
    target_source_subsets: tuple[str, ...],
    intent_families: tuple[str, ...],
    length_tol_cm: float,
    angle_tol_deg: float,
    prioritized_sample_stems: set[str] | None = None,
    max_samples_per_file_per_family: int = DEFAULT_MAX_SAMPLES_PER_FILE_PER_FAMILY,
    excluded_candidate_keys: set[tuple[Any, ...]] | None = None,
    reserved_source_families: dict[str, set[str]] | None = None,
) -> tuple[list[LengthEditCandidate], dict[str, Any]]:
    if sample_size_per_source_subset <= 0:
        raise ValueError("sample_size_per_source_subset must be positive.")
    if max_samples_per_file_per_family <= 0:
        raise ValueError("max_samples_per_file_per_family must be positive.")

    json_paths = _iter_json_files(dataset_root)
    if not json_paths:
        raise ValueError(f"No JSON files found under: {dataset_root}")
    rng.shuffle(json_paths)
    json_paths = _prioritize_json_paths_by_stem(json_paths, prioritized_sample_stems)
    excluded_candidate_keys = set(excluded_candidate_keys or ())
    local_reserved_source_families = {
        source_key: set(families)
        for source_key, families in (reserved_source_families or {}).items()
    }

    selected_by_subset: dict[str, list[LengthEditCandidate]] = {
        subset: [] for subset in target_source_subsets
    }
    selected_by_subset_and_family: dict[str, dict[str, list[LengthEditCandidate]]] = {
        subset: {family: [] for family in intent_families}
        for subset in target_source_subsets
    }
    selected_keys_by_subset: dict[str, set[tuple[Any, ...]]] = {
        subset: set() for subset in target_source_subsets
    }
    assigned_file_count_by_subset: dict[str, int] = dict.fromkeys(
        target_source_subsets, 0
    )
    assigned_file_count_by_intent_family: dict[str, int] = dict.fromkeys(
        intent_families, 0
    )
    observed_candidate_count_by_subset: dict[str, int] = dict.fromkeys(
        target_source_subsets, 0
    )
    observed_candidate_count_by_intent_family: dict[str, int] = dict.fromkeys(
        intent_families, 0
    )
    files_scanned = 0
    files_with_candidates = 0
    files_skipped_outside_target_subsets = 0

    for json_path in json_paths:
        if all(
            all(
                len(selected_by_subset_and_family[subset][family])
                >= sample_size_per_source_subset
                for family in intent_families
            )
            for subset in target_source_subsets
        ):
            break
        files_scanned += 1
        source_subset = _infer_source_subset(json_path)
        if source_subset not in selected_by_subset:
            files_skipped_outside_target_subsets += 1
            continue
        source_key = _source_json_key(json_path)
        locked_families = {
            family
            for family in local_reserved_source_families.get(source_key, set())
            if family
        }
        if locked_families:
            continue
        if all(
            len(selected_by_subset_and_family[source_subset][family])
            >= sample_size_per_source_subset
            for family in intent_families
        ):
            continue

        file_candidates = _collect_eligible_candidates_for_file(
            json_path,
            converter,
            intent_families=intent_families,
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
        )
        if not file_candidates:
            continue
        files_with_candidates += 1

        file_candidates_by_family: dict[str, list[LengthEditCandidate]] = {
            family: [] for family in intent_families
        }
        for candidate in file_candidates:
            family = str(candidate.intent_family)
            if family not in file_candidates_by_family:
                continue
            observed_candidate_count_by_subset[source_subset] += 1
            observed_candidate_count_by_intent_family[family] += 1
            candidate_key = _candidate_identity(candidate)
            if candidate_key in excluded_candidate_keys:
                continue
            if candidate_key in selected_keys_by_subset[source_subset]:
                continue
            file_candidates_by_family[family].append(candidate)

        eligible_families = [
            family
            for family in intent_families
            if file_candidates_by_family[family]
            and len(selected_by_subset_and_family[source_subset][family])
            < sample_size_per_source_subset
        ]
        if not eligible_families:
            continue

        remaining_by_family = {
            family: sample_size_per_source_subset
            - len(selected_by_subset_and_family[source_subset][family])
            for family in eligible_families
        }
        max_remaining = max(remaining_by_family.values())
        tied_families = [
            family
            for family in eligible_families
            if remaining_by_family[family] == max_remaining
        ]
        assigned_family = rng.choice(tied_families)
        assigned_file_count_by_subset[source_subset] += 1
        assigned_file_count_by_intent_family[assigned_family] += 1

        family_candidates = file_candidates_by_family[assigned_family]
        remaining = sample_size_per_source_subset - len(
            selected_by_subset_and_family[source_subset][assigned_family]
        )
        source_payload: list[dict[str, Any]] | None = None
        rng.shuffle(family_candidates)
        accepted_from_file = 0
        limit_per_file = min(1, remaining, max_samples_per_file_per_family)
        precheck_attempts = 0
        for candidate in family_candidates:
            if accepted_from_file >= limit_per_file:
                break
            candidate_key = _candidate_identity(candidate)
            if candidate_key in excluded_candidate_keys:
                continue
            if candidate_key in selected_keys_by_subset[source_subset]:
                continue
            if (
                client is not None
                and candidate.intent_family != INTENT_FAMILY_MODIFY_DIMENSION
            ):
                if precheck_attempts >= DEFAULT_MAX_PRECHECKS_PER_FILE:
                    break
                precheck_attempts += 1
                if source_payload is None:
                    try:
                        source_payload = converter.load_json(json_path)
                    except Exception:
                        break
                if not _candidate_is_constrained_feasible(
                    converter=converter,
                    client=client,
                    source_payload=source_payload,
                    candidate=candidate,
                    length_tol_cm=length_tol_cm,
                    angle_tol_deg=angle_tol_deg,
                    case_name=(
                        f"candidate_precheck_{source_subset}_{assigned_family}_{files_scanned}_"
                        f"{accepted_from_file + 1}"
                    ),
                ):
                    continue
            selected_by_subset[source_subset].append(candidate)
            selected_by_subset_and_family[source_subset][assigned_family].append(
                candidate
            )
            selected_keys_by_subset[source_subset].add(candidate_key)
            local_reserved_source_families.setdefault(source_key, set()).add(
                assigned_family
            )
            accepted_from_file += 1

    selected_candidates = [
        candidate
        for subset in target_source_subsets
        for candidate in selected_by_subset[subset]
    ]
    if not selected_candidates:
        raise ValueError(f"No eligible editable sketches found under: {dataset_root}")

    return selected_candidates, {
        "files_total": len(json_paths),
        "files_scanned": files_scanned,
        "files_with_candidates": files_with_candidates,
        "files_skipped_outside_target_subsets": files_skipped_outside_target_subsets,
        "stopped_early": files_scanned < len(json_paths),
        "observed_candidate_count": sum(observed_candidate_count_by_subset.values()),
        "observed_candidate_count_by_intent_family": observed_candidate_count_by_intent_family,
        "observed_candidate_count_by_source_subset": observed_candidate_count_by_subset,
        "assigned_file_count_by_intent_family": assigned_file_count_by_intent_family,
        "assigned_file_count_by_source_subset": assigned_file_count_by_subset,
        "sample_counts_by_intent_family": {
            family: sum(
                len(selected_by_subset_and_family[subset][family])
                for subset in target_source_subsets
            )
            for family in intent_families
        },
        "sample_counts_by_source_subset": {
            subset: len(selected_by_subset[subset]) for subset in target_source_subsets
        },
        "sample_counts_by_source_subset_and_intent_family": {
            subset: {
                family: len(selected_by_subset_and_family[subset][family])
                for family in intent_families
            }
            for subset in target_source_subsets
        },
        "quota_met_by_source_subset": {
            subset: all(
                len(selected_by_subset_and_family[subset][family])
                >= sample_size_per_source_subset
                for family in intent_families
            )
            for subset in target_source_subsets
        },
        "remaining_quota_by_source_subset": {
            subset: max(
                0,
                (sample_size_per_source_subset * len(intent_families))
                - len(selected_by_subset[subset]),
            )
            for subset in target_source_subsets
        },
        "quota_met_by_source_subset_and_intent_family": {
            subset: {
                family: len(selected_by_subset_and_family[subset][family])
                >= sample_size_per_source_subset
                for family in intent_families
            }
            for subset in target_source_subsets
        },
        "remaining_quota_by_source_subset_and_intent_family": {
            subset: {
                family: max(
                    0,
                    sample_size_per_source_subset
                    - len(selected_by_subset_and_family[subset][family]),
                )
                for family in intent_families
            }
            for subset in target_source_subsets
        },
    }


def _sample_replacement_candidates_for_remaining_quota(
    *,
    dataset_root: Path,
    converter: JsonToFusionConverter,
    client: Fusion360Client | None,
    rng: random.Random,
    using_source_subset_sampling: bool,
    requested_sample_size: int,
    intent_families: tuple[str, ...],
    accepted_counts_by_intent_family: dict[str, int],
    accepted_counts_by_source_subset_and_intent_family: dict[str, dict[str, int]],
    length_tol_cm: float,
    angle_tol_deg: float,
    prioritized_sample_stems: set[str] | None,
    max_samples_per_file_per_family: int,
    excluded_candidate_keys: set[tuple[Any, ...]],
    reserved_source_families: dict[str, set[str]],
) -> tuple[list[LengthEditCandidate], dict[str, Any]]:
    local_excluded_candidate_keys = set(excluded_candidate_keys)
    local_reserved_source_families = {
        source_key: set(families)
        for source_key, families in reserved_source_families.items()
    }
    replacement_candidates: list[LengthEditCandidate] = []
    replacement_counts_by_intent_family: dict[str, int] = dict.fromkeys(
        intent_families, 0
    )
    replacement_counts_by_source_subset: dict[str, int] = dict.fromkeys(
        KNOWN_SOURCE_SUBSETS, 0
    )
    replacement_counts_by_source_subset_and_intent_family: dict[str, dict[str, int]] = {
        subset: dict.fromkeys(intent_families, 0) for subset in KNOWN_SOURCE_SUBSETS
    }
    sampling_call_count = 0

    if using_source_subset_sampling:
        for source_subset in KNOWN_SOURCE_SUBSETS:
            for family in intent_families:
                remaining = (
                    requested_sample_size
                    - accepted_counts_by_source_subset_and_intent_family[source_subset][
                        family
                    ]
                )
                if remaining <= 0:
                    continue
                try:
                    sampled_candidates, _sampling_stats = (
                        _sample_candidates_by_source_subset(
                            dataset_root,
                            converter,
                            client=client,
                            rng=rng,
                            sample_size_per_source_subset=remaining,
                            target_source_subsets=(source_subset,),
                            intent_families=(family,),
                            length_tol_cm=length_tol_cm,
                            angle_tol_deg=angle_tol_deg,
                            prioritized_sample_stems=prioritized_sample_stems,
                            max_samples_per_file_per_family=max_samples_per_file_per_family,
                            excluded_candidate_keys=local_excluded_candidate_keys,
                            reserved_source_families=local_reserved_source_families,
                        )
                    )
                except ValueError:
                    sampled_candidates = []
                if not sampled_candidates:
                    continue
                sampling_call_count += 1
                replacement_candidates.extend(sampled_candidates)
                replacement_counts_by_intent_family[family] += len(sampled_candidates)
                replacement_counts_by_source_subset[source_subset] += len(
                    sampled_candidates
                )
                replacement_counts_by_source_subset_and_intent_family[source_subset][
                    family
                ] += len(sampled_candidates)
                for candidate in sampled_candidates:
                    local_excluded_candidate_keys.add(_candidate_identity(candidate))
                    local_reserved_source_families.setdefault(
                        _candidate_source_json_key(candidate),
                        set(),
                    ).add(candidate.intent_family)
    else:
        for family in intent_families:
            remaining = requested_sample_size - accepted_counts_by_intent_family[family]
            if remaining <= 0:
                continue
            try:
                sampled_candidates, _sampling_stats = _sample_candidates_by_family(
                    dataset_root,
                    converter,
                    client=client,
                    rng=rng,
                    sample_size=remaining,
                    intent_families=(family,),
                    length_tol_cm=length_tol_cm,
                    angle_tol_deg=angle_tol_deg,
                    prioritized_sample_stems=prioritized_sample_stems,
                    max_samples_per_file_per_family=max_samples_per_file_per_family,
                    excluded_candidate_keys=local_excluded_candidate_keys,
                    reserved_source_families=local_reserved_source_families,
                )
            except ValueError:
                sampled_candidates = []
            if not sampled_candidates:
                continue
            sampling_call_count += 1
            replacement_candidates.extend(sampled_candidates)
            replacement_counts_by_intent_family[family] += len(sampled_candidates)
            for candidate in sampled_candidates:
                local_excluded_candidate_keys.add(_candidate_identity(candidate))
                local_reserved_source_families.setdefault(
                    _candidate_source_json_key(candidate),
                    set(),
                ).add(candidate.intent_family)

    return replacement_candidates, {
        "sampling_call_count": sampling_call_count,
        "sample_count": len(replacement_candidates),
        "sample_counts_by_intent_family": replacement_counts_by_intent_family,
        "sample_counts_by_source_subset": replacement_counts_by_source_subset,
        "sample_counts_by_source_subset_and_intent_family": replacement_counts_by_source_subset_and_intent_family,
    }


def _update_dimension_constraint_value(
    payload: list[dict[str, Any]],
    candidate: LengthEditCandidate,
    new_value: float,
    converter: JsonToFusionConverter,
) -> None:
    item = payload[candidate.op_index]
    constraints = dict(item.get("constraints") or {})
    raw_entries = constraints[candidate.constraint_type]
    normalized_entries = list(
        converter._normalize_constraint_entries(
            raw_entries,
            constraint_type=candidate.constraint_type,
        )
    )
    entry = normalized_entries[candidate.entry_index]
    if isinstance(entry, str):
        raise ValueError(
            f"{candidate.constraint_type} constraint entry must be a list."
        )
    updated_entry = list(entry)
    normalized_type = candidate.constraint_type.strip().lower()
    formatted_value = _format_constraint_value(candidate.constraint_type, new_value)
    if updated_entry and isinstance(updated_entry[-1], dict):
        metadata = dict(updated_entry[-1])
        metadata[
            _value_key_for_constraint(converter, candidate.constraint_type, metadata)
        ] = formatted_value
        updated_entry[-1] = metadata
    elif converter._has_inline_dimension_value(normalized_type, updated_entry):
        updated_entry[-1] = formatted_value
    else:
        updated_entry.append(formatted_value)
    normalized_entries[candidate.entry_index] = updated_entry
    constraints[candidate.constraint_type] = normalized_entries
    item["constraints"] = constraints


def _build_added_dimension_entry(
    candidate: LengthEditCandidate,
    new_value: float,
    converter: JsonToFusionConverter,
) -> list[Any]:
    entry: list[Any] = list(candidate.entities)
    formatted_value = _format_constraint_value(candidate.constraint_type, new_value)
    normalized_type = _constraint_type_key(candidate.constraint_type)
    if normalized_type == "angle":
        entry.append(formatted_value)
        return entry
    if candidate.extra:
        metadata = dict(candidate.extra)
        metadata[
            _value_key_for_constraint(converter, candidate.constraint_type, metadata)
        ] = formatted_value
        entry.append(metadata)
        return entry
    if normalized_type == "distance" and len(candidate.entities) >= 2:
        entry.append(
            {
                _value_key_for_constraint(
                    converter, candidate.constraint_type
                ): formatted_value
            }
        )
        return entry
    entry.append(formatted_value)
    return entry


def _build_added_geometric_entry(
    candidate: LengthEditCandidate,
) -> list[Any] | str:
    if len(candidate.entities) == 1:
        return candidate.entities[0]
    return list(candidate.entities)


def _append_new_intent_constraint(
    payload: list[dict[str, Any]],
    candidate: LengthEditCandidate,
    target_value: float | None,
    converter: JsonToFusionConverter,
) -> None:
    item = payload[candidate.op_index]
    constraints = dict(item.get("constraints") or {})
    raw_entries = constraints.get(candidate.constraint_type, [])
    if raw_entries in ({}, None):
        updated_entries = []
    else:
        # Preserve flat single-entry payloads as one logical constraint before
        # appending a new intent constraint.
        updated_entries = list(
            converter._normalize_constraint_entries(
                raw_entries,
                constraint_type=candidate.constraint_type,
            )
        )

    if candidate.intent_family == INTENT_FAMILY_ADD_GEOMETRIC:
        updated_entries.append(_build_added_geometric_entry(candidate))
    else:
        if target_value is None:
            raise ValueError(
                f"{candidate.intent_family} requires a target value: {candidate.constraint_type}"
            )
        updated_entries.append(
            _build_added_dimension_entry(candidate, target_value, converter)
        )
    constraints[candidate.constraint_type] = updated_entries
    item["constraints"] = constraints


def _filter_constraints_by_type(
    constraints: dict[str, Any],
    allowed_types: set[str] | frozenset[str],
) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for constraint_type, raw_entries in (constraints or {}).items():
        if _constraint_type_key(constraint_type) not in allowed_types:
            continue
        filtered[constraint_type] = copy.deepcopy(raw_entries)
    return filtered


def _apply_closure_only_constraints(
    payload: list[dict[str, Any]],
    *,
    target_op_index: int,
) -> None:
    item = payload[target_op_index]
    item["constraints"] = _filter_constraints_by_type(
        item.get("constraints") or {},
        CLOSURE_ONLY_CONSTRAINT_TYPES,
    )


def _build_constrained_payload(
    source_payload: list[dict[str, Any]],
    candidate: LengthEditCandidate,
    new_value: float | None,
    converter: JsonToFusionConverter,
) -> list[dict[str, Any]]:
    payload = copy.deepcopy(source_payload)
    if candidate.intent_family == INTENT_FAMILY_MODIFY_DIMENSION:
        if new_value is None:
            raise ValueError("modify_dimension requires a target value.")
        _update_dimension_constraint_value(payload, candidate, new_value, converter)
    else:
        _append_new_intent_constraint(payload, candidate, new_value, converter)
    return payload


def _build_closure_only_payload(
    source_payload: list[dict[str, Any]],
    candidate: LengthEditCandidate,
    new_value: float | None,
    converter: JsonToFusionConverter,
) -> list[dict[str, Any]]:
    payload = copy.deepcopy(source_payload)
    _apply_closure_only_constraints(payload, target_op_index=candidate.op_index)
    _append_new_intent_constraint(payload, candidate, new_value, converter)
    return payload


def _constraint_record_matches_candidate(
    record: ConstraintRecord,
    candidate: LengthEditCandidate,
) -> bool:
    if candidate.entry_index is None:
        return False
    return (
        _constraint_type_key(record.constraint_type)
        == _constraint_type_key(candidate.constraint_type)
        and int(record.entry_index) == int(candidate.entry_index)
        and tuple(record.entities) == tuple(candidate.entities)
    )


def _updated_target_record(
    source_record: ConstraintRecord,
    candidate: LengthEditCandidate,
    new_value: float | None,
) -> ConstraintRecord:
    return ConstraintRecord(
        constraint_type=source_record.constraint_type,
        entry_index=source_record.entry_index,
        entities=source_record.entities,
        value=(
            _format_constraint_value(candidate.constraint_type, new_value)
            if new_value is not None
            else None
        ),
        extra=dict(source_record.extra)
        if isinstance(source_record.extra, dict)
        else source_record.extra,
    )


def _split_target_and_preserved_records(
    source_records: list[ConstraintRecord],
    candidate: LengthEditCandidate,
    new_value: float | None,
) -> tuple[ConstraintRecord, list[ConstraintRecord]]:
    if candidate.intent_family != INTENT_FAMILY_MODIFY_DIMENSION:
        return (
            ConstraintRecord(
                constraint_type=candidate.constraint_type,
                entry_index=-1,
                entities=tuple(candidate.entities),
                value=(
                    _format_constraint_value(candidate.constraint_type, new_value)
                    if new_value is not None
                    and candidate.intent_family != INTENT_FAMILY_ADD_GEOMETRIC
                    else None
                ),
                extra=dict(candidate.extra)
                if isinstance(candidate.extra, dict)
                else candidate.extra,
            ),
            list(source_records),
        )

    target_record: ConstraintRecord | None = None
    preserved_records: list[ConstraintRecord] = []
    for record in source_records:
        if target_record is None and _constraint_record_matches_candidate(
            record, candidate
        ):
            target_record = _updated_target_record(record, candidate, new_value)
            continue
        preserved_records.append(record)
    if target_record is None:
        raise ValueError(
            "Failed to locate the edited target constraint in source sketch records: "
            f"{candidate.constraint_type}[{candidate.entry_index}] {candidate.entities}"
        )
    return target_record, preserved_records


def _sample_candidate_target_value(
    rng: random.Random,
    candidate: LengthEditCandidate,
    *,
    edit_scale_min: float,
    edit_scale_max: float,
    angle_delta_min_deg: float,
    angle_delta_max_deg: float,
) -> tuple[float | None, dict[str, float | None]]:
    if candidate.intent_family == INTENT_FAMILY_ADD_GEOMETRIC:
        return None, {"edit_scale": None, "edit_delta_deg": None}

    if candidate.original_value_mm is None:
        raise ValueError(
            f"Missing numeric source value for {candidate.intent_family}: {candidate.constraint_type}"
        )

    if candidate.value_domain == "angle":
        delta = rng.uniform(angle_delta_min_deg, angle_delta_max_deg)
        if rng.random() < 0.5:
            delta = -delta
        new_value = _normalize_angle_target_deg(candidate.original_value_mm + delta)
        if math.isclose(
            _smallest_angle_delta_deg(new_value, candidate.original_value_mm),
            0.0,
            abs_tol=1e-3,
        ):
            fallback_delta = angle_delta_max_deg if delta >= 0 else -angle_delta_max_deg
            new_value = _normalize_angle_target_deg(
                candidate.original_value_mm + fallback_delta
            )
            delta = fallback_delta
        return new_value, {"edit_scale": None, "edit_delta_deg": delta}

    scale = _sample_length_edit_scale(
        rng,
        scale_min=edit_scale_min,
        scale_max=edit_scale_max,
    )
    return candidate.original_value_mm * scale, {
        "edit_scale": scale,
        "edit_delta_deg": None,
    }


def _collect_original_refs(records: list[ConstraintRecord]) -> list[str]:
    refs: set[str] = set()
    for record in records:
        for entity_ref in record.entities:
            refs.add(entity_ref)
    return sorted(refs)


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


def _xy_points_match(
    left: tuple[float, float],
    right: tuple[float, float],
    *,
    abs_tol: float = 1e-9,
) -> bool:
    return math.isclose(left[0], right[0], abs_tol=abs_tol) and math.isclose(
        left[1],
        right[1],
        abs_tol=abs_tol,
    )


def _distance_candidate_redundant_with_length(
    entities: tuple[str, ...],
    extra: dict[str, Any] | None,
    sketch_live_refs: dict[str, dict[str, Any]],
    line_names: list[str],
) -> bool:
    direction = (
        str((extra or {}).get("direction", "MINIMUM")).strip().upper() or "MINIMUM"
    )
    if len(entities) != 2 or not all("." in ref for ref in entities):
        return False

    try:
        left_xy = _point_xy(sketch_live_refs[entities[0]])
        right_xy = _point_xy(sketch_live_refs[entities[1]])
    except Exception:
        return False

    for line_name in line_names:
        try:
            start_xy, end_xy = _line_segment(sketch_live_refs[line_name])
        except Exception:
            continue
        endpoints_match = (
            _xy_points_match(left_xy, start_xy) and _xy_points_match(right_xy, end_xy)
        ) or (
            _xy_points_match(left_xy, end_xy) and _xy_points_match(right_xy, start_xy)
        )
        if not endpoints_match:
            continue
        if direction == "MINIMUM":
            return True
        if direction == "HORIZONTAL" and math.isclose(
            start_xy[1], end_xy[1], abs_tol=1e-9
        ):
            return True
        if direction == "VERTICAL" and math.isclose(
            start_xy[0], end_xy[0], abs_tol=1e-9
        ):
            return True
    return False


def _point_to_segment_distance(
    point: tuple[float, float],
    segment: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    (x1, y1), (x2, y2) = segment
    dx = x2 - x1
    dy = y2 - y1
    if math.isclose(dx, 0.0, abs_tol=1e-12) and math.isclose(dy, 0.0, abs_tol=1e-12):
        return math.hypot(point[0] - x1, point[1] - y1)
    projection = ((point[0] - x1) * dx + (point[1] - y1) * dy) / float(
        dx * dx + dy * dy
    )
    projection = min(1.0, max(0.0, projection))
    nearest_x = x1 + projection * dx
    nearest_y = y1 + projection * dy
    return math.hypot(point[0] - nearest_x, point[1] - nearest_y)


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


def _cross_product2(left: tuple[float, float], right: tuple[float, float]) -> float:
    return left[0] * right[1] - left[1] * right[0]


def _directed_angle_deg(
    vector_a: tuple[float, float], vector_b: tuple[float, float]
) -> float:
    cross = vector_a[0] * vector_b[1] - vector_a[1] * vector_b[0]
    dot = vector_a[0] * vector_b[0] + vector_a[1] * vector_b[1]
    angle = math.degrees(math.atan2(cross, dot))
    if angle < 0.0:
        angle += 360.0
    return angle


def _normalize_vector2(
    vector: tuple[float, float], tol: float = 1e-9
) -> tuple[float, float] | None:
    length = math.hypot(vector[0], vector[1])
    if length <= tol:
        return None
    return (vector[0] / length, vector[1] / length)


def _smallest_angle_delta_deg(actual: float, expected: float) -> float:
    delta = abs(float(actual) - float(expected)) % 360.0
    return min(delta, 360.0 - delta)


def _smallest_line_angle_delta_deg(actual: float, expected: float) -> float:
    # HistCAD line-line angle values are orientation-dependent, while the
    # runtime/Fusion constraint acts on undirected lines. Values that differ by
    # 180 degrees are therefore geometrically equivalent for validation.
    delta = abs(float(actual) - float(expected)) % 180.0
    return min(delta, 180.0 - delta)


def _segment_line_intersection(
    line1: tuple[tuple[float, float], tuple[float, float]],
    line2: tuple[tuple[float, float], tuple[float, float]],
    tol: float = 1e-9,
) -> dict[str, Any] | None:
    s1, e1 = line1
    s2, e2 = line2
    r = (e1[0] - s1[0], e1[1] - s1[1])
    s = (e2[0] - s2[0], e2[1] - s2[1])
    denom = _cross_product2(r, s)
    if abs(denom) <= tol:
        return None

    len1 = math.hypot(r[0], r[1])
    len2 = math.hypot(s[0], s[1])
    if len1 <= tol or len2 <= tol:
        return None

    delta = (s2[0] - s1[0], s2[1] - s1[1])
    t1 = _cross_product2(delta, s) / denom
    t2 = _cross_product2(delta, r) / denom

    param_tol1 = tol / len1
    param_tol2 = tol / len2
    if not (
        -param_tol1 <= t1 <= 1.0 + param_tol1 and -param_tol2 <= t2 <= 1.0 + param_tol2
    ):
        return None

    t1 = min(1.0, max(0.0, t1))
    t2 = min(1.0, max(0.0, t2))
    return {
        "point": (s1[0] + r[0] * t1, s1[1] + r[1] * t1),
        "t1": t1,
        "t2": t2,
    }


def _infinite_line_intersection(
    line1: tuple[tuple[float, float], tuple[float, float]],
    line2: tuple[tuple[float, float], tuple[float, float]],
    tol: float = 1e-9,
) -> dict[str, Any] | None:
    s1, e1 = line1
    s2, e2 = line2
    r = (e1[0] - s1[0], e1[1] - s1[1])
    s = (e2[0] - s2[0], e2[1] - s2[1])
    denom = _cross_product2(r, s)
    if abs(denom) <= tol:
        return None

    len1 = math.hypot(r[0], r[1])
    len2 = math.hypot(s[0], s[1])
    if len1 <= tol or len2 <= tol:
        return None

    delta = (s2[0] - s1[0], s2[1] - s1[1])
    t1 = _cross_product2(delta, s) / denom
    t2 = _cross_product2(delta, r) / denom
    return {
        "point": (s1[0] + r[0] * t1, s1[1] + r[1] * t1),
        "t1": t1,
        "t2": t2,
    }


def _select_line_intersection_ray(
    line: tuple[tuple[float, float], tuple[float, float]],
    t: float,
    tol: float = 1e-9,
) -> tuple[float, float] | None:
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


def _intersection_corner_angle_deg(
    segment_a: tuple[tuple[float, float], tuple[float, float]],
    segment_b: tuple[tuple[float, float], tuple[float, float]],
    tol: float = 1e-9,
) -> float:
    intersection = _segment_line_intersection(segment_a, segment_b, tol=tol)
    if intersection is None:
        intersection = _infinite_line_intersection(segment_a, segment_b, tol=tol)
    if intersection is None:
        raise UnsupportedConstraintError(
            "Cannot infer an angular intersection for parallel lines."
        )

    ray_a = _select_line_intersection_ray(segment_a, intersection["t1"], tol=tol)
    ray_b = _select_line_intersection_ray(segment_b, intersection["t2"], tol=tol)
    if ray_a is None or ray_b is None:
        raise UnsupportedConstraintError(
            "Cannot infer angular rays for degenerate lines."
        )
    unit_a = _normalize_vector2(ray_a, tol=tol)
    unit_b = _normalize_vector2(ray_b, tol=tol)
    if unit_a is None or unit_b is None:
        raise UnsupportedConstraintError(
            "Cannot normalize angular rays for degenerate lines."
        )
    dot = max(-1.0, min(1.0, unit_a[0] * unit_b[0] + unit_a[1] * unit_b[1]))
    return math.degrees(math.acos(dot))


def _distance_point_point(
    point_a: tuple[float, float], point_b: tuple[float, float]
) -> float:
    return math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1])


def _distance_point_segment(
    point: tuple[float, float], segment: tuple[tuple[float, float], tuple[float, float]]
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
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


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
    if abs(o4) <= tol and _on_segment(b1, b2, a2):
        return True
    return False


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


def _curve_center_radius(
    live_payload: dict[str, Any],
) -> tuple[tuple[float, float], float]:
    return _center_xy(live_payload), _radius_value(live_payload)


def _should_use_client_constraint_check(record: ConstraintRecord) -> bool:
    normalized_type = record.constraint_type.strip().lower()
    if normalized_type in {"horizontal", "vertical"}:
        return True
    if normalized_type != "distance":
        return False
    direction = (
        str((record.extra or {}).get("direction", "MINIMUM")).strip().upper()
        or "MINIMUM"
    )
    return direction in {"HORIZONTAL", "VERTICAL"}


def _evaluate_constraint_record_with_client(
    client: Fusion360Client,
    record: ConstraintRecord,
    runtime_entity_map: dict[str, str],
    *,
    sketch_index: int,
) -> dict[str, Any] | None:
    if not _should_use_client_constraint_check(record):
        return None

    runtime_entities: list[str] = []
    for entity_ref in record.entities:
        runtime_entity = runtime_entity_map.get(entity_ref)
        if not runtime_entity:
            return None
        runtime_entities.append(str(runtime_entity))

    response = _normalize_client_response(
        client.check_constraint_satisfied(
            record.constraint_type,
            runtime_entities,
            value=record.value,
            sketch_num=sketch_index,
            extra=record.extra,
        )
    )
    if response.get("supported") is False:
        reason = str(
            response.get("reason")
            or f"Unsupported constraint type: {record.constraint_type}"
        )
        raise UnsupportedConstraintError(reason)

    satisfied = response.get("satisfied")
    if satisfied is None:
        return None

    detail_items = (
        response.get("details") if isinstance(response.get("details"), list) else []
    )
    actual = None
    expected = None
    delta = None
    if detail_items:
        detail_dicts = [item for item in detail_items if isinstance(item, dict)]
        if detail_dicts:
            actual_deltas = [
                float(item["actual_delta"])
                for item in detail_dicts
                if item.get("actual_delta") is not None
            ]
            if actual_deltas:
                actual = max(actual_deltas)
            else:
                primary = detail_dicts[0]
                if primary.get("actual") is not None:
                    actual = float(primary["actual"])
                if primary.get("target") is not None:
                    expected = float(primary["target"])
                if actual is not None and expected is not None:
                    delta = abs(actual - expected)

    return {
        "ok": bool(satisfied),
        "actual": actual,
        "expected": expected,
        "delta": delta,
    }


def _tangent_deviation(
    left_payload: dict[str, Any],
    right_payload: dict[str, Any],
) -> float:
    left_kind = str(left_payload.get("kind"))
    right_kind = str(right_payload.get("kind"))

    if left_kind == "line" and right_kind in {"circle", "arc"}:
        center, radius = _curve_center_radius(right_payload)
        return abs(
            _distance_point_segment(center, _line_segment(left_payload)) - radius
        )
    if right_kind == "line" and left_kind in {"circle", "arc"}:
        center, radius = _curve_center_radius(left_payload)
        return abs(
            _distance_point_segment(center, _line_segment(right_payload)) - radius
        )
    if left_kind in {"circle", "arc"} and right_kind in {"circle", "arc"}:
        center_left, radius_left = _curve_center_radius(left_payload)
        center_right, radius_right = _curve_center_radius(right_payload)
        center_distance = _distance_point_point(center_left, center_right)
        external = abs(center_distance - (radius_left + radius_right))
        internal = abs(center_distance - abs(radius_left - radius_right))
        return min(external, internal)
    raise UnsupportedConstraintError(
        f"Unsupported tangent pair: {left_kind}, {right_kind}"
    )


def _normal_deviation(
    left_payload: dict[str, Any],
    right_payload: dict[str, Any],
    *,
    angle_tol_deg: float,
) -> float:
    left_kind = str(left_payload.get("kind"))
    right_kind = str(right_payload.get("kind"))
    if left_kind == "line" and right_kind == "line":
        segment_a = _line_segment(left_payload)
        segment_b = _line_segment(right_payload)
        angle = _directed_angle_deg(
            _vector_from_segment(segment_a), _vector_from_segment(segment_b)
        )
        return min(abs(angle - 90.0), abs(angle - 270.0))
    if left_kind == "line" and right_kind in {"circle", "arc"}:
        center, _radius = _curve_center_radius(right_payload)
        return _distance_point_segment(center, _line_segment(left_payload))
    if right_kind == "line" and left_kind in {"circle", "arc"}:
        center, _radius = _curve_center_radius(left_payload)
        return _distance_point_segment(center, _line_segment(right_payload))
    raise UnsupportedConstraintError(
        f"Unsupported normal pair: {left_kind}, {right_kind}"
    )


def _evaluate_constraint_record(
    converter: JsonToFusionConverter,
    record: ConstraintRecord,
    live_refs: dict[str, dict[str, Any]],
    *,
    length_tol_cm: float,
    angle_tol_deg: float,
) -> dict[str, Any]:
    normalized_type = record.constraint_type.strip().lower()
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
            base = _point_xy(live_refs[refs[0]])
            actual = (
                max(abs(base[1] - _point_xy(live_refs[ref])[1]) for ref in refs[1:])
                if len(refs) > 1
                else 0.0
            )
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "vertical":
        if len(refs) == 1:
            start, end = _line_segment(live_refs[refs[0]])
            actual = abs(start[0] - end[0])
        else:
            base = _point_xy(live_refs[refs[0]])
            actual = (
                max(abs(base[0] - _point_xy(live_refs[ref])[0]) for ref in refs[1:])
                if len(refs) > 1
                else 0.0
            )
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "parallel":
        base = _line_segment(live_refs[refs[0]])
        deltas = []
        ok = True
        for ref in refs[1:]:
            angle = _directed_angle_deg(
                _vector_from_segment(base),
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

    if normalized_type == "normal":
        delta = _normal_deviation(
            live_refs[refs[0]],
            live_refs[refs[1]],
            angle_tol_deg=angle_tol_deg,
        )
        tol = (
            angle_tol_deg
            if live_refs[refs[0]].get("kind") == "line"
            and live_refs[refs[1]].get("kind") == "line"
            else length_tol_cm
        )
        return {"ok": delta <= tol, "actual": delta}

    if normalized_type == "angle":
        expected = float(record.value)
        segment_a = _line_segment(live_refs[refs[0]])
        segment_b = _line_segment(live_refs[refs[1]])
        actual = _intersection_corner_angle_deg(segment_a, segment_b)
        delta = _smallest_line_angle_delta_deg(actual, expected)
        return {
            "ok": delta <= angle_tol_deg,
            "actual": actual,
            "expected": expected,
            "delta": delta,
        }

    if normalized_type == "length":
        expected_cm = (
            _parse_dimension_value_mm(converter, record.constraint_type, record.value)
            * converter.UNITLESS_LENGTH_SCALE
        )
        actual = _segment_length(_line_segment(live_refs[refs[0]]))
        return {
            "ok": abs(actual - expected_cm) <= length_tol_cm,
            "actual": actual,
            "expected": expected_cm,
        }

    if normalized_type == "radius":
        expected_cm = (
            _parse_dimension_value_mm(converter, record.constraint_type, record.value)
            * converter.UNITLESS_LENGTH_SCALE
        )
        actual = _radius_value(live_refs[refs[0]])
        return {
            "ok": abs(actual - expected_cm) <= length_tol_cm,
            "actual": actual,
            "expected": expected_cm,
        }

    if normalized_type == "diameter":
        expected_cm = (
            _parse_dimension_value_mm(converter, record.constraint_type, record.value)
            * converter.UNITLESS_LENGTH_SCALE
        )
        actual = 2.0 * _radius_value(live_refs[refs[0]])
        return {
            "ok": abs(actual - expected_cm) <= length_tol_cm,
            "actual": actual,
            "expected": expected_cm,
        }

    if normalized_type == "equal":
        if live_refs[refs[0]]["kind"] == "line":
            base = _segment_length(_line_segment(live_refs[refs[0]]))
            actual = (
                max(
                    abs(base - _segment_length(_line_segment(live_refs[ref])))
                    for ref in refs[1:]
                )
                if len(refs) > 1
                else 0.0
            )
        else:
            base = _radius_value(live_refs[refs[0]])
            actual = (
                max(abs(base - _radius_value(live_refs[ref])) for ref in refs[1:])
                if len(refs) > 1
                else 0.0
            )
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "concentric":
        base = _center_xy(live_refs[refs[0]])
        actual = (
            max(
                _distance_point_point(base, _center_xy(live_refs[ref]))
                for ref in refs[1:]
            )
            if len(refs) > 1
            else 0.0
        )
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "midpoint":
        if len(refs) == 2:
            point = _point_xy(live_refs[refs[0]])
            start, end = _line_segment(live_refs[refs[1]])
            target = ((start[0] + end[0]) * 0.5, (start[1] + end[1]) * 0.5)
        elif len(refs) == 3:
            point = _point_xy(live_refs[refs[0]])
            point_a = _point_xy(live_refs[refs[1]])
            point_b = _point_xy(live_refs[refs[2]])
            target = ((point_a[0] + point_b[0]) * 0.5, (point_a[1] + point_b[1]) * 0.5)
        else:
            raise UnsupportedConstraintError("Unsupported midpoint arity.")
        actual = _distance_point_point(point, target)
        return {"ok": actual <= length_tol_cm, "actual": actual}

    if normalized_type == "distance":
        expected_cm = (
            _parse_dimension_value_mm(converter, record.constraint_type, record.value)
            * converter.UNITLESS_LENGTH_SCALE
        )
        actual = _actual_distance_for_record(record, live_refs)
        return {
            "ok": abs(actual - expected_cm) <= length_tol_cm,
            "actual": actual,
            "expected": expected_cm,
        }

    raise UnsupportedConstraintError(
        f"Unsupported constraint type: {record.constraint_type}"
    )


def _evaluate_constraint_records(
    converter: JsonToFusionConverter,
    records: list[ConstraintRecord],
    live_refs: dict[str, dict[str, Any]],
    *,
    length_tol_cm: float,
    angle_tol_deg: float,
    client: Fusion360Client | None = None,
    runtime_entity_map: dict[str, str] | None = None,
    sketch_index: int | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    supported_total = 0
    satisfied_total = 0
    unsupported_total = 0

    for record in records:
        if (
            client is not None
            and runtime_entity_map is not None
            and sketch_index is not None
        ):
            try:
                client_evaluated = _evaluate_constraint_record_with_client(
                    client,
                    record,
                    runtime_entity_map,
                    sketch_index=sketch_index,
                )
            except Exception:
                client_evaluated = None
            if client_evaluated is not None:
                supported_total += 1
                if bool(client_evaluated.get("ok")):
                    satisfied_total += 1
                results.append(
                    {
                        "constraint_type": record.constraint_type,
                        "entities": list(record.entities),
                        "ok": bool(client_evaluated.get("ok")),
                        "actual": client_evaluated.get("actual"),
                        "expected": client_evaluated.get("expected"),
                        "delta": client_evaluated.get("delta"),
                    }
                )
                continue

        missing_record_refs = [ref for ref in record.entities if ref not in live_refs]
        if missing_record_refs:
            unsupported_total += 1
            results.append(
                {
                    "constraint_type": record.constraint_type,
                    "entities": list(record.entities),
                    "ok": None,
                    "error": f"Missing live refs: {missing_record_refs}",
                }
            )
            continue
        try:
            evaluated = _evaluate_constraint_record(
                converter,
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
                    "entities": list(record.entities),
                    "ok": None,
                    "error": str(exc),
                }
            )

    satisfaction_rate = (
        float(satisfied_total) / float(supported_total) if supported_total > 0 else None
    )
    total_rate = float(satisfied_total) / float(len(records)) if records else None
    return {
        "records": results,
        "supported_constraints": supported_total,
        "unsupported_constraints": unsupported_total,
        "satisfied_constraints": satisfied_total,
        "total_constraints": len(records),
        "satisfaction_rate": satisfaction_rate,
        "satisfaction_rate_total": total_rate,
        "all_satisfied": unsupported_total == 0 and satisfied_total == supported_total,
    }


def _constraint_record_to_dict(record: ConstraintRecord) -> dict[str, Any]:
    return {
        "constraint_type": record.constraint_type,
        "entry_index": record.entry_index,
        "entities": list(record.entities),
        "value": record.value,
        "extra": dict(record.extra) if isinstance(record.extra, dict) else record.extra,
    }


def _normalize_client_response(response: Any) -> Any:
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


def _evaluate_sketch_constraints(
    converter: JsonToFusionConverter,
    target_record: ConstraintRecord,
    preserved_records: list[ConstraintRecord],
    live_refs: dict[str, dict[str, Any]],
    *,
    length_tol_cm: float,
    angle_tol_deg: float,
    client: Fusion360Client | None = None,
    runtime_entity_map: dict[str, str] | None = None,
    sketch_index: int | None = None,
) -> dict[str, Any]:
    target_evaluation = _evaluate_constraint_records(
        converter,
        [target_record],
        live_refs,
        length_tol_cm=length_tol_cm,
        angle_tol_deg=angle_tol_deg,
        client=client,
        runtime_entity_map=runtime_entity_map,
        sketch_index=sketch_index,
    )
    preserved_evaluation = _evaluate_constraint_records(
        converter,
        preserved_records,
        live_refs,
        length_tol_cm=length_tol_cm,
        angle_tol_deg=angle_tol_deg,
        client=client,
        runtime_entity_map=runtime_entity_map,
        sketch_index=sketch_index,
    )
    target_result = (
        target_evaluation["records"][0] if target_evaluation["records"] else None
    )
    return {
        "target_record": _constraint_record_to_dict(target_record),
        "target_hit": target_result.get("ok") if target_result else None,
        "target_actual": target_result.get("actual") if target_result else None,
        "target_expected": target_result.get("expected") if target_result else None,
        "target_delta": target_result.get("delta") if target_result else None,
        "target_error": target_result.get("error") if target_result else None,
        "target_evaluation": target_result,
        "preserved_records": preserved_evaluation["records"],
        "preserved_supported_constraints": preserved_evaluation[
            "supported_constraints"
        ],
        "preserved_unsupported_constraints": preserved_evaluation[
            "unsupported_constraints"
        ],
        "preserved_satisfied_constraints": preserved_evaluation[
            "satisfied_constraints"
        ],
        "preserved_total_constraints": preserved_evaluation["total_constraints"],
        "preserved_constraint_satisfaction_rate": preserved_evaluation[
            "satisfaction_rate"
        ],
        "preserved_constraints_all_satisfied": preserved_evaluation["all_satisfied"],
    }


def _inspect_live_refs(
    client: Fusion360Client,
    entity_map: dict[str, str],
    original_refs: list[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    missing_refs = [ref for ref in original_refs if ref not in entity_map]
    runtime_ids = [entity_map[ref] for ref in original_refs if ref in entity_map]
    if not runtime_ids:
        return {}, missing_refs

    inspection = _normalize_client_response(client.inspect_entities(runtime_ids))
    entities_by_runtime_id = {
        entity["entity_id"]: entity for entity in inspection.get("entities", [])
    }
    live_refs: dict[str, dict[str, Any]] = {}
    for original_ref in original_refs:
        runtime_id = entity_map.get(original_ref)
        if runtime_id is None:
            continue
        inspected = entities_by_runtime_id.get(runtime_id)
        if inspected is None:
            missing_refs.append(original_ref)
            continue
        live_refs[original_ref] = inspected
    return live_refs, sorted(set(missing_refs))


def _execute_case(
    *,
    converter: JsonToFusionConverter,
    client: Fusion360Client,
    payload: list[dict[str, Any]],
    candidate: LengthEditCandidate,
    target_record: ConstraintRecord,
    preserved_records: list[ConstraintRecord],
    case_name: str,
    length_tol_cm: float,
    angle_tol_deg: float,
) -> dict[str, Any]:
    commands = converter.build_commands(
        payload, REPO_ROOT / "tmp" / f"{case_name}.step"
    )
    if commands and commands[-1].method == "export_step":
        commands = commands[:-1]

    execution = converter.execute_commands(commands, context=case_name)
    validation = _normalize_client_response(
        client.validate_sketch_constraints(sketch_num=candidate.sketch_index)
    )
    original_refs = _collect_original_refs([target_record, *preserved_records])
    # Entity names like "line_1" are reused across sketches. Replaying only through the
    # target op keeps the edited sketch as the latest binding in the runtime entity map.
    inspection_payload = payload[: candidate.op_index + 1]
    inspection_commands = converter.build_commands(
        inspection_payload,
        REPO_ROOT / "tmp" / f"{case_name}_inspect.step",
    )
    if inspection_commands and inspection_commands[-1].method == "export_step":
        inspection_commands = inspection_commands[:-1]
    inspection_execution = converter.execute_commands(
        inspection_commands,
        context=f"{case_name}_inspect",
    )
    runtime_entity_map = {
        str(original_ref): str(runtime_id)
        for original_ref, runtime_id in (
            inspection_execution.get("entities", {}) or {}
        ).items()
        if runtime_id is not None
    }
    live_refs, missing_refs = _inspect_live_refs(
        client,
        runtime_entity_map,
        original_refs,
    )
    evaluation = _evaluate_sketch_constraints(
        converter,
        target_record,
        preserved_records,
        live_refs,
        length_tol_cm=length_tol_cm,
        angle_tol_deg=angle_tol_deg,
        client=client,
        runtime_entity_map=runtime_entity_map,
        sketch_index=candidate.sketch_index,
    )

    return {
        "execution": execution,
        "inspection_execution": inspection_execution,
        "validation": validation,
        "missing_refs": missing_refs,
        "evaluation": evaluation,
    }


def _validation_is_healthy(validation: dict[str, Any]) -> bool:
    if not bool(validation.get("ok")):
        return False
    if bool(validation.get("over_constrained", False)):
        return False

    health_state = validation.get("health_state")
    if isinstance(health_state, (int, float)) and int(health_state) != 0:
        return False

    health_state_name = str(validation.get("health_state_name") or "").strip()
    if health_state_name and health_state_name != "HealthyFeatureHealthState":
        return False

    warning_message = str(validation.get("error_or_warning_message") or "").strip()
    if warning_message and health_state_name:
        return False
    return True


def _case_rebuild_success(case_result: dict[str, Any]) -> bool:
    execution = case_result.get("execution") or {}
    return len(execution.get("skipped_constraints", [])) == 0


def _case_overall_editable_success(case_result: dict[str, Any]) -> bool:
    evaluation = case_result.get("evaluation") or {}
    return (
        _case_rebuild_success(case_result)
        and _validation_is_healthy(case_result.get("validation") or {})
        and evaluation.get("target_hit") is True
        and evaluation.get("preserved_constraints_all_satisfied") is True
        and not case_result.get("missing_refs")
    )


def _case_reference_task_success(case_result: dict[str, Any]) -> bool:
    evaluation = case_result.get("evaluation") or {}
    return (
        _validation_is_healthy(case_result.get("validation") or {})
        and evaluation.get("target_hit") is True
        and evaluation.get("preserved_constraints_all_satisfied") is True
    )


def _candidate_precheck_target_value(candidate: LengthEditCandidate) -> float | None:
    if candidate.intent_family == INTENT_FAMILY_ADD_DIMENSION:
        return candidate.original_value_mm
    if candidate.intent_family == INTENT_FAMILY_ADD_GEOMETRIC:
        return None
    return None


def _candidate_is_constrained_feasible(
    *,
    converter: JsonToFusionConverter,
    client: Fusion360Client | None,
    source_payload: list[dict[str, Any]],
    candidate: LengthEditCandidate,
    length_tol_cm: float,
    angle_tol_deg: float,
    case_name: str,
) -> bool:
    if client is None or candidate.intent_family == INTENT_FAMILY_MODIFY_DIMENSION:
        return True

    precheck_value = _candidate_precheck_target_value(candidate)
    if (
        candidate.intent_family == INTENT_FAMILY_ADD_DIMENSION
        and precheck_value is None
    ):
        return False

    source_records = _supported_source_records_for_candidate(
        source_payload,
        candidate,
        converter,
    )
    try:
        target_record, preserved_records = _split_target_and_preserved_records(
            source_records,
            candidate,
            precheck_value,
        )
        constrained_payload = _build_constrained_payload(
            source_payload,
            candidate,
            precheck_value,
            converter,
        )
        case_result = _execute_case(
            converter=converter,
            client=client,
            payload=constrained_payload,
            candidate=candidate,
            target_record=target_record,
            preserved_records=preserved_records,
            case_name=case_name,
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
        )
    except Exception:
        return False
    return _case_reference_task_success(case_result)


def _prepare_candidate_execution_bundle(
    *,
    sample_index: int,
    candidate: LengthEditCandidate,
    source_payload: list[dict[str, Any]],
    source_records: list[ConstraintRecord],
    converter: JsonToFusionConverter,
    client: Fusion360Client,
    rng: random.Random,
    edit_scale_min: float,
    edit_scale_max: float,
    angle_delta_min_deg: float,
    angle_delta_max_deg: float,
    target_value_retry_attempts: int,
    length_tol_cm: float,
    angle_tol_deg: float,
) -> dict[str, Any]:
    attempts = 1
    precomputed_case_results: dict[str, dict[str, Any]] = {}
    if candidate.intent_family in {
        INTENT_FAMILY_MODIFY_DIMENSION,
        INTENT_FAMILY_ADD_DIMENSION,
    }:
        attempts = max(1, int(target_value_retry_attempts))

    last_bundle: dict[str, Any] | None = None
    for attempt_index in range(attempts):
        new_value_numeric, edit_parameters = _sample_candidate_target_value(
            rng,
            candidate,
            edit_scale_min=edit_scale_min,
            edit_scale_max=edit_scale_max,
            angle_delta_min_deg=angle_delta_min_deg,
            angle_delta_max_deg=angle_delta_max_deg,
        )
        target_record, preserved_records = _split_target_and_preserved_records(
            source_records,
            candidate,
            new_value_numeric,
        )

        constrained_payload = _build_constrained_payload(
            source_payload,
            candidate,
            new_value_numeric,
            converter,
        )
        closure_only_payload = _build_closure_only_payload(
            source_payload,
            candidate,
            new_value_numeric,
            converter,
        )

        bundle = {
            "new_value_numeric": new_value_numeric,
            "edit_parameters": edit_parameters,
            "target_record": target_record,
            "preserved_records": preserved_records,
            "constrained_payload": constrained_payload,
            "closure_only_payload": closure_only_payload,
            "precomputed_case_results": {},
        }
        last_bundle = bundle

        if candidate.intent_family not in {
            INTENT_FAMILY_MODIFY_DIMENSION,
            INTENT_FAMILY_ADD_DIMENSION,
        }:
            return bundle

        constrained_case_id = (
            f"{sample_index:04d}_{BRANCH_CONSTRAINED}_attempt_{attempt_index + 1}"
        )
        constrained_case_result = _execute_case(
            converter=converter,
            client=client,
            payload=constrained_payload,
            candidate=candidate,
            target_record=target_record,
            preserved_records=preserved_records,
            case_name=constrained_case_id,
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
        )
        precomputed_case_results = {BRANCH_CONSTRAINED: constrained_case_result}
        bundle["precomputed_case_results"] = precomputed_case_results
        if _case_reference_task_success(constrained_case_result):
            return bundle

    if last_bundle is None:
        raise RuntimeError(
            "Failed to prepare any execution bundle for the selected candidate."
        )
    if candidate.intent_family in {
        INTENT_FAMILY_MODIFY_DIMENSION,
        INTENT_FAMILY_ADD_DIMENSION,
    }:
        raise IntentInfeasibleError(
            "Failed to find a reference-feasible target value after "
            f"{attempts} attempt(s) for {candidate.intent_family} {candidate.constraint_type} "
            f"on {candidate.entities}."
        )
    last_bundle["precomputed_case_results"] = precomputed_case_results
    return last_bundle


def _rate_of_true(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if row.get(key) is True) / float(len(rows))


def _classify_failure_reason(row: dict[str, Any]) -> str:
    if row.get("exception"):
        return "execution_exception"
    if row.get("overall_editable_success") is True:
        return ""
    if not row.get("rebuild_success", False):
        return "downstream_rebuild_failed"
    if row.get("target_hit") is False:
        return "target_miss"
    if row.get("target_hit") is None:
        return "target_evaluation_incomplete"
    if row.get("preserved_constraints_all_satisfied") is False:
        return "preserved_constraints_broken"
    if row.get("preserved_constraints_all_satisfied") is None:
        return "preserved_evaluation_incomplete"
    if row.get("validation_ok") is False and row.get("validation_over_constrained"):
        return "sketch_over_constrained"
    if row.get("validation_ok") is False:
        return "sketch_validation_failed"
    if row.get("missing_ref_count", 0) > 0:
        return "missing_live_refs"
    return ""


def _grouped_row_summaries(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = sorted(
        {str(row.get(key) or "") for row in rows if str(row.get(key) or "").strip()}
    )
    return {
        value: _summarize_result_rows(
            [row for row in rows if str(row.get(key) or "") == value]
        )
        for value in values
    }


def _summarize_preserved_constraint_type_breakdown(
    detailed_cases: list[dict[str, Any]],
    branch: str,
) -> dict[str, Any]:
    by_type: dict[str, dict[str, int]] = {}
    for detail in detailed_cases:
        if str(detail.get("branch")) != branch:
            continue
        evaluation = detail.get("evaluation") or {}
        preserved_records = evaluation.get("preserved_records") or []
        case_types: set[str] = set()
        for record in preserved_records:
            constraint_type = str(record.get("constraint_type") or "").strip()
            if not constraint_type:
                continue
            stats = by_type.setdefault(
                constraint_type,
                {
                    "case_count": 0,
                    "overall_editable_success_count": 0,
                    "supported_constraints": 0,
                    "unsupported_constraints": 0,
                    "satisfied_constraints": 0,
                    "total_constraints": 0,
                },
            )
            stats["total_constraints"] += 1
            ok_value = record.get("ok")
            if ok_value is None:
                stats["unsupported_constraints"] += 1
            else:
                stats["supported_constraints"] += 1
                if ok_value is True:
                    stats["satisfied_constraints"] += 1
            case_types.add(constraint_type)
        for constraint_type in case_types:
            stats = by_type[constraint_type]
            stats["case_count"] += 1
            if detail.get("overall_editable_success") is True:
                stats["overall_editable_success_count"] += 1

    summary: dict[str, Any] = {}
    for constraint_type in sorted(by_type):
        stats = by_type[constraint_type]
        supported = stats["supported_constraints"]
        total = stats["total_constraints"]
        summary[constraint_type] = {
            **stats,
            "preserved_constraint_satisfaction_rate": (
                float(stats["satisfied_constraints"]) / float(supported)
                if supported > 0
                else None
            ),
            "preserved_constraint_satisfaction_rate_all_records": (
                float(stats["satisfied_constraints"]) / float(total)
                if total > 0
                else None
            ),
            "overall_editable_success_rate": (
                float(stats["overall_editable_success_count"])
                / float(stats["case_count"])
                if stats["case_count"] > 0
                else None
            ),
        }
    return summary


def _summarize_result_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    preserved_rates = [
        row["preserved_constraint_satisfaction_rate"]
        for row in rows
        if row["preserved_constraint_satisfaction_rate"] is not None
    ]
    supported_total = sum(
        int(row["preserved_supported_constraints"])
        for row in rows
        if row.get("preserved_supported_constraints") is not None
    )
    satisfied_total = sum(
        int(row["preserved_satisfied_constraints"])
        for row in rows
        if row.get("preserved_satisfied_constraints") is not None
    )
    unsupported_total = sum(
        int(row["preserved_unsupported_constraints"])
        for row in rows
        if row.get("preserved_unsupported_constraints") is not None
    )
    preserved_total = sum(
        int(row["preserved_total_constraints"])
        for row in rows
        if row.get("preserved_total_constraints") is not None
    )
    failure_counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("failure_reason") or "")
        if not reason:
            continue
        failure_counts[reason] = failure_counts.get(reason, 0) + 1
    return {
        "count": len(rows),
        "target_hit_rate": _rate_of_true(rows, "target_hit"),
        "mean_preserved_constraint_satisfaction_rate": statistics.fmean(preserved_rates)
        if preserved_rates
        else None,
        "macro_preserved_constraint_satisfaction_rate": statistics.fmean(
            preserved_rates
        )
        if preserved_rates
        else None,
        "micro_preserved_constraint_satisfaction_rate": (
            float(satisfied_total) / float(supported_total)
            if supported_total > 0
            else None
        ),
        "preserved_supported_constraints_total": supported_total,
        "preserved_satisfied_constraints_total": satisfied_total,
        "preserved_unsupported_constraints_total": unsupported_total,
        "preserved_total_constraints_total": preserved_total,
        "preserved_constraints_all_satisfied_rate": _rate_of_true(
            rows, "preserved_constraints_all_satisfied"
        ),
        "rebuild_success_rate": _rate_of_true(rows, "rebuild_success"),
        "overall_editable_success_rate": _rate_of_true(
            rows, "overall_editable_success"
        ),
        "validation_ok_rate": _rate_of_true(rows, "validation_ok"),
        "failure_reasons": failure_counts,
    }


def _summarize_branch_results(
    rows: list[dict[str, Any]],
    branch: str,
    *,
    detailed_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    branch_rows = [row for row in rows if row["branch"] == branch]
    summary = _summarize_result_rows(branch_rows)
    summary["by_intent_family"] = _grouped_row_summaries(branch_rows, "intent_family")
    summary["by_constraint_type"] = _grouped_row_summaries(
        branch_rows, "target_constraint_type"
    )
    summary["by_source_subset"] = _grouped_row_summaries(branch_rows, "source_subset")
    summary["by_source_domain"] = _grouped_row_summaries(branch_rows, "source_domain")
    if detailed_cases is not None:
        summary["by_preserved_constraint_type"] = (
            _summarize_preserved_constraint_type_breakdown(
                detailed_cases,
                branch,
            )
        )
    return summary


def _parse_visualization_sample_ids(raw_value: str | None) -> list[int] | None:
    if raw_value is None or not str(raw_value).strip():
        return None
    values: list[int] = []
    for part in str(raw_value).split(","):
        stripped = part.strip()
        if not stripped:
            continue
        values.append(int(stripped))
    return values or None


def _build_experiment_visualizations(
    *,
    result_dir: Path,
    output_dir: Path | None,
    selection_mode: str,
    max_samples: int,
    sample_ids: list[int] | None,
    host: str,
    port: int,
    image_size: int,
    view_orientation: str,
    gallery_columns: int,
) -> dict[str, Any]:
    from editability.visualization import build_visualizations

    visualization_output_dir = output_dir or (result_dir / "visualization")
    return build_visualizations(
        result_dir=result_dir,
        output_dir=visualization_output_dir,
        selection_mode=selection_mode,
        max_samples=max_samples,
        sample_ids=sample_ids,
        host=host,
        port=port,
        image_size=image_size,
        view_orientation=view_orientation,
        gallery_columns=gallery_columns,
        reuse_existing_renders=True,
    )


def _render_sample_visualization_assets(
    *,
    sample_id: int,
    sample_uid: str,
    sample_name: str,
    output_dir: Path,
    manifest_entry: dict[str, Any],
    source_payload: list[dict[str, Any]],
    converter: JsonToFusionConverter,
    client: Fusion360Client,
    image_size: int,
    view_orientation: str,
) -> Path:
    from editability.visualization import (
        render_intent_card,
        render_payload_or_placeholder,
    )

    sample_dir = output_dir / _visualization_sample_dir_name(sample_id, sample_name)
    sample_dir.mkdir(parents=True, exist_ok=True)
    render_intent_card(
        manifest_entry,
        output_path=sample_dir / "edit_intent.png",
        image_size=image_size,
    )
    render_payload_or_placeholder(
        converter,
        client,
        source_payload,
        output_path=sample_dir / "source_model.png",
        image_size=image_size,
        view_orientation=view_orientation,
        context=f"exp_{sample_uid}_source",
        title="Original render failed",
    )
    return sample_dir


def _prepare_sample_visualization_dir(
    *,
    sample_id: int,
    sample_name: str,
    output_dir: Path,
    manifest_entry: dict[str, Any],
    image_size: int,
) -> Path:
    from editability.visualization import render_intent_card

    sample_dir = output_dir / _visualization_sample_dir_name(sample_id, sample_name)
    sample_dir.mkdir(parents=True, exist_ok=True)
    render_intent_card(
        manifest_entry,
        output_path=sample_dir / "edit_intent.png",
        image_size=image_size,
    )
    return sample_dir


def _export_payload_step(
    *,
    converter: JsonToFusionConverter,
    client: Fusion360Client,
    payload: list[dict[str, Any]],
    output_path: Path,
    context: str,
) -> str | None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    commands = converter.build_commands(payload, output_path)
    if commands and commands[-1].method == "export_step":
        commands = commands[:-1]
    converter.execute_commands(commands, context=context)
    try:
        client.export_step(filepath=str(output_path))
    except Exception as exc:
        return str(exc)
    return None


def _export_current_step(
    *,
    client: Fusion360Client,
    output_path: Path,
) -> str | None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.export_step(filepath=str(output_path))
    except Exception as exc:
        return str(exc)
    return None


def _export_branch_visualization_image(
    *,
    converter: JsonToFusionConverter,
    client: Fusion360Client,
    payload: list[dict[str, Any]],
    sample_dir: Path,
    branch_name: str,
    image_size: int,
    view_orientation: str,
    title: str,
) -> None:
    from editability.visualization import render_payload_or_placeholder

    render_payload_or_placeholder(
        converter,
        client,
        payload,
        output_path=sample_dir / f"{branch_name}_model.png",
        image_size=image_size,
        view_orientation=view_orientation,
        context=f"{sample_dir.name}_{branch_name}",
        title=title,
    )


def _write_branch_visualization_placeholder(
    *,
    sample_dir: Path,
    branch_name: str,
    image_size: int,
    title: str,
    body: str,
) -> None:
    from editability.visualization import write_placeholder_image

    write_placeholder_image(
        sample_dir / f"{branch_name}_model.png",
        title=title,
        body=body,
        size=image_size,
    )


def _format_timeout_seconds(seconds: float) -> str:
    return str(int(seconds)) if float(seconds).is_integer() else str(seconds)


def _configure_fusion_launch_env(port: int) -> None:
    os.environ.setdefault("FUSION360_SERVER_UI_LOG", "0")
    os.environ.setdefault("FUSION360_SERVER_STDOUT_LOG", "0")
    os.environ.setdefault("FUSION360_SERVER_VERBOSE_REQUEST_LOG", "0")
    os.environ.setdefault("FUSION360_SERVER_DO_EVENTS_INTERVAL_SECONDS", "0.5")
    os.environ.setdefault("FUSION_PRIVATE_DISPLAY_MODE", "1")
    os.environ.setdefault("FUSION_PRIVATE_DISPLAY", f":{10 + max(0, int(port) - 8080)}")
    os.environ.setdefault("FUSION_VNC_GEOMETRY", "1280x800")
    os.environ.setdefault("FUSION_VNC_DEPTH", "16")
    os.environ.setdefault("FUSION_WINE_VIRTUAL_DESKTOP", "1280x800")


def _build_fusion_handles(
    host: str, port: int
) -> tuple[JsonToFusionConverter, Fusion360Client]:
    return JsonToFusionConverter(host=host, port=port), Fusion360Client(
        f"http://{host}:{port}"
    )


def _iter_exception_chain(ex: BaseException):
    seen: set[int] = set()
    current: BaseException | None = ex
    while current is not None and id(current) not in seen:
        yield current
        seen.add(id(current))
        current = current.__cause__ or current.__context__


def _is_fusion_transport_error(ex: BaseException) -> bool:
    transport_types = (
        requests.exceptions.RequestException,
        socket.timeout,
        TimeoutError,
        ConnectionError,
    )
    transport_markers = (
        "connection aborted",
        "connection refused",
        "connection reset",
        "connect timeout",
        "failed to establish a new connection",
        "max retries exceeded",
        "read timed out",
        "remote end closed connection",
    )
    for current in _iter_exception_chain(ex):
        if isinstance(current, transport_types):
            return True
        message = str(current).strip().lower()
        if any(marker in message for marker in transport_markers):
            return True
    return False


def _is_fusion_timeout_skip_error(ex: BaseException) -> bool:
    if _is_fusion_transport_error(ex):
        return True

    timeout_markers = (
        "fusion recovery remained unavailable",
        "timed out waiting for an empty fusion design",
        "timed out waiting for fusion 360 server",
    )
    for current in _iter_exception_chain(ex):
        message = str(current).strip().lower()
        if any(marker in message for marker in timeout_markers):
            return True
    return False


class FusionRestartController:
    """Launch and periodically restart the Fusion bridge when needed."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        restart_every: int | None = None,
        restart_timeout_seconds: float = DEFAULT_CLI_RESTART_TIMEOUT_SECONDS,
        restart_poll_seconds: float = DEFAULT_CLI_RESTART_POLL_SECONDS,
        health_timeout_seconds: float = DEFAULT_CLI_HEALTH_TIMEOUT_SECONDS,
        restart_kill_existing: bool = False,
        restart_retry_attempts: int = DEFAULT_CLI_RESTART_RETRY_ATTEMPTS,
        restart_retry_backoff_seconds: float = DEFAULT_CLI_RESTART_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self.host = host
        self.port = port
        self.restart_every = restart_every
        self.restart_timeout_seconds = max(1.0, float(restart_timeout_seconds))
        self.restart_poll_seconds = max(0.2, float(restart_poll_seconds))
        self.health_timeout_seconds = max(0.5, float(health_timeout_seconds))
        self.restart_kill_existing = restart_kill_existing
        self.restart_retry_attempts = max(1, int(restart_retry_attempts))
        self.restart_retry_backoff_seconds = max(
            0.0, float(restart_retry_backoff_seconds)
        )
        self._launcher: Launcher | None = None
        self._managed_process: Any | None = None
        self._needs_recovery = False

    @property
    def enabled(self) -> bool:
        return self.restart_every is not None and int(self.restart_every) > 0

    def ensure_server(self) -> None:
        if self._ping():
            try:
                self._ensure_clean_design()
                self._needs_recovery = False
                return
            except Exception as exc:
                print(
                    f"[fusion] existing server on {self.host}:{self.port} failed health check: {exc}"
                )

        if self.restart_kill_existing:
            self._stop_managed_process()
            self._kill_existing_processes()

        port_conflict = self._describe_port_conflict()
        if port_conflict:
            raise RuntimeError(port_conflict)

        print(
            f"[fusion] server {self.host}:{self.port} is offline, launching Fusion 360..."
        )
        if not self._restart_with_retries(f"startup on {self.host}:{self.port}"):
            raise RuntimeError(
                f"Failed to start a healthy Fusion 360 server on {self.host}:{self.port} "
                f"after {self.restart_retry_attempts} attempt(s)."
            )

    def prepare_for_processing(self, work_name: str) -> bool:
        if self._needs_recovery:
            print(f"[fusion] retrying recovery before {work_name}.")
            return self.recover_or_restart(
                f"pending Fusion recovery before {work_name}"
            )
        try:
            self._ensure_clean_design(log_success=False)
            self._needs_recovery = False
            return True
        except Exception as exc:
            print(f"[fusion] clean-design reset failed before {work_name}: {exc}")
            return self.recover_or_restart(
                f"clean-design reset failed before {work_name}"
            )

    def maybe_restart(self, *, processed_units: int, has_pending_work: bool) -> bool:
        if (
            not self.enabled
            or not has_pending_work
            or processed_units <= 0
            or processed_units % int(self.restart_every) != 0
        ):
            return True
        print(
            f"[fusion] restarting Fusion 360 after {processed_units} processed sample(s)."
        )
        return self.restart(
            reason=f"scheduled restart after {processed_units} processed sample(s)"
        )

    def restart(self, *, reason: str) -> bool:
        return self._restart_with_retries(reason)

    def recover_or_restart(self, reason: str) -> bool:
        if self._try_soft_recover(reason):
            self._needs_recovery = False
            return True
        print(f"[fusion] falling back to full Fusion restart after {reason}.")
        return self.restart(reason=f"full restart after {reason}")

    def _restart_with_retries(self, reason: str) -> bool:
        last_error: str | None = None
        total_attempts = max(1, int(self.restart_retry_attempts))
        for attempt in range(1, total_attempts + 1):
            try:
                if total_attempts > 1:
                    print(
                        f"[fusion] restart attempt {attempt}/{total_attempts} after {reason}."
                    )
                self._restart_once(force_kill_existing=(attempt > 1))
                if attempt > 1:
                    print(
                        f"[fusion] restart recovered on attempt {attempt} after {reason}."
                    )
                self._needs_recovery = False
                return True
            except Exception as exc:
                last_error = str(exc)
                print(
                    f"[fusion] restart attempt {attempt}/{total_attempts} failed after {reason}: {exc}"
                )
                if attempt >= total_attempts:
                    break
                delay = self.restart_retry_backoff_seconds * float(attempt)
                if delay > 0:
                    print(
                        f"[fusion] waiting {delay:.1f}s before retrying Fusion restart."
                    )
                    time.sleep(delay)

        suffix = f" Last error: {last_error}" if last_error else ""
        print(f"[fusion] giving up restart after {reason}.{suffix}")
        self._needs_recovery = True
        return False

    def _restart_once(self, *, force_kill_existing: bool = False) -> None:
        self._stop_managed_process()
        if self.restart_kill_existing or force_kill_existing:
            self._kill_existing_processes()
        self._launch_and_wait()
        self._ensure_clean_design()

    def _try_soft_recover(self, reason: str) -> bool:
        deadline = time.monotonic() + min(self.restart_timeout_seconds, 30.0)
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                if self._ping():
                    self._ensure_clean_design(log_success=False)
                    print(
                        f"[fusion] recovered server on {self.host}:{self.port} without relaunch after {reason}."
                    )
                    return True
                last_error = f"server {self.host}:{self.port} not responding yet"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(self.restart_poll_seconds)

        suffix = f" Last error: {last_error}" if last_error else ""
        print(f"[fusion] soft recovery failed after {reason}.{suffix}")
        return False

    def _launch_and_wait(self) -> None:
        launch_json_path = (
            Path(tempfile.gettempdir())
            / f"fusion360_launch_{self.host.replace(':', '_')}_{self.port}.json"
        )
        os.environ["FUSION360_LAUNCH_JSON"] = str(launch_json_path)
        create_launch_json(self.host, self.port, 1)
        launcher = self._get_launcher()
        self._managed_process = launcher.launch()
        if self._managed_process is None:
            raise RuntimeError("Failed to launch Fusion 360.")
        self._wait_for_server()

    def _wait_for_server(self) -> None:
        deadline = time.monotonic() + self.restart_timeout_seconds
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                if self._ping():
                    print(f"[fusion] server ready on {self.host}:{self.port}")
                    return
            except Exception as exc:
                last_error = str(exc)
            time.sleep(self.restart_poll_seconds)

        suffix = f" Last error: {last_error}" if last_error else ""
        raise RuntimeError(
            f"Timed out waiting for Fusion 360 server on {self.host}:{self.port}.{suffix}"
        )

    def _make_health_client(self) -> Fusion360Client:
        return Fusion360Client(
            f"http://{self.host}:{self.port}",
            timeout_seconds=self.health_timeout_seconds,
        )

    def _ensure_clean_design(self, *, log_success: bool = True) -> None:
        client = self._make_health_client()
        deadline = time.monotonic() + self.restart_timeout_seconds
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                response = _normalize_client_response(client.clear())
                status = (
                    response.get("status", 200) if isinstance(response, dict) else 200
                )
                if int(status) == 200:
                    if log_success:
                        print(f"[fusion] empty design ready on {self.host}:{self.port}")
                    return
                last_error = str(response)
            except Exception as exc:
                last_error = str(exc)
            time.sleep(self.restart_poll_seconds)

        suffix = f" Last error: {last_error}" if last_error else ""
        raise RuntimeError(
            f"Timed out waiting for an empty Fusion design on {self.host}:{self.port}.{suffix}"
        )

    def _ping(self) -> bool:
        try:
            response = _normalize_client_response(self._make_health_client().ping())
        except Exception:
            return False
        if isinstance(response, dict) and "status" in response:
            return int(response["status"]) == 200
        return True

    def _describe_port_conflict(self) -> str | None:
        if not self._has_listener():
            return None
        owner = self._lookup_listener_owner()
        owner_text = ""
        if owner is not None:
            owner_text = f" by process {owner[1]} (pid {owner[0]})"
        return (
            f"Port {self.host}:{self.port} is already in use{owner_text}. "
            f"Fusion 360 cannot bind this port. Free the port or rerun with a different "
            f"`--port` value such as 18080."
        )

    def _has_listener(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=0.5):
                return True
        except OSError:
            return False

    def _lookup_listener_owner(self) -> tuple[int, str] | None:
        if sys.platform != "win32":
            return None
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None

        pid: int | None = None
        suffix = f":{self.port}"
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line.startswith("TCP"):
                continue
            columns = line.split()
            if len(columns) < 5:
                continue
            local_addr, state, pid_text = columns[1], columns[3], columns[4]
            if not local_addr.endswith(suffix) or state.upper() != "LISTENING":
                continue
            try:
                pid = int(pid_text)
            except ValueError:
                return None
            break

        if pid is None:
            return None

        try:
            task = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return (pid, "unknown")
        if task.returncode != 0:
            return (pid, "unknown")

        rows = [row for row in csv.reader(task.stdout.splitlines()) if row]
        if not rows:
            return (pid, "unknown")
        first = rows[0]
        if first and first[0].startswith("INFO:"):
            return (pid, "unknown")
        process_name = first[0] if first else "unknown"
        return (pid, process_name or "unknown")

    def _stop_managed_process(self) -> None:
        if self._managed_process is None:
            return
        try:
            self._managed_process.kill()
            self._managed_process.wait(timeout=30)
            print("[fusion] stopped managed Fusion 360 process")
        except Exception as exc:
            print(f"[fusion] managed process stop skipped: {exc}")
        finally:
            self._managed_process = None

    def _kill_existing_processes(self) -> None:
        if sys.platform != "win32":
            return
        killed = False
        for image_name in ("Fusion360.exe", "FusionLauncher.exe", "cer_dialog.exe"):
            try:
                result = subprocess.run(
                    ["taskkill", "/IM", image_name, "/F", "/T"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                continue
            killed = result.returncode == 0 or killed
        if killed:
            print("[fusion] killed existing Fusion-related processes")

    def _get_launcher(self) -> Launcher:
        if self._launcher is None:
            self._launcher = Launcher()
        return self._launcher


def _run_with_fusion_recovery(
    *,
    action_name: str,
    host: str,
    port: int,
    restart_controller: FusionRestartController,
    action: Callable[[JsonToFusionConverter, Fusion360Client], T],
    max_attempts: int = DEFAULT_FUSION_TRANSPORT_RETRY_ATTEMPTS,
) -> T:
    attempts = max(1, int(max_attempts))
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        if not restart_controller.prepare_for_processing(action_name):
            raise RuntimeError(
                f"Fusion recovery remained unavailable before {action_name}."
            )
        converter, client = _build_fusion_handles(host, port)
        try:
            return action(converter, client)
        except Exception as exc:
            last_error = exc
            if not _is_fusion_transport_error(exc) or attempt >= attempts:
                raise
            print(f"[fusion] transport error during {action_name}: {exc}")
            if not restart_controller.recover_or_restart(
                f"{action_name} failed with a transport error"
            ):
                raise
    assert last_error is not None
    raise last_error


def _process_experiment_sample(
    *,
    sample_index: int,
    candidate: LengthEditCandidate,
    source_payload: list[dict[str, Any]],
    source_records: list[ConstraintRecord],
    source_subset: str,
    source_domain: str,
    host: str,
    port: int,
    restart_controller: FusionRestartController,
    rng: random.Random,
    edit_scale_min: float,
    edit_scale_max: float,
    angle_delta_min_deg: float,
    angle_delta_max_deg: float,
    target_value_retry_attempts: int,
    length_tol_cm: float,
    angle_tol_deg: float,
    visualize: bool,
    render_visualization_assets_during_run: bool,
    visualization_output_dir: Path,
    visualization_image_size: int,
    visualization_view_orientation: str,
) -> dict[str, Any]:
    json_path = candidate.json_path
    sample_attempt = 1
    while True:
        work_name = f"sample {sample_index} ({json_path.name})"

        def _skip_current_sample(
            stage: str, exc: BaseException | None = None
        ) -> dict[str, Any]:
            error_text = str(exc).strip() if exc is not None else ""
            suffix = f": {error_text}" if error_text else ""
            print(
                f"[fusion] skipping {work_name} after repeated timeout during {stage}{suffix}"
            )
            return {
                "skipped": True,
                "skip_reason": "fusion_transport_timeout",
                "skip_stage": stage,
                "skip_error": error_text,
            }

        def _retry_current_sample(stage: str, exc: BaseException) -> bool:
            nonlocal sample_attempt
            if not _is_fusion_transport_error(exc):
                return False
            if sample_attempt >= DEFAULT_FUSION_TRANSPORT_RETRY_ATTEMPTS:
                return False
            print(f"[fusion] transport error during {stage}: {exc}")
            if not restart_controller.recover_or_restart(
                f"{stage} failed with a transport error"
            ):
                return False
            sample_attempt += 1
            return True

        if not restart_controller.prepare_for_processing(work_name):
            return _skip_current_sample(
                f"{work_name} startup",
                RuntimeError(
                    f"Fusion recovery remained unavailable before {work_name}."
                ),
            )
        sample_converter, sample_client = _build_fusion_handles(host, port)

        try:
            prepared_bundle = _prepare_candidate_execution_bundle(
                sample_index=sample_index,
                candidate=candidate,
                source_payload=source_payload,
                source_records=source_records,
                converter=sample_converter,
                client=sample_client,
                rng=rng,
                edit_scale_min=edit_scale_min,
                edit_scale_max=edit_scale_max,
                angle_delta_min_deg=angle_delta_min_deg,
                angle_delta_max_deg=angle_delta_max_deg,
                target_value_retry_attempts=target_value_retry_attempts,
                length_tol_cm=length_tol_cm,
                angle_tol_deg=angle_tol_deg,
            )
        except IntentInfeasibleError:
            return {"skipped": True, "skip_reason": "reference_infeasible"}
        except Exception as exc:
            if _retry_current_sample(f"{work_name} bundle preparation", exc):
                continue
            if _is_fusion_timeout_skip_error(exc):
                return _skip_current_sample(f"{work_name} bundle preparation", exc)
            raise

        new_value_numeric = prepared_bundle["new_value_numeric"]
        edit_parameters = prepared_bundle["edit_parameters"]
        target_record = prepared_bundle["target_record"]
        preserved_records = prepared_bundle["preserved_records"]
        constrained_payload = prepared_bundle["constrained_payload"]
        closure_only_payload = prepared_bundle["closure_only_payload"]
        precomputed_case_results = dict(
            prepared_bundle.get("precomputed_case_results") or {}
        )

        edited_value_repr = (
            _format_constraint_value(candidate.constraint_type, new_value_numeric)
            if new_value_numeric is not None
            and candidate.intent_family != INTENT_FAMILY_ADD_GEOMETRIC
            else None
        )
        sample_uid = _sample_uid_from_candidate(candidate)
        sample_name = _sample_source_name_from_candidate(candidate)
        manifest_entry = {
            "sample_id": sample_index,
            "sample_uid": sample_uid,
            "json_path": str(json_path),
            "source_subset": source_subset,
            "source_domain": source_domain,
            "op_index": candidate.op_index,
            "sketch_index": candidate.sketch_index,
            "intent_family": candidate.intent_family,
            "constraint_type": candidate.constraint_type,
            "target_constraint_type": candidate.constraint_type,
            "source_record_ref": (
                {
                    "constraint_type": candidate.constraint_type,
                    "entry_index": candidate.entry_index,
                }
                if candidate.entry_index is not None
                else None
            ),
            "target_value_domain": candidate.value_domain,
            "target_entry_index": candidate.entry_index,
            "entities": list(candidate.entities),
            "target_entities": list(candidate.entities),
            "line_name": candidate.line_name,
            "primary_entity": candidate.line_name,
            "extra": dict(candidate.extra)
            if isinstance(candidate.extra, dict)
            else candidate.extra,
            "target_extra": dict(candidate.extra)
            if isinstance(candidate.extra, dict)
            else candidate.extra,
            "source_value": (
                _manifest_value_repr(candidate.original_value_expr)
                if candidate.original_value_expr is not None
                else None
            ),
            "original_target_value_expr": candidate.original_value_expr,
            "original_target_value_numeric": candidate.original_value_mm,
            "target_value": (
                _manifest_value_repr(edited_value_repr)
                if edited_value_repr is not None
                else None
            ),
            "edited_target_value_expr": (
                _manifest_value_repr(edited_value_repr)
                if edited_value_repr is not None
                else None
            ),
            "edited_target_value_numeric": new_value_numeric,
            "edit_scale": edit_parameters["edit_scale"],
            "edit_delta_deg": edit_parameters["edit_delta_deg"],
            "source_constraint_count": candidate.sketch_constraint_count,
            "preserved_constraint_count": len(preserved_records),
        }
        manifest_entry["original_length_mm"] = candidate.original_value_mm
        manifest_entry["edited_length_mm"] = new_value_numeric

        sample_visualization_dir: Path | None = None
        deferred_branch_step_exports_attempted: set[str] = set()
        try:
            if visualize and render_visualization_assets_during_run:
                sample_visualization_dir = _render_sample_visualization_assets(
                    sample_id=sample_index,
                    sample_uid=sample_uid,
                    sample_name=sample_name,
                    output_dir=visualization_output_dir,
                    manifest_entry=manifest_entry,
                    source_payload=source_payload,
                    converter=sample_converter,
                    client=sample_client,
                    image_size=visualization_image_size,
                    view_orientation=visualization_view_orientation,
                )
            elif visualize:
                sample_visualization_dir = _prepare_sample_visualization_dir(
                    sample_id=sample_index,
                    sample_name=sample_name,
                    output_dir=visualization_output_dir,
                    manifest_entry=manifest_entry,
                    image_size=visualization_image_size,
                )
                if BRANCH_CONSTRAINED in precomputed_case_results:
                    deferred_branch_step_exports_attempted.add(BRANCH_CONSTRAINED)
                    step_error = _export_current_step(
                        client=sample_client,
                        output_path=sample_visualization_dir
                        / f"{BRANCH_CONSTRAINED}_model.step",
                    )
                    if step_error:
                        print(
                            f"[warn] STEP export failed for "
                            f"{sample_visualization_dir / f'{BRANCH_CONSTRAINED}_model.step'}: "
                            f"{step_error}"
                        )
                source_step_path = sample_visualization_dir / "source_model.step"
                source_step_error = _export_payload_step(
                    converter=sample_converter,
                    client=sample_client,
                    payload=source_payload,
                    output_path=source_step_path,
                    context=f"exp_{sample_uid}_source_step",
                )
                if source_step_error:
                    print(
                        f"[warn] STEP export failed for {source_step_path}: {source_step_error}"
                    )
        except Exception as exc:
            if _retry_current_sample(f"{work_name} visualization setup", exc):
                continue
            if _is_fusion_timeout_skip_error(exc):
                return _skip_current_sample(f"{work_name} visualization setup", exc)
            raise

        sample_rows: list[dict[str, Any]] = []
        sample_details: list[dict[str, Any]] = []
        retry_sample = False
        case_inputs = [
            (BRANCH_CONSTRAINED, constrained_payload),
            (BRANCH_CLOSURE_ONLY, closure_only_payload),
        ]
        for branch_name, payload in case_inputs:
            case_id = f"{sample_index:04d}_{branch_name}"
            row = {
                "sample_id": sample_index,
                "case_id": case_id,
                "branch": branch_name,
                "json_path": str(json_path),
                "source_subset": source_subset,
                "source_domain": source_domain,
                "op_index": candidate.op_index,
                "sketch_index": candidate.sketch_index,
                "intent_family": candidate.intent_family,
                "target_constraint_type": candidate.constraint_type,
                "target_value_domain": candidate.value_domain,
                "target_entry_index": candidate.entry_index,
                "target_entities": json.dumps(
                    list(candidate.entities), ensure_ascii=False
                ),
                "line_name": candidate.line_name,
                "primary_entity": candidate.line_name,
                "original_target_value_numeric": candidate.original_value_mm,
                "edited_target_value_numeric": new_value_numeric,
                "edit_scale": edit_parameters["edit_scale"],
                "edit_delta_deg": edit_parameters["edit_delta_deg"],
                "source_constraint_count": candidate.sketch_constraint_count,
                "preserved_constraint_count": len(preserved_records),
                "target_hit": None,
                "target_actual": None,
                "target_expected": None,
                "target_delta": None,
                "target_error": "",
                "preserved_supported_constraints": None,
                "preserved_satisfied_constraints": None,
                "preserved_unsupported_constraints": None,
                "preserved_total_constraints": None,
                "preserved_constraint_satisfaction_rate": None,
                "preserved_constraints_all_satisfied": None,
                "skip_count": None,
                "rebuild_success": False,
                "validation_ok": None,
                "validation_fully_constrained": None,
                "validation_over_constrained": None,
                "missing_ref_count": None,
                "overall_editable_success": False,
                "failure_reason": "",
                "exception": "",
            }
            row["original_length_mm"] = candidate.original_value_mm
            row["edited_length_mm"] = new_value_numeric
            details = {
                "sample_id": sample_index,
                "case_id": case_id,
                "branch": branch_name,
                "json_path": str(json_path),
                "source_subset": source_subset,
                "source_domain": source_domain,
                "op_index": candidate.op_index,
                "sketch_index": candidate.sketch_index,
                "line_name": candidate.line_name,
                "intent_family": candidate.intent_family,
                "target_value_domain": candidate.value_domain,
                "target_extra": dict(candidate.extra)
                if isinstance(candidate.extra, dict)
                else candidate.extra,
                "original_target_value_numeric": candidate.original_value_mm,
                "edited_target_value_numeric": new_value_numeric,
                "edit_scale": edit_parameters["edit_scale"],
                "edit_delta_deg": edit_parameters["edit_delta_deg"],
                "target_record": _constraint_record_to_dict(target_record),
                "preserved_records": [
                    _constraint_record_to_dict(record) for record in preserved_records
                ],
            }
            try:
                case_result = precomputed_case_results.get(branch_name)
                if case_result is None:
                    case_result = _execute_case(
                        converter=sample_converter,
                        client=sample_client,
                        payload=payload,
                        candidate=candidate,
                        target_record=target_record,
                        preserved_records=preserved_records,
                        case_name=case_id,
                        length_tol_cm=length_tol_cm,
                        angle_tol_deg=angle_tol_deg,
                    )
                evaluation = case_result["evaluation"]
                validation = case_result["validation"]
                execution = case_result.get("execution") or {}
                skip_count = len(execution.get("skipped_constraints", []))
                rebuild_success = _case_rebuild_success(case_result)
                validation_ok = _validation_is_healthy(validation)
                overall_editable_success = (
                    rebuild_success
                    and validation_ok
                    and evaluation.get("target_hit") is True
                    and evaluation.get("preserved_constraints_all_satisfied") is True
                )
                row.update(
                    {
                        "target_hit": evaluation.get("target_hit"),
                        "target_actual": evaluation.get("target_actual"),
                        "target_expected": evaluation.get("target_expected"),
                        "target_delta": evaluation.get("target_delta"),
                        "target_error": evaluation.get("target_error") or "",
                        "preserved_supported_constraints": evaluation.get(
                            "preserved_supported_constraints"
                        ),
                        "preserved_satisfied_constraints": evaluation.get(
                            "preserved_satisfied_constraints"
                        ),
                        "preserved_unsupported_constraints": evaluation.get(
                            "preserved_unsupported_constraints"
                        ),
                        "preserved_total_constraints": evaluation.get(
                            "preserved_total_constraints"
                        ),
                        "preserved_constraint_satisfaction_rate": evaluation.get(
                            "preserved_constraint_satisfaction_rate"
                        ),
                        "preserved_constraints_all_satisfied": evaluation.get(
                            "preserved_constraints_all_satisfied"
                        ),
                        "skip_count": skip_count,
                        "rebuild_success": rebuild_success,
                        "validation_ok": validation_ok,
                        "validation_fully_constrained": bool(
                            validation.get("is_fully_constrained", False)
                        ),
                        "validation_over_constrained": bool(
                            validation.get("over_constrained", False)
                        ),
                        "missing_ref_count": len(case_result.get("missing_refs", [])),
                        "overall_editable_success": overall_editable_success,
                    }
                )
                row["failure_reason"] = _classify_failure_reason(row)
                details.update(
                    {
                        "evaluation": evaluation,
                        "validation": validation,
                        "skipped_constraints": execution.get("skipped_constraints", []),
                        "missing_refs": case_result.get("missing_refs", []),
                        "rebuild_success": rebuild_success,
                        "overall_editable_success": overall_editable_success,
                        "failure_reason": row["failure_reason"],
                    }
                )
                if (
                    sample_visualization_dir is not None
                    and render_visualization_assets_during_run
                ):
                    _export_branch_visualization_image(
                        converter=sample_converter,
                        client=sample_client,
                        payload=payload,
                        sample_dir=sample_visualization_dir,
                        branch_name=branch_name,
                        image_size=visualization_image_size,
                        view_orientation=visualization_view_orientation,
                        title=(
                            "Constraint-aware render failed"
                            if branch_name == BRANCH_CONSTRAINED
                            else "Closure-only render failed"
                        ),
                    )
                elif (
                    sample_visualization_dir is not None
                    and branch_name not in deferred_branch_step_exports_attempted
                ):
                    deferred_branch_step_exports_attempted.add(branch_name)
                    step_path = sample_visualization_dir / f"{branch_name}_model.step"
                    step_error = _export_current_step(
                        client=sample_client,
                        output_path=step_path,
                    )
                    if step_error:
                        print(
                            f"[warn] STEP export failed for {step_path}: {step_error}"
                        )
            except Exception as exc:
                if _retry_current_sample(f"{work_name} {branch_name}", exc):
                    retry_sample = True
                    break
                if _is_fusion_timeout_skip_error(exc):
                    return _skip_current_sample(f"{work_name} {branch_name}", exc)
                row["exception"] = str(exc)
                row["failure_reason"] = _classify_failure_reason(row)
                details["exception"] = str(exc)
                if (
                    sample_visualization_dir is not None
                    and render_visualization_assets_during_run
                ):
                    _write_branch_visualization_placeholder(
                        sample_dir=sample_visualization_dir,
                        branch_name=branch_name,
                        image_size=visualization_image_size,
                        title=(
                            "Constraint-aware render failed"
                            if branch_name == BRANCH_CONSTRAINED
                            else "Closure-only render failed"
                        ),
                        body=str(exc),
                    )
            sample_rows.append(row)
            sample_details.append(details)

        if retry_sample:
            continue
        return {
            "skipped": False,
            "manifest_entry": manifest_entry,
            "rows": sample_rows,
            "details": sample_details,
        }


def run_experiment(
    *,
    dataset_root: Path,
    output_dir: Path,
    sample_size: int,
    sample_size_per_source_subset: int | None = None,
    test_uids_json: Path | None = DEFAULT_CLI_TEST_UIDS_JSON,
    seed: int,
    intent_families: tuple[str, ...] = DEFAULT_INTENT_FAMILIES,
    edit_scale_min: float,
    edit_scale_max: float,
    angle_delta_min_deg: float,
    angle_delta_max_deg: float,
    host: str,
    port: int,
    length_tol_cm: float,
    angle_tol_deg: float,
    max_samples_per_file_per_family: int = DEFAULT_MAX_SAMPLES_PER_FILE_PER_FAMILY,
    target_value_retry_attempts: int = DEFAULT_TARGET_VALUE_RETRY_ATTEMPTS,
    restart_every: int = DEFAULT_CLI_RESTART_EVERY,
    restart_timeout_seconds: float = DEFAULT_CLI_RESTART_TIMEOUT_SECONDS,
    restart_poll_seconds: float = DEFAULT_CLI_RESTART_POLL_SECONDS,
    health_timeout_seconds: float = DEFAULT_CLI_HEALTH_TIMEOUT_SECONDS,
    restart_retry_attempts: int = DEFAULT_CLI_RESTART_RETRY_ATTEMPTS,
    restart_retry_backoff_seconds: float = DEFAULT_CLI_RESTART_RETRY_BACKOFF_SECONDS,
    restart_kill_existing: bool = False,
    client_timeout_seconds: float | None = None,
    visualize: bool = False,
    render_visualization_assets_during_run: bool = False,
    visualization_output_dir: Path | None = None,
    visualization_selection_mode: str = "contrast",
    visualization_max_samples: int = 6,
    visualization_sample_ids: list[int] | None = None,
    visualization_image_size: int = 420,
    visualization_view_orientation: str = "iso",
    visualization_gallery_columns: int = 2,
) -> dict[str, Any]:
    if restart_every < 0:
        raise ValueError("restart_every must be >= 0.")
    if restart_timeout_seconds <= 0:
        raise ValueError("restart_timeout_seconds must be > 0.")
    if restart_poll_seconds <= 0:
        raise ValueError("restart_poll_seconds must be > 0.")
    if health_timeout_seconds <= 0:
        raise ValueError("health_timeout_seconds must be > 0.")
    if restart_retry_attempts <= 0:
        raise ValueError("restart_retry_attempts must be > 0.")
    if restart_retry_backoff_seconds < 0:
        raise ValueError("restart_retry_backoff_seconds must be >= 0.")
    if client_timeout_seconds is not None and client_timeout_seconds <= 0:
        raise ValueError("client_timeout_seconds must be > 0 when provided.")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    csv_path = output_dir / "results.csv"
    details_path = output_dir / "details.json"
    summary_path = output_dir / "summary.json"
    checkpoint_path = output_dir / CHECKPOINT_FILENAME
    resolved_visualization_output_dir = (
        visualization_output_dir or (output_dir / "visualization")
    ).resolve()

    normalized_intent_families = _normalize_intent_families(intent_families)
    using_source_subset_sampling = sample_size_per_source_subset is not None
    run_checkpoint_config = _build_run_checkpoint_config(
        dataset_root=dataset_root,
        output_dir=output_dir,
        sample_size=sample_size,
        sample_size_per_source_subset=sample_size_per_source_subset,
        test_uids_json=test_uids_json,
        seed=seed,
        normalized_intent_families=normalized_intent_families,
        edit_scale_min=edit_scale_min,
        edit_scale_max=edit_scale_max,
        angle_delta_min_deg=angle_delta_min_deg,
        angle_delta_max_deg=angle_delta_max_deg,
        host=host,
        port=port,
        length_tol_cm=length_tol_cm,
        angle_tol_deg=angle_tol_deg,
        max_samples_per_file_per_family=max_samples_per_file_per_family,
        target_value_retry_attempts=target_value_retry_attempts,
        restart_every=restart_every,
        restart_timeout_seconds=restart_timeout_seconds,
        restart_poll_seconds=restart_poll_seconds,
        health_timeout_seconds=health_timeout_seconds,
        restart_retry_attempts=restart_retry_attempts,
        restart_retry_backoff_seconds=restart_retry_backoff_seconds,
        restart_kill_existing=restart_kill_existing,
        client_timeout_seconds=client_timeout_seconds,
        visualize=visualize,
        render_visualization_assets_during_run=render_visualization_assets_during_run,
        visualization_output_dir=resolved_visualization_output_dir,
        visualization_selection_mode=visualization_selection_mode,
        visualization_max_samples=visualization_max_samples,
        visualization_sample_ids=visualization_sample_ids,
        visualization_image_size=visualization_image_size,
        visualization_view_orientation=visualization_view_orientation,
        visualization_gallery_columns=visualization_gallery_columns,
    )
    checkpoint_payload = _load_checkpoint_payload(checkpoint_path)
    resumed_from_checkpoint = False
    resume_from_completed_baseline = False
    completed_summary_payload: dict[str, Any] | None = None
    if checkpoint_payload is not None:
        checkpoint_status = str(checkpoint_payload.get("status") or "")
        saved_run_config = checkpoint_payload.get("run_config") or {}
        source_subset_quota_expanded = _is_source_subset_sample_size_expansion(
            saved_run_config,
            run_checkpoint_config,
        )
        _validate_checkpoint_config(
            saved_run_config,
            run_checkpoint_config,
            allow_expanded_source_subset_sample_size=(
                checkpoint_status in {"running", "completed"}
            ),
        )
        if checkpoint_status == "completed":
            completed_summary = checkpoint_payload.get("summary")
            if isinstance(completed_summary, dict):
                completed_summary_payload = completed_summary
            elif summary_path.is_file():
                completed_summary_payload = json.loads(
                    summary_path.read_text(encoding="utf-8")
                )
            if source_subset_quota_expanded:
                if not isinstance(completed_summary_payload, dict):
                    raise RuntimeError(
                        f"Completed checkpoint exists at {checkpoint_path}, but no summary payload was found."
                    )
                resume_from_completed_baseline = True
                resumed_from_checkpoint = True
            else:
                if isinstance(completed_summary_payload, dict):
                    return completed_summary_payload
                raise RuntimeError(
                    f"Completed checkpoint exists at {checkpoint_path}, but no summary payload was found."
                )
        else:
            resumed_from_checkpoint = True

    if client_timeout_seconds is not None:
        os.environ["FUSION360_CLIENT_TIMEOUT_SECONDS"] = _format_timeout_seconds(
            client_timeout_seconds
        )
    _configure_fusion_launch_env(port)

    rng = random.Random(seed)
    if checkpoint_payload is not None:
        rng_state = checkpoint_payload.get("rng_state")
        if rng_state is not None:
            rng.setstate(_json_lists_to_tuples(rng_state))

    local_converter = JsonToFusionConverter(host=host, port=port)
    prioritized_sample_stems = (
        _load_test_uid_stems(test_uids_json) if test_uids_json is not None else set()
    )
    source_payload_cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    restart_controller = FusionRestartController(
        host=host,
        port=port,
        restart_every=(int(restart_every) or None),
        restart_timeout_seconds=restart_timeout_seconds,
        restart_poll_seconds=restart_poll_seconds,
        health_timeout_seconds=health_timeout_seconds,
        restart_kill_existing=restart_kill_existing,
        restart_retry_attempts=restart_retry_attempts,
        restart_retry_backoff_seconds=restart_retry_backoff_seconds,
    )

    if checkpoint_payload is not None and not resume_from_completed_baseline:
        sampling_stats = dict(checkpoint_payload["sampling_stats"])
        initial_selected_candidate_count = int(
            checkpoint_payload["initial_selected_candidate_count"]
        )
        rows = list(checkpoint_payload.get("rows", []))
        detailed_cases = list(checkpoint_payload.get("detailed_cases", []))
        manifest = list(checkpoint_payload.get("manifest", []))
        candidate_queue = [
            _deserialize_candidate(item)
            for item in checkpoint_payload.get("candidate_queue", [])
        ]
        reserved_candidate_keys = {
            _json_lists_to_tuples(item)
            for item in checkpoint_payload.get("reserved_candidate_keys", [])
        }
        reserved_source_families = _source_families_from_reserved_candidate_keys(
            reserved_candidate_keys
        )
        reserved_source_keys = _source_keys_from_reserved_candidate_keys(
            reserved_candidate_keys
        )
        skipped_reference_infeasible_by_intent_family = {
            family: int(
                checkpoint_payload.get(
                    "skipped_reference_infeasible_by_intent_family", {}
                ).get(family, 0)
            )
            for family in normalized_intent_families
        }
        skipped_transport_timeout_by_intent_family = {
            family: int(
                checkpoint_payload.get(
                    "skipped_transport_timeout_by_intent_family", {}
                ).get(family, 0)
            )
            for family in normalized_intent_families
        }
        selected_candidate_count_pre_reference_target_validation = int(
            checkpoint_payload[
                "selected_candidate_count_pre_reference_target_validation"
            ]
        )
        selected_candidate_counts_by_intent_family_pre_reference_target_validation = {
            family: int(
                checkpoint_payload.get(
                    "selected_candidate_counts_by_intent_family_pre_reference_target_validation",
                    {},
                ).get(family, 0)
            )
            for family in normalized_intent_families
        }
        selected_candidate_counts_by_source_subset_pre_reference_target_validation = {
            subset: int(
                checkpoint_payload.get(
                    "selected_candidate_counts_by_source_subset_pre_reference_target_validation",
                    {},
                ).get(subset, 0)
            )
            for subset in KNOWN_SOURCE_SUBSETS
        }
        selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation = {
            subset: {
                family: int(
                    checkpoint_payload.get(
                        "selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation",
                        {},
                    )
                    .get(subset, {})
                    .get(family, 0)
                )
                for family in normalized_intent_families
            }
            for subset in KNOWN_SOURCE_SUBSETS
        }
        replacement_sampling_batch_count = int(
            checkpoint_payload.get("replacement_sampling_batch_count", 0)
        )
        replacement_sampling_call_count = int(
            checkpoint_payload.get("replacement_sampling_call_count", 0)
        )
        accepted_counts_by_intent_family = {
            family: int(
                checkpoint_payload.get("accepted_counts_by_intent_family", {}).get(
                    family, 0
                )
            )
            for family in normalized_intent_families
        }
        accepted_counts_by_source_subset_and_intent_family = {
            subset: {
                family: int(
                    checkpoint_payload.get(
                        "accepted_counts_by_source_subset_and_intent_family",
                        {},
                    )
                    .get(subset, {})
                    .get(family, 0)
                )
                for family in normalized_intent_families
            }
            for subset in KNOWN_SOURCE_SUBSETS
        }
        accepted_source_keys = {
            _source_json_key(entry.get("json_path"))
            for entry in manifest
            if entry.get("json_path")
        }
    elif resume_from_completed_baseline:
        if not manifest_path.is_file():
            raise RuntimeError(
                f"Cannot continue from completed checkpoint because {manifest_path} is missing."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = _load_results_csv(csv_path)
        detailed_cases = (
            json.loads(details_path.read_text(encoding="utf-8"))
            if details_path.is_file()
            else []
        )
        completed_summary = completed_summary_payload or {}
        sampling_stats = _rebuild_sampling_stats_from_completed_summary(
            completed_summary,
            intent_families=normalized_intent_families,
            using_source_subset_sampling=using_source_subset_sampling,
        )
        replacement_candidate_count_pre_reference_target_validation = int(
            completed_summary.get(
                "replacement_candidate_count_pre_reference_target_validation", 0
            )
        )
        selected_candidate_count_pre_reference_target_validation = int(
            completed_summary.get(
                "selected_candidate_count_pre_reference_target_validation",
                len(manifest),
            )
        )
        initial_selected_candidate_count = max(
            0,
            selected_candidate_count_pre_reference_target_validation
            - replacement_candidate_count_pre_reference_target_validation,
        )
        candidate_queue = []
        reserved_candidate_keys = {
            _reserved_candidate_key_from_manifest_entry(entry) for entry in manifest
        }
        reserved_source_families = _source_families_from_reserved_candidate_keys(
            reserved_candidate_keys
        )
        reserved_source_keys = _source_keys_from_reserved_candidate_keys(
            reserved_candidate_keys
        )
        accepted_counts_by_intent_family = {
            family: sum(
                1 for entry in manifest if str(entry.get("intent_family")) == family
            )
            for family in normalized_intent_families
        }
        accepted_counts_by_source_subset_and_intent_family = {
            subset: {
                family: sum(
                    1
                    for entry in manifest
                    if str(entry.get("source_subset")) == subset
                    and str(entry.get("intent_family")) == family
                )
                for family in normalized_intent_families
            }
            for subset in KNOWN_SOURCE_SUBSETS
        }
        accepted_source_keys = {
            _source_json_key(entry.get("json_path"))
            for entry in manifest
            if entry.get("json_path")
        }
        skipped_reference_infeasible_by_intent_family = {
            family: int(
                (
                    completed_summary.get(
                        "skipped_reference_infeasible_by_intent_family", {}
                    )
                    or {}
                ).get(
                    family,
                    0,
                )
            )
            for family in normalized_intent_families
        }
        skipped_transport_timeout_by_intent_family = {
            family: int(
                (
                    completed_summary.get(
                        "skipped_transport_timeout_by_intent_family", {}
                    )
                    or {}
                ).get(
                    family,
                    0,
                )
            )
            for family in normalized_intent_families
        }
        selected_candidate_counts_by_intent_family_pre_reference_target_validation = {
            family: int(
                (
                    completed_summary.get(
                        "selected_candidate_counts_by_intent_family_pre_reference_target_validation",
                        {},
                    )
                    or {}
                ).get(family, accepted_counts_by_intent_family[family])
            )
            for family in normalized_intent_families
        }
        selected_candidate_counts_by_source_subset_pre_reference_target_validation = {
            subset: int(
                (
                    completed_summary.get(
                        "selected_candidate_counts_by_source_subset_pre_reference_target_validation",
                        {},
                    )
                    or {}
                ).get(
                    subset,
                    sum(
                        accepted_counts_by_source_subset_and_intent_family[
                            subset
                        ].values()
                    ),
                )
            )
            for subset in KNOWN_SOURCE_SUBSETS
        }
        selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation = {
            subset: {
                family: int(
                    (
                        completed_summary.get(
                            "selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation",
                            {},
                        )
                        or {}
                    )
                    .get(subset, {})
                    .get(
                        family,
                        accepted_counts_by_source_subset_and_intent_family[subset][
                            family
                        ],
                    )
                )
                for family in normalized_intent_families
            }
            for subset in KNOWN_SOURCE_SUBSETS
        }
        replacement_sampling_batch_count = int(
            completed_summary.get("replacement_sampling_batch_count", 0)
        )
        replacement_sampling_call_count = int(
            completed_summary.get("replacement_sampling_call_count", 0)
        )
    else:
        if using_source_subset_sampling:
            selected_candidates, sampling_stats = _sample_candidates_by_source_subset(
                dataset_root,
                local_converter,
                rng=rng,
                sample_size_per_source_subset=int(sample_size_per_source_subset),
                target_source_subsets=KNOWN_SOURCE_SUBSETS,
                intent_families=normalized_intent_families,
                length_tol_cm=length_tol_cm,
                angle_tol_deg=angle_tol_deg,
                prioritized_sample_stems=prioritized_sample_stems,
                max_samples_per_file_per_family=max_samples_per_file_per_family,
                client=None,
            )
        else:
            selected_candidates, sampling_stats = _sample_candidates_by_family(
                dataset_root,
                local_converter,
                rng=rng,
                sample_size=sample_size,
                intent_families=normalized_intent_families,
                length_tol_cm=length_tol_cm,
                angle_tol_deg=angle_tol_deg,
                prioritized_sample_stems=prioritized_sample_stems,
                max_samples_per_file_per_family=max_samples_per_file_per_family,
                client=None,
            )

        initial_selected_candidate_count = len(selected_candidates)
        rows = []
        detailed_cases = []
        manifest = []
        reserved_candidate_keys = {
            _candidate_identity(candidate) for candidate in selected_candidates
        }
        reserved_source_families = _source_families_from_reserved_candidate_keys(
            reserved_candidate_keys
        )
        reserved_source_keys = _source_keys_from_reserved_candidate_keys(
            reserved_candidate_keys
        )
        skipped_reference_infeasible_by_intent_family = dict.fromkeys(
            normalized_intent_families, 0
        )
        skipped_transport_timeout_by_intent_family = dict.fromkeys(
            normalized_intent_families, 0
        )
        selected_candidate_count_pre_reference_target_validation = len(
            selected_candidates
        )
        selected_candidate_counts_by_intent_family_pre_reference_target_validation = {
            family: int(sampling_stats["sample_counts_by_intent_family"].get(family, 0))
            for family in normalized_intent_families
        }
        selected_candidate_counts_by_source_subset_pre_reference_target_validation = {
            subset: int(
                sampling_stats.get("sample_counts_by_source_subset", {}).get(subset, 0)
            )
            for subset in KNOWN_SOURCE_SUBSETS
        }
        selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation = {
            subset: {
                family: int(
                    sampling_stats.get(
                        "sample_counts_by_source_subset_and_intent_family", {}
                    )
                    .get(subset, {})
                    .get(family, 0)
                )
                for family in normalized_intent_families
            }
            for subset in KNOWN_SOURCE_SUBSETS
        }
        replacement_sampling_batch_count = 0
        replacement_sampling_call_count = 0
        accepted_counts_by_intent_family = dict.fromkeys(normalized_intent_families, 0)
        accepted_counts_by_source_subset_and_intent_family = {
            subset: dict.fromkeys(normalized_intent_families, 0)
            for subset in KNOWN_SOURCE_SUBSETS
        }
        candidate_queue = list(selected_candidates)
        accepted_source_keys: set[str] = set()

    def _persist_current_progress() -> None:
        _persist_experiment_progress(
            checkpoint_path=checkpoint_path,
            manifest_path=manifest_path,
            csv_path=csv_path,
            details_path=details_path,
            checkpoint_payload=_build_running_checkpoint_payload(
                run_config=run_checkpoint_config,
                sampling_stats=sampling_stats,
                initial_selected_candidate_count=initial_selected_candidate_count,
                candidate_queue=candidate_queue,
                manifest=manifest,
                rows=rows,
                detailed_cases=detailed_cases,
                skipped_reference_infeasible_by_intent_family=(
                    skipped_reference_infeasible_by_intent_family
                ),
                skipped_transport_timeout_by_intent_family=(
                    skipped_transport_timeout_by_intent_family
                ),
                selected_candidate_count_pre_reference_target_validation=(
                    selected_candidate_count_pre_reference_target_validation
                ),
                selected_candidate_counts_by_intent_family_pre_reference_target_validation=(
                    selected_candidate_counts_by_intent_family_pre_reference_target_validation
                ),
                selected_candidate_counts_by_source_subset_pre_reference_target_validation=(
                    selected_candidate_counts_by_source_subset_pre_reference_target_validation
                ),
                selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation=(
                    selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation
                ),
                replacement_sampling_batch_count=replacement_sampling_batch_count,
                replacement_sampling_call_count=replacement_sampling_call_count,
                accepted_counts_by_intent_family=accepted_counts_by_intent_family,
                accepted_counts_by_source_subset_and_intent_family=(
                    accepted_counts_by_source_subset_and_intent_family
                ),
                reserved_candidate_keys=reserved_candidate_keys,
                rng=rng,
            ),
            manifest=manifest,
            rows=rows,
            detailed_cases=detailed_cases,
        )

    # A resumed run already has the same state on disk; avoid rewriting large
    # checkpoint/manifest/detail payloads before any new progress is made.
    if checkpoint_payload is None:
        _persist_current_progress()

    def _quotas_met() -> bool:
        if using_source_subset_sampling:
            assert sample_size_per_source_subset is not None
            return all(
                accepted_counts_by_source_subset_and_intent_family[subset][family]
                >= int(sample_size_per_source_subset)
                for subset in KNOWN_SOURCE_SUBSETS
                for family in normalized_intent_families
            )
        return all(
            accepted_counts_by_intent_family[family] >= sample_size
            for family in normalized_intent_families
        )

    while True:
        if not candidate_queue:
            if _quotas_met():
                break

            replacement_candidates, replacement_sampling_stats = (
                _sample_replacement_candidates_for_remaining_quota(
                    dataset_root=dataset_root,
                    converter=local_converter,
                    client=None,
                    rng=rng,
                    using_source_subset_sampling=using_source_subset_sampling,
                    requested_sample_size=(
                        int(sample_size_per_source_subset)
                        if using_source_subset_sampling
                        and sample_size_per_source_subset is not None
                        else sample_size
                    ),
                    intent_families=normalized_intent_families,
                    accepted_counts_by_intent_family=accepted_counts_by_intent_family,
                    accepted_counts_by_source_subset_and_intent_family=accepted_counts_by_source_subset_and_intent_family,
                    length_tol_cm=length_tol_cm,
                    angle_tol_deg=angle_tol_deg,
                    prioritized_sample_stems=prioritized_sample_stems,
                    max_samples_per_file_per_family=max_samples_per_file_per_family,
                    excluded_candidate_keys=reserved_candidate_keys,
                    reserved_source_families=reserved_source_families,
                )
            )
            if not replacement_candidates:
                break

            replacement_sampling_batch_count += 1
            replacement_sampling_call_count += int(
                replacement_sampling_stats["sampling_call_count"]
            )
            selected_candidate_count_pre_reference_target_validation += len(
                replacement_candidates
            )
            for family in normalized_intent_families:
                selected_candidate_counts_by_intent_family_pre_reference_target_validation[
                    family
                ] += int(
                    replacement_sampling_stats["sample_counts_by_intent_family"].get(
                        family, 0
                    )
                )
            for subset in KNOWN_SOURCE_SUBSETS:
                selected_candidate_counts_by_source_subset_pre_reference_target_validation[
                    subset
                ] += int(
                    replacement_sampling_stats["sample_counts_by_source_subset"].get(
                        subset, 0
                    )
                )
                for family in normalized_intent_families:
                    selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation[
                        subset
                    ][family] += int(
                        replacement_sampling_stats[
                            "sample_counts_by_source_subset_and_intent_family"
                        ]
                        .get(subset, {})
                        .get(family, 0)
                    )
            candidate_queue.extend(replacement_candidates)
            reserved_candidate_keys.update(
                _candidate_identity(candidate) for candidate in replacement_candidates
            )
            for candidate in replacement_candidates:
                reserved_source_keys.add(_candidate_source_json_key(candidate))
                reserved_source_families.setdefault(
                    _candidate_source_json_key(candidate),
                    set(),
                ).add(candidate.intent_family)
            _persist_current_progress()

        candidate = candidate_queue.pop(0)
        candidate_source_key = _candidate_source_json_key(candidate)
        if candidate_source_key in accepted_source_keys:
            _persist_current_progress()
            continue
        sample_index = len(manifest) + 1
        json_path = candidate.json_path
        source_payload = _load_source_payload_cached(
            converter=local_converter,
            json_path=json_path,
            cache=source_payload_cache,
        )
        source_records = _supported_source_records_for_candidate(
            source_payload,
            candidate,
            local_converter,
        )
        source_subset = _infer_source_subset(json_path)
        source_domain = _infer_source_domain(source_subset)
        sample_result = _process_experiment_sample(
            sample_index=sample_index,
            candidate=candidate,
            source_payload=source_payload,
            source_records=source_records,
            source_subset=source_subset,
            source_domain=source_domain,
            host=host,
            port=port,
            restart_controller=restart_controller,
            rng=rng,
            edit_scale_min=edit_scale_min,
            edit_scale_max=edit_scale_max,
            angle_delta_min_deg=angle_delta_min_deg,
            angle_delta_max_deg=angle_delta_max_deg,
            target_value_retry_attempts=target_value_retry_attempts,
            length_tol_cm=length_tol_cm,
            angle_tol_deg=angle_tol_deg,
            visualize=visualize,
            render_visualization_assets_during_run=render_visualization_assets_during_run,
            visualization_output_dir=resolved_visualization_output_dir,
            visualization_image_size=visualization_image_size,
            visualization_view_orientation=visualization_view_orientation,
        )
        if sample_result.get("skipped"):
            skip_reason = str(
                sample_result.get("skip_reason") or "reference_infeasible"
            )
            if skip_reason == "fusion_transport_timeout":
                skipped_transport_timeout_by_intent_family[candidate.intent_family] += 1
            else:
                skipped_reference_infeasible_by_intent_family[
                    candidate.intent_family
                ] += 1
            _persist_current_progress()
            continue

        manifest_entry = sample_result["manifest_entry"]
        manifest.append(manifest_entry)
        accepted_source_keys.add(candidate_source_key)
        accepted_counts_by_intent_family[candidate.intent_family] += 1
        if source_subset in accepted_counts_by_source_subset_and_intent_family:
            accepted_counts_by_source_subset_and_intent_family[source_subset][
                candidate.intent_family
            ] += 1
        rows.extend(sample_result["rows"])
        detailed_cases.extend(sample_result["details"])
        _persist_current_progress()
        restart_controller.maybe_restart(
            processed_units=len(manifest),
            has_pending_work=bool(candidate_queue) or not _quotas_met(),
        )

    actual_sample_counts_by_intent_family = {
        family: sum(1 for entry in manifest if str(entry["intent_family"]) == family)
        for family in normalized_intent_families
    }
    actual_sample_counts_by_source_subset = {
        source_subset: sum(
            1 for entry in manifest if str(entry["source_subset"]) == source_subset
        )
        for source_subset in sorted({str(entry["source_subset"]) for entry in manifest})
    }
    actual_sample_counts_by_source_subset_and_intent_family = {
        subset: {
            family: sum(
                1
                for entry in manifest
                if str(entry["source_subset"]) == subset
                and str(entry["intent_family"]) == family
            )
            for family in normalized_intent_families
        }
        for subset in KNOWN_SOURCE_SUBSETS
    }
    actual_sample_counts_by_source_domain = {
        source_domain: sum(
            1 for entry in manifest if str(entry["source_domain"]) == source_domain
        )
        for source_domain in sorted({str(entry["source_domain"]) for entry in manifest})
    }
    requested_sample_size = (
        int(sample_size_per_source_subset)
        if using_source_subset_sampling and sample_size_per_source_subset is not None
        else sample_size
    )
    requested_sample_size_total = (
        requested_sample_size
        * len(KNOWN_SOURCE_SUBSETS)
        * len(normalized_intent_families)
        if using_source_subset_sampling
        else requested_sample_size * len(normalized_intent_families)
    )
    summary = {
        "dataset_root": str(dataset_root),
        "sample_size_requested": requested_sample_size,
        "sample_size_requested_total": requested_sample_size_total,
        "selected_candidate_count_pre_reference_target_validation": selected_candidate_count_pre_reference_target_validation,
        "sample_size_actual": len(manifest),
        "sampling_unit": (
            "streaming_random_files_until_source_subset_quota"
            if using_source_subset_sampling
            else "streaming_random_files_until_family_quota"
        ),
        "sample_size_semantics": (
            "per_source_subset_per_intent_family"
            if using_source_subset_sampling
            else "per_intent_family"
        ),
        "files_total": sampling_stats["files_total"],
        "files_scanned": sampling_stats["files_scanned"],
        "files_with_candidates": sampling_stats["files_with_candidates"],
        "stopped_early_after_quota": sampling_stats["stopped_early"],
        "eligible_candidate_count": sampling_stats["observed_candidate_count"],
        "eligible_candidate_count_complete": not sampling_stats["stopped_early"],
        "observed_candidate_count_by_intent_family": sampling_stats[
            "observed_candidate_count_by_intent_family"
        ],
        "assigned_file_count_by_intent_family": sampling_stats[
            "assigned_file_count_by_intent_family"
        ],
        "seed": seed,
        "intent_families": list(normalized_intent_families),
        "intent_family": normalized_intent_families[0]
        if len(normalized_intent_families) == 1
        else None,
        "supported_edit_dimension_types": sorted(SUPPORTED_EDIT_DIMENSION_TYPES),
        "supported_add_geometric_types": sorted(SUPPORTED_ADD_GEOMETRIC_TYPES),
        "default_intent_families": list(DEFAULT_INTENT_FAMILIES),
        "supported_intent_families": list(SUPPORTED_EXPERIMENT_INTENT_FAMILIES),
        "selected_candidate_counts_by_intent_family_pre_reference_target_validation": dict(
            selected_candidate_counts_by_intent_family_pre_reference_target_validation
        ),
        "sample_counts_by_intent_family": actual_sample_counts_by_intent_family,
        "sample_counts_by_constraint_type": {
            constraint_type: sum(
                1
                for entry in manifest
                if str(entry["target_constraint_type"]) == constraint_type
            )
            for constraint_type in sorted(
                {str(entry["target_constraint_type"]) for entry in manifest}
            )
        },
        "sample_counts_by_source_subset": actual_sample_counts_by_source_subset,
        "sample_counts_by_source_subset_and_intent_family": actual_sample_counts_by_source_subset_and_intent_family,
        "sample_counts_by_source_domain": actual_sample_counts_by_source_domain,
        "skipped_reference_infeasible_candidates": sum(
            skipped_reference_infeasible_by_intent_family.values()
        ),
        "skipped_reference_infeasible_by_intent_family": skipped_reference_infeasible_by_intent_family,
        "skipped_transport_timeout_candidates": sum(
            skipped_transport_timeout_by_intent_family.values()
        ),
        "skipped_transport_timeout_by_intent_family": skipped_transport_timeout_by_intent_family,
        "replacement_sampling_batch_count": replacement_sampling_batch_count,
        "replacement_sampling_call_count": replacement_sampling_call_count,
        "replacement_candidate_count_pre_reference_target_validation": (
            selected_candidate_count_pre_reference_target_validation
            - initial_selected_candidate_count
        ),
        "edit_scale_min": edit_scale_min,
        "edit_scale_max": edit_scale_max,
        "edit_scale_neutral_hole_min": DEFAULT_EDIT_SCALE_NEUTRAL_HOLE_MIN,
        "edit_scale_neutral_hole_max": DEFAULT_EDIT_SCALE_NEUTRAL_HOLE_MAX,
        "angle_delta_min_deg": angle_delta_min_deg,
        "angle_delta_max_deg": angle_delta_max_deg,
        "closure_constraint_types": sorted(CLOSURE_ONLY_CONSTRAINT_TYPES),
        "length_tolerance_cm": length_tol_cm,
        "angle_tolerance_deg": angle_tol_deg,
        "max_samples_per_file_per_family": max_samples_per_file_per_family,
        "target_value_retry_attempts": target_value_retry_attempts,
        BRANCH_CONSTRAINED: _summarize_branch_results(
            rows,
            BRANCH_CONSTRAINED,
            detailed_cases=detailed_cases,
        ),
        BRANCH_CLOSURE_ONLY: _summarize_branch_results(
            rows,
            BRANCH_CLOSURE_ONLY,
            detailed_cases=detailed_cases,
        ),
        "manifest_json": str(manifest_path),
        "results_csv": str(csv_path),
        "details_json": str(details_path),
        "checkpoint_json": str(checkpoint_path),
        "resumed_from_checkpoint": resumed_from_checkpoint,
    }

    if using_source_subset_sampling:
        summary["target_source_subsets"] = list(KNOWN_SOURCE_SUBSETS)
        summary["observed_candidate_count_by_source_subset"] = sampling_stats[
            "observed_candidate_count_by_source_subset"
        ]
        summary["assigned_file_count_by_source_subset"] = sampling_stats[
            "assigned_file_count_by_source_subset"
        ]
        summary[
            "selected_candidate_counts_by_source_subset_pre_reference_target_validation"
        ] = dict(
            selected_candidate_counts_by_source_subset_pre_reference_target_validation
        )
        summary[
            "selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation"
        ] = dict(
            selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation
        )
        summary[
            "quota_met_by_source_subset_selected_pre_reference_target_validation"
        ] = {
            subset: all(
                selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation[
                    subset
                ][family]
                >= requested_sample_size
                for family in normalized_intent_families
            )
            for subset in KNOWN_SOURCE_SUBSETS
        }
        summary[
            "quota_met_by_source_subset_and_intent_family_selected_pre_reference_target_validation"
        ] = {
            subset: {
                family: selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation[
                    subset
                ][family]
                >= requested_sample_size
                for family in normalized_intent_families
            }
            for subset in KNOWN_SOURCE_SUBSETS
        }
        summary[
            "remaining_quota_by_source_subset_selected_pre_reference_target_validation"
        ] = {
            subset: max(
                0,
                (requested_sample_size * len(normalized_intent_families))
                - selected_candidate_counts_by_source_subset_pre_reference_target_validation[
                    subset
                ],
            )
            for subset in KNOWN_SOURCE_SUBSETS
        }
        summary[
            "remaining_quota_by_source_subset_and_intent_family_selected_pre_reference_target_validation"
        ] = {
            subset: {
                family: max(
                    0,
                    requested_sample_size
                    - selected_candidate_counts_by_source_subset_and_intent_family_pre_reference_target_validation[
                        subset
                    ][family],
                )
                for family in normalized_intent_families
            }
            for subset in KNOWN_SOURCE_SUBSETS
        }
        summary["quota_met_by_source_subset"] = {
            subset: all(
                actual_sample_counts_by_source_subset_and_intent_family.get(
                    subset, {}
                ).get(family, 0)
                >= requested_sample_size
                for family in normalized_intent_families
            )
            for subset in KNOWN_SOURCE_SUBSETS
        }
        summary["remaining_quota_by_source_subset"] = {
            subset: max(
                0,
                (requested_sample_size * len(normalized_intent_families))
                - actual_sample_counts_by_source_subset.get(subset, 0),
            )
            for subset in KNOWN_SOURCE_SUBSETS
        }
        summary["quota_met_by_source_subset_and_intent_family"] = {
            subset: {
                family: actual_sample_counts_by_source_subset_and_intent_family.get(
                    subset, {}
                ).get(family, 0)
                >= requested_sample_size
                for family in normalized_intent_families
            }
            for subset in KNOWN_SOURCE_SUBSETS
        }
        summary["remaining_quota_by_source_subset_and_intent_family"] = {
            subset: {
                family: max(
                    0,
                    requested_sample_size
                    - actual_sample_counts_by_source_subset_and_intent_family.get(
                        subset, {}
                    ).get(family, 0),
                )
                for family in normalized_intent_families
            }
            for subset in KNOWN_SOURCE_SUBSETS
        }
        summary["files_skipped_outside_target_subsets"] = sampling_stats[
            "files_skipped_outside_target_subsets"
        ]
    else:
        summary[
            "quota_met_by_intent_family_selected_pre_reference_target_validation"
        ] = {
            family: selected_candidate_counts_by_intent_family_pre_reference_target_validation[
                family
            ]
            >= requested_sample_size
            for family in normalized_intent_families
        }
        summary[
            "remaining_quota_by_intent_family_selected_pre_reference_target_validation"
        ] = {
            family: max(
                0,
                requested_sample_size
                - selected_candidate_counts_by_intent_family_pre_reference_target_validation[
                    family
                ],
            )
            for family in normalized_intent_families
        }
        summary["quota_met_by_intent_family"] = {
            family: actual_sample_counts_by_intent_family.get(family, 0)
            >= requested_sample_size
            for family in normalized_intent_families
        }
        summary["remaining_quota_by_intent_family"] = {
            family: max(
                0,
                requested_sample_size
                - actual_sample_counts_by_intent_family.get(family, 0),
            )
            for family in normalized_intent_families
        }

    if visualize:
        try:
            visualization_summary = _build_experiment_visualizations(
                result_dir=output_dir,
                output_dir=visualization_output_dir,
                selection_mode=visualization_selection_mode,
                max_samples=visualization_max_samples,
                sample_ids=visualization_sample_ids,
                host=host,
                port=port,
                image_size=visualization_image_size,
                view_orientation=visualization_view_orientation,
                gallery_columns=visualization_gallery_columns,
            )
            summary["visualization"] = {
                "enabled": True,
                "inline_render_during_run": bool(
                    render_visualization_assets_during_run
                ),
                "status": "ok",
                **visualization_summary,
            }
        except Exception as exc:
            summary["visualization"] = {
                "enabled": True,
                "inline_render_during_run": bool(
                    render_visualization_assets_during_run
                ),
                "status": "failed",
                "error": str(exc),
                "output_dir": str(resolved_visualization_output_dir.resolve()),
            }
    else:
        summary["visualization"] = {
            "enabled": False,
            "inline_render_during_run": bool(render_visualization_assets_during_run),
        }

    _write_json_atomically(
        checkpoint_path,
        {
            "version": CHECKPOINT_VERSION,
            "status": "completed",
            "run_config": run_checkpoint_config,
            "summary": summary,
        },
    )
    _write_json_atomically(summary_path, summary)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the constraint editability experiment on HistCAD JSON files. "
            "Defaults use the dimensional edit families; "
            "pass --intent-families add_geometric to enable the full supported intent space."
        ),
    )
    parser.add_argument(
        "--dataset-root",
        default=str(DEFAULT_CLI_DATASET_ROOT),
        help=(
            "Root directory containing cleaned JSON files. "
            f"Default: {DEFAULT_CLI_DATASET_ROOT}"
        ),
    )
    parser.add_argument(
        "--test-uids-json",
        default="",
        help=(
            "Optional JSON file of stems to prioritize during sampling; "
            "if quotas are not met, sampling falls back to files outside the list. "
            "Default: disabled."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_CLI_OUTPUT_DIR),
        help=(
            "Directory for CSV/JSON outputs and resume checkpoints. "
            "Rerunning with the same directory automatically resumes an interrupted run. "
            f"Default: {DEFAULT_CLI_OUTPUT_DIR}"
        ),
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_CLI_SAMPLE_SIZE,
        help=(
            "Number of edit intents to sample for each enabled intent family. "
            f"Default: {DEFAULT_CLI_SAMPLE_SIZE}"
        ),
    )
    parser.add_argument(
        "--sample-size-per-source-subset",
        type=int,
        default=DEFAULT_CLI_SAMPLE_SIZE_PER_SOURCE_SUBSET,
        help=(
            "Sample this many benchmark cases for each source subset and each enabled intent family. "
            f"Known subsets follow the directory mapping: 0001-0099 -> {SOURCE_SUBSET_HISTCAD_DEEPCAD}, "
            f"0100 -> {SOURCE_SUBSET_HISTCAD_FUSION360}, 0101 -> {SOURCE_SUBSET_HISTCAD_INDUSTRIAL}. "
            "When set, this stratified subset-plus-family quota overrides the default global per-intent-family sampling semantics. "
            f"Default: {DEFAULT_CLI_SAMPLE_SIZE_PER_SOURCE_SUBSET}"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_CLI_SEED,
        help=f"Random seed for sampling and target generation. Default: {DEFAULT_CLI_SEED}",
    )
    parser.add_argument(
        "--intent-families",
        nargs="+",
        default=list(DEFAULT_INTENT_FAMILIES),
        help=(
            "Intent families to sample from. Accepts space- or comma-separated values from "
            f"{', '.join(SUPPORTED_EXPERIMENT_INTENT_FAMILIES)}. "
            "Defaults to the dimensional edit families."
        ),
    )
    parser.add_argument(
        "--edit-scale-min",
        type=float,
        default=DEFAULT_EDIT_SCALE_MIN,
        help=(
            "Minimum multiplicative edit scale for length-like dimensions. "
            f"Default: {DEFAULT_EDIT_SCALE_MIN}"
        ),
    )
    parser.add_argument(
        "--edit-scale-max",
        type=float,
        default=DEFAULT_EDIT_SCALE_MAX,
        help=(
            "Maximum multiplicative edit scale for length-like dimensions. "
            f"Default: {DEFAULT_EDIT_SCALE_MAX}"
        ),
    )
    parser.add_argument(
        "--angle-delta-min-deg",
        type=float,
        default=DEFAULT_ANGLE_DELTA_MIN_DEG,
        help="Minimum absolute angular perturbation in degrees.",
    )
    parser.add_argument(
        "--angle-delta-max-deg",
        type=float,
        default=DEFAULT_ANGLE_DELTA_MAX_DEG,
        help="Maximum absolute angular perturbation in degrees.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_CLI_HOST,
        help=f"Fusion 360 bridge host. Default: {DEFAULT_CLI_HOST}",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_CLI_PORT,
        help=f"Fusion 360 bridge port. Default: {DEFAULT_CLI_PORT}",
    )
    parser.add_argument(
        "--restart-every",
        type=int,
        default=DEFAULT_CLI_RESTART_EVERY,
        help="Restart Fusion 360 after every N accepted samples. 0 disables scheduled restarts.",
    )
    parser.add_argument(
        "--restart-timeout-seconds",
        type=float,
        default=DEFAULT_CLI_RESTART_TIMEOUT_SECONDS,
        help="Seconds to wait for Fusion 360 to come back after launch or restart.",
    )
    parser.add_argument(
        "--restart-poll-seconds",
        type=float,
        default=DEFAULT_CLI_RESTART_POLL_SECONDS,
        help="Polling interval, in seconds, while waiting for Fusion after restart.",
    )
    parser.add_argument(
        "--health-timeout-seconds",
        type=float,
        default=DEFAULT_CLI_HEALTH_TIMEOUT_SECONDS,
        help="Short HTTP timeout, in seconds, for Fusion health probes such as ping and clear.",
    )
    parser.add_argument(
        "--restart-retry-attempts",
        type=int,
        default=DEFAULT_CLI_RESTART_RETRY_ATTEMPTS,
        help="How many times to retry a failed Fusion restart before aborting the run.",
    )
    parser.add_argument(
        "--restart-retry-backoff-seconds",
        type=float,
        default=DEFAULT_CLI_RESTART_RETRY_BACKOFF_SECONDS,
        help="Base backoff, in seconds, between automatic Fusion restart retries.",
    )
    parser.add_argument(
        "--restart-kill-existing",
        action="store_true",
        help="Kill existing Fusion360.exe processes during auto-restart to avoid stale windows piling up.",
    )
    parser.add_argument(
        "--client-timeout-seconds",
        type=float,
        default=300,
        help="Abort a single Fusion HTTP request after N seconds. Leave unset to keep the client default.",
    )
    parser.add_argument(
        "--length-tol-cm",
        type=float,
        default=DEFAULT_LENGTH_TOL_CM,
        help="Length tolerance in Fusion's internal cm units.",
    )
    parser.add_argument(
        "--angle-tol-deg",
        type=float,
        default=DEFAULT_ANGLE_TOL_DEG,
        help="Angle tolerance in degrees.",
    )
    parser.add_argument(
        "--max-samples-per-file-per-family",
        type=int,
        default=DEFAULT_MAX_SAMPLES_PER_FILE_PER_FAMILY,
        help=(
            "Legacy compatibility cap. The sampler now enforces a stricter global rule: "
            "each source JSON can contribute at most one accepted benchmark sample total."
        ),
    )
    parser.add_argument(
        "--target-value-retry-attempts",
        type=int,
        default=DEFAULT_TARGET_VALUE_RETRY_ATTEMPTS,
        help=(
            "Maximum number of target-value retries for dimensional edits before the "
            "reference-infeasible edit is dropped from the benchmark sample set."
        ),
    )
    parser.add_argument(
        "--skip-visualization",
        action="store_true",
        default=True,
        help="Skip automatic qualitative visualization after the experiment finishes. This is the default.",
    )
    parser.add_argument(
        "--visualize",
        dest="skip_visualization",
        action="store_false",
        help="Render qualitative visualization after the experiment finishes.",
    )
    parser.add_argument(
        "--render-visualization-assets-during-run",
        action="store_true",
        help=(
            "Eagerly render per-sample visualization assets during the main experiment loop. "
            "Disabled by default to reduce Fusion calls; when omitted, selected visualizations "
            "are rendered once after sampling."
        ),
    )
    parser.add_argument(
        "--visualization-output-dir",
        help="Directory for rendered comparison panels. Defaults to <output-dir>/visualization.",
    )
    parser.add_argument(
        "--visualization-selection",
        default=DEFAULT_CLI_VISUALIZATION_SELECTION,
        choices=["contrast", "constrained_failures", "all"],
        help=(
            "How to choose representative samples for automatic visualization. "
            f"Default: {DEFAULT_CLI_VISUALIZATION_SELECTION}"
        ),
    )
    parser.add_argument(
        "--visualization-sample-ids",
        help="Comma-separated sample IDs for visualization. Overrides --visualization-selection.",
    )
    parser.add_argument(
        "--visualization-max-samples",
        type=int,
        default=DEFAULT_CLI_VISUALIZATION_MAX_SAMPLES,
        help=(
            "Maximum number of samples to render automatically. "
            f"Default: {DEFAULT_CLI_VISUALIZATION_MAX_SAMPLES}"
        ),
    )
    parser.add_argument(
        "--visualization-image-size",
        type=int,
        default=DEFAULT_CLI_VISUALIZATION_IMAGE_SIZE,
        help=(
            "Visualization tile size in pixels. "
            f"Default: {DEFAULT_CLI_VISUALIZATION_IMAGE_SIZE}"
        ),
    )
    parser.add_argument(
        "--visualization-view-orientation",
        default=DEFAULT_CLI_VISUALIZATION_VIEW_ORIENTATION,
        help=(
            "Fusion view orientation for automatic visualization, e.g. iso/front/top. "
            f"Default: {DEFAULT_CLI_VISUALIZATION_VIEW_ORIENTATION}"
        ),
    )
    parser.add_argument(
        "--visualization-gallery-columns",
        type=int,
        default=DEFAULT_CLI_VISUALIZATION_GALLERY_COLUMNS,
        help=(
            "Number of columns in the automatic visualization gallery image. "
            f"Default: {DEFAULT_CLI_VISUALIZATION_GALLERY_COLUMNS}"
        ),
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    summary = run_experiment(
        dataset_root=Path(args.dataset_root).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        sample_size=int(args.sample_size),
        sample_size_per_source_subset=(
            int(args.sample_size_per_source_subset)
            if args.sample_size_per_source_subset is not None
            else None
        ),
        test_uids_json=(
            Path(args.test_uids_json).resolve() if args.test_uids_json else None
        ),
        seed=int(args.seed),
        intent_families=tuple(args.intent_families),
        edit_scale_min=float(args.edit_scale_min),
        edit_scale_max=float(args.edit_scale_max),
        angle_delta_min_deg=float(args.angle_delta_min_deg),
        angle_delta_max_deg=float(args.angle_delta_max_deg),
        host=str(args.host),
        port=int(args.port),
        restart_every=int(args.restart_every),
        restart_timeout_seconds=float(args.restart_timeout_seconds),
        restart_poll_seconds=float(args.restart_poll_seconds),
        health_timeout_seconds=float(args.health_timeout_seconds),
        restart_retry_attempts=int(args.restart_retry_attempts),
        restart_retry_backoff_seconds=float(args.restart_retry_backoff_seconds),
        restart_kill_existing=bool(args.restart_kill_existing),
        client_timeout_seconds=(
            float(args.client_timeout_seconds)
            if args.client_timeout_seconds is not None
            else None
        ),
        length_tol_cm=float(args.length_tol_cm),
        angle_tol_deg=float(args.angle_tol_deg),
        max_samples_per_file_per_family=int(args.max_samples_per_file_per_family),
        target_value_retry_attempts=int(args.target_value_retry_attempts),
        visualize=not bool(args.skip_visualization),
        render_visualization_assets_during_run=bool(
            args.render_visualization_assets_during_run
        ),
        visualization_output_dir=(
            Path(args.visualization_output_dir).resolve()
            if args.visualization_output_dir
            else None
        ),
        visualization_selection_mode=str(args.visualization_selection),
        visualization_max_samples=int(args.visualization_max_samples),
        visualization_sample_ids=_parse_visualization_sample_ids(
            args.visualization_sample_ids
        ),
        visualization_image_size=int(args.visualization_image_size),
        visualization_view_orientation=str(args.visualization_view_orientation),
        visualization_gallery_columns=int(args.visualization_gallery_columns),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
