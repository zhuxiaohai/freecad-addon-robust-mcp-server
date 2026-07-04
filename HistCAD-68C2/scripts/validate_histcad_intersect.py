r"""Validate HistCAD intersect examples against Fusion 360 adapter semantics.

Each example JSON defines two extrude steps whose boolean Intersect should
produce a non-zero solid.  The script also checks that the 01002396 step-1
profile (which has inner holes) produces a smaller solid than a hole-free
rebuild, confirming that FaceMakerBullseye handles multi-loop profiles.

Usage (from the HistCAD-68C2 directory or any FreeCADCmd launch path)::

    FreeCADCmd -c "exec(open('scripts/validate_histcad_intersect.py').read())"

    # Windows (WSL) — FreeCAD 1.1 example:
    "/mnt/c/Program Files/FreeCAD 1.1/bin/FreeCADCmd.exe" \
        scripts/validate_histcad_intersect.py

The script resolves its own location via ``__file__`` when executed as a
file argument, and falls back to the current working directory when run via
``-c exec(...)``.  Example JSON files are expected in ``../examples/``
relative to this script (i.e. ``HistCAD-68C2/examples/``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import FreeCAD
import Part


def _script_root() -> Path:
    """Return the directory of this script, or cwd as fallback."""
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()


SCRIPTS_DIR = _script_root()
EXAMPLES_DIR = SCRIPTS_DIR.parent / "examples"

EXAMPLES = (
    "01008879",
    "01000571",
    "01002396",
)


def _placement_from_cs(cs: dict) -> FreeCAD.Placement:
    """Active R = Rx(a)*Ry(b)*Rz(g), matching Fusion adapter."""
    euler = cs.get("Euler Angles", cs.get("euler_angles", [0, 0, 0]))
    trans = cs.get("Translation Vector", cs.get("translation", [0, 0, 0]))
    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), euler[2])
    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), euler[1]) * rot
    rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), euler[0]) * rot
    return FreeCAD.Placement(FreeCAD.Vector(*trans), rot)


def _edges_from_sketch(sketch: dict) -> list:
    """Build Part edges from a HistCAD sketch entity dict."""
    edges = []
    for name, spec in sketch.items():
        if name.startswith("line_"):
            edges.append(
                Part.makeLine(
                    FreeCAD.Vector(spec["start"][0], spec["start"][1], 0),
                    FreeCAD.Vector(spec["end"][0], spec["end"][1], 0),
                )
            )
        elif name.startswith("arc_"):
            s = FreeCAD.Vector(spec["start"][0], spec["start"][1], 0)
            m = FreeCAD.Vector(spec["middle"][0], spec["middle"][1], 0)
            e = FreeCAD.Vector(spec["end"][0], spec["end"][1], 0)
            edges.append(Part.Edge(Part.ArcOfCircle(s, m, e)))
        elif name.startswith("circle_"):
            c = FreeCAD.Vector(spec["center"][0], spec["center"][1], 0)
            edges.append(
                Part.Edge(Part.Circle(c, FreeCAD.Vector(0, 0, 1), spec["radius"]))
            )
    return edges


def _make_face(edges: list) -> Part.Face:
    """Build a face from all closed edge loops (outer + holes via Bullseye)."""
    if not edges:
        raise ValueError("no edges")
    try:
        groups = Part.sortEdges(edges)
    except Exception:
        groups = [edges]
    wires = []
    for grp in groups:
        try:
            wire = Part.Wire(grp)
            if wire.isClosed():
                wires.append(wire)
        except Exception:  # noqa: S112
            continue
    if not wires:
        best = max(groups, key=len)
        return Part.Face(Part.Wire(best))
    if len(wires) == 1:
        return Part.Face(wires[0])
    try:
        return Part.makeFace(wires, "Part::FaceMakerBullseye")
    except Exception:
        return Part.Face(wires[0])


def _extrude_step(step: dict, label: str) -> tuple[Part.Shape, float]:
    """Extrude one HistCAD step and return (solid, volume_mm3)."""
    pl = _placement_from_cs(step["coordinate_system"])
    face = _make_face(_edges_from_sketch(step["sketch"]))
    face_world = face.transformGeometry(pl.toMatrix())
    normal = pl.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
    towards = float(step.get("towards", 0) or 0)
    opposite = float(step.get("opposite", 0) or 0)
    if opposite > 0:
        solid = face_world.extrude(normal * towards).fuse(
            face_world.extrude(-normal * opposite)
        )
    else:
        solid = face_world.extrude(normal * towards)
    vol = solid.Volume
    if vol <= 0:
        raise RuntimeError(f"{label}: zero volume extrude")
    return solid, vol


def _run_example(uid: str) -> dict:
    """Run one HistCAD example; return a metrics dict."""
    path = EXAMPLES_DIR / f"{uid}.json"
    steps = json.loads(path.read_text(encoding="utf-8"))
    if len(steps) < 2:
        raise RuntimeError(f"{uid}: expected at least 2 steps")

    solid1, vol1 = _extrude_step(steps[0], f"{uid}/step1")
    solid2, vol2 = _extrude_step(steps[1], f"{uid}/step2")
    result = solid1.common(solid2)
    vol_int = result.Volume
    if vol_int <= 0:
        raise RuntimeError(f"{uid}: intersect volume is zero")

    out = {
        "uid": uid,
        "vol_step1": round(vol1, 4),
        "vol_step2": round(vol2, 4),
        "vol_intersect": round(vol_int, 4),
    }
    if uid == "01002396":
        # Step1 has inner holes; solid without holes should be noticeably larger.
        pl = _placement_from_cs(steps[0]["coordinate_system"])
        groups = Part.sortEdges(_edges_from_sketch(steps[0]["sketch"]))
        outer_wire = Part.Wire(max(groups, key=len))
        solid_no_hole = (
            Part.Face(outer_wire)
            .transformGeometry(pl.toMatrix())
            .extrude(
                pl.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
                * float(steps[0]["towards"])
            )
        )
        if vol1 >= solid_no_hole.Volume * 0.99:
            raise RuntimeError(
                f"{uid}: step1 volume {vol1} not smaller than "
                f"no-hole {solid_no_hole.Volume}"
            )
        out["vol_step1_no_hole"] = round(solid_no_hole.Volume, 4)
    return out


def main() -> int:
    """Run all HistCAD intersect examples; return 0 on success."""
    results = []
    failures = []
    log_lines = []
    for uid in EXAMPLES:
        try:
            results.append(_run_example(uid))
            line = f"PASS {uid}: {results[-1]}"
            log_lines.append(line)
            print(line)
        except Exception as exc:
            failures.append((uid, str(exc)))
            line = f"FAIL {uid}: {exc}"
            log_lines.append(line)
            print(line)

    log_path = SCRIPTS_DIR / "validate_histcad_intersect.log"
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    if failures:
        print("Validation failed:", failures, file=sys.stderr)
        return 1
    print("All examples passed:", results)
    return 0


raise SystemExit(main())
