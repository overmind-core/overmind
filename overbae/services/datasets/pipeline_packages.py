import ast
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

from overbae.models import DatasetPipelinePackage, Project
from overbae.services.datasets import pipeline_graph
from overbae.services.datasets.lifecycle import DatasetError

MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 100
TYPES = {"string", "integer", "number", "boolean", "object", "array", "null", "any"}


def error(detail):
    return DatasetError(detail, code="pipeline_package")


def schema(value):
    if not isinstance(value, dict) or len(value) > 200:
        raise error("A column contract must contain at most 200 named types.")
    for name, kind in value.items():
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 255
            or not isinstance(kind, str)
            or kind not in TYPES
        ):
            raise DatasetError(
                "Column contracts use string, integer, number, boolean, object, array, null or any. Use object for a decision object; json is not a column type.",
                code="pipeline_column_type",
            )
    return value


def check_rows(records, contract):
    for index, row in enumerate(records):
        for name, kind in contract.items():
            if name not in row:
                raise ValueError(f"Row {index} lacks required column {name}.")
            value = row[name]
            valid = {
                "any": True,
                "null": value is None,
                "string": isinstance(value, str),
                "integer": type(value) is int,
                "number": type(value) in (int, float),
                "boolean": type(value) is bool,
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
            }[kind]
            if not valid:
                raise ValueError(f"Row {index} column {name} does not match {kind}.")
        yield row


