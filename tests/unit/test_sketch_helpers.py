"""Tests for sketch constraint helper functions."""

from __future__ import annotations

import pytest

from freecad_mcp.tools.sketch_helpers import directed_axis_distance, ground_truth_xy


class TestSketchHelpers:
    """Tests for directed distance sign handling."""

    @pytest.fixture
    def step0_ground_truth(self) -> dict:
        return {
            "line_3": {"start": [10.0, -0.75], "end": [10.0, 0.75]},
            "line_4": {"start": [10.0, 0.75], "end": [3.8611, 1.0717]},
        }

    def test_ground_truth_xy_resolves_point_refs(
        self, step0_ground_truth: dict
    ) -> None:
        assert ground_truth_xy(step0_ground_truth, "line_4.start") == [10.0, 0.75]

    def test_directed_vertical_distance_original_sign(
        self, step0_ground_truth: dict
    ) -> None:
        val = directed_axis_distance(
            "line_4.start",
            "line_3.start",
            1,
            1.5,
            step0_ground_truth,
        )
        assert val == pytest.approx(-1.5)

    def test_directed_vertical_distance_preserves_sign_on_edit(
        self, step0_ground_truth: dict
    ) -> None:
        """Editing magnitude must not flip directed Distance sign."""
        val = directed_axis_distance(
            "line_4.start",
            "line_3.start",
            1,
            1.8,
            step0_ground_truth,
        )
        assert val == pytest.approx(-1.8)

    def test_directed_horizontal_distance_sign(self) -> None:
        ground_truth = {
            "line_2": {"start": [3.8628, -1.0716], "end": [10.0, -0.75]},
        }
        val = directed_axis_distance(
            "line_2.start",
            "line_2.end",
            0,
            6.1372,
            ground_truth,
        )
        assert val == pytest.approx(6.1372)
