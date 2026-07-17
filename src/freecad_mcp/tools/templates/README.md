# Layer 2: Domain Template Tools

This directory contains scene-specific template tools that compile a structured
**IntentSpec** (from an external Intent Model) into a **FabricationPlan** for
Layer 1 generic fabrication primitives.

## Architecture Overview

```text
User: "生成一个L型连接件，一边5cm，一边8cm，宽3cm"
          │
          ▼
   Intent Model (external — Cursor, skill/RAG, Policy 1)
   NL → IntentSpec { template_name, slots, slot_bindings, placement, hole_groups? }
          │
          ▼
   Layer 2 Template Tool  [deterministic, no LLM]
   resolve_template(intent) → FabricationPlan
          │
          ▼
   Layer 1 Generic Primitives
   execute_fabrication_plan(plan, doc_name=doc_name)
```

Natural language parsing is **not** done in this package.  The Intent Model
produces structured slots; templates compile them deterministically.

## Current Agent/MCP Boundary

During early validation, Cursor may temporarily play both roles:

- **Intent Agent**: read skills/docs/template catalog, expand natural language
  into an ``IntentSpec``.
- **Execution Agent**: traceable production TUI agents should call
  ``compile_intent(intent)`` after ``validate_intent``, write the returned
  ``fabrication_plan`` directly to trace/process memory, then call
  ``execute_fabrication_plan`` with that plan without asking the LLM to copy the
  JSON. Tools accept structured dicts, JSON strings, or ``intent_path`` /
  ``plan_path`` file inputs for file-backed harness handoff.

This repository still treats those as separate stages.  Production agent code
can move to a dedicated agent repo later without changing the contracts:

```text
NL / L3 prompt → IntentSpec
IntentSpec → FabricationPlan
FabricationPlan → FreeCAD model
Execution feedback → IntentSpec repair
```

## Template Discovery Tools

Intent Agents should choose templates through the catalog, not by treating every
template shortcut as a separate primary action:

| Tool | Intended user | Purpose |
| ---- | ------------- | ------- |
| ``list_templates`` | Intent Agent | List aliases, use cases, slots, features, face IDs, examples |
| ``describe_template`` | Intent Agent | Inspect one template contract before slot filling |
| ``validate_intent`` | Intent/repair loop | Validate IntentSpec and return structured failure attribution |
| ``compile_intent`` | Handoff | Return IntentSpec + deterministic FabricationPlan package |
| ``resolve_template`` | Compiler shortcut | Return only the FabricationPlan for an IntentSpec |
| ``resolve_l_connector_template`` | Debug/manual | Bypass IntentSpec with resolved slots; not the main agent path |

The underlying implementation is ordinary Python.  MCP tools are thin wrappers
so Cursor, Codex, external agents, and future rollout runners can share the same
contracts.

## IntentSpec Contract

See ``freecad_mcp.intent.schema.IntentSpec``.  Example for an L connector:

```json
{
  "template_name": "l_connector",
  "slots": {
    "arm_x_length": 50,
    "arm_y_length": 80,
    "width": 30,
    "thickness": 10
  },
  "slot_bindings": ["arm_x_width = width", "arm_y_width = width"],
  "assumptions": ["unit=mm", "width applies to both arms"],
  "hole_groups": [
    {
      "face_id": "arm_x_top",
      "count_u": 2,
      "count_v": 2,
      "pitch_u": 15,
      "pitch_v": 15,
      "diameter": 5,
      "margin_u": 10,
      "margin_v": 10
    }
  ]
}
```

- ``slot_bindings``: semantic slot coupling — compiled by the template, **not**
  passed through to FabricationPlan geometric constraints.
- ``placement``: optional sketch-plane placement — passed through as ``plane``.
- ``hole_groups``: optional per-face rectangular hole arrays (see
  ``face_catalog.py`` for nine exterior ``face_id`` values).

## IntentSpec Package

When an Intent Agent hands work to an Execution Agent, prefer a package rather
than a bare FabricationPlan:

