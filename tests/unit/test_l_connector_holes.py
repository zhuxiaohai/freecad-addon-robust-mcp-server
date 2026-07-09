"""Tests for L-connector hole arrays, face catalog, and plan compilation."""

from __future__ import annotations

import pytest

from freecad_mcp.intent.registry import resolve_intent_to_plan
from freecad_mcp.intent.schema import (
    HoleArraySpec,
    IntentSpec,
    validate_hole_array_spec,
)
from freecad_mcp.tools.templates.connectors import build_l_connector_plan
from freecad_mcp.tools.templates.face_catalog import (
    L_CONNECTOR_EXTERIOR_FACE_IDS,
    all_l_connector_face_frames,
    resolve_l_connector_face_frame,
)
from freecad_mcp.tools.templates.hole_arrays import (
    compile_hole_groups,
    validate_hole_array_on_face,
)


@pytest.fixture
def base_slots() -> dict[str, float]:
    """Standard L-connector dimensions for tests (mm)."""
    return {
        "arm_x_length": 80.0,
        "arm_y_length": 100.0,
        "arm_x_width": 20.0,
        "arm_y_width": 20.0,
        "thickness": 10.0,
    }


class TestFaceCatalog:
    """Tests for semantic face frames."""

    def test_nine_exterior_faces(self) -> None:
        """Catalog defines nine exterior face IDs."""
        assert len(L_CONNECTOR_EXTERIOR_FACE_IDS) == 9

    def test_all_frames_resolve(self, base_slots: dict[str, float]) -> None:
        """Every face_id produces a frame with positive extents."""
        frames = all_l_connector_face_frames(base_slots)
        assert len(frames) == 9
        for frame in frames.values():
            assert frame.extent_u > 0
            assert frame.extent_v > 0
            assert len(frame.near_point) == 3

    def test_arm_x_top_near_point(self, base_slots: dict[str, float]) -> None:
        """arm_x_top near_point sits on the horizontal arm top."""
        frame = resolve_l_connector_face_frame("arm_x_top", base_slots)
        assert frame.near_point == [50.0, 10.0, 10.0]

    def test_top_face_holes_use_origin_uv_margins(
        self, base_slots: dict[str, float]
    ) -> None:
        """Top-face margins are measured from the UV origin; all UV stay positive."""
        from freecad_mcp.tools.templates.hole_arrays import _hole_centres_uv

        x_frame = resolve_l_connector_face_frame("arm_x_top", base_slots)
        y_frame = resolve_l_connector_face_frame("arm_y_top", base_slots)
        x_spec = HoleArraySpec(
            face_id="arm_x_top",
            count_u=2,
            count_v=2,
            pitch_u=20.0,
            pitch_v=8.0,
            margin_u=15.0,
            margin_v=5.0,
        )
        y_spec = HoleArraySpec(
            face_id="arm_y_top",
            count_u=2,
            count_v=2,
            pitch_u=10.0,
            pitch_v=20.0,
            margin_u=5.0,
            margin_v=15.0,
        )
        x_uv = _hole_centres_uv(x_spec, x_frame)
        y_uv = _hole_centres_uv(y_spec, y_frame)
        assert x_uv == [(15.0, 5.0), (35.0, 5.0), (15.0, 13.0), (35.0, 13.0)]
        assert y_uv == [(5.0, 15.0), (15.0, 15.0), (5.0, 35.0), (15.0, 35.0)]
        assert all(u >= 0 and v >= 0 for u, v in x_uv)
        assert all(u >= 0 and v >= 0 for u, v in y_uv)
        x_world = [x_frame.uv_to_world(u, v) for u, v in x_uv]
        y_world = [y_frame.uv_to_world(u, v) for u, v in y_uv]
        # Sketch-local u,v convert to L-global via face-catalog origin.
        assert x_world == [
            [35.0, 5.0, 10.0],
            [55.0, 5.0, 10.0],
            [35.0, 13.0, 10.0],
            [55.0, 13.0, 10.0],
        ]
        assert y_world == [
            [5.0, 35.0, 10.0],
            [15.0, 35.0, 10.0],
            [5.0, 55.0, 10.0],
            [15.0, 55.0, 10.0],
        ]

    def test_unknown_face_raises(self, base_slots: dict[str, float]) -> None:
        """Unknown face_id raises ValueError."""
        with pytest.raises(ValueError, match="Unknown L-connector face_id"):
            resolve_l_connector_face_frame("not_a_face", base_slots)


