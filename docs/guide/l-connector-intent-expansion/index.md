# L-Connector Intent Expansion Guide

External Intent Models (your separate agent, Cursor for local debugging, or trained
Policy 1) convert natural-language L-connector requests into structured
**IntentSpec** JSON. Layer 2 templates compile that deterministically — no LLM in
this repository's template path.

This guide mirrors CADDesigner's requirement-refinement pattern: the runtime user
speaks briefly; the Intent Model produces an L3-level specification before CAD
execution.

## Reference documents

| Document | Purpose |
| -------- | ------- |
| [Face catalog](face-catalog.md) | Nine semantic `face_id` values and user phrase mapping |
| [Hole slot schema](hole-slot-schema.md) | `HoleArraySpec` / `IntentSpec` JSON contract |
| [L3 examples](l3-examples.md) | Fuzzy natural language → full IntentSpec |
| [Fabrication tools](fabrication-tools.md) | MCP tools after IntentSpec is ready |

Do not invent face IDs or slot names. Use the catalog and schema above.

## Agent roles during local debugging

For now, Cursor may act as both roles in one workflow:

```text
Intent role:    NL → L3 notes → IntentSpec
Compiler step:  IntentSpec → OperationPlan package
Execution role: OperationPlan → FreeCAD model → feedback
Repair loop:    feedback → revised IntentSpec
```

Keep these artifacts separate in notes and logs even when one IDE agent performs
all steps. This makes the trace usable later for SFT/RL data.

## Required output format

When expanding user intent, produce these sections in order:

### Refined Requirements

Geometry goals, units (mm), which faces get holes, through-hole vs blind (v1: through only).

### Parameter Table

| Slot / face_id | Value | Notes |
| -------------- | ----- | ----- |

Include all dimension slots and every hole group (counts, pitch, diameter, margins).

### IntentSpec JSON

A single JSON object valid for `resolve_template(intent)`:

```json
{
  "template_name": "l_connector",
  "slots": { },
  "slot_bindings": ["arm_x_width = width", "arm_y_width = width"],
  "hole_groups": [ ],
  "assumptions": [ ]
}
```

### Assumptions

List defaults you applied (margins, pitch, which faces, unit=mm, etc.).

## Standard trace record

Use this structure when collecting debug cases:

```json
{
  "user_query": "",
  "expanded_l3_prompt": "",
  "intent_spec": {},
  "operation_plan": {},
  "execution_result": {},
  "validation_feedback": {},
  "repair_action": ""
}
```

Prefer `compile_intent(intent)` when you want a complete handoff package with
`intent_spec`, deterministic `operation_plan`, provenance, assumptions, and
compile diagnostics. Use `resolve_template(intent)` when you only need the plan.

## Default inference rules

| User says | Expand to |
| --------- | --------- |
| "宽 3cm" / "width 30mm" | `width: 30` + bindings for both arm widths |
| "每个面打孔" / "all faces" | One `hole_groups` entry per face in the face catalog (9 faces) |
| "顶面 3×3 孔" | `arm_x_top` and `arm_y_top` each `count_u=3, count_v=3` |
| No margin given | `margin_u=10`, `margin_v=10` (mm) |
| No pitch given | `pitch_u=15`, `pitch_v=15` (mm) |
| No diameter given | `diameter=5` (mm) |
| No thickness | omit from slots (template default 10 mm) |

## Validation checklist

Before returning IntentSpec JSON:

- All required slots resolvable (`arm_x_length`, `arm_y_length`, widths, `thickness`)
- `arm_x_width < arm_y_length` and `arm_y_width < arm_x_length`
- Every `face_id` is from the catalog
- `count_u`, `count_v` >= 1; pitches and diameters > 0
- `diameter < min(pitch_u, pitch_v)` when multiple holes per face
- Holes fit face extent (template raises `ValueError` if not)

## Execution workflow (after IntentSpec)

1. Present Parameter Table; confirm with user if ambiguous
2. `list_templates()` / `describe_template("l_connector")` when template choice is uncertain
3. `validate_intent(intent)` for structured slot/template feedback
4. `compile_intent(intent)` to produce the deterministic OperationPlan
   package and write it directly to trace/process memory.
5. `execute_operation_plan(plan, doc_name=<session_doc_name>)`, passing the
   compiled plan from memory/trace rather than asking an LLM to copy the JSON.
   File-backed harnesses may pass `plan_path` instead of recopying the plan.
6. `validate_document(doc_name=<session_doc_name>)` /
   `get_body_snapshot(doc_name=<session_doc_name>)` for verification

## Run Logging

Store assumptions and the final IntentSpec JSON with each run. Template
compilation and CAD execution are deterministic, so callers can separate intent
expansion issues from geometry execution issues. If execution succeeds but geometry
violates the user query, repair the IntentSpec first; only blame the template compiler
when the IntentSpec is correct and the deterministic OperationPlan is wrong.

## Local agent debugging

This directory is an agent skill/debug aid, not an MCP runtime contract.  Future
production agent projects may copy or replace it; the MCP runtime contract is the
machine-readable template catalog and IntentSpec schema exposed by tools.
