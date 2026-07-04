from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from export.converter import JsonToFusionConverter  # noqa: E402
from fusion360_tools.client.fusion360_client import Fusion360Client  # noqa: E402

from editability.experiment import (  # noqa: E402
    BRANCH_CLOSURE_ONLY,
    BRANCH_CONSTRAINED,
    LengthEditCandidate,
    _build_closure_only_payload,
    _build_constrained_payload,
    _sample_source_name_from_manifest_entry,
    _sample_uid_from_manifest_entry,
    _visualization_sample_dir_name,
)

CANVAS_BG = "#f6f1e8"
SKETCH_BG = "#fcfaf6"
PRIMARY = "#153a5b"
ACCENT = "#c24d2c"
SUCCESS = "#2f7d4a"
FAILURE = "#b54545"
MUTED = "#5f6b72"
GRID_LINE = "#d8d2c8"
PAPER = "#fbf7ef"
PAPER_EDGE = "#d8cebe"
PAPER_INK = "#1f1f1f"
PAPER_SUBTLE = "#6d655d"
PAPER_PANEL = "#fffdf8"
PAPER_HIGHLIGHT = "#efe5d2"


@dataclass(frozen=True)
class BranchResult:
    sample_id: int
    branch: str
    case_id: str
    json_path: Path
    line_name: str
    target_constraint_type: str
    original_length_mm: float | None
    edited_length_mm: float | None
    edit_scale: float | None
    preserved_constraint_satisfaction_rate: float | None
    overall_editable_success: bool
    target_hit: bool | None
    rebuild_success: bool
    validation_ok: bool
    failure_reason: str
    row: dict[str, Any]
    intent_family: str = "modify_dimension"
    target_value_domain: str = "length"
    original_target_value_numeric: float | None = None
    edited_target_value_numeric: float | None = None
    edit_delta_deg: float | None = None
    broken_constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class SampleComparison:
    sample_id: int
    manifest_entry: dict[str, Any]
    constrained: BranchResult
    closure_only: BranchResult


