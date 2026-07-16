"""Compile hole array specifications into FabricationPlan fragments."""

from __future__ import annotations

from typing import Any

from freecad_mcp.intent.schema import HoleArraySpec, validate_hole_array_spec
from freecad_mcp.tools.fabrication_schema import (
    CoordinateSystemSpec,
    FeatureSpec,
    SketchSpec,
)
from freecad_mcp.tools.templates.face_catalog import (
    FaceFrame,
    resolve_l_connector_face_frame,
)

_THROUGH_EXTRA = 0.5
_SHEET = "FabricationParams"


def _hole_diameters(spec: HoleArraySpec) -> list[float]:
    """Expand diameter to one value per hole (row-major u then v)."""
    count = spec.count_u * spec.count_v
    if isinstance(spec.diameter, list):
        return [float(d) for d in spec.diameter]
    return [float(spec.diameter)] * count


def _hole_centres_uv(
    spec: HoleArraySpec, _frame: FaceFrame
) -> list[tuple[float, float]]:
    """Compute hole centre positions in face UV coordinates.

    ``margin_u`` / ``margin_v`` are distances from the face UV origin along
    +u / +v to the first hole row; ``pitch_*`` steps additional holes outward.
    Positive margins yield non-negative UV for every hole centre.
    """
    centres: list[tuple[float, float]] = []
    for v_idx in range(spec.count_v):
        for u_idx in range(spec.count_u):
            u = spec.margin_u + u_idx * spec.pitch_u
            v = spec.margin_v + v_idx * spec.pitch_v
            centres.append((u, v))
    return centres


def _dim_meta(
    *,
    length: float | None = None,
    diameter: float | None = None,
    alias: str | None = None,
    expression: str | None = None,
    label: str,
    role: str,
    default: float,
    min_val: float | None = None,
) -> dict[str, Any]:
    """Build a HistCAD dimension metadata dict for sketch constraints."""
    meta: dict[str, Any] = {
        "label": label,
        "role": role,
        "unit": "mm",
        "default": default,
    }
    if length is not None:
        meta["length"] = length
    if diameter is not None:
        meta["diameter"] = diameter
    if alias is not None:
        meta["alias"] = alias
    if expression is not None:
        meta["expression"] = expression
    if min_val is not None:
        meta["min"] = min_val
    return meta


def _distance_entry(
    point_a: str,
    point_b: str,
    *,
    meta: dict[str, Any],
    direction: str,
) -> list[Any]:
    """Build a HistCAD Distance constraint entry with axis direction."""
    return [point_a, point_b, {**meta, "direction": direction}]


