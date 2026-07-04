from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from .converter import JsonToFusionConverter

STEP_SUFFIXES = {".step", ".stp"}
F3D_SUFFIXES = {".f3d"}
EXPORT_SUFFIXES = {
    "step": STEP_SUFFIXES,
    "f3d": F3D_SUFFIXES,
}
EXPORT_DEFAULT_SUFFIX = {
    "step": ".step",
    "f3d": ".f3d",
}
EXPORT_METHODS = {
    "step": "export_step",
    "f3d": "export_f3d",
}


@dataclass(frozen=True)
class ExportOptions:
    host: str = "127.0.0.1"
    port: int = 8080
    skip_existing: bool = False
    normalize_degenerate_arcs: bool = True
    output_format: str = "step"


@dataclass(frozen=True)
class ExportItem:
    json_path: Path
    step_path: Path
    label: str

    @property
    def output_path(self) -> Path:
        return self.step_path


@dataclass(frozen=True)
class ExportResult:
    json_path: Path
    step_path: Path
    operation_count: int
    command_count: int
    normalized_arc_count: int = 0
    recoverable_skip_count: int = 0
    skipped_existing: bool = False

    @property
    def output_path(self) -> Path:
        return self.step_path


@dataclass(frozen=True)
class ExportFailure:
    json_path: Path
    step_path: Path
    error: str

    @property
    def output_path(self) -> Path:
        return self.step_path


