"""Refresh constraint editability summaries from experiment outputs.

The refreshed summary reports benchmark metrics used by the evaluator:

- PCSR: case-level preserved-relation pass rate
- ER: edit reachability rate
- cPCSR: conditional preserved-relation pass rate given reachability

The optional --summary-only mode recomputes summary.json directly from cached
results.csv / details.json.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from editability import experiment as core  # noqa: E402


def _rate_of_true(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if row.get(key) is True) / float(len(rows))


def _first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        return value
    return None


def _sum_optional_int(rows: list[dict[str, Any]], *keys: str) -> int:
    total = 0
    for row in rows:
        value = _first_present(row, tuple(keys))
        if value is None:
            continue
        total += int(value)
    return total


def _mean_optional_float(rows: list[dict[str, Any]], key: str) -> float | None:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if value is None or value == "":
            continue
        values.append(float(value))
    return statistics.fmean(values) if values else None


def _is_edit_reachable(row: dict[str, Any]) -> bool:
    return (
        row.get("target_hit") is True
        and row.get("validation_ok") is True
        and row.get("rebuild_success") is True
    )


def _dedupe_records_by_case_id(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    ordered_fallback_index = 0
    for record in records:
        case_id = str(record.get("case_id") or f"__fallback_{ordered_fallback_index}")
        deduped[case_id] = record
        ordered_fallback_index += 1
    return list(deduped.values())


def _load_details(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return _dedupe_records_by_case_id(
        [item for item in payload if isinstance(item, dict)]
    )


def _summarize_result_rows_v2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = dict(core._summarize_result_rows(rows))
    reachable_rows = [row for row in rows if _is_edit_reachable(row)]
    reachable_supported = _sum_optional_int(
        reachable_rows, "preserved_supported_constraints"
    )
    reachable_satisfied = _sum_optional_int(
        reachable_rows, "preserved_satisfied_constraints"
    )
    all_case_preserved_total = _sum_optional_int(
        rows,
        "preserved_constraint_count",
        "preserved_total_constraints",
    )
    all_case_preserved_satisfied = _sum_optional_int(
        rows, "preserved_satisfied_constraints"
    )
    summary.update(
        {
            "edit_reachability_rate": (
                float(len(reachable_rows)) / float(len(rows)) if rows else None
            ),
            "reachable_case_count": len(reachable_rows),
            "conditional_preserved_constraints_all_satisfied_rate": _rate_of_true(
                reachable_rows,
                "preserved_constraints_all_satisfied",
            ),
            "conditional_mean_preserved_constraint_satisfaction_rate": _mean_optional_float(
                reachable_rows,
                "preserved_constraint_satisfaction_rate",
            ),
            "conditional_micro_preserved_constraint_satisfaction_rate": (
                float(reachable_satisfied) / float(reachable_supported)
                if reachable_supported > 0
                else None
            ),
            "all_case_preserved_constraint_count_total": all_case_preserved_total,
            "all_case_preserved_constraint_recall": (
                float(all_case_preserved_satisfied) / float(all_case_preserved_total)
                if all_case_preserved_total > 0
                else None
            ),
        }
    )
    return summary


def _grouped_row_summaries_v2(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = sorted(
        {str(row.get(key) or "") for row in rows if str(row.get(key) or "").strip()}
    )
    return {
        value: _summarize_result_rows_v2(
            [row for row in rows if str(row.get(key) or "") == value]
        )
        for value in values
    }


def _benchmark_metrics_from_branch_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "THR": summary.get("target_hit_rate"),
        "PCSR": summary.get("preserved_constraints_all_satisfied_rate"),
        "PCSR_soft": summary.get("mean_preserved_constraint_satisfaction_rate"),
        "PCSR_micro": summary.get("micro_preserved_constraint_satisfaction_rate"),
        "RSR": summary.get("rebuild_success_rate"),
        "OES": summary.get("overall_editable_success_rate"),
        "ER": summary.get("edit_reachability_rate"),
        "cPCSR": summary.get("conditional_preserved_constraints_all_satisfied_rate"),
        "cPCSR_soft": summary.get(
            "conditional_mean_preserved_constraint_satisfaction_rate"
        ),
        "all_case_preserved_recall": summary.get(
            "all_case_preserved_constraint_recall"
        ),
        "count": summary.get("count", 0),
    }


def _compatibility_metrics_from_branch_summary_v1(
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "THR": summary.get("target_hit_rate"),
        "PCSR": summary.get("mean_preserved_constraint_satisfaction_rate"),
        "PCSR_micro": summary.get("micro_preserved_constraint_satisfaction_rate"),
        "RSR": summary.get("rebuild_success_rate"),
        "OES": summary.get("overall_editable_success_rate"),
        "count": summary.get("count", 0),
    }


def _attach_benchmark_metrics_recursive(summary: dict[str, Any]) -> dict[str, Any]:
    summary["benchmark_metrics"] = _benchmark_metrics_from_branch_summary(summary)
    summary["compatibility_metrics_v1"] = _compatibility_metrics_from_branch_summary_v1(
        summary
    )
    for grouped_key in (
        "by_intent_family",
        "by_constraint_type",
        "by_source_subset",
        "by_source_domain",
    ):
        grouped = summary.get(grouped_key)
        if not isinstance(grouped, dict):
            continue
        for item in grouped.values():
            if isinstance(item, dict):
                _attach_benchmark_metrics_recursive(item)
    return summary


def _summarize_branch_results_v2(
    rows: list[dict[str, Any]],
    branch: str,
    *,
    detailed_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    branch_rows = [row for row in rows if row.get("branch") == branch]
    summary = _summarize_result_rows_v2(branch_rows)
    summary["by_intent_family"] = _grouped_row_summaries_v2(
        branch_rows, "intent_family"
    )
    summary["by_constraint_type"] = _grouped_row_summaries_v2(
        branch_rows,
        "target_constraint_type",
    )
    summary["by_source_subset"] = _grouped_row_summaries_v2(
        branch_rows, "source_subset"
    )
    summary["by_source_domain"] = _grouped_row_summaries_v2(
        branch_rows, "source_domain"
    )
    if detailed_cases is not None:
        summary["by_preserved_constraint_type"] = (
            core._summarize_preserved_constraint_type_breakdown(
                detailed_cases,
                branch,
            )
        )
    return _attach_benchmark_metrics_recursive(summary)


def _load_existing_summary(output_dir: Path) -> dict[str, Any]:
    summary_path = output_dir / "summary.json"
    checkpoint_path = output_dir / core.CHECKPOINT_FILENAME
    if summary_path.is_file():
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    if checkpoint_path.is_file():
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("summary"), dict):
            return dict(payload["summary"])
    return {}


def _update_completed_checkpoint(output_dir: Path, summary: dict[str, Any]) -> None:
    checkpoint_path = output_dir / core.CHECKPOINT_FILENAME
    if not checkpoint_path.is_file():
        return
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return
    payload["summary"] = summary
    core._write_json_atomically(checkpoint_path, payload)


def refresh_summary_from_cache(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    csv_path = output_dir / "results.csv"
    details_path = output_dir / "details.json"
    if not csv_path.is_file():
        raise FileNotFoundError(f"Missing cached results file: {csv_path}")

    rows = _dedupe_records_by_case_id(core._load_results_csv(csv_path))
    details = _load_details(details_path)
    constrained_summary = _summarize_branch_results_v2(
        rows,
        core.BRANCH_CONSTRAINED,
        detailed_cases=details,
    )
    closure_only_summary = _summarize_branch_results_v2(
        rows,
        core.BRANCH_CLOSURE_ONLY,
        detailed_cases=details,
    )

    summary = _load_existing_summary(output_dir)
    summary["constrained"] = constrained_summary
    summary["closure_only"] = closure_only_summary
    summary["metric_definition_version"] = 2
    summary["metric_definition_notes"] = {
        "PCSR": "case-level preserved-relation pass rate",
        "PCSR_soft": "mean per-case preserved-relation satisfaction rate",
        "ER": "target_hit AND validation_ok AND rebuild_success",
        "cPCSR": "case-level preserved-relation pass rate conditioned on ER",
        "identity": "OES = ER x cPCSR",
    }
    summary["benchmark_metrics_by_branch"] = {
        core.BRANCH_CONSTRAINED: constrained_summary.get("benchmark_metrics") or {},
        core.BRANCH_CLOSURE_ONLY: closure_only_summary.get("benchmark_metrics") or {},
    }
    summary["compatibility_metrics_v1_by_branch"] = {
        core.BRANCH_CONSTRAINED: constrained_summary.get("compatibility_metrics_v1")
        or {},
        core.BRANCH_CLOSURE_ONLY: closure_only_summary.get("compatibility_metrics_v1")
        or {},
    }

    summary_path = output_dir / "summary.json"
    core._write_json_atomically(summary_path, summary)
    _update_completed_checkpoint(output_dir, summary)
    return summary


def build_arg_parser():
    parser = core.build_arg_parser()
    parser.description = (
        "Refresh constraint editability summaries from cached benchmark outputs. "
        "The refreshed summary includes ER, cPCSR, and OES diagnostics."
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help=("Refresh summary.json directly from cached results.csv/details.json."),
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    if args.summary_only:
        summary = refresh_summary_from_cache(output_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    summary = core.run_experiment(
        dataset_root=Path(args.dataset_root).resolve(),
        output_dir=output_dir,
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
        visualization_sample_ids=core._parse_visualization_sample_ids(
            args.visualization_sample_ids
        ),
        visualization_image_size=int(args.visualization_image_size),
        visualization_view_orientation=str(args.visualization_view_orientation),
        visualization_gallery_columns=int(args.visualization_gallery_columns),
    )
    del summary
    refreshed_summary = refresh_summary_from_cache(output_dir)
    print(json.dumps(refreshed_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
