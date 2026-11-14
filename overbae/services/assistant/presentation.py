"""The components the assistant may show. The Console renders exactly these; nothing the
model writes is executed. Charts, tables and metrics read rows of a table the kernel
computed, never numbers the model typed."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from overbae.core.errors import InputValidationError
from overbae.models import Cell

MAX_ROWS = 2000
MAX_COLUMNS = 60
MAX_IMAGE_BYTES = 4 * 1024**2
# Leading bytes of the formats a browser is given, and the extension each is stored under.
IMAGE_KINDS = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"RIFF", "webp"))
Name = Annotated[str, Field(min_length=1, max_length=160)]
Text = Annotated[str, Field(max_length=16000)]


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Option(Component):
    label: Name
    value: Name


class FormField(Component):
    name: Name
    label: Name
    type: Literal["text", "textarea", "number", "select", "multiselect", "boolean"]
    options: list[Option] = Field(default_factory=list, max_length=100)
    placeholder: str = Field(default="", max_length=160)
    required: bool = False
    min: float | None = None
    max: float | None = None


class Callout(Component):
    """An assumption, limitation, finding or warning."""

    type: Literal["Callout"]
    tone: Literal["neutral", "info", "success", "warning", "error"]
    text: Text


class CellLink(Component):
    """Open an exact published dataset version by its cell id."""

    type: Literal["Cell"]
    cell: Name
    label: str = Field(default="", max_length=160)


class Chart(Component):
    """Plot a table by key. Use bar for computed histogram bins."""

    type: Literal["Chart"]
    title: Name
    kind: Literal["bar", "horizontal-bar", "line", "area", "pie", "scatter"]
    data: Name
    x: Name
    y: list[Name] = Field(min_length=1, max_length=8)
    stacked: bool = False
    x_label: str = Field(default="", max_length=160)
    y_label: str = Field(default="", max_length=160)


class Choices(Component):
    """Offer next steps or resolve an ambiguity. The selection arrives as the user's reply."""

    type: Literal["Choices"]
    id: Name
    prompt: Text
    options: list[Option] = Field(min_length=1, max_length=12)


class Code(Component):
    """Code shown without execution."""

    type: Literal["Code"]
    code: Text
    language: str = Field(default="", max_length=40)


class Details(Component):
    """Optional explanation or evidence behind a disclosure."""

    type: Literal["Details"]
    title: Name
    children: list[Node] = Field(max_length=64)


class Heading(Component):
    type: Literal["Heading"]
    text: Name


class Image(Component):
    """Show a PNG, JPEG or WebP file the kernel wrote, by image key."""

    type: Literal["Image"]
    title: Name
    image: Name


class Metric(Component):
    """One value from the first row of a table. Format percent expects a fraction."""

    type: Literal["Metric"]
    label: Name
    data: Name
    key: Name
    format: Literal["number", "percent", "text"] = "number"


class Question(Component):
    """Collect intent, mappings or constraints. The answers arrive as the user's reply."""

    type: Literal["Question"]
    id: Name
    prompt: Text
    fields: list[FormField] = Field(min_length=1, max_length=20)
    submit_label: str = Field(default="", max_length=160)


class Stack(Component):
    """Group content in a column, or in a wrapping row."""

    type: Literal["Stack"]
    direction: Literal["column", "row"] = "column"
    children: list[Node] = Field(max_length=64)


class Step(Component):
    status: Literal["pending", "running", "completed", "failed"]
    text: Text


class Steps(Component):
    """A plan or recorded progress. Mark a step completed only when it ran."""

    type: Literal["Steps"]
    items: list[Step] = Field(min_length=1, max_length=30)


class Table(Component):
    """Searchable, sortable rows of a table. Omit columns to show all."""

    type: Literal["Table"]
    title: Name
    data: Name
    columns: list[Name] = Field(default_factory=list, max_length=40)


class Tab(Component):
    title: Name
    children: list[Node] = Field(max_length=64)


class Tabs(Component):
    """Related views, such as distribution and data quality."""

    type: Literal["Tabs"]
    tabs: list[Tab] = Field(min_length=1, max_length=8)


class Markdown(Component):
    """Explanation in Markdown. Measured data belongs in data components."""

    type: Literal["Text"]
    text: Text


