import io
import json
import zipfile

from django.test import override_settings

from overbae.services.datasets import pipeline_packages, store


def script_step(code, *, columns=(), output_schema=None, **graph):
    return {
        "name": "Transform",
        "input_schema": dict.fromkeys(columns, "any"),
        "output_schema": output_schema or {},
        **graph,
        "code": code,
    }


def select_script(columns, **graph):
    return script_step(
        "def transform(rows, parameters):\n"
        "    for row in rows:\n"
        f"        yield {{key: row[key] for key in {['source_row', *columns]!r}}}\n",
        columns=columns,
        output_schema=dict.fromkeys(["source_row", *columns], "any"),
        **graph,
    )


def filter_script(column, equals, **graph):
    return script_step(
        "def transform(rows, parameters):\n"
        "    for row in rows:\n"
        f"        if row[{column!r}] == {equals!r}:\n"
        "            yield {key: value for key, value in row.items() if key != '_overmind_parent_rows'}\n",
        columns=[column],
        output_schema={"source_row": "integer", column: "any"},
        condition={"expression": f"{column} = {json.dumps(equals)}", "line": 3},
        **graph,
    )


def rename_script(mapping, **graph):
    return script_step(
        "def transform(rows, parameters):\n"
        f"    mapping = {mapping!r}\n"
        "    for row in rows:\n"
        "        if any(target in row and target not in mapping for target in mapping.values()):\n"
        "            raise ValueError('Rename cannot overwrite existing columns.')\n"
        "        yield {mapping.get(key, key): value for key, value in row.items()}\n",
        columns=list(mapping),
        output_schema={"source_row": "integer", **dict.fromkeys(mapping.values(), "any")},
        **graph,
    )


def conversation_script(question, answer, **graph):
    return script_step(
        "def transform(rows, parameters):\n"
        "    for row in rows:\n"
        "        if 'messages' in row:\n"
        "            raise ValueError('Messages already exist.')\n"
        f"        question, answer = row[{question!r}], row[{answer!r}]\n"
        "        if not isinstance(question, str) or not isinstance(answer, str):\n"
        "            raise ValueError('Conversation columns must contain strings.')\n"
        "        result = {key: value for key, value in row.items() if key != '_overmind_parent_rows'}\n"
        "        yield {**result, 'messages': [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]}\n",
        columns=[question, answer],
        output_schema={"source_row": "integer", "messages": "array"},
        **graph,
    )


def identity_script(**graph):
    return script_step("def transform(rows, parameters):\n    yield from rows\n", **graph)


def retained_package(project, user, steps):
    stream = io.BytesIO()
    manifest = {"version": 1, "runtime": "sha256:" + "b" * 64, "steps": []}
    with zipfile.ZipFile(stream, "w") as archive:
        for index, definition in enumerate(steps):
            step = dict(definition)
            code = step.pop("code")
            step["entrypoint"] = f"step_{index}.py"
            archive.writestr(
                zipfile.ZipInfo(step["entrypoint"]),
                code + "\nif __name__ == '__main__':\n"
                "    import json, sys\n"
                "    with open(sys.argv[1]) as source, open(sys.argv[2], 'w') as output, open(sys.argv[3]) as params:\n"
                "        for row in transform(map(json.loads, source), json.load(params)):\n"
                "            output.write(json.dumps(row, ensure_ascii=False) + '\\n')\n",
            )
            manifest["steps"].append(step)
        archive.writestr(zipfile.ZipInfo("manifest.json"), json.dumps(manifest))
    stream.seek(0)
    with override_settings(WORKSHOP_RUNTIME_IMAGES=[manifest["runtime"]]):
        return str(pipeline_packages.save(project, user, stream).pk)


def execute_fixture(run, index, input_path, directory, step, files, progress):
    # Only trusted fixture code uses this hook; the live E2E exercises the restricted runner.
    namespace = {"__name__": "fixture"}
    exec(compile(files[step["entrypoint"]], step["entrypoint"], "exec"), namespace)
    return namespace["transform"](store.iter_rows(input_path), run.specification["parameters"])
