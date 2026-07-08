"""Layer 2 domain template tools.

This package contains scene-specific template tools that compile a structured
:class:`~freecad_mcp.intent.schema.IntentSpec` into a
:class:`~freecad_mcp.tools.fabrication_schema.FabricationPlan` that Layer 1
generic fabrication primitives can execute.

Architecture
------------

::

    User Intent (L0-L2, natural language)
        │
        ▼
    Intent Model (external — Cursor, skill/RAG, or Policy 1)
        IntentSpec (template_name + slots + slot_bindings + placement)
        │
        ▼
    Layer 2 — Domain Template Tools  (this package)
        resolve_template()  → FabricationPlan   [deterministic, no LLM]
        │
        ▼
    Layer 1 — Generic Fabrication Primitives  (tools/fabrication.py)
        execute_fabrication_plan()

Adding a New Domain Template
-----------------------------

1. Create ``tools/templates/<domain>.py`` (e.g. ``connectors.py``).
2. Implement ``build_*_plan(slots, plane)`` returning a ``FabricationPlan``.
3. Register a builder in ``freecad_mcp.intent.registry.TEMPLATE_BUILDERS``.
4. Add a ``register_<domain>_templates(mcp, get_bridge)`` function if needed.
5. Import and call it in ``register_template_tools()`` below.

Contract
--------

Every template tool MUST:
- Accept structured ``slots`` or a full ``IntentSpec`` dict — never natural language.
- Return a ``FabricationPlan`` serialised as ``dict[str, Any]``.
- Never call FreeCAD API directly — all CAD execution goes through Layer 1.
- Document which ``param_aliases`` it exposes (these become frontend sliders).

See ``freecad_mcp.intent.schema`` and ``tools/fabrication_schema.py``.
"""

from collections.abc import Awaitable, Callable
from typing import Any


def register_template_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register all Layer 2 domain template tools with the MCP server.

    Args:
        mcp: The FastMCP (Robust MCP Server) instance.
        get_bridge: Async function to get the active bridge connection.
    """
    from freecad_mcp.intent.registry import resolve_intent_to_plan
    from freecad_mcp.intent.schema import IntentSpec
    from freecad_mcp.tools.templates.connectors import register_connector_templates

    register_connector_templates(mcp, get_bridge)

    @mcp.tool()
    async def resolve_template(intent: dict[str, Any]) -> dict[str, Any]:
        """Compile an IntentSpec into a FabricationPlan via the template registry.

        This step is fully deterministic.  The Intent Model (LLM or Cursor)
        must produce the ``intent`` dict upstream; this tool does not parse
        natural language.

        Args:
            intent: IntentSpec dict with ``template_name``, ``slots``, and
                optional ``assumptions``, ``slot_bindings``, ``placement``.

        Returns:
            FabricationPlan dict ready for ``execute_fabrication_plan()``.

        Example:
            >>> intent = {
            ...     "template_name": "l_connector",
            ...     "slots": {
            ...         "arm_x_length": 50,
            ...         "arm_y_length": 80,
            ...         "width": 30,
            ...     },
            ...     "slot_bindings": [
            ...         "arm_x_width = width",
            ...         "arm_y_width = width",
            ...     ],
            ... }
            >>> plan = await resolve_template(intent)
        """
        spec = IntentSpec.from_dict(intent)
        plan = resolve_intent_to_plan(spec)
        return plan.to_dict()
