"""Canonical, kernel-neutral coordinate-system contract for fabrication tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

COORDINATE_PARAMETER_PATHS = frozenset(
    {
        "translation.x",
        "translation.y",
        "translation.z",
        "euler_angles.x",
        "euler_angles.y",
        "euler_angles.z",
    }
)


def validate_coordinate_system_payload(
    payload: dict[str, Any],
    add_error: Callable[[str, str, str], None],
    path: str,
    *,
    known_targets: set[str] | None = None,
) -> None:
    """Validate the public coordinate-system contract used by all callers."""
    for field in ("euler_angles", "translation"):
        value = payload.get(field)
        if not (
            isinstance(value, list)
            and len(value) == 3
            and all(isinstance(item, int | float) for item in value)
        ):
            add_error(f"{path}.{field}", "must be a 3-number list", "type_error")

    attachment = payload.get("attachment_support")
    if attachment is not None:
        if not isinstance(attachment, dict) or set(attachment) != {"target"}:
            add_error(
                f"{path}.attachment_support",
                "must be {'target': '<upstream object or feature name>'}",
                "bad_attachment",
            )
        elif not isinstance(attachment["target"], str) or not attachment["target"]:
            add_error(
                f"{path}.attachment_support.target",
                "must be a non-empty string",
                "type_error",
            )
        elif known_targets is not None and attachment["target"] not in known_targets:
            add_error(
                f"{path}.attachment_support.target",
                f"unknown upstream target {attachment['target']!r}",
                "unknown_ref",
            )

    aliases = payload.get("param_aliases", {})
    if not isinstance(aliases, dict):
        add_error(f"{path}.param_aliases", "must be a dict", "type_error")
    else:
        for prop, alias in aliases.items():
            if prop not in COORDINATE_PARAMETER_PATHS:
                add_error(
                    f"{path}.param_aliases.{prop}",
                    "only translation.x/y/z and euler_angles.x/y/z are supported",
                    "unsupported",
                )
            if not isinstance(alias, str) or not alias.isidentifier():
                add_error(
                    f"{path}.param_aliases.{prop}",
                    "alias must be a Python identifier",
                    "bad_alias",
                )
