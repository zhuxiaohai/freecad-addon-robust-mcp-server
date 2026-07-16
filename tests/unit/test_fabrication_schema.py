"""Tests for fabrication schema (FabricationPlan dataclass) serialisation."""

import pytest

from freecad_mcp.tools.fabrication_schema import (
    CoordinateSystemSpec,
    FabricationPlan,
    FeatureSpec,
    FinishSpec,
    SketchSpec,
    describe_fabrication_plan_schema,
    minimal_fabrication_plan_example,
    validate_fabrication_plan,
)


class TestCoordinateSystemSpec:
    """Tests for CoordinateSystemSpec serialisation / deserialisation."""

    def test_round_trip_no_name(self) -> None:
        """to_dict / from_dict round-trip for a nameless CS."""
        cs = CoordinateSystemSpec(
            euler_angles=[0.0, 0.0, 90.0], translation=[10.0, 0.0, 0.0]
        )
        d = cs.to_dict()
        cs2 = CoordinateSystemSpec.from_dict(d)
        assert cs2.euler_angles == [0.0, 0.0, 90.0]
        assert cs2.translation == [10.0, 0.0, 0.0]
        assert cs2.name is None

    def test_round_trip_with_name(self) -> None:
        """to_dict / from_dict preserves the name field."""
        cs = CoordinateSystemSpec([0, 0, 0], [0, 0, 50], name="TopPlane")
        cs2 = CoordinateSystemSpec.from_dict(cs.to_dict())
        assert cs2.name == "TopPlane"

    def test_to_dict_structure(self) -> None:
        """to_dict produces the expected keys."""
        cs = CoordinateSystemSpec([1, 2, 3], [4, 5, 6], name="A")
        d = cs.to_dict()
        assert set(d.keys()) == {"euler_angles", "translation", "name"}

    def test_attached_parametric_coordinate_system_round_trip(self) -> None:
        """The public attached-coordinate payload survives serialisation."""
        cs = CoordinateSystemSpec(
            [0, 0, 0],
            [20, 0, 10],
            name="CS_arm_x_top",
            attachment_support={"target": "L_Connector_Solid"},
            param_aliases={
                "translation.x": "arm_y_width",
                "translation.z": "thickness",
            },
        )
        cs2 = CoordinateSystemSpec.from_dict(cs.to_dict())
        assert cs2.param_aliases == cs.param_aliases
        assert cs2.attachment_support == {"target": "L_Connector_Solid"}

    def test_from_dict_missing_name_defaults_none(self) -> None:
        """from_dict tolerates a missing 'name' key."""
        d = {"euler_angles": [0, 0, 0], "translation": [0, 0, 0]}
        cs = CoordinateSystemSpec.from_dict(d)
        assert cs.name is None


class TestSketchSpec:
    """Tests for SketchSpec serialisation / deserialisation."""

    @pytest.fixture
    def minimal_sketch(self) -> SketchSpec:
        """Minimal valid SketchSpec references a named coordinate system."""
        return SketchSpec(
            sketch={"line_1": {"start": [0, 0], "end": [10, 0]}},
            constraints={"Horizontal": ["line_1"]},
            coordinate_system_name="XY_Base",
        )

    def test_round_trip_with_cs_name(self, minimal_sketch: SketchSpec) -> None:
        """Named coordinate-system reference survives to_dict / from_dict."""
        d = minimal_sketch.to_dict()
        sk2 = SketchSpec.from_dict(d)
        assert sk2.coordinate_system_name == "XY_Base"
        assert sk2.sketch == minimal_sketch.sketch
        assert sk2.constraints == minimal_sketch.constraints

    def test_round_trip_with_cs_name_ref(self) -> None:
        """coordinate_system_name reference survives to_dict / from_dict."""
        sk = SketchSpec(
            sketch={"circle_1": {"center": [0, 0], "radius": 5}},
            constraints={"Radius": [["circle_1", "5 mm"]]},
            coordinate_system_name="TopPlane",
        )
        sk2 = SketchSpec.from_dict(sk.to_dict())
        assert sk2.coordinate_system_name == "TopPlane"

    def test_missing_constraints_defaults_empty_dict(self) -> None:
        """from_dict tolerates missing 'constraints' key."""
        d = {
            "sketch": {"line_1": {"start": [0, 0], "end": [1, 0]}},
            "coordinate_system_name": "XY_Base",
        }
        sk = SketchSpec.from_dict(d)
        assert sk.constraints == {}


