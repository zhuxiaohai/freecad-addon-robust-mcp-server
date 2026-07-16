"""Helpers for loading structured MCP tool inputs from values or JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_structured_input(
    value: Any,
    path: str | None,
    name: str,
) -> dict[str, Any]:
    """Load a structured dict from a dict value, JSON string, or JSON file path."""
    if isinstance(value, dict):
        return value

    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{name} must be a dict or JSON string; failed to parse {name}: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{name} JSON must decode to an object")
        return parsed

    if path:
        input_path = Path(path)
        try:
            parsed = json.loads(input_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"{name}_path does not exist: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{name}_path must contain UTF-8 JSON object: {path}: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{name}_path JSON must decode to an object: {path}")
        return parsed

    raise ValueError(f"Provide {name} as a dict/JSON string or {name}_path")
