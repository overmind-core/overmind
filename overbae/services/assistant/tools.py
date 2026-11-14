"""What the assistant can do. Platform actions are the MCP catalog's own tools, called in
process, so the assistant and a customer's coding agent cannot drift apart. The assistant's
own tools need the kernel, the result panel or the thread itself."""

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model

from overbae.core.errors import InputValidationError
from overbae.models import AssistantEvent, AssistantHandoff, AssistantThread, Cell
from overbae.services import sandbox_sessions
from overbae.services.assistant import handoffs, jobs, presentation, sessions
from overbae.services.datasets import pipeline_packages, rows
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import ASSISTANT, MCPContext, PlatformCredential
from sandbox.sessions import MAX_EXECUTION_SECONDS

# Always offered: the project, credits and the state of any job.
BASE_TOOLS = (
    "get_project_overview",
    "list_objects",
    "inspect_dependencies",
    "get_credit_balance",
    "get_credit_usage",
    "get_job",
    "inspect_jobs",
)
# A thread offers the toolsets it opened: every tool's schema costs tokens on every reply.
TOOLSETS = {
    "data": (
        "list_datasets",
        "inspect_dataset",
        "query_dataset",
        "explore_dataset",
        "derive_dataset",
        "create_dataset_from_traces",
        "create_dataset_from_llm_calls",
        "inspect_dataset_workbench",
        "start_dataset_preparation",
        "update_dataset_preparation",
        "save_dataset_pipeline",
        "validate_dataset_pipeline",
        "run_dataset_pipeline",
        "cancel_dataset_pipeline_run",
        "create_data_partition",
        "retry_data_partition",
    ),
    "training": (
        "get_model_catalog",
        "list_decision_training_models",
        "list_decision_models",
        "check_finetune_readiness",
        "estimate_finetune",
        "prepare_training_data",
        "start_finetune",
        "start_decision_job",
        "cancel_finetune",
        "inspect_training_progress",
        "save_training_plan",
        "create_training_profile",
        "create_training_experiment",
        "prepare_training_experiment",
        "launch_training_experiment",
        "inspect_model_lineage",
        "list_model_workflows",
    ),
    "evaluation": (
        "create_eval_set",
        "create_eval_matrix_row",
        "revise_eval_matrix_row",
        "check_evaluation_readiness",
        "run_evaluation",
        "compare_evaluations",
        "create_native_evaluation",
        "prepare_native_evaluation",
        "launch_native_evaluation",
        "pause_native_evaluation",
        "resume_native_evaluation",
        "reuse_evaluation_predictions",
        "inspect_native_evaluation",
        "measure_decision_performance",
        "resume_decision_performance",
        "list_model_workflows",
    ),
    "inference": (
        "deploy_finetune",
        "retry_deployment",
        "set_active_model",
        "set_benchmark_model",
        "run_inference",
        "list_dedicated_gpus",
        "inspect_model_lineage",
    ),
    # The Optimiser run itself needs the customer's repository, so it is a hand_off.
    "observability": (
        "inspect_capability_health",
        "query_failures",
        "query_traces",
        "inspect_operation",
        "list_signals",
        "check_optimizer_readiness",
        "inspect_optimizer_result",
        "create_dataset_from_traces",
        "create_dataset_from_llm_calls",
    ),
}
PLATFORM_TOOLS = frozenset(BASE_TOOLS).union(*TOOLSETS.values())
# Result keys that hold the receipt of work a mutating tool started.
RECEIPTS = ("job", "workflow", "retry", "activation", "decision_job", "training_job")
# Provider work the user pays for: each call needs the user's approval of that exact call.
PAID = ("llm", "gpu")
APPROVE = "approve"
DEFAULT_EXECUTION_SECONDS = 120
# Every provider takes a cell of this size; a Daytona command line holds little more.
MAX_CODE_CHARS = 64_000
RESULT_CHARS = 24_000

