"""Layer 2 domain template tools.

This package contains scene-specific template tools that convert
natural-language or low-detail user intent into a structured
FabricationPlan (defined in ``tools/fabrication_schema.py``) that
Layer 1 generic fabrication primitives can execute.

Architecture
------------

::

    User Intent (L0-L2)
        │
        ▼
    Layer 2 — Domain Template Tools  (this package)
        resolve_connector_params()  → FabricationPlan
        resolve_structural_params() → FabricationPlan
        ...
        │  FabricationPlan  (typed contract)
        ▼
    Layer 1 — Generic Fabrication Primitives  (tools/fabrication.py)
        create_coordinate_system()
        create_sketch_geometry()
        apply_sketch_constraints()
        execute_extrude() / execute_boolean() / execute_revolve() / execute_helix()
        feature_fillet() / feature_chamfer()
        list_tunable_params() / set_tunable_param()
        execute_fabrication_plan()

Adding a New Domain Template
-----------------------------

1. Create ``tools/templates/<domain>.py`` (e.g. ``connectors.py``).
2. Implement one or more ``@mcp.tool()`` functions that return a
   ``FabricationPlan.to_dict()`` as their result.
3. Add a ``register_<domain>_templates(mcp, get_bridge)`` function.
4. Import and call it in ``register_template_tools()`` below.

Contract
--------

Every template tool MUST:
- Accept at minimum a natural-language ``description: str`` parameter.
- Return a ``FabricationPlan`` serialised as ``dict[str, Any]``.
- Never call FreeCAD API directly — all CAD execution goes through Layer 1.
- Document which ``param_aliases`` it exposes (these become frontend sliders).

See ``tools/fabrication_schema.py`` for the full FabricationPlan schema.
"""

from collections.abc import Awaitable, Callable
from typing import Any


def register_template_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register all Layer 2 domain template tools with the MCP server.

    This function is a placeholder. Add domain-specific registrations here
    as new template modules are created under ``tools/templates/``.

    Args:
        mcp: The FastMCP (Robust MCP Server) instance.
        get_bridge: Async function to get the active bridge connection.

    Example:
        When the ``connectors`` domain template is implemented::

            from freecad_mcp.tools.templates.connectors import (
                register_connector_templates,
            )
            register_connector_templates(mcp, get_bridge)
    """
    # No domain templates registered yet.
    # Add imports and calls here as templates are developed.
