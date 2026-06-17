"""Tests for assembly semantics tools."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from freecad_mcp.bridge.base import ExecutionResult


class TestAssemblyTools:
    """Tests for assembly-oriented MCP tools."""

    @pytest.fixture
    def mock_mcp(self):
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
    def mock_bridge(self):
        """Create a mock FreeCAD bridge."""
        return AsyncMock()

    @pytest.fixture
    def register_tools(self, mock_mcp, mock_bridge):
        """Register assembly tools and return the registered functions."""
        from freecad_mcp.tools.assembly import register_assembly_tools

        async def get_bridge():
            return mock_bridge

        register_assembly_tools(mock_mcp, get_bridge)
        return mock_mcp._registered_tools

    def _success(self, payload):
        return ExecutionResult(
            success=True,
            result=payload,
            stdout="",
            stderr="",
            execution_time_ms=1.0,
        )

    @pytest.mark.asyncio
    async def test_registers_expected_tools(self, register_tools):
        """All assembly tools should be registered."""
        assert set(register_tools) == {
            "align_coordinate_systems",
            "create_local_coordinate_system",
            "find_faces_by_constraints",
            "get_mounting_features",
            "list_assembly_state",
            "preview_or_highlight_references",
        }

    @pytest.mark.asyncio
    async def test_find_faces_by_constraints_embeds_constraints(
        self, register_tools, mock_bridge
    ):
        """find_faces_by_constraints should pass structured constraints."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"candidates": [{"face": "Face1"}]})
        )
        constraints = {
            "surface_type": "Plane",
            "external_only": True,
            "normal_parallel_to": [0, 0, 1],
        }

        result = await register_tools["find_faces_by_constraints"](
            "Part", constraints, doc_name="Doc"
        )

        assert result["candidates"][0]["face"] == "Face1"
        code = mock_bridge.execute_python.call_args.args[0]
        assert "normal_parallel_to" in code
        assert "external_only" in code
        assert "FreeCAD.getDocument('Doc')" in code
        assert "center_local" in code
        assert "normal_local" in code
        assert "center_world" not in code

    @pytest.mark.asyncio
    async def test_find_faces_by_constraints_defaults_to_external_faces(
        self, register_tools, mock_bridge
    ):
        """find_faces_by_constraints should default to external-only faces."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"candidates": []})
        )

        await register_tools["find_faces_by_constraints"](
            "Part", {"surface_type": "Plane"}
        )

        code = mock_bridge.execute_python.call_args.args[0]
        assert 'if "external_only" not in constraints:' in code
        assert 'constraints["external_only"] = True' in code
        assert 'if constraints["external_only"] and is_ext is not True:' in code

    @pytest.mark.asyncio
    async def test_find_faces_by_constraints_allows_internal_faces_when_requested(
        self, register_tools, mock_bridge
    ):
        """find_faces_by_constraints should allow opting out of external-only."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"candidates": []})
        )

        await register_tools["find_faces_by_constraints"](
            "Part", {"surface_type": "Plane", "external_only": False}
        )

        code = mock_bridge.execute_python.call_args.args[0]
        assert "'external_only': False" in code

    @pytest.mark.asyncio
    async def test_find_faces_by_constraints_embeds_hole_radius_bounds(
        self, register_tools, mock_bridge
    ):
        """find_faces_by_constraints should embed optional hole-radius bounds."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"candidates": [{"face": "Face21"}]})
        )
        constraints = {
            "min_hole_count": 6,
            "min_hole_radius": 1.0,
            "max_hole_radius": 5.0,
        }

        await register_tools["find_faces_by_constraints"]("Part", constraints)

        code = mock_bridge.execute_python.call_args.args[0]
        assert "min_hole_radius" in code
        assert "max_hole_radius" in code
        assert "hole_radius_matches" in code
        assert "filter_holes_by_radius" in code
        assert "largest_radius" in code

    @pytest.mark.asyncio
    async def test_get_mounting_features_uses_face_reference(
        self, register_tools, mock_bridge
    ):
        """get_mounting_features should resolve the requested face."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"face": "Face19", "hole_count": 4})
        )

        result = await register_tools["get_mounting_features"]("Part", "Face19")

        assert result["hole_count"] == 4
        code = mock_bridge.execute_python.call_args.args[0]
        assert "face_name = 'Face19'" in code
        assert "connector_candidates" in code
        assert "origin_local" in code
        assert "primary_axis_local" in code
        assert "tertiary_axis_local" in code
        assert "origin_world" not in code

    @pytest.mark.asyncio
    async def test_create_local_coordinate_system_accepts_resolvers(
        self, register_tools, mock_bridge
    ):
        """create_local_coordinate_system should accept structured resolvers."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success(
                {
                    "name": "MountCS",
                    "contract": {"origin_local": [0, 0, 0]},
                }
            )
        )

        result = await register_tools["create_local_coordinate_system"](
            object_name="Part",
            name="MountCS",
            origin={"type": "face_center", "face": "Face1"},
            primary_axis={"type": "face_normal", "face": "Face1"},
            tertiary_axis={"type": "bbox_center_to_face_center", "face": "Face1"},
        )

        assert result["name"] == "MountCS"
        code = mock_bridge.execute_python.call_args.args[0]
        assert "OriginLocal" in code
        assert "PrimaryAxisLocal" in code
        assert "TertiaryAxisLocal" in code
        assert "ContractVersion" in code
        assert "SemanticRole" in code
        assert "face_center" in code
        assert "bbox_center_to_face_center" in code
        assert "face_orientation_reference" in code
        assert "default_tertiary_face_ref" in code
        assert "tertiary_spec" in code
        assert "RotationUnderconstrained" not in code
        assert "Origin =" not in code
        assert "origin_world" not in code
        assert "tertiary.cross(primary)" in code

    @pytest.mark.asyncio
    async def test_align_coordinate_systems_returns_transform(
        self, register_tools, mock_bridge
    ):
        """align_coordinate_systems should compute and apply a rigid transform."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success(
                {
                    "moving_object": "Moving",
                    "transform_matrix": [[1, 0, 0, 5]],
                }
            )
        )

        result = await register_tools["align_coordinate_systems"](
            moving_object="Moving",
            moving_csys="MovingCS",
            fixed_object="Fixed",
            fixed_csys="FixedCS",
            preserve_offset=[0, 0, 10],
        )

        assert result["moving_object"] == "Moving"
        code = mock_bridge.execute_python.call_args.args[0]
        assert "resolve_connector_world_frame" in code
        assert "OriginLocal" in code
        assert "target_x = fixed_frame" in code
        assert "target_y = target_z.cross(target_x)" in code
        assert "RelationshipJson" in code
        assert "observation" in code
        assert "Connector {connector.Name} is missing local contract fields" in code
        assert "preserve_offset" not in code
        assert "[0, 0, 10]" in code

    @pytest.mark.asyncio
    async def test_list_assembly_state_returns_semantic_state(
        self, register_tools, mock_bridge
    ):
        """list_assembly_state should expose connectors and relationships."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"objects": [], "relationships": []})
        )

        result = await register_tools["list_assembly_state"](
            object_names=["Part"], doc_name="Doc"
        )

        assert result["objects"] == []
        code = mock_bridge.execute_python.call_args.args[0]
        assert "object_names = ['Part']" in code
        assert "RelationshipJson" in code
        assert "OriginLocal" in code
        assert "relationships" in code
        assert "Faces" not in code

    @pytest.mark.asyncio
    async def test_preview_or_highlight_references_selects_subelements(
        self, register_tools, mock_bridge
    ):
        """preview_or_highlight_references should support faces and edges."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success(
                {"success": True, "highlighted_faces": ["Face2"]}
            )
        )

        result = await register_tools["preview_or_highlight_references"](
            "Part",
            faces=["Face2"],
            edges=["Edge4"],
            points=[[1, 2, 3]],
            axes=[{"origin": [0, 0, 0], "direction": [0, 0, 1]}],
        )

        assert result["success"] is True
        code = mock_bridge.execute_python.call_args.args[0]
        assert "Selection.addSelection" in code
        assert "AssemblyPointPreview" in code
        assert "AssemblyAxisPreview" in code

    @pytest.mark.asyncio
    async def test_failed_script_raises_value_error(self, register_tools, mock_bridge):
        """Tool failures should surface bridge tracebacks."""
        mock_bridge.execute_python = AsyncMock(
            return_value=ExecutionResult(
                success=False,
                result=None,
                stdout="",
                stderr="",
                execution_time_ms=1.0,
                error_traceback="boom",
            )
        )

        with pytest.raises(ValueError, match="boom"):
            await register_tools["find_faces_by_constraints"]("Missing", {})
