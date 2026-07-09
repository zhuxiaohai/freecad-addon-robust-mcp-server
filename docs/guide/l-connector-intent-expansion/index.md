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
2. `resolve_template(intent)` → FabricationPlan
3. `execute_fabrication_plan(plan)`
4. `validate_document()` / `get_body_snapshot()` for verification

## RL / logging

Store assumptions and the final IntentSpec JSON for reward attribution. Template
compilation and CAD execution are deterministic — reward intent expansion separately
from geometry success.

## Local Cursor debugging

This repo does **not** ship Cursor-specific skill paths. For local IDE debugging you
may copy or symlink this guide into your agent project's skill layout; the canonical
source of truth is `docs/guide/l-connector-intent-expansion/` in this repository.
