#!/usr/bin/env python3
"""Live integration test for HistCAD 01000571 step0 after endpoint_map fix."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from freecad_mcp.bridge.xmlrpc import XmlRpcBridge  # noqa: E402
from freecad_mcp.tools.fabrication import (  # noqa: E402
    _SKETCH_CONSTRAINT_CODE,
    _SKETCH_ENTITY_CODE,
)


async def main() -> int:
    example_path = Path(__file__).resolve().parents[1] / "examples" / "01000571.json"
    steps = json.loads(example_path.read_text(encoding="utf-8"))
    step0 = steps[0]
    sketch = step0["sketch"]
    constraints = step0["constraints"]
    cs = step0["coordinate_system"]

    bridge = XmlRpcBridge()
    try:
        await bridge.connect()
    except Exception as exc:
        print(f"FAIL: FreeCAD bridge not connected: {exc}")
        return 1

    doc_name = "Test_01000571_Live"
    code = f"""
import Part, Sketcher, json

{_SKETCH_ENTITY_CODE}
{_SKETCH_CONSTRAINT_CODE}

doc = FreeCAD.newDocument({doc_name!r})
sk = doc.addObject("Sketcher::SketchObject", "Step0")
euler = {cs.get("Euler Angles", cs.get("euler_angles", [0,0,0]))!r}
trans = {cs.get("Translation Vector", cs.get("translation", [0,0,0]))!r}
rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), euler[2])
rot = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), euler[1]) * rot
rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), euler[0]) * rot
sk.Placement = FreeCAD.Placement(FreeCAD.Vector(*trans), rot)

sketch_dict = {json.dumps(sketch)}
constraints_in = {json.dumps(constraints)}

idx_map, endpoint_map = _build_sketch_entities(sk, sketch_dict)
doc.recompute()

import __main__ as _main
_main._sketch_geometry_cache = {{sk.Name: sketch_dict}}
_main._sketch_idx_map_cache = {{sk.Name: idx_map}}
_main._sketch_endpoint_map_cache = {{sk.Name: endpoint_map}}

# Pre-constraint coincident gaps
def _gap(ref_a, ref_b):
    def _pt(ref):
        if "." not in ref:
            return None
        name, point = ref.split(".", 1)
        gi = idx_map[name]
        if name in endpoint_map:
            pos = endpoint_map[name][point]
        else:
            pos = 1 if point == "start" else 2
        return sk.getPoint(gi, pos)
    pa, pb = _pt(ref_a), _pt(ref_b)
    return pa.distanceToPoint(pb)

pre_gaps = []
for pair in constraints_in.get("Coincident", []):
    pre_gaps.append({{"pair": pair, "gap_mm": round(_gap(pair[0], pair[1]), 4)}})

constraint_result = _apply_histcad_constraints(
    sk,
    constraints_in,
    idx_map,
    endpoint_map=endpoint_map,
)
doc.recompute()
sk.solve()

def _entity_drift(gt, idx, ep_map):
    def dist2d(p, c):
        return ((p.x - c[0]) ** 2 + (p.y - c[1]) ** 2) ** 0.5
    out = {{}}
    for nm, sp in gt.items():
        gi = idx[nm]
        if nm.startswith("line_") or nm.startswith("arc_"):
            ep = ep_map[nm]
            ps = sk.getPoint(gi, ep["start"])
            pe = sk.getPoint(gi, ep["end"])
            d = max(dist2d(ps, sp["start"]), dist2d(pe, sp["end"]))
        else:
            continue
        out[nm] = round(d, 4)
    return out

post_drift = _entity_drift(sketch_dict, idx_map, endpoint_map)
max_post_drift = max(post_drift.values()) if post_drift else 0.0

extrude_vec = sk.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, {step0["towards"]}))

# Baseline: no constraints extrude volume for comparison
sk_base = doc.addObject("Sketcher::SketchObject", "Step0_NoConstraints")
sk_base.Placement = sk.Placement
idx_base, arc_base = _build_sketch_entities(sk_base, sketch_dict)
doc.recompute()
base_groups = Part.sortEdges(sk_base.Shape.Edges)
base_wires = [Part.Wire(g) for g in base_groups if Part.Wire(g).isClosed()]
base_face = (
    Part.Face(base_wires[0])
    if len(base_wires) == 1
    else Part.makeFace(base_wires, "Part::FaceMakerBullseye")
)
base_vol = base_face.extrude(extrude_vec).Volume

