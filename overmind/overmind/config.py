"""overmind.toml — version-tracked local snapshot, synced via ``overmind sync``."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("overmind.toml")
CREDENTIALS_PATH = Path(".overmind") / "credentials.toml"


_CAP_FIELDS = (
    "id",
    "slug",
    "name",
    "description",
    "entrypoint_fn",
    "model",
    "source_path",
    "system_prompt",
    "tools_summary",
)
_METRIC_FIELDS = (
    "name",
    "type",
    "prompt",
    "measures",
    "rationale",
    "rubric",
    "requires_reference",
    "managed_name",
)


@dataclass
class EvalMetric:
    name: str
    type: str = "llm_judge_custom"
    prompt: str = ""
    measures: str = ""
    rationale: str = ""
    rubric: str = ""
    requires_reference: bool = False
    managed_name: str = ""


@dataclass
class Capability:
    slug: str
    name: str
    id: str = ""
    description: str = ""
    entrypoint_fn: str = ""
    model: str = ""
    source_path: str = ""
    system_prompt: str = ""
    tools_summary: str = ""
    archived: bool = False
    eval_metrics: list[EvalMetric] = field(default_factory=list)
    capability_card: dict[str, Any] = field(default_factory=dict)
    eval_matrix: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Config:
    api_key: str = ""
    base_url: str = "https://api.overmindlab.ai"
    project_id: str = ""
    project_name: str = ""
    repo_summary: str = ""
    trace_provider: str = "overmind"
    version: str = "0.2.1"
    capabilities: dict[str, Capability] = field(default_factory=dict)

    def to_snapshot(self) -> dict:
        """Shape posted to ``POST /api/v1/sync``. Credentials are omitted."""
        capabilities = []
        for cap in self.capabilities.values():
            body: dict[str, Any] = {"slug": cap.slug, "name": cap.name, "eval_metrics": []}
            for key in _CAP_FIELDS:
                value = getattr(cap, key)
                if value not in ("", None) and key not in body:
                    body[key] = value
            for metric in cap.eval_metrics:
                row: dict[str, Any] = {"name": metric.name, "type": metric.type}
                for key in _METRIC_FIELDS:
                    value = getattr(metric, key)
                    if value not in ("", False) and key not in row:
                        row[key] = value
                body["eval_metrics"].append(row)
            if cap.capability_card:
                body["capability_card"] = cap.capability_card
            if cap.eval_matrix:
                body["eval_matrix"] = cap.eval_matrix
            if cap.archived:
                body["archived"] = True
            capabilities.append(body)
        return {
            "project_id": self.project_id,
            "repo_summary": self.repo_summary,
            "trace_provider": self.trace_provider,
            "version": self.version,
            "capabilities": capabilities,
        }

    def apply_snapshot(self, snapshot: dict) -> None:
        """Overlay server state; keep local api_key / base_url."""
        self.project_id = str(snapshot.get("project_id") or self.project_id)
        self.repo_summary = snapshot.get("repo_summary", self.repo_summary) or ""
        self.trace_provider = snapshot.get("trace_provider", self.trace_provider) or "overmind"
        self.version = snapshot.get("version", self.version) or self.version
        caps: dict[str, Capability] = {}
        for raw in snapshot.get("capabilities") or []:
            slug = raw.get("slug") or ""
            if not slug:
                continue
            metrics = [_metric_from_dict(m) for m in raw.get("eval_metrics") or []]
            card = raw.get("capability_card") if isinstance(raw.get("capability_card"), dict) else {}
            matrix = raw.get("eval_matrix") if isinstance(raw.get("eval_matrix"), list) else []
            caps[slug] = Capability(
                slug=slug,
                name=raw.get("name") or slug,
                id=str(raw.get("id") or ""),
                description=raw.get("description") or "",
                entrypoint_fn=raw.get("entrypoint_fn") or "",
                model=raw.get("model") or "",
                source_path=raw.get("source_path") or "",
                system_prompt=raw.get("system_prompt") or "",
                tools_summary=raw.get("tools_summary") or "",
                archived=bool(raw.get("archived")),
                eval_metrics=metrics,
                capability_card=dict(card),
                eval_matrix=list(matrix),
            )
        self.capabilities = caps


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


def _metric_from_dict(raw: dict) -> EvalMetric:
    return EvalMetric(
        name=raw.get("name") or "",
        type=raw.get("type") or "llm_judge_custom",
        prompt=raw.get("prompt") or "",
        measures=raw.get("measures") or "",
        rationale=raw.get("rationale") or "",
        rubric=raw.get("rubric") or "",
        requires_reference=bool(raw.get("requires_reference", False)),
        managed_name=raw.get("managed_name") or "",
    )


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


def _strip_none(value: Any) -> Any:
    """tomli-w rejects None; drop nulls from nested card/matrix trees."""
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value if v is not None]
    return value


def load(path: Path = DEFAULT_PATH) -> Config:
    raw = _load_toml(path)
    project_id = str(raw.get("project-id") or raw.get("project_id") or "")
    base_url = str(raw.get("base-url") or raw.get("base_url") or "https://api.overmindlab.ai")
    caps_raw = raw.get("capabilities") or {}
    caps: dict[str, Capability] = {}
    for slug, body in caps_raw.items():
        if not isinstance(body, dict):
            continue
        evals = body.get("evals") or {}
        metrics_raw = evals.get("metrics") if isinstance(evals, dict) else []
        card = body.get("capability_card") or {}
        if isinstance(card, str):
            try:
                card = json.loads(card)
            except json.JSONDecodeError:
                card = {}
        if not isinstance(card, dict):
            card = {}
        matrix = body.get("eval_matrix") or []
        if isinstance(matrix, str):
            try:
                matrix = json.loads(matrix)
            except json.JSONDecodeError:
                matrix = []
        if not isinstance(matrix, list):
            matrix = []
        caps[slug] = Capability(
            slug=body.get("slug") or slug,
            name=body.get("name") or slug,
            id=str(body.get("id") or ""),
            description=body.get("description") or "",
            entrypoint_fn=body.get("entrypoint_fn") or "",
            model=body.get("model") or "",
            source_path=body.get("source_path") or "",
            system_prompt=body.get("system_prompt") or "",
            tools_summary=body.get("tools_summary") or "",
            archived=bool(body.get("archived")),
            eval_metrics=[_metric_from_dict(m) for m in metrics_raw or []],
            capability_card=dict(card),
            eval_matrix=list(matrix),
        )
    return Config(
        api_key=saved_project_api_key(path) or str(raw.get("api-key") or raw.get("api_key") or ""),
        base_url=base_url,
        project_id=project_id,
        project_name=str(raw.get("project-name") or raw.get("project_name") or ""),
        repo_summary=str(raw.get("repo_summary") or ""),
        trace_provider=str(raw.get("trace-provider") or raw.get("trace_provider") or "overmind"),
        version=str(raw.get("version") or "0.2.1"),
        capabilities=caps,
    )


def _metric_to_dict(metric: EvalMetric) -> dict[str, Any]:
    row: dict[str, Any] = {"name": metric.name, "type": metric.type}
    if metric.prompt:
        row["prompt"] = metric.prompt
    if metric.measures:
        row["measures"] = metric.measures
    if metric.rationale:
        row["rationale"] = metric.rationale
    if metric.rubric:
        row["rubric"] = metric.rubric
    if metric.requires_reference:
        row["requires_reference"] = True
    if metric.managed_name:
        row["managed_name"] = metric.managed_name
    return row


def _config_to_toml_dict(config: Config) -> dict[str, Any]:
    """JSON-shaped tree that ``tomli_w`` turns into nested ``overmind.toml`` tables."""
    capabilities: dict[str, Any] = {}
    for cap in config.capabilities.values():
        body: dict[str, Any] = {"slug": cap.slug, "name": cap.name}
        if cap.id:
            body["id"] = cap.id
        if cap.description:
            body["description"] = cap.description
        if cap.entrypoint_fn:
            body["entrypoint_fn"] = cap.entrypoint_fn
        if cap.model:
            body["model"] = cap.model
        if cap.source_path:
            body["source_path"] = cap.source_path
        if cap.system_prompt:
            body["system_prompt"] = cap.system_prompt
        if cap.tools_summary:
            body["tools_summary"] = cap.tools_summary
        if cap.archived:
            body["archived"] = True
        if cap.capability_card:
            body["capability_card"] = _strip_none(cap.capability_card)
        if cap.eval_matrix:
            body["eval_matrix"] = [_strip_none(row) for row in cap.eval_matrix if isinstance(row, dict)]
        if cap.eval_metrics:
            body["evals"] = {"metrics": [_metric_to_dict(m) for m in cap.eval_metrics]}
        capabilities[cap.slug] = body
    return {
        "base-url": config.base_url,
        "project-id": config.project_id,
        "project-name": config.project_name,
        "repo_summary": config.repo_summary,
        "trace-provider": config.trace_provider,
        "version": config.version,
        "capabilities": capabilities,
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