def _append_hole_position_constraints(
    *,
    spec: HoleArraySpec,
    face: str,
    idx: int,
    iu: int,
    iv: int,
    u: float,
    v: float,
    circle: str,
    margin_u_alias: str,
    margin_v_alias: str,
    pitch_u_alias: str,
    pitch_v_alias: str,
    distance_entries: list[Any],
) -> None:
    """Append margin/pitch Distance constraints for one hole centre."""
    if idx == 0:
        distance_entries.append(
            _distance_entry(
                "origin",
                f"{circle}.center",
                meta=_dim_meta(
                    length=u,
                    alias=margin_u_alias,
                    label=f"{face} margin u",
                    role="hole_margin",
                    default=spec.margin_u,
                    min_val=0.0,
                ),
                direction="HORIZONTAL",
            )
        )
        distance_entries.append(
            _distance_entry(
                "origin",
                f"{circle}.center",
                meta=_dim_meta(
                    length=v,
                    alias=margin_v_alias,
                    label=f"{face} margin v",
                    role="hole_margin",
                    default=spec.margin_v,
                    min_val=0.0,
                ),
                direction="VERTICAL",
            )
        )
        return

    if iu == 0:
        prev = f"circle_{idx - spec.count_u + 1}.center"
        distance_entries.append(
            _distance_entry(
                prev,
                f"{circle}.center",
                meta=_dim_meta(
                    length=spec.pitch_v,
                    alias=pitch_v_alias if iv == 1 else None,
                    expression=None if iv == 1 else f"{_SHEET}.{pitch_v_alias}",
                    label=f"{face} pitch v",
                    role="hole_pitch",
                    default=spec.pitch_v,
                    min_val=0.1,
                ),
                direction="VERTICAL",
            )
        )
        distance_entries.append(
            _distance_entry(
                "origin",
                f"{circle}.center",
                meta=_dim_meta(
                    length=u,
                    expression=f"{_SHEET}.{margin_u_alias}",
                    label=f"{face} hole {idx + 1} u",
                    role="hole_position",
                    default=u,
                ),
                direction="HORIZONTAL",
            )
        )
        return

    if iv == 0:
        prev = f"circle_{idx}.center"
        distance_entries.append(
            _distance_entry(
                prev,
                f"{circle}.center",
                meta=_dim_meta(
                    length=spec.pitch_u,
                    alias=pitch_u_alias if iu == 1 else None,
                    expression=None if iu == 1 else f"{_SHEET}.{pitch_u_alias}",
                    label=f"{face} pitch u",
                    role="hole_pitch",
                    default=spec.pitch_u,
                    min_val=0.1,
                ),
                direction="HORIZONTAL",
            )
        )
        distance_entries.append(
            _distance_entry(
                "origin",
                f"{circle}.center",
                meta=_dim_meta(
                    length=v,
                    expression=f"{_SHEET}.{margin_v_alias}",
                    label=f"{face} hole {idx + 1} v",
                    role="hole_position",
                    default=v,
                ),
                direction="VERTICAL",
            )
        )
        return

    left = f"circle_{idx}.center"
    above = f"circle_{idx - spec.count_u + 1}.center"
    distance_entries.append(
        _distance_entry(
            left,
            f"{circle}.center",
            meta=_dim_meta(
                length=spec.pitch_u,
                expression=f"{_SHEET}.{pitch_u_alias}",
                label=f"{face} hole {idx + 1} pitch u",
                role="hole_pitch",
                default=spec.pitch_u,
                min_val=0.1,
            ),
            direction="HORIZONTAL",
        )
    )
    distance_entries.append(
        _distance_entry(
            above,
            f"{circle}.center",
            meta=_dim_meta(
                length=spec.pitch_v,
                expression=f"{_SHEET}.{pitch_v_alias}",
                label=f"{face} hole {idx + 1} pitch v",
                role="hole_pitch",
                default=spec.pitch_v,
                min_val=0.1,
            ),
            direction="VERTICAL",
        )
    )


def _build_hole_sketch_constraints(
    spec: HoleArraySpec,
    *,
    diameters: list[float],
    centres: list[tuple[float, float]],
) -> tuple[dict[str, Any], list[str]]:
    """Build sketch constraints and spreadsheet aliases for one hole group.

    Uses origin-relative DistanceX/DistanceY for the first hole (margin aliases),
    inter-hole Distance for pitch aliases, and Equal radius for uniform diameter.
    """
    face = spec.face_id
    margin_u_alias = f"hole_{face}_margin_u"
    margin_v_alias = f"hole_{face}_margin_v"
    pitch_u_alias = f"hole_{face}_pitch_u"
    pitch_v_alias = f"hole_{face}_pitch_v"
    diameter_alias = f"hole_{face}_diameter"

    radius_entries: list[Any] = []
    diameter_entries: list[Any] = []
    distance_entries: list[Any] = []
    horizontal: list[str] = []
    vertical: list[str] = []
    equal_entries: list[Any] = []
    aliases: list[str] = [
        margin_u_alias,
        margin_v_alias,
        pitch_u_alias,
        pitch_v_alias,
    ]

    uniform_diameter = not isinstance(spec.diameter, list)
    if uniform_diameter:
        aliases.append(diameter_alias)

    for idx, (diam, (u, v)) in enumerate(zip(diameters, centres, strict=True)):
        circle = f"circle_{idx + 1}"
        iu = idx % spec.count_u
        iv = idx // spec.count_u

        _append_hole_position_constraints(
            spec=spec,
            face=face,
            idx=idx,
            iu=iu,
            iv=iv,
            u=u,
            v=v,
            circle=circle,
            margin_u_alias=margin_u_alias,
            margin_v_alias=margin_v_alias,
            pitch_u_alias=pitch_u_alias,
            pitch_v_alias=pitch_v_alias,
            distance_entries=distance_entries,
        )

        if uniform_diameter:
            if idx == 0:
                diameter_entries.append(
                    [
                        circle,
                        _dim_meta(
                            diameter=diam,
                            alias=diameter_alias,
                            label=f"{face} hole diameter",
                            role="hole_diameter",
                            default=diam,
                            min_val=0.1,
                        ),
                    ]
                )
            else:
                equal_entries.append(["circle_1", circle])
        else:
            alias = f"hole_{face}_r_{idx}"
            aliases.append(alias)
            radius_entries.append(
                [
                    circle,
                    _dim_meta(
                        length=diam / 2.0,
                        alias=alias,
                        label=f"{face} hole {idx + 1} radius",
                        role="hole_radius",
                        default=diam / 2.0,
                        min_val=0.1,
                    ),
                ]
            )

    constraints: dict[str, Any] = {}
    if diameter_entries:
        constraints["Diameter"] = diameter_entries
    if radius_entries:
        constraints["Radius"] = radius_entries
    if distance_entries:
        constraints["Distance"] = distance_entries
    if equal_entries:
        constraints["Equal"] = equal_entries
    if horizontal:
        constraints["Horizontal"] = horizontal
    if vertical:
        constraints["Vertical"] = vertical

    return constraints, aliases