class TestHoleArrayValidation:
    """Tests for hole array bounds checking."""

    def test_single_hole_fits(self, base_slots: dict[str, float]) -> None:
        """A single centred hole passes validation."""
        spec = HoleArraySpec(
            face_id="arm_x_top",
            count_u=1,
            count_v=1,
            diameter=6.0,
            margin_u=20.0,
            margin_v=5.0,
        )
        frame = resolve_l_connector_face_frame("arm_x_top", base_slots)
        validate_hole_array_on_face(spec, frame)

    def test_hole_outside_extent_raises(self, base_slots: dict[str, float]) -> None:
        """Hole centre outside face extent raises ValueError."""
        spec = HoleArraySpec(
            face_id="arm_x_end",
            count_u=1,
            count_v=1,
            diameter=4.0,
            margin_u=100.0,
            margin_v=5.0,
        )
        frame = resolve_l_connector_face_frame("arm_x_end", base_slots)
        with pytest.raises(ValueError, match="exceeds face extent"):
            validate_hole_array_on_face(spec, frame)

    def test_diameter_list_length_mismatch_raises(self) -> None:
        """Diameter list must match hole count."""
        spec = HoleArraySpec(
            face_id="arm_x_top",
            count_u=2,
            count_v=2,
            diameter=[5.0, 5.0],
        )
        with pytest.raises(ValueError, match="diameter list length"):
            validate_hole_array_spec(spec)


class TestHolePlanCompilation:
    """Tests for FabricationPlan hole feature chains."""

    def test_single_face_adds_sketch_extrude_boolean(
        self, base_slots: dict[str, float]
    ) -> None:
        """One hole group adds sketch, tool extrude, and boolean cut."""
        groups = [
            HoleArraySpec(
                face_id="arm_x_top",
                count_u=2,
                count_v=2,
                pitch_u=20.0,
                pitch_v=8.0,
                diameter=5.0,
                margin_u=15.0,
                margin_v=5.0,
            )
        ]
        plan = build_l_connector_plan(slots=base_slots, hole_groups=groups)

        assert len(plan.sketches) == 2  # profile + holes
        assert len(plan.features) == 3  # extrude + tool + cut
        assert plan.metadata["hole_face_ids"] == ["arm_x_top"]
        assert plan.metadata["hole_group_count"] == 1

        hole_sketch = plan.sketches[1]
        assert hole_sketch.sketch_name == "Holes_arm_x_top_0"
        assert hole_sketch.attachment_support is None
        assert hole_sketch.coordinate_system is not None
        assert hole_sketch.coordinate_system.translation == [20.0, 0.0, 10.0]
        assert hole_sketch.coordinate_system.attachment_support == {
            "feature_name": "L_Connector_Solid",
        }
        assert hole_sketch.coordinate_system.offset_expressions == {
            "AttachmentOffset.Base.x": "FabricationParams.arm_y_width",
            "AttachmentOffset.Base.y": "0 mm",
            "AttachmentOffset.Base.z": "FabricationParams.thickness",
        }
        assert hole_sketch.attach_after_feature == "L_Connector_Solid"
        assert "circle_1" in hole_sketch.sketch
        assert len(hole_sketch.sketch) == 4
        assert "hole_arm_x_top_diameter" in plan.metadata["editable_aliases"]
        assert "hole_arm_x_top_margin_u" in plan.metadata["editable_aliases"]

        tool_feat = plan.features[1]
        cut_feat = plan.features[2]
        assert tool_feat.type == "extrude"
        assert tool_feat.feature_name == "HoleTool_arm_x_top_0"
        assert cut_feat.type == "boolean"
        assert cut_feat.params["base_object_name"] == "L_Connector_Solid"
        assert cut_feat.params["tool_object_name"] == "HoleTool_arm_x_top_0"

    def test_multiple_faces_chain_boolean_base(
        self, base_slots: dict[str, float]
    ) -> None:
        """Second face cuts from result of first face."""
        groups = [
            HoleArraySpec(
                face_id="arm_x_top", diameter=5.0, margin_u=30.0, margin_v=8.0
            ),
            HoleArraySpec(
                face_id="arm_y_top", diameter=5.0, margin_u=15.0, margin_v=30.0
            ),
        ]
        plan = build_l_connector_plan(slots=base_slots, hole_groups=groups)

        cuts = [f for f in plan.features if f.type == "boolean"]
        assert len(cuts) == 2
        assert cuts[0].params["base_object_name"] == "L_Connector_Solid"
        assert cuts[1].params["base_object_name"] == "L_After_Holes_arm_x_top_0"

    def test_mixed_diameters_per_face(self, base_slots: dict[str, float]) -> None:
        """Per-hole diameter list creates separate radius aliases."""
        groups = [
            HoleArraySpec(
                face_id="arm_x_top",
                count_u=2,
                count_v=1,
                pitch_u=25.0,
                diameter=[5.0, 7.0],
                margin_u=20.0,
                margin_v=8.0,
            )
        ]
        plan = build_l_connector_plan(slots=base_slots, hole_groups=groups)
        aliases = plan.metadata["editable_aliases"]
        assert "hole_arm_x_top_r_0" in aliases
        assert "hole_arm_x_top_r_1" in aliases

    def test_nine_face_plan_structure(self, base_slots: dict[str, float]) -> None:
        """All nine exterior faces produce nine boolean cuts."""
        groups = [
            HoleArraySpec(
                face_id=face_id,
                count_u=1,
                count_v=1,
                diameter=4.0,
                margin_u=10.0,
                margin_v=5.0,
            )
            for face_id in L_CONNECTOR_EXTERIOR_FACE_IDS
        ]
        # Adjust margins for small faces
        for g in groups:
            if g.face_id in ("arm_x_end", "arm_y_end"):
                g.margin_u = 5.0
                g.margin_v = 4.0
            if g.face_id.startswith("inner_corner"):
                g.margin_u = 15.0
                g.margin_v = 4.0

        plan = build_l_connector_plan(slots=base_slots, hole_groups=groups)
        assert plan.metadata["hole_group_count"] == 9
        assert len([f for f in plan.features if f.type == "boolean"]) == 9


