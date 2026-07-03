# Layer 2: Domain Template Tools

This directory contains scene-specific template tools that bridge
natural-language user intent and the generic Layer 1 fabrication primitives.

## Architecture Overview

```text
User: "生成一个L型连接件，一边5cm，一边8cm，宽3cm"
          │
          ▼
   Layer 2 Template Tool
   resolve_connector_params(description) → FabricationPlan
          │
          │  FabricationPlan (typed schema, see fabrication_schema.py)
          ▼
   Layer 1 Generic Primitives
   create_coordinate_system()
   create_sketch_geometry() + apply_sketch_constraints()
   execute_extrude()
   feature_fillet()
   list_tunable_params() / set_tunable_param()
```

## Adding a New Domain Template

### 1. Create the module

```text
tools/templates/
├── __init__.py          ← register here
├── connectors.py        ← connector domain (example)
├── structural.py        ← structural elements
└── README.md            ← this file
```

### 2. Implement the template tool

```python
# tools/templates/connectors.py

from freecad_mcp.tools.fabrication_schema import (
    CoordinateSystemSpec, FabricationPlan, FeatureSpec, FinishSpec, SketchSpec,
)


def register_connector_templates(mcp, get_bridge):
    """Register connector domain template tools."""

    @mcp.tool()
    async def resolve_connector_params(description: str) -> dict:
        """Convert a connector description to a FabricationPlan.

        Args:
            description: Natural language, e.g. "L型连接件，5cm×8cm，宽3cm"

        Returns:
            FabricationPlan dict ready for execute_fabrication_plan().

        Example param_aliases exposed as frontend sliders:
            - short_arm_length: 短臂长度 (mm)
            - long_arm_length:  长臂长度 (mm)
            - width:            全局宽度 (mm)
            - thickness:        壁厚 (mm)
        """
        # TODO: use RAG over product knowledge base to resolve ambiguities
        # Parse description → extract dimensions
        # Build and return FabricationPlan
        plan = FabricationPlan(
            coordinate_systems=[...],
            sketches=[...],
            features=[...],
            finishes=[...],
        )
        return plan.to_dict()
```

### 3. Register in `__init__.py`

```python
# In register_template_tools():
from freecad_mcp.tools.templates.connectors import register_connector_templates
register_connector_templates(mcp, get_bridge)
```

## Contract Rules for Template Tools

Every template tool MUST:

1. Accept at minimum a `description: str` parameter.
2. Return `FabricationPlan.to_dict()` — a plain `dict[str, Any]`.
3. Never call the FreeCAD API directly — all CAD execution is Layer 1's job.
4. Document which `param_aliases` it exposes (these become frontend sliders).
5. Validate required dimensions and raise `ValueError` for unresolvable inputs.

## Typical Workflow for an RL Agent

The RL agent can use templates at the geometry level while learning constraints:

```text
Agent calls: resolve_connector_params("L型连接件，5cm×8cm，宽3cm")
  → Returns FabricationPlan with:
      sketches[].sketch      ← geometry provided by template
      sketches[].constraints ← empty dict  (RL agent fills this in)

Agent fills in constraints:
  apply_sketch_constraints("Sketch001", {...agent-generated constraints...})

Agent executes features:
  execute_fabrication_plan({...plan with filled constraints...})
```

This lets the RL policy focus on constraint strategy while the template
handles the geometry parameterization that requires domain knowledge.
