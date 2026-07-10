#!/usr/bin/env python3
"""Compare parametric extrude across native vs fabrication constraint paths."""

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
    step0 = json.loads(example_path.read_text(encoding="utf-8"))[0]
    sketch = step0["sketch"]
    constraints = step0["constraints"]
    cs = step0["coordinate_system"]
    towards = step0["towards"]

    bridge = XmlRpcBridge()
    try:
        await bridge.connect()
    except Exception as exc:
        print(f"FAIL: FreeCAD bridge not connected: {exc}")
        return 1

    code = f"""
import Part, Sketcher, json

{_SKETCH_ENTITY_CODE}
{_SKETCH_CONSTRAINT_CODE}

sketch_dict = {json.dumps(sketch)}
constraints_in = {json.dumps(constraints)}
euler = {cs.get("Euler Angles", cs.get("euler_angles", [0,0,0]))!r}
trans = {cs.get("Translation Vector", cs.get("translation", [0,0,0]))!r}
towards = {towards!r}

def _placement():
    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), euler[2])
    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), euler[1]) * rot
    rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), euler[0]) * rot
    return FreeCAD.Placement(FreeCAD.Vector(*trans), rot)

def _try_parametric_extrude(sk):
    _pl = sk.Placement
    _n = _pl.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
    e = doc.addObject("Part::Extrusion", "TmpExtrude")
    e.Base = sk
    e.DirMode = "Normal"
    e.Dir = _n
    e.FaceMakerClass = "Part::FaceMakerBullseye"
    e.LengthFwd = towards
    e.Solid = True
    doc.recompute()
    try:
        sh = e.Shape
        out = {{
            "null": sh.isNull(),
            "valid": sh.isValid() if not sh.isNull() else False,
            "volume": round(sh.Volume, 4) if not sh.isNull() else 0.0,
            "error": None,
        }}
    except Exception as ex:
        out = {{"null": True, "valid": False, "volume": 0.0, "error": str(ex)}}
    doc.removeObject(e.Name)
    return out

def _try_face_extrude(sk):
    _pl = sk.Placement
    _n = _pl.Rotation.multVec(FreeCAD.Vector(0, 0, towards))
    edges = sk.Shape.Edges
    groups = Part.sortEdges(edges) if edges else []
    try:
        wires = [Part.Wire(grp) for grp in groups if Part.Wire(grp).isClosed()]
        if len(wires) == 1:
            face = Part.Face(wires[0])
        elif len(wires) > 1:
            face = Part.makeFace(wires, "Part::FaceMakerBullseye")
        else:
            return {{"volume": 0.0, "error": "no closed wire"}}
        solid = face.extrude(_n)
        return {{"volume": round(solid.Volume, 4), "error": None}}
    except Exception as ex:
        return {{"volume": 0.0, "error": str(ex)}}

def _native_constraints(sk, idx_map, arc_map):
    def rp(ref):
        name, point = ref.split(".", 1)
        gi = idx_map[name]
        if name.startswith("arc_"):
            pos = arc_map[name][point]
        else:
            pos = 1 if point == "start" else 2
        return gi, pos

    coinc_map = {{}}
    for pair in constraints_in.get("Coincident", []):
        n1, p1 = pair[0].split(".", 1)
        n2, p2 = pair[1].split(".", 1)
        coinc_map[(n1, p1)] = (n2, p2)
        coinc_map[(n2, p2)] = (n1, p1)

    doc.openTransaction("native constraints")
    for pair in constraints_in.get("Coincident", []):
        g1, p1 = rp(pair[0])
        g2, p2 = rp(pair[1])
        sk.addConstraint(Sketcher.Constraint("Coincident", g1, p1, g2, p2))
    for pair in constraints_in.get("Diameter", []):
        sk.addConstraint(Sketcher.Constraint("Radius", idx_map[pair[0]], pair[1] / 2.0))
    for entry in constraints_in.get("Distance", []):
        g1, p1 = rp(entry[0])
        g2, p2 = rp(entry[1])
        spec = entry[2]
        direction = spec.get("direction", "HORIZONTAL")
        length = spec["length"]
        ctype = "DistanceY" if direction == "VERTICAL" else "DistanceX"
        sk.addConstraint(Sketcher.Constraint(ctype, g1, p1, g2, p2, length))
    for pair in constraints_in.get("Radius", []):
        sk.addConstraint(Sketcher.Constraint("Radius", idx_map[pair[0]], pair[1]))
    for ref1, ref2 in constraints_in.get("Tangent", []):
        shared = None
        for pn in ("start", "end"):
            k = (ref1, pn)
            if k in coinc_map and coinc_map[k][0] == ref2:
                shared = (pn, coinc_map[k][1])
                break
        if shared is None:
            for pn in ("start", "end"):
                k = (ref2, pn)
                if k in coinc_map and coinc_map[k][0] == ref1:
                    shared = (coinc_map[k][1], pn)
                    break
        if shared is not None:
            def jpos(name, point):
                if name.startswith("arc_"):
                    return arc_map[name][point]
                return 1 if point == "start" else 2

            g1, p1 = idx_map[ref1], jpos(ref1, shared[0])
            g2, p2 = idx_map[ref2], jpos(ref2, shared[1])
            sk.addConstraint(Sketcher.Constraint("Tangent", g1, p1, g2, p2))
        else:
            sk.addConstraint(Sketcher.Constraint("Tangent", idx_map[ref1], idx_map[ref2]))
    doc.recompute()
    sk.solve()
    doc.commitTransaction()

doc = FreeCAD.newDocument("CompareExtrudePaths")
results = {{}}

# A: fabrication geometry + fabrication constraints
sk_a = doc.addObject("Sketcher::SketchObject", "FabGeom_FabCons")
sk_a.Placement = _placement()
idx_a, ep_a = _build_sketch_entities(sk_a, sketch_dict)
doc.recompute()
import __main__ as _main
_main._sketch_geometry_cache = {{sk_a.Name: sketch_dict}}
_main._sketch_idx_map_cache = {{sk_a.Name: idx_a}}
_main._sketch_endpoint_map_cache = {{sk_a.Name: ep_a}}
_apply_histcad_constraints(sk_a, constraints_in, idx_a, endpoint_map=ep_a)
doc.recompute(); sk_a.solve()
results["fab_geom_fab_cons"] = {{
    "constraints": len(sk_a.Constraints),
    "dof": getattr(sk_a, "DoF", -1),
    "face_extrude": _try_face_extrude(sk_a),
    "part_extrusion": _try_parametric_extrude(sk_a),
}}

# B: fabrication geometry + native constraints (correct arc endpoint map)
sk_b = doc.addObject("Sketcher::SketchObject", "FabGeom_NativeCons")
sk_b.Placement = _placement()
idx_b, arc_b = _build_sketch_entities(sk_b, sketch_dict)
_native_constraints(sk_b, idx_b, arc_b)
results["fab_geom_native_cons"] = {{
    "constraints": len(sk_b.Constraints),
    "dof": getattr(sk_b, "DoF", -1),
    "face_extrude": _try_face_extrude(sk_b),
    "part_extrusion": _try_parametric_extrude(sk_b),
}}

# C: fabrication geometry + native constraints (WRONG: naive arc 1=start 2=end)
sk_c = doc.addObject("Sketcher::SketchObject", "FabGeom_NativeCons_WrongMap")
sk_c.Placement = _placement()
idx_c, arc_c = _build_sketch_entities(sk_c, sketch_dict)
wrong_map = {{n: {{"start": 1, "end": 2}} for n in arc_c}}
_native_constraints(sk_c, idx_c, wrong_map)
results["fab_geom_native_cons_wrong_map"] = {{
    "constraints": len(sk_c.Constraints),
    "dof": getattr(sk_c, "DoF", -1),
    "face_extrude": _try_face_extrude(sk_c),
    "part_extrusion": _try_parametric_extrude(sk_c),
}}

# D: fabrication geom + fabrication constraints but WITHOUT tangent (16)
sk_d = doc.addObject("Sketcher::SketchObject", "FabGeom_NoTangent")
sk_d.Placement = _placement()
idx_d, ep_d = _build_sketch_entities(sk_d, sketch_dict)
cons_no_tan = dict(constraints_in)
cons_no_tan.pop("Tangent", None)
_apply_histcad_constraints(sk_d, cons_no_tan, idx_d, endpoint_map=ep_d)
doc.recompute(); sk_d.solve()
results["fab_geom_no_tangent"] = {{
    "constraints": len(sk_d.Constraints),
    "dof": getattr(sk_d, "DoF", -1),
    "face_extrude": _try_face_extrude(sk_d),
    "part_extrusion": _try_parametric_extrude(sk_d),
}}

_result_ = results
"""
    result = await bridge.execute_python(code)
    if not result.success or not result.result:
        print("FAIL:", result.error_traceback or result.stderr)
        return 1

    print("=== 01000571 Step0 Parametric Extrude Comparison ===\n")
    labels = {
        "fab_geom_fab_cons": "Fabrication草图 + Fabrication约束(18条含Tangent)",
        "fab_geom_native_cons": "Fabrication草图 + 原生约束(正确端点map)",
        "fab_geom_native_cons_wrong_map": "Fabrication草图 + 原生约束(错误端点map)",
        "fab_geom_no_tangent": "Fabrication草图 + Fabrication约束(16条无Tangent)",
    }
    for key, label in labels.items():
        row = result.result[key]
        pe = row["part_extrusion"]
        fe = row["face_extrude"]
        pe_ok = (not pe["null"]) and pe["valid"] and pe["volume"] > 0
        fe_ok = fe["volume"] > 0
        print(f"{label}")
        print(f"  constraints={row['constraints']} dof={row['dof']}")
        print(
            f"  Part::Extrusion(parametric): {'OK' if pe_ok else 'FAIL'} "
            f"volume={pe['volume']} err={pe.get('error')}"
        )
        print(
            f"  Face直拉(robust路径): {'OK' if fe_ok else 'FAIL'} "
            f"volume={fe['volume']} err={fe.get('error')}"
        )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
