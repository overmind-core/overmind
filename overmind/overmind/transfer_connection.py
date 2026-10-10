import errno
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import requests

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from overmind.config import Config
from overmind.sync import resolve_api_key, resolve_api_url


class TransferError(Exception):
    def __init__(self, message, *, code="transfer_failed", stage="local", next_action="inspect_transfer"):
        super().__init__(message)
        self.code, self.stage, self.next_action = code, stage, next_action
        self.receipt = None

    def record(self):
        return {
            "code": self.code,
            "message": str(self),
            "stage": self.stage,
            "next_action": self.next_action,
            "transfer": self.receipt,
        }


def request_json(client, method, url, *, stage, **kwargs):
    try:
        response = client.request(method, url, timeout=60, allow_redirects=False, **kwargs)
    except requests.RequestException as exc:
        pending, visited, denied = [exc], set(), False
        while pending:
            error = pending.pop()
            if id(error) in visited:
                continue
            visited.add(id(error))
            denied |= isinstance(error, PermissionError) or getattr(error, "errno", None) in {errno.EACCES, errno.EPERM}
            pending.extend(
                item
                for item in (error.__cause__, error.__context__, getattr(error, "reason", None), *error.args)
                if isinstance(item, BaseException)
            )
        raise TransferError(
            "The execution environment denied network access."
            if denied
            else "The API connection failed; sandbox denial and server unavailability are not distinguished by this error.",
            code="network_permission_denied" if denied else "connection_unavailable",
            stage=stage,
            next_action="request_host_network_permission" if denied else "check_host_network_permission_and_api",
        ) from None
    if not 200 <= response.status_code < 300:
        known_errors = {
            "pipeline_parameters": (
                'Manifest parameters declare types, for example {"seed": "integer"}. '
                'Supply values such as {"seed": 42} in run_dataset_pipeline(parameters=...).',
                "correct_manifest_parameter_types",
            ),
            "pipeline_package": (
                "The pipeline package was rejected. Check manifest.json, Python syntax, "
                "the approved runtime, relative file paths and package limits in overmind://dataset-upload.",
                "correct_pipeline_package",
            ),
            "file_too_large": (
                "The file exceeds the server's byte limit for this file type. "
                "Read overmind://dataset-upload for the limits; split or reduce the file. "
                "No transfer was reserved.",
                "split_or_reduce_file",
            ),
            "request_key_conflict": (
                "This request key binds different file bytes or destination settings.",
                "use_original_inputs_or_new_request_key",
            ),
            "file_hash_mismatch": (
                "The uploaded bytes do not match the declared file hash.",
                "inspect_file_and_start_new_transfer",
            ),
            "transfer_bytes_missing": (
                "The staged upload bytes are unavailable.",
                "start_new_transfer_with_new_request_key",
            ),
            "transfer_incomplete": ("Not all file bytes have been received.", "resume_same_command"),
            "chunk_conflict": ("The retried chunk differs from stored bytes.", "inspect_file_and_start_new_transfer"),
            "transfer_published": ("This transfer has already published its dataset.", "inspect_transfer"),
            "invalid_chunk": ("The chunk is outside the declared file bounds.", "inspect_transfer"),
            "target_unavailable": ("The destination is no longer accessible.", "inspect_transfer"),
        }
        try:
            error_code = response.json().get("code")
        except (ValueError, AttributeError):
            error_code = None
        if isinstance(error_code, list) and len(error_code) == 1:
            error_code = error_code[0]
        if isinstance(error_code, str) and error_code in known_errors:
            message, action = known_errors[error_code]
            raise TransferError(message, code=error_code, stage=stage, next_action=action)
        code = {
            401: "authentication_failed",
            403: "permission_denied",
            404: "resource_not_found",
            409: "transfer_conflict",
        }.get(response.status_code, "http_error")
        if 300 <= response.status_code < 400:
            code = "redirect_refused"
        # Neither provider error bodies nor exception strings are safe diagnostics.
        raise TransferError(
            f"The {stage} request returned HTTP {response.status_code}.", code=code, stage=stage
        ) from None
    if response.status_code == 202:
        return {}
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except ValueError:
        raise TransferError("The API returned an invalid response.", code="invalid_response", stage=stage) from None