class TestIntentWithHoles:
    """End-to-end IntentSpec → plan tests."""

    def test_resolve_intent_with_hole_groups(self) -> None:
        """resolve_intent_to_plan compiles hole_groups from IntentSpec."""
        intent = IntentSpec(
            template_name="l_connector",
            slots={
                "arm_x_length": 80.0,
                "arm_y_length": 100.0,
                "width": 20.0,
                "thickness": 10.0,
            },
            slot_bindings=["arm_x_width = width", "arm_y_width = width"],
            hole_groups=[
                HoleArraySpec(
                    face_id="arm_x_top",
                    count_u=2,
                    count_v=2,
                    pitch_u=20.0,
                    pitch_v=8.0,
                    diameter=5.0,
                    margin_u=15.0,
                    margin_v=5.0,
                )
            ],
        )
        plan = resolve_intent_to_plan(intent)
        assert plan.metadata["hole_group_count"] == 1
        assert "hole_groups" in plan.metadata["intent_spec"]

    def test_unknown_face_in_intent_raises(self) -> None:
        """Invalid face_id in hole_groups raises during validation."""
        intent = IntentSpec(
            template_name="l_connector",
            slots={
                "arm_x_length": 80.0,
                "arm_y_length": 100.0,
                "arm_x_width": 20.0,
                "arm_y_width": 20.0,
            },
            hole_groups=[HoleArraySpec(face_id="invalid_face")],
        )
        with pytest.raises(ValueError, match="Unknown face_id"):
            resolve_intent_to_plan(intent)


class TestCompileHoleGroups:
    """Direct compile_hole_groups helper tests."""

    def test_returns_plane_attached_sketches(
        self, base_slots: dict[str, float]
    ) -> None:
        """Each hole group adds a deferred sketch on the face UV plane."""
        groups = [HoleArraySpec(face_id="bottom", margin_u=30.0, margin_v=40.0)]
        sketches, features, _face_ids, aliases = compile_hole_groups(
            groups, slots=base_slots
        )
        assert len(sketches) == 1
        assert sketches[0].attachment_support is None
        assert sketches[0].coordinate_system is not None
        assert sketches[0].attach_after_feature == "L_Connector_Solid"
        assert "hole_bottom_diameter" in aliases
        assert len(features) == 2
