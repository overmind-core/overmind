"""overmind.toml — connection settings only. Capability graph comes from the
decorator manifest pushed by ``overmind sync``.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("overmind.toml")
CREDENTIALS_PATH = Path(".overmind") / "credentials.toml"


@dataclass
class Config:
    api_key: str = ""
    base_url: str = "https://api.overmindlab.ai"
    project_id: str = ""
    project_name: str = ""
    version: str = "0.3.0"


class SecretFileError(RuntimeError):
    pass


def credentials_path(config_path: Path = DEFAULT_PATH) -> Path:
    return config_path.resolve().parent / CREDENTIALS_PATH


def _safe_local_target(path: Path, repo_root: Path) -> Path:
    root = repo_root.resolve()
    target = path.absolute()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise SecretFileError(f"Refusing to write an API key outside {root}") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise SecretFileError(f"Refusing to write an API key through symlink {current}")
    resolved = target.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SecretFileError(f"Refusing to write an API key outside {root}") from exc
    return resolved


def protect_secret_file(path: Path, *, repo_root: Path) -> None:
    path = _safe_local_target(path, repo_root)
    try:
        root_result = subprocess.run(
            ["git", "-C", str(repo_root.resolve()), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        root_result = None
    if root_result is not None and root_result.returncode == 0:
        git_root = Path(root_result.stdout.strip()).resolve()
        try:
            relative = path.relative_to(git_root)
        except ValueError:
            relative = None
        if relative is not None:
            tracked = subprocess.run(
                ["git", "-C", str(git_root), "ls-files", "--error-unmatch", "--", relative.as_posix()],
                capture_output=True,
                check=False,
                text=True,
            )
            if tracked.returncode == 0:
                raise SecretFileError(f"Refusing to write an API key to tracked file {relative.as_posix()}")

            exclude_result = subprocess.run(
                ["git", "-C", str(git_root), "rev-parse", "--git-path", "info/exclude"],
                capture_output=True,
                check=False,
                text=True,
            )
            if exclude_result.returncode == 0:
                exclude_path = Path(exclude_result.stdout.strip())
                if not exclude_path.is_absolute():
                    exclude_path = git_root / exclude_path
                entry = f"/{relative.as_posix()}"
                existing = exclude_path.read_text() if exclude_path.exists() else ""
                if entry not in existing.splitlines():
                    exclude_path.parent.mkdir(parents=True, exist_ok=True)
                    exclude_path.write_text(f"{existing.rstrip()}\n{entry}\n" if existing.strip() else f"{entry}\n")

    if path.exists():
        path.chmod(0o600)


def default_project_name(toml_path: Path) -> str:
    return toml_path.resolve().parent.name or "project"


def ensure_project_name(config: Config, toml_path: Path) -> bool:
    if (config.project_name or "").strip():
        return False
    config.project_name = default_project_name(toml_path)
    return True


def _load_toml(path: Path) -> dict:
    import tomllib

    with path.open("rb") as fh:
        return tomllib.load(fh)


def saved_project_api_key(path: Path = DEFAULT_PATH) -> str:
    secret_path = credentials_path(path)
    if not path.exists() or not secret_path.exists():
        return ""
    try:
        _safe_local_target(secret_path, path.resolve().parent)
    except SecretFileError:
        return ""
    raw = _load_toml(path)
    secrets = _load_toml(secret_path)
    project_id = str(raw.get("project-id") or raw.get("project_id") or "")
    base_url = str(raw.get("base-url") or raw.get("base_url") or "https://api.overmindlab.ai")
    if (
        project_id
        and str(secrets.get("project-id") or "") == project_id
        and str(secrets.get("base-url") or "").rstrip("/") == base_url.rstrip("/")
    ):
        return str(secrets.get("api-key") or "")
    return ""


def load(path: Path = DEFAULT_PATH) -> Config:
    raw = _load_toml(path)
    project_id = str(raw.get("project-id") or raw.get("project_id") or "")
    base_url = str(raw.get("base-url") or raw.get("base_url") or "https://api.overmindlab.ai")
    return Config(
        api_key=saved_project_api_key(path) or str(raw.get("api-key") or raw.get("api_key") or ""),
        base_url=base_url,
        project_id=project_id,
        project_name=str(raw.get("project-name") or raw.get("project_name") or ""),
        version=str(raw.get("version") or "0.3.0"),
    )


def _config_to_toml_dict(config: Config) -> dict[str, Any]:
    return {
        "base-url": config.base_url,
        "project-id": config.project_id,
        "project-name": config.project_name,
        "version": config.version,
    }


def dump(config: Config, path: Path = DEFAULT_PATH) -> None:
    import tomli_w

    def write_toml(data: dict[str, Any], destination: Path, mode: int) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                tomli_w.dump(data, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(temp_name, mode)
            os.replace(temp_name, destination)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    if config.api_key and config.project_id:
        secret_path = credentials_path(path)
        protect_secret_file(secret_path, repo_root=path.resolve().parent)
        write_toml(
            {
                "api-key": config.api_key,
                "base-url": config.base_url,
                "project-id": config.project_id,
            },
            secret_path,
            0o600,
        )
    write_toml(_config_to_toml_dict(config), path, 0o644)
