"""
Minimal permission enums for core.

Enterprise (overmind_backend) extends these with full RBAC role resolution.
Core's NoopAuthorizationProvider never actually checks these values, but the
endpoint code references them so they need to exist.
"""

from enum import Enum


class ProjectPermission(str, Enum):
    ADMIN = "project:admin"
    ADD_CONTENT = "project:add_content"
    VIEW_CONTENT = "project:view_content"
    MANAGE_TOKENS = "project:manage_tokens"
    VIEW_SETTINGS = "project:view_settings"
