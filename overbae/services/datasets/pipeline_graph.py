import json
import re

from overbae.services.datasets.lifecycle import DatasetError


def describe(steps, *, files=None):
    nodes, edges, known = [], [], {"source"}
    previous = "source"
    for index, step in enumerate(steps):
        identity = step.get("id", f"step_{index + 1}")
        sources = step.get("inputs", [step.get("input", previous)])
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", identity)
            or identity in known
        ):
            raise DatasetError(
                "Step IDs must be unique names, excluding source.", code="pipeline_flow"
            )
        if (
            ("input" in step and "inputs" in step)
            or not isinstance(sources, list)
            or not 1 <= len(sources) <= 20
            or any(not isinstance(source, str) or source not in known for source in sources)
            or len(set(sources)) != len(sources)
        ):
            raise DatasetError(
                "Use input or a nonempty inputs list of distinct earlier steps/source. Cycles and forward references are not supported.",
                code="pipeline_flow",
            )
        condition = None
        if "condition" in step:
            declared = step["condition"]
            if (
                not isinstance(declared, dict)
                or set(declared) != {"expression", "line"}
                or not isinstance(declared["expression"], str)
                or not 1 <= len(declared["expression"]) <= 1000
                or type(declared["line"]) is not int
                or declared["line"] < 1
                or not step.get("entrypoint")
            ):
                raise DatasetError(
                    "Script conditions require an expression and a positive line in the step's entrypoint.",
                    code="pipeline_flow",
                )
            if files is not None and declared["line"] > len(files[step["entrypoint"]].splitlines()):
                raise DatasetError(
                    "The condition line is outside its retained script.", code="pipeline_flow"
                )
            condition = {**declared, "file": step["entrypoint"], "evidence": "agent_declared"}
        elif step.get("operation") == "filter":
            condition = {
                "expression": f"{step['column']} = {json.dumps(step['equals'])}",
                "evidence": "executable_filter",
            }
        nodes.append(
            {
                "id": identity,
                "step": index,
                "name": step.get("name", step.get("operation", "Transformation")),
                "entrypoint": step.get("entrypoint"),
                "input": sources[0] if len(sources) == 1 else None,
                "inputs": sources,
            }
        )
        edges.extend(
            {"source": source, "target": identity, "condition": condition} for source in sources
        )
        known.add(identity)
        previous = identity
    consumed = {edge["source"] for edge in edges}
    terminals = [node["id"] for node in nodes if node["id"] not in consumed]
    return {
        "source": "source",
        "output": previous,
        "nodes": nodes,
        "edges": edges,
        "terminal_steps": terminals,
        "unconsumed_steps": [identity for identity in terminals if identity != previous],
        "input_semantics": "Multiple inputs concatenate in declared order. Nonempty branches must have matching data columns/types; incompatible schemas and overlapping source_row identities are rejected, never coerced or deduplicated.",
        "routing": "retained_code",
        "condition_semantics": "Labels describe code; labels are never evaluated as code. Each step executes against its declared input.",
    }
