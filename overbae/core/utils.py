import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def recurse_redact(value: Any, redact_str: Callable[[str], tuple[str, bool]]) -> tuple[Any, bool]:
    """Apply *redact_str* over every string leaf in a str / dict / list value.

    ``redact_str`` maps a string to ``(new_string, changed)``; the return is the
    rebuilt value plus whether any leaf changed.
    """
    if isinstance(value, str):
        return redact_str(value)
    if isinstance(value, dict):
        changed = False
        out: dict[Any, Any] = {}
        for k, v in value.items():
            nv, c = recurse_redact(v, redact_str)
            out[k] = nv
            changed = changed or c
        return out, changed
    if isinstance(value, list):
        changed = False
        out_list: list[Any] = []
        for v in value:
            nv, c = recurse_redact(v, redact_str)
            out_list.append(nv)
            changed = changed or c
        return out_list, changed
    return value, False


def safe_int(val, default: int = 0) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def safe_json(val) -> Any:
    """JSON-decode a string; malformed input becomes ``{"raw": val}``."""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return {"raw": val}
    return val


def safe_json_or_default(val, default=None):
    if not val:
        return default
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return default
    return val
