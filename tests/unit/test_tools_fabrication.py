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
            "execute_revolve",
            "execute_helix",
            "feature_fillet",
            "feature_chamfer",
            "list_tunable_params",
            "set_tunable_param",
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
        """create_sketch_geometry returns sketch_name and entity_index_map."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "sketch_name": "Sketch001",
                "entity_index_map": {"line_1": 0, "line_2": 1},
                "dof_remaining": 4,
                "attachment_info": None,
                "success": True,
            }
        )
        result = await register_tools["create_sketch_geometry"](
            sketch={"line_1": {"start": [0, 0], "end": [10, 0]}},
            coordinate_system={"euler_angles": [0, 0, 0], "translation": [0, 0, 0]},
        )
        assert result["sketch_name"] == "Sketch001"
        assert result["entity_index_map"]["line_1"] == 0
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
        """apply_sketch_constraints returns dof_after and sketch_valid."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "dof_before": 8,
                "dof_after": 0,
                "redundant_constraints": [],
                "sketch_valid": True,
                "applied_count": 7,
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
        assert result["sketch_valid"] is True
        assert result["applied_count"] == 7

    @pytest.mark.asyncio
    async def test_apply_sketch_constraints_redundancy_detected(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """apply_sketch_constraints surfaces redundant constraints."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "dof_before": 2,
                "dof_after": -1,
                "redundant_constraints": ["Horizontal"],
                "sketch_valid": False,
                "applied_count": 2,
                "success": True,
            }
        )
        result = await register_tools["apply_sketch_constraints"](
            sketch_name="Sketch001",
            constraints={"Horizontal": ["line_1", "line_1"]},
        )
        assert result["dof_after"] == -1
        assert "Horizontal" in result["redundant_constraints"]
        assert result["sketch_valid"] is False

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
        """execute_extrude returns feature_name, bounding_box, and bound_params."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "feature_name": "Pad001",
                "body_name": "Body",
                "bounding_box": {
                    "x_min": -10.0,
                    "x_max": 10.0,
                    "y_min": -10.0,
                    "y_max": 10.0,
                    "z_min": 0.0,
                    "z_max": 50.0,
                },
                "volume": 20000.0,
                "bound_params": [
                    {"alias": "column_height", "cell": "A1", "value": 50.0}
                ],
                "success": True,
            }
        )
        result = await register_tools["execute_extrude"](
            sketch_name="Sketch001",
            towards=50.0,
            param_aliases={"towards": "column_height"},
        )
        assert result["feature_name"] == "Pad001"
        assert result["bounding_box"]["z_max"] == 50.0
        assert result["bound_params"][0]["alias"] == "column_height"

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


class TestTemplatePlaceholder:
    """Tests for the Layer 2 template placeholder."""

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

    def test_register_template_tools_registers_nothing(
        self, mock_mcp: MagicMock
    ) -> None:
        """Template placeholder registers zero tools (until domains are added)."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        assert mock_mcp._registered_tools == {}