def check_connection(*, api_key, api_url, project_id, session=None):
    client = session or requests.Session()
    client.headers.update({"X-Api-Key": api_key})
    base = api_url.rstrip("/")
    mcp_url = base + "/api/mcp/"
    report = {
        "ready": False,
        "api_url": base,
        "project_id": project_id,
        "mcp": {"status": "not_checked"},
        "transfer": {"status": "not_checked"},
        "scope": "this command's execution environment; not other clients or future sessions",
    }

    def rpc(method, params, ident=1):
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if ident is not None:
            body["id"] = ident
        reply = request_json(
            client,
            "POST",
            mcp_url,
            stage="mcp",
            json=body,
            headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"},
        )
        if reply.get("error") or reply.get("result", {}).get("isError"):
            raise TransferError("MCP rejected the connection check.", code="mcp_check_failed", stage="mcp")
        return reply.get("result", {})

    try:
        initialized = rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "overmind-transfer", "version": "1"},
            },
        )
        if initialized.get("protocolVersion") != "2025-06-18":
            raise TransferError("The MCP protocol is incompatible.", code="protocol_mismatch", stage="mcp")
        rpc("notifications/initialized", {}, ident=None)
        resource = rpc("resources/read", {"uri": "overmind://interface/current"})
        interface = json.loads(resource["contents"][0]["text"])
        if interface.get("transfer_protocol_version") != 1:
            raise TransferError(
                "This API does not support durable file transfers.", code="protocol_mismatch", stage="mcp"
            )
        if interface.get("connection", {}).get("mcp_url") != mcp_url:
            raise TransferError("MCP reports a different endpoint.", code="endpoint_mismatch", stage="mcp")
        offset, found = 0, False
        while True:
            result = rpc("tools/call", {"name": "list_projects", "arguments": {"offset": offset, "limit": 100}})
            projects = result.get("structuredContent")
            if projects is None:
                projects = json.loads(result["content"][0]["text"])
            found |= any(item["id"] == project_id for item in projects["projects"])
            next_offset = projects.get("next_offset")
            if found or next_offset is None:
                break
            if next_offset <= offset:
                raise ValueError
            offset = next_offset
        if not found:
            raise TransferError(
                "The selected project is not accessible through MCP.", code="project_not_accessible", stage="mcp"
            )
        report["mcp"] = {
            "status": "ready",
            "contract_version": interface["contract_version"],
            "catalog_sha256": interface.get("catalog_sha256"),
        }
        readiness = request_json(
            client, "GET", base + "/api/dataset-transfers/readiness/", stage="transfer", params={"project": project_id}
        )
        if (
            readiness.get("protocol_version") != 1
            or readiness.get("project_id") != project_id
            or readiness.get("mcp_url") != mcp_url
        ):
            raise TransferError(
                "The transfer connection does not match MCP.", code="connection_mismatch", stage="transfer"
            )
        if not readiness.get("can_upload") or not readiness.get("can_export"):
            raise TransferError(
                "This connection requires read and write permissions for dataset transfer.",
                code="permission_denied",
                stage="transfer",
            )
        report["transfer"] = {"status": "ready", **readiness}
        report["ready"] = True
    except TransferError as exc:
        report["error"] = exc.record()
        report[exc.stage if exc.stage in {"mcp", "transfer"} else "transfer"]["status"] = "failed"
    except (ValueError, KeyError, TypeError, IndexError):
        report["error"] = TransferError(
            "The connection check returned an invalid contract.", code="invalid_response"
        ).record()
    finally:
        if session is None:
            client.close()
    return report


def resolve_transfer_connection(api_key: str, api_url: str, config: Config | None) -> tuple[str, str]:
    key = resolve_api_key(api_key, config)
    if key:
        base = resolve_api_url(api_url, config)
        bound_key = config.api_key if config else ""
        if bound_key.startswith("env:"):
            bound_key = os.environ.get(bound_key[4:], "")
        if bound_key and key == bound_key and base != config.base_url.rstrip("/"):
            raise ValueError(
                "Repository credentials belong to a different API; select a matching connection "
                "instead of overriding only its address. No request was sent."
            )
        return key, base

    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = root / "overmind" / "connection.toml"
    if not path.exists():
        raise ValueError(
            "Missing API key. Configure the local transfer connection, pass --api-key or set OVERMIND_API_KEY."
        )
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("Local transfer connection must have file permissions 0600.")
    try:
        with path.open("rb") as stream:
            saved = tomllib.load(stream)
        key = saved.get("api-key")
        base = saved.get("base-url")
        if not isinstance(key, str) or not key.strip() or not isinstance(base, str):
            raise ValueError
        parsed = urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        base = base.rstrip("/")
    except (OSError, ValueError):
        # TOML errors can include the secret-bearing source line.
        raise ValueError("Invalid local transfer connection; check its api-key and base-url.") from None

    requested = api_url or os.environ.get("OVERMIND_API_URL") or (config.base_url if config else "")
    if requested and requested.rstrip("/") != base:
        raise ValueError("Saved transfer credentials belong to a different API; provide an explicit connection.")
    return key, base
