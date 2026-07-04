from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .exporter import (
    ExportOptions,
    export_json_tree,
    export_json_tree_to_f3d,
    format_item_status,
)

TOOL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = TOOL_ROOT / "examples" / "test1.json"
DEFAULT_OUTPUT_DIR = TOOL_ROOT / "outputs" / "export_test1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bundled test1.json through the STEP and F3D exporters."
    )
    parser.add_argument(
        "--input", default=str(DEFAULT_INPUT), help="Input HistCAD JSON file."
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for test exports.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Fusion 360 server host.")
    parser.add_argument(
        "--port", type=int, default=8080, help="Fusion 360 server port."
    )
    parser.add_argument(
        "--skip-existing", action="store_true", help="Reuse existing STEP/F3D outputs."
    )
    return parser


def _print_result(label: str, result, *, output_format: str) -> bool:
    for success in result.successes:
        print(f"[{label}] {format_item_status(success, output_format=output_format)}")
    for failure in result.failures:
        print(f"[{label}] error -> {failure.error}", file=sys.stderr)
    return result.ok


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    options = ExportOptions(
        host=args.host, port=args.port, skip_existing=args.skip_existing
    )

    step_result = export_json_tree(
        input_path, output_dir / "test1.step", options=options
    )
    f3d_result = export_json_tree_to_f3d(
        input_path, output_dir / "test1.f3d", options=options
    )

    ok = True
    ok = _print_result("STEP", step_result, output_format="step") and ok
    ok = _print_result("F3D", f3d_result, output_format="f3d") and ok
    print(f"Outputs written under: {output_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
