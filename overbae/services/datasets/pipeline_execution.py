from itertools import islice

from overbae.services.datasets import contract, store


def execute_rows(run, index, source, directory, step, files, progress, executor):
    batch_rows = step.get("batch_rows")
    total = store.row_count(source)
    if not batch_rows or not total:
        yield from executor(run, index, source, directory, step, files, progress)
        return
    receipt = run.result["steps"][index]
    receipt["batches"] = {
        "completed": 0,
        "total": (total + batch_rows - 1) // batch_rows,
        "input_rows": 0,
        "output_rows": 0,
    }
    facts = receipt["batches"]
    records = iter(store.iter_rows(source))
    manifest = store.read_manifest(source)
    for offset in range(0, total, batch_rows):
        facts["current"] = facts["completed"] + 1
        for field in ("runtime", "exit_code", "provider_execution", "container"):
            receipt.pop(field, None)
        if not progress():
            raise ValueError("Publication was cancelled before the next batch.")
        working = directory / f"{index}-batch-{facts['current']}"
        working.mkdir(parents=True, exist_ok=True)
        incoming = working / "input.parquet"
        store.write_rows(incoming, islice(records, batch_rows), manifest)
        produced = 0
        try:
            for row in executor(run, index, incoming, working, step, files, progress):
                produced += 1
                yield row
        finally:
            incoming.unlink(missing_ok=True)
        facts["completed"] += 1
        facts["input_rows"] += min(batch_rows, total - offset)
        facts["output_rows"] += produced
        if not progress():
            raise ValueError("Publication was cancelled after batch execution.")


def check_consumer(path, consumer):
    if not consumer:
        return None
    native = consumer.startswith("decision")
    intent = "train" if consumer.endswith("train") else "eval"
    for index, row in enumerate(store.iter_rows(path)):
        is_decision = (
            "decision" in row
            if intent == "train"
            else contract.eval_input_type(row.get("input")) == "decision"
        )
        if native != is_decision:
            raise ValueError(f"{consumer}: row {index} has an incompatible format.")
    report = contract.measure_path(path)
    result = report[intent]
    if not result["ok"]:
        raise ValueError(f"{consumer}: {result['reason']}")
    actual = result.get("format") if intent == "train" else result.get("input_type")
    if native != (actual == "decision"):
        raise ValueError(f"{consumer}: output has incompatible format {actual!r}.")
    return {"consumer": consumer, "rows": report["rows"], "passed": True}
