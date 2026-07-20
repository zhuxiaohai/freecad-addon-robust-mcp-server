# Fabrication MCP Tools

## Pipeline

```text
list_templates()/describe_template()  →  template contract
validate_intent(intent)  →  structured diagnostics
compile_intent(intent)  →  IntentSpec package with OperationPlan
execute_operation_plan(plan, doc_name)  →  FreeCAD body
```

## Key tools

| Tool | Layer | Purpose |
| ---- | ----- | ------- |
| `list_templates` | Agent1 | Discover aliases, use cases, slots, features, face IDs |
| `describe_template` | Agent1 | Inspect one template contract |
| `validate_intent` | Agent1/repair | Validate IntentSpec without CAD execution |
| `compile_intent` | 2 | Compile IntentSpec → package with provenance + OperationPlan |
| `resolve_template` | 2 | Compile IntentSpec → bare OperationPlan |
| `resolve_l_connector_template` | 2/debug | Shortcut with `slots` + optional `hole_groups` |
| `execute_operation_plan` | 1 | Batch execute CS, sketches, extrude, boolean |
| `validate_document` | — | Check object health after build |
| `get_body_snapshot` | 1 | Edge/face samples for debugging |

## IntentSpec entry point

Prefer `compile_intent` with full IntentSpec (includes `hole_groups`) when
handoff, production trace, or training logs need provenance. The orchestrator
should pass the returned `operation_plan` directly from trace/process memory
into `execute_operation_plan`; do not ask an LLM to copy the large JSON. Use
`resolve_template` only when a bare OperationPlan is explicitly needed for
debugging.

For file-backed harnesses, `validate_intent` / `compile_intent` also accept
`intent_path`, and `validate_operation_plan` / `execute_operation_plan`
accept `plan_path`.

Direct shortcut:

```python
plan = await resolve_l_connector_template(
    slots={"arm_x_length": 50, "arm_y_length": 80, "width": 30, "thickness": 10},
    hole_groups=[{"face_id": "arm_x_top", "count_u": 2, "count_v": 2, ...}],
)
await execute_operation_plan(plan, doc_name=doc_name)
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