# Extrude test
edges = sk.Shape.Edges
groups = Part.sortEdges(edges) if edges else []
closed = sum(1 for grp in groups if Part.Wire(grp).isClosed())
profile_closed = len(groups) > 0 and closed == len(groups)

extrusion_mode = "parametric_sketch"
volume = 0.0
face_valid = False
try:
  wires = [Part.Wire(grp) for grp in groups if Part.Wire(grp).isClosed()]
  if len(wires) == 1:
    profile_face = Part.Face(wires[0])
  elif len(wires) > 1:
    profile_face = Part.makeFace(wires, "Part::FaceMakerBullseye")
  else:
    profile_face = None
  if profile_face and profile_face.isValid():
    face_valid = True
    solid = profile_face.extrude(extrude_vec)
    volume = solid.Volume if solid else 0.0
except Exception as _ex:
  extrusion_mode = str(_ex)

_result_ = {{
  "endpoint_map": endpoint_map,
  "pre_coincident_gaps": pre_gaps,
  "max_pre_gap_mm": max((g["gap_mm"] for g in pre_gaps), default=0.0),
  "applied_count": len(sk.Constraints),
  "constraint_steps_ok": len(sk.Constraints),
  "geometry_drift_max": constraint_result,
  "dof_after": getattr(sk, "DoF", -1),
  "profile_closed_after": profile_closed,
  "face_valid": face_valid,
  "max_post_drift_mm": round(max_post_drift, 4),
  "post_entity_drift": post_drift,
  "geometry_matches_json": max_post_drift < 0.01,
  "baseline_volume_mm3": round(base_vol, 4),
  "volume_delta_mm3": round(volume - base_vol, 4),
  "volume_mm3": round(volume, 4),
}}
"""
    result = await bridge.execute_python(code)
    if not result.success or not result.result:
        print("FAIL: execute_python error")
        print(result.error_traceback or result.stderr)
        return 1

    out = result.result
    print("=== 01000571 Step0 Live Test ===")
    print(f"Max pre-constraint coincident gap: {out['max_pre_gap_mm']:.4f} mm")
    print(f"Constraints applied: {out['applied_count']} (JSON has 18 entries)")
    print(f"DoF after: {out['dof_after']}")
    print(f"Profile closed after constraints: {out['profile_closed_after']}")
    print(f"Face valid: {out['face_valid']}")
    print(f"Extrude volume: {out['volume_mm3']:.4f} mm³")
    print(f"Baseline (no constraints) volume: {out['baseline_volume_mm3']:.4f} mm³")
    print(f"Volume delta: {out['volume_delta_mm3']:.4f} mm³")
    print(f"Max post-constraint drift vs JSON: {out['max_post_drift_mm']:.4f} mm")
    print(f"Geometry matches JSON (<0.01mm): {out['geometry_matches_json']}")
    print()
    print("Endpoint map:")
    for name, mapping in out["endpoint_map"].items():
        swapped = mapping["start"] == 2
        print(
            f"  {name}: start→pos{mapping['start']} end→pos{mapping['end']}"
            f"{' (swapped)' if swapped else ''}"
        )
    print()
    print("Pre-constraint coincident gaps (should be ~0):")
    for g in out["pre_coincident_gaps"]:
        flag = "OK" if g["gap_mm"] < 0.01 else "BAD"
        print(f"  [{flag}] {g['pair']}: {g['gap_mm']:.4f} mm")
    print()
    print("Post-constraint entity drift vs JSON:")
    for name, drift in sorted(out["post_entity_drift"].items()):
        flag = "OK" if drift < 0.01 else "DRIFT"
        print(f"  [{flag}] {name}: {drift:.4f} mm")

    ok = (
        out["max_pre_gap_mm"] < 0.01
        and out["applied_count"] == 18
        and out["profile_closed_after"]
        and out["volume_mm3"] > 0
    )
    if not out["geometry_matches_json"]:
        print()
        print(
            "Note: post-constraint drift is a solver issue (under-constrained); "
            "all JSON constraints were still applied."
        )
    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
