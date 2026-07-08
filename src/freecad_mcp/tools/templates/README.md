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
   NL → IntentSpec { template_name, slots, slot_bindings, placement }
          │
          ▼
   Layer 2 Template Tool  [deterministic, no LLM]
   resolve_template(intent) → FabricationPlan
          │
          ▼
   Layer 1 Generic Primitives
   execute_fabrication_plan(plan)
```

Natural language parsing is **not** done in this package.  The Intent Model
produces structured slots; templates compile them deterministically.

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
  "assumptions": ["unit=mm", "width applies to both arms"]
}
```

- ``slot_bindings``: semantic slot coupling — compiled by the template, **not**
  passed through to FabricationPlan geometric constraints.
- ``placement``: optional sketch-plane placement — passed through as ``plane``.

## Adding a New Domain Template

### 1. Create the module

```text
tools/templates/
├── __init__.py          ← register resolve_template + domain tools
├── connectors.py        ← connector domain (example)
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

# 2. Deterministic template compilation
plan = await resolve_template(intent)

# 3. Deterministic CAD execution
result = await execute_fabrication_plan(plan)
```

For RL training on Layer 1 tool trajectories, the agent can call primitives
individually instead of batch execution — see the fabrication prompt.
