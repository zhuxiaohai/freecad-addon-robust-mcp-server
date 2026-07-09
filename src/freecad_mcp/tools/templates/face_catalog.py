"""Semantic face catalog for L-connector exterior mounting surfaces.

Each face exposes a deterministic UV frame and ``near_point`` for face
attachment.  Sketch coordinates ``(u, v)`` map to world points as::

    world = origin + u * u_axis + v * v_axis

Extrusion through the part uses ``towards`` / ``opposite`` along the face
outward normal (sketch +Z when the face coordinate system is applied).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

L_CONNECTOR_EXTERIOR_FACE_IDS: tuple[str, ...] = (
    "arm_x_top",
    "arm_y_top",
    "arm_x_outer",
    "arm_y_outer",
    "arm_x_end",
    "arm_y_end",
    "inner_corner_vertical",
    "inner_corner_horizontal",
    "bottom",
)


@dataclass(frozen=True)
class FaceFrame:
    """UV coordinate frame on one exterior face of the L connector.

    Attributes:
        face_id: Semantic identifier for the face.
        near_point: 3-D point on the face centre for ``attachment_support``.
        origin: UV origin in world coordinates (mm).
        u_axis: Unit vector for the face u-axis in world coordinates.
        v_axis: Unit vector for the face v-axis in world coordinates.
        outward_normal: Unit face normal pointing out of the solid (mm).
        extent_u: Usable face width along u (mm) for array validation.
        extent_v: Usable face height along v (mm) for array validation.
        euler_angles: Rotation [a, b, g] degrees for ``CoordinateSystemSpec``
            so sketch +X/+Y align with u/v and sketch +Z aligns with outward
            normal (HistCAD / Fusion adapter convention).
        cs_name: Suggested coordinate-system name for fabrication plans.
        offset_expressions: FreeCAD expressions binding the hole-sketch
            coordinate-system ``AttachmentOffset`` to ``FabricationParams`` aliases.
        cut_depth: Default through-hole depth along the face normal (mm).
        cut_depth_alias: Spreadsheet alias that should drive the cut depth.
    """

    face_id: str
    near_point: list[float]
    origin: list[float]
    u_axis: list[float]
    v_axis: list[float]
    outward_normal: list[float]
    extent_u: float
    extent_v: float
    euler_angles: list[float]
    cs_name: str
    offset_expressions: dict[str, str] = field(default_factory=dict)
    cut_depth: float = 0.0
    cut_depth_alias: str = "thickness"

    def uv_to_world(self, u: float, v: float) -> list[float]:
        """Convert face UV coordinates to a world-space point."""
        ox, oy, oz = self.origin
        ux, uy, uz = self.u_axis
        vx, vy, vz = self.v_axis
        return [
            ox + u * ux + v * vx,
            oy + u * uy + v * vy,
            oz + u * uz + v * vz,
        ]

    def to_dict(self) -> dict[str, Any]:
        """Serialise frame metadata for plan debugging."""
        return {
            "face_id": self.face_id,
            "near_point": self.near_point,
            "origin": self.origin,
            "extent_u": self.extent_u,
            "extent_v": self.extent_v,
            "euler_angles": self.euler_angles,
            "cs_name": self.cs_name,
        }


def _l_connector_dims(
    slots: dict[str, float],
) -> tuple[float, float, float, float, float]:
    """Return ``lx, ly, wx, wy, thick`` from resolved L-connector slots."""
    lx = float(slots["arm_x_length"])
    ly = float(slots["arm_y_length"])
    wx = float(slots["arm_x_width"])
    wy = float(slots["arm_y_width"])
    thick = float(slots["thickness"])
    return lx, ly, wx, wy, thick


def _hole_sketch_offset_expressions(face_id: str) -> dict[str, str]:
    """Build parametric AttachmentOffset expressions for a hole-sketch LCS."""
    sheet = "FabricationParams"
    if face_id == "arm_x_top":
        return {
            "AttachmentOffset.Base.x": f"{sheet}.arm_y_width",
            "AttachmentOffset.Base.y": "0 mm",
            "AttachmentOffset.Base.z": f"{sheet}.thickness",
        }
    if face_id == "arm_y_top":
        return {
            "AttachmentOffset.Base.x": "0 mm",
            "AttachmentOffset.Base.y": f"{sheet}.arm_x_width",
            "AttachmentOffset.Base.z": f"{sheet}.thickness",
        }
    return {}


def _build_face_frame(face_id: str, slots: dict[str, float]) -> FaceFrame:
    """Build a face frame for a known ``face_id``."""
    lx, ly, wx, wy, thick = _l_connector_dims(slots)

    ex, ey, ez = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]
    nx, ny, nz = [0.0, -1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, -1.0]

    builders: dict[str, FaceFrame] = {
        "arm_x_top": FaceFrame(
            face_id="arm_x_top",
            near_point=[(wy + lx) / 2.0, wx / 2.0, thick],
            origin=[wy, 0.0, thick],
            u_axis=ex,
            v_axis=ey,
            outward_normal=ez,
            extent_u=lx - wy,
            extent_v=wx,
            euler_angles=[0.0, 0.0, 0.0],
            cs_name="CS_arm_x_top",
            offset_expressions=_hole_sketch_offset_expressions("arm_x_top"),
            cut_depth=thick,
            cut_depth_alias="thickness",
        ),
        "arm_y_top": FaceFrame(
            face_id="arm_y_top",
            near_point=[wy / 2.0, (wx + ly) / 2.0, thick],
            origin=[0.0, wx, thick],
            u_axis=ex,
            v_axis=ey,
            outward_normal=ez,
            extent_u=wy,
            extent_v=ly - wx,
            euler_angles=[0.0, 0.0, 0.0],
            cs_name="CS_arm_y_top",
            offset_expressions=_hole_sketch_offset_expressions("arm_y_top"),
            cut_depth=thick,
            cut_depth_alias="thickness",
        ),
        "bottom": FaceFrame(
            face_id="bottom",
            near_point=[lx / 2.0, ly / 2.0, 0.0],
            origin=[0.0, 0.0, 0.0],
            u_axis=ex,
            v_axis=ey,
            outward_normal=nz,
            extent_u=lx,
            extent_v=ly,
            euler_angles=[180.0, 0.0, 0.0],
            cs_name="CS_bottom",
            cut_depth=thick,
            cut_depth_alias="thickness",
        ),
        "arm_x_outer": FaceFrame(
            face_id="arm_x_outer",
            near_point=[lx / 2.0, 0.0, thick / 2.0],
            origin=[0.0, 0.0, 0.0],
            u_axis=ex,
            v_axis=ez,
            outward_normal=ny,
            extent_u=lx,
            extent_v=thick,
            euler_angles=[90.0, 0.0, 0.0],
            cs_name="CS_arm_x_outer",
            cut_depth=wx,
            cut_depth_alias="arm_x_width",
        ),
        "arm_y_outer": FaceFrame(
            face_id="arm_y_outer",
            near_point=[0.0, ly / 2.0, thick / 2.0],
            origin=[0.0, 0.0, 0.0],
            u_axis=ey,
            v_axis=ez,
            outward_normal=nx,
            extent_u=ly,
            extent_v=thick,
            euler_angles=[0.0, 90.0, 0.0],
            cs_name="CS_arm_y_outer",
            cut_depth=wy,
            cut_depth_alias="arm_y_width",
        ),
        "arm_x_end": FaceFrame(
            face_id="arm_x_end",
            near_point=[lx, wx / 2.0, thick / 2.0],
            origin=[lx, 0.0, 0.0],
            u_axis=[0.0, -1.0, 0.0],
            v_axis=ez,
            outward_normal=ex,
            extent_u=wx,
            extent_v=thick,
            euler_angles=[0.0, 0.0, 90.0],
            cs_name="CS_arm_x_end",
            cut_depth=lx,
            cut_depth_alias="arm_x_length",
        ),
        "arm_y_end": FaceFrame(
            face_id="arm_y_end",
            near_point=[wy / 2.0, ly, thick / 2.0],
            origin=[0.0, ly, 0.0],
            u_axis=[-1.0, 0.0, 0.0],
            v_axis=ez,
            outward_normal=ey,
            extent_u=wy,
            extent_v=thick,
            euler_angles=[0.0, 0.0, -90.0],
            cs_name="CS_arm_y_end",
            cut_depth=ly,
            cut_depth_alias="arm_y_length",
        ),
        "inner_corner_vertical": FaceFrame(
            face_id="inner_corner_vertical",
            near_point=[wy, (wx + ly) / 2.0, thick / 2.0],
            origin=[wy, wx, 0.0],
            u_axis=ey,
            v_axis=ez,
            outward_normal=ex,
            extent_u=ly - wx,
            extent_v=thick,
            # Align sketch axes with (u,v,normal) = (+Y,+Z,+X).
            # X->Y, Y->Z, Z->X
            euler_angles=[0.0, 90.0, 90.0],
            cs_name="CS_inner_corner_vertical",
            cut_depth=wy,
            cut_depth_alias="arm_y_width",
        ),
        "inner_corner_horizontal": FaceFrame(
            face_id="inner_corner_horizontal",
            near_point=[(wy + lx) / 2.0, wx, thick / 2.0],
            # Place origin at the top edge so +v goes down into the solid.
            origin=[wy, wx, thick],
            u_axis=ex,
            # Use -Z so u x v = +Y matches outward_normal (+Y).
            v_axis=[0.0, 0.0, -1.0],
            outward_normal=ey,
            extent_u=lx - wy,
            extent_v=thick,
            euler_angles=[-90.0, 0.0, 0.0],
            cs_name="CS_inner_corner_horizontal",
            cut_depth=wx,
            cut_depth_alias="arm_x_width",
        ),
    }
    return builders[face_id]


def resolve_l_connector_face_frame(face_id: str, slots: dict[str, float]) -> FaceFrame:
    """Resolve the UV frame for one L-connector exterior face.

    Args:
        face_id: Semantic face key from :data:`L_CONNECTOR_EXTERIOR_FACE_IDS`.
        slots: Resolved L-connector dimension slots in millimetres.

    Returns:
        FaceFrame with origin, axes, extents, and placement metadata.

    Raises:
        ValueError: If ``face_id`` is unknown or dimensions are infeasible.
    """
    if face_id not in L_CONNECTOR_EXTERIOR_FACE_IDS:
        known = ", ".join(L_CONNECTOR_EXTERIOR_FACE_IDS)
        msg = f"Unknown L-connector face_id {face_id!r}. Known: {known}"
        raise ValueError(msg)

    return _build_face_frame(face_id, slots)


def all_l_connector_face_frames(slots: dict[str, float]) -> dict[str, FaceFrame]:
    """Return UV frames for every exterior face of the L connector."""
    return {
        face_id: resolve_l_connector_face_frame(face_id, slots)
        for face_id in L_CONNECTOR_EXTERIOR_FACE_IDS
    }
