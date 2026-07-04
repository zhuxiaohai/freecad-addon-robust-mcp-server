from __future__ import annotations

import argparse
import sys

from .exporter import (
    ExportOptions,
    export_json_tree_to_f3d,
    format_item_status,
    resolve_export_items,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a HistCAD JSON file/folder to a Fusion archive (.f3d) file/folder via a running Fusion 360 server."
    )
    parser.add_argument(
        "input", help="Input .json file or directory containing .json files."
    )
    parser.add_argument(
        "output", help="Output .f3d file for one input file, or output directory."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Fusion 360 server host.")
    parser.add_argument(
        "--port", type=int, default=8080, help="Fusion 360 server port."
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip target F3D files that already exist.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    options = ExportOptions(
        host=args.host,
        port=args.port,
        skip_existing=args.skip_existing,
        output_format="f3d",
    )

    try:
        items = resolve_export_items(args.input, args.output, output_format="f3d")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Selected {len(items)} JSON file(s).")
    result = export_json_tree_to_f3d(args.input, args.output, options=options)

    by_path = {item.json_path: item for item in result.items}
    for success in result.successes:
        item = by_path.get(success.json_path)
        label = item.label if item is not None else success.json_path.name
        print(f"[{label}] {format_item_status(success, output_format='f3d')}")

    for failure in result.failures:
        item = by_path.get(failure.json_path)
        label = item.label if item is not None else failure.json_path.name
        print(f"[{label}] error -> {failure.error}", file=sys.stderr)

    print(
        f"Done: {result.processed_count}/{len(result.items)} processed, "
        f"{result.skipped_existing_count} skipped existing, "
        f"{len(result.failures)} failed."
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
