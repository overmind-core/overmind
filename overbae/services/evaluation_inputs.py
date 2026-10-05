import fcntl
import json
import tempfile
from contextlib import contextmanager
from itertools import islice
from pathlib import Path

from modal_shared.serving.artifacts import atomic_json, digest_file

CHUNK_ROWS = 128


@contextmanager
def artifact_lock(directory):
    directory.parent.mkdir(parents=True, exist_ok=True)
    with (directory.parent / (directory.name + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def index(source):
    destination = source.parent / "chunks"
    with artifact_lock(destination):
        if not destination.exists():
            manifest = json.loads((source.parent / "manifest.json").read_text())
            if digest_file(source) != manifest["files"][source.name]:
                raise ValueError("Sealed evaluation inputs changed")
            with tempfile.TemporaryDirectory(dir=source.parent) as temporary:
                work = Path(temporary) / "chunks"
                work.mkdir()
                chunks = []
                with source.open("rb") as stream:
                    while batch := list(islice(stream, CHUNK_ROWS)):
                        path = work / f"{len(chunks)}.jsonl"
                        path.write_bytes(b"".join(batch))
                        chunks.append({"rows": len(batch), "sha256": digest_file(path)})
                atomic_json(
                    work / "index.json",
                    {
                        "input_sha256": manifest["files"][source.name],
                        "chunks": chunks,
                        "rows": sum(c["rows"] for c in chunks),
                    },
                )
                work.rename(destination)
        result = json.loads((destination / "index.json").read_text())
        manifest = json.loads((source.parent / "manifest.json").read_text())
        if result["input_sha256"] != manifest["files"][source.name]:
            raise ValueError("Input chunk index differs from the sealed suite")
        return result


def read_chunk(source, position, metadata):
    path = source.parent / "chunks" / f"{position}.jsonl"
    if digest_file(path) != metadata["chunks"][position]["sha256"]:
        raise ValueError("Evaluation input chunk changed")
    result = [json.loads(line) for line in path.read_text().splitlines()]
    if len(result) != metadata["chunks"][position]["rows"]:
        raise ValueError("Evaluation input chunk coverage changed")
    return result


def read_from(source, offset, limit):
    metadata = index(source)
    if not 0 <= offset <= metadata["rows"]:
        raise ValueError("Invalid saved evaluation cursor")
    if offset == metadata["rows"]:
        return [], metadata["rows"]
    position, start = divmod(offset, CHUNK_ROWS)
    return read_chunk(source, position, metadata)[start : start + limit], metadata["rows"]


def batches(source):
    metadata = index(source)
    for position in range(len(metadata["chunks"])):
        yield read_chunk(source, position, metadata)
