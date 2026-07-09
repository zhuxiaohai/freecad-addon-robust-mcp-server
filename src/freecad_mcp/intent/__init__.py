"""Intent specification contract between Intent Model and Layer 2 templates.

The Intent Model (external LLM, Cursor, or trained Policy 1) converts natural
language into a structured :class:`IntentSpec`.  Layer 2 templates compile
``IntentSpec`` into a :class:`~freecad_mcp.tools.fabrication_schema.FabricationPlan`
deterministically — no LLM involvement at this stage.
"""

from freecad_mcp.intent.registry import resolve_intent_to_plan, validate_intent
from freecad_mcp.intent.schema import (
    HoleArraySpec,
    IntentSpec,
    apply_slot_bindings,
    validate_hole_array_spec,
)

__all__ = [
    "HoleArraySpec",
    "IntentSpec",
    "apply_slot_bindings",
    "resolve_intent_to_plan",
    "validate_hole_array_spec",
    "validate_intent",
]
