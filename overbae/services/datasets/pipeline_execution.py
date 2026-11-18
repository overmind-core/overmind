from pathlib import Path

from overbae.services.datasets import lineage, store

PARENT_ROWS = "_overmind_parent_rows"
# Identity columns an output row keeps from its parents when every parent agrees.
INHERITED = ("trace_id", "source_trace_id", "conversation_id", store.EVIDENCE)
_INTEGER_JSON = ("'BIGINT'", "'UBIGINT'", "'HUGEINT'")


def contract_violation(path: Path, contract: dict) -> str | None:
    """The first row whose value does not match the declared column type, read from the
    stored columns rather than decoded rows."""
    kinds = {column["name"]: column["type"] for column in store.read_manifest(path)}
    missing = sorted(set(contract) - set(kinds))
    if missing:
        return f"Missing required input columns: {', '.join(missing)}."
    checks = {name: _matches(name, kinds[name], kind) for name, kind in contract.items()}
    checks = {name: check for name, check in checks.items() if check != "true"}
    if not checks:
        return None
    literal = str(path).replace("'", "''")
    # Rows are named by source_row: DuckDB's file_row_number option refuses a frame that
    # has a column of that name.
    row = store.quote_identifier(store.SOURCE_ROW) if store.SOURCE_ROW in kinds else "NULL"
    failures = [
        f"(SELECT CASE WHEN count(*) > 0 THEN coalesce(min({row}), -1) END "
        f"FROM read_parquet('{literal}') "
        f"WHERE NOT coalesce({check}, false))"
        for check in checks.values()
    ]
    with store.connect() as con:
        rows = con.execute("SELECT " + ", ".join(failures)).fetchone()
    found = [(row, name) for row, name in zip(rows, checks, strict=True) if row is not None]
    if not found:
        return None
    row, name = min(found)
    where = f"Row {row} column" if row >= 0 else "Column"
    return f"{where} {name} does not match {contract[name]}."


def _matches(name: str, stored: str, declared: str) -> str:
    column = store.quote_identifier(name)
    json_type = f"json_type({column})"
    if declared == "any":
        return "true"
    if declared == "null":
        return f"{column} IS NULL"
    if stored == "json":
        return {
            "string": f"{json_type} = 'VARCHAR'",
            "integer": f"{json_type} IN ({', '.join(_INTEGER_JSON)})",
            "number": f"{json_type} IN ({', '.join(_INTEGER_JSON)}, 'DOUBLE')",
            "boolean": f"{json_type} = 'BOOLEAN'",
            "object": f"{json_type} = 'OBJECT'",
            "array": f"{json_type} = 'ARRAY'",
        }[declared]
    accepted = {
        "string": {"string", "datetime"},
        "integer": {"integer"},
        "number": {"integer", "number"},
        "boolean": {"boolean"},
    }.get(declared, set())
    return f"{column} IS NOT NULL" if stored in accepted else "false"


class Parents:
    """The rows a step reads: each one's lineage keys, with the declared group keys, and
    the identity columns its outputs inherit."""

    def __init__(self, inputs: list[tuple[Path, list[tuple[int, lineage.Keys]]]], group_by):
        self.keys = {}
        self.values = {}
        self.columns = set()
        for frame, keyed in inputs:
            names = {column["name"] for column in store.read_manifest(frame)}
            columns = sorted(names & {*INHERITED, *group_by})
            self.columns.update(columns)
            values = store.iter_rows(frame, columns=columns) if columns else None
            for source_row, keys in lineage.grouped(frame, keyed, group_by):
                if source_row is None or source_row in self.keys:
                    raise ValueError(
                        "The source has ambiguous row identities. Select an earlier intact version."
                    )
                self.keys[source_row] = keys
                if values is not None:
                    self.values[source_row] = next(values)


def carry(records, parents: Parents, inherited: list[lineage.Keys]):
    """Output rows with their identity resolved against ``parents``. Appends each row's
    inherited lineage keys to ``inherited`` in output order."""
    next_id = max(parents.keys, default=0) + 1
    assigned = set()
    for row in records:
        explicit = row.pop(PARENT_ROWS, None)
        identity = row.get(store.SOURCE_ROW)
        parent_ids = explicit if explicit is not None else [identity]
        if not isinstance(parent_ids, list) or not parent_ids or len(parent_ids) > 10000:
            raise ValueError(
                "A merged row needs _overmind_parent_rows containing its contributing source_row identities."
            )
        keys = set()
        values = []
        # Each ID is checked before deduplication: True would otherwise fold into 1.
        for parent in parent_ids:
            if isinstance(parent, bool) or not isinstance(parent, int) or parent < 0:
                raise ValueError(
                    "Preserve source_row, or declare _overmind_parent_rows for a split or merge."
                )
        for parent in dict.fromkeys(parent_ids):
            if parent not in parents.keys:
                raise ValueError("A parent row is not present in the bound source version.")
            keys |= parents.keys[parent]
            values.append(parents.values.get(parent, {}))
        if explicit is not None or identity in assigned:
            identity, next_id = next_id, next_id + 1
        assigned.add(identity)
        row[store.SOURCE_ROW] = identity
        for column in sorted(parents.columns):
            found = [value.get(column) for value in values]
            if all(value == found[0] for value in found):
                if found[0] is not None:
                    row[column] = found[0]
            else:
                # Parents that disagree give the merged row no single identity.
                row.pop(column, None)
        row.pop("human_reviewed", None)
        inherited.append(frozenset(keys))
        yield row


def check_consumer(report: dict, consumer: str) -> dict:
    """Whether the measured output is the format ``consumer`` reads."""
    native = consumer.startswith("decision")
    use = "train" if consumer.endswith("train") else "eval"
    result = report[use]
    if not result["ok"]:
        raise ValueError(f"{consumer}: {result['reason']}")
    if use == "train":
        decision = result.get("format") == "decision"
    else:
        types = set(result.get("input_types") or {})
        decision = types == {"decision"} if native else "decision" in types
    if native != decision:
        actual = result.get("format") if use == "train" else result.get("input_type")
        raise ValueError(f"{consumer}: output has incompatible format {actual!r}.")
    return {"consumer": consumer, "passed": True}
