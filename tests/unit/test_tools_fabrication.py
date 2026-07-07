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
                "redundant_constraints": ["Horizontal"],
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
        assert "Horizontal" in result["redundant_constraints"]
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
                "operation": "Intersect",
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
                    "sketches": [],
                }
            ),
        ]

        result = await register_tools["evaluate_editability"](
            target_alias="arm_x_length",
            value=60.0,
        )

        assert result["ER"] == 1.0
        assert result["cPCSR"] == 1.0
        assert result["OES"] == 1.0
        assert result["preserved_satisfied_constraints"] == 1
        assert result["component_scores"]["target_hit"] == 1.0
        assert result["component_scores"]["preserved_alias_satisfaction"] == 1.0

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

    def test_coordinate_system_key_normalization(self) -> None:
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

    def test_register_template_tools_registers_l_connector(
        self, mock_mcp: MagicMock
    ) -> None:
        """Template registration exposes the L connector resolver."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        assert "resolve_l_connector_template" in mock_mcp._registered_tools

    @pytest.mark.asyncio
    async def test_l_connector_template_contract(self, mock_mcp: MagicMock) -> None:
        """L connector template returns a FabricationPlan with tunable aliases."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        result = await mock_mcp._registered_tools["resolve_l_connector_template"](
            description="L connector 5cm 8cm 3cm",
            thickness=6.0,
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

    def test_l_connector_template_parses_keyed_chinese_dimensions(self) -> None:
        """Chinese long/short/width/thickness labels map to the intended aliases."""
        from freecad_mcp.tools.templates.connectors import build_l_connector_plan

        plan = build_l_connector_plan(
            description="生成一个L型焊装连接件, 长边80mm, 短边50mm, 宽度30mm, 厚度6mm"
        )
        sketch = plan.sketches[0].sketch
        length_constraints = plan.sketches[0].constraints["Length"]

        assert sketch["line_1"]["end"] == [50.0, 0.0]
        assert sketch["line_6"]["end"] == [0.0, 0.0]
        assert length_constraints[0][1]["length"] == 50.0
        assert length_constraints[1][1]["length"] == 30.0
        assert length_constraints[2][1]["length"] == 30.0
        assert length_constraints[3][1]["length"] == 80.0
        assert plan.features[0].params["towards"] == 6.0
