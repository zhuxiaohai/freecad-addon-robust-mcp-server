"""Tests for fabrication primitives (Layer 1) tool registration and behaviour."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from freecad_mcp.bridge.base import ExecutionResult


class TestFabricationTools:
    """Tests for Layer 1 generic fabrication MCP tools."""

    @pytest.fixture
    def mock_mcp(self) -> MagicMock:
        """Create a mock MCP server that captures tool registrations."""
        mcp = MagicMock()
        mcp._registered_tools = {}

        def tool_decorator():
            def wrapper(func):
                mcp._registered_tools[func.__name__] = func
                return func

            return wrapper

        mcp.tool = tool_decorator
        return mcp

    @pytest.fixture
    def mock_bridge(self) -> AsyncMock:
        """Create a mock FreeCAD bridge."""
        return AsyncMock()

    @pytest.fixture
    def register_tools(self, mock_mcp: MagicMock, mock_bridge: AsyncMock) -> dict:
        """Register fabrication tools and return the registered functions."""
        from freecad_mcp.tools.fabrication import register_fabrication_tools

        async def get_bridge():
            return mock_bridge

        register_fabrication_tools(mock_mcp, get_bridge)
        return mock_mcp._registered_tools

    def _success(self, payload: dict) -> ExecutionResult:
        return ExecutionResult(
            success=True,
            result=payload,
            stdout="",
            stderr="",
            execution_time_ms=1.0,
        )

    def _failure(self, error: str) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            result=None,
            stdout="",
            stderr=error,
            error_traceback=error,
            execution_time_ms=1.0,
        )

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def test_registers_all_expected_tools(self, register_tools: dict) -> None:
        """All fabrication tools should be registered."""
        expected = {
            "create_coordinate_system",
            "create_sketch_geometry",
            "parse_freecad_sketch",
            "check_sketch_constraints",
            "apply_sketch_constraints",
            "evaluate_sketch_editability",
            "build_edited_sketch_constraints",
            "execute_extrude",
            "execute_boolean",
            "execute_revolve",
            "execute_helix",
            "feature_fillet",
            "feature_chamfer",
            "list_tunable_params",
            "set_tunable_param",
            "evaluate_editability",
            "get_body_snapshot",
            "describe_fabrication_plan_schema",
            "validate_fabrication_plan",
            "execute_fabrication_plan",
        }
        assert set(register_tools.keys()) == expected

    # ------------------------------------------------------------------
    # create_coordinate_system
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_coordinate_system_success(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """create_coordinate_system returns cs_name and placement on success."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "cs_name": "LocalCSYS_001",
                "label": "TopPlane",
                "placement": {
                    "euler_angles": [0.0, 0.0, 0.0],
                    "translation": [0.0, 0.0, 50.0],
                },
                "success": True,
            }
        )
        result = await register_tools["create_coordinate_system"](
            euler_angles=[0.0, 0.0, 0.0],
            translation=[0.0, 0.0, 50.0],
            name="TopPlane",
        )
        assert result["cs_name"] == "LocalCSYS_001"
        assert result["placement"]["translation"] == [0.0, 0.0, 50.0]
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_create_coordinate_system_raises_on_failure(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """create_coordinate_system raises ValueError on bridge failure."""
        mock_bridge.execute_python.return_value = self._failure("No active document")
        with pytest.raises(ValueError, match="No active document"):
            await register_tools["create_coordinate_system"](
                euler_angles=[0, 0, 0], translation=[0, 0, 0]
            )

    # ------------------------------------------------------------------
    # create_sketch_geometry
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_sketch_geometry_success(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """create_sketch_geometry returns sketch_name, counts, and profile."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "sketch_name": "Sketch001",
                "geometry_count": {"total": 2, "lines": 2, "arcs": 0, "circles": 0},
                "profile": {
                    "loops": 1,
                    "closed_loops": 1,
                    "open_loops": 0,
                    "closed": True,
                },
                "dof_remaining": 4,
                "fully_constrained": False,
                "sketch_normal_world": [0.0, 0.0, 1.0],
                "sketch_origin_world": [0.0, 0.0, 0.0],
                "success": True,
            }
        )
        result = await register_tools["create_sketch_geometry"](
            sketch={"line_1": {"start": [0, 0], "end": [10, 0]}},
            coordinate_system={"euler_angles": [0, 0, 0], "translation": [0, 0, 0]},
        )
        assert result["sketch_name"] == "Sketch001"
        assert result["geometry_count"]["lines"] == 2
        assert result["profile"]["closed"] is True
        assert result["dof_remaining"] == 4

    @pytest.mark.asyncio
    async def test_create_sketch_geometry_with_attachment_support(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """attachment_support is forwarded to the bridge code."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "sketch_name": "Sketch002",
                "entity_index_map": {},
                "dof_remaining": 2,
                "attachment_info": {"face_name": "Face3", "attachment_offset": 0.0},
                "success": True,
            }
        )
        result = await register_tools["create_sketch_geometry"](
            sketch={"circle_1": {"center": [0, 0], "radius": 5}},
            attachment_support={"near_point": [0.0, 0.0, 30.0]},
        )
        assert result["attachment_info"]["face_name"] == "Face3"

    # ------------------------------------------------------------------
    # apply_sketch_constraints
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_apply_sketch_constraints_returns_dof(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """apply_sketch_constraints returns dof_after, profile, and drift."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "dof_before": 8,
                "dof_after": 0,
                "fully_constrained": True,
                "solve_status": 0,
                "applied_count": 7,
                "redundant_constraints": [],
                "conflicting_constraints": [],
                "profile": {
                    "loops": 1,
                    "closed_loops": 1,
                    "open_loops": 0,
                    "closed": True,
                },
                "geometry_drift": {"max_mm": 0.0, "drifted_entities": []},
                "success": True,
            }
        )
        result = await register_tools["apply_sketch_constraints"](
            sketch_name="Sketch001",
            constraints={
                "Horizontal": ["line_1", "line_3"],
                "Vertical": ["line_2", "line_4"],
                "Length": [["line_1", "20 mm"]],
            },
        )
        assert result["dof_after"] == 0
        assert result["fully_constrained"] is True
        assert result["applied_count"] == 7
        assert result["profile"]["closed"] is True
        assert result["geometry_drift"]["max_mm"] == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_sketch_editability_uses_parse(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """evaluate_sketch_editability parses sketch geometry when needed."""
        step0_sketch = {
            "line_3": {"start": [10.0, -0.75], "end": [10.0, 0.75]},
            "line_4": {"start": [10.0, 0.75], "end": [3.8611, 1.0717]},
        }
        constraints = {
            "Distance": [
                [
                    "line_4.start",
                    "line_3.start",
                    {"length": 1.5, "direction": "VERTICAL"},
                ]
            ]
        }
        mock_bridge.execute_python.return_value = self._success(
            {
                "sketch_name": "Sketch001",
                "sketch": step0_sketch,
                "entity_index_map": {},
                "dof_remaining": 0,
                "existing_constraints": [],
            }
        )
        result = await register_tools["evaluate_sketch_editability"](
            reference_constraints=constraints,
            constraint_type="Distance",
            entry_index=0,
            edited_value_mm=1.5,
            sketch_name="Sketch001",
        )
        assert result["target_hit"] is True
        assert result["OES"] is True
        assert result["ER"] is True

    @pytest.mark.asyncio
    async def test_apply_sketch_constraints_redundancy_detected(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """apply_sketch_constraints surfaces redundancy and geometry drift."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "dof_before": 2,
                "dof_after": -1,
                "fully_constrained": False,
                "solve_status": 0,
                "applied_count": 2,
                "input_constraint_count": 1,
                "constraint_catalog": [
                    {
                        "freecad_index": 1,
                        "freecad_type": "Horizontal",
                        "redundant": True,
                        "conflicting": False,
                        "input": {
                            "type": "Horizontal",
                            "entry_index": 0,
                            "entry": "line_1",
                        },
                        "entity_refs": ["line_1"],
                        "source": "input",
                    }
                ],
                "redundant": [
                    {
                        "freecad_index": 1,
                        "freecad_type": "Horizontal",
                        "redundant": True,
                    }
                ],
                "conflicting": [],
                "redundant_constraints": [1],
                "conflicting_constraints": [],
                "profile": {
                    "loops": 1,
                    "closed_loops": 1,
                    "open_loops": 0,
                    "closed": True,
                },
                "geometry_drift": {
                    "max_mm": 3.5706,
                    "drifted_entities": ["line_1", "line_2"],
                },
                "success": True,
            }
        )
        result = await register_tools["apply_sketch_constraints"](
            sketch_name="Sketch001",
            constraints={"Horizontal": ["line_1", "line_1"]},
        )
        assert result["dof_after"] == -1
        assert result["redundant_constraints"] == [1]
        assert result["redundant"][0]["freecad_index"] == 1
        assert result["geometry_drift"]["max_mm"] > 0
        assert "line_1" in result["geometry_drift"]["drifted_entities"]

    # ------------------------------------------------------------------
    # check_sketch_constraints
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_check_sketch_constraints_valid(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """check_sketch_constraints returns valid=True for non-conflicting set."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "valid": True,
                "would_over_constrain": False,
                "redundant_constraints": [],
                "estimated_dof_after": 2,
                "conflict_details": None,
            }
        )
        result = await register_tools["check_sketch_constraints"](
            sketch_name="Sketch001",
            constraints={"Horizontal": ["line_1"]},
        )
        assert result["valid"] is True
        assert result["estimated_dof_after"] == 2

    # ------------------------------------------------------------------
    # execute_extrude
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_execute_extrude_success(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """execute_extrude returns feature_name, local_obb, and global_center."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "feature_name": "Solid001",
                "local_obb": {
                    "center": [8.5, 2.0, 0.0],
                    "semi_extents": [8.5, 2.0, 10.0],
                },
                "global_center": [8.5, 2.0, 0.0],
                "bounding_box": {
                    "x_min": 0.0,
                    "x_max": 17.0,
                    "y_min": 0.0,
                    "y_max": 4.0,
                    "z_min": -10.0,
                    "z_max": 10.0,
                },
                "volume_mm3": 697.4026,
                "sketch_normal_world": [0.0, 0.0, 1.0],
                "extrusion_mode_used": "parametric_sketch",
                "fallback_reason": None,
                "success": True,
            }
        )
        result = await register_tools["execute_extrude"](
            sketch_name="Sketch001",
            towards=10.0,
            opposite=10.0,
        )
        assert result["feature_name"] == "Solid001"
        assert result["local_obb"]["center"] == [8.5, 2.0, 0.0]
        assert result["global_center"] == [8.5, 2.0, 0.0]
        assert result["bounding_box"]["z_max"] == 10.0
        assert result["extrusion_mode_used"] == "parametric_sketch"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_extrude_raises_on_failure(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """execute_extrude raises ValueError when the sketch is missing."""
        mock_bridge.execute_python.return_value = self._failure(
            "Sketch not found: 'Sketch999'"
        )
        with pytest.raises(ValueError, match="Sketch999"):
            await register_tools["execute_extrude"](
                sketch_name="Sketch999", towards=10.0
            )

    # ------------------------------------------------------------------
    # execute_boolean
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_execute_boolean_success(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """execute_boolean returns base/tool/result observations."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "feature_name": "Intersect001",
                "type_id": "Part::Common",
                "operation": "Intersect",
                "base_object_name": "Solid001",
                "tool_object_name": "Solid002",
                "dependency_preserved": True,
                "boolean_mode_used": "parametric",
                "fallback_reason": None,
                "base": {
                    "name": "Solid001",
                    "global_center": [8.5, 2.0, 0.0],
                    "volume_mm3": 697.4026,
                },
                "tool": {
                    "name": "Solid002",
                    "global_center": [8.5, 0.0, 3.5],
                    "volume_mm3": 1644.5576,
                },
                "result": {
                    "global_center": [8.5, 2.0, 3.5],
                    "bounding_box": {
                        "x_min": 0.0,
                        "x_max": 17.0,
                        "y_min": 0.0,
                        "y_max": 4.0,
                        "z_min": 0.0,
                        "z_max": 7.0,
                    },
                    "volume_mm3": 112.2476,
                },
                "center_distance": 4.0311,
                "success": True,
            }
        )
        result = await register_tools["execute_boolean"](
            base_object_name="Solid001",
            tool_object_name="Solid002",
            operation="Intersect",
        )
        assert result["feature_name"] == "Intersect001"
        assert result["type_id"] == "Part::Common"
        assert result["base_object_name"] == "Solid001"
        assert result["tool_object_name"] == "Solid002"
        assert result["dependency_preserved"] is True
        assert result["boolean_mode_used"] == "parametric"
        assert result["base"]["global_center"] == [8.5, 2.0, 0.0]
        assert result["tool"]["global_center"] == [8.5, 0.0, 3.5]
        assert result["center_distance"] == 4.0311
        assert result["result"]["volume_mm3"] == 112.2476

    @pytest.mark.asyncio
    async def test_execute_boolean_raises_on_missing_object(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """execute_boolean raises ValueError when the base object is missing."""
        mock_bridge.execute_python.return_value = self._failure(
            "Base object not found: 'Ghost'"
        )
        with pytest.raises(ValueError, match="Ghost"):
            await register_tools["execute_boolean"](
                base_object_name="Ghost",
                tool_object_name="Solid002",
                operation="Cut",
            )

    # ------------------------------------------------------------------
    # feature_fillet
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_feature_fillet_success(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """feature_fillet returns resolved_edge_count."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "feature_name": "Fillet001",
                "resolved_edge_count": 2,
                "radius": 2.0,
                "success": True,
            }
        )
        result = await register_tools["feature_fillet"](
            near_points=[[10.0, 0.0, 50.0], [-10.0, 0.0, 50.0]],
            radius=2.0,
        )
        assert result["resolved_edge_count"] == 2
        assert result["radius"] == 2.0

    # ------------------------------------------------------------------
    # list_tunable_params / set_tunable_param
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_tunable_params_empty(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """list_tunable_params returns empty list when no spreadsheet exists."""
        mock_bridge.execute_python.return_value = self._success(
            {"params": [], "spreadsheet_name": None}
        )
        result = await register_tools["list_tunable_params"]()
        assert result["params"] == []
        assert result["spreadsheet_name"] is None

    @pytest.mark.asyncio
    async def test_list_tunable_params_with_entries(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """list_tunable_params returns param list when spreadsheet populated."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "params": [
                    {
                        "alias": "column_height",
                        "value": 150.0,
                        "unit": "mm",
                        "cell": "A1",
                        "bound_to": [{"object": "Pad001", "property": ".Length"}],
                    }
                ],
                "spreadsheet_name": "FabricationParams",
            }
        )
        result = await register_tools["list_tunable_params"]()
        assert len(result["params"]) == 1
        assert result["params"][0]["alias"] == "column_height"

    @pytest.mark.asyncio
    async def test_set_tunable_param_success(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """set_tunable_param returns updated value and affected features."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "alias": "column_height",
                "old_value": 150.0,
                "new_value": 200.0,
                "recomputed": True,
                "affected_features": ["Pad001"],
                "success": True,
            }
        )
        result = await register_tools["set_tunable_param"](
            alias="column_height", value=200.0
        )
        assert result["new_value"] == 200.0
        assert "Pad001" in result["affected_features"]

    def test_set_tunable_param_collects_downstream_boolean_dependencies(self) -> None:
        """Source includes traversal from direct aliases to boolean result objects."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert 'for attr in ("Base", "Tool")' in source
        assert 'getattr(obj, "Shapes", [])' in source
        assert "affected_set.add(obj.Name)" in source

    @pytest.mark.asyncio
    async def test_set_tunable_param_missing_alias_raises(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """set_tunable_param raises ValueError when alias not found."""
        mock_bridge.execute_python.return_value = self._failure(
            "Alias 'unknown_param' not found"
        )
        with pytest.raises(ValueError, match="unknown_param"):
            await register_tools["set_tunable_param"](alias="unknown_param", value=10.0)

    @pytest.mark.asyncio
    async def test_evaluate_editability_success(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """evaluate_editability returns HistCAD-style reward metrics."""
        before_params = {
            "params": [
                {
                    "alias": "arm_x_length",
                    "value": 50.0,
                    "bound_to": [{"object": "Sketch", "property": "Constraints[13]"}],
                },
                {
                    "alias": "arm_y_length",
                    "value": 80.0,
                    "bound_to": [{"object": "Sketch", "property": "Constraints[16]"}],
                },
            ],
            "spreadsheet_name": "FabricationParams",
        }
        after_params = {
            "params": [
                {
                    "alias": "arm_x_length",
                    "value": 60.0,
                    "bound_to": [{"object": "Sketch", "property": "Constraints[13]"}],
                },
                {
                    "alias": "arm_y_length",
                    "value": 80.0,
                    "bound_to": [{"object": "Sketch", "property": "Constraints[16]"}],
                },
            ],
            "spreadsheet_name": "FabricationParams",
        }
        mock_bridge.execute_python.side_effect = [
            self._success(before_params),
            self._success(
                {
                    "objects": [
                        {
                            "name": "L_Connector_Profile",
                            "type_id": "Sketcher::SketchObject",
                            "volume": 0.0,
                            "bounding_box": [0, 50, 0, 80, 0, 0],
                            "dependencies": [],
                        }
                    ]
                }
            ),
            self._success(
                {
                    "alias": "arm_x_length",
                    "old_value": 50.0,
                    "new_value": 60.0,
                    "recomputed": True,
                    "affected_features": ["L_Connector_Profile"],
                    "success": True,
                }
            ),
            self._success(after_params),
            self._success(
                {
                    "rebuild_success": True,
                    "validation_ok": True,
                    "exception": None,
                    "shape_errors": [],
                    "sketches": [
                        {
                            "name": "Sketch",
                            "dof": 0,
                            "fully_constrained": True,
                            "conflicting": [],
                            "redundant": [],
                            "constraint_types": ["Horizontal", "Vertical", "Length"],
                        }
                    ],
                }
            ),
            self._success(
                {
                    "objects": [
                        {
                            "name": "L_Connector_Profile",
                            "type_id": "Sketcher::SketchObject",
                            "volume": 0.0,
                            "bounding_box": [0, 60, 0, 80, 0, 0],
                            "dependencies": [],
                        }
                    ]
                }
            ),
        ]

        result = await register_tools["evaluate_editability"](
            target_alias="arm_x_length",
            value=60.0,
            design_intent={
                "preserve_aliases": ["arm_y_length"],
                "coupled_aliases": [],
                "free_aliases": [],
                "expected_dof": 0,
                "required_constraint_types": ["Horizontal", "Vertical", "Length"],
            },
        )

        assert result["ER"] == 1.0
        assert result["cPCSR"] == 1.0
        assert result["OES"] == 1.0
        assert result["preserved_satisfied_constraints"] == 3
        assert len(result["preserved_records"]) == 1
        assert len(result["sketch_constraint_records"]) == 1
        assert result["component_scores"]["target_hit"] == 1.0
        assert result["component_scores"]["preserved_alias_satisfaction"] == 1.0
        assert result["component_scores"]["sketch_constraint_health"] == 1.0
        assert result["component_scores"]["geometry_update"] == 1.0
        assert result["weighted_reward"] == 1.0
        assert result["design_intent"]["expected_dof"] == 0

    @pytest.mark.asyncio
    async def test_evaluate_editability_fails_when_affected_geometry_is_static(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """Editability catches a changed parameter that does not update final shape."""
        params_before = {
            "params": [
                {
                    "alias": "arm_x_length",
                    "value": 50.0,
                    "bound_to": [{"object": "Sketch", "property": "Constraints[13]"}],
                }
            ],
            "spreadsheet_name": "FabricationParams",
        }
        params_after = {
            "params": [
                {
                    "alias": "arm_x_length",
                    "value": 60.0,
                    "bound_to": [{"object": "Sketch", "property": "Constraints[13]"}],
                }
            ],
            "spreadsheet_name": "FabricationParams",
        }
        static_shape = {
            "objects": [
                {
                    "name": "Join",
                    "type_id": "Part::Feature",
                    "volume": 178000.0,
                    "bounding_box": [0, 100, 0, 80, 0, 26],
                    "dependencies": [],
                }
            ]
        }
        mock_bridge.execute_python.side_effect = [
            self._success(params_before),
            self._success(static_shape),
            self._success(
                {
                    "alias": "arm_x_length",
                    "old_value": 50.0,
                    "new_value": 60.0,
                    "recomputed": True,
                    "affected_features": ["Join"],
                    "success": True,
                }
            ),
            self._success(params_after),
            self._success(
                {
                    "rebuild_success": True,
                    "validation_ok": True,
                    "exception": None,
                    "shape_errors": [],
                    "sketches": [],
                }
            ),
            self._success(static_shape),
        ]

        result = await register_tools["evaluate_editability"](
            target_alias="arm_x_length",
            value=60.0,
        )

        assert result["geometry_update_ok"] is False
        assert result["component_scores"]["geometry_update"] == 0.0
        assert result["ER"] == 0.0
        assert result["reward"] == 0.0

    # ------------------------------------------------------------------
    # get_body_snapshot
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_body_snapshot_returns_edge_samples(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """get_body_snapshot returns edge_samples list with near_point entries."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "bounding_box": {
                    "x_min": -10.0,
                    "x_max": 10.0,
                    "y_min": -10.0,
                    "y_max": 10.0,
                    "z_min": 0.0,
                    "z_max": 30.0,
                },
                "volume": 12000.0,
                "features": [{"name": "Pad001", "type": "Pad"}],
                "edge_samples": [
                    {
                        "near_point": [10.0, 0.0, 30.0],
                        "length": 62.8,
                        "curve_type": "Circle",
                    }
                ],
                "success": True,
            }
        )
        result = await register_tools["get_body_snapshot"]()
        assert result["volume"] == 12000.0
        assert len(result["edge_samples"]) == 1
        assert result["edge_samples"][0]["curve_type"] == "Circle"

    @pytest.mark.asyncio
    async def test_execute_fabrication_plan_rejects_implicit_extrude_boolean(
        self, register_tools: dict
    ) -> None:
        """Batch plans must use explicit boolean features, not extrude.operation."""
        plan = {
            "coordinate_systems": [],
            "sketches": [],
            "features": [
                {
                    "type": "extrude",
                    "sketch_name": "ToolSketch",
                    "operation": "Join",
                    "params": {"towards": 10.0},
                    "feature_name": "ToolSolid",
                }
            ],
        }

        with pytest.raises(ValueError, match="explicit boolean features"):
            await register_tools["execute_fabrication_plan"](plan)

    @pytest.mark.asyncio
    async def test_execute_fabrication_plan_boolean_requires_base_and_tool(
        self, register_tools: dict
    ) -> None:
        """Boolean features must explicitly identify both operands."""
        plan = {
            "coordinate_systems": [],
            "sketches": [],
            "features": [
                {
                    "type": "boolean",
                    "operation": "Intersect",
                    "params": {"base_object_name": "BaseSolid"},
                    "feature_name": "Intersection",
                }
            ],
        }

        with pytest.raises(ValueError, match="base_object_name"):
            await register_tools["execute_fabrication_plan"](plan)

    @pytest.mark.asyncio
    async def test_execute_fabrication_plan_runs_explicit_boolean_feature(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """Batch executor dispatches boolean features with explicit base/tool."""
        mock_bridge.execute_python.side_effect = [
            self._success(
                {
                    "feature_name": "Intersection",
                    "type_id": "Part::Common",
                    "operation": "Intersect",
                    "base_object_name": "BaseSolid",
                    "tool_object_name": "ToolSolid",
                    "dependency_preserved": True,
                    "boolean_mode_used": "parametric",
                    "fallback_reason": None,
                    "base": {
                        "name": "BaseSolid",
                        "global_center": [0, 0, 0],
                        "volume_mm3": 10.0,
                    },
                    "tool": {
                        "name": "ToolSolid",
                        "global_center": [0, 0, 0],
                        "volume_mm3": 10.0,
                    },
                    "result": {
                        "global_center": [0, 0, 0],
                        "bounding_box": {},
                        "volume_mm3": 5.0,
                    },
                    "center_distance": 0.0,
                    "success": True,
                }
            ),
            self._success(
                {
                    "bounding_box": {},
                    "volume": 5.0,
                    "features": [],
                    "edge_samples": [],
                    "success": True,
                }
            ),
            self._success({"params": [], "spreadsheet_name": None}),
        ]
        plan = {
            "coordinate_systems": [],
            "sketches": [],
            "features": [
                {
                    "type": "boolean",
                    "operation": "Intersect",
                    "params": {
                        "base_object_name": "BaseSolid",
                        "tool_object_name": "ToolSolid",
                    },
                    "feature_name": "Intersection",
                }
            ],
        }

        result = await register_tools["execute_fabrication_plan"](plan)

        assert result["feature_names"] == ["Intersection"]
        assert result["body_name"] == "Intersection"
        boolean_code = mock_bridge.execute_python.call_args_list[0].args[0]
        assert "BaseSolid" in boolean_code
        assert "ToolSolid" in boolean_code


class TestFabricationSourceConventions:
    """Static checks that fabrication.py matches Fusion adapter semantics."""

    def test_euler_angles_not_negated(self) -> None:
        """Placement uses active Rx*Ry*Rz without negating HistCAD angles."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "FreeCAD.Vector(0, 0, 1), -euler[2]" not in source
        assert "FreeCAD.Vector(0, 0, 1), euler[2]" in source

    def test_extrude_uses_bullseye_face_maker(self) -> None:
        """Multi-loop profiles use FaceMakerBullseye for holes."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "Part::FaceMakerBullseye" in source
        assert "_make_face" in source

    def test_extrude_has_robust_face_fallback(self) -> None:
        """Sketch extrusion falls back to raw-face extrusion when invalid."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        extrude_source = source.split("async def execute_extrude", 1)[1].split(
            "async def execute_boolean", 1
        )[0]
        assert 'extrusion_mode: str = "auto"' in extrude_source
        assert '"robust_face"' in extrude_source
        assert "_create_robust_face_extrusion" in extrude_source
        assert "_shape_failure_reason" in extrude_source
        assert 'extrusion_mode_used": extrusion_mode_used' in extrude_source

    def test_boolean_uses_parametric_objects_with_static_fallback(self) -> None:
        """Boolean execution preserves dependencies until fallback is needed."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        boolean_source = source.split("async def execute_boolean", 1)[1].split(
            "async def execute_revolve", 1
        )[0]
        assert ".Shape.copy()" not in boolean_source
        assert "feat.Shape = result_shape" not in boolean_source
        assert "_create_parametric_boolean" in boolean_source
        assert "_create_linked_boolean_feature" in boolean_source
        assert "_FabricationBooleanViewProvider" in boolean_source
        assert "_try_linked_boolean_fallback" in boolean_source
        assert "_prepare_boolean_dependencies" in boolean_source
        assert "_direct_boolean_shape" in boolean_source
        assert "boolean_mode_used" in boolean_source
        assert "feat.Base = base_obj" in boolean_source
        assert "feat.Tool = tool_obj" in boolean_source
        assert "dependency_preserved" in boolean_source

    def test_mirror_constraint_axis_is_middle_entry(self) -> None:
        """HistCAD Mirror format is [entity, axis, entity]."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "i_ax, _ = _resolve_entity(entry[1])" in source
        assert "i_dst, _ = _resolve_entity(entry[2])" in source

    def test_mirror_handles_point_pair_format(self) -> None:
        """Mirror with point refs ('line_14.end') uses _resolve_point not _resolve_entity."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        # Point-pair branch detects "." in entry[0] and entry[2]
        assert '"." in str(entry[0]) and "." in str(entry[2])' in source
        # Point-pair branch calls _resolve_point for source and target
        assert "i_src, p_src = _resolve_point(entry[0])" in source
        assert "i_dst, p_dst = _resolve_point(entry[2])" in source

    def test_endpoint_map_binds_semantic_start_end_at_creation(self) -> None:
        """Start/end labels bind to PointPos once at sketch creation."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "_endpoint_map_for_geometry" in source
        assert "sketch_obj.getPoint(geo_idx, START)" in source
        assert "endpoint_map[name] = _endpoint_map_for_geometry" in source
        assert "_sketch_endpoint_map_cache" in source
        assert "if name in endpoint_map:" in source
        assert "_json_endpoints_for_entity(name, spec)" in source

    def test_endpoint_map_persisted_on_sketch_object(self) -> None:
        """Maps persist on sketch via HistCADMaps for cross-call reuse."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "_persist_sketch_maps" in source
        assert "_load_sketch_maps" in source
        assert "HistCADMaps" in source
        assert "HistCADGeometry" in source
        assert "_rebuild_endpoint_map(sk, idx_map, ground_truth)" not in source

    def test_fix_constraint_uses_block_for_whole_entity(self) -> None:
        """HistCAD Fix on entity uses Block(geo_idx) for FreeCAD 1.1."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "_fix_constraint" in source
        assert 'Sketcher.Constraint("Block", geo_idx)' in source
        assert (
            "p = None"
            in source.split('elif ctype == "Fix":')[1].split(
                'elif ctype == "Midpoint":'
            )[0]
        )

    def test_directed_vertical_distance_from_ground_truth(self) -> None:
        """VERTICAL Distance uses signed delta from sketch_helpers."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "_SKETCH_HELPER_CODE" in source
        assert "directed_axis_distance" in source
        assert "ground_truth=ground_truth" in source

    def test_orientation_stabilization_from_ground_truth(self) -> None:
        """Orientation stabilization is opt-in and skips Parallel-covered lines."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "_orientation_entries_from_ground_truth" in source
        assert "_orientation_covered_by_json_constraints" in source
        assert "Phase 1: topology" in source
        assert "Phase 2: optional axis-aligned orientation" in source
        assert "if orientation_stabilization:" in source
        assert "orientation_stabilization: bool = False" in source
        assert 'constraint_dict.get("Parallel"' in source
        assert "HistCADPolarities" in source
        assert "distance_polarity_key" in source
        assert "polarity_from_signed" in source

    def test_parse_freecad_sketch_uses_endpoint_map(self) -> None:
        """parse_freecad_sketch reads start/end via endpoint_map."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        parse_block = source.split("async def parse_freecad_sketch", 1)[1].split(
            "async def check_sketch_constraints", 1
        )[0]
        assert "_semantic_line_endpoints" in parse_block
        assert "_load_sketch_maps" in parse_block
        assert "geo.StartPoint" not in parse_block

    def test_entity_kind_from_histcad_name_prefix(self) -> None:
        """Sketch entities follow HistCAD name prefixes (line_, arc_, …)."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "_entity_kind_from_name" in source
        assert 'if name.startswith("line_"):' in source

    def test_constraint_catalog_in_apply_sketch_constraints(self) -> None:
        """apply_sketch_constraints returns mapped constraint_catalog rows."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert '"constraint_catalog":      _catalog' in source
        assert '"applied_constraints":     _catalog' in source
        assert '"redundant":               _redundant_entries' in source
        assert '"purged_redundant":        purged_redundant' in source
        assert '"applied_log":             applied_log' in source

    def test_directional_distance_feedback_is_exposed(self) -> None:
        """Directional Distance execution exposes actual FreeCAD constraint type."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert 'Sketcher.Constraint("DistanceY", i1, p1, i2, p2, val)' in source
        assert 'Sketcher.Constraint("DistanceX", i1, p1, i2, p2, val)' in source
        assert '"freecad_type": freecad_type' in source
        assert '"sketch_constraint_results": sketch_constraint_results' in source

    def test_redundant_constraints_purged_after_apply(self) -> None:
        """Tangent junction Coincident rows are purged so extrusion stays parametric."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "_purge_redundant_sketch_constraints" in source
        assert "_extend_coinc_map_from_sketch" in source
        assert '"purged_redundant": purged_redundant' in source
        assert "_pos = _r_idx - 1" in source
        assert "_purge_redundant_sketch_constraints(sk)" in source

    def test_execute_extrude_purges_before_parametric(self) -> None:
        """execute_extrude purges redundant sketch constraints before extrusion."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        extrude_block = source.split("async def execute_extrude", 1)[1].split(
            "async def execute_boolean", 1
        )[0]
        assert "_purge_redundant_sketch_constraints" in extrude_block
        assert "_create_parametric_extrusion()" in extrude_block

    def test_coordinate_system_key_normalization(self) -> None:
        """cs_inline parsing accepts both 'Euler Angles' and 'euler_angles' keys."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert '"Euler Angles"' in source
        assert '"Translation Vector"' in source
        assert '"euler_angles"' in source
        assert '"translation"' in source
        """cs_inline parsing accepts both 'Euler Angles' and 'euler_angles' keys."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        # Both capitalised (Fusion-360-adapter style) and lowercase keys must be tried
        assert '"Euler Angles"' in source
        assert '"Translation Vector"' in source
        # Fallback to lowercase-underscore style
        assert '"euler_angles"' in source
        assert '"translation"' in source


class TestConnectorTemplates:
    """Tests for Layer 2 connector templates."""

    @pytest.fixture
    def mock_mcp(self) -> MagicMock:
        mcp = MagicMock()
        mcp._registered_tools = {}

        def tool_decorator():
            def wrapper(func):
                mcp._registered_tools[func.__name__] = func
                return func

            return wrapper

        mcp.tool = tool_decorator
        return mcp

    def test_register_template_tools_registers_template_tools(
        self, mock_mcp: MagicMock
    ) -> None:
        """Template registration exposes resolve_template and L connector tool."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        assert "list_templates" in mock_mcp._registered_tools
        assert "describe_template" in mock_mcp._registered_tools
        assert "validate_intent" in mock_mcp._registered_tools
        assert "compile_intent" in mock_mcp._registered_tools
        assert "resolve_template" in mock_mcp._registered_tools
        assert "resolve_l_connector_template" in mock_mcp._registered_tools

    @pytest.mark.asyncio
    async def test_l_connector_template_contract(self, mock_mcp: MagicMock) -> None:
        """L connector template returns a FabricationPlan with tunable aliases."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        result = await mock_mcp._registered_tools["resolve_l_connector_template"](
            slots={
                "arm_x_length": 50.0,
                "arm_y_length": 80.0,
                "arm_x_width": 30.0,
                "arm_y_width": 30.0,
                "thickness": 6.0,
            },
        )

        assert len(result["coordinate_systems"]) == 1
        assert len(result["sketches"]) == 1
        assert len(result["features"]) == 1
        sketch = result["sketches"][0]
        assert len(sketch["sketch"]) == 6
        assert sketch["constraints"]["Length"][0][1]["alias"] == "arm_x_length"
        assert sketch["constraints"]["Length"][1][1]["alias"] == "arm_x_width"
        assert sketch["constraints"]["Length"][2][1]["alias"] == "arm_y_width"
        assert sketch["constraints"]["Length"][3][1]["alias"] == "arm_y_length"
        assert result["features"][0]["param_aliases"] == {"towards": "thickness"}
        assert result["metadata"]["editable_aliases"] == [
            "arm_x_length",
            "arm_x_width",
            "arm_y_width",
            "arm_y_length",
            "thickness",
        ]

    @pytest.mark.asyncio
    async def test_resolve_template_dispatcher(self, mock_mcp: MagicMock) -> None:
        """resolve_template routes IntentSpec to the correct domain template."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        result = await mock_mcp._registered_tools["resolve_template"](
            {
                "template_name": "l_connector",
                "slots": {
                    "arm_x_length": 50.0,
                    "arm_y_length": 80.0,
                    "width": 30.0,
                    "thickness": 6.0,
                },
                "slot_bindings": [
                    "arm_x_width = width",
                    "arm_y_width = width",
                ],
            }
        )

        assert result["metadata"]["template"] == "l_connector"
        assert result["metadata"]["intent_spec"]["template_name"] == "l_connector"
        assert result["features"][0]["params"]["towards"] == 6.0

    @pytest.mark.asyncio
    async def test_template_catalog_tools(self, mock_mcp: MagicMock) -> None:
        """Template catalog tools expose Agent1 selection metadata."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)

        catalog = await mock_mcp._registered_tools["list_templates"]()
        assert catalog[0]["template_name"] == "l_connector"
        assert (
            "width applies to both arms" in catalog[0]["example_intent"]["assumptions"]
        )

        described = await mock_mcp._registered_tools["describe_template"]("l_connector")
        assert "arm_x_top" in described["valid_face_ids"]

    @pytest.mark.asyncio
    async def test_validate_intent_tool_returns_attribution(
        self, mock_mcp: MagicMock
    ) -> None:
        """validate_intent returns structured repair attribution."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)

        result = await mock_mcp._registered_tools["validate_intent"](
            {
                "template_name": "l_connector",
                "slots": {"arm_x_length": 50.0},
            }
        )

        assert result["valid"] is False
        assert result["failure_attribution"] == "intent_agent_slot_or_capability"

    @pytest.mark.asyncio
    async def test_compile_intent_tool_returns_handoff_package(
        self, mock_mcp: MagicMock
    ) -> None:
        """compile_intent returns IntentSpec + deterministic plan package."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)

        package = await mock_mcp._registered_tools["compile_intent"](
            {
                "template_name": "l_connector",
                "slots": {
                    "arm_x_length": 50.0,
                    "arm_y_length": 80.0,
                    "width": 30.0,
                },
                "assumptions": ["unit=mm"],
            }
        )

        assert package["intent_spec"]["template_name"] == "l_connector"
        assert package["fabrication_plan"]["metadata"]["template"] == "l_connector"
        assert package["template_provenance"]["deterministic"] is True
        assert package["compile_diagnostics"]["ok"] is True
