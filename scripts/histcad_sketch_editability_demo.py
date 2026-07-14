#!/usr/bin/env python3
"""Run a HistCAD-aligned sketch editability case without restarting MCP.

Uses local ``register_fabrication_tools`` (latest code) with ``XmlRpcBridge``
(FreeCAD XML-RPC on port 9875).  Iteration workflow when MCP tools are stale::

    uv run python scripts/histcad_sketch_editability_demo.py

Follows ``HistCAD-68C2/editability/experiment.py`` sketch scoring:

1. Replay step0 geometry from JSON (fresh document).
2. ``modify_dimension``: Distance 1.5 mm → 1.8 mm via edited constraints.
3. Recompute + sketch/shape validation (``rebuild_success``, ``validation_ok``).
4. Geometric constraint verification → ER / cPCSR / OES (metrics.py v2).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from freecad_mcp.bridge.xmlrpc import XmlRpcBridge  # noqa: E402
from freecad_mcp.tools.fabrication import register_fabrication_tools  # noqa: E402

EXAMPLE_JSON = PROJECT_ROOT / "HistCAD-68C2" / "examples" / "01000571.json"
DOC_NAME = "_histcad_edit_demo"
EDITED_DISTANCE_MM = 1.8


def _register_tools(bridge: XmlRpcBridge) -> dict[str, Any]:
    """Register fabrication tools against a live bridge."""
    mcp = MagicMock()
    registered: dict[str, Any] = {}

    def tool_decorator() -> Any:
        def wrapper(func: Any) -> Any:
            registered[func.__name__] = func
            return func

        return wrapper

    mcp.tool = tool_decorator

    async def get_bridge() -> XmlRpcBridge:
        return bridge

    register_fabrication_tools(mcp, get_bridge)
    return registered


async def _ensure_document(bridge: XmlRpcBridge, doc_name: str) -> None:
    """Create or replace a FreeCAD document by internal name."""
    result = await bridge.execute_python(
        f"""
import FreeCAD
if {doc_name!r} in FreeCAD.listDocuments():
    FreeCAD.closeDocument({doc_name!r})
doc = FreeCAD.newDocument({doc_name!r})
_result_ = {{"name": doc.Name, "label": doc.Label}}
"""
    )
    if not result.success:
        raise RuntimeError(result.error_traceback or "Failed to create document")


async def _replay_step0(
    bridge: XmlRpcBridge,
    tools: dict[str, Any],
    step0: dict[str, Any],
) -> None:
    """Build step0 sketch with constraints from ``step0``."""
    await _ensure_document(bridge, DOC_NAME)

    cs = step0["coordinate_system"]
    euler = cs.get("Euler Angles") or cs.get("euler_angles")
    translation = cs.get("Translation Vector") or cs.get("translation")
    await tools["create_coordinate_system"](
        euler_angles=euler,
        translation=translation,
        name="CoordinateSystem",
        doc_name=DOC_NAME,
    )
    await tools["create_sketch_geometry"](
        sketch=step0["sketch"],
        coordinate_system_name="CoordinateSystem",
        doc_name=DOC_NAME,
    )
    await tools["apply_sketch_constraints"](
        sketch_name="Sketch",
        constraints=step0["constraints"],
        doc_name=DOC_NAME,
    )
    await tools["execute_extrude"](
        sketch_name="Sketch",
        towards=float(step0["towards"]),
        opposite=float(step0.get("opposite", 0.0)),
        doc_name=DOC_NAME,
    )


async def _evaluate_case(
    tools: dict[str, Any],
    reference_constraints: dict[str, Any],
    *,
    constraint_type: str,
    entry_index: int,
    edited_value_mm: float,
    label: str,
) -> dict[str, Any]:
    """Score one edit case with HistCAD v2 metrics."""
    metrics = await tools["evaluate_sketch_editability"](
        reference_constraints=reference_constraints,
        constraint_type=constraint_type,
        entry_index=entry_index,
        edited_value_mm=edited_value_mm,
        sketch_name="Sketch",
        doc_name=DOC_NAME,
    )
    print(f"\n=== {label} ===")
    print(f"  metric_definition : {metrics.get('metric_definition')}")
    print(f"  target_hit        : {metrics.get('target_hit')}")
    print(f"  rebuild_success   : {metrics.get('rebuild_success')}")
    print(f"  validation_ok     : {metrics.get('validation_ok')}")
    print(f"  ER                : {metrics.get('ER')}")
    print(f"  cPCSR             : {metrics.get('cPCSR')}")
    print(f"  OES               : {metrics.get('OES')}")
    if metrics.get("failed_preserved"):
        print(f"  failed_preserved  : {len(metrics['failed_preserved'])}")
    validation = metrics.get("validation") or {}
    if validation.get("sketches"):
        sk = validation["sketches"][0]
        print(
            f"  sketch health     : dof={sk.get('dof')}, "
            f"conflicting={sk.get('conflicting')}"
        )
    return metrics


async def main() -> int:
    """Run baseline + modify_dimension edit case."""
    if not EXAMPLE_JSON.is_file():
        print(f"Missing example JSON: {EXAMPLE_JSON}", file=sys.stderr)
        return 1

    payload = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    step0 = payload[0]
    reference_constraints = step0["constraints"]

    bridge = XmlRpcBridge()
    await bridge.connect()
    tools = _register_tools(bridge)

    print("Replaying step0 (original constraints) …")
    await _replay_step0(bridge, tools, step0)

    baseline = await _evaluate_case(
        tools,
        reference_constraints,
        constraint_type="Distance",
        entry_index=0,
        edited_value_mm=1.5,
        label="Baseline (no edit, Distance=1.5 mm)",
    )

    edited = tools["build_edited_sketch_constraints"](
        constraints=reference_constraints,
        constraint_type="Distance",
        entry_index=0,
        edited_value_mm=EDITED_DISTANCE_MM,
    )
    step0_edited = dict(step0)
    step0_edited["constraints"] = edited["constraints"]

    # HistCAD replays with edited payload — fresh sketch, not stacked constraints.
    print(f"\nReplaying step0 with edited Distance={EDITED_DISTANCE_MM} mm …")
    await _replay_step0(bridge, tools, step0_edited)

    edit_metrics = await _evaluate_case(
        tools,
        reference_constraints,
        constraint_type="Distance",
        entry_index=0,
        edited_value_mm=EDITED_DISTANCE_MM,
        label=f"modify_dimension (Distance → {EDITED_DISTANCE_MM} mm)",
    )

    print("\n=== HistCAD v2 identity check ===")
    er = float(edit_metrics.get("ER") or 0.0)
    cpcsr = float(edit_metrics.get("cPCSR") or 0.0)
    oes = float(edit_metrics.get("OES") or 0.0)
    print(f"  OES ~ ER x cPCSR : {oes:.4f} ~ {er:.4f} x {cpcsr:.4f}")

    ok = (
        baseline.get("metric_definition") == "histcad_v2"
        and edit_metrics.get("metric_definition") == "histcad_v2"
        and baseline.get("OES") == 1.0
    )
    print(f"\nDemo {'PASSED' if ok else 'completed with warnings'}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
