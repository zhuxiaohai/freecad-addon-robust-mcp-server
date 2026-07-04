# Fusion 360 Server API Used by the JSON Exporter

The public exporter calls the Fusion add-in through HTTP `POST` requests. Each request has this shape:

```json
{
  "command": "command_name",
  "data": {}
}
```

Core export commands:

- `ping`
- `run_batch`
- `clear`
- `create_sketch`
- `create_sketch_by_three_points`
- `add_line`
- `add_circle`
- `add_arc`
- `add_ellipse`
- `add_elliptical_arc`
- `add_nurbs`
- `add_constraint`
- `set_dimension_expression`
- `create_extrude`
- `revolve`
- `create_helix_sweep`
- `create_fillet`
- `create_chamfer`
- `export_step`
- `export_f3d`
- `detach`

Additional commands used by the editability benchmark and diagnostics:

- `add_raw_tangent_distance_dimension`
- `debug_distance_resolution`
- `auto_close_loop`
- `validate_step_file`
- `validate_sketch_constraints`
- `validate_all_sketch_constraints`
- `check_constraint_satisfied`
- `probe_constraint_effect`
- `inspect_entities`
- `inspect_sketch_profiles`
- `create_user_parameter`
- `set_parameter_expression`
- `get_parameter`
- `get_model_metrics`
- `check_model`
- `calculate_iou`
- `calculate_assembly_collisions`
- `calculate_step_physical_properties`
- `export_view`

`run_batch` accepts a list of serialized commands and an optional `entities` dictionary. It resolves `{"__entity_ref__": "name"}` placeholders from prior command return values, then returns the updated entity map and the last command response.

The exporter normally uses `run_batch`; individual commands remain available as a fallback.
