"""What the assistant can do. Platform actions are the MCP catalog's own tools, called in
process, so the assistant and a customer's coding agent cannot drift apart. Its own tools
run Python in the project's workspace, run a transformation, and show results."""

import hashlib
import io
import json
import re
import time
import zipfile
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db.models import Q

from overbae.core.errors import InputValidationError
from overbae.models import AssistantThread, Cell, DatasetPipeline
from overbae.services import workspaces
from overbae.services.assistant import presentation
from overbae.services.assistant.threads import data_name
from overbae.services.datasets import pipeline_packages, rows
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import ASSISTANT, MCPContext, PlatformCredential

CATALOG_TOOLS = (
    "list_datasets",
    "inspect_dataset",
    "query_dataset",
    "inspect_dataset_workbench",
    "create_data_partition",
    "get_job",
)
# What the user reads while a call runs and in the list of steps afterwards.
SUMMARY = {
    "type": "string",
    "maxLength": 80,
    "description": "A few words that tell the user what this step does, such as "
    "'Checking label balance'. Sentence case. No ids, no tool names.",
}
_WRITTEN_SUMMARY = re.compile(r'"summary"\s*:\s*("(?:[^"\\]|\\.)*")')
MAX_CODE_CHARS = 64_000
RESULT_CHARS = 24_000
# A job a call started is waited for within the turn, to this limit.
JOB_SECONDS = 25 * 60
POLL_SECONDS = 2

OWN_TOOLS = {
    "run_python": (
        "Run Python in this conversation's kernel. Variables and files in the working "
        "directory stay between calls. The value of the last line is shown. There is no "
        "network. pandas, numpy, pyarrow, duckdb, scipy, scikit-learn and matplotlib are "
        "installed. Name every dataset version the cell reads in `versions`: each one is "
        "at data/<cell id>.parquet. For more than a few hundred thousand rows, prefer duckdb.",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "maxLength": MAX_CODE_CHARS},
                "versions": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "maxItems": 10,
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": workspaces.MAX_SECONDS,
                    "description": f"Seconds. Default {workspaces.DEFAULT_SECONDS}.",
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    ),
    "run_pipeline": (
        "Retain a transformation and run it on one dataset version; the result comes back "
        "when the run ends. `files` is the package: manifest.json and its entrypoints, as "
        "the package contract says. Mode publish runs every row and adds the result to the "
        "dataset as new versions. Mode preview runs the first rows and changes nothing. To "
        "correct a pipeline, pass its id as `pipeline` with the new files; that saves its "
        "next revision. To run a saved pipeline again, pass `pipeline` and no files. The "
        "platform sets the manifest's `runtime`: leave it out.",
        {
            "type": "object",
            "properties": {
                "source_cell": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The dataset version the pipeline reads.",
                },
                "mode": {"type": "string", "enum": ["publish", "preview"]},
                "name": {"type": "string", "description": "Required with files."},
                "files": {
                    "type": "object",
                    "description": "Relative path to file content.",
                    "additionalProperties": {"type": "string"},
                },
                "pipeline": {"type": "string", "format": "uuid"},
                "parameters": {"type": "object"},
            },
            "required": ["source_cell", "mode"],
            "additionalProperties": False,
        },
    ),
}


@dataclass(frozen=True)
class Outcome:
    ok: bool
    content: object
    # The result document a ``present`` call shows.
    presentation: dict | None = None
    # A question to the user: the turn ends with it.
    asks: bool = False


def _supplied(definition) -> tuple[str, ...]:
    """Arguments the turn gives: the model never chooses a project or a request identity.
    On a read-only tool ``request_key`` is a filter, and the model's own."""
    return ("project_id",) if definition.read_only else ("project_id", "request_key")


@cache
def specifications() -> list[dict]:
    specs = []
    for name in CATALOG_TOOLS:
        definition = CATALOG.get(name).definition
        schema = deepcopy(definition.as_mcp_tool().inputSchema)
        supplied = _supplied(definition)
        for argument in supplied:
            schema["properties"].pop(argument, None)
        schema["required"] = [key for key in schema.get("required", []) if key not in supplied]
        specs.append(_function(name, definition.description, schema))
    specs += [_function(name, text, schema) for name, (text, schema) in OWN_TOOLS.items()]
    specs.append(
        _function(
            "present",
            presentation.Presentation.__doc__,
            presentation.Presentation.model_json_schema(),
        )
    )
    return specs


def summary(raw: str) -> str:
    """The summary of a call from its arguments, also while they are still being written."""
    written = _WRITTEN_SUMMARY.search(raw)
    try:
        return json.loads(written.group(1)) if written else ""
    except ValueError:
        return ""


def _function(name, description, parameters):
    # First, so the model writes it before the long arguments and the user reads it at once.
    parameters = {
        **parameters,
        "properties": {"summary": SUMMARY, **parameters.get("properties", {})},
        "required": ["summary", *parameters.get("required", [])],
    }
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


