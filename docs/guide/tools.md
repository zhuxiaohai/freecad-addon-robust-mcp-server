# Tools Reference

The FreeCAD Robust MCP Server provides 150+ tools for CAD operations. This page provides a quick reference organized by category.

!!! tip "Transaction Support"
    All MCP operations are wrapped in FreeCAD transactions for undo support. After any operation, you can use `undo` to revert changes. This makes it safe for AI agents to experiment and recover from mistakes.

For detailed documentation including parameters and examples, see [MCP Tools Reference](../MCP_TOOLS_REFERENCE.md).

---

## Tool Categories

| Category                                        | Tools | Description                          |
| ----------------------------------------------- | ----- | ------------------------------------ |
| [Execution](#execution-tools)                   | 5     | Python execution, debugging          |
| [Documents](#document-tools)                    | 7     | Document management                  |
| [Primitives](#primitive-tools)                  | 8     | Basic 3D shapes                      |
| [Part Workbench](#part-workbench-tools)         | 30    | Shape creation, operations, queries  |
| [Objects](#object-tools)                        | 12    | Object manipulation                  |
| [PartDesign](#partdesign-tools)                 | 28    | Parametric modeling                  |
| [Sketcher Geometry](#sketcher-geometry-tools)   | 18    | Sketch shapes and operations         |
| [Sketcher Constraints](#sketcher-constraints)   | 17    | Sketch constraints and dimensions    |
| [Spreadsheet](#spreadsheet-tools)               | 10    | Parametric design with spreadsheets  |
| [Draft ShapeString](#draft-tools)               | 6     | 3D text geometry for emboss/engrave  |
| [Validation](#validation-tools)                 | 4     | Object/document health checking      |
| [View & Display](#view-tools)                   | 11    | View control, screenshots (GUI only) |
| [Export/Import](#export-tools)                  | 7     | File format conversion               |
| [Macros](#macro-tools)                          | 6     | Macro management                     |
| [Utility](#utility-tools)                       | 7     | Undo/redo, parts library             |

---

## Execution Tools

| Tool                         | Description                                |
| ---------------------------- | ------------------------------------------ |
| `execute_python`             | Execute arbitrary Python in FreeCAD        |
| `get_freecad_version`        | Get FreeCAD version and build info         |
| `get_connection_status`      | Check MCP bridge connection                |
| `get_console_output`         | Get recent console output                  |
| `get_mcp_server_environment` | Get Robust MCP Server environment info     |

---

## Document Tools

| Tool                  | Description                   |
| --------------------- | ----------------------------- |
| `list_documents`      | List all open documents       |
| `get_active_document` | Get currently active document |
| `create_document`     | Create a new document         |
| `open_document`       | Open an existing .FCStd file  |
| `save_document`       | Save a document               |
| `close_document`      | Close a document              |
| `recompute_document`  | Recompute all features        |

---

## Primitive Tools

| Tool              | Description                  |
| ----------------- | ---------------------------- |
| `create_box`      | Create a parametric box      |
| `create_cylinder` | Create a parametric cylinder |
| `create_sphere`   | Create a parametric sphere   |
| `create_cone`     | Create a parametric cone     |
| `create_torus`    | Create a torus (donut)       |
| `create_wedge`    | Create a tapered wedge       |
| `create_helix`    | Create a helix curve         |
| `create_object`   | Create any object by type ID |

---

## Part Workbench Tools

The Part workbench provides direct shape creation and manipulation tools using OpenCASCADE geometry.

### Shape Creation

| Tool                 | Description                              |
| -------------------- | ---------------------------------------- |
| `part_make_line`     | Create a line between two points         |
| `part_make_wire`     | Create a wire from connected edges       |
| `part_make_face`     | Create a face from a closed wire         |
| `part_make_shell`    | Create a shell from faces                |
| `part_make_solid`    | Create a solid from a closed shell       |
| `part_make_compound` | Create a compound from multiple shapes   |
| `part_make_polygon`  | Create a polygon wire from points        |
| `part_make_circle`   | Create a circle edge or wire             |
| `part_make_ellipse`  | Create an ellipse edge                   |
| `part_make_b_spline` | Create a B-spline curve from points      |

### Shape Operations

| Tool            | Description                                |
| --------------- | ------------------------------------------ |
| `part_fuse`     | Boolean union (combine shapes)             |
| `part_cut`      | Boolean subtraction (cut one from another) |
| `part_common`   | Boolean intersection (common volume)       |
| `part_fillet`   | Add fillets to shape edges                 |
| `part_chamfer`  | Add chamfers to shape edges                |
| `part_extrude`  | Extrude a shape along a vector             |
| `part_revolve`  | Revolve a shape around an axis             |
| `part_loft`     | Create a loft through multiple profiles    |
| `part_sweep`    | Sweep a profile along a path               |
| `part_offset`   | Create an offset shell of a shape          |
| `part_slice`    | Slice a shape with a plane                 |
| `part_section`  | Create intersection curve of two shapes    |

### Shape Queries

| Tool                      | Description                           |
| ------------------------- | ------------------------------------- |
| `part_check_shape`        | Validate shape geometry               |
| `part_get_faces`          | Get all faces of a shape              |
| `part_get_edges`          | Get all edges of a shape              |
| `part_get_vertices`       | Get all vertices of a shape           |
| `part_measure_distance`   | Measure distance between shapes       |
| `part_measure_angle`      | Measure angle between faces/edges     |
| `part_get_center_of_mass` | Calculate center of mass              |
| `part_get_bounding_box`   | Get axis-aligned bounding box         |

---

## Object Tools

| Tool                | Description                      |
| ------------------- | -------------------------------- |
| `list_objects`      | List objects in a document       |
| `inspect_object`    | Get detailed object information  |
| `edit_object`       | Modify object properties         |
| `delete_object`     | Delete an object                 |
| `boolean_operation` | Union, cut, or intersect objects |
| `set_placement`     | Set position and rotation        |
| `rotate_object`     | Rotate around an axis            |
| `scale_object`      | Scale uniformly or non-uniformly |
| `copy_object`       | Create a copy                    |
| `mirror_object`     | Mirror across a plane            |
| `get_selection`     | Get selected objects (GUI)       |
| `set_selection`     | Select objects (GUI)             |
| `clear_selection`   | Clear selection (GUI)            |

---

## PartDesign Tools

### Bodies and Sketches

| Tool                     | Description                     |
| ------------------------ | ------------------------------- |
| `create_partdesign_body` | Create a PartDesign body        |
| `create_sketch`          | Create a sketch on a plane/face |

### Datum Features

| Tool                             | Description                           |
| -------------------------------- | ------------------------------------- |
| `partdesign_create_datum_point`  | Create a datum point for construction |
| `partdesign_create_datum_line`   | Create a datum line/axis              |
| `partdesign_create_datum_plane`  | Create a datum plane for sketches     |

### Basic Sketch Geometry

| Tool                   | Description             |
| ---------------------- | ----------------------- |
| `add_sketch_rectangle` | Add rectangle to sketch |
| `add_sketch_circle`    | Add circle to sketch    |
| `add_sketch_line`      | Add line to sketch      |
| `add_sketch_arc`       | Add arc to sketch       |
| `add_sketch_point`     | Add point to sketch     |

### Additive Features

| Tool                              | Description                    |
| --------------------------------- | ------------------------------ |
| `pad_sketch`                      | Extrude sketch (additive)      |
| `revolution_sketch`               | Revolve sketch around axis     |
| `loft_sketches`                   | Loft through multiple sketches |
| `sweep_sketch`                    | Sweep profile along path       |
| `partdesign_create_additive_pipe` | Pipe/sweep with auxiliary path |
| `partdesign_create_additive_loft` | Loft with more options         |

### Subtractive Features

| Tool                                 | Description                  |
| ------------------------------------ | ---------------------------- |
| `pocket_sketch`                      | Cut by extruding sketch      |
| `groove_sketch`                      | Cut by revolving sketch      |
| `create_hole`                        | Create parametric holes      |
| `partdesign_create_subtractive_pipe` | Subtractive pipe/sweep       |
| `partdesign_create_subtractive_loft` | Subtractive loft             |

### Dress-up Features

| Tool                          | Description               |
| ----------------------------- | ------------------------- |
| `fillet_edges`                | Add rounded edges         |
| `chamfer_edges`               | Add beveled edges         |
| `partdesign_create_thickness` | Shell/hollow a solid      |
| `partdesign_create_draft`     | Add draft angle to faces  |

### Patterns

| Tool               | Description                 |
| ------------------ | --------------------------- |
| `linear_pattern`   | Repeat feature linearly     |
| `polar_pattern`    | Repeat feature circularly   |
| `mirrored_feature` | Mirror feature across plane |

---

## Sketcher Geometry Tools

Extended sketch geometry and manipulation tools beyond the basic shapes.

### Additional Geometry

| Tool                     | Description                            |
| ------------------------ | -------------------------------------- |
| `sketcher_add_ellipse`   | Add ellipse to sketch                  |
| `sketcher_add_b_spline`  | Add B-spline curve from control points |
| `sketcher_add_polygon`   | Add regular polygon                    |
| `sketcher_add_slot`      | Add slot (rounded rectangle)           |

### Edge Operations

| Tool                    | Description                       |
| ----------------------- | --------------------------------- |
| `sketcher_add_fillet`   | Fillet corner between two lines   |
| `sketcher_add_chamfer`  | Chamfer corner between two lines  |
| `sketcher_trim_curve`   | Trim curve at intersection        |
| `sketcher_extend_curve` | Extend curve to boundary          |
| `sketcher_split_curve`  | Split curve at a point            |
| `sketcher_offset_curve` | Create offset copy of curve       |

### Transformations

| Tool                    | Description                       |
| ----------------------- | --------------------------------- |
| `sketcher_mirror`       | Mirror geometry across axis       |
| `sketcher_array_linear` | Create linear array of geometry   |
| `sketcher_array_polar`  | Create polar array of geometry    |

---

## Sketcher Constraints

Constraints define relationships between sketch geometry elements.

### Geometric Constraints

| Tool                                     | Description                            |
| ---------------------------------------- | -------------------------------------- |
| `sketcher_add_constraint_horizontal`     | Make line horizontal                   |
| `sketcher_add_constraint_vertical`       | Make line vertical                     |
| `sketcher_add_constraint_coincident`     | Make two points coincide               |
| `sketcher_add_constraint_point_on_object`| Place point on line/curve              |
| `sketcher_add_constraint_parallel`       | Make lines parallel                    |
| `sketcher_add_constraint_perpendicular`  | Make lines perpendicular               |
| `sketcher_add_constraint_tangent`        | Make curves tangent                    |
| `sketcher_add_constraint_equal`          | Make lengths/radii equal               |
| `sketcher_add_constraint_symmetric`      | Make points symmetric about a line     |

### Dimensional Constraints

| Tool                               | Description                          |
| ---------------------------------- | ------------------------------------ |
| `sketcher_add_constraint_distance` | Set distance between elements        |
| `sketcher_add_constraint_radius`   | Set circle/arc radius                |
| `sketcher_add_constraint_diameter` | Set circle/arc diameter              |
| `sketcher_add_constraint_angle`    | Set angle between lines              |

### Fix/Lock Constraints

| Tool                            | Description                          |
| ------------------------------- | ------------------------------------ |
| `sketcher_add_constraint_lock`  | Lock point to specific coordinates   |
| `sketcher_add_constraint_block` | Block element from moving            |
| `sketcher_add_constraint_fix`   | Fix point position                   |

### Constraint Management

| Tool                        | Description                          |
| --------------------------- | ------------------------------------ |
| `sketcher_delete_constraint`| Delete a constraint by index         |

---

## Spreadsheet Tools

The Spreadsheet workbench enables parametric design by storing values in cells
that can drive model dimensions through expressions.

| Tool                         | Description                                   |
| ---------------------------- | --------------------------------------------- |
| `spreadsheet_create`         | Create a new Spreadsheet object               |
| `spreadsheet_set_cell`       | Set cell value (number, string, or formula)   |
| `spreadsheet_get_cell`       | Get cell value and computed result            |
| `spreadsheet_set_alias`      | Set alias for parametric references           |
| `spreadsheet_get_aliases`    | Get all aliases in a spreadsheet              |
| `spreadsheet_clear_cell`     | Clear a cell and its alias                    |
| `spreadsheet_bind_property`  | Bind object property to spreadsheet cell      |
| `spreadsheet_get_cell_range` | Get values from a range of cells              |
| `spreadsheet_import_csv`     | Import CSV data into spreadsheet              |
| `spreadsheet_export_csv`     | Export spreadsheet to CSV file                |

!!! tip "Parametric Design Workflow"
    1. Create a spreadsheet with `spreadsheet_create`
    2. Set parameter values with `spreadsheet_set_cell`
    3. Define aliases with `spreadsheet_set_alias` (e.g., "Length", "Width")
    4. Bind to object properties with `spreadsheet_bind_property`
    5. Now changing the spreadsheet cell updates the model automatically!

---

## Draft Tools

The Draft workbench ShapeString tools create 3D text geometry that can be
used for embossing, engraving, or standalone 3D text objects.

| Tool                          | Description                              |
| ----------------------------- | ---------------------------------------- |
| `draft_shapestring`           | Create 3D text geometry from font        |
| `draft_list_fonts`            | List available system fonts              |
| `draft_shapestring_to_sketch` | Convert ShapeString to Sketch            |
| `draft_shapestring_to_face`   | Convert ShapeString to Face              |
| `draft_text_on_surface`       | Emboss or engrave text on a surface      |
| `draft_extrude_shapestring`   | Extrude ShapeString to 3D solid          |

<!-- markdownlint-disable MD046 -->
!!! example "Text Embossing Workflow"
    ```python
    # Create a box
    await create_box(length=100, width=50, height=20)

    # Engrave text on top face
    await draft_text_on_surface(
        text="SAMPLE",
        target_face="Face6",  # Top face
        target_object="Box",
        depth=1.5,
        size=8,
        operation="engrave"  # or "emboss" for raised text
    )
    ```
<!-- markdownlint-enable MD046 -->

---

## Validation Tools

Tools for checking object and document health, with automatic recovery.

| Tool                | Description                                       |
| ------------------- | ------------------------------------------------- |
| `validate_object`   | Check shape validity, error states, recompute     |
| `validate_document` | Check all objects in document, return summary     |
| `undo_if_invalid`   | Validate and auto-undo if invalid objects exist   |
| `safe_execute`      | Execute code with validation and auto-rollback    |

!!! tip "Recovery Pattern"
    Use `undo_if_invalid` after complex operations to automatically recover from failures.

---

## View Tools

!!! warning "GUI Mode Required"
Tools marked with **GUI** only work when FreeCAD is running in GUI mode.

| Tool                    | Mode | Description                        |
| ----------------------- | ---- | ---------------------------------- |
| `get_screenshot`        | GUI  | Capture 3D view screenshot         |
| `set_view_angle`        | Both | Set camera angle                   |
| `fit_all`               | Both | Fit all objects in view            |
| `zoom_in`               | GUI  | Zoom in                            |
| `zoom_out`              | GUI  | Zoom out                           |
| `set_camera_position`   | GUI  | Set exact camera position          |
| `set_object_visibility` | GUI  | Show/hide objects                  |
| `set_display_mode`      | GUI  | Set display mode (wireframe, etc.) |
| `set_object_color`      | GUI  | Change object color                |
| `list_workbenches`      | Both | List available workbenches         |
| `activate_workbench`    | Both | Switch workbench                   |

---

## Export Tools

| Tool          | Description                        |
| ------------- | ---------------------------------- |
| `export_step` | Export to STEP format              |
| `export_stl`  | Export to STL (3D printing)        |
| `export_3mf`  | Export to 3MF (modern 3D printing) |
| `export_obj`  | Export to OBJ format               |
| `export_iges` | Export to IGES format              |
| `import_step` | Import STEP files                  |
| `import_stl`  | Import STL files                   |

---

## Macro Tools

| Tool                         | Description                     |
| ---------------------------- | ------------------------------- |
| `list_macros`                | List available macros           |
| `run_macro`                  | Execute a macro                 |
| `create_macro`               | Create a new macro              |
| `read_macro`                 | Read macro source code          |
| `delete_macro`               | Delete a user macro             |
| `create_macro_from_template` | Create from predefined template |

---

## Utility Tools

| Tool                       | Description                 |
| -------------------------- | --------------------------- |
| `undo`                     | Undo last operation         |
| `redo`                     | Redo undone operation       |
| `get_undo_redo_status`     | Get undo/redo availability  |
| `recompute`                | Force recompute all objects |
| `get_console_log`          | Get console log with levels |
| `list_parts_library`       | List parts library          |
| `insert_part_from_library` | Insert part from library    |

---

## Assembly Connector Tools

> **Requires FreeCAD 1.1+** — uses `Part::LocalCoordinateSystem` for automatic state tracking.

Implements the ArtiCAD connector schema `c = (name, origin ∈ ℝ³, primary_axis ẑ, tertiary_axis x̂, semantic_label)`.
The LLM layer is software-free: only explicit `[x, y, z]` values are passed to connectors.
FreeCAD adapts them internally into `Part::LocalCoordinateSystem` objects that follow part movement.

### Discovery (adapter-specific, returns explicit coordinates)

| Tool                          | Description                                                    |
| ----------------------------- | -------------------------------------------------------------- |
| `get_mounting_features`       | Extract connector candidates from a face — returns `[x,y,z]`   |
| `find_faces_by_constraints`   | Find faces by geometric constraints (normal, area, hole count) |

### Connector Creation and Alignment

| Tool                         | Description                                                                                                                                                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `create_connector`           | Create a `Part::LocalCoordinateSystem` connector on a part. Accepts only `list[float]` inputs, no resolver dicts. Returns `_global` and `_local` coordinates. |
| `align_coordinate_systems`   | Align moving part by matching two connector frames (SE(3) transform)                                                                                          |

### State Observation (geometric object list)

| Tool                              | Description                                                                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `list_assembly_state`             | Returns global-frame coords of all parts and connectors for reflective modeling. `include_local=True` adds local-frame debug fields. |
| `preview_or_highlight_references` | Highlight faces/edges and draw axis preview markers                                                                                  |

### Two-Phase Workflow

```python
# Phase 1: Discover candidates (adapter returns explicit coordinates)
result = await get_mounting_features(object_name="PART_A", face="Face19")
candidate = result["connector_candidates"][0]

# Phase 2: Create connector (LLM passes plain numbers only)
await create_connector(
    object_name="PART_A",
    name="mount_A",
    origin=candidate["origin_local"],       # list[float]
    primary_axis=candidate["primary_axis_local"],
    tertiary_axis=candidate["tertiary_axis_local"],
    semantic_label="top mounting face bolt pattern center",
)

# Phase 3: Align
await align_coordinate_systems(
    moving_object="PART_A", moving_csys="mount_A",
    fixed_object="PART_B",  fixed_csys="mount_B",
)

# Phase 4: Verify (global coords for reflective modeling)
state = await list_assembly_state()
# state["objects"][i]["connectors"][j]["origin_global"] — live world position
```

---

## Fabrication Tools (HistCAD-Style Layer 1 Primitives)

HistCAD-style generic fabrication primitives for parametric solid modeling.
The LLM or RL agent specifies geometry and constraints using kernel-independent
JSON schemas; the adapter translates them deterministically to FreeCAD API calls.

### Three-Layer Architecture

```text
User Intent (L0–L2, natural language)
    │
    ▼
Intent Model (external — Cursor, skill/RAG, Policy 1)
    IntentSpec { template_name, slots, slot_bindings, placement }
    │
    ▼
Layer 2 — Domain Template Tools  (tools/templates/)
    resolve_template(intent) → FabricationPlan   [deterministic]
    │
    ▼
Layer 1 — Generic Fabrication Primitives  (these tools)
    create_coordinate_system → create_sketch_geometry
    → apply_sketch_constraints → execute_extrude / revolve / helix
    → execute_boolean(base_object_name, tool_object_name, Join / Cut / Intersect)
    → feature_fillet / chamfer
    → list_tunable_params / set_tunable_param
```

### Layer 2 Template Tools

| Tool                           | Description                                                                           |
| ------------------------------ | ------------------------------------------------------------------------------------- |
| `resolve_template`             | Compile an IntentSpec dict into a FabricationPlan (deterministic).                    |
| `resolve_l_connector_template` | Build an L connector plan from resolved dimension slots (mm).                         |

IntentSpec schema: ``freecad_mcp.intent.schema.IntentSpec``.  Natural language
parsing happens upstream in the Intent Model — not in template tools.

### Group A: Coordinate System

| Tool                       | Description                                                                            |
| -------------------------- | -------------------------------------------------------------------------------------- |
| `create_coordinate_system` | Create a named datum LCS from Euler angles + translation. Returns `cs_name` for reuse. |

### Group B: Sketch Geometry

| Tool                     | Description                                                                              |
| ------------------------ | ---------------------------------------------------------------------------------------- |
| `create_sketch_geometry` | Create a 2-D sketch from HistCAD entity dict with inline or named coordinate system.     |
| `parse_freecad_sketch`   | Reverse-parse an existing FreeCAD sketch into HistCAD JSON for STEP reverse engineering. |

### Group C: Sketch Constraints

| Tool                       | Description                                                                                                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `check_sketch_constraints` | Dry-run constraint validation without modifying the sketch. Returns `valid` and `estimated_dof_after`.                                                                    |
| `apply_sketch_constraints` | Apply 19 HistCAD types. Returns `dof_after`, etc. Default `orientation_stabilization=False` (opt-in invented axis dims).                                                  |

### Group D: Feature Execution

<!-- markdownlint-disable MD060 -->
| Tool              | Description                                                                                  |
| ----------------- | -------------------------------------------------------------------------------------------- |
| `execute_extrude` | Extrude a sketch into a standalone solid. Defaults to `extrusion_mode="auto"` with robust face fallback.  |
| `execute_boolean` | Create an explicit base/tool boolean; `boolean_mode="auto"` falls back to direct shape boolean if needed. |
| `execute_revolve` | Revolve a sketch into a standalone solid; combine later with explicit `execute_boolean`.                    |
| `execute_helix`   | Sweep a sketch along a helix into a standalone solid; combine later with explicit boolean.                  |
<!-- markdownlint-enable MD060 -->

### Group E: Finishing Features

| Tool              | Description                                                                               |
| ----------------- | ----------------------------------------------------------------------------------------- |
| `feature_fillet`  | Add fillets to edges resolved by 3-D proximity (`near_points`). Bypasses TNP.             |
| `feature_chamfer` | Add chamfers to edges resolved by 3-D proximity (`near_points`).                          |

### Group F: Parametric Controls

| Tool                  | Description                                                                                |
| --------------------- | ------------------------------------------------------------------------------------------ |
| `list_tunable_params` | List all named parameters from `FabricationParams` spreadsheet for frontend slider panel.  |
| `set_tunable_param`   | Update a named parameter and trigger model recompute (backend for slider drag events).     |

### Group G: Observation

| Tool                | Description                                                                                          |
| ------------------- | ---------------------------------------------------------------------------------------------------- |
| `get_body_snapshot` | Get bounding box, volume, feature list, and `edge_samples` of a PartDesign Body for fillet/chamfer.  |

### PrimitivePlan Contract

| Tool                             | Description                                                                                   |
| -------------------------------- | --------------------------------------------------------------------------------------------- |
| `describe_primitive_plan_schema` | Describe the agent-facing primitive tool plan contract for no-template workflows.             |
| `validate_primitive_plan`        | Validate a primitive plan envelope against known tool names and complete args where present.  |
| `validate_fabrication_plan`      | Validate a deterministic `FabricationPlan` batch input before execution without touching CAD. |

### Batch Execution

| Tool                       | Description                                                               |
| -------------------------- | ------------------------------------------------------------------------- |
| `execute_fabrication_plan` | Batch-execute a `FabricationPlan` after validation; accepts `plan_path`.  |

### Standard Workflow Example

```python
# Step 1: Create a datum plane
cs = await create_coordinate_system([0,0,0], [0,0,0], name="XY_Base")

# Step 2: Create sketch geometry
geo = await create_sketch_geometry(
    sketch={
        "line_1": {"start":[0,0], "end":[20,0]},
        "line_2": {"start":[20,0], "end":[20,10]},
        "line_3": {"start":[20,10], "end":[0,10]},
        "line_4": {"start":[0,10], "end":[0,0]},
    },
    coordinate_system_name="XY_Base",
)
# geo["dof_remaining"] → 8 (unconstrained rectangle)

# Step 3: Apply constraints
# orientation_stabilization defaults to False so HistCAD free DOFs stay free.
# Pass orientation_stabilization=True only if segment-flip is observed.
result = await apply_sketch_constraints(geo["sketch_name"], {
    "Coincident": [["line_1.end","line_2.start"],["line_2.end","line_3.start"],
                   ["line_3.end","line_4.start"],["line_4.end","line_1.start"]],
    "Horizontal": ["line_1","line_3"],
    "Vertical":   ["line_2","line_4"],
    "Fix":        ["line_1.start"],
    "Length":     [["line_1","20 mm"],["line_2","10 mm"]],
})
# result["dof_after"] → 0  (fully constrained)

# Step 4: Extrude with named parameter
feat = await execute_extrude(geo["sketch_name"], towards=30.0,
                              param_aliases={"towards": "box_height"})
# feat["extrusion_mode_used"] is "parametric_sketch" or "robust_face"
# param_aliases bind only when the extrusion remains parametric_sketch; robust_face
# returns bound_params entries with role="unbound_fallback".
# For complex HistCAD JSON profiles, pass extrusion_mode="robust_face".

# Step 4b (optional): Boolean-combine with an explicit base and tool
# combined = await execute_boolean(base_object_name="Solid001",
#                                  tool_object_name=feat["feature_name"],
#                                  operation="Intersect")
# combined["boolean_mode_used"] is "parametric" or "static_shape"

# Step 5: Get edge midpoints for filleting
snap = await get_body_snapshot(doc_name=doc_name)
# snap["edge_samples"] → [{near_point:[x,y,z], length:..., curve_type:...}, ...]

# Step 6: Fillet by proximity
await feature_fillet([[10.0, 0.0, 30.0]], radius=2.0)

# Step 7: Frontend slider
await set_tunable_param("box_height", 50.0)  # updates model live
```

For sketch dimensions requested as tunable or linked, use dimension dictionaries
with `alias`, such as `["line_1", {"length": "20 mm", "alias": "box_length"}]`.
`execute_revolve` and `execute_helix` accept `param_aliases` for interface
compatibility, but do not rely on them as automatic bindings unless
`list_tunable_params()` reports the alias.

---

## GUI vs Headless Mode

When running in headless mode, GUI-only tools return structured errors instead of crashing:

```json
{
  "success": false,
  "error": "GUI not available - screenshots cannot be captured in headless mode"
}
```

To check the current mode programmatically:

```python
result = await execute_python("_result_ = FreeCAD.GuiUp")
is_gui_mode = result["result"]
```

---

## Next Steps

- [MCP Tools Reference](../MCP_TOOLS_REFERENCE.md) - Detailed documentation with parameters and examples
- [MCP Resources](resources.md) - Query FreeCAD state via MCP resources
