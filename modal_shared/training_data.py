import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path


def row_key(row: dict) -> str:
    payload = {
        key: row[key] for key in ("messages", "tools", "decision") if row.get(key) is not None
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class TransferProgress:
    def __init__(self, callback, stage, total, unit):
        self.callback = callback
        self.stage, self.total, self.unit = stage, total, unit
        self.next_update = 0
        self.report(0, force=True)

    def report(self, completed, *, force=False):
        now = time.monotonic()
        if self.callback and (force or now >= self.next_update):
            self.callback(
                {
                    "stage": self.stage,
                    "completed": completed,
                    "total": self.total,
                    "unit": self.unit,
                    "source_at": time.time(),
                }
            )
            self.next_update = time.monotonic() + 2


def write_selection(source, destination, *, progress=None):
    digest = hashlib.sha256()
    count = 0
    measurement = TransferProgress(
        progress, "building_selections", Path(source).stat().st_size, "bytes"
    )
    consumed = 0
    with Path(source).open("rb") as rows, Path(destination).open("wb") as output:
        for line in rows:
            consumed += len(line)
            if not line.strip():
                continue
            key = bytes.fromhex(row_key(json.loads(line)))
            output.write(key)
            digest.update(key)
            count += 1
            measurement.report(consumed)
    measurement.report(consumed, force=True)
    return {"rows": count, "sha256": digest.hexdigest()}


def materialize_files(artifact, expected_digest, selections, *, progress=None):
    artifact_bytes = Path(artifact).stat().st_size
    measurement = TransferProgress(progress, "verifying_prepared_data", artifact_bytes, "bytes")
    digest, consumed = hashlib.sha256(), 0
    with Path(artifact).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
            consumed += len(chunk)
            measurement.report(consumed)
    if digest.hexdigest() != expected_digest:
        raise ValueError("The preprocessing artifact changed.")
    measurement.report(consumed, force=True)
    for source, _, selection in selections:
        count = selection.get("rows")
        if type(count) is not int or count < 0 or Path(source).stat().st_size != count * 32:
            raise ValueError("The training selection row count changed.")
        if file_digest(source) != selection.get("sha256"):
            raise ValueError("The training selection checksum changed.")
    with (
        tempfile.TemporaryDirectory(prefix="training-index-") as directory,
        sqlite3.connect(Path(directory) / "tokens.sqlite") as index,
    ):
        index.execute("PRAGMA cache_size = -16384")
        index.execute("CREATE TABLE tokens (key TEXT PRIMARY KEY, offset INTEGER)")
        measurement = TransferProgress(progress, "indexing_prepared_rows", artifact_bytes, "bytes")
        with Path(artifact).open("rb") as tokens:
            while True:
                offset = tokens.tell()
                line = tokens.readline()
                if not line:
                    break
                row = json.loads(line)
                index.execute("INSERT OR IGNORE INTO tokens VALUES (?, ?)", (row["key"], offset))
                measurement.report(tokens.tell())
            index.commit()
            measurement.report(tokens.tell(), force=True)
            measurement = TransferProgress(
                progress,
                "selecting_prepared_rows",
                sum(spec["rows"] for _, _, spec in selections),
                "rows",
            )
            completed = 0
            pending = []
            try:
                for source, destination, _ in selections:
                    destination = Path(destination)
                    temporary = destination.with_suffix(".pending")
                    pending.append((temporary, destination))
                    with Path(source).open("rb") as rows, temporary.open("wb") as output:
                        # Fixed-width SHA-256 records retain duplicate visits and selected order.
                        for key_bytes in iter(lambda: rows.read(32), b""):
                            key = key_bytes.hex()
                            match = index.execute(
                                "SELECT offset FROM tokens WHERE key = ?", (key,)
                            ).fetchone()
                            if match is None:
                                raise ValueError(
                                    "A training row was not in the validated preprocessing artifact."
                                )
                            tokens.seek(match[0])
                            output.write(tokens.readline())
                            completed += 1
                            measurement.report(completed)
                for temporary, destination in pending:
                    os.replace(temporary, destination)
                measurement.report(completed, force=True)
            finally:
                for temporary, _ in pending:
                    temporary.unlink(missing_ok=True)