OWN_TOOLS = {
    "stage_dataset": (
        "Copy one dataset version into the Python kernel. It appears read-only at "
        "../inputs/<cell>.parquet; read it with pandas.read_parquet.",
        {
            "type": "object",
            "properties": {"cell": {"type": "string", "format": "uuid"}},
            "required": ["cell"],
            "additionalProperties": False,
        },
    ),
    "run_python": (
        "Run Python in the persistent kernel. Variables and files in the working directory "
        "stay between calls. The value of the last expression is shown. There is no network. "
        "pandas, numpy, pyarrow, duckdb, scipy, scikit-learn and matplotlib are installed.",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "maxLength": MAX_CODE_CHARS},
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_EXECUTION_SECONDS,
                    "description": f"Seconds. Default {DEFAULT_EXECUTION_SECONDS}.",
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    ),
    "write_pipeline_package": (
        "Retain a pipeline package from its files and return the package id for "
        "save_dataset_pipeline. Include manifest.json; its runtime is set to the approved "
        "runtime of this deployment.",
        {
            "type": "object",
            "properties": {
                "files": {
                    "type": "object",
                    "description": "Relative path to file content.",
                    "additionalProperties": {"type": "string"},
                }
            },
            "required": ["files"],
            "additionalProperties": False,
        },
    ),
    "open_toolset": (
        "Add the tools of another stage to this conversation: data, training, evaluation, "
        "inference or observability. The tools are available from your next reply.",
        {
            "type": "object",
            "properties": {"toolset": {"type": "string", "enum": sorted(TOOLSETS)}},
            "required": ["toolset"],
            "additionalProperties": False,
        },
    ),
    "hand_off": (
        "Give work to the user's coding agent when it needs their repository or machine: "
        "instrumentation, the Optimiser loop, a model swap in their code, local files. Write "
        "complete instructions: the goal, the exact Overmind ids, the commands or MCP tools "
        "to use, and what to report. The user approves the instructions first; then the "
        "agent reads them over MCP, and the platform waits for its report and gives it to you.",
        {
            "type": "object",
            "properties": {
                "instructions": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": handoffs.MAX_CHARS,
                }
            },
            "required": ["instructions"],
            "additionalProperties": False,
        },
    ),
}


@dataclass
class Outcome:
    ok: bool
    content: object
    # Jobs the call started that the platform still works on, as [{"kind", "id"}].
    jobs: list[dict] | None = None
    # The approval this refused paid call asks for: the identity of the exact call.
    asks: str | None = None
    # The refused call whose approval this paid call used, so one approval starts one call.
    approval: str | None = None
    # The stored document of a ``present`` call.
    presentation: dict | None = None


def _supplied(definition) -> tuple[str, ...]:
    """Arguments the loop gives: the model never chooses a project or a request identity.
    On a read-only tool ``request_key`` is a filter, and the model's own."""
    return ("project_id",) if definition.read_only else ("project_id", "request_key")


def specifications(thread: AssistantThread) -> list[dict]:
    return _specifications(tuple(thread.toolsets))


@cache
def _specifications(toolsets: tuple[str, ...]) -> list[dict]:
    specs = []
    offered = dict.fromkeys(BASE_TOOLS + sum((TOOLSETS[name] for name in toolsets), ()))
    for name in offered:
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


def _function(name, description, parameters):
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def call(thread: AssistantThread, user, call_id: str, name: str, arguments: dict) -> Outcome:
    try:
        if name in PLATFORM_TOOLS:
            return _platform(thread, user, call_id, name, arguments)
        if name in _OWN:
            return _OWN[name](thread, user, call_id, arguments)
    except (InputValidationError, DatasetError, rows.RowStoreError) as exc:
        return Outcome(False, {"error": getattr(exc, "detail", str(exc))})
    except sandbox_sessions.SessionUnavailableError as exc:
        return Outcome(False, {"error": f"The sandbox is not available now: {exc}"})
    return Outcome(False, {"error": f"There is no tool named {name}."})


def finished(thread: AssistantThread, waiting: dict) -> dict:
    """What each job a tool call waited for is now, as the model reads it."""
    user = get_user_model().objects.filter(pk=waiting["user"]).first()
    states = []
    for job in waiting["jobs"]:
        if job["kind"] == "handoff":
            handoff = AssistantHandoff.objects.get(pk=job["id"])
            states.append({"kind": "handoff", "state": handoff.state, "result": handoff.result})
            continue
        try:
            states.append(_invoke(thread, user, "get_job", job).structuredContent)
        except InputValidationError as exc:
            states.append({**job, "error": str(exc)})
    return {"started": waiting["started"], "now": states}


def _invoke(thread, user, name, arguments):
    if not thread.project.memberships.filter(user=user).exists():
        raise InputValidationError("The user this call acts for left the project.")
    context = MCPContext(
        user=user,
        token=PlatformCredential(thread.project_id),
        project=thread.project,
        client_name=ASSISTANT,
    )
    return async_to_sync(CATALOG.invoke)(name, arguments, context)


