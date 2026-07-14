#!/usr/bin/env python3
"""Verify 01000571 Intersect stays parametric and updates after parent edits."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
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
    step0, step1 = steps

    bridge = XmlRpcBridge()
    try:
        await bridge.connect()
    except Exception as exc:
        print(f"FAIL: FreeCAD bridge not connected: {exc}")
        return 1

    doc_name = f"Test_01000571_Boolean_{uuid.uuid4().hex[:8]}"
    code = f"""
import json

{_SKETCH_ENTITY_CODE}
{_SKETCH_CONSTRAINT_CODE}

def _cs_placement(cs):
    euler = cs.get("Euler Angles", cs.get("euler_angles", [0, 0, 0]))
    trans = cs.get("Translation Vector", cs.get("translation", [0, 0, 0]))
    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), euler[2])
    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), euler[1]) * rot
    rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), euler[0]) * rot
    return FreeCAD.Placement(FreeCAD.Vector(*trans), rot)

def _make_step(doc, sketch_name, extrude_name, step):
    sk = doc.addObject("Sketcher::SketchObject", sketch_name)
    sk.Placement = _cs_placement(step["coordinate_system"])
    sketch_dict = step["sketch"]
    constraints = step["constraints"]
    idx_map, endpoint_map = _build_sketch_entities(sk, sketch_dict)
    import __main__ as _main
    _main._sketch_geometry_cache = {{sk.Name: sketch_dict}}
    _main._sketch_idx_map_cache = {{sk.Name: idx_map}}
    _main._sketch_endpoint_map_cache = {{sk.Name: endpoint_map}}
    _apply_histcad_constraints(
        sk,
        constraints,
        idx_map,
        endpoint_map=endpoint_map,
        ground_truth=sketch_dict,
    )
    doc.recompute()
    sk.solve()
    ex = doc.addObject("Part::Extrusion", extrude_name)
    ex.Base = sk
    ex.DirMode = "Normal"
    ex.Dir = sk.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
    ex.FaceMakerClass = "Part::FaceMakerBullseye"
    ex.LengthFwd = step["towards"]
    ex.LengthRev = step.get("opposite", 0.0)
    ex.Solid = True
    doc.recompute()
    return sk, ex

class _FabricationBooleanProxy:
    def __init__(self, obj):
        obj.addProperty("App::PropertyLink", "Base", "Boolean", "Base solid")
        obj.addProperty("App::PropertyLink", "Tool", "Boolean", "Tool solid")
        obj.addProperty(
            "App::PropertyEnumeration",
            "Operation",
            "Boolean",
            "Boolean operation",
        )
        obj.Operation = ["Join", "Cut", "Intersect"]
        obj.Proxy = self

    def execute(self, obj):
        _base_sh = obj.Base.Shape
        _tool_sh = obj.Tool.Shape
        if obj.Operation == "Join":
            obj.Shape = _base_sh.fuse(_tool_sh)
        elif obj.Operation == "Cut":
            obj.Shape = _base_sh.cut(_tool_sh)
        else:
            obj.Shape = _base_sh.common(_tool_sh)

def _linked_intersect(base_obj, tool_obj, result_name):
    import sys as _sys

    _mod = _sys.modules.get("__main__")
    if _mod is not None:
        _mod._FabricationBooleanProxy = _FabricationBooleanProxy
    feat = doc.addObject("Part::FeaturePython", result_name)
    _FabricationBooleanProxy(feat)
    feat.Base = base_obj
    feat.Tool = tool_obj
    feat.Operation = "Intersect"
    doc.recompute()
    return feat

doc = FreeCAD.newDocument({doc_name!r})
step0 = {json.dumps(step0)}
step1 = {json.dumps(step1)}

sk1, base = _make_step(doc, "Sketch_Step1", "Extrude_Step1", step0)
sk2, tool = _make_step(doc, "Sketch_Step2", "Extrude_Step2", step1)

direct_vol = round(base.Shape.common(tool.Shape).Volume, 4)
common = _linked_intersect(base, tool, "Result_01000571")
v0 = round(common.Shape.Volume, 4)

# Move the tool solid to shrink the intersection (reliable update signal).
tool.Placement = tool.Placement * FreeCAD.Placement(
    FreeCAD.Vector(2, 0, 0), FreeCAD.Rotation()
)
doc.recompute()
v1 = round(common.Shape.Volume, 4)
direct_after = round(base.Shape.common(tool.Shape).Volume, 4)

_result_ = {{
    "direct_volume_mm3": direct_vol,
    "initial_volume_mm3": v0,
    "after_tool_move_mm3": v1,
    "direct_after_move_mm3": direct_after,
    "common_type_id": common.TypeId,
    "parametric_nonzero": v0 > 1e-6,
    "matches_direct": abs(v0 - direct_vol) < 0.1,
    "updates_after_parent_edit": abs(v1 - v0) > 0.01,
    "still_matches_direct": abs(v1 - direct_after) < 0.1,
}}
"""
    result = await bridge.execute_python(code)
    if not result.success or not result.result:
        print("FAIL: execute_python error")
        print(result.error_traceback or result.stderr)
        return 1

    out = result.result
    print("=== 01000571 Intersect Parametric Test ===")
    print(f"Direct common volume:     {out['direct_volume_mm3']:.4f} mm³")
    print(f"Linked boolean initial:   {out['initial_volume_mm3']:.4f} mm³")
    print(f"After tool move:          {out['after_tool_move_mm3']:.4f} mm³")
    print(f"Direct after move:        {out['direct_after_move_mm3']:.4f} mm³")
    print(f"Result type:              {out['common_type_id']}")
    print(f"Parametric non-zero:      {out['parametric_nonzero']}")
    print(f"Matches direct boolean:   {out['matches_direct']}")
    print(f"Updates after parent edit:{out['updates_after_parent_edit']}")
    print(f"Still matches direct:     {out['still_matches_direct']}")

    ok = (
        out["parametric_nonzero"]
        and out["matches_direct"]
        and out["common_type_id"] == "Part::FeaturePython"
        and out["updates_after_parent_edit"]
        and out["still_matches_direct"]
    )
    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
