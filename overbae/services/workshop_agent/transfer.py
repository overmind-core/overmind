import gzip
import hashlib
import json
import shlex
import shutil
import tempfile
import time
from contextlib import suppress
from pathlib import Path

INSTALL_SCRIPT = """import gzip, hashlib, json, os, pathlib, sys, tempfile
request = json.loads(sys.argv[1])
target = pathlib.Path(request['destination'])
target.parent.mkdir(parents=True, exist_ok=True)
expected = request['sha256']
size = request['bytes']
if request['operation'] == 'probe':
    checksum = None
    if not target.is_symlink() and target.is_file() and target.stat().st_size == size:
        with target.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if checksum == expected:
            target.chmod(0o444)
    print(json.dumps({'ready': checksum == expected, 'sha256': checksum, 'bytes': size}))
else:
    descriptor, temporary = tempfile.mkstemp(prefix='.overmind-install-', dir=target.parent)
    try:
        count = 0
        digest = hashlib.sha256()
        with os.fdopen(descriptor, 'wb') as output, gzip.open(request['archive'], 'rb') as source:
            while block := source.read(1024 * 1024):
                count += len(block)
                if count > size:
                    raise ValueError('Expanded source exceeds its pinned size')
                digest.update(block)
                output.write(block)
            if count != size or digest.hexdigest() != expected:
                raise ValueError('Workspace transfer checksum mismatch')
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o444)
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        print(json.dumps({'ready': True, 'sha256': digest.hexdigest(), 'bytes': count}))
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
"""


def copy_verified(sandbox, source, destination):
    started = time.monotonic()
    size = source.stat().st_size
    if size > 2 * 1024**3:
        raise ValueError("File exceeds the 2 GiB transfer limit.")
    with source.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    archive = destination + f".upload-{checksum}.gz"
    request = {"destination": destination, "bytes": size, "sha256": checksum}

    def execute(operation):
        response = sandbox.process.exec(
            "/opt/overmind-runtime/venv/bin/python -c "
            + shlex.quote(INSTALL_SCRIPT)
            + " "
            + shlex.quote(json.dumps({**request, "operation": operation, "archive": archive})),
            timeout=120,
        )
        if response.exit_code:
            raise ValueError(f"Workspace source verification failed: {response.result[-2000:]}")
        return json.loads(response.result)

    metrics = {"uploaded_bytes": 0, "compression_seconds": 0.0, "upload_seconds": 0.0}
    try:
        probe = time.monotonic()
        receipt = execute("probe")
        metrics["probe_seconds"] = time.monotonic() - probe
        metrics["reused"] = receipt["ready"]
        if not receipt["ready"]:
            with tempfile.TemporaryDirectory() as directory:
                packed = Path(directory) / "source.gz"
                compression = time.monotonic()
                with (
                    source.open("rb") as incoming,
                    packed.open("wb") as output,
                    gzip.GzipFile(
                        filename="", fileobj=output, mode="wb", compresslevel=1, mtime=0
                    ) as zipped,
                ):
                    shutil.copyfileobj(incoming, zipped)
                metrics["compression_seconds"] = time.monotonic() - compression
                metrics["uploaded_bytes"] = packed.stat().st_size
                uploading = time.monotonic()
                sandbox.fs.upload_file(str(packed), archive)
                metrics["upload_seconds"] = time.monotonic() - uploading
            verifying = time.monotonic()
            receipt = execute("install")
            metrics["install_seconds"] = time.monotonic() - verifying
        if not receipt["ready"] or receipt["sha256"] != checksum or receipt["bytes"] != size:
            raise ValueError("Workspace transfer checksum mismatch.")
    finally:
        with suppress(Exception):
            sandbox.fs.delete_file(archive)
    metrics["seconds"] = time.monotonic() - started
    return {"bytes": size, "sha256": checksum, "transfer": metrics}