def validate_hole_array_on_face(
    spec: HoleArraySpec,
    frame: FaceFrame,
) -> None:
    """Validate that holes fit on the face bounding box.

    Args:
        spec: Hole array specification.
        frame: Resolved face UV frame.

    Raises:
        ValueError: If holes exceed face extents or overlap pitch constraints.
    """
    validate_hole_array_spec(spec)
    diameters = _hole_diameters(spec)
    centres = _hole_centres_uv(spec, frame)

    for idx, ((u, v), diam) in enumerate(zip(centres, diameters, strict=True)):
        radius = diam / 2.0
        if u - radius < 0 or v - radius < 0:
            msg = (
                f"Hole {idx} on {spec.face_id!r} at ({u}, {v}) with "
                f"diameter {diam} exceeds face origin bounds"
            )
            raise ValueError(msg)
        if u + radius > frame.extent_u or v + radius > frame.extent_v:
            msg = (
                f"Hole {idx} on {spec.face_id!r} at ({u}, {v}) with "
                f"diameter {diam} exceeds face extent "
                f"{frame.extent_u} x {frame.extent_v}"
            )
            raise ValueError(msg)

    min_pitch = min(spec.pitch_u, spec.pitch_v)
    max_diam = max(diameters)
    if (spec.count_u > 1 or spec.count_v > 1) and max_diam >= min_pitch:
        msg = (
            f"Maximum hole diameter {max_diam} must be less than "
            f"minimum pitch {min_pitch} on face {spec.face_id!r}"
        )
        raise ValueError(msg)


