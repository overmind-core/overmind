"""What changed between two frames, matched on ``source_row``. Counts for the
cell, marks for a page of the grid, samples for the chat."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from overbae.services.datasets import store

_SAMPLE = 5
_VALUE_CHARS = 300


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _VALUE_CHARS:
        return value[:_VALUE_CHARS] + "…"
    return value


def _names(path: Path) -> list[str]:
    return [c["name"] for c in store.read_manifest(path)]


def _tracked(a: Path, b: Path) -> bool:
    return store.SOURCE_ROW in _names(a) and store.SOURCE_ROW in _names(b)


def _neq(columns: list[str]) -> str:
    return " OR ".join(
        f'(CAST(a."{c}" AS VARCHAR) IS DISTINCT FROM CAST(b."{c}" AS VARCHAR))' for c in columns
    )


def summary(before: Path, after: Path) -> dict[str, Any]:
    """The line under a cell: rows added and removed, table cells changed,
    columns added and removed."""
    names_a, names_b = _names(before), _names(after)
    out: dict[str, Any] = {
        "rows_before": store.row_count(before),
        "rows_after": store.row_count(after),
        "columns_added": [n for n in names_b if n not in names_a],
        "columns_removed": [n for n in names_a if n not in names_b],
        "tracked": _tracked(before, after),
    }
    if not out["tracked"]:
        return out
    shared = [n for n in names_b if n in names_a and n != store.SOURCE_ROW]
    con = store.connect(a=before, b=after)
    try:
        out["rows_removed"] = int(
            con.execute(
                "SELECT count(*) FROM a WHERE a.source_row IS NULL OR a.source_row NOT IN "
                "(SELECT source_row FROM b WHERE source_row IS NOT NULL)"
            ).fetchone()[0]
        )
        out["rows_added"] = int(
            con.execute(
                "SELECT count(*) FROM b WHERE b.source_row IS NULL OR b.source_row NOT IN "
                "(SELECT source_row FROM a WHERE source_row IS NOT NULL)"
            ).fetchone()[0]
        )
        changed: dict[str, int] = {}
        for c in shared:
            n = con.execute(
                f"SELECT count(*) FROM a JOIN b USING (source_row) WHERE {_neq([c])}"
            ).fetchone()[0]
            if n:
                changed[c] = int(n)
        out["cells_changed"] = sum(changed.values())
        out["changed_columns"] = changed
    finally:
        con.close()
    return out


def marks(before: Path, after: Path, source_rows: list[int]) -> dict[int, dict[str, Any]]:
    """For rows of ``after`` on one grid page: ``{source_row: {added: true} |
    {before: {column: old value}}}``. Rows absent from the map are unchanged."""
    if not source_rows or not _tracked(before, after):
        return {}
    names_a, names_b = _names(before), _names(after)
    shared = [n for n in names_b if n in names_a and n != store.SOURCE_ROW]
    con = store.connect(a=before, b=after)
    out: dict[int, dict[str, Any]] = {}
    try:
        ids = ", ".join(str(int(i)) for i in source_rows)
        present = {
            r[0]
            for r in con.execute(f"SELECT source_row FROM a WHERE source_row IN ({ids})").fetchall()
        }
        for sr in source_rows:
            if sr not in present:
                out[int(sr)] = {"added": True}
        if shared and present:
            select = ", ".join(f'a."{c}" AS "{c}"' for c in shared)
            kinds = {c["name"]: c["type"] for c in store.read_manifest(before)}
            cur = con.execute(
                f"SELECT a.source_row, {select} FROM a JOIN b USING (source_row) "
                f"WHERE a.source_row IN ({ids}) AND ({_neq(shared)})"
            )
            cols = [d[0] for d in cur.description]
            # One connection, one live cursor: fetch before the next statement.
            changed_rows = cur.fetchall()
            after_rows = {
                r[0]: r
                for r in con.execute(
                    f"SELECT b.source_row, {', '.join(f'b."{c}"' for c in shared)} FROM b "
                    f"WHERE b.source_row IN ({ids})"
                ).fetchall()
            }
            for rec in changed_rows:
                sr = rec[0]
                b_rec = after_rows.get(sr)
                changed: dict[str, Any] = {}
                for i, c in enumerate(cols[1:], start=1):
                    b_val = b_rec[i] if b_rec is not None else None
                    if str(rec[i]) != str(b_val):
                        changed[c] = store.decode(rec[i], kinds.get(c, "string"))
                if changed:
                    out[int(sr)] = {"before": changed}
    finally:
        con.close()
    return out


def removed_rows(before: Path, after: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Rows of ``before`` that ``after`` no longer has."""
    if not _tracked(before, after):
        return []
    result = store.query(
        "SELECT a.* FROM a WHERE a.source_row NOT IN "
        "(SELECT source_row FROM b WHERE source_row IS NOT NULL) ORDER BY a.source_row",
        limit=limit,
        a=before,
        b=after,
    )
    return result["rows"]


def between(before: Path, after: Path) -> dict[str, Any]:
    """The summary plus a few example rows, for the chat."""
    out = summary(before, after)
    if not out.get("tracked"):
        return out
    names_a, names_b = _names(before), _names(after)
    shared = [n for n in names_b if n in names_a and n != store.SOURCE_ROW]
    changed = list((out.get("changed_columns") or {}).keys())
    con = store.connect(a=before, b=after)
    try:
        if changed:
            select = ", ".join(
                f'a."{c}" AS "before__{c}", b."{c}" AS "after__{c}"' for c in changed
            )
            rows = con.execute(
                f"SELECT a.source_row, {select} FROM a JOIN b USING (source_row) "
                f"WHERE {_neq(shared)} LIMIT {_SAMPLE}"
            ).fetchall()
            out["changed_examples"] = [
                {
                    "source_row": rec[0],
                    **{
                        c: {"before": _short(rec[1 + 2 * i]), "after": _short(rec[2 + 2 * i])}
                        for i, c in enumerate(changed)
                        if str(rec[1 + 2 * i]) != str(rec[2 + 2 * i])
                    },
                }
                for rec in rows
            ]
    finally:
        con.close()
    if out.get("rows_removed"):
        out["removed_examples"] = [
            {k: _short(v) for k, v in r.items()} for r in removed_rows(before, after, limit=_SAMPLE)
        ]
    return out
