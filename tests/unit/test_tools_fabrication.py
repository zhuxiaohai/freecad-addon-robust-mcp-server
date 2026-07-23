"""Tests for fabrication primitives (Layer 1) tool registration and behaviour."""

import json
from typing import Any
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
        mcp._registered_tool_meta = {}

        def tool_decorator(*, meta=None):
            def wrapper(func):
                mcp._registered_tools[func.__name__] = func
                mcp._registered_tool_meta[func.__name__] = meta or {}
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
            "get_body_snapshot",
            "validate_primitive_plan",
            "validate_operation_plan",
            "execute_operation_plan",
        }
        assert set(register_tools.keys()) == expected
        assert "describe_primitive_plan_schema" not in register_tools

    def test_no_template_primitives_expose_complete_mcp_metadata(
        self, register_tools: dict, mock_mcp: MagicMock
    ) -> None:
        """No-template primitive contracts are exposed through MCP tool metadata."""
        from freecad_mcp.tools.fabrication import _tool_args_schema

        no_template_primitives = [
            "create_coordinate_system",
            "create_sketch_geometry",
            "apply_sketch_constraints",
            "execute_extrude",
            "execute_boolean",
            "execute_revolve",
            "execute_helix",
            "feature_fillet",
            "feature_chamfer",
            "get_body_snapshot",
        ]

        for tool_name in no_template_primitives:
            assert tool_name in register_tools
            meta = mock_mcp._registered_tool_meta[tool_name]
            schema = _tool_args_schema(register_tools[tool_name])
            properties = schema.get("properties", {})
            for arg_name, arg_schema in properties.items():
                assert arg_schema.get("description"), (tool_name, arg_name)
            examples = meta["argument_examples"]
            assert examples, tool_name
            for example in examples:
                example_args = example.get("args", {})
                assert set(example_args).issubset(properties), tool_name
                assert set(schema.get("required", [])).issubset(example_args), (
                    tool_name,
                    example.get("name"),
                )
            assert meta["output_exports"], tool_name

        tools = mock_mcp._registered_tool_meta
        policy = tools["apply_sketch_constraints"]["planning_policy"]
        assert policy["constraint_selection_policy"] == "prompt_driven"
        assert "skip_apply_sketch_constraints_by_default" not in policy
        assert (
            "Do not add or omit apply_sketch_constraints without prompt evidence"
            in policy["tool_selection"]
        )
        assert any(
            "diameters" in condition
            for condition in policy["include_apply_sketch_constraints_when"]
        )
        assert "not a reason to ignore explicit constraints" in policy["examples_note"]
        constraint_tool = tools["apply_sketch_constraints"]
        assert "constraint_reference" in constraint_tool
        assert (
            "Concentric" in constraint_tool["constraint_reference"]["supported_types"]
        )
        assert any(
            example["name"] == "concentric_hole_pattern_constraints"
            for example in constraint_tool["argument_examples"]
        )
        recipes = tools["feature_fillet"]["workflow_recipes"]
        assert "snapshot_then_fillet_edges" in recipes
        assert (
            "snapshot_then_chamfer_edges"
            in tools["feature_chamfer"]["workflow_recipes"]
        )
        assert (
            "extrude_then_boolean_cut" in tools["execute_boolean"]["workflow_recipes"]
        )
        assert "revolve_profile" in tools["execute_revolve"]["workflow_recipes"]
        assert "helix_sweep" in tools["execute_helix"]["workflow_recipes"]
        assert "edge_samples" in tools["get_body_snapshot"]["output_exports"]
        assert "hole_features" in tools["get_body_snapshot"]["output_exports"]
        assert (
            "edge_samples" in tools["feature_fillet"]["argument_examples"][0]["notes"]
        )
        assert "near_points" in tools["feature_fillet"]["argument_examples"][0]["notes"]
        assert (
            "edge_samples" in tools["feature_chamfer"]["argument_examples"][0]["notes"]
        )
        assert (
            "near_points" in tools["feature_chamfer"]["argument_examples"][0]["notes"]
        )
        sketch_meta = tools["create_sketch_geometry"]
        assert "histcad_sketch_entity_reference" in sketch_meta
        entity_reference = sketch_meta["histcad_sketch_entity_reference"]["entities"]
        for entity_type in (
            "line",
            "circle",
            "ellipse",
            "arc",
            "elliptical_arc",
            "nurbs",
        ):
            assert entity_type in entity_reference
            assert entity_reference[entity_type]["schema"]
            assert entity_reference[entity_type]["example"]
        assert "sketch" in sketch_meta["argument_descriptions"]
        assert "large semantic slot" in sketch_meta["slot_guidance"]["sketch"]
        cs_schema = _tool_args_schema(register_tools["create_coordinate_system"])
        assert cs_schema["source"] == "python_function_signature"
        assert cs_schema["required"] == ["euler_angles", "translation"]
        assert cs_schema["properties"]["name"]["nullable"] is True
        extrude_schema = _tool_args_schema(register_tools["execute_extrude"])
        assert "sketch_name" in extrude_schema["required"]
        assert "towards" in extrude_schema["properties"]

    @pytest.mark.asyncio
    async def test_protocol_list_tools_exposes_primitive_metadata_and_arg_descriptions(
        self,
    ) -> None:
        """Protocol-level tools/list exposes the contract consumed by MCP clients."""
        from mcp.server.fastmcp import FastMCP

        from freecad_mcp.tools.fabrication import register_fabrication_tools

        async def get_bridge():
            raise AssertionError("list_tools must not require a FreeCAD bridge")

        mcp = FastMCP(name="metadata-test")
        register_fabrication_tools(mcp, get_bridge)

        tools = await mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}
        sketch_tool = by_name["create_sketch_geometry"]
        sketch_schema = sketch_tool.inputSchema["properties"]["sketch"]
        extrude_schema = by_name["execute_extrude"].inputSchema
        protocol_meta = sketch_tool.meta

        assert sketch_schema["description"]
        assert "HistCAD entity dict" in sketch_schema["description"]
        assert protocol_meta is not None
        assert protocol_meta["argument_examples"]
        assert "histcad_sketch_entity_reference" in protocol_meta
        assert (
            "large_arc"
            not in protocol_meta["histcad_sketch_entity_reference"]["entities"][
                "elliptical_arc"
            ]["schema"]
        )
        assert (
            "positive sketch normal"
            in extrude_schema["properties"]["towards"]["description"]
        )
        assert extrude_schema["properties"]["extrusion_mode"]["enum"] == [
            "auto",
            "parametric_sketch",
            "robust_face",
        ]

    @pytest.mark.asyncio
    async def test_validate_primitive_plan_checks_known_tools_and_complete_args(
        self, register_tools: dict
    ) -> None:
        """PrimitivePlan validation checks tool names and complete step args."""
        valid_result = await register_tools["validate_primitive_plan"](
            {
                "plan_level": "L3",
                "steps": [
                    {
                        "tool_name": "create_coordinate_system",
                        "args": {
                            "euler_angles": [0.0, 0.0, 0.0],
                            "translation": [0.0, 0.0, 0.0],
                        },
                    }
                ],
            }
        )
        assert valid_result["valid"] is True

        partial_result = await register_tools["validate_primitive_plan"](
            {
                "plan_level": "L2",
                "steps": [
                    {
                        "tool_name": "create_sketch_geometry",
                        "args": {"sketch_name": "BlockProfile"},
                        "missing_args": ["sketch", "coordinate_system_name"],
                    }
                ],
            }
        )
        assert partial_result["valid"] is True

        invalid_result = await register_tools["validate_primitive_plan"](
            {
                "plan_level": "L3",
                "steps": [
                    {
                        "tool_name": "create_coordinate_system",
                        "args": {"euler_angles": [0.0, 0.0, 0.0]},
                    },
                    {"tool_name": "unknown_tool", "args": {}},
                ],
            }
        )
        assert invalid_result["valid"] is False
        messages = "\n".join(error["message"] for error in invalid_result["errors"])
        assert "required arg 'translation' is missing" in messages
        assert "unknown tool 'unknown_tool'" in messages

        invalid_enum_result = await register_tools["validate_primitive_plan"](
            {
                "plan_level": "L3",
                "steps": [
                    {
                        "tool_name": "execute_extrude",
                        "args": {
                            "sketch_name": "Sketch",
                            "towards": 1.0,
                            "extrusion_mode": "NewBody",
                        },
                    }
                ],
            }
        )
        assert invalid_enum_result["valid"] is False
        enum_messages = "\n".join(
            error["message"] for error in invalid_enum_result["errors"]
        )
        assert "invalid value 'NewBody'" in enum_messages

        internal_constraint_result = await register_tools["validate_primitive_plan"](
            {
                "plan_level": "L3",
                "steps": [
                    {
                        "tool_name": "apply_sketch_constraints",
                        "args": {
                            "sketch_name": "Sketch",
                            "constraints": {
                                "c1": {"type": "Horizontal", "references": [0]}
                            },
                        },
                    }
                ],
            }
        )
        assert internal_constraint_result["valid"] is False
        assert "FreeCAD internal" in internal_constraint_result["errors"][0]["message"]

        unknown_constraint_result = await register_tools["validate_primitive_plan"](
            {
                "plan_level": "L3",
                "steps": [
                    {
                        "tool_name": "apply_sketch_constraints",
                        "args": {
                            "sketch_name": "Sketch",
                            "constraints": {
                                "DistanceX": [["origin", "circle_1.center"]]
                            },
                        },
                    }
                ],
            }
        )
        assert unknown_constraint_result["valid"] is False
        assert "unknown HistCAD constraint type" in "\n".join(
            error["message"] for error in unknown_constraint_result["errors"]
        )

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
    async def test_create_coordinate_system_accepts_attached_parametric_contract(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        mock_bridge.execute_python.return_value = self._success(
            {
                "cs_name": "HoleTopPlane",
                "attachment_support": {"target": "BlockSolid"},
                "bound_params": [],
                "success": True,
            }
        )
        result = await register_tools["create_coordinate_system"](
            euler_angles=[0, 0, 0],
            translation=[0, 0, 10],
            name="HoleTopPlane",
            attachment_support={"target": "BlockSolid"},
            param_aliases={"translation.z": "block_thickness"},
        )
        assert result["attachment_support"] == {"target": "BlockSolid"}
        code = mock_bridge.execute_python.call_args.args[0]
        assert "AttachmentSupport" in code
        assert "AttachmentOffset.Base.z" in code

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
            coordinate_system_name="XY_Base",
        )
        assert result["sketch_name"] == "Sketch001"
        assert result["geometry_count"]["lines"] == 2
        assert result["profile"]["closed"] is True
        assert result["dof_remaining"] == 4

    @pytest.mark.asyncio
    async def test_create_sketch_geometry_uses_named_coordinate_system(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """A sketch uses the existing named coordinate-system contract."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "sketch_name": "Sketch002",
                "entity_index_map": {},
                "dof_remaining": 2,
                "success": True,
            }
        )
        result = await register_tools["create_sketch_geometry"](
            sketch={"circle_1": {"center": [0, 0], "radius": 5}},
            coordinate_system_name="HoleTopPlane",
        )
        assert result["sketch_name"] == "Sketch002"

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

    def test_apply_sketch_constraints_has_alias_solver_guard(self) -> None:
        """Source guards expression binding when sketch solving is unhealthy."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "binding_guard_reasons" in source
        assert '"role": "unbound_solver_guard"' in source
        assert "pre_bind_solve_status" in source
        assert "profile_open" in source

    def test_apply_sketch_constraints_uses_diameter_constraint_for_diameter(
        self,
    ) -> None:
        """Diameter constraint entries create Diameter constraints."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert 'elif ctype == "Diameter":' in source
        assert 'Sketcher.Constraint("Diameter", i, val)' in source
        assert 'c = Sketcher.Constraint("Radius", i, val / 2.0)' not in source

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
    async def test_execute_extrude_reports_unbound_fallback(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """Robust-face fallback returns unbound param diagnostics."""
        mock_bridge.execute_python.return_value = self._success(
            {
                "feature_name": "Solid",
                "extrusion_mode_used": "robust_face",
                "fallback_reason": "zero_volume_0.0",
                "bound_params": [
                    {
                        "alias": "box_height",
                        "cell": None,
                        "property": None,
                        "role": "unbound_fallback",
                        "diagnostic": "zero_volume_0.0",
                    }
                ],
                "success": True,
            }
        )
        result = await register_tools["execute_extrude"](
            sketch_name="Sketch001",
            towards=30.0,
            param_aliases={"towards": "box_height"},
        )
        assert result["bound_params"][0]["role"] == "unbound_fallback"
        assert result["bound_params"][0]["property"] is None

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
    async def test_execute_operation_plan_summarizes_binding_diagnostics(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """Ordered batch execution exposes sketch and feature binding diagnostics."""
        mock_bridge.execute_python.side_effect = [
            self._success({"cs_name": "XY_Base", "success": True}),
            self._success({"sketch_name": "PlateProfile", "success": True}),
            self._success(
                {
                    "dof_after": -1,
                    "solve_status": 1,
                    "input_constraint_count": 1,
                    "applied_count": 0,
                    "failed_or_skipped_constraints": [
                        {
                            "phase": "remaining",
                            "source": "input",
                            "input": {
                                "type": "Length",
                                "entry_index": 0,
                                "entry": [
                                    "line_1",
                                    {
                                        "length": 80,
                                        "alias": "plate_length",
                                    },
                                ],
                            },
                            "reason": "apply_exception",
                            "exception_type": "ValueError",
                            "message": "mock failure",
                        }
                    ],
                    "purged_redundant": [
                        {
                            "freecad_index": 1,
                            "freecad_type": "Distance",
                            "input": {
                                "type": "Length",
                                "entry_index": 0,
                                "entry": [
                                    "line_1",
                                    {
                                        "length": 80,
                                        "alias": "plate_length",
                                    },
                                ],
                            },
                            "source": "input",
                            "adapter_reason": None,
                            "reason": "solver_redundant",
                            "removed_aliases": ["plate_length"],
                        }
                    ],
                    "redundant": [],
                    "conflicting": [],
                    "bound_params": [
                        {
                            "alias": "plate_length",
                            "cell": None,
                            "property": "Constraints[0]",
                            "role": "unbound_solver_guard",
                            "diagnostic": "solve_status=1",
                        }
                    ],
                    "success": True,
                }
            ),
            self._success(
                {
                    "feature_name": "PlateSolid",
                    "body_name": "PlateSolid",
                    "bound_params": [
                        {
                            "alias": "thickness",
                            "cell": "A1",
                            "property": "LengthFwd",
                            "role": "thickness",
                            "value": 6.0,
                            "unit": "mm",
                        }
                    ],
                    "success": True,
                }
            ),
            self._success(
                {
                    "bounding_box": {"x_min": 0, "x_max": 80},
                    "volume": 480.0,
                    "success": True,
                }
            ),
            self._success(
                {
                    "params": [{"alias": "thickness", "value": 6.0, "cell": "A1"}],
                    "spreadsheet_name": "FabricationParams",
                }
            ),
        ]
        plan = {
            "operations": [
                {
                    "tool_name": "create_coordinate_system",
                    "args": {
                        "euler_angles": [0, 0, 0],
                        "translation": [0, 0, 0],
                        "name": "XY_Base",
                    },
                },
                {
                    "tool_name": "create_sketch_geometry",
                    "args": {
                        "sketch_name": "PlateProfile",
                        "coordinate_system_name": "XY_Base",
                        "sketch": {
                            "line_1": {"start": [0, 0], "end": [80, 0]},
                        },
                    },
                },
                {
                    "tool_name": "apply_sketch_constraints",
                    "args": {
                        "sketch_name": "PlateProfile",
                        "constraints": {
                            "Length": [
                                [
                                    "line_1",
                                    {
                                        "length": 80,
                                        "alias": "plate_length",
                                    },
                                ]
                            ]
                        },
                    },
                },
                {
                    "tool_name": "execute_extrude",
                    "args": {
                        "sketch_name": "PlateProfile",
                        "towards": 6.0,
                        "opposite": 0.0,
                        "param_aliases": {"towards": "thickness"},
                        "feature_name": "PlateSolid",
                    },
                },
            ]
        }
        result = await register_tools["execute_operation_plan"](plan)
        diagnostics = result["parametric_binding_diagnostics"]
        assert diagnostics[0]["alias"] == "plate_length"
        assert diagnostics[0]["role"] == "unbound_solver_guard"
        assert diagnostics[0]["bound"] is False
        assert diagnostics[1]["alias"] == "thickness"
        assert diagnostics[1]["bound"] is True
        assert result["operation_count"] == 4
        sketch_result = result["sketch_constraint_results"][0]
        assert sketch_result["input_constraint_count"] == 1
        assert sketch_result["applied_count"] == 0
        assert sketch_result["failed_or_skipped_constraints"][0]["phase"] == "remaining"
        assert sketch_result["purged_redundant"][0]["input"]["type"] == "Length"
        assert sketch_result["redundant"] == []
        assert sketch_result["conflicting"] == []

    @pytest.mark.asyncio
    async def test_validate_operation_plan_accepts_json_string_and_path(
        self, register_tools: dict, tmp_path
    ) -> None:
        """validate_operation_plan accepts dict-equivalent JSON inputs."""
        plan: dict[str, Any] = {"operations": []}
        from_json = await register_tools["validate_operation_plan"](json.dumps(plan))
        assert from_json["valid"] is True

        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        from_path = await register_tools["validate_operation_plan"](
            plan_path=str(plan_path)
        )
        assert from_path["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_operation_plan_reports_bad_json(
        self, register_tools: dict
    ) -> None:
        """Invalid JSON is reported as validation feedback, not CAD execution."""
        result = await register_tools["validate_operation_plan"]("{bad")
        assert result["valid"] is False
        assert "failed to parse plan" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_execute_operation_plan_accepts_plan_path(
        self, register_tools: dict, mock_bridge: AsyncMock, tmp_path
    ) -> None:
        """execute_operation_plan can load plan JSON directly from a file."""
        mock_bridge.execute_python.return_value = self._success(
            {"params": [], "spreadsheet_name": None}
        )
        plan: dict[str, Any] = {"operations": []}
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        result = await register_tools["execute_operation_plan"](
            plan_path=str(plan_path),
            doc_name="Doc",
        )

        assert result["success"] is True
        assert result["operation_count"] == 0

    @pytest.mark.asyncio
    async def test_execute_operation_plan_rejects_missing_plan_path_without_cad(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """Bad plan_path fails before calling the FreeCAD bridge."""
        with pytest.raises(ValueError, match="plan_path does not exist"):
            await register_tools["execute_operation_plan"](
                plan_path="/no/such/plan.json",
                doc_name="Doc",
            )
        mock_bridge.execute_python.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_operation_plan_runs_explicit_boolean_operation(
        self, register_tools: dict, mock_bridge: AsyncMock
    ) -> None:
        """Ordered batch executor dispatches explicit boolean operations."""
        mock_bridge.execute_python.side_effect = [
            self._success(
                {
                    "feature_name": "Intersection",
                    "type_id": "Part::Common",
                    "operation": "Intersect",
                    "base_object_name": "BaseSolid",
                    "tool_object_name": "ToolSolid",
                    "dependency_preserved": True,
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
            "operations": [
                {
                    "tool_name": "execute_boolean",
                    "args": {
                        "base_object_name": "BaseSolid",
                        "tool_object_name": "ToolSolid",
                        "operation": "Intersect",
                        "result_name": "Intersection",
                    },
                }
            ]
        }

        result = await register_tools["execute_operation_plan"](plan)

        assert result["feature_names"] == ["Intersection"]
        assert result["body_name"] == "Intersection"
        assert result["operation_count"] == 1
        boolean_code = mock_bridge.execute_python.call_args_list[0].args[0]
        assert "BaseSolid" in boolean_code
        assert "ToolSolid" in boolean_code

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
                "hole_features": [
                    {
                        "radius": 1.5,
                        "axis": [0.0, 1.0, 0.0],
                        "axis_point": [5.9, 0.0, 0.0],
                        "approx_depth": 6.0,
                        "faces": ["Face3"],
                    }
                ],
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
        assert result["hole_features"][0]["radius"] == 1.5
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
            "c = _fix_constraint(i)"
            in source.split('elif ctype == "Fix":')[1].split(
                'elif ctype == "Midpoint":'
            )[0]
        )

    def test_point_fix_lowers_to_origin_distances(self) -> None:
        """Point-level Fix avoids version-sensitive FreeCAD Lock/Block overloads."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        fix_branch = source.split('elif ctype == "Fix":')[1].split(
            'elif ctype == "Midpoint":'
        )[0]
        assert "_apply_point_fix_as_distances" in source
        assert "point_fix_lowered_to_distance" in source
        assert '["origin", ref, {"length": length, "direction": direction}]' in source
        assert (
            "_apply_point_fix_as_distances(entry, i, p, entry_index=entry_index)"
            in fix_branch
        )
        assert "return" in fix_branch

    def test_directed_vertical_distance_from_ground_truth(self) -> None:
        """VERTICAL Distance uses signed delta from sketch_helpers."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "_SKETCH_HELPER_CODE" in source
        assert "directed_axis_distance" in source
        assert "ground_truth=ground_truth" in source

    def test_negative_directed_distance_swaps_constraint_endpoints(self) -> None:
        """FreeCAD Sketcher preserves direction by endpoint order, not negative values."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        distance_block = source.split("def _apply_distance_entry", 1)[1].split(
            "def _apply_constraint_entry", 1
        )[0]

        assert "if float(val) < 0.0:" in distance_block
        assert "ci1, cp1, ci2, cp2 = i2, p2, i1, p1" in distance_block
        assert (
            'Sketcher.Constraint("DistanceX", ci1, cp1, ci2, cp2, val)'
            in distance_block
        )
        assert (
            'Sketcher.Constraint("DistanceY", ci1, cp1, ci2, cp2, val)'
            in distance_block
        )

    def test_directional_distance_resolves_line_refs_to_point_anchors(self) -> None:
        """HORIZONTAL/VERTICAL Distance lowers whole-line refs before DistanceX/Y."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "def _directional_distance_anchor" in source
        assert "def _select_line_endpoint_for_direction" in source

        distance_block = source.split("def _apply_distance_entry", 1)[1].split(
            "def _apply_constraint_entry", 1
        )[0]
        assert "ref1 = _directional_distance_anchor(" in distance_block
        assert "ref2 = _directional_distance_anchor(" in distance_block
        assert "i1, p1 = _resolve_point(ref1)" in distance_block
        assert "i2, p2 = _resolve_point(ref2)" in distance_block
        assert "live_a=_live_xy(ref1)" in distance_block
        assert "live_b=_live_xy(ref2)" in distance_block

    def test_redundant_purge_records_removed_dimension_aliases(self) -> None:
        """Solver-redundant rows are purged even when they carried aliases."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        purge_block = source.split("def _purge_redundant_sketch_constraints", 1)[
            1
        ].split("purged_redundant =", 1)[0]

        assert "removed_aliases" in purge_block
        assert '_binding["_removed"] = True' in purge_block
        assert "def _is_bound_dimension_pos" not in purge_block
        assert 'if _c.Type != "Coincident":' not in purge_block

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
        assert '"constraint_catalog_summary": _constraint_catalog_summary' in source
        assert '"applied_constraints":     _catalog' in source
        assert '"redundant":               _redundant_entries' in source
        assert (
            '"failed_or_skipped_constraints": failed_or_skipped_constraints' in source
        )
        assert '"purged_redundant":        purged_redundant' in source
        assert '"applied_log":             applied_log' in source
        assert '"failed_or_skipped_count": len(failed_or_skipped_constraints)' in source
        assert '"purged_redundant_count": len(purged_redundant)' in source
        assert '"lossless": (' in source

    def test_apply_sketch_constraints_records_skipped_and_purge_input(self) -> None:
        """Skipped add attempts and purged rows retain input mapping."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert "failed_or_skipped_constraints = []" in source
        assert "def _record_skipped_constraint(" in source
        assert 'phase="topology"' in source
        assert 'phase="orientation_stabilization"' in source
        assert 'phase="remaining"' in source
        assert '"reason": reason' in source
        assert '"exception_type"] = type(exc).__name__' in source
        assert "_base = applied_log[_pos] if _pos < len(applied_log) else {}" in source
        assert '"input": _base.get("input")' in source
        assert '"source": _base.get("source")' in source
        assert '"adapter_reason": _base.get("adapter_reason")' in source

    def test_execute_operation_plan_returns_stable_summary_fields(self) -> None:
        """Agent harness depends on structured execute_operation_plan summary."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        execute_block = source.split("async def execute_operation_plan", 1)[1]
        for field in [
            '"body_name": last_body_name',
            '"feature_names": feature_names',
            '"tunable_params": tunable',
            '"bounding_box": snapshot.get("bounding_box", {})',
            '"volume": snapshot.get("volume", 0.0)',
            '"sketch_constraint_results": sketch_constraint_results',
            '"parametric_binding_diagnostics": parametric_binding_diagnostics',
            '"steps_completed": steps_completed',
            '"operation_count": len(op_plan.operations)',
            '"success": True',
        ]:
            assert field in execute_block
        for field in [
            '"input_constraint_count": result.get("input_constraint_count")',
            '"applied_count": result.get("applied_count")',
            '"failed_or_skipped_constraints": result.get(',
            '"purged_redundant": result.get("purged_redundant", [])',
            '"redundant": result.get("redundant", [])',
            '"conflicting": result.get("conflicting", [])',
        ]:
            assert field in execute_block

    def test_directional_distance_feedback_is_exposed(self) -> None:
        """Directional Distance execution exposes actual FreeCAD constraint type."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        assert 'Sketcher.Constraint("DistanceY", ci1, cp1, ci2, cp2, val)' in source
        assert 'Sketcher.Constraint("DistanceX", ci1, cp1, ci2, cp2, val)' in source
        assert '"freecad_type": freecad_type' in source
        assert '"sketch_constraint_results": sketch_constraint_results' in source

    def test_body_snapshot_reports_hole_features(self) -> None:
        """Snapshot includes structured cylindrical hole evidence."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        snapshot_block = source.split("async def get_body_snapshot", 1)[1].split(
            "async def validate_primitive_plan", 1
        )[0]

        assert "hole_features = []" in snapshot_block
        assert '"hole_features": hole_features' in snapshot_block
        assert '"approx_depth"' in snapshot_block

    def test_geometry_volumes_use_scientific_precision_not_four_digit_rounding(
        self,
    ) -> None:
        """Small tool solids should not be summarized as 0.0 by rounding."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )

        assert "float(_value):.12e" in source
        assert '"volume_mm3_display"' in source
        assert '"volume_display"' in source
        assert '"volume_mm3":          round(volume, 4)' not in source
        assert '"volume_mm3":    round(tool_shape.Volume, 4)' not in source

    def test_finishing_tools_support_partdesign_and_shape_objects(self) -> None:
        """Fillet/chamfer contracts support both Body and generic shape outputs."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        fillet_block = source.split("async def feature_fillet", 1)[1].split(
            "async def feature_chamfer", 1
        )[0]
        chamfer_block = source.split("async def feature_chamfer", 1)[1].split(
            "# ------------------------------------------------------------------\n"
            "    # Group F",
            1,
        )[0]

        assert 'body.TypeId == "PartDesign::Body"' in fillet_block
        assert 'doc.addObject("Part::Fillet", "Fillet")' in fillet_block
        assert 'getattr(body, "Tip", None) or body' in fillet_block
        assert "Fillet failed validation" in fillet_block
        assert "Fillet produced an empty shape" in fillet_block
        assert '"object_name":        fillet.Name' in fillet_block

        assert 'body.TypeId == "PartDesign::Body"' in chamfer_block
        assert 'doc.addObject("Part::Chamfer", "Chamfer")' in chamfer_block
        assert 'getattr(body, "Tip", None) or body' in chamfer_block
        assert "Chamfer failed validation" in chamfer_block
        assert "Chamfer produced an empty shape" in chamfer_block
        assert '"object_name":        chamfer.Name' in chamfer_block

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

    def test_coordinate_system_uses_canonical_named_lcs_contract(self) -> None:
        """Sketch creation has no inline or face-attachment plane path."""
        from pathlib import Path

        source = Path("src/freecad_mcp/tools/fabrication.py").read_text(
            encoding="utf-8"
        )
        sketch_block = source.split("async def create_sketch_geometry", 1)[1].split(
            "async def parse_freecad_sketch", 1
        )[0]
        assert "coordinate_system_name: str" in sketch_block
        assert "cs_inline" not in sketch_block
        assert "FlatFace" not in sketch_block


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
        """L connector template returns an ordered OperationPlan with tunable aliases."""
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

        operations = result["operations"]
        assert [op["tool_name"] for op in operations] == [
            "create_coordinate_system",
            "create_sketch_geometry",
            "apply_sketch_constraints",
            "execute_extrude",
        ]
        sketch = operations[1]["args"]["sketch"]
        constraints = operations[2]["args"]["constraints"]
        assert len(sketch) == 6
        assert constraints["Length"][0][1]["alias"] == "arm_x_length"
        assert constraints["Length"][1][1]["alias"] == "arm_x_width"
        assert constraints["Length"][2][1]["alias"] == "arm_y_width"
        assert constraints["Length"][3][1]["alias"] == "arm_y_length"
        assert operations[3]["args"]["param_aliases"] == {"towards": "thickness"}
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
        assert result["operations"][3]["args"]["towards"] == 6.0

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
    async def test_validate_intent_accepts_json_string_and_path(
        self, mock_mcp: MagicMock, tmp_path
    ) -> None:
        """validate_intent accepts dict-equivalent JSON inputs."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        intent = {
            "template_name": "l_connector",
            "slots": {
                "arm_x_length": 50.0,
                "arm_y_length": 80.0,
                "width": 30.0,
            },
        }

        from_json = await mock_mcp._registered_tools["validate_intent"](
            json.dumps(intent)
        )
        assert from_json["valid"] is True

        intent_path = tmp_path / "intent.json"
        intent_path.write_text(json.dumps(intent), encoding="utf-8")
        from_path = await mock_mcp._registered_tools["validate_intent"](
            intent_path=str(intent_path)
        )
        assert from_path["valid"] is True

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
        assert package["operation_plan"]["metadata"]["template"] == "l_connector"
        assert package["template_provenance"]["deterministic"] is True
        assert package["compile_diagnostics"]["ok"] is True

    @pytest.mark.asyncio
    async def test_compile_intent_accepts_json_string_and_path(
        self, mock_mcp: MagicMock, tmp_path
    ) -> None:
        """compile_intent accepts JSON string and file-backed IntentSpec."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        intent = {
            "template_name": "l_connector",
            "slots": {
                "arm_x_length": 50.0,
                "arm_y_length": 80.0,
                "width": 30.0,
            },
        }

        from_json = await mock_mcp._registered_tools["compile_intent"](
            json.dumps(intent)
        )
        assert from_json["compile_diagnostics"]["ok"] is True

        intent_path = tmp_path / "intent.json"
        intent_path.write_text(json.dumps(intent), encoding="utf-8")
        from_path = await mock_mcp._registered_tools["compile_intent"](
            intent_path=str(intent_path)
        )
        assert from_path["operation_plan"]["metadata"]["template"] == "l_connector"

    @pytest.mark.asyncio
    async def test_compile_intent_writes_compact_file_backed_handoff(
        self, mock_mcp: MagicMock, tmp_path
    ) -> None:
        """A requested artifact path keeps the OperationPlan out of MCP text."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        plan_path = tmp_path / "artifacts" / "compiled-plan.json"
        result = await mock_mcp._registered_tools["compile_intent"](
            {
                "template_name": "l_connector",
                "slots": {"arm_x_length": 50.0, "arm_y_length": 80.0, "width": 30.0},
            },
            output_plan_path=str(plan_path),
        )

        assert "operation_plan" not in result
        assert result["operation_plan_path"] == str(plan_path.resolve())
        assert result["operation_plan_summary"]["operation_count"] > 0
        written = json.loads(plan_path.read_text(encoding="utf-8"))
        assert written["metadata"]["template"] == "l_connector"

    @pytest.mark.asyncio
    async def test_compile_l_connector_two_top_holes_prompt_contract(
        self, mock_mcp: MagicMock, tmp_path
    ) -> None:
        """The natural-language fixture maps to two top-face hole groups."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        plan_path = tmp_path / "artifacts" / "l-connector-top-holes.json"
        intent = {
            "template_name": "l_connector",
            "slots": {
                "arm_x_length": 50.0,
                "arm_y_length": 80.0,
                "width": 30.0,
                "thickness": 10.0,
            },
            "slot_bindings": [
                "arm_x_width = width",
                "arm_y_width = width",
            ],
            "hole_groups": [
                {
                    "face_id": "arm_x_top",
                    "pattern": "rectangular",
                    "count_u": 1,
                    "count_v": 1,
                    "diameter": 6.0,
                    "margin_u": 10.0,
                    "margin_v": 10.0,
                },
                {
                    "face_id": "arm_y_top",
                    "pattern": "rectangular",
                    "count_u": 1,
                    "count_v": 1,
                    "diameter": 6.0,
                    "margin_u": 10.0,
                    "margin_v": 10.0,
                },
            ],
            "assumptions": ["unit=mm", "through holes", "top faces only"],
        }

        result = await mock_mcp._registered_tools["compile_intent"](
            intent,
            output_plan_path=str(plan_path),
        )

        assert "operation_plan" not in result
        assert result["operation_plan_path"] == str(plan_path.resolve())
        assert result["operation_plan_summary"]["tool_counts"]["execute_boolean"] >= 2
        written = json.loads(plan_path.read_text(encoding="utf-8"))
        assert written["metadata"]["template"] == "l_connector"
        assert (
            written["metadata"]["intent_spec"]["hole_groups"][0]["face_id"]
            == "arm_x_top"
        )
        assert (
            written["metadata"]["intent_spec"]["hole_groups"][1]["face_id"]
            == "arm_y_top"
        )
        assert all(
            group["diameter"] == 6.0
            for group in written["metadata"]["intent_spec"]["hole_groups"]
        )

    @pytest.mark.asyncio
    async def test_compile_intent_reports_bad_path(self, mock_mcp: MagicMock) -> None:
        """compile_intent reports file input errors as compile diagnostics."""
        from freecad_mcp.tools.templates import register_template_tools

        async def get_bridge() -> AsyncMock:
            return AsyncMock()

        register_template_tools(mock_mcp, get_bridge)
        result = await mock_mcp._registered_tools["compile_intent"](
            intent_path="/no/such/intent.json"
        )
        assert result["operation_plan"] is None
        assert "intent_path does not exist" in result["compile_diagnostics"]["error"]
