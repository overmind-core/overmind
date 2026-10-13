import fcntl
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import duckdb

from modal_shared.serving.artifacts import atomic_json

SAMPLE_ROWS = 10_000


class StatisticsUnavailableError(ValueError):
    pass


def profile(path, manifest, *, fingerprint="", top=5):
    path = Path(path).resolve()
    stat = path.stat()
    identity = hashlib.sha256(
        json.dumps(
            {
                "path": str(path),
                "fingerprint": fingerprint,
                "size": stat.st_size,
                "mtime": stat.st_mtime_ns,
                "inode": stat.st_ino,
                "top": top,
                "sample": SAMPLE_ROWS,
                "implementation": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "method": "deterministic_reservoir",
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    directory = path.parent / ".statistics"
    directory.mkdir(exist_ok=True)
    cache = directory / f"{identity}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    # A process-wide semaphore is insufficient with multiple API workers.
    lock_path = Path(tempfile.gettempdir()) / "overmind-dataset-statistics.lock"
    with lock_path.open("a") as lock:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StatisticsUnavailableError(
                        "Dataset statistics are busy. Retry shortly."
                    ) from None
                time.sleep(0.05)
        try:
            if cache.exists():
                return json.loads(cache.read_text())
            result = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    str(path),
                    json.dumps(manifest),
                    str(top),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
            values = json.loads(result.stdout)
            current = path.stat()
            if (current.st_size, current.st_mtime_ns, current.st_ino) != (
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ino,
            ):
                raise StatisticsUnavailableError(
                    "Dataset changed while computing statistics. Retry."
                )
            atomic_json(cache, values)
            return values
        except (subprocess.SubprocessError, TimeoutError, ValueError) as exc:
            raise StatisticsUnavailableError(
                "Dataset statistics could not be computed. Retry shortly."
            ) from exc
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def compute(path, manifest, top):
    with duckdb.connect(config={"memory_limit": "256MB", "threads": 1}) as con:
        literal = str(path).replace("'", "''")
        con.execute(f"CREATE VIEW source AS SELECT * FROM read_parquet('{literal}')")
        total = con.execute("SELECT count(*) FROM source").fetchone()[0]
        con.execute(
            f"CREATE TEMP TABLE sample AS SELECT * FROM source USING SAMPLE reservoir({SAMPLE_ROWS} ROWS) REPEATABLE (73491)"
        )
        n = con.execute("SELECT count(*) FROM sample").fetchone()[0]
        result = []
        for spec in manifest:
            name, kind = spec["name"], spec["type"]
            col = '"' + name.replace('"', '""') + '"'
            text = f"CAST({col} AS VARCHAR)"
            nulls, distinct = con.execute(
                f"SELECT count(*) FILTER (WHERE {col} IS NULL OR {text} = ''), count(DISTINCT {text}) FROM sample"
            ).fetchone()
            entry = {
                "name": name,
                "type": kind,
                "null_rate": nulls / n if n else 0.0,
                "distinct": int(distinct),
                "sample_rows": n,
                "total_rows": total,
                "approximate": n < total,
                "method": "deterministic_reservoir",
            }
            if kind in ("integer", "number"):
                mn, mx, mean = con.execute(
                    f"SELECT min({col}), max({col}), avg({col}) FROM sample"
                ).fetchone()
                entry.update(min=mn, max=mx, mean=mean)
            elif kind in ("string", "json"):
                mean, maximum = con.execute(
                    f"SELECT avg(length({text})), max(length({text})) FROM sample"
                ).fetchone()
                entry.update(mean_len=mean, max_len=maximum)
            if kind in ("string", "boolean", "integer") and 0 < distinct <= 200:
                values = con.execute(
                    f"SELECT {text}, count(*) AS c FROM sample WHERE {col} IS NOT NULL GROUP BY 1 ORDER BY c DESC, 1 LIMIT ?",
                    [top],
                ).fetchall()
                entry["top"] = [{"value": v, "count": int(c)} for v, c in values]
            result.append(entry)
        return result


if __name__ == "__main__":
    print(json.dumps(compute(Path(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3]))))