class Turn:
    """What a tool may ask of the turn that runs it: to stay alive while it waits, and
    whether the user stopped it."""

    def __init__(self, thread: AssistantThread, user, call_id: str, alive: Callable[[], bool]):
        self.thread, self.user, self.call_id, self.alive = thread, user, call_id, alive

    def key(self, part: str) -> str:
        # One call is one request to the platform: a repeat returns its first receipt.
        identity = f"{self.thread.pk}:{self.call_id}:{part}"
        return "assistant-" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def call(turn: Turn, name: str, arguments: dict) -> Outcome:
    # The summary is for the user: no tool reads it.
    arguments = {key: value for key, value in arguments.items() if key != "summary"}
    try:
        if name in CATALOG_TOOLS:
            return _platform(turn, name, arguments)
        if name in _OWN:
            return _OWN[name](turn, arguments)
    except (InputValidationError, DatasetError, rows.RowStoreError) as exc:
        return Outcome(False, {"error": getattr(exc, "detail", str(exc))})
    except workspaces.WorkspaceUnavailableError as exc:
        return Outcome(False, {"error": f"The workspace is not available now: {exc}"})
    return Outcome(False, {"error": f"There is no tool named {name}."})


def _invoke(turn: Turn, name, arguments):
    thread = turn.thread
    if not thread.project.memberships.filter(user=turn.user).exists():
        raise InputValidationError("The user this call acts for left the project.")
    context = MCPContext(
        user=turn.user,
        token=PlatformCredential(thread.project_id),
        project=thread.project,
        client_name=ASSISTANT,
    )
    return async_to_sync(CATALOG.invoke)(name, arguments, context)


def _wait(turn: Turn, job: dict) -> dict:
    """The job's receipt once the platform no longer moves it, or as it is when the turn
    must stop waiting."""
    deadline = time.monotonic() + JOB_SECONDS
    while True:
        state = _invoke(turn, "get_job", {"kind": job["kind"], "id": job["id"]})
        content = state.structuredContent or {}
        if state.isError or content.get("poll_after_seconds") is None:
            return content
        if time.monotonic() > deadline:
            return {**content, "note": "Still running: inspect it later with get_job."}
        if not turn.alive():
            return {**content, "note": "The user stopped the conversation while it ran."}
        time.sleep(POLL_SECONDS)


def _platform(turn: Turn, name: str, arguments: dict) -> Outcome:
    definition = CATALOG.get(name).definition
    arguments = {key: value for key, value in arguments.items() if key not in _supplied(definition)}
    if not definition.read_only:
        arguments["request_key"] = turn.key(name)
    result = _invoke(turn, name, arguments)
    content = result.structuredContent or {}
    if result.isError:
        return Outcome(False, content)
    job = content.get("job")
    if not definition.read_only and isinstance(job, dict) and job.get("kind"):
        content = {**content, "job": _wait(turn, job)}
    return Outcome(True, content)


def _put_versions(turn: Turn, provider, versions) -> None:
    if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
        raise InputValidationError("versions lists dataset version ids.")
    cells = Cell.objects.filter(dataset__project=turn.thread.project_id, pk__in=versions)
    if cells.count() != len(set(versions)):
        raise InputValidationError("A listed dataset version is not in this project.")
    for cell in cells:
        # A deleted project takes its thread along; a turn that lost it copies nothing
        # into a workspace its removal already cleared.
        turn.alive()
        with open(rows.frame_path(cell), "rb") as frame:
            provider.put_data(str(turn.thread.project_id), data_name(cell), frame)


def _run_python(turn: Turn, arguments) -> Outcome:
    code = arguments.get("code")
    timeout = arguments.get("timeout", workspaces.DEFAULT_SECONDS)
    if not isinstance(code, str) or type(timeout) is not int:
        raise InputValidationError("code is text and timeout is a whole number of seconds.")
    if len(code) > MAX_CODE_CHARS:
        raise InputValidationError(f"code is at most {MAX_CODE_CHARS:,} characters.")
    provider = workspaces.provider()
    _put_versions(turn, provider, arguments.get("versions") or [])
    turn.alive()
    run = provider.run(
        str(turn.thread.project_id),
        turn.thread.pk.hex,
        code,
        max(1, min(timeout, workspaces.MAX_SECONDS)),
    )
    content = {"output": run.output, "error": run.error, "truncated": run.truncated}
    if run.restarted:
        content["note"] = run.restarted
    return Outcome(run.ok, content)


