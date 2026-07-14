"""Tests for sketch editability evaluation helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freecad_mcp.tools.sketch_editability import (
    evaluate_sketch_editability,
    finalize_histcad_editability_metrics,
    normalize_constraint_records,
    replace_constraint_value,
)
from freecad_mcp.tools.sketch_helpers import directed_axis_distance

HISTCAD_STEP0 = (
    Path(__file__).resolve().parents[2] / "HistCAD-68C2" / "examples" / "01000571.json"
)


@pytest.fixture
def step0_op0() -> dict:
    payload = json.loads(HISTCAD_STEP0.read_text(encoding="utf-8"))
    return payload[0]


class TestSketchEditability:
    """HistCAD-style geometric editability checks."""

    def test_replace_constraint_value_updates_distance_dict(
        self, step0_op0: dict
    ) -> None:
        constraints = step0_op0["constraints"]
        edited = replace_constraint_value(
            constraints,
            constraint_type="Distance",
            entry_index=0,
            edited_value_mm=1.8,
        )
        entry = edited["Distance"][0]
        assert entry[2]["length"] == pytest.approx(1.8)

    def test_original_geometry_passes_all_constraints(self, step0_op0: dict) -> None:
        metrics = evaluate_sketch_editability(
            live_sketch=step0_op0["sketch"],
            reference_constraints=step0_op0["constraints"],
            constraint_type="Distance",
            entry_index=0,
            edited_value_mm=1.5,
        )
        assert metrics["target_hit"] is True
        assert metrics["OES"] is True
        assert metrics["cPCSR"] == 1.0

    def test_diameter_edit_candidate_on_original_sketch(self, step0_op0: dict) -> None:
        records = normalize_constraint_records(step0_op0["constraints"])
        diameter = next(r for r in records if r.constraint_type == "Diameter")
        metrics = evaluate_sketch_editability(
            live_sketch=step0_op0["sketch"],
            reference_constraints=step0_op0["constraints"],
            constraint_type="Diameter",
            entry_index=diameter.entry_index,
            edited_value_mm=6.0,
            entities=diameter.entities,
        )
        assert metrics["target_hit"] is False
        assert metrics["cPCSR"] == 1.0

    def test_directed_distance_sign_for_vertical_edit(self, step0_op0: dict) -> None:
        sketch = step0_op0["sketch"]
        val = directed_axis_distance(
            "line_4.start",
            "line_3.start",
            1,
            1.8,
            sketch,
        )
        assert val == pytest.approx(-1.8)


class TestFinalizeHistcadMetrics:
    """HistCAD v2 ER/OES finalization."""

    def test_histcad_v2_oes_is_product(self) -> None:
        base = {
            "target_hit": True,
            "cPCSR": 0.8,
            "preserved_constraints_all_satisfied": False,
        }
        metrics = finalize_histcad_editability_metrics(
            base,
            rebuild_success=True,
            validation_ok=True,
        )
        assert metrics["ER"] == 1.0
        assert metrics["OES"] == pytest.approx(0.8)
        assert metrics["metric_definition"] == "histcad_v2"

    def test_histcad_v2_er_requires_validation(self) -> None:
        base = {"target_hit": True, "cPCSR": 1.0}
        metrics = finalize_histcad_editability_metrics(
            base,
            rebuild_success=True,
            validation_ok=False,
        )
        assert metrics["ER"] == 0.0
        assert metrics["OES"] == 0.0
