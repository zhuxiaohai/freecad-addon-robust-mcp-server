# HoleArraySpec and IntentSpec Schema

## IntentSpec (top level)

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
  "assumptions": ["unit=mm"],
  "placement": null
}
```

### Dimension slots (mm)

| Slot | Meaning |
| ---- | ------- |
| `arm_x_length` | Horizontal arm length (lx) |
| `arm_y_length` | Vertical arm length (ly) |
| `arm_x_width` | Horizontal arm thickness (wx) |
| `arm_y_width` | Vertical arm thickness (wy) |
| `width` | Global width alias (bound to both arm widths) |
| `thickness` | Extrude depth (default 10 if omitted) |

## HoleArraySpec (one per face group)

```json
{
  "face_id": "arm_x_top",
  "pattern": "rectangular",
  "count_u": 2,
  "count_v": 2,
  "pitch_u": 15,
  "pitch_v": 15,
  "diameter": 5,
  "margin_u": 10,
  "margin_v": 10
}
```

| Field | Type | Default | Notes |
| ----- | ---- | ------- | ----- |
| `face_id` | string | required | From face catalog |
| `pattern` | `"rectangular"` | rectangular | `polar` reserved |
| `count_u` | int | 1 | Holes along u |
| `count_v` | int | 1 | Holes along v |
| `pitch_u` | float | 10 | mm between centres (u) |
| `pitch_v` | float | 10 | mm between centres (v) |
| `diameter` | float or float[] | 5 | mm; list length = count_u * count_v |
| `margin_u` | float | 10 | mm from hole-sketch u-origin to first hole centre (sketch-local) |
| `margin_v` | float | 10 | mm from hole-sketch v-origin to first hole centre (sketch-local) |

## Per-face different diameters

Row-major `count_u` then `count_v`:

```json
{
  "face_id": "bottom",
  "count_u": 2,
  "count_v": 2,
  "pitch_u": 20,
  "pitch_v": 20,
  "diameter": [5, 5, 5, 8],
  "margin_u": 15,
  "margin_v": 15
}
```
