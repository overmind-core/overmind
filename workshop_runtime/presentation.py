import base64
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

CATALOG = json.loads(Path(__file__).with_name("presentation-catalog.json").read_text())
MAX_BYTES = 2 * 1024 * 1024


class DataReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=512)
    cells: list[UUID] = Field(min_length=1, max_length=20)
    scope: str = Field(min_length=1, max_length=1000)


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: dict
    data: dict[str, DataReference] = Field(default_factory=dict, max_length=20)
    images: dict[str, DataReference] = Field(default_factory=dict, max_length=10)


class Interaction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=180)
    component: str = Field(min_length=1, max_length=160)
    values: dict = Field(default_factory=dict)


def nodes(root):
    count = 0

    def visit(value, depth):
        nonlocal count
        if depth > 20:
            raise ValueError("Presentation nesting is too deep")
        if isinstance(value, dict):
            if "component" in value:
                count += 1
                if count > 64 or set(value) != {"component", "props"}:
                    raise ValueError("Presentation needs at most 64 component/props nodes")
                name = value["component"]
                if not isinstance(name, str) or name not in CATALOG:
                    raise ValueError("Unknown presentation component")
                error = next(
                    Draft202012Validator(CATALOG[name]["props"]).iter_errors(value["props"]), None
                )
                if error:
                    raise ValueError(f"{name}: {error.message}")
                yield value
            for item in value.values():
                yield from visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                yield from visit(item, depth + 1)

    if root.get("component") != "Stack":
        raise ValueError("Presentation root must be Stack")
    yield from visit(root, 0)


def validate(document):
    document = Document.model_validate(document).model_dump(mode="json")
    questions = set()
    for node in nodes(document["root"]):
        props, kind = node["props"], node["component"]
        children = (
            props.get("children", [])
            if kind in {"Stack", "Details"}
            else [child for tab in props["tabs"] for child in tab["children"]]
            if kind == "Tabs"
            else []
        )
        if any(
            not isinstance(child, dict) or set(child) != {"component", "props"}
            for child in children
        ):
            raise ValueError("Children must be component/props nodes")
        if kind in {"Chart", "Table", "Metric"} and props["data"] not in document["data"]:
            raise ValueError("Data component references an undeclared data file")
        if kind == "Image" and props["image"] not in document["images"]:
            raise ValueError("Image component references an undeclared image file")
        if kind in {"Question", "Choices"}:
            if props["id"] in questions:
                raise ValueError("Question IDs must be unique within the presentation")
            questions.add(props["id"])
            fields = props.get("fields", [])
            if len({field["name"] for field in fields}) != len(fields):
                raise ValueError("Question field names must be unique")
            for field in fields:
                if field["type"] in {"select", "multiselect"} and not field.get("options"):
                    raise ValueError("Selection fields require options")
                if field.get("min", -math.inf) > field.get("max", math.inf):
                    raise ValueError("Field minimum exceeds maximum")
            for options in [props.get("options", []), *[f.get("options", []) for f in fields]]:
                if len({option["value"] for option in options}) != len(options):
                    raise ValueError("Option values must be unique")
    return document


