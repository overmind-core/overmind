"""Tests for overclaw.utils.env_scan."""

from __future__ import annotations

from overclaw.utils.env_scan import discover_env_var_defaults


def test_discover_getenv_with_default():
    src = 'x = os.getenv("MY_MODEL", "gpt-4o-mini")\n'
    out = discover_env_var_defaults({"t.py": src})
    assert out == {"MY_MODEL": "gpt-4o-mini"}


def test_discover_environ_get_int_default():
    src = 'n = int(os.environ.get("PORT", "8080"))\n'
    out = discover_env_var_defaults({"t.py": src})
    assert out == {"PORT": "8080"}


def test_discover_environ_get_no_default():
    src = 'k = os.environ.get("SECRET_KEY")\n'
    out = discover_env_var_defaults({"t.py": src})
    assert out == {"SECRET_KEY": None}


def test_discover_environ_subscript():
    src = 'v = os.environ["REQUIRED"]\n'
    out = discover_env_var_defaults({"t.py": src})
    assert out == {"REQUIRED": None}


def test_merge_prefers_literal_default():
    a = 'os.environ.get("X", "1")\n'
    b = 'os.environ.get("X")\n'
    out = discover_env_var_defaults({"a.py": a, "b.py": b})
    assert out["X"] == "1"
