# L-Connector Fabrication Intent Expansion

Use this skill when a user asks for an L-shaped connector/bracket/直角连接件 and
expects FreeCAD fabrication tools to generate the model.

## Workflow

1. Read `index.md` for the current agent workflow and trace format.
2. Use `face-catalog.md` only when the request includes holes on semantic faces.
3. Use `hole-slot-schema.md` only when constructing `hole_groups`.
4. Use `l3-examples.md` when the natural language is fuzzy.
5. Use `fabrication-tools.md` for the MCP call order after IntentSpec is ready.

## Contract

- The learned/agent output is `IntentSpec`.
- `IntentSpec -> OperationPlan` is deterministic post-processing via MCP.
- Prefer MCP tools in this order: `list_templates`, `describe_template`,
  `validate_intent`, `compile_intent`, `execute_operation_plan`,
  `validate_document`, `get_body_snapshot`.
- Keep trace fields separate: `user_query`, `expanded_l3_prompt`,
  `intent_spec`, `operation_plan`, `execution_result`,
  `validation_feedback`, `repair_action`.
