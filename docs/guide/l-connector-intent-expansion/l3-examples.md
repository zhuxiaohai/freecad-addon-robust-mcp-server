# L3 Examples: Natural Language → IntentSpec

## Example 1 — Dimensions only

**User:** 生成一个L型连接件，一边5cm，一边8cm，宽度全局3cm，厚1cm

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
  "hole_groups": [],
  "assumptions": ["unit=mm", "width applies to both arms"]
}
```

## Example 2 — Two top faces, same array

**User:** L连接件 50×80×30×10mm，两个顶面各 3×3 孔，孔距20，孔径6

Nine-face expansion is **not** needed — only top mounting faces.

```json
{
  "template_name": "l_connector",
  "slots": {
    "arm_x_length": 50,
    "arm_y_length": 80,
    "arm_x_width": 30,
    "arm_y_width": 30,
    "thickness": 10
  },
  "hole_groups": [
    {
      "face_id": "arm_x_top",
      "pattern": "rectangular",
      "count_u": 3,
      "count_v": 3,
      "pitch_u": 20,
      "pitch_v": 20,
      "diameter": 6,
      "margin_u": 10,
      "margin_v": 10
    },
    {
      "face_id": "arm_y_top",
      "pattern": "rectangular",
      "count_u": 3,
      "count_v": 3,
      "pitch_u": 20,
      "pitch_v": 20,
      "diameter": 6,
      "margin_u": 10,
      "margin_v": 10
    }
  ],
  "assumptions": ["unit=mm", "through holes", "top faces only"]
}
```

## Example 3 — All exterior faces

**User:** L连接件 5cm×8cm 宽3cm 厚1cm，每个面 2×2 孔阵，孔距15，孔径5，底面孔径8

Generate **9** `hole_groups` entries. Only `bottom` uses `diameter: 8`; others use `5`.

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
  "hole_groups": [
    {"face_id": "arm_x_top", "count_u": 2, "count_v": 2, "pitch_u": 15, "pitch_v": 15, "diameter": 5, "margin_u": 10, "margin_v": 10},
    {"face_id": "arm_y_top", "count_u": 2, "count_v": 2, "pitch_u": 15, "pitch_v": 15, "diameter": 5, "margin_u": 10, "margin_v": 10},
    {"face_id": "arm_x_outer", "count_u": 2, "count_v": 2, "pitch_u": 15, "pitch_v": 15, "diameter": 5, "margin_u": 10, "margin_v": 5},
    {"face_id": "arm_y_outer", "count_u": 2, "count_v": 2, "pitch_u": 15, "pitch_v": 15, "diameter": 5, "margin_u": 10, "margin_v": 5},
    {"face_id": "arm_x_end", "count_u": 2, "count_v": 2, "pitch_u": 10, "pitch_v": 5, "diameter": 5, "margin_u": 5, "margin_v": 5},
    {"face_id": "arm_y_end", "count_u": 2, "count_v": 2, "pitch_u": 10, "pitch_v": 5, "diameter": 5, "margin_u": 5, "margin_v": 5},
    {"face_id": "inner_corner_vertical", "count_u": 1, "count_v": 2, "pitch_u": 10, "pitch_v": 10, "diameter": 5, "margin_u": 10, "margin_v": 10},
    {"face_id": "inner_corner_horizontal", "count_u": 2, "count_v": 1, "pitch_u": 10, "pitch_v": 10, "diameter": 5, "margin_u": 10, "margin_v": 10},
    {"face_id": "bottom", "count_u": 2, "count_v": 2, "pitch_u": 15, "pitch_v": 15, "diameter": 8, "margin_u": 10, "margin_v": 10}
  ],
  "assumptions": ["unit=mm", "through holes", "all exterior faces", "bottom holes 8mm others 5mm"]
}
```

Adjust counts/margins on small faces (end faces, inner corner) so holes fit extents.
