"""Static scan for ``os.environ`` / ``os.getenv`` usage in agent source."""

from __future__ import annotations

import ast


def _literal_to_str(node: ast.expr) -> str | None:
    """Return a simple string form for *node* if it is a literal; else ``None``."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, (int, float, bool)):
            return str(node.value)
        if node.value is None:
            return ""
    return None


def _is_os_environ_get(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "get":
        return False
    base = func.value
    if not isinstance(base, ast.Attribute) or base.attr != "environ":
        return False
    return isinstance(base.value, ast.Name) and base.value.id == "os"


def _is_os_getenv(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "getenv"
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    )


def _is_os_environ_subscript(node: ast.Subscript) -> bool:
    val = node.value
    if not isinstance(val, ast.Attribute) or val.attr != "environ":
        return False
    return isinstance(val.value, ast.Name) and val.value.id == "os"


def _env_key_from_slice(slice_node: ast.expr) -> str | None:
    # Python 3.8: ``ast.Index`` wraps the subscript expression.
    if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
        slice_node = slice_node.value
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return slice_node.value
    return None


def discover_env_var_defaults(sources: dict[str, str]) -> dict[str, str | None]:
    """Scan Python sources for environment variable reads.

    Detects:

    - ``os.environ.get("KEY", default)`` with a literal *default*
    - ``os.getenv("KEY", default)`` with a literal *default*
    - ``os.environ.get("KEY")`` / ``os.getenv("KEY")`` (maps to ``None`` default)
    - ``os.environ["KEY"]`` (maps to ``None`` default)

    Returns ``name -> default string`` or ``name -> None`` when no literal
    default exists in code. When duplicates disagree, prefer a non-``None``
    default over ``None``.
    """
    out: dict[str, str | None] = {}

    def record(key: str, value: str | None) -> None:
        if not key or not key.replace("_", "").isalnum():
            return
        if key not in out or (out[key] is None and value is not None):
            out[key] = value

    for text in sources.values():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (_is_os_getenv(node) or _is_os_environ_get(node)):
                if not node.args:
                    continue
                key = _literal_to_str(node.args[0])
                if key is None:
                    continue
                default: str | None = None
                if len(node.args) >= 2:
                    default = _literal_to_str(node.args[1])
                record(key, default)
            elif isinstance(node, ast.Subscript) and _is_os_environ_subscript(node):
                key = _env_key_from_slice(node.slice)
                if key is not None:
                    record(key, None)

    return out