```json
{
  "intent_spec": {},
  "fabrication_plan": {},
  "assumptions": [],
  "template_provenance": {
    "template_name": "l_connector",
    "template_version": "v1",
    "deterministic": true
  },
  "compile_diagnostics": {
    "ok": true,
    "stage": "intent_to_fabrication_plan",
    "failure_attribution": null
  }
}
```

The model learns ``NL → IntentSpec``.  ``IntentSpec → FabricationPlan`` is
deterministic post-processing, but the resulting plan is still included as the
executable handoff payload.

## Failure Attribution

- IntentSpec schema cannot be parsed: Intent Agent schema error.
- Unknown template, missing slot, invalid binding, or unknown ``face_id``:
  Intent Agent template/slot/capability error.
- Valid IntentSpec compiles to wrong geometry: template compiler bug.
- Valid FabricationPlan fails in FreeCAD: Layer 1 adapter/execution bug.
- Execution succeeds but violates user intent: repair the IntentSpec first.

## L-Connector Modules

| Module | Role |
| ------ | ---- |
| `connectors.py` | L profile + extrude + optional holes |
| `face_catalog.py` | Semantic face UV frames and `near_point` |
| `hole_arrays.py` | Compile `hole_groups` → sketches + boolean cuts |

## Adding a New Domain Template

### 1. Create the module

```text
tools/templates/
├── __init__.py          ← register resolve_template + domain tools
├── connectors.py        ← connector domain (example)
├── face_catalog.py      ← L-connector semantic face frames
├── hole_arrays.py       ← hole_groups → plan fragments
└── README.md            ← this file
```

### 2. Implement the plan builder

```python
def build_my_part_plan(*, slots: dict[str, float], plane: dict | None = None) -> FabricationPlan:
  # validate slots, build sketch + constraints + features
  return FabricationPlan(...)
```

### 3. Register in intent registry

```python
# freecad_mcp/intent/registry.py
TEMPLATE_BUILDERS["my_part"] = _build_my_part_from_intent
TEMPLATE_SLOT_SCHEMAS["my_part"] = SlotSchema(required=[...], defaults={...})
```

### 4. Register MCP tools (optional domain-specific shortcut)

```python
# In register_<domain>_templates():
@mcp.tool()
async def resolve_my_part_template(slots: dict[str, float], ...) -> dict:
    return build_my_part_plan(slots=slots).to_dict()
```

## Contract Rules for Template Tools

Every template tool MUST:

1. Accept structured ``slots`` or a full ``IntentSpec`` dict — **never** natural language.
2. Return ``FabricationPlan.to_dict()`` — a plain ``dict[str, Any]``.
3. Never call the FreeCAD API directly — all CAD execution is Layer 1's job.
4. Document which ``param_aliases`` it exposes (these become frontend sliders).
5. Validate required slots and raise ``ValueError`` for infeasible geometry.

## Typical Workflow

```text
# 1. Intent Model (Cursor) produces IntentSpec from user NL
intent = {
    "template_name": "l_connector",
    "slots": {"arm_x_length": 50, "arm_y_length": 80, "width": 30},
    "slot_bindings": ["arm_x_width = width", "arm_y_width = width"],
}

# 2. Traceable deterministic CAD execution
doc_name = "FabricationDoc_123"
await create_document(name=doc_name)
package = await compile_intent(intent)
# The orchestrator should pass package["fabrication_plan"] directly from memory
# or trace storage; do not ask the LLM to recopy this JSON.
result = await execute_fabrication_plan(package["fabrication_plan"], doc_name=doc_name)

# File-backed harnesses may also pass JSON file paths accepted by this batch
# tool. Generic artifact storage, selectors, and arg composition stay in the
# agent harness; MCP tools do not expose separate artifact bridge tools.
package = await compile_intent(intent_path="/path/to/intent.json")
result = await execute_fabrication_plan(plan_path="/path/to/plan.json", doc_name=doc_name)
```

Debug / handoff workflow when the full FabricationPlan is explicitly needed:

```text
plan = await resolve_template(intent)

result = await execute_fabrication_plan(plan, doc_name=doc_name)
```

For RL training on Layer 1 tool trajectories, the agent can call primitives
individually instead of batch execution — see the fabrication prompt.