class TestFeatureSpec:
    """Tests for FeatureSpec serialisation / deserialisation."""

    def test_extrude_round_trip(self) -> None:
        """Extrude FeatureSpec survives to_dict / from_dict."""
        feat = FeatureSpec(
            type="extrude",
            sketch_name="Sketch001",
            operation="NewBody",
            params={"towards": 50.0, "opposite": 0.0},
            param_aliases={"towards": "arm_length"},
        )
        feat2 = FeatureSpec.from_dict(feat.to_dict())
        assert feat2.type == "extrude"
        assert feat2.params["towards"] == 50.0
        assert feat2.param_aliases == {"towards": "arm_length"}
        assert feat2.feature_name is None

    def test_revolve_round_trip(self) -> None:
        """Revolve FeatureSpec survives to_dict / from_dict."""
        feat = FeatureSpec(
            type="revolve",
            sketch_name="Sketch002",
            operation="NewBody",
            params={"axis": [[0, 0, 0], [0, 0, 1]], "start": 0.0, "end": 360.0},
        )
        feat2 = FeatureSpec.from_dict(feat.to_dict())
        assert feat2.type == "revolve"
        assert feat2.params["end"] == 360.0

    def test_param_aliases_defaults_empty(self) -> None:
        """from_dict with missing param_aliases defaults to empty dict."""
        d = {
            "type": "extrude",
            "sketch_name": "Sketch001",
            "operation": "NewBody",
            "params": {"towards": 10.0},
        }
        feat = FeatureSpec.from_dict(d)
        assert feat.param_aliases == {}

    def test_boolean_feature_accepts_operation_from_params(self) -> None:
        """Boolean features can carry operation inside params."""
        d = {
            "type": "boolean",
            "params": {
                "base_object_name": "BaseSolid",
                "tool_object_name": "ToolSolid",
                "operation": "Intersect",
            },
            "feature_name": "Intersection",
        }
        feat = FeatureSpec.from_dict(d)
        assert feat.type == "boolean"
        assert feat.sketch_name == ""
        assert feat.operation == "Intersect"
        assert feat.params["base_object_name"] == "BaseSolid"


class TestFinishSpec:
    """Tests for FinishSpec serialisation / deserialisation."""

    def test_fillet_round_trip(self) -> None:
        """Fillet FinishSpec survives to_dict / from_dict."""
        fin = FinishSpec(
            type="fillet",
            near_points=[[10.0, 0.0, 50.0], [-10.0, 0.0, 50.0]],
            params={"radius": 2.0},
        )
        fin2 = FinishSpec.from_dict(fin.to_dict())
        assert fin2.type == "fillet"
        assert fin2.near_points == [[10.0, 0.0, 50.0], [-10.0, 0.0, 50.0]]
        assert fin2.params["radius"] == 2.0

    def test_chamfer_round_trip(self) -> None:
        """Chamfer FinishSpec survives to_dict / from_dict."""
        fin = FinishSpec(
            type="chamfer",
            near_points=[[5.0, 5.0, 0.0]],
            params={"dist": 1.0, "angle": 45.0},
        )
        fin2 = FinishSpec.from_dict(fin.to_dict())
        assert fin2.params["angle"] == 45.0