def prepare(document, read):
    document = validate(document)
    result = {"document": document, "data": {}, "images": {}}
    total = len(json.dumps(document).encode())
    for group in ("data", "images"):
        for key, reference in document[group].items():
            raw = read(reference["path"])
            total += len(raw)
            if total > MAX_BYTES:
                raise ValueError("Presentation exceeds 2 MiB; aggregate or sample data explicitly")
            if group == "data":
                rows = json.loads(raw)
                if (
                    not isinstance(rows, list)
                    or len(rows) > 2000
                    or not all(isinstance(row, dict) for row in rows)
                ):
                    raise ValueError(
                        "Presentation data must be a JSON array of at most 2000 objects"
                    )
                json.dumps(rows, allow_nan=False)
                result[group][key] = {"rows": rows, "sha256": hashlib.sha256(raw).hexdigest()}
            else:
                mime = (
                    "image/png"
                    if raw.startswith(b"\x89PNG\r\n\x1a\n")
                    else "image/jpeg"
                    if raw.startswith(b"\xff\xd8\xff")
                    else "image/webp"
                    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
                    else None
                )
                if not mime:
                    raise ValueError("Presentation images must be PNG, JPEG or WebP")
                result[group][key] = {
                    "url": f"data:{mime};base64,{base64.b64encode(raw).decode()}",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
    validate_data(result)
    return result


def validate_data(presentation):
    for node in nodes(presentation["document"]["root"]):
        props, kind = node["props"], node["component"]
        if kind not in {"Chart", "Table", "Metric"}:
            continue
        rows = presentation["data"][props["data"]]["rows"]
        columns = set().union(*(row.keys() for row in rows))
        required = (
            [props["x"], *props["y"]]
            if kind == "Chart"
            else [props["key"]]
            if kind == "Metric"
            else props.get("columns", [])
        )
        if not set(required) <= columns:
            raise ValueError(f"{kind} references columns missing from its data")
        if kind == "Metric" and len(rows) != 1:
            raise ValueError("Metric data must contain exactly one row")
        if kind == "Chart":
            if not rows:
                raise ValueError("Chart data is empty")
            if kind == "Chart" and props["kind"] in {"pie", "scatter"} and len(props["y"]) != 1:
                raise ValueError("Pie and scatter charts require one y column")
            numeric = props["y"] + ([props["x"]] if props["kind"] == "scatter" else [])
            for row in rows:
                if not isinstance(row.get(props["x"]), (str, int, float)):
                    raise ValueError("Chart labels must be strings or numbers")
                for column in numeric:
                    value = row.get(column)
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError(
                            "Chart values must be finite numbers; missing values are not zeros"
                        )
                    if props["kind"] == "pie" and value < 0:
                        raise ValueError("Pie chart values must be nonnegative")


def program(root):
    def render(value):
        if isinstance(value, dict) and "component" in value:
            name, props = value["component"], value["props"]
            keys = list(CATALOG[name]["props"]["properties"])
            last = max((keys.index(key) for key in props), default=-1)
            return f"{name}(" + ", ".join(render(props.get(key)) for key in keys[: last + 1]) + ")"
        if isinstance(value, list):
            return "[" + ", ".join(map(render, value)) + "]"
        if isinstance(value, dict):
            return (
                "{"
                + ", ".join(json.dumps(key) + ": " + render(item) for key, item in value.items())
                + "}"
            )
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    return "root = " + render(root)


def answer(document, identity, values):
    question = next(
        (
            node
            for node in nodes(document["root"])
            if node["component"] in {"Question", "Choices"} and node["props"]["id"] == identity
        ),
        None,
    )
    if question is None:
        raise ValueError("Question no longer exists in this presentation")
    if len(json.dumps(values, allow_nan=False)) > 16000:
        raise ValueError("Response exceeds 16000 characters")
    props = question["props"]
    fields = props.get("fields") or [
        {
            "name": "choice",
            "label": "Choice",
            "type": "select",
            "required": True,
            "options": props["options"],
        }
    ]
    if set(values) - {field["name"] for field in fields}:
        raise ValueError("Response includes unknown fields")
    lines = [props["prompt"]]
    for field in fields:
        value = values.get(field["name"])
        if value is None or value == "" or value == []:
            if field.get("required"):
                raise ValueError(f"{field['label']} is required")
            continue
        kind = field["type"]
        if kind == "number" and (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not field.get("min", -math.inf) <= value <= field.get("max", math.inf)
        ):
            raise ValueError(f"{field['label']} needs a number within its bounds")
        if kind == "boolean" and type(value) is not bool:
            raise ValueError(f"{field['label']} needs a boolean")
        if kind in {"text", "textarea"} and (not isinstance(value, str) or len(value) > 8000):
            raise ValueError(f"{field['label']} needs text up to 8000 characters")
        if kind in {"select", "multiselect"}:
            selected = value if kind == "multiselect" else [value]
            allowed = {option["value"]: option["label"] for option in field["options"]}
            if not isinstance(selected, list) or any(
                not isinstance(item, str) or item not in allowed for item in selected
            ):
                raise ValueError(f"{field['label']} includes an unknown option")
            if len(set(selected)) != len(selected):
                raise ValueError("Response contains repeated options")
            value = ", ".join(allowed[item] for item in selected)
        lines.append(f"{field['label']}: {value}")
    return "\n".join(lines)