@dataclass(frozen=True)
class BatchExportResult:
    items: list[ExportItem]
    successes: list[ExportResult] = field(default_factory=list)
    failures: list[ExportFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def processed_count(self) -> int:
        return len(self.successes) + len(self.failures)

    @property
    def skipped_existing_count(self) -> int:
        return sum(1 for item in self.successes if item.skipped_existing)


def _as_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _normalize_output_format(output_format: str) -> str:
    normalized = str(output_format).strip().lower()
    if normalized not in EXPORT_METHODS:
        raise ValueError(
            f"Unsupported output format: {output_format!r}. Expected one of: step, f3d"
        )
    return normalized


def _export_method(output_format: str) -> str:
    return EXPORT_METHODS[_normalize_output_format(output_format)]


def _default_suffix(output_format: str) -> str:
    return EXPORT_DEFAULT_SUFFIX[_normalize_output_format(output_format)]


def iter_json_files(input_path: Path) -> list[Path]:
    if input_path.is_dir():
        return sorted(path for path in input_path.rglob("*.json") if path.is_file())
    return [input_path]


def _looks_like_output_file(path: Path, output_format: str) -> bool:
    return (
        path.suffix.lower() in EXPORT_SUFFIXES[_normalize_output_format(output_format)]
    )


def _single_file_output_path(
    input_path: Path, output_path: Path, output_format: str
) -> Path:
    if _looks_like_output_file(output_path, output_format):
        return output_path
    return output_path / f"{input_path.stem}{_default_suffix(output_format)}"


def resolve_export_items(
    input_path: str | Path,
    output_path: str | Path,
    *,
    output_format: str = "step",
) -> list[ExportItem]:
    """Map one JSON file or one JSON folder to explicit exported outputs."""
    output_format = _normalize_output_format(output_format)
    source = _as_path(input_path)
    target = _as_path(output_path)

    if not source.exists():
        raise FileNotFoundError(f"Input path does not exist: {source}")

    if source.is_file():
        if source.suffix.lower() != ".json":
            raise ValueError(f"Input file must have a .json suffix: {source}")
        output_file = _single_file_output_path(source, target, output_format)
        return [ExportItem(json_path=source, step_path=output_file, label=source.name)]

    if _looks_like_output_file(target, output_format):
        suffix = _default_suffix(output_format)
        raise ValueError(
            f"Directory input requires an output directory, not a {suffix} file."
        )

    files = iter_json_files(source)
    if not files:
        raise ValueError(f"No .json files found under: {source}")

    items: list[ExportItem] = []
    for json_path in files:
        relative_path = json_path.relative_to(source)
        output_file = target / relative_path.with_suffix(_default_suffix(output_format))
        items.append(
            ExportItem(
                json_path=json_path,
                step_path=output_file,
                label=relative_path.as_posix(),
            )
        )
    return items


def resolve_f3d_export_items(
    input_path: str | Path, output_path: str | Path
) -> list[ExportItem]:
    return resolve_export_items(input_path, output_path, output_format="f3d")


def _load_and_build_commands(
    converter: JsonToFusionConverter,
    item: ExportItem,
    options: ExportOptions,
):
    payload = converter.load_json(item.json_path)
    if not isinstance(payload, list):
        raise ValueError(
            f"Expected a JSON list of modeling operations: {item.json_path}"
        )

    normalized_arc_count = 0
    if options.normalize_degenerate_arcs:
        payload, records = converter.normalize_degenerate_three_point_arcs(payload)
        normalized_arc_count = len(records)

    commands = converter.build_export_commands(
        payload,
        item.output_path,
        export_method=_export_method(options.output_format),
        export_label=_export_method(options.output_format),
    )
    return payload, commands, normalized_arc_count


def _with_output_format(
    options: ExportOptions | None, output_format: str
) -> ExportOptions:
    output_format = _normalize_output_format(output_format)
    if options is None:
        return ExportOptions(output_format=output_format)
    return replace(options, output_format=output_format)


def export_json_to_step(
    item: ExportItem,
    *,
    converter: JsonToFusionConverter | None = None,
    options: ExportOptions | None = None,
) -> ExportResult:
    """Export one HistCAD operation JSON file to one STEP or F3D file."""
    options = options or ExportOptions()
    options = replace(
        options, output_format=_normalize_output_format(options.output_format)
    )
    own_converter = converter is None
    converter = converter or JsonToFusionConverter(host=options.host, port=options.port)

    if options.skip_existing and item.output_path.exists():
        return ExportResult(
            json_path=item.json_path,
            step_path=item.output_path,
            operation_count=0,
            command_count=0,
            skipped_existing=True,
        )

    try:
        payload, commands, normalized_arc_count = _load_and_build_commands(
            converter, item, options
        )
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        execution = converter.execute_commands(commands, context=item.label)
        recoverable_skip_count = len(list(execution.get("skipped_constraints", [])))
        return ExportResult(
            json_path=item.json_path,
            step_path=item.output_path,
            operation_count=len(payload),
            command_count=len(commands),
            normalized_arc_count=normalized_arc_count,
            recoverable_skip_count=recoverable_skip_count,
        )
    finally:
        if own_converter:
            converter.close_runtime_client()


def export_json_to_f3d(
    item: ExportItem,
    *,
    converter: JsonToFusionConverter | None = None,
    options: ExportOptions | None = None,
) -> ExportResult:
    """Export one HistCAD operation JSON file to one Fusion archive (.f3d) file."""
    return export_json_to_step(
        item,
        converter=converter,
        options=_with_output_format(options, "f3d"),
    )


def export_json_tree(
    input_path: str | Path,
    output_path: str | Path,
    *,
    options: ExportOptions | None = None,
    fail_fast: bool = False,
) -> BatchExportResult:
    """Export a JSON file/folder to STEP or F3D, preserving subfolders."""
    options = options or ExportOptions()
    options = replace(
        options, output_format=_normalize_output_format(options.output_format)
    )
    items = resolve_export_items(
        input_path, output_path, output_format=options.output_format
    )
    converter = JsonToFusionConverter(host=options.host, port=options.port)
    successes: list[ExportResult] = []
    failures: list[ExportFailure] = []

    try:
        for item in items:
            converter.close_runtime_client()
            try:
                successes.append(
                    export_json_to_step(item, converter=converter, options=options)
                )
            except Exception as exc:
                failure = ExportFailure(item.json_path, item.output_path, str(exc))
                failures.append(failure)
                if fail_fast:
                    break
    finally:
        converter.close_runtime_client()

    return BatchExportResult(items=items, successes=successes, failures=failures)


def export_json_tree_to_f3d(
    input_path: str | Path,
    output_path: str | Path,
    *,
    options: ExportOptions | None = None,
    fail_fast: bool = False,
) -> BatchExportResult:
    """Export a JSON file/folder to F3D, preserving subfolders."""
    return export_json_tree(
        input_path,
        output_path,
        options=_with_output_format(options, "f3d"),
        fail_fast=fail_fast,
    )


def format_item_status(
    result: ExportResult,
    *,
    output_format: str = "step",
) -> str:
    if result.skipped_existing:
        return f"skip existing -> {result.output_path}"
    action = _normalize_output_format(output_format)
    return (
        f"{action} -> {result.output_path} "
        f"({result.operation_count} ops, {result.command_count} commands, "
        f"{result.normalized_arc_count} normalized arcs, "
        f"{result.recoverable_skip_count} recoverable skips)"
    )
