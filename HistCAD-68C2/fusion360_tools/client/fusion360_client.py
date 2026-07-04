from __future__ import annotations

import os
from typing import Any

import requests


class Fusion360Client:
    """HTTP client for the bundled Fusion 360 command server."""

    def __init__(
        self, url: str = "http://127.0.0.1:8080", timeout_seconds: float | None = None
    ):
        self.url = url.rstrip("/")
        if timeout_seconds is None:
            timeout_seconds = float(
                os.environ.get("FUSION360_CLIENT_TIMEOUT_SECONDS", "60")
            )
        self.timeout_seconds = float(timeout_seconds)
        self._session = requests.Session()
        self._session.headers.update({"Connection": "close"})

    def close(self) -> None:
        self._session.close()

    def send_command(
        self, command: str, data: dict[str, Any] | None = None, stream: bool = False
    ) -> Any:
        payload: dict[str, Any] = {"command": command}
        if data is not None:
            payload["data"] = data
        response = self._session.post(
            url=self.url,
            json=payload,
            stream=stream,
            timeout=self.timeout_seconds,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(
                f"Fusion server HTTP {response.status_code} for command {command!r}: {response.text}"
            ) from exc
        try:
            return response.json()
        except ValueError:
            return response.text

    def ping(self) -> Any:
        return self.send_command("ping")

    def clear(self) -> Any:
        return self.send_command("clear")

    def detach(self) -> Any:
        return self.send_command("detach")

    def run_batch(
        self, commands: list[dict[str, Any]], entities: dict[str, Any] | None = None
    ) -> Any:
        data: dict[str, Any] = {"commands": commands}
        if entities is not None:
            data["entities"] = entities
        return self.send_command("run_batch", data)

    def validate_sketch_constraints(self, sketch_num: int = -1) -> Any:
        return self.send_command(
            "validate_sketch_constraints", {"sketch_num": sketch_num}
        )

    def validate_all_sketch_constraints(self) -> Any:
        return self.send_command("validate_all_sketch_constraints")

    def check_constraint_satisfied(
        self,
        type: str,
        entities: list[str],
        value: Any = None,
        sketch_num: int = -1,
        extra: dict[str, Any] | None = None,
    ) -> Any:
        data: dict[str, Any] = {
            "type": type,
            "entities": entities,
            "sketch_num": sketch_num,
        }
        if value is not None:
            data["value"] = value
        if extra is not None:
            data["extra"] = extra
        return self.send_command("check_constraint_satisfied", data)

    def inspect_entities(self, entity_ids: list[str]) -> Any:
        return self.send_command("inspect_entities", {"entity_ids": entity_ids})

    def export_step(self, filepath: str) -> Any:
        return self.send_command("export_step", {"filepath": filepath})

    def export_f3d(self, filepath: str) -> Any:
        return self.send_command("export_f3d", {"filepath": filepath})

    def export_view(
        self,
        filepath: str,
        w: int = 512,
        h: int = 512,
        view_orientation: str = "iso",
    ) -> Any:
        return self.send_command(
            "export_view",
            {
                "filepath": filepath,
                "w": w,
                "h": h,
                "view_orientation": view_orientation,
            },
        )

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def _command(*args: Any, **kwargs: Any) -> Any:
            if args:
                if len(args) == 1 and isinstance(args[0], dict) and not kwargs:
                    return self.send_command(name, args[0])
                raise TypeError(f"{name} accepts keyword arguments only")
            return self.send_command(name, kwargs or None)

        return _command