def compile_hole_array_features(
    spec: HoleArraySpec,
    *,
    slots: dict[str, float],
    base_body_name: str,
    group_index: int,
) -> tuple[
    list[CoordinateSystemSpec],
    list[SketchSpec],
    list[FeatureSpec],
    str,
    list[str],
]:
    """Compile one hole group into deferred face-attached sketches and features.

    Args:
        spec: Hole array on a single semantic face.
        slots: Resolved L-connector dimension slots.
        base_body_name: Current solid name to cut from and attach sketches to.
        group_index: Index for unique sketch/tool/boolean names.

    Returns:
        Tuple of (coordinate_systems, sketches, features, updated_base_name,
        editable_aliases).

    Raises:
        ValueError: If the array is invalid or does not fit the face.
    """
    frame = resolve_l_connector_face_frame(spec.face_id, slots)
    validate_hole_array_on_face(spec, frame)

    centres = _hole_centres_uv(spec, frame)
    diameters = _hole_diameters(spec)

    sketch_entities: dict[str, Any] = {}
    for idx, ((u, v), diam) in enumerate(zip(centres, diameters, strict=True)):
        circle_name = f"circle_{idx + 1}"
        sketch_entities[circle_name] = {"center": [u, v], "radius": diam / 2.0}

    constraints, aliases = _build_hole_sketch_constraints(
        spec,
        diameters=diameters,
        centres=centres,
    )

    sketch_name = f"Holes_{spec.face_id}_{group_index}"
    tool_name = f"HoleTool_{spec.face_id}_{group_index}"
    result_name = f"L_After_Holes_{spec.face_id}_{group_index}"

    # Hole sketch plane uses the face-catalog UV origin in world space.  Sketch
    # entity coordinates and FabricationParams margin/pitch aliases are always
    # relative to this sketch origin (not L-global).  World position:
    # ``frame.uv_to_world(u, v)``.
    coordinate_system = CoordinateSystemSpec(
        euler_angles=list(frame.euler_angles),
        translation=list(frame.origin),
        name=frame.cs_name,
        attachment_support={"target": base_body_name},
        param_aliases=_face_frame_param_aliases(spec.face_id),
    )
    sketch = SketchSpec(
        sketch=sketch_entities,
        constraints=constraints,
        coordinate_system_name=frame.cs_name,
        sketch_name=sketch_name,
        attach_after_feature=base_body_name,
    )

    extrude = FeatureSpec(
        type="extrude",
        sketch_name=sketch_name,
        operation="NewBody",
        params={
            "towards": _THROUGH_EXTRA,
            # Keep hole tools fully parametric: cut depth tracks the base
            # thickness so editing FabricationParams.thickness continues to
            # produce through-holes after recompute.
            "opposite": float(frame.cut_depth),
        },
        param_aliases={
            "opposite": frame.cut_depth_alias,
        },
        feature_name=tool_name,
    )

    boolean_feat = FeatureSpec(
        type="boolean",
        sketch_name="",
        operation="Cut",
        params={
            "base_object_name": base_body_name,
            "tool_object_name": tool_name,
            "operation": "Cut",
        },
        feature_name=result_name,
    )

    return (
        [coordinate_system],
        [sketch],
        [extrude, boolean_feat],
        result_name,
        aliases,
    )


def _face_frame_param_aliases(face_id: str) -> dict[str, str]:
    if face_id == "arm_x_top":
        return {"translation.x": "arm_y_width", "translation.z": "thickness"}
    if face_id == "arm_y_top":
        return {"translation.y": "arm_x_width", "translation.z": "thickness"}
    return {}


def compile_hole_groups(
    hole_groups: list[HoleArraySpec],
    *,
    slots: dict[str, float],
    base_body_name: str = "L_Connector_Solid",
) -> tuple[
    list[CoordinateSystemSpec],
    list[SketchSpec],
    list[FeatureSpec],
    list[str],
    list[str],
]:
    """Compile all hole groups into plan fragments.

    Args:
        hole_groups: Per-face hole specifications.
        slots: Resolved L-connector slots.
        base_body_name: Initial solid name before any hole cuts.

    Returns:
        (coordinate_systems, sketches, features, hole_face_ids, editable_aliases)
    """
    all_coordinate_systems: list[CoordinateSystemSpec] = []
    all_sketches: list[SketchSpec] = []
    all_features: list[FeatureSpec] = []
    face_ids: list[str] = []
    aliases: list[str] = []

    current_base = base_body_name
    for idx, group in enumerate(hole_groups):
        cs_list, sk_list, feat_list, new_base, group_aliases = (
            compile_hole_array_features(
                group,
                slots=slots,
                base_body_name=current_base,
                group_index=idx,
            )
        )
        all_coordinate_systems.extend(cs_list)
        all_sketches.extend(sk_list)
        all_features.extend(feat_list)
        face_ids.append(group.face_id)
        aliases.extend(group_aliases)
        current_base = new_base

    return all_coordinate_systems, all_sketches, all_features, face_ids, aliases
