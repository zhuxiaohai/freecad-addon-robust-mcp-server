#!/usr/bin/env python3
"""Compare endpoint binding on 01000571 step1 across git branches."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from freecad_mcp.bridge.xmlrpc import XmlRpcBridge  # noqa: E402

BRANCHES = ("exp", "exp_sketcher_map")
STEP_INDEX = 0  # HistCAD step1 (first NewBody extrude profile)


def _extract_embedded_code(fabrication_py: str, name: str) -> str:
    pattern = rf"{name} = r\"\"\"(.*?)\"\"\""
    match = re.search(pattern, fabrication_py, re.DOTALL)
    if not match:
        raise ValueError(f"Could not extract {name}")
    return match.group(1)


def _load_branch_codes(branch: str) -> tuple[str, str]:
    raw = subprocess.check_output(
        ["git", "show", f"{branch}:src/freecad_mcp/tools/fabrication.py"],
        cwd=ROOT,
        text=True,
    )
    return (
        _extract_embedded_code(raw, "_SKETCH_ENTITY_CODE"),
        _extract_embedded_code(raw, "_SKETCH_CONSTRAINT_CODE"),
    )


def _freecad_runner(
    branch: str,
    entity_code: str,
    constraint_code: str,
    sketch: dict,
    constraints: dict,
    cs: dict,
    doc_name: str,
) -> str:
    return f"""
import Part, json, math

{entity_code}
{constraint_code}

sketch_dict = {json.dumps(sketch)}
constraints_in = {json.dumps(constraints)}
euler = {cs.get("Euler Angles", cs.get("euler_angles", [0, 0, 0]))!r}
trans = {cs.get("Translation Vector", cs.get("translation", [0, 0, 0]))!r}
branch = {branch!r}

def placement():
    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), euler[2])
    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), euler[1]) * rot
    rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), euler[0]) * rot
    return FreeCAD.Placement(FreeCAD.Vector(*trans), rot)

def naive_pos(name, point, endpoint_map):
    if endpoint_map and name in endpoint_map:
        return endpoint_map[name].get(point, 1 if point == "start" else 2)
    return 1 if point == "start" else 2

def resolve_pt(ref, idx_map, endpoint_map):
    if "." not in ref:
        return None
    name, point = ref.split(".", 1)
    gi = idx_map[name]
    pos = naive_pos(name, point, endpoint_map)
    return sk.getPoint(gi, pos)

def coincident_gaps(pairs, idx_map, endpoint_map):
    out = []
    for a, b in pairs:
        pa, pb = resolve_pt(a, idx_map, endpoint_map), resolve_pt(b, idx_map, endpoint_map)
        gap = pa.distanceToPoint(pb) if pa and pb else -1.0
        out.append({{"pair": [a, b], "gap_mm": round(gap, 4)}})
    return out

def semantic_endpoint_errors(idx_map, endpoint_map):
  # Check JSON start/end vs getPoint at mapped (or naive) pos — pre-constraint.
    errs = {{}}
    for name, spec in sketch_dict.items():
        if "start" not in spec or "end" not in spec:
            continue
        gi = idx_map[name]
        for label, coords in (("start", spec["start"]), ("end", spec["end"])):
            pos = naive_pos(name, label, endpoint_map)
            pt = sk.getPoint(gi, pos)
            d = math.hypot(pt.x - coords[0], pt.y - coords[1])
            errs[f"{{name}}.{{label}}"] = round(d, 4)
    return errs

try:
    if FreeCAD.getDocument({doc_name!r}):
        FreeCAD.closeDocument({doc_name!r})
except Exception:
    pass

doc = FreeCAD.newDocument({doc_name!r})
sk = doc.addObject("Sketcher::SketchObject", "Step1Sketch")
sk.Label = f"01000571 step1 ({{branch}})"
sk.Placement = placement()

built = _build_sketch_entities(sk, sketch_dict)
endpoint_map = {{}}
if isinstance(built, tuple):
    idx_map, endpoint_map = built
else:
    idx_map = built

doc.recompute()

pre_semantic = semantic_endpoint_errors(idx_map, endpoint_map)
pre_coincident = coincident_gaps(constraints_in.get("Coincident", []), idx_map, endpoint_map)

if endpoint_map:
    _apply_histcad_constraints(
        sk, constraints_in, idx_map, endpoint_map=endpoint_map,
    )
else:
    _apply_histcad_constraints(sk, constraints_in, idx_map)

doc.recompute()
sk.solve()