class TestFabricationPlan:
    """Tests for FabricationPlan (full document round-trip and validation)."""

    @pytest.fixture
    def simple_plan(self) -> FabricationPlan:
        """A minimal but complete FabricationPlan for a box."""
        return FabricationPlan(
            coordinate_systems=[
                CoordinateSystemSpec([0, 0, 0], [0, 0, 0], name="XY_Base"),
            ],
            sketches=[
                SketchSpec(
                    sketch={
                        "line_1": {"start": [0, 0], "end": [20, 0]},
                        "line_2": {"start": [20, 0], "end": [20, 10]},
                        "line_3": {"start": [20, 10], "end": [0, 10]},
                        "line_4": {"start": [0, 10], "end": [0, 0]},
                    },
                    constraints={
                        "Horizontal": ["line_1", "line_3"],
                        "Vertical": ["line_2", "line_4"],
                        "Length": [["line_1", "20 mm"], ["line_2", "10 mm"]],
                    },
                    coordinate_system_name="XY_Base",
                    sketch_name="Sketch001",
                )
            ],
            features=[
                FeatureSpec(
                    type="extrude",
                    sketch_name="Sketch001",
                    operation="NewBody",
                    params={"towards": 30.0},
                    param_aliases={"towards": "box_height"},
                )
            ],
            finishes=[
                FinishSpec(
                    type="fillet",
                    near_points=[[10.0, 5.0, 30.0]],
                    params={"radius": 1.0},
                )
            ],
            param_aliases={"box_height": "box_height"},
            metadata={"product_family": "structural_box", "material": "aluminium"},
        )

    def test_round_trip_full_plan(self, simple_plan: FabricationPlan) -> None:
        """Full FabricationPlan survives to_dict / from_dict."""
        d = simple_plan.to_dict()
        plan2 = FabricationPlan.from_dict(d)

        assert len(plan2.coordinate_systems) == 1
        assert plan2.coordinate_systems[0].name == "XY_Base"

        assert len(plan2.sketches) == 1
        assert plan2.sketches[0].sketch_name == "Sketch001"
        assert "Horizontal" in plan2.sketches[0].constraints

        assert len(plan2.features) == 1
        assert plan2.features[0].type == "extrude"
        assert plan2.features[0].param_aliases == {"towards": "box_height"}

        assert len(plan2.finishes) == 1
        assert plan2.finishes[0].type == "fillet"

        assert plan2.metadata["material"] == "aluminium"

    def test_describe_fabrication_plan_schema_is_agent_readable(self) -> None:
        """Schema description exposes examples and primitive contracts."""
        schema = describe_fabrication_plan_schema()

        assert schema["schema_name"] == "FabricationPlan"
        assert "coordinate_systems" in schema["top_level_fields"]
        assert "line_N" in schema["sketch_entity_conventions"]
        assert "extrude" in schema["feature_spec"]["types"]
        assert "Distance" in schema["constraint_entry_formats"]
        assert "vertical" in schema["distance_semantics"]
        assert schema["examples"][0]["features"][0]["type"] == "extrude"
        assert "parametric_linkage" in schema
        assert schema["parametric_linkage"]["extrude_feature_example"][
            "param_aliases"
        ] == {"towards": "thickness"}
        assert "diameter_note" in schema["parametric_linkage"]
        assert "expression" not in schema["parametric_linkage"]["diameter_example"][1]
        length_entries = schema["constraint_entry_formats"]["Length"]
        assert length_entries[1][1]["alias"] == "base_length"
        diameter_entries = schema["constraint_entry_formats"]["Diameter"]
        assert diameter_entries[1][1]["alias"] == "hole_diameter"
        assert "expression" not in diameter_entries[1][1]
        assert schema["feature_spec"]["revolve"]["param_aliases_note"]
        assert schema["feature_spec"]["helix"]["param_aliases_note"]

    def test_validate_fabrication_plan_accepts_minimal_example(self) -> None:
        """The published minimal example validates successfully."""
        result = validate_fabrication_plan(minimal_fabrication_plan_example())

        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_fabrication_plan_rejects_unknown_coordinate_system(
        self,
        simple_plan: FabricationPlan,
    ) -> None:
        """Sketches must reference declared coordinate systems."""
        plan = simple_plan.to_dict()
        plan["sketches"][0]["coordinate_system_name"] = "MissingPlane"

        result = validate_fabrication_plan(plan)

        assert result["valid"] is False
        assert any(
            error["code"] == "unknown_ref" and "MissingPlane" in error["message"]
            for error in result["errors"]
        )

    def test_validate_fabrication_plan_rejects_bad_constraint_ref(
        self,
        simple_plan: FabricationPlan,
    ) -> None:
        """Constraint references must point at known sketch entities."""
        plan = simple_plan.to_dict()
        plan["sketches"][0]["constraints"]["Horizontal"] = ["line_99"]

        result = validate_fabrication_plan(plan)

        assert result["valid"] is False
        assert any(
            error["code"] == "unknown_ref" and "line_99" in error["message"]
            for error in result["errors"]
        )

    def test_validate_fabrication_plan_accepts_directional_distance(
        self,
        simple_plan: FabricationPlan,
    ) -> None:
        """Directional Distance uses a dict payload and validates cleanly."""
        plan = simple_plan.to_dict()
        plan["sketches"][0]["constraints"] = {
            "Coincident": [["line_1.end", "line_2.start"]],
            "Distance": [
                [
                    "line_4.start",
                    "line_3.start",
                    {"length": "1.5 mm", "direction": "VERTICAL"},
                ]
            ],
        }

        result = validate_fabrication_plan(plan)

        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_fabrication_plan_accepts_origin_directional_distance(
        self, simple_plan: FabricationPlan
    ) -> None:
        plan = simple_plan.to_dict()
        plan["sketches"][0]["constraints"] = {
            "Distance": [
                [
                    "origin",
                    "line_1.start",
                    {"length": "5 mm", "direction": "HORIZONTAL"},
                ]
            ]
        }
        assert validate_fabrication_plan(plan)["valid"] is True

    def test_validate_fabrication_plan_rejects_self_distance(
        self, simple_plan: FabricationPlan
    ) -> None:
        plan = simple_plan.to_dict()
        plan["sketches"][0]["constraints"] = {
            "Distance": [
                [
                    "line_1.start",
                    "line_1.start",
                    {"length": "5 mm", "direction": "HORIZONTAL"},
                ]
            ]
        }
        result = validate_fabrication_plan(plan)
        assert result["valid"] is False
        assert any(error["code"] == "self_distance" for error in result["errors"])

    def test_validate_fabrication_plan_rejects_inline_sketch_coordinate_system(
        self, simple_plan: FabricationPlan
    ) -> None:
        plan = simple_plan.to_dict()
        plan["sketches"][0]["coordinate_system"] = {
            "euler_angles": [0, 0, 0],
            "translation": [0, 0, 0],
        }
        result = validate_fabrication_plan(plan)
        assert result["valid"] is False
        assert any(error["code"] == "retired_field" for error in result["errors"])

    def test_validate_fabrication_plan_warns_for_plain_point_distance(
        self,
        simple_plan: FabricationPlan,
    ) -> None:
        """Plain point Distance remains valid but warns about lost direction."""
        plan = simple_plan.to_dict()
        plan["sketches"][0]["constraints"] = {
            "Distance": [["line_4.start", "line_3.start", "1.5 mm"]]
        }

        result = validate_fabrication_plan(plan)

        assert result["valid"] is True
        assert result["errors"] == []
        assert any(
            warning["code"] == "plain_distance_no_direction"
            for warning in result["warnings"]
        )

    def test_validate_fabrication_plan_rejects_bad_distance_direction(
        self,
        simple_plan: FabricationPlan,
    ) -> None:
        """Distance.direction is limited to supported execution semantics."""
        plan = simple_plan.to_dict()
        plan["sketches"][0]["constraints"] = {
            "Distance": [
                [
                    "line_4.start",
                    "line_3.start",
                    {"length": "1.5 mm", "direction": "DIAGONAL"},
                ]
            ]
        }

        result = validate_fabrication_plan(plan)

        assert result["valid"] is False
        assert any(error["code"] == "bad_direction" for error in result["errors"])

    def test_validate_fabrication_plan_rejects_bad_constraint_shapes(
        self,
        simple_plan: FabricationPlan,
    ) -> None:
        """Validator checks arity and point/entity reference shapes."""
        plan = simple_plan.to_dict()
        plan["sketches"][0]["constraints"] = {
            "Length": [["line_1.start", "20 mm"]],
            "Coincident": [["line_1", "line_2.start"]],
            "Tangent": [["line_1", "line_2", "line_3"]],
        }

        result = validate_fabrication_plan(plan)

        assert result["valid"] is False
        assert any(error["code"] == "bad_ref_shape" for error in result["errors"])
        assert any(error["code"] == "bad_arity" for error in result["errors"])

    def test_validate_fabrication_plan_rejects_unknown_feature_type(
        self,
        simple_plan: FabricationPlan,
    ) -> None:
        """Feature types are limited to the Layer 1 primitive set."""
        plan = simple_plan.to_dict()
        plan["features"][0]["type"] = "sweep"

        result = validate_fabrication_plan(plan)

        assert result["valid"] is False
        assert any(error["code"] == "unsupported" for error in result["errors"])

    def test_to_dict_is_json_serialisable(self, simple_plan: FabricationPlan) -> None:
        """to_dict output can be serialised by the stdlib json module."""
        import json

        d = simple_plan.to_dict()
        serialised = json.dumps(d)
        assert '"box_height"' in serialised

    def test_empty_plan_defaults(self) -> None:
        """A plan with no finishes / aliases defaults correctly."""
        plan = FabricationPlan(coordinate_systems=[], sketches=[], features=[])
        d = plan.to_dict()
        assert d["finishes"] == []
        assert d["param_aliases"] == {}
        assert d["metadata"] == {}

    def test_from_dict_missing_finishes_defaults_empty(self) -> None:
        """from_dict tolerates missing 'finishes' key."""
        d: dict[str, list[object]] = {
            "coordinate_systems": [],
            "sketches": [],
            "features": [],
        }
        plan = FabricationPlan.from_dict(d)
        assert plan.finishes == []

    def test_feature_with_helix_type(self) -> None:
        """Helix FeatureSpec is preserved through a FabricationPlan round-trip."""
        plan = FabricationPlan(
            coordinate_systems=[],
            sketches=[],
            features=[
                FeatureSpec(
                    type="helix",
                    sketch_name="ProfileSketch",
                    operation="NewBody",
                    params={"axis": [[0, 0, 0], [0, 0, 1]], "pitch": 5.0, "turns": 4.0},
                    param_aliases={"pitch": "thread_pitch"},
                )
            ],
        )
        plan2 = FabricationPlan.from_dict(plan.to_dict())
        assert plan2.features[0].type == "helix"
        assert plan2.features[0].params["pitch"] == 5.0
        assert plan2.features[0].param_aliases["pitch"] == "thread_pitch"