Node = Annotated[
    Callout
    | CellLink
    | Chart
    | Choices
    | Code
    | Details
    | Heading
    | Image
    | Metric
    | Question
    | Stack
    | Steps
    | Table
    | Tabs
    | Markdown,
    Field(discriminator="type"),
]


class Presentation(Component):
    """Show a result to the user. Tables name JSON files the kernel wrote in its working
    directory, each a list of flat records; images name PNG, JPEG or WebP files there."""

    root: Node
    tables: dict[Name, Name] = Field(
        default_factory=dict,
        max_length=12,
        description="Table key to file name, for example {'classes': 'classes.json'}.",
    )
    images: dict[Name, Name] = Field(
        default_factory=dict,
        max_length=8,
        description="Image key to file name, for example {'ages': 'ages.png'}.",
    )


for _model in (Details, Stack, Tab, Tabs, Presentation):
    _model.model_rebuild()


def _walk(node):
    yield node
    for child in getattr(node, "children", []):
        yield from _walk(child)
    for tab in getattr(node, "tabs", []):
        for child in tab.children:
            yield from _walk(child)


def _rows(name: str, raw: bytes) -> list[dict]:
    try:
        rows = json.loads(raw)
    except ValueError:
        raise InputValidationError(f"{name} is not JSON.") from None
    if (
        not isinstance(rows, list)
        or len(rows) > MAX_ROWS
        or not all(
            isinstance(row, dict)
            and len(row) <= MAX_COLUMNS
            and all(
                value is None or isinstance(value, str | int | float | bool)
                for value in row.values()
            )
            for row in rows
        )
    ):
        raise InputValidationError(
            f"{name} must be a list of at most {MAX_ROWS} flat records with scalar values."
        )
    return rows


def _image(name: str, raw: bytes) -> str:
    kind = next((ext for magic, ext in IMAGE_KINDS if raw.startswith(magic)), None)
    if kind is None or (kind == "webp" and raw[8:12] != b"WEBP") or len(raw) > MAX_IMAGE_BYTES:
        raise InputValidationError(f"{name} must be a PNG, JPEG or WebP image of at most 4 MiB.")
    return f"{hashlib.sha256(raw).hexdigest()}.{kind}"


def build(project, arguments: dict, read_file, save_image) -> dict:
    """The validated document with the rows, images and cell facts it names, ready to
    store. ``save_image(name, bytes)`` retains an image under its content name."""
    try:
        presentation = Presentation.model_validate(arguments)
    except ValidationError as exc:
        raise InputValidationError(str(exc)) from exc
    nodes = list(_walk(presentation.root))
    used = {node.data for node in nodes if isinstance(node, Chart | Metric | Table)}
    missing = used - presentation.tables.keys()
    if missing:
        raise InputValidationError(f"No table is declared for: {', '.join(sorted(missing))}.")
    data = {}
    for key in sorted(used):
        try:
            data[key] = _rows(presentation.tables[key], read_file(presentation.tables[key]))
        except FileNotFoundError:
            raise InputValidationError(
                f"The kernel has no file {presentation.tables[key]} in its working directory."
            ) from None
    images = {}
    for key in sorted({node.image for node in nodes if isinstance(node, Image)}):
        if key not in presentation.images:
            raise InputValidationError(f"No image is declared for: {key}.")
        try:
            raw = read_file(presentation.images[key])
        except FileNotFoundError:
            raise InputValidationError(
                f"The kernel has no file {presentation.images[key]} in its working directory."
            ) from None
        images[key] = _image(presentation.images[key], raw)
        save_image(images[key], raw)
    wanted = {node.cell for node in nodes if isinstance(node, CellLink)}
    cells = {
        str(cell.pk): {
            "dataset": str(cell.dataset_id),
            "title": cell.title,
            "rows": cell.rows,
            "name": cell.dataset.name,
        }
        for cell in Cell.objects.filter(
            dataset__project=project, pk__in=_uuids(wanted)
        ).select_related("dataset")
    }
    if wanted - cells.keys():
        raise InputValidationError("A Cell component names a version that is not in this project.")
    return {
        "root": presentation.root.model_dump(mode="json"),
        "data": data,
        "images": images,
        "cells": cells,
    }


def _uuids(values):
    out = []
    for value in values:
        try:
            out.append(uuid.UUID(value))
        except ValueError:
            raise InputValidationError(f"{value} is not a cell id.") from None
    return out