def _parse_bool(value: str | bool | None) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _parse_float(value: str | float | int | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _primitive_name_from_ref(ref: str) -> str:
    return str(ref).split(".", 1)[0]


def _target_entities(entry: dict[str, Any]) -> tuple[str, ...]:
    raw_entities = entry.get("target_entities") or entry.get("entities") or []
    return tuple(str(item) for item in raw_entities)


def _format_numeric_value(value: float | None, domain: str) -> str:
    if value is None:
        return "n/a"
    if str(domain) == "angle":
        return f"{float(value):.2f} deg"
    if str(domain) == "relation":
        return "relation"
    return f"{float(value):.3f} mm"


def _intent_subject(entry: dict[str, Any]) -> str:
    entities = _target_entities(entry)
    if entities:
        return ", ".join(entities)
    primary_entity = str(entry.get("primary_entity") or entry.get("line_name") or "")
    return primary_entity or "unknown"


def _intent_summary(entry: dict[str, Any]) -> str:
    family = str(entry.get("intent_family") or "modify_dimension")
    constraint_type = str(
        entry.get("target_constraint_type") or entry.get("constraint_type") or ""
    )
    domain = str(entry.get("target_value_domain") or "length")
    source_value = _parse_float(
        entry.get("original_target_value_numeric", entry.get("original_length_mm"))
    )
    target_value = _parse_float(
        entry.get("edited_target_value_numeric", entry.get("edited_length_mm"))
    )
    subject = _intent_subject(entry)

    if family == "add_geometric":
        return f"add {constraint_type}({subject})"
    if (
        family == "modify_dimension"
        and source_value is not None
        and target_value is not None
    ):
        return (
            f"modify {constraint_type}({subject}) "
            f"{_format_numeric_value(source_value, domain)} -> {_format_numeric_value(target_value, domain)}"
        )
    if target_value is not None:
        return f"add {constraint_type}({subject}) {_format_numeric_value(target_value, domain)}"
    return f"{family} {constraint_type}({subject})"


def _intent_short_summary(entry: dict[str, Any]) -> str:
    family = str(entry.get("intent_family") or "modify_dimension")
    constraint_type = str(
        entry.get("target_constraint_type") or entry.get("constraint_type") or ""
    )
    domain = str(entry.get("target_value_domain") or "length")
    source_value = _parse_float(
        entry.get("original_target_value_numeric", entry.get("original_length_mm"))
    )
    target_value = _parse_float(
        entry.get("edited_target_value_numeric", entry.get("edited_length_mm"))
    )

    if family == "modify_dimension":
        return f"{_format_numeric_value(source_value, domain)} -> {_format_numeric_value(target_value, domain)}"
    if family == "add_dimension":
        return f"{constraint_type}: {_format_numeric_value(target_value, domain)}"
    if family == "add_geometric":
        return f"new relation: {constraint_type}"
    return _intent_summary(entry)


def _intent_card_summary(entry: dict[str, Any]) -> str:
    family = str(entry.get("intent_family") or "modify_dimension")
    constraint_type = str(
        entry.get("target_constraint_type")
        or entry.get("constraint_type")
        or "constraint"
    )
    domain = str(entry.get("target_value_domain") or "length")
    source_value = _parse_float(
        entry.get("original_target_value_numeric", entry.get("original_length_mm"))
    )
    target_value = _parse_float(
        entry.get("edited_target_value_numeric", entry.get("edited_length_mm"))
    )

    if family == "modify_dimension":
        return f"{_format_numeric_value(source_value, domain)} -> {_format_numeric_value(target_value, domain)}"
    if family == "add_dimension":
        return f"target {_format_numeric_value(target_value, domain)}"
    if family == "add_geometric":
        return f"{constraint_type} relation"
    return _intent_short_summary(entry)


def _family_label(family: str) -> str:
    labels = {
        "modify_dimension": "Modify Dimension",
        "add_dimension": "Add Dimension",
        "add_geometric": "Add Geometric",
    }
    return labels.get(str(family), str(family).replace("_", " ").title())


def _entity_phrase(entities: tuple[str, ...]) -> str:
    if not entities:
        return "the selected entities"
    if len(entities) == 1:
        return entities[0]
    if len(entities) == 2:
        return f"{entities[0]} and {entities[1]}"
    return ", ".join(entities[:-1]) + f", and {entities[-1]}"


def _constraint_result_label(result: dict[str, Any]) -> str:
    constraint_type = str(result.get("constraint_type") or "constraint")
    raw_entities = result.get("entities") or []
    entities = tuple(str(item) for item in raw_entities)
    if not entities:
        return constraint_type
    if len(entities) <= 2:
        return f"{constraint_type}({', '.join(entities)})"
    return f"{constraint_type}({entities[0]}, {entities[1]}, ...)"


def _summarize_broken_constraints(
    results: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    max_items: int = 3,
) -> tuple[str, ...]:
    labels: list[str] = []
    seen: set[str] = set()
    for result in results:
        if bool(result.get("ok")):
            continue
        label = _constraint_result_label(result)
        if label in seen:
            continue
        labels.append(label)
        seen.add(label)
        if len(labels) >= max_items:
            break
    return tuple(labels)


def _intent_action_title(entry: dict[str, Any]) -> str:
    family = str(entry.get("intent_family") or "modify_dimension")
    constraint_type = str(
        entry.get("target_constraint_type")
        or entry.get("constraint_type")
        or "constraint"
    )
    entities = _target_entities(entry)
    primary_entity = str(entry.get("primary_entity") or entry.get("line_name") or "")

    if family == "modify_dimension":
        subject = primary_entity or _entity_phrase(entities)
        return f"Change {constraint_type} on {subject}"

    if family == "add_dimension":
        if len(entities) >= 2:
            return f"Add {constraint_type} between {entities[0]} and {entities[1]}"
        subject = primary_entity or _entity_phrase(entities)
        return f"Add {constraint_type} on {subject}"

    if family == "add_geometric":
        normalized_type = constraint_type.strip().lower()
        if (
            normalized_type
            in {
                "parallel",
                "perpendicular",
                "equal",
                "concentric",
                "tangent",
                "normal",
                "coincident",
            }
            and len(entities) >= 2
        ):
            return f"Make {entities[0]} {normalized_type} to {entities[1]}"
        if normalized_type == "midpoint" and len(entities) >= 2:
            return f"Make {entities[0]} the midpoint of {entities[1]}"
        if normalized_type in {"horizontal", "vertical"}:
            if len(entities) == 1:
                return f"Make {entities[0]} {normalized_type}"
            if len(entities) >= 2:
                adverb = (
                    "horizontally" if normalized_type == "horizontal" else "vertically"
                )
                return f"Align {entities[0]} and {entities[1]} {adverb}"
        return f"Add {constraint_type} on {_entity_phrase(entities)}"

    return _intent_summary(entry)


def _intent_value_blocks(entry: dict[str, Any]) -> list[tuple[str, str]]:
    family = str(entry.get("intent_family") or "modify_dimension")
    domain = str(entry.get("target_value_domain") or "length")
    source_value = _parse_float(
        entry.get("original_target_value_numeric", entry.get("original_length_mm"))
    )
    target_value = _parse_float(
        entry.get("edited_target_value_numeric", entry.get("edited_length_mm"))
    )
    constraint_type = str(
        entry.get("target_constraint_type")
        or entry.get("constraint_type")
        or "constraint"
    )

    if family == "modify_dimension":
        return [
            ("Original", _format_numeric_value(source_value, domain)),
            ("Target", _format_numeric_value(target_value, domain)),
        ]
    if family == "add_dimension":
        return [
            ("New constraint", constraint_type),
            ("Target", _format_numeric_value(target_value, domain)),
        ]
    return [
        ("New relation", constraint_type),
        ("Effect", "enforce relation"),
    ]


def _intent_card_lines(entry: dict[str, Any]) -> list[str]:
    family = str(entry.get("intent_family") or "modify_dimension")
    constraint_type = str(
        entry.get("target_constraint_type") or entry.get("constraint_type") or "unknown"
    )
    entities = _target_entities(entry)
    source_record_ref = entry.get("source_record_ref")
    extra = entry.get("target_extra") or entry.get("extra") or {}

    lines = [
        f"Action: {_intent_action_title(entry)}",
        f"Family: {_family_label(family)}",
        f"Constraint: {constraint_type}",
        f"Entities: {', '.join(entities) if entities else 'n/a'}",
    ]
    if source_record_ref:
        lines.append(
            "Source record: "
            f"{source_record_ref.get('constraint_type', 'unknown')}#{source_record_ref.get('entry_index', 'n/a')}"
        )
    else:
        lines.append("Source record: new constraint")
    if isinstance(extra, dict) and extra:
        for key in sorted(extra):
            lines.append(f"{str(key).replace('_', ' ').title()}: {extra[key]}")
    return lines


def _panel_title(sample: SampleComparison) -> str:
    return f"Sample {sample.sample_id:03d} | {_intent_summary(sample.manifest_entry)}"


def _point_xy_from_ref(sketch: dict[str, Any], ref: str) -> tuple[float, float] | None:
    primitive_name, _, suffix = str(ref).partition(".")
    primitive = sketch.get(primitive_name)
    if not isinstance(primitive, dict) or not suffix:
        return None
    if suffix in {"start", "end", "middle", "center"}:
        point = primitive.get(suffix)
        if isinstance(point, list) and len(point) >= 2:
            return float(point[0]), float(point[1])
    return None


def _load_broken_constraints_by_case(result_dir: Path) -> dict[str, tuple[str, ...]]:
    details_path = result_dir / "details.json"
    if not details_path.exists():
        return {}
    details = json.loads(details_path.read_text(encoding="utf-8"))
    broken_by_case: dict[str, tuple[str, ...]] = {}
    for item in details:
        case_id = item.get("case_id")
        if not case_id:
            continue
        evaluation = item.get("evaluation") or {}
        preserved_results = (
            evaluation.get("preserved_records")
            or evaluation.get("preserved_results")
            or []
        )
        broken_by_case[str(case_id)] = _summarize_broken_constraints(preserved_results)
    return broken_by_case


def _load_results_rows(
    result_dir: Path,
    broken_constraints_by_case: dict[str, tuple[str, ...]] | None = None,
) -> dict[int, dict[str, BranchResult]]:
    csv_path = result_dir / "results.csv"
    rows_by_sample: dict[int, dict[str, BranchResult]] = {}
    broken_constraints_by_case = broken_constraints_by_case or {}
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            sample_id = int(row["sample_id"])
            branch = str(row["branch"])
            case_id = str(row["case_id"])
            branch_result = BranchResult(
                sample_id=sample_id,
                branch=branch,
                case_id=case_id,
                json_path=Path(row["json_path"]),
                line_name=str(row["line_name"]),
                target_constraint_type=str(row["target_constraint_type"]),
                original_length_mm=_parse_float(row.get("original_length_mm")),
                edited_length_mm=_parse_float(row.get("edited_length_mm")),
                edit_scale=_parse_float(row.get("edit_scale")),
                preserved_constraint_satisfaction_rate=_parse_float(
                    row.get("preserved_constraint_satisfaction_rate")
                ),
                overall_editable_success=bool(
                    _parse_bool(row.get("overall_editable_success"))
                ),
                target_hit=_parse_bool(row.get("target_hit")),
                rebuild_success=bool(_parse_bool(row.get("rebuild_success"))),
                validation_ok=bool(_parse_bool(row.get("validation_ok"))),
                failure_reason=str(row.get("failure_reason") or ""),
                row=dict(row),
                intent_family=str(row.get("intent_family") or "modify_dimension"),
                target_value_domain=str(row.get("target_value_domain") or "length"),
                original_target_value_numeric=_parse_float(
                    row.get("original_target_value_numeric")
                ),
                edited_target_value_numeric=_parse_float(
                    row.get("edited_target_value_numeric")
                ),
                edit_delta_deg=_parse_float(row.get("edit_delta_deg")),
                broken_constraints=broken_constraints_by_case.get(case_id, ()),
            )
            rows_by_sample.setdefault(sample_id, {})[branch] = branch_result
    return rows_by_sample


def _load_manifest(result_dir: Path) -> dict[int, dict[str, Any]]:
    manifest_path = result_dir / "manifest.json"
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {int(entry["sample_id"]): dict(entry) for entry in entries}


def load_sample_comparisons(result_dir: Path) -> list[SampleComparison]:
    manifest_by_sample = _load_manifest(result_dir)
    broken_constraints_by_case = _load_broken_constraints_by_case(result_dir)
    rows_by_sample = _load_results_rows(result_dir, broken_constraints_by_case)
    comparisons: list[SampleComparison] = []
    for sample_id, manifest_entry in sorted(manifest_by_sample.items()):
        branch_rows = rows_by_sample.get(sample_id, {})
        constrained = branch_rows.get(BRANCH_CONSTRAINED)
        closure_only = branch_rows.get(BRANCH_CLOSURE_ONLY)
        if constrained is None or closure_only is None:
            continue
        comparisons.append(
            SampleComparison(
                sample_id=sample_id,
                manifest_entry=manifest_entry,
                constrained=constrained,
                closure_only=closure_only,
            )
        )
    return comparisons


def _candidate_from_manifest(entry: dict[str, Any]) -> LengthEditCandidate:
    entry_index = entry.get("target_entry_index")
    return LengthEditCandidate(
        json_path=Path(entry["json_path"]),
        op_index=int(entry["op_index"]),
        sketch_index=int(entry["sketch_index"]),
        constraint_type=str(entry["target_constraint_type"]),
        entry_index=int(entry_index) if entry_index is not None else None,
        entities=_target_entities(entry),
        line_name=str(entry.get("primary_entity") or entry.get("line_name") or ""),
        original_value_expr=entry.get("original_target_value_expr"),
        original_value_mm=_parse_float(
            entry.get("original_target_value_numeric", entry.get("original_length_mm"))
        ),
        intent_family=str(entry.get("intent_family") or "modify_dimension"),
        sketch_constraint_count=int(entry["source_constraint_count"]),
        value_domain=str(entry.get("target_value_domain", "length")),
        extra=dict(entry.get("target_extra") or {}) or None,
    )


def _contrast_sort_key(sample: SampleComparison) -> tuple[Any, ...]:
    failure_priority = {
        "target_miss": 0,
        "preserved_constraints_broken": 1,
        "downstream_rebuild_failed": 2,
        "": 3,
    }
    constrained_rate = sample.constrained.preserved_constraint_satisfaction_rate
    closure_rate = sample.closure_only.preserved_constraint_satisfaction_rate
    delta = (constrained_rate or 0.0) - (closure_rate or 0.0)
    return (
        failure_priority.get(sample.closure_only.failure_reason, 99),
        -delta,
        sample.sample_id,
    )


def _failure_sort_key(sample: SampleComparison) -> tuple[Any, ...]:
    failure_priority = {
        "downstream_rebuild_failed": 0,
        "preserved_constraints_broken": 1,
        "target_miss": 2,
        "": 3,
    }
    return (
        failure_priority.get(sample.constrained.failure_reason, 99),
        sample.sample_id,
    )


def select_samples(
    comparisons: list[SampleComparison],
    *,
    mode: str,
    max_samples: int,
    sample_ids: list[int] | None,
) -> list[SampleComparison]:
    by_id = {item.sample_id: item for item in comparisons}
    if sample_ids:
        selected = [by_id[sample_id] for sample_id in sample_ids if sample_id in by_id]
        return selected[:max_samples]

    if mode == "contrast":
        pool = [
            item
            for item in comparisons
            if item.constrained.overall_editable_success
            and not item.closure_only.overall_editable_success
        ]
        pool.sort(key=_contrast_sort_key)
        return pool[:max_samples]

    if mode == "constrained_failures":
        pool = [
            item
            for item in comparisons
            if not item.constrained.overall_editable_success
        ]
        pool.sort(key=_failure_sort_key)
        return pool[:max_samples]

    return comparisons[:max_samples]


def _load_font(
    size: int,
    *,
    preferred: tuple[str, ...] | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = list(preferred or ())
    candidates.extend(
        [
            "C:\\Windows\\Fonts\\cambria.ttc",
            "C:\\Windows\\Fonts\\georgia.ttf",
            "C:\\Windows\\Fonts\\times.ttf",
            "C:\\Windows\\Fonts\\timesbd.ttf",
            "C:\\Windows\\Fonts\\arial.ttf",
            "C:\\Windows\\Fonts\\segoeui.ttf",
            "DejaVuSerif.ttf",
            "DejaVuSans.ttf",
        ]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _safe_rate_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.1f}%"


def _status_text(branch_result: BranchResult) -> str:
    if branch_result.overall_editable_success:
        return "success"
    if branch_result.failure_reason:
        return branch_result.failure_reason.replace("_", " ")
    return "failed"


def _status_color(branch_result: BranchResult) -> str:
    return SUCCESS if branch_result.overall_editable_success else FAILURE


def _join_phrases(items: tuple[str, ...]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _branch_note_text(branch_result: BranchResult) -> str:
    if branch_result.overall_editable_success:
        return "same edit, all preserved"
    if (
        branch_result.failure_reason == "preserved_constraints_broken"
        and branch_result.broken_constraints
    ):
        return f"breaks {_join_phrases(branch_result.broken_constraints)}"
    preserved = _safe_rate_text(branch_result.preserved_constraint_satisfaction_rate)
    return f"preserved {preserved}"


def _tile_caption_lines(sample: SampleComparison, branch: str) -> list[str]:
    if branch == "original":
        return ["Reference model", "before edit"]
    branch_result = (
        sample.constrained if branch == BRANCH_CONSTRAINED else sample.closure_only
    )
    branch_label = (
        "With constraints" if branch == BRANCH_CONSTRAINED else "Closure only"
    )
    return [
        branch_label,
        _branch_note_text(branch_result),
    ]


def _draw_metric_box(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    label: str,
    value: str,
    label_font: ImageFont.ImageFont,
    value_font: ImageFont.ImageFont,
) -> None:
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=18,
        fill=PAPER_HIGHLIGHT,
        outline=PAPER_EDGE,
        width=1,
    )
    draw.text((x + 18, y + 14), label.upper(), fill=PAPER_SUBTLE, font=label_font)
    draw.text((x + 18, y + 48), value, fill=PAPER_INK, font=value_font)


def _write_placeholder_image(path: Path, title: str, body: str, size: int) -> None:
    image = Image.new("RGB", (size, size), color="white")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(28)
    body_font = _load_font(20)
    draw.rectangle((12, 12, size - 12, size - 12), outline=FAILURE, width=4)
    draw.text((28, 30), title, fill=FAILURE, font=title_font)
    draw.multiline_text((28, 90), body, fill="#222222", font=body_font, spacing=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def write_placeholder_image(path: Path, *, title: str, body: str, size: int) -> None:
    _write_placeholder_image(path, title=title, body=body, size=size)


def _wrap_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    if max_width <= 8:
        return [text] if text else [""]

    def _word_fits(word: str) -> bool:
        bbox = draw.textbbox((0, 0), word, font=font)
        return (bbox[2] - bbox[0]) <= max_width

    def _split_word(word: str) -> list[str]:
        if _word_fits(word):
            return [word]
        parts: list[str] = []
        current = ""
        for char in word:
            candidate = f"{current}{char}"
            if current and not _word_fits(candidate):
                parts.append(current)
                current = char
            else:
                current = candidate
        if current:
            parts.append(current)
        return parts or [word]

    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    expanded_words: list[str] = []
    for word in words:
        expanded_words.extend(_split_word(word))

    current = expanded_words[0]
    for word in expanded_words[1:]:
        candidate = f"{current} {word}"
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
    lines.append(current)
    return lines


def _line_height(font: ImageFont.ImageFont, *, extra: int = 0) -> int:
    bbox = font.getbbox("Ag")
    return (bbox[3] - bbox[1]) + extra


def _fit_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    preferred_sizes: list[int],
    preferred_fonts: tuple[str, ...] | None,
    max_width: int,
    max_lines: int,
) -> tuple[ImageFont.ImageFont, list[str]]:
    for size in preferred_sizes:
        font = _load_font(size, preferred=preferred_fonts)
        lines = _wrap_text_to_width(draw, text, font=font, max_width=max_width)
        if len(lines) <= max_lines:
            return font, lines
    fallback_size = preferred_sizes[-1]
    fallback_font = _load_font(fallback_size, preferred=preferred_fonts)
    lines = _wrap_text_to_width(draw, text, font=fallback_font, max_width=max_width)
    if len(lines) <= max_lines:
        return fallback_font, lines

    trimmed = lines[:max_lines]
    last_line = trimmed[-1]
    ellipsis = "..."
    while last_line:
        candidate = last_line.rstrip() + ellipsis
        bbox = draw.textbbox((0, 0), candidate, font=fallback_font)
        if (bbox[2] - bbox[0]) <= max_width:
            trimmed[-1] = candidate
            break
        last_line = last_line[:-1]
    else:
        trimmed[-1] = ellipsis
    return fallback_font, trimmed


def _draw_wrapped_lines(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    lines: list[str],
    font: ImageFont.ImageFont,
    fill: str,
    line_gap: int,
) -> int:
    line_height = _line_height(font, extra=line_gap)
    for line in lines:
        draw.text((x, y), line, fill=fill, font=font)
        y += line_height
    return y


def render_intent_card(
    entry: dict[str, Any],
    *,
    output_path: Path,
    image_size: int,
) -> None:
    image = Image.new("RGB", (image_size, image_size), color=PAPER_PANEL)
    draw = ImageDraw.Draw(image)
    margin = max(16, image_size // 18)
    inner_left = margin
    inner_right = image_size - margin
    max_text_width = inner_right - inner_left

    eyebrow_font = _load_font(16, preferred=("C:\\Windows\\Fonts\\arial.ttf",))
    draw.rectangle((0, 0, image_size - 1, image_size - 1), outline=PAPER_EDGE, width=2)

    label_y = margin + 6
    draw.text((inner_left, label_y), "Local edit", fill=PAPER_SUBTLE, font=eyebrow_font)
    draw.line(
        (inner_left, label_y + 24, inner_right, label_y + 24), fill=PAPER_EDGE, width=2
    )

    headline = _intent_action_title(entry)
    title_font, headline_lines = _fit_wrapped_text(
        draw,
        headline,
        preferred_sizes=[28, 26, 24, 22],
        preferred_fonts=(
            "C:\\Windows\\Fonts\\timesbd.ttf",
            "C:\\Windows\\Fonts\\cambria.ttc",
        ),
        max_width=max_text_width,
        max_lines=3,
    )
    y = label_y + 42
    y = _draw_wrapped_lines(
        draw,
        x=inner_left,
        y=y,
        lines=headline_lines,
        font=title_font,
        fill=PAPER_INK,
        line_gap=4,
    )

    summary_font, summary_lines = _fit_wrapped_text(
        draw,
        _intent_card_summary(entry),
        preferred_sizes=[20, 19, 18, 17],
        preferred_fonts=("C:\\Windows\\Fonts\\cambria.ttc",),
        max_width=max_text_width,
        max_lines=2,
    )
    y += 10
    y = _draw_wrapped_lines(
        draw,
        x=inner_left,
        y=y,
        lines=summary_lines,
        font=summary_font,
        fill=PAPER_SUBTLE,
        line_gap=3,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


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


def _sample_arc_points(
    start: tuple[float, float],
    middle: tuple[float, float],
    end: tuple[float, float],
    count: int = 128,
) -> list[tuple[float, float]]:
    arc_circle = _arc_circle_from_points(start, middle, end)
    if arc_circle is None:
        return [start, middle, end]
    center, radius = arc_circle

    def _angle(point: tuple[float, float]) -> float:
        return math.atan2(point[1] - center[1], point[0] - center[0])

    angle_start = _angle(start)
    angle_middle = _angle(middle)
    angle_end = _angle(end)

    ccw_span = (angle_end - angle_start) % (2.0 * math.pi)
    ccw_middle = (angle_middle - angle_start) % (2.0 * math.pi)
    use_ccw = ccw_middle <= ccw_span

    if use_ccw:
        stop = angle_start + ccw_span
    else:
        cw_span = (angle_start - angle_end) % (2.0 * math.pi)
        stop = angle_start - cw_span

    samples: list[tuple[float, float]] = []
    if count <= 1:
        count = 2
    for index in range(count):
        t = index / float(count - 1)
        angle = angle_start + (stop - angle_start) * t
        samples.append(
            (
                center[0] + radius * math.cos(angle),
                center[1] + radius * math.sin(angle),
            )
        )
    return samples


def _collect_sketch_bounds(sketch: dict[str, Any]) -> tuple[float, float, float, float]:
    points: list[tuple[float, float]] = []
    for primitive_name, primitive in sketch.items():
        if primitive_name.startswith("line_"):
            points.append((float(primitive["start"][0]), float(primitive["start"][1])))
            points.append((float(primitive["end"][0]), float(primitive["end"][1])))
        elif primitive_name.startswith("circle_"):
            cx, cy = float(primitive["center"][0]), float(primitive["center"][1])
            radius = float(primitive["radius"])
            points.extend(
                [
                    (cx - radius, cy - radius),
                    (cx + radius, cy + radius),
                ]
            )
        elif primitive_name.startswith("arc_"):
            start = (float(primitive["start"][0]), float(primitive["start"][1]))
            middle = (float(primitive["middle"][0]), float(primitive["middle"][1]))
            end = (float(primitive["end"][0]), float(primitive["end"][1]))
            points.extend(_sample_arc_points(start, middle, end, count=64))
    if not points:
        return (-1.0, 1.0, -1.0, 1.0)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y, 1.0)
    pad = span * 0.12
    return min_x - pad, max_x + pad, min_y - pad, max_y + pad


def render_sketch_image(
    sketch: dict[str, Any],
    *,
    target_entities: tuple[str, ...],
    output_path: Path,
    image_size: int,
) -> None:
    fig = plt.figure(
        figsize=(image_size / 100.0, image_size / 100.0), dpi=100, facecolor=SKETCH_BG
    )
    axis = fig.add_subplot(111)
    axis.set_facecolor(SKETCH_BG)
    axis.grid(False)
    highlighted_primitives = {_primitive_name_from_ref(ref) for ref in target_entities}
    highlighted_points = {ref for ref in target_entities if "." in ref}

    for primitive_name, primitive in sorted(sketch.items()):
        is_target = primitive_name in highlighted_primitives
        if primitive_name.startswith("line_"):
            start = primitive["start"]
            end = primitive["end"]
            color = ACCENT if is_target else PRIMARY
            width = 3.8 if is_target else 2.2
            axis.plot(
                [float(start[0]), float(end[0])],
                [float(start[1]), float(end[1])],
                color=color,
                linewidth=width,
                solid_capstyle="round",
            )
        elif primitive_name.startswith("circle_"):
            circle = Circle(
                (float(primitive["center"][0]), float(primitive["center"][1])),
                float(primitive["radius"]),
                edgecolor=ACCENT if is_target else PRIMARY,
                facecolor="none",
                linewidth=3.0 if is_target else 2.0,
            )
            axis.add_patch(circle)
        elif primitive_name.startswith("arc_"):
            start = (float(primitive["start"][0]), float(primitive["start"][1]))
            middle = (float(primitive["middle"][0]), float(primitive["middle"][1]))
            end = (float(primitive["end"][0]), float(primitive["end"][1]))
            samples = _sample_arc_points(start, middle, end)
            axis.plot(
                [point[0] for point in samples],
                [point[1] for point in samples],
                color=ACCENT if is_target else PRIMARY,
                linewidth=3.0 if is_target else 2.0,
                solid_capstyle="round",
            )

    for point_ref in sorted(highlighted_points):
        point_xy = _point_xy_from_ref(sketch, point_ref)
        if point_xy is None:
            continue
        axis.scatter(
            [point_xy[0]],
            [point_xy[1]],
            s=54,
            color=ACCENT,
            edgecolors="white",
            linewidths=1.2,
            zorder=6,
        )

    min_x, max_x, min_y, max_y = _collect_sketch_bounds(sketch)
    axis.set_xlim(min_x, max_x)
    axis.set_ylim(min_y, max_y)
    axis.set_aspect("equal", adjustable="box")
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.set_xticks([])
    axis.set_yticks([])
    axis.axhline(0.0, color=GRID_LINE, linewidth=0.8, zorder=0)
    axis.axvline(0.0, color=GRID_LINE, linewidth=0.8, zorder=0)
    fig.subplots_adjust(left=0.03, right=0.97, top=0.97, bottom=0.03)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100)
    plt.close(fig)


def _render_payload_view(
    converter: JsonToFusionConverter,
    client: Fusion360Client,
    payload: list[dict[str, Any]],
    *,
    output_path: Path,
    image_size: int,
    view_orientation: str,
    context: str,
) -> str | None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    step_path = output_path.with_suffix(".step")
    commands = converter.build_commands(payload, step_path)
    if commands and commands[-1].method == "export_step":
        commands = commands[:-1]
    converter.execute_commands(commands, context=context)
    client.export_view(
        filepath=str(output_path),
        w=int(image_size),
        h=int(image_size),
        view_orientation=view_orientation,
    )
    try:
        client.export_step(filepath=str(step_path))
    except Exception as exc:
        return str(exc)
    return None


def render_payload_or_placeholder(
    converter: JsonToFusionConverter,
    client: Fusion360Client,
    payload: list[dict[str, Any]],
    *,
    output_path: Path,
    image_size: int,
    view_orientation: str,
    context: str,
    title: str,
) -> None:
    try:
        step_error = _render_payload_view(
            converter,
            client,
            payload,
            output_path=output_path,
            image_size=image_size,
            view_orientation=view_orientation,
            context=context,
        )
        if step_error:
            print(
                f"[warn] STEP export failed for {output_path.with_suffix('.step')}: "
                f"{step_error}"
            )
    except Exception as exc:
        _write_placeholder_image(
            output_path,
            title=title,
            body=str(exc),
            size=image_size,
        )


def _payload_render_artifacts_exist(output_path: Path) -> bool:
    return output_path.exists() and output_path.with_suffix(".step").exists()


def export_current_view_or_placeholder(
    client: Fusion360Client,
    *,
    output_path: Path,
    image_size: int,
    view_orientation: str,
    title: str,
    body: str | None = None,
) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        client.export_view(
            filepath=str(output_path),
            w=int(image_size),
            h=int(image_size),
            view_orientation=view_orientation,
        )
    except Exception as exc:
        _write_placeholder_image(
            output_path,
            title=title,
            body=body or str(exc),
            size=image_size,
        )


def _fit_image(
    image: Image.Image, target_size: tuple[int, int], background: str = "white"
) -> Image.Image:
    target_width, target_height = target_size
    canvas = Image.new("RGB", (target_width, target_height), color=background)
    src_width, src_height = image.size
    scale = min(target_width / float(src_width), target_height / float(src_height))
    resized = image.resize(
        (max(1, int(src_width * scale)), max(1, int(src_height * scale))),
        Image.Resampling.LANCZOS,
    )
    offset_x = (target_width - resized.width) // 2
    offset_y = (target_height - resized.height) // 2
    canvas.paste(resized, (offset_x, offset_y))
    return canvas


def _draw_tag(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    text: str,
    fill: str,
    font: ImageFont.ImageFont,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.rounded_rectangle(
        (x, y, x + width + 24, y + height + 12), radius=10, fill=fill
    )
    draw.text((x + 12, y + 6), text, font=font, fill="white")


def _measure_tag_size(text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    scratch = Image.new("RGB", (10, 10), "white")
    scratch_draw = ImageDraw.Draw(scratch)
    bbox = scratch_draw.textbbox((0, 0), text, font=font)
    return (bbox[2] - bbox[0]) + 24, (bbox[3] - bbox[1]) + 12


def _draw_tile_block(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    tile_width: int,
    image_height: int,
    caption_height: int,
    image_path: Path,
    title_lines: list[str],
    subtitle_lines: list[str],
    title_font: ImageFont.ImageFont,
    subtitle_font: ImageFont.ImageFont,
    tag: tuple[str, str] | None = None,
) -> None:
    block_height = image_height + caption_height
    image = Image.open(image_path).convert("RGB")

    draw.rounded_rectangle(
        (x, y, x + tile_width, y + block_height),
        radius=18,
        outline=PAPER_EDGE,
        width=2,
        fill=PAPER_PANEL,
    )

    image_padding = 12
    fitted = _fit_image(
        image,
        (tile_width - image_padding * 2, image_height - image_padding * 2),
        background="white",
    )
    image_x = x + image_padding
    image_y = y + image_padding
    canvas.paste(fitted, (image_x, image_y))
    draw.rounded_rectangle(
        (
            image_x - 1,
            image_y - 1,
            image_x + fitted.width + 1,
            image_y + fitted.height + 1,
        ),
        radius=12,
        outline="#c6beb3",
        width=1,
    )

    if tag is not None:
        tag_text, tag_fill = tag
        tag_width, _ = _measure_tag_size(tag_text, subtitle_font)
        _draw_tag(
            draw,
            x=x + tile_width - image_padding - tag_width,
            y=y + image_padding + 10,
            text=tag_text,
            fill=tag_fill,
            font=subtitle_font,
        )

    text_x = x + 16
    text_y = y + image_height + 12
    text_y = _draw_wrapped_lines(
        draw,
        x=text_x,
        y=text_y,
        lines=title_lines,
        font=title_font,
        fill=PAPER_INK,
        line_gap=2,
    )
    _draw_wrapped_lines(
        draw,
        x=text_x,
        y=text_y + 4,
        lines=subtitle_lines,
        font=subtitle_font,
        fill=MUTED,
        line_gap=2,
    )


def compose_panel(
    sample: SampleComparison,
    *,
    intent_path: Path,
    original_model_path: Path,
    constrained_model_path: Path,
    closure_only_model_path: Path,
    output_path: Path,
    tile_size: int,
) -> None:
    del intent_path
    outer_margin = 28
    gutter = 24
    title_max_width = tile_size * 2 + gutter
    panel_width = outer_margin * 2 + tile_size * 2 + gutter

    measure_canvas = Image.new("RGB", (panel_width, 200), color=CANVAS_BG)
    measure_draw = ImageDraw.Draw(measure_canvas)
    sample_font = _load_font(22, preferred=("C:\\Windows\\Fonts\\arial.ttf",))
    title_font, title_lines = _fit_wrapped_text(
        measure_draw,
        _intent_summary(sample.manifest_entry),
        preferred_sizes=[28, 26, 24, 22],
        preferred_fonts=(
            "C:\\Windows\\Fonts\\timesbd.ttf",
            "C:\\Windows\\Fonts\\cambria.ttc",
        ),
        max_width=title_max_width,
        max_lines=2,
    )
    title_area_height = 34 + len(title_lines) * _line_height(title_font, extra=3) + 16
    source_image_height = max(250, int(tile_size * 0.74))
    source_caption_height = 60
    result_image_height = tile_size
    result_caption_height = 92
    panel_height = (
        outer_margin
        + title_area_height
        + source_image_height
        + source_caption_height
        + gutter
        + result_image_height
        + result_caption_height
        + outer_margin
    )

    canvas = Image.new("RGB", (panel_width, panel_height), color=CANVAS_BG)
    draw = ImageDraw.Draw(canvas)

    caption_font = _load_font(19, preferred=("C:\\Windows\\Fonts\\cambria.ttc",))
    small_font = _load_font(16, preferred=("C:\\Windows\\Fonts\\arial.ttf",))

    draw.text(
        (outer_margin, outer_margin - 2),
        f"Sample {sample.sample_id:03d}",
        fill=PAPER_SUBTLE,
        font=sample_font,
    )
    _draw_wrapped_lines(
        draw,
        x=outer_margin,
        y=outer_margin + 28,
        lines=title_lines,
        font=title_font,
        fill=PAPER_INK,
        line_gap=3,
    )

    base_y = outer_margin + title_area_height
    source_width = panel_width - outer_margin * 2
    caption_max_width = tile_size - 32

    _draw_tile_block(
        canvas,
        draw,
        x=outer_margin,
        y=base_y,
        tile_width=source_width,
        image_height=source_image_height,
        caption_height=source_caption_height,
        image_path=original_model_path,
        title_lines=["Reference model"],
        subtitle_lines=["before edit"],
        title_font=caption_font,
        subtitle_font=small_font,
        tag=None,
    )

    result_y = base_y + source_image_height + source_caption_height + gutter
    tiles = [
        (BRANCH_CONSTRAINED, constrained_model_path, outer_margin, result_y),
        (
            BRANCH_CLOSURE_ONLY,
            closure_only_model_path,
            outer_margin + tile_size + gutter,
            result_y,
        ),
    ]

    for key, image_path, x, y in tiles:
        title_text, subtitle_text = _tile_caption_lines(sample, key)
        title_lines = _fit_wrapped_text(
            draw,
            title_text,
            preferred_sizes=[19, 18, 17],
            preferred_fonts=("C:\\Windows\\Fonts\\cambria.ttc",),
            max_width=caption_max_width,
            max_lines=1,
        )[1]
        subtitle_lines = _fit_wrapped_text(
            draw,
            subtitle_text,
            preferred_sizes=[16, 15, 14],
            preferred_fonts=("C:\\Windows\\Fonts\\arial.ttf",),
            max_width=caption_max_width,
            max_lines=3,
        )[1]

        _draw_tile_block(
            canvas,
            draw,
            x=x,
            y=y,
            tile_width=tile_size,
            image_height=result_image_height,
            caption_height=result_caption_height,
            image_path=image_path,
            title_lines=title_lines,
            subtitle_lines=subtitle_lines,
            title_font=caption_font,
            subtitle_font=small_font,
            tag=None,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def compose_gallery(
    panel_paths: list[Path],
    *,
    output_path: Path,
    columns: int,
    gap: int = 32,
    margin: int = 32,
) -> None:
    if not panel_paths:
        return
    panels = [Image.open(path).convert("RGB") for path in panel_paths]
    panel_width = max(panel.width for panel in panels)
    panel_height = max(panel.height for panel in panels)
    columns = max(1, columns)
    rows = math.ceil(len(panels) / float(columns))
    gallery_width = int(columns * panel_width + (columns - 1) * gap + margin * 2)
    gallery_height = int(rows * panel_height + (rows - 1) * gap + margin * 2)
    gallery = Image.new("RGB", (gallery_width, gallery_height), color=CANVAS_BG)
    for index, panel in enumerate(panels):
        row = index // columns
        column = index % columns
        x = margin + column * (panel_width + gap)
        y = margin + row * (panel_height + gap)
        gallery.paste(panel, (x, y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gallery.save(output_path)


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_visualizations(
    *,
    result_dir: Path,
    output_dir: Path,
    selection_mode: str,
    max_samples: int,
    sample_ids: list[int] | None,
    host: str,
    port: int,
    image_size: int,
    view_orientation: str,
    gallery_columns: int,
    reuse_existing_renders: bool = False,
) -> dict[str, Any]:
    comparisons = load_sample_comparisons(result_dir)
    selected = select_samples(
        comparisons,
        mode=selection_mode,
        max_samples=max_samples,
        sample_ids=sample_ids,
    )
    converter = JsonToFusionConverter(host=host, port=port)
    client = Fusion360Client(f"http://{host}:{port}")
    panel_paths: list[Path] = []
    selection_manifest: list[dict[str, Any]] = []

    for sample in selected:
        sample_uid = _sample_uid_from_manifest_entry(sample.manifest_entry)
        sample_name = _sample_source_name_from_manifest_entry(sample.manifest_entry)
        sample_dir = _ensure_dir(
            output_dir / _visualization_sample_dir_name(sample.sample_id, sample_name)
        )
        intent_path = sample_dir / "edit_intent.png"
        source_model_path = sample_dir / "source_model.png"
        constrained_path = sample_dir / "constrained_model.png"
        closure_only_path = sample_dir / "closure_only_model.png"
        panel_path = sample_dir / "panel.png"

        need_intent = not (reuse_existing_renders and intent_path.exists())
        need_source = not (
            reuse_existing_renders
            and _payload_render_artifacts_exist(source_model_path)
        )
        need_constrained = not (
            reuse_existing_renders and _payload_render_artifacts_exist(constrained_path)
        )
        need_closure_only = not (
            reuse_existing_renders
            and _payload_render_artifacts_exist(closure_only_path)
        )

        source_payload: list[dict[str, Any]] | None = None
        candidate: LengthEditCandidate | None = None
        edited_target_value: float | None = None

        def _ensure_case_payloads() -> tuple[
            list[dict[str, Any]], LengthEditCandidate, float | None
        ]:
            nonlocal source_payload, candidate, edited_target_value
            if source_payload is None:
                source_payload = converter.load_json(sample.manifest_entry["json_path"])
            if candidate is None:
                candidate = _candidate_from_manifest(sample.manifest_entry)
            if edited_target_value is None:
                edited_target_value = _parse_float(
                    sample.manifest_entry.get(
                        "edited_target_value_numeric",
                        sample.manifest_entry.get("edited_length_mm"),
                    )
                )
            return source_payload, candidate, edited_target_value

        if need_intent:
            render_intent_card(
                sample.manifest_entry,
                output_path=intent_path,
                image_size=image_size,
            )
        if need_source:
            source_payload, _, _ = _ensure_case_payloads()
            render_payload_or_placeholder(
                converter,
                client,
                source_payload,
                output_path=source_model_path,
                image_size=image_size,
                view_orientation=view_orientation,
                context=f"viz_{sample_uid}_source",
                title="Original render failed",
            )
        if need_constrained:
            source_payload, candidate, edited_target_value = _ensure_case_payloads()
            constrained_payload = _build_constrained_payload(
                source_payload,
                candidate,
                edited_target_value,
                converter,
            )
            render_payload_or_placeholder(
                converter,
                client,
                constrained_payload,
                output_path=constrained_path,
                image_size=image_size,
                view_orientation=view_orientation,
                context=f"viz_{sample_uid}_constrained",
                title="Constraint-aware render failed",
            )
        if need_closure_only:
            source_payload, candidate, edited_target_value = _ensure_case_payloads()
            closure_only_payload = _build_closure_only_payload(
                source_payload,
                candidate,
                edited_target_value,
                converter,
            )
            render_payload_or_placeholder(
                converter,
                client,
                closure_only_payload,
                output_path=closure_only_path,
                image_size=image_size,
                view_orientation=view_orientation,
                context=f"viz_{sample_uid}_closure_only",
                title="Closure-only render failed",
            )
        compose_panel(
            sample,
            intent_path=intent_path,
            original_model_path=source_model_path,
            constrained_model_path=constrained_path,
            closure_only_model_path=closure_only_path,
            output_path=panel_path,
            tile_size=image_size,
        )
        panel_paths.append(panel_path)
        selection_manifest.append(
            {
                "sample_id": sample.sample_id,
                "sample_uid": sample_uid,
                "sample_name": sample_name,
                "json_path": sample.manifest_entry["json_path"],
                "panel_png": str(panel_path),
                "edit_intent_png": str(intent_path),
                "source_step": str(source_model_path.with_suffix(".step")),
                "constrained_step": str(constrained_path.with_suffix(".step")),
                "closure_only_step": str(closure_only_path.with_suffix(".step")),
                "constrained_case_id": sample.constrained.case_id,
                "closure_only_case_id": sample.closure_only.case_id,
                "constrained_success": sample.constrained.overall_editable_success,
                "closure_only_success": sample.closure_only.overall_editable_success,
                "constrained_failure_reason": sample.constrained.failure_reason,
                "closure_only_failure_reason": sample.closure_only.failure_reason,
                "intent_family": sample.manifest_entry.get("intent_family")
                or candidate.intent_family,
                "target_constraint_type": sample.manifest_entry.get(
                    "target_constraint_type"
                ),
                "intent_summary": _intent_summary(sample.manifest_entry),
            }
        )

    gallery_path = output_dir / f"gallery_{selection_mode}.png"
    compose_gallery(
        panel_paths,
        output_path=gallery_path,
        columns=gallery_columns,
    )
    selection_manifest_path = output_dir / "selected_samples.json"
    selection_manifest_path.write_text(
        json.dumps(selection_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "result_dir": str(result_dir),
        "output_dir": str(output_dir),
        "selection_mode": selection_mode,
        "selected_count": len(selected),
        "gallery_png": str(gallery_path),
        "selected_samples_json": str(selection_manifest_path),
        "sample_dirs": [
            str(
                output_dir
                / _visualization_sample_dir_name(
                    sample.sample_id,
                    _sample_source_name_from_manifest_entry(sample.manifest_entry),
                )
            )
            for sample in selected
        ],
    }


def _parse_sample_ids(raw_value: str | None) -> list[int] | None:
    if raw_value is None or not raw_value.strip():
        return None
    values: list[int] = []
    for part in raw_value.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        values.append(int(stripped))
    return values or None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render sample-level qualitative panels for the constraint editability experiment.",
    )
    parser.add_argument(
        "--result-dir",
        required=True,
        help="Experiment result directory containing summary.json/results.csv/manifest.json.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for rendered panels. Defaults to <result-dir>/visualization.",
    )
    parser.add_argument(
        "--selection",
        default="contrast",
        choices=["contrast", "constrained_failures", "all"],
        help="How to choose representative samples when --sample-ids is not provided.",
    )
    parser.add_argument(
        "--sample-ids", help="Comma-separated sample IDs. Overrides --selection."
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=6,
        help="Maximum number of samples to render.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Fusion 360 bridge host.")
    parser.add_argument(
        "--port", type=int, default=8080, help="Fusion 360 bridge port."
    )
    parser.add_argument(
        "--image-size", type=int, default=420, help="Tile image size in pixels."
    )
    parser.add_argument(
        "--view-orientation",
        default="iso",
        help="Fusion view orientation, e.g. iso/front/top.",
    )
    parser.add_argument(
        "--gallery-columns",
        type=int,
        default=2,
        help="Number of columns in the final gallery image.",
    )
    parser.add_argument(
        "--reuse-existing-renders",
        action="store_true",
        help="Reuse existing PNG renders in the output directory and only recompose panels/gallery.",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    result_dir = Path(args.result_dir).resolve()
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else (result_dir / "visualization").resolve()
    )
    summary = build_visualizations(
        result_dir=result_dir,
        output_dir=output_dir,
        selection_mode=str(args.selection),
        max_samples=int(args.max_samples),
        sample_ids=_parse_sample_ids(args.sample_ids),
        host=str(args.host),
        port=int(args.port),
        image_size=int(args.image_size),
        view_orientation=str(args.view_orientation),
        gallery_columns=int(args.gallery_columns),
        reuse_existing_renders=bool(args.reuse_existing_renders),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