post_coincident = coincident_gaps(constraints_in.get("Coincident", []), idx_map, endpoint_map)

_result_ = {{
    "branch": branch,
    "doc_name": doc.Name,
    "endpoint_map": endpoint_map,
    "idx_map": idx_map,
    "constraints_applied": len(sk.Constraints),
    "dof": sk.DoF,
    "pre_semantic_endpoint_err_mm": pre_semantic,
    "max_pre_semantic_err_mm": max(pre_semantic.values()) if pre_semantic else 0.0,
    "pre_coincident_gaps": pre_coincident,
    "max_pre_coincident_gap_mm": max((g["gap_mm"] for g in pre_coincident), default=0.0),
    "post_coincident_gaps": post_coincident,
    "max_post_coincident_gap_mm": max((g["gap_mm"] for g in post_coincident), default=0.0),
}}
"""


async def main() -> int:
    example = json.loads(
        (ROOT / "HistCAD-68C2/examples/01000571.json").read_text(encoding="utf-8")
    )
    step = example[STEP_INDEX]
    sketch = step["sketch"]
    constraints = step["constraints"]
    cs = step["coordinate_system"]

    bridge = XmlRpcBridge()
    try:
        await bridge.connect()
    except Exception as exc:
        print(f"FAIL: FreeCAD bridge not connected: {exc}")
        return 1

    print("=== 01000571 step1 endpoint binding: exp vs exp_sketcher_map ===\n")
    results = []
    for branch in BRANCHES:
        entity_code, constraint_code = _load_branch_codes(branch)
        doc_name = f"EndpointTest_01000571_{branch}"
        code = _freecad_runner(
            branch, entity_code, constraint_code, sketch, constraints, cs, doc_name
        )
        result = await bridge.execute_python(code)
        if not result.success or not result.result:
            print(f"[{branch}] FAIL:", result.error_traceback or result.stderr)
            return 1
        results.append(result.result)

    for out in results:
        branch = out["branch"]
        print(f"--- {branch} ---")
        print(f"FreeCAD doc: {out['doc_name']}")
        print(f"Constraints: {out['constraints_applied']}, DoF={out['dof']}")
        print(f"Max pre-constraint semantic endpoint err: {out['max_pre_semantic_err_mm']:.4f} mm")
        print(f"Max pre-constraint coincident gap: {out['max_pre_coincident_gap_mm']:.4f} mm")
        print(f"Max post-constraint coincident gap: {out['max_post_coincident_gap_mm']:.4f} mm")
        if out["endpoint_map"]:
            print("endpoint_map:")
            for name, mapping in sorted(out["endpoint_map"].items()):
                swapped = mapping.get("start") == 2
                flag = " (swapped)" if swapped and name.startswith("arc_") else ""
                print(f"  {name}: start→pos{mapping['start']} end→pos{mapping['end']}{flag}")
        else:
            print("endpoint_map: (none — uses start→1, end→2)")
        bad_pre = [k for k, v in out["pre_semantic_endpoint_err_mm"].items() if v > 0.01]
        if bad_pre:
            print("Pre-constraint semantic mismatches (>0.01mm):")
            for k in bad_pre:
                print(f"  {k}: {out['pre_semantic_endpoint_err_mm'][k]:.4f} mm")
        bad_coin_pre = [g for g in out["pre_coincident_gaps"] if g["gap_mm"] > 0.01]
        if bad_coin_pre:
            print("Pre-constraint coincident gaps (>0.01mm):")
            for g in bad_coin_pre:
                print(f"  {g['pair']}: {g['gap_mm']:.4f} mm")
        bad_coin_post = [g for g in out["post_coincident_gaps"] if g["gap_mm"] > 0.01]
        if bad_coin_post:
            print("Post-constraint coincident gaps (>0.01mm):")
            for g in bad_coin_post:
                print(f"  {g['pair']}: {g['gap_mm']:.4f} mm")
        print()

    exp_pre = results[0]["max_pre_coincident_gap_mm"]
    map_pre = results[1]["max_pre_coincident_gap_mm"]
    print("Summary:")
    print(f"  exp pre-coincident gap: {exp_pre:.4f} mm")
    print(f"  exp_sketcher_map pre-coincident gap: {map_pre:.4f} mm")
    if map_pre < 0.01 and exp_pre > 0.01:
        print("  => endpoint_map fixes pre-constraint endpoint binding on this case.")
    elif exp_pre < 0.01:
        print("  => exp branch already binds endpoints correctly pre-constraint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
