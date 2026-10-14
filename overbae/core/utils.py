import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


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