def _package(turn: Turn, files):
    if not isinstance(files, dict) or not all(isinstance(text, str) for text in files.values()):
        raise InputValidationError("files maps each relative path to its text.")
    try:
        manifest = json.loads(files.get("manifest.json", ""))
    except ValueError:
        raise InputValidationError("manifest.json is required and must be JSON.") from None
    if isinstance(manifest, dict):
        manifest["runtime"] = pipeline_packages.runtime_id()
        files = {**files, "manifest.json": json.dumps(manifest, indent=2)}
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in sorted(files.items()):
            archive.writestr(path, text)
    bundle.seek(0)
    return pipeline_packages.save(turn.thread.project, turn.user, bundle)


def _run_pipeline(turn: Turn, arguments) -> Outcome:
    """Save, check and run in one call: the model writes the code, the platform's own
    tools retain and run it."""
    project = turn.thread.project
    source = Cell.objects.filter(dataset__project=project, pk=arguments.get("source_cell")).first()
    if source is None:
        raise InputValidationError("source_cell is a dataset version of this project.")
    files, named = arguments.get("files"), arguments.get("pipeline")
    # A pipeline has an id for each revision and one for all of them: either names it.
    known = (
        DatasetPipeline.objects.filter(project=project)
        .filter(Q(family=named) | Q(pk=named))
        .first()
        if named
        else None
    )
    if named and known is None:
        raise InputValidationError("pipeline is a pipeline of this project.")
    latest = (
        DatasetPipeline.objects.filter(project=project, family=known.family)
        .order_by("-revision")
        .first()
        if known
        else None
    )
    if files is None and latest is None:
        raise InputValidationError("Give files for a new pipeline, or pipeline to run a saved one.")
    dataset = str(source.dataset_id)
    revision = str(latest.pk) if latest else None
    if files is not None:
        save = {
            "dataset": dataset,
            "name": arguments.get("name") or (latest.name if latest else ""),
            "package": str(_package(turn, files).pk),
            "request_key": turn.key("save"),
        }
        if latest:
            save |= {"pipeline": str(latest.family), "expected_revision": latest.revision}
        saved = _invoke(turn, "save_dataset_pipeline", save)
        if saved.isError:
            return Outcome(False, saved.structuredContent)
        revision = saved.structuredContent["pipeline"]["id"]
    pinned = {"source_cell": str(source.pk), "source_fingerprint": source.fingerprint}
    checked = _invoke(turn, "validate_dataset_pipeline", {"pipeline": revision, **pinned})
    if checked.isError:
        return Outcome(False, checked.structuredContent)
    validation = checked.structuredContent["validation"]
    record = validation.pop("pipeline")
    content = {
        "pipeline": {name: record[name] for name in ("id", "pipeline_id", "revision", "name")},
        "validation": validation,
    }
    if not validation["valid"]:
        missing = ", ".join(validation["missing_columns"])
        return Outcome(False, {**content, "error": f"The source has no column {missing}."})
    run = {
        "dataset": dataset,
        "pipeline": revision,
        "mode": arguments.get("mode"),
        "parameters": arguments.get("parameters") or {},
        "request_key": turn.key("run"),
        **pinned,
    }
    ran = _invoke(turn, "run_dataset_pipeline", run)
    if ran.isError:
        return Outcome(False, {**content, **ran.structuredContent})
    receipt = ran.structuredContent["job"]
    finished = _wait(turn, receipt)
    if finished.get("status") == "completed":
        return Outcome(True, {**content, "run": finished})
    # The reader sees a failed step by its error, wherever the run recorded it.
    error = (finished.get("details") or {}).get("error") or f"The run is {finished.get('status')}."
    return Outcome(False, {**content, "run": finished, "error": error})


def image_path(thread: AssistantThread, name: str) -> Path:
    """Where a presented image is retained, under its content name."""
    return Path(settings.MEDIA_ROOT) / "assistant" / str(thread.pk) / name


def _present(turn: Turn, arguments) -> Outcome:
    thread, provider = turn.thread, workspaces.provider()

    def read_file(name):
        return provider.read_file(str(thread.project_id), thread.pk.hex, name)

    def save_image(name, data):
        path = image_path(thread, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    document = presentation.build(thread.project, arguments, read_file, save_image)
    asks = presentation.asks(document)
    return Outcome(True, {"shown": True}, presentation=document, asks=asks)


_OWN = {"run_python": _run_python, "run_pipeline": _run_pipeline, "present": _present}


def _compact(value):
    """MCP results carry every optional field; the empty ones only cost the model tokens.
    Rows are data: a null or empty value in one is a measured value and stays."""
    if isinstance(value, dict):
        kept = {key: item if key == "rows" else _compact(item) for key, item in value.items()}
        return {key: item for key, item in kept.items() if item not in (None, [], {})}
    if isinstance(value, list):
        return [_compact(item) for item in value]
    return value


def bounded(content) -> str:
    """A tool result as the model reads it."""
    text = json.dumps(_compact(content), default=str, ensure_ascii=False)
    if len(text) <= RESULT_CHARS:
        return text
    return json.dumps({"truncated": True, "characters": len(text), "start": text[:RESULT_CHARS]})
