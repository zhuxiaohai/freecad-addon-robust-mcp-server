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

COORDINATE_BINDING_KINDS = frozenset({"feature_param", "sketch_constraint"})


def validate_coordinate_system_payload(  # noqa: PLR0912
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

    bindings = payload.get("param_bindings", {})
    if not isinstance(bindings, dict):
        add_error(f"{path}.param_bindings", "must be a dict", "type_error")
        return
    for prop, binding in bindings.items():
        binding_path = f"{path}.param_bindings.{prop}"
        if prop not in {"translation.x", "translation.y", "translation.z"}:
            add_error(
                binding_path,
                "only translation.x/y/z support direct bindings",
                "unsupported",
            )
        source = binding.get("source") if isinstance(binding, dict) else None
        if (
            not isinstance(source, dict)
            or source.get("kind") not in COORDINATE_BINDING_KINDS
        ):
            add_error(
                binding_path,
                "source.kind must be feature_param or sketch_constraint",
                "bad_binding",
            )
            continue
        if source["kind"] == "feature_param":
            if (
                not isinstance(source.get("feature_name"), str)
                or not source["feature_name"]
            ):
                add_error(
                    binding_path,
                    "feature_param requires source.feature_name",
                    "bad_binding",
                )
            if source.get("param") not in {"towards", "opposite"}:
                add_error(
                    binding_path,
                    "feature_param requires source.param towards or opposite",
                    "bad_binding",
                )
        if source["kind"] == "sketch_constraint":
            if (
                not isinstance(source.get("sketch_name"), str)
                or not source["sketch_name"]
            ):
                add_error(
                    binding_path,
                    "sketch_constraint requires source.sketch_name",
                    "bad_binding",
                )
            if (
                not isinstance(source.get("constraint_alias"), str)
                or not source["constraint_alias"]
            ):
                add_error(
                    binding_path,
                    "sketch_constraint requires source.constraint_alias",
                    "bad_binding",
                )