def _digest(name: str, arguments: dict) -> str:
    """The identity of one paid call: its tool and its exact arguments."""
    canonical = json.dumps([name, arguments], sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _approval(thread, digest: str) -> tuple[str, int] | None:
    """An approval of this exact call that no call used yet: the refused call the user
    approved, and that user."""
    results = thread.events.filter(kind=AssistantEvent.Kind.TOOL_RESULT)
    asked = list(results.filter(payload__asks=digest).values_list("payload__call", flat=True))
    used = set(
        results.filter(payload__approval__in=asked).values_list("payload__approval", flat=True)
    )
    given = thread.events.filter(
        kind=AssistantEvent.Kind.USER,
        payload__interaction__call__in=asked,
        payload__interaction__id=APPROVE,
        payload__interaction__values__choice=APPROVE,
    ).values_list("payload__interaction__call", "payload__user")
    return next(((call, user) for call, user in given if call not in used), None)


def _ask(digest: str, shown: dict, prompt: str, decline: str, error: str) -> Outcome:
    """Refuse a call until the user approves exactly it: ``shown`` is what they approve."""
    return Outcome(
        False,
        {"error": error},
        asks=digest,
        presentation={
            "root": {
                "type": "Stack",
                "direction": "column",
                "children": [
                    shown,
                    {
                        "type": "Choices",
                        "id": APPROVE,
                        "prompt": prompt,
                        "options": [
                            {"label": "Approve", "value": APPROVE},
                            {"label": decline, "value": "decline"},
                        ],
                    },
                ],
            },
            "data": {},
            "images": {},
            "cells": {},
        },
    )


def _receipts(name: str, content: dict) -> list[dict]:
    if name == "get_job":
        found = [content]
    else:
        found = [content[key] for key in RECEIPTS if isinstance(content.get(key), dict)]
    if name == "run_evaluation":
        found += [{"kind": "eval_run", "id": run["id"]} for run in content.get("runs") or []]
    return [
        {"kind": receipt["kind"], "id": str(receipt["id"])}
        for receipt in found
        if receipt.get("kind") and receipt.get("id")
    ]


def _platform(thread, user, call_id, name, arguments) -> Outcome:
    definition = CATALOG.get(name).definition
    arguments = {key: value for key, value in arguments.items() if key not in _supplied(definition)}
    approval = None
    if definition.cost_class in PAID and not definition.read_only:
        digest = _digest(name, arguments)
        granted = _approval(thread, digest)
        if granted is None:
            shown = json.dumps(arguments, indent=1, default=str, ensure_ascii=False)
            return _ask(
                digest,
                {"type": "Code", "code": shown, "language": "json"},
                f"Start paid work: {name}?",
                "Do not start",
                "This call starts paid work and the user has not approved it. Nothing ran. "
                "The user now sees an approval request for this exact call. Say in one "
                "sentence what it costs, from an estimate or readiness tool result, and end "
                "your turn. Call the tool again with the same arguments after the user "
                "approves.",
            )
        # The approver pays, whoever wrote the newest message.
        approval, approver = granted
        user = get_user_model().objects.get(pk=approver)
    if not definition.read_only and (
        definition.receipt_supported or "request_key" in definition.input_model.model_fields
    ):
        # One approval is one request to the platform: a repeat before its result is
        # recorded returns the first receipt and starts nothing new.
        identity = f"{thread.pk}:{approval or call_id}"
        arguments["request_key"] = "assistant-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
    result = _invoke(thread, user, name, arguments)
    content = result.structuredContent or {}
    if result.isError:
        # A call that failed spent its approval: the platform keeps that failure under the
        # approval's request key, so the next attempt needs a new one.
        return Outcome(False, content, approval=approval)
    # A wait is for work this call started, or for the job the model asks get_job about.
    started = _receipts(name, content) if name == "get_job" or not definition.read_only else []
    moving = jobs.moving(started)
    waiting = [job for job in started if (job["kind"], job["id"]) in moving]
    return Outcome(True, content, jobs=waiting or None, approval=approval)


def _stage_dataset(thread, _user, _call_id, arguments) -> Outcome:
    cell = (
        Cell.objects.filter(dataset__project=thread.project, pk=arguments.get("cell"))
        .select_related("dataset")
        .first()
    )
    if cell is None:
        raise InputValidationError("That dataset version is not in this project.")
    sessions.stage(thread, sandbox_sessions.provider(), cell)
    return Outcome(
        True,
        {
            "path": f"../inputs/{cell.pk}.parquet",
            "dataset": cell.dataset.name,
            "version": cell.title,
            "rows": cell.rows,
            "columns": cell.columns,
        },
    )


def _run_python(thread, _user, call_id, arguments) -> Outcome:
    code, timeout = arguments.get("code"), arguments.get("timeout", DEFAULT_EXECUTION_SECONDS)
    if not isinstance(code, str) or type(timeout) is not int:
        raise InputValidationError("code is text and timeout is a whole number of seconds.")
    if len(code) > MAX_CODE_CHARS:
        raise InputValidationError(f"code is at most {MAX_CODE_CHARS:,} characters.")
    timeout = max(1, min(timeout, MAX_EXECUTION_SECONDS))
    provider = sandbox_sessions.provider()
    while True:
        session = sessions.use(thread, provider)
        if thread.kernel_lost:
            break
        try:
            execution = provider.execute(session, code, execution_id=call_id, timeout=timeout)
        except sandbox_sessions.SessionGoneError as exc:
            sessions.forget(thread, str(exc))
            continue
        return Outcome(
            execution.ok,
            {
                "output": execution.output,
                "error": execution.error,
                "truncated": execution.truncated,
                "seconds": execution.seconds,
            },
        )
    # The model wrote this cell for a kernel that no longer exists: it does not run.
    lost = thread.kernel_lost
    thread.kernel_lost = ""
    AssistantThread.objects.filter(pk=thread.pk).update(kernel_lost="")
    return Outcome(
        False,
        {
            "error": f"{lost} A new kernel started: variables and working files are gone, "
            "staged datasets are back. Run the setup again."
        },
    )


def _write_pipeline_package(thread, user, _call_id, arguments) -> Outcome:
    files = arguments.get("files")
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
    package = pipeline_packages.save(thread.project, user, bundle)
    return Outcome(True, {"package": str(package.pk), "sha256": package.sha256})


def image_path(thread: AssistantThread, name: str) -> Path:
    """Where a presented image is retained, under its content name."""
    return Path(settings.MEDIA_ROOT) / "assistant" / str(thread.pk) / name


def _present(thread, _user, _call_id, arguments) -> Outcome:
    provider = sandbox_sessions.provider()

    def read_file(name):
        if not thread.session:
            raise FileNotFoundError(name)
        try:
            return provider.get_file(thread.session, name)
        except sandbox_sessions.SessionGoneError as exc:
            sessions.forget(thread, str(exc))
            raise FileNotFoundError(name) from None

    def save_image(name, data):
        path = image_path(thread, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    return Outcome(
        True,
        {"shown": True},
        presentation=presentation.build(thread.project, arguments, read_file, save_image),
    )


def _open_toolset(thread, _user, _call_id, arguments) -> Outcome:
    name = arguments.get("toolset")
    if name not in TOOLSETS:
        raise InputValidationError(f"toolset is one of: {', '.join(sorted(TOOLSETS))}.")
    if name not in thread.toolsets:
        thread.toolsets = [*thread.toolsets, name]
        AssistantThread.objects.filter(pk=thread.pk).update(toolsets=thread.toolsets)
    return Outcome(True, {"tools": list(TOOLSETS[name])})


def _hand_off(thread, _user, call_id, arguments) -> Outcome:
    instructions = handoffs.checked(arguments.get("instructions"))
    # The model wrote these after it read customer data, and an agent with the user's
    # repository acts on them: the user reads them first.
    digest = _digest("hand_off", {"instructions": instructions})
    granted = _approval(thread, digest)
    if granted is None:
        return _ask(
            digest,
            {"type": "Text", "text": instructions},
            "Give this work to your coding agent?",
            "Do not send",
            "The user has not approved this handoff. Nothing was sent. The user now sees "
            "the instructions and an approval request. End your turn. Call the tool again "
            "with the same instructions after the user approves.",
        )
    handoff = handoffs.open_for(thread, call_id, instructions)
    return Outcome(
        True,
        {"handoff": str(handoff.pk), "state": handoff.state},
        jobs=[{"kind": "handoff", "id": str(handoff.pk)}],
        approval=granted[0],
    )


_OWN = {
    "stage_dataset": _stage_dataset,
    "run_python": _run_python,
    "write_pipeline_package": _write_pipeline_package,
    "present": _present,
    "open_toolset": _open_toolset,
    "hand_off": _hand_off,
}


def _compact(value):
    """MCP results carry every optional field; the empty ones only cost the model tokens."""
    if isinstance(value, dict):
        kept = {key: _compact(item) for key, item in value.items()}
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
