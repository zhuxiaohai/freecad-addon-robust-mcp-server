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
    from freecad_mcp.intent import registry as intent_registry
    from freecad_mcp.intent.schema import IntentSpec
    from freecad_mcp.tools.structured_input import load_structured_input
    from freecad_mcp.tools.templates.connectors import register_connector_templates

    register_connector_templates(mcp, get_bridge)

    @mcp.tool()
    async def list_templates() -> list[dict[str, object]]:
        """List domain templates available to an Intent Agent.

        This is the preferred Agent1 discovery entry point.  The result is a
        machine-readable catalog containing aliases, natural-language use cases,
        slot contracts, supported features, valid semantic face IDs, and example
        IntentSpec payloads.  Do not use shortcut template tools as the primary
        template-selection action space.
        """
        return intent_registry.list_template_catalog()

    @mcp.tool()
    async def describe_template(template_name: str) -> dict[str, object]:
        """Describe one domain template for template selection and slot filling.

        Args:
            template_name: Registry key such as ``"l_connector"``.

        Returns:
            Machine-readable template contract for building an IntentSpec.
        """
        return intent_registry.describe_template(template_name)

    @mcp.tool()
    async def validate_intent(
        intent: dict[str, Any] | str | None = None,
        intent_path: str | None = None,
    ) -> dict[str, object]:
        """Validate an IntentSpec and return structured attribution.

        This tool is for Agent1/repair workflows.  It does not execute CAD.  A
        valid result means the deterministic template compiler can produce a
        FabricationPlan from the supplied IntentSpec. ``intent`` may be a dict
        or JSON string; ``intent_path`` may point to a UTF-8 JSON file.
        """
        try:
            intent_data = load_structured_input(intent, intent_path, "intent")
            spec = IntentSpec.from_dict(intent_data)
            intent_registry.validate_intent(spec)
        except Exception as exc:
            message = str(exc)
            return {
                "valid": False,
                "error": message,
                "failure_attribution": intent_registry.classify_compile_error(message),
            }
        return {
            "valid": True,
            "error": None,
            "failure_attribution": None,
        }

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
        intent_data = load_structured_input(intent, None, "intent")
        spec = IntentSpec.from_dict(intent_data)
        plan = intent_registry.resolve_intent_to_plan(spec)
        return plan.to_dict()

    @mcp.tool()
    async def compile_intent(
        intent: dict[str, Any] | str | None = None,
        intent_path: str | None = None,
    ) -> dict[str, object]:
        """Compile IntentSpec into an executable FabricationPlan package.

        Agent1 may expose this package as its handoff artifact: the model output
        remains the IntentSpec, while ``fabrication_plan`` is deterministic
        post-processing.  ``intent`` may be a dict or JSON string;
        ``intent_path`` may point to a UTF-8 JSON file. The package preserves
        provenance and diagnostics for Agent2 execution/repair.
        """
        try:
            intent_data = load_structured_input(intent, intent_path, "intent")
            spec = IntentSpec.from_dict(intent_data)
            return intent_registry.compile_intent_package(spec)
        except Exception as exc:
            message = str(exc)
            fallback_intent = intent if isinstance(intent, dict) else {}
            return {
                "intent_spec": fallback_intent,
                "fabrication_plan": None,
                "assumptions": list(fallback_intent.get("assumptions", [])),
                "template_provenance": {
                    "template_name": fallback_intent.get("template_name"),
                    "template_version": None,
                    "compiler": "freecad_mcp.intent.registry.resolve_intent_to_plan",
                    "deterministic": True,
                },
                "compile_diagnostics": {
                    "ok": False,
                    "stage": "intent_to_fabrication_plan",
                    "error": message,
                    "failure_attribution": intent_registry.classify_compile_error(
                        message
                    ),
                },
            }
