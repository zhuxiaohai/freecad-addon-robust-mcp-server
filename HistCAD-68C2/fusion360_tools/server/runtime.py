import os
import time

import adsk.core

_LAST_DO_EVENTS_TS = 0.0


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def maybe_do_events(force: bool = False) -> bool:
    global _LAST_DO_EVENTS_TS

    if not _env_flag("FUSION360_SERVER_DO_EVENTS", default=True):
        return False

    interval_seconds = max(
        0.0,
        _env_float("FUSION360_SERVER_DO_EVENTS_INTERVAL_SECONDS", default=0.25),
    )
    now = time.monotonic()
    if (
        not force
        and interval_seconds > 0.0
        and (now - _LAST_DO_EVENTS_TS) < interval_seconds
    ):
        return False

    try:
        adsk.doEvents()
    except Exception:
        return False

    _LAST_DO_EVENTS_TS = now
    return True