def validate_manifest(manifest, files, *, require_runtime=True):
    if not isinstance(manifest, dict) or set(manifest) - {
        "version",
        "runtime",
        "steps",
        "parameters",
        "limits",
    }:
        raise error("Use a versioned manifest with runtime, steps, parameters and limits.")
    if type(manifest.get("version")) is not int or manifest["version"] != 1:
        raise error("The package manifest version must be 1.")
    runtime = manifest.get("runtime", "")
    if not isinstance(runtime, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", runtime):
        raise error("Pin the runtime to an immutable Docker image ID (sha256).")
    if require_runtime and runtime not in settings.WORKSHOP_RUNTIME_IMAGES:
        raise error(
            "The runtime is not approved on this deployment. Inspect Workshop runtime availability."
        )
    steps = manifest.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise error("Provide 1–20 ordered script steps.")
    for step in steps:
        if not isinstance(step, dict) or set(step) - {
            "id",
            "input",
            "inputs",
            "condition",
            "name",
            "entrypoint",
            "input_schema",
            "output_schema",
            "checks",
            "batch_rows",
            "consumer",
        }:
            raise error(
                "Each step needs a name, entrypoint, column contracts and optional row checks."
            )
        if not isinstance(step.get("name"), str) or not 1 <= len(step["name"]) <= 255:
            raise error("Every step needs a name of at most 255 characters.")
        entrypoint = step.get("entrypoint")
        if (
            not isinstance(entrypoint, str)
            or entrypoint not in files
            or not entrypoint.endswith(".py")
        ):
            raise error("Every entrypoint must name a Python file inside the package.")
        schema(step.get("input_schema", {}))
        schema(step.get("output_schema", {}))
        if "batch_rows" in step and (
            type(step["batch_rows"]) is not int or not 1 <= step["batch_rows"] <= 100000
        ):
            raise error(
                "batch_rows must be an integer between 1 and 100000. Only declare it for independent row transformations."
            )
        if step.get("consumer") not in {
            None,
            "decision_train",
            "decision_eval",
            "chat_train",
            "model_eval",
        }:
            raise error("Consumer must be decision_train, decision_eval, chat_train or model_eval.")
        checks = step.get("checks", {})
        if not isinstance(checks, dict) or set(checks) - {"min_rows", "max_rows", "preserve_rows"}:
            raise error("Supported checks: min_rows, max_rows, preserve_rows.")
        for key in ("min_rows", "max_rows"):
            if key in checks and (
                type(checks[key]) is not int or not 0 <= checks[key] <= 100000000
            ):
                raise error("Row check bounds must be nonnegative integers.")
        if "preserve_rows" in checks and type(checks["preserve_rows"]) is not bool:
            raise error("preserve_rows must be a boolean.")
        if checks.get("min_rows", 0) > checks.get("max_rows", 100000000):
            raise error("min_rows cannot exceed max_rows.")
    pipeline_graph.describe(steps, files=files)
    try:
        schema(manifest.get("parameters", {}))
    except DatasetError as exc:
        raise DatasetError(
            'Manifest parameters declare types, for example {"seed": "integer"}. Supply parameter values when running the pipeline.',
            code="pipeline_parameters",
        ) from exc
    limits = manifest.get("limits", {})
    bounds = {
        "seconds": (1, 1200),
        "memory_mb": (64, 4096),
        "scratch_mb": (16, 4096),
        "cpus": (1, 4),
    }
    if not isinstance(limits, dict) or set(limits) - bounds.keys():
        raise error("Supported limits: seconds, memory_mb, scratch_mb, cpus.")
    for key, value in limits.items():
        low, high = bounds[key]
        if type(value) is not int or not low <= value <= high:
            raise error(f"{key} must be between {low} and {high}.")
    return manifest


def inspect_bytes(data, *, require_runtime=True):
    if not data or len(data) > MAX_BYTES:
        raise error("Pipeline packages must be nonempty and at most 10 MiB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_FILES or sum(e.file_size for e in entries) > MAX_BYTES:
                raise error("Package exceeds 100 files or 10 MiB expanded.")
            files = {}
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in entry.filename
                    or path.as_posix() != entry.filename
                    or entry.is_dir()
                    or entry.filename in files
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                    or path.suffix not in {".py", ".json", ".txt", ".lock"}
                ):
                    raise error(
                        "Packages accept unique relative regular Python, JSON, text and lock files only."
                    )
                files[entry.filename] = archive.read(entry).decode("utf-8")
                if path.suffix == ".py":
                    ast.parse(files[entry.filename], filename=entry.filename)
            manifest = json.loads(files["manifest.json"])
            validate_manifest(manifest, files, require_runtime=require_runtime)
    except DatasetError:
        raise
    except (
        zipfile.BadZipFile,
        KeyError,
        UnicodeError,
        SyntaxError,
        ValueError,
        RuntimeError,
    ) as exc:
        raise error(
            "The package is not a valid UTF-8 ZIP with manifest.json and valid Python syntax."
        ) from exc
    inventory = [
        {
            "path": name,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "bytes": len(content.encode()),
        }
        for name, content in sorted(files.items())
    ]
    return manifest, inventory, files


def save(project, user, upload):
    data = upload.read(MAX_BYTES + 1)
    manifest, inventory, _ = inspect_bytes(data)
    checksum = hashlib.sha256(data).hexdigest()
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        existing = DatasetPipelinePackage.objects.filter(project=project, sha256=checksum).first()
        if existing:
            return existing
        package = DatasetPipelinePackage(
            project=project,
            sha256=checksum,
            size=len(data),
            manifest=manifest,
            inventory=inventory,
            created_by=user,
        )
        package.bundle.save(f"{package.pk}.zip", ContentFile(data), save=False)
        package.save()
    return package


def files(package):
    with package.bundle.open("rb") as source:
        data = source.read(MAX_BYTES + 1)
    if hashlib.sha256(data).hexdigest() != package.sha256:
        raise error("The retained package fingerprint does not match.")
    manifest, inventory, retained = inspect_bytes(data, require_runtime=False)
    if manifest != package.manifest or inventory != package.inventory:
        raise error("The retained package manifest or inventory does not match its bytes.")
    return retained


def record(package):
    return {
        "id": str(package.pk),
        "project": str(package.project_id),
        "sha256": package.sha256,
        "size": package.size,
        "manifest": package.manifest,
        "inventory": package.inventory,
        "created_at": package.created_at.isoformat(),
    }


def source_file(package, filename, *, offset=0, limit=8000):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
        raise error("Use a nonnegative character offset and limit 1–16000.")
    retained = files(package)
    if filename not in retained:
        raise error("That file is not in the retained package.")
    content = retained[filename]
    return {
        "path": filename,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "content": content[offset : offset + limit],
        "offset": offset,
        "total": len(content),
        "next_offset": offset + limit if offset + limit < len(content) else None,
    }
