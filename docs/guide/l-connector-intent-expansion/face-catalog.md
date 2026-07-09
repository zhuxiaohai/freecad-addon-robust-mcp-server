# L-Connector Exterior Face Catalog

Nine semantic `face_id` values for the standard L-profile (XY sketch, extruded +Z by `thickness`).

| face_id | Description | User phrases |
| ------- | ----------- | ------------ |
| `arm_x_top` | Horizontal arm top (+Z) | "水平臂顶面", "X臂顶面", "长边顶面" |
| `arm_y_top` | Vertical arm top (+Z) | "竖直臂顶面", "Y臂顶面", "短边顶面" |
| `arm_x_outer` | Outer face at y=0 (-Y normal) | "水平臂外侧面", "底边外侧面" |
| `arm_y_outer` | Outer face at x=0 (-X normal) | "竖直臂外侧面", "左边外侧面" |
| `arm_x_end` | End face at x=lx (+X) | "水平臂端面", "X端面" |
| `arm_y_end` | End face at y=ly (+Y) | "竖直臂端面", "Y端面" |
| `inner_corner_vertical` | Inner re-entrant face at x=wy | "内角竖直面" |
| `inner_corner_horizontal` | Inner re-entrant face at y=wx | "内角水平面" |
| `bottom` | Bottom cap (-Z) | "底面", "下面" |

## Phrase → face_id mapping

| User phrase | face_id list |
| ----------- | ------------ |
| "顶面" / "top" | `arm_x_top`, `arm_y_top` |
| "每个面" / "all faces" / "全表面" | All 9 face_ids |
| "外侧面" | `arm_x_outer`, `arm_y_outer` |
| "端面" | `arm_x_end`, `arm_y_end` |
| "内角" | `inner_corner_vertical`, `inner_corner_horizontal` |

## UV frame (for hole placement)

Each face defines a **hole-sketch coordinate system** (origin + u/v axes). Hole centres
use `(margin_u + i*pitch_u, margin_v + j*pitch_v)` in that sketch frame — distances
measured from the sketch origin along +u / +v. `FabricationParams` margin/pitch aliases
show the same sketch-local values (not L-global coordinates). Convert to L-global with
`world = origin + u*u_axis + v*v_axis`. Implementation:
`src/freecad_mcp/tools/templates/face_catalog.py`.

### Top face sketch origins (L-global)

| face_id | Sketch origin (L-global) | `extent_u` × `extent_v` | Example (`margin_u=15`, `pitch_u=20`) |
| ------- | ------------------------ | ----------------------- | ------------------------------------- |
| `arm_x_top` | `(arm_y_width, 0, thickness)` | `(arm_x_length - arm_y_width) × arm_x_width` | sketch u=15,35 → L x=35,55 when `arm_y_width=20` |
| `arm_y_top` | `(0, arm_x_width, thickness)` | `arm_y_width × (arm_y_length - arm_x_width)` | sketch v=15,35 → L y=35,55 when `arm_x_width=20` |

With positive margins and pitches, every hole centre has sketch `u ≥ 0` and `v ≥ 0`.

Top-face hole-sketch LCS uses `attachment_support` on the solid plus
`offset_expressions` on `AttachmentOffset` (assembly-style linkage).
Margin/pitch aliases remain sketch-local; `translation` is the offset origin
in the reference solid's local frame (HistCAD `Translation Vector`).
