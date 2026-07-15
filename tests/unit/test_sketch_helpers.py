"""Tests for sketch constraint helper functions."""

from __future__ import annotations

import pytest

from freecad_mcp.tools.sketch_helpers import (
    directed_axis_distance,
    distance_polarity_key,
    ground_truth_xy,
    polarity_from_signed,
)


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

    def test_persisted_polarity_overrides_ground_truth(
        self, step0_ground_truth: dict
    ) -> None:
        """Persisted polarity wins even if GT delta has the opposite sign."""
        val = directed_axis_distance(
            "line_4.start",
            "line_3.start",
            1,
            1.8,
            step0_ground_truth,
            polarity=1,
        )
        assert val == pytest.approx(1.8)

    def test_persisted_polarity_keeps_sign_on_magnitude_edit(
        self, step0_ground_truth: dict
    ) -> None:
        first = directed_axis_distance(
            "line_4.start",
            "line_3.start",
            1,
            1.5,
            step0_ground_truth,
        )
        polarity = polarity_from_signed(first)
        edited = directed_axis_distance(
            "line_4.start",
            "line_3.start",
            1,
            2.2,
            None,
            polarity=polarity,
        )
        assert polarity == -1
        assert edited == pytest.approx(-2.2)

    def test_live_fallback_when_ground_truth_near_zero(self) -> None:
        """Near-zero GT delta falls back to live endpoint sign."""
        ground_truth = {
            "line_1": {"start": [0.0, 0.0], "end": [0.0, 0.0]},
        }
        val = directed_axis_distance(
            "line_1.start",
            "line_1.end",
            1,
            3.5,
            ground_truth,
            live_a=[0.0, 1.0],
            live_b=[0.0, -2.0],
        )
        assert val == pytest.approx(-3.5)

    def test_unsigned_last_resort_without_topology(self) -> None:
        val = directed_axis_distance(
            "line_1.start",
            "line_1.end",
            0,
            4.0,
            None,
        )
        assert val == pytest.approx(4.0)

    def test_distance_polarity_key_normalizes_direction(self) -> None:
        assert (
            distance_polarity_key("a.start", "b.start", "vertical")
            == "Distance:a.start:b.start:VERTICAL"
        )

    def test_polarity_from_signed(self) -> None:
        assert polarity_from_signed(-1.5) == -1
        assert polarity_from_signed(1.5) == 1
        assert polarity_from_signed(0.0) == 1
