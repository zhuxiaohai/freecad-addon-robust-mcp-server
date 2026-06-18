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
        """All assembly tools should be registered with updated names."""
        assert set(register_tools) == {
            "align_coordinate_systems",
            "create_connector",
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
    async def test_create_connector_uses_lcs_backend(self, register_tools, mock_bridge):
        """create_connector should create a Part::LocalCoordinateSystem with attachment."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success(
                {
                    "name": "MountCS",
                    "contract": {
                        "origin_local": [805.0, 354.0, -26.0],
                        "origin_global": [805.0, 354.0, -26.0],
                        "contract_version": 2,
                    },
                    "observation": {"objects": []},
                }
            )
        )

        result = await register_tools["create_connector"](
            object_name="Part",
            name="MountCS",
            origin=[805.0, 354.0, -26.0],
            primary_axis=[0.0, 0.0, 1.0],
            tertiary_axis=[1.0, 0.0, 0.0],
            semantic_label="top face bolt pattern center",
        )

        assert result["name"] == "MountCS"
        code = mock_bridge.execute_python.call_args.args[0]

        # LCS backend checks
        assert "Part::LocalCoordinateSystem" in code
        assert "AttachmentSupport" in code
        assert "MapMode" in code
        assert "AttachmentOffset" in code

        # Semantic label (not role)
        assert "SemanticLabel" in code
        assert "SemanticRole" not in code
        assert "ReferenceObjectName" in code
        assert "ReferenceFace" not in code

        # Contract version 2
        assert "ContractVersion" in code
        assert "2" in code

        # Global fields in observation
        assert "origin_global" in code
        assert "primary_axis_global" in code
        assert "getGlobalPlacement" in code

        # No resolver mechanism
        assert "resolve_origin" not in code
        assert "resolve_axis" not in code
        assert "face_center" not in code
        assert "face_normal" not in code

    @pytest.mark.asyncio
    async def test_create_connector_validates_explicit_coordinates(
        self, register_tools, mock_bridge
    ):
        """create_connector generated code should reject non-list inputs."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"name": "CS", "contract": {}})
        )

        await register_tools["create_connector"](
            object_name="Part",
            name="CS",
            origin=[1.0, 2.0, 3.0],
            primary_axis=[0.0, 0.0, 1.0],
            tertiary_axis=[1.0, 0.0, 0.0],
        )

        code = mock_bridge.execute_python.call_args.args[0]
        # Must validate that only list[float] is accepted
        assert "isinstance(origin_raw, (list, tuple))" in code
        assert "isinstance(primary_raw, (list, tuple))" in code
        assert "isinstance(tertiary_raw, (list, tuple))" in code
        assert "TypeError" in code

    @pytest.mark.asyncio
    async def test_create_connector_builds_orthonormal_basis(
        self, register_tools, mock_bridge
    ):
        """create_connector should build an orthonormal frame from primary+tertiary."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"name": "CS", "contract": {}})
        )

        await register_tools["create_connector"](
            object_name="Part",
            name="CS",
            origin=[0.0, 0.0, 0.0],
            primary_axis=[0.0, 0.0, 1.0],
            tertiary_axis=[1.0, 0.0, 0.0],
        )

        code = mock_bridge.execute_python.call_args.args[0]
        assert (
            "tertiary_local.cross(primary_local)" in code
            or "tertiary_local.cross" in code
        )
        assert "secondary_local" in code
        assert "tertiary_local" in code

    @pytest.mark.asyncio
    async def test_create_connector_checks_freecad_version(
        self, register_tools, mock_bridge
    ):
        """create_connector should require FreeCAD 1.1+ at runtime."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"name": "CS", "contract": {}})
        )

        await register_tools["create_connector"](
            object_name="Part",
            name="CS",
            origin=[0.0, 0.0, 0.0],
            primary_axis=[0.0, 0.0, 1.0],
            tertiary_axis=[1.0, 0.0, 0.0],
        )

        code = mock_bridge.execute_python.call_args.args[0]
        assert "App.Version()" in code
        assert "(1, 1)" in code
        assert "FreeCAD 1.1+" in code

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
                    "observation": {"objects": []},
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
        assert "target_x = fixed_frame" in code
        assert "target_y = target_z.cross(target_x)" in code
        assert "RelationshipJson" in code
        assert "observation" in code
        assert "[0, 0, 10]" in code

        # LCS support
        assert "_is_lcs_connector" in code
        assert "getGlobalPlacement" in code
        assert "Part::LocalCoordinateSystem" in code

        # Legacy backward compatibility
        assert "_is_legacy_connector" in code
        assert "OriginLocal" in code

        # No refresh_marker (LCS updates automatically)
        assert "refresh_marker" not in code

        # Global coords in observation
        assert "global_position" in code
        assert "global_orientation_quat" in code

    @pytest.mark.asyncio
    async def test_list_assembly_state_returns_global_state(
        self, register_tools, mock_bridge
    ):
        """list_assembly_state should expose global-coord connectors and relationships."""
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
        assert "relationships" in code

        # Global coordinates are primary output
        assert "origin_global" in code
        assert "primary_axis_global" in code
        assert "getGlobalPlacement" in code

        # LCS scanner
        assert "Part::LocalCoordinateSystem" in code
        assert "SemanticLabel" in code
        assert "ReferenceObjectName" in code

        # Legacy scanner for backward compatibility
        assert "OriginLocal" in code

        # No raw face geometry in assembly state
        assert "Faces" not in code

    @pytest.mark.asyncio
    async def test_list_assembly_state_include_local_parameter(
        self, register_tools, mock_bridge
    ):
        """list_assembly_state should embed include_local flag in generated code."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"objects": [], "relationships": []})
        )

        await register_tools["list_assembly_state"](include_local=True)

        code = mock_bridge.execute_python.call_args.args[0]
        assert "include_local = True" in code
        assert "AttachmentOffset" in code

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
        assert "DiffuseColor" in code
        assert "rgb_color" in code
        assert "0.0)" not in code.split("rgb_color")[0]

    @pytest.mark.asyncio
    async def test_preview_or_highlight_skips_face_color_without_faces(
        self, register_tools, mock_bridge
    ):
        """Edge-only preview should not rewrite DiffuseColor."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success(
                {"success": True, "highlighted_edges": ["Edge1"]}
            )
        )

        await register_tools["preview_or_highlight_references"](
            "Part",
            edges=["Edge1"],
        )

        code = mock_bridge.execute_python.call_args.args[0]
        assert 'if faces and hasattr(obj, "ViewObject")' in code

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

    @pytest.mark.asyncio
    async def test_find_faces_by_constraints_default_uses_design_frame(
        self, register_tools, mock_bridge
    ):
        """Without reference_frame_connector the design frame (obj.getGlobalPlacement) is used."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"candidates": []})
        )

        await register_tools["find_faces_by_constraints"]("Part", {})

        code = mock_bridge.execute_python.call_args.args[0]
        # Default path: ref_connector_name is None, so design frame branch is taken
        assert "ref_connector_name = None" in code
        assert "ref_pl = obj.getGlobalPlacement()" in code
        # Semantic-frame lookup must not be attempted
        assert "ref_lcs = doc.getObject(ref_connector_name)" in code  # branch exists
        # But the None value means it will not be executed at runtime

    @pytest.mark.asyncio
    async def test_find_faces_by_constraints_semantic_frame_connector(
        self, register_tools, mock_bridge
    ):
        """When reference_frame_connector is set, the LCS connector frame is used."""
        mock_bridge.execute_python = AsyncMock(
            return_value=self._success({"candidates": []})
        )

        await register_tools["find_faces_by_constraints"](
            "Part",
            {"bbox_side": "-z"},
            reference_frame_connector="LCS_semantic_frame",
        )

        code = mock_bridge.execute_python.call_args.args[0]
        # Connector name is embedded
        assert "ref_connector_name = 'LCS_semantic_frame'" in code
        # Lookup and global placement of the LCS are present
        assert "ref_lcs = doc.getObject(ref_connector_name)" in code
        assert "ref_pl = ref_lcs.getGlobalPlacement()" in code
        # Error path for missing connector is present
        assert "reference_frame_connector not found" in code
        # Shape is transformed using ref_pl, not directly obj.getGlobalPlacement()
        assert "shape.transformShape(ref_pl.inverse().toMatrix())" in code
