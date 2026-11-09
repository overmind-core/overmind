import fcntl
import gzip
import hashlib
import json
import os
import shutil
import signal
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath

CHUNK_BYTES = 32 * 1024**2


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative_path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or not path.parts:
        raise ValueError("Invalid retained path")
    return path


def inventory(root, *, link_root=None):
    root = Path(root)
    link_root = Path(link_root) if link_root else root
    if root.is_symlink():
        raise ValueError("Task root cannot be a link")
    entries = []
    for path in sorted(root.rglob("*")):
        name = str(path.relative_to(root))
        metadata = path.lstat()
        entry = {
            "path": name,
            "mode": stat.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
        }
        if entry["mode"] & 0o6000:
            raise ValueError("Privileged workspace file")
        if path.is_symlink():
            target = os.readlink(path)
            resolved = Path(os.path.normpath(str(link_root / Path(name).parent / target)))
            if not resolved.is_relative_to(link_root):
                raise ValueError("Workspace link escapes task")
            entry.update(kind="link", target=target)
        elif path.is_dir():
            entry["kind"] = "directory"
        elif path.is_file():
            chunks = []
            with path.open("rb") as stream:
                while block := stream.read(CHUNK_BYTES):
                    chunks.append(
                        {"sha256": hashlib.sha256(block).hexdigest(), "bytes": len(block)}
                    )
            entry.update(kind="file", bytes=metadata.st_size, chunks=chunks)
            after = path.stat()
            if (metadata.st_size, metadata.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Workspace changed during inventory")
        else:
            raise ValueError("Workspace has an active socket or special file")
        entries.append(entry)
    return entries


def validate(entries, root):
    names, links = set(), set()
    for entry in entries:
        path = relative_path(entry["path"])
        if path in names or entry["mode"] & 0o6000:
            raise ValueError("Duplicate or privileged retained path")
        names.add(path)
        if entry["kind"] == "link":
            target = Path(os.path.normpath(str(Path(root) / path.parent / entry["target"])))
            if not target.is_relative_to(root):
                raise ValueError("Retained link escapes task")
            links.add(path)
        elif entry["kind"] == "file":
            if sum(c["bytes"] for c in entry["chunks"]) != entry["bytes"]:
                raise ValueError("Retained chunk sizes disagree")
            for chunk in entry["chunks"]:
                if not 0 < chunk["bytes"] <= CHUNK_BYTES or len(chunk["sha256"]) != 64:
                    raise ValueError("Invalid retained chunk")
                int(chunk["sha256"], 16)
            if any(c["bytes"] != CHUNK_BYTES for c in entry["chunks"][:-1]):
                raise ValueError("Incomplete retained chunk")
        elif entry["kind"] != "directory":
            raise ValueError("Invalid retained entry")
    if any(parent in links for path in names for parent in path.parents):
        raise ValueError("Retained path writes through a link")


def chunk_bytes(root, entry, index):
    path = Path(root) / relative_path(entry["path"])
    if path.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Source chunk escapes task")
    chunk = entry["chunks"][index]
    with path.open("rb") as stream:
        stream.seek(index * CHUNK_BYTES)
        block = stream.read(chunk["bytes"])
    if hashlib.sha256(block).hexdigest() != chunk["sha256"]:
        raise ValueError("Source changed after inventory")
    return block


def export_chunk(root, entry, index, destination):
    with gzip.GzipFile(filename=str(destination), mode="wb", compresslevel=1, mtime=0) as stream:
        stream.write(chunk_bytes(root, entry, index))
    return {"sha256": digest(destination), "bytes": Path(destination).stat().st_size}


def install_chunk(root, entry, index, source):
    path = Path(root) / relative_path(entry["path"])
    if path.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Destination chunk escapes task")
    chunk = entry["chunks"][index]
    with gzip.open(source, "rb") as stream:
        block = stream.read(chunk["bytes"] + 1)
    if len(block) != chunk["bytes"] or hashlib.sha256(block).hexdigest() != chunk["sha256"]:
        raise ValueError("Retained chunk failed verification")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("r+b" if path.exists() else "w+b") as stream:
        stream.seek(index * CHUNK_BYTES)
        stream.write(block)
        if index == len(entry["chunks"]) - 1:
            stream.truncate(entry["bytes"])
        stream.flush()
        os.fsync(stream.fileno())


def fingerprint(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def settle(root, inspect_agent, request_kernel):
    snapshot = inspect_agent(root)
    if snapshot["alive"] or snapshot["pending"]:
        raise ValueError("Task still has active or unresolved work")
    socket = root / ".session/socket"
    if socket.exists():
        try:
            health = request_kernel(root, {"operation": "health"})
        except OSError:
            with (root / ".session/server.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            if health.get("active"):
                raise ValueError("Kernel execution has not settled")
            request_kernel(root, {"operation": "close"})
            pid = health["supervisor_pid"]
            command = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
            if b"workshop_runtime.session" not in command or str(root).encode() not in command:
                raise ValueError("Kernel supervisor identity changed")
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while (Path("/proc") / str(pid)).exists():
                if "\nState:\tZ" in (Path("/proc") / str(pid) / "status").read_text():
                    break
                if time.monotonic() > deadline:
                    raise ValueError("Kernel supervisor did not exit")
                time.sleep(0.05)
        socket.unlink(missing_ok=True)
    # A kernel can leave children behind after its own exit. Their files remain pinned.
    proc = Path("/proc")
    if proc.is_dir():
        for process in proc.iterdir():
            if not process.name.isdigit() or int(process.name) == os.getpid():
                continue
            try:
                handles = [process / "cwd", *list((process / "fd").iterdir())]
            except FileNotFoundError:
                continue
            for handle in handles:
                try:
                    target = Path(os.readlink(handle))
                except FileNotFoundError:
                    continue
                if target.is_relative_to(root):
                    raise ValueError("A workspace process still holds task files")


def dispatch(root, request, *, inspect_agent=None, request_kernel=None):
    root = Path(root)
    operation = request["operation"]
    if root.is_symlink():
        raise ValueError("Task root cannot be a link")
    if operation == "exists":
        return {"exists": root.is_dir()}
    if operation == "catalogue":
        parent = root.parent
        usage = shutil.disk_usage(parent if parent.exists() else parent.parent)
        return {
            "roots": [p.name for p in parent.iterdir() if p.is_dir() and not p.is_symlink()]
            if parent.exists()
            else [],
            "free": usage.free,
            "reserve": min(1024**3, usage.total // 10),
        }
    if operation == "prepare_restore":
        root.mkdir(parents=True, exist_ok=True)
        existing = sum(
            p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink()
        )
        if shutil.disk_usage(root).free < max(0, request["bytes"] - existing) + 2 * CHUNK_BYTES:
            raise ValueError("Insufficient workspace capacity for verified recovery")
        return {"ready": True}
    if operation == "inventory":
        if not root.is_dir():
            return {"exists": False}
        settle(root, inspect_agent, request_kernel)
        entries = inventory(root)
        validate(entries, root)
        return {"exists": True, "entries": entries, "sha256": fingerprint(entries)}
    if operation == "export":
        return export_chunk(root, request["entry"], request["index"], request["temporary"])
    if operation == "export_archive":
        with zipfile.ZipFile(request["temporary"], "w") as archive:
            for index, item in enumerate(request["items"]):
                block = chunk_bytes(root, item["entry"], item["index"])
                archive.writestr(str(index), gzip.compress(block, compresslevel=1, mtime=0))
        return {"sha256": digest(request["temporary"])}
    if operation == "install":
        install_chunk(root, request["entry"], request["index"], request["temporary"])
        return {"installed": True}
    if operation == "install_archive":
        try:
            with zipfile.ZipFile(request["temporary"]) as archive:
                if archive.namelist() != [str(i) for i in range(len(request["items"]))]:
                    raise ValueError("Recovery archive members differ")
                for index, item in enumerate(request["items"]):
                    with archive.open(str(index)) as compressed:
                        install_chunk(root, item["entry"], item["index"], compressed)
        finally:
            Path(request["temporary"]).unlink(missing_ok=True)
        return {"installed": True}
    if operation == "evict":
        trash = root.with_name(root.name + ".evicted")
        if trash.is_symlink():
            raise ValueError("Eviction directory cannot be a link")
        if root.exists():
            settle(root, inspect_agent, request_kernel)
            if fingerprint(inventory(root)) != request["sha256"]:
                raise ValueError("Task changed after retention; refusing eviction")
            if trash.exists():
                shutil.rmtree(trash)
            # A failed deletion must never leave a partial directory visible to recovery.
            root.rename(trash)
        if trash.exists():
            shutil.rmtree(trash)
        return {"evicted": True}
    if operation == "restore":
        entries = request["entries"]
        validate(entries, request["destination"])
        for entry in entries:
            path = root / entry["path"]
            if entry["kind"] == "directory":
                path.mkdir(parents=True, exist_ok=True)
            elif entry["kind"] == "file":
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists() and not entry["chunks"]:
                    path.touch()
        for entry in entries:
            if entry["kind"] == "link":
                path = root / entry["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.is_symlink():
                    path.symlink_to(entry["target"])
        for entry in sorted(entries, key=lambda item: len(Path(item["path"]).parts), reverse=True):
            path = root / entry["path"]
            os.chown(path, entry["uid"], entry["gid"], follow_symlinks=False)
            if entry["kind"] != "link":
                path.chmod(entry["mode"])
        # Absolute internal links refer to the final path, not the staging directory.
        actual = inventory(root, link_root=request["destination"])
        if fingerprint(actual) != request["sha256"]:
            raise ValueError("Restored workspace does not match retained inventory")
        destination = Path(request["destination"])
        if destination.exists():
            raise ValueError("Refusing to overwrite an existing workspace")
        root.rename(destination)
        return {"restored": True}
    raise ValueError("Unknown cache operation")
