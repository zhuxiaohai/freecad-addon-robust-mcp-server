# Fabrication MCP Tools

## Pipeline

```text
resolve_template(intent)  →  FabricationPlan dict
execute_fabrication_plan(plan)  →  FreeCAD body
```

## Key tools

| Tool | Layer | Purpose |
| ---- | ----- | ------- |
| `resolve_template` | 2 | Compile IntentSpec → FabricationPlan |
| `resolve_l_connector_template` | 2 | Shortcut with `slots` + optional `hole_groups` |
| `execute_fabrication_plan` | 1 | Batch execute CS, sketches, extrude, boolean |
| `validate_document` | — | Check object health after build |
| `get_body_snapshot` | 1 | Edge/face samples for debugging |

## IntentSpec entry point

Prefer `resolve_template` with full IntentSpec (includes `hole_groups`).

Direct shortcut:

```python
plan = await resolve_l_connector_template(
    slots={"arm_x_length": 50, "arm_y_length": 80, "width": 30, "thickness": 10},
    hole_groups=[{"face_id": "arm_x_top", "count_u": 2, "count_v": 2, ...}],
)
await execute_fabrication_plan(plan)
```

## Capabilities resource

Read `freecad://capabilities` for the full fabrication and template tool catalog.

## Source modules

| Module | Role |
| ------ | ---- |
| `src/freecad_mcp/intent/schema.py` | IntentSpec, HoleArraySpec |
| `src/freecad_mcp/tools/templates/connectors.py` | L connector plan builder |
| `src/freecad_mcp/tools/templates/face_catalog.py` | face_id → UV frame |
| `src/freecad_mcp/tools/templates/hole_arrays.py` | hole_groups → plan fragments |
| `src/freecad_mcp/tools/fabrication.py` | Layer 1 execution |
