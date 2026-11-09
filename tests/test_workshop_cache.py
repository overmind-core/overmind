import fcntl
import gzip
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from fakes.daytona import DaytonaAPI
from mcp_fixtures import mcp_context

from overbae.models import Dataset, WorkshopAction, WorkshopTask, WorkshopWorkspace
from overbae.services.workshop_agent import cache, cache_files, lifecycle, workspace
from overbae.services.workshop_agent.contracts import StartTask
from workshop_runtime import mailbox, session

pytestmark = pytest.mark.covers("work-once")


def dispatch(root, operation, **values):
    return cache_files.dispatch(
        root,
        {"operation": operation, **values},
        inspect_agent=mailbox.inspect,
        request_kernel=session.request,
    )


def test_workspace_roundtrip_restores_files_permissions_links_and_interrupted_install(tmp_path):
    root = tmp_path / "task"
    root.mkdir()
    (root / "work").mkdir()
    payload = b"data\n" * (cache_files.CHUNK_BYTES // 5 + 10)
    (root / "work/input").write_bytes(payload)
    (root / "work/input").chmod(0o444)
    (root / "work/notes.md").write_text("café: keep the original groups")
    (root / "work/empty").touch()
    (root / "script.py").write_text("print('replay')")
    (root / "work/link").symlink_to("input")
    snapshot = dispatch(root, "inventory")
    staged = tmp_path / "task.restore"
    for entry in snapshot["entries"]:
        for index, chunk in enumerate(entry.get("chunks", [])):
            compressed = tmp_path / (chunk["sha256"] + ".gz")
            dispatch(root, "export", entry=entry, index=index, temporary=str(compressed))
    dispatch(root, "evict", sha256=snapshot["sha256"])
    assert not root.exists()
    for entry in snapshot["entries"]:
        for index, chunk in enumerate(entry.get("chunks", [])):
            compressed = tmp_path / (chunk["sha256"] + ".gz")
            for _ in range(2):
                dispatch(staged, "install", entry=entry, index=index, temporary=str(compressed))
            assert not root.exists()
    dispatch(
        staged,
        "restore",
        entries=snapshot["entries"],
        destination=str(root),
        sha256=snapshot["sha256"],
    )
    assert (root / "work/input").read_bytes() == payload
    assert (root / "work/input").stat().st_mode & 0o777 == 0o444
    assert (root / "work/link").read_bytes() == payload
    assert cache_files.inventory(root) == snapshot["entries"]


def test_cleanup_refuses_active_pending_and_changed_work(tmp_path):
    root = tmp_path / "task"
    root.mkdir()
    directory = mailbox.private(root)
    with (directory / "lock").open("a") as active:
        fcntl.flock(active, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="active or unresolved"):
            dispatch(root, "inventory")
    pending = directory / f"request-{uuid4()}.json"
    pending.write_text("{}")
    with pytest.raises(ValueError, match="active or unresolved"):
        dispatch(root, "inventory")
    pending.unlink()
    snapshot = dispatch(root, "inventory")
    (root / "new-work").write_text("not retained yet")
    with pytest.raises(ValueError, match="changed after retention"):
        dispatch(root, "evict", sha256=snapshot["sha256"])
    assert (root / "new-work").read_text() == "not retained yet"


@pytest.mark.parametrize("damage", ["checksum", "oversize", "truncated"])
def test_restore_rejects_damaged_chunks_without_installing_them(tmp_path, damage):
    root = tmp_path / "source"
    root.mkdir()
    (root / "file").write_bytes(b"expected")
    entry = cache_files.inventory(root)[0]
    blob = tmp_path / "chunk.gz"
    cache_files.export_chunk(root, entry, 0, blob)
    if damage == "truncated":
        blob.write_bytes(blob.read_bytes()[:10])
    else:
        blob.write_bytes(gzip.compress(b"different" if damage == "checksum" else b"x" * 100))
    stage = tmp_path / "stage"
    with pytest.raises((ValueError, EOFError)):
        dispatch(stage, "install", entry=entry, index=0, temporary=str(blob))
    assert not (stage / "file").exists()


def test_retention_rejects_external_links_and_special_files(tmp_path):
    (tmp_path / "escape").symlink_to("../outside")
    with pytest.raises(ValueError, match="escapes"):
        cache_files.inventory(tmp_path)
    (tmp_path / "escape").unlink()
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(ValueError, match="special file"):
        cache_files.inventory(tmp_path)


@pytest.mark.django_db
def test_platform_retention_eviction_recovery_and_corrupt_archive_protection(
    monkeypatch, scripted, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path / "platform"
    settings.WORKSHOP_CACHE_BYTES = 0
    context = mcp_context(["read", "write"])
    binding = WorkshopWorkspace.objects.create(project=context.project, sandbox_id=str(uuid4()))
    dataset = Dataset.objects.create(project=context.project, name="Source", state="ready")
    task = lifecycle.start(
        context, StartTask(dataset=dataset.pk, objective="inspect", request_key="cache")
    )
    action, _ = lifecycle.claim(task.pk, 0, "prepare_workshop_environment", {})
    lifecycle.record_action(action.pk, "completed", {})
    task.refresh_from_db()
    task.state = "completed"
    task.save(update_fields=["state", "updated_at"])
    vendor = DaytonaAPI(binding.sandbox_id, {})
    vendor.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    root = workspace.task_root(task)
    vendor.files[root + "/work/input.jsonl"] = b'{"value":1}\n' * 120000
    vendor.files[root + "/work/script.py"] = b"print('retained')"
    sandbox = workspace.attach(task, reconcile=True)
    receipt = cache.retain(task, sandbox)
    assert receipt["manifest"]
    task.refresh_from_db()
    before = cache.verify_archive(task)
    original = {
        name: content for name, content in vendor.files.items() if name.startswith(root + "/")
    }
    cache.sweep(task.project_id)
    task.refresh_from_db()
    assert not task.retention["resident"]
    assert not any(name.startswith(root + "/") for name in vendor.files)
    assert cache.restore(task)
    assert all(vendor.files[name] == content for name, content in original.items())
    task.refresh_from_db()
    uploads = len(vendor.uploads)
    assert not cache.restore(task)
    assert len(vendor.uploads) == uploads
    chunk = next(entry["chunks"][0] for entry in before if entry.get("chunks"))
    key = cache.object_key(task, chunk["sha256"])
    default_storage.delete(key)
    default_storage.save(key, ContentFile(b"corrupted"))
    healthy = WorkshopTask.objects.create(
        project=context.project,
        dataset=dataset,
        created_by=context.user,
        request_key="healthy-cache",
        state="completed",
        objective="inspect",
    )
    WorkshopAction.objects.create(
        task=healthy, sequence=0, name="prepare_workshop_environment", state="completed"
    )
    healthy_root = workspace.task_root(healthy)
    vendor.files[healthy_root + "/work/data"] = b"healthy retained data"
    cache.sweep(task.project_id)
    healthy.refresh_from_db()
    assert healthy.retention["resident"] is False
    assert not any(name.startswith(healthy_root + "/") for name in vendor.files)
    assert all(vendor.files[name] == content for name, content in original.items())


@pytest.mark.django_db
@pytest.mark.parametrize("pressure", [False, True])
def test_lru_budget_keeps_recent_data_and_pins_active_or_uncertain_work(
    monkeypatch, scripted, settings, pressure
):
    context = mcp_context(["read", "write"])
    binding = WorkshopWorkspace.objects.create(project=context.project, sandbox_id=str(uuid4()))
    dataset = Dataset.objects.create(project=context.project, name="Cache", state="ready")
    vendor = DaytonaAPI(binding.sandbox_id, {})
    vendor.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    tasks = []
    for name, state in [("old", "completed"), ("recent", "completed"), ("active", "running")]:
        task = WorkshopTask.objects.create(
            project=context.project,
            dataset=dataset,
            created_by=context.user,
            request_key=name,
            state=state,
            objective="inspect",
        )
        WorkshopAction.objects.create(
            task=task, sequence=0, name="prepare_workshop_environment", state="completed"
        )
        vendor.files[workspace.task_root(task) + "/work/data"] = name.encode() * 100
        tasks.append(task)
    settings.WORKSHOP_CACHE_BYTES = 2 * 1024**3 if pressure else 601
    vendor.capacity_bytes = 1200 if pressure else None
    cache.sweep(context.project.pk)
    old, recent, active = tasks
    assert not any(name.startswith(workspace.task_root(old) + "/") for name in vendor.files)
    if pressure:
        assert workspace.task_root(recent) + "/work/data" not in vendor.files
        assert vendor.files[workspace.task_root(active) + "/work/data"] == b"active" * 100
        return
    assert vendor.files[workspace.task_root(recent) + "/work/data"] == b"recent" * 100
    assert vendor.files[workspace.task_root(active) + "/work/data"] == b"active" * 100
    settings.WORKSHOP_CACHE_BYTES = 0
    binding.task_owner = active.pk
    binding.save(update_fields=["task_owner"])
    cache.sweep(context.project.pk)
    assert workspace.task_root(recent) + "/work/data" in vendor.files
    binding.task_owner = None
    binding.save(update_fields=["task_owner"])
    WorkshopAction.objects.create(task=recent, sequence=1, name="publish_workshop", state="unknown")
    cache.sweep(context.project.pk)
    assert workspace.task_root(recent) + "/work/data" in vendor.files


@pytest.mark.skipif(
    not Path("/proc").is_dir(), reason="Daytona process checks require Linux procfs"
)
def test_cleanup_pins_files_held_by_an_orphan_process(tmp_path):
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready',flush=True); sys.stdin.read()"],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(ValueError, match="process still holds"):
            dispatch(tmp_path, "inventory")
    finally:
        child.communicate("")


@pytest.mark.skipif(os.geteuid() == 0, reason="Root bypasses the deletion permission fault")
def test_interrupted_cleanup_never_exposes_a_partial_workspace(tmp_path):
    root = tmp_path / "task"
    root.mkdir()
    locked = root / "locked"
    locked.mkdir()
    (locked / "data").write_text("retain before deleting")
    locked.chmod(0o500)
    snapshot = dispatch(root, "inventory")
    try:
        with pytest.raises(PermissionError):
            dispatch(root, "evict", sha256=snapshot["sha256"])
        assert not dispatch(root, "exists")["exists"]
        assert (tmp_path / "task.evicted/locked/data").read_text() == "retain before deleting"
    finally:
        (tmp_path / "task.evicted/locked").chmod(0o700)
    dispatch(root, "evict", sha256=snapshot["sha256"])
    assert not (tmp_path / "task.evicted").exists()


@pytest.mark.django_db
def test_disk_pressure_keeps_recently_accessed_older_task(monkeypatch, scripted, settings):
    context = mcp_context(["read", "write"])
    binding = WorkshopWorkspace.objects.create(project=context.project, sandbox_id=str(uuid4()))
    dataset = Dataset.objects.create(project=context.project, name="Cache", state="ready")
    vendor = DaytonaAPI(binding.sandbox_id, {})
    vendor.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    settings.WORKSHOP_CACHE_BYTES = 2 * 1024**3
    tasks = []
    for name in ["older", "newer"]:
        task = WorkshopTask.objects.create(
            project=context.project,
            dataset=dataset,
            created_by=context.user,
            request_key=name,
            state="completed",
            objective="inspect",
        )
        WorkshopAction.objects.create(
            task=task, sequence=0, name="prepare_workshop_environment", state="completed"
        )
        vendor.files[workspace.task_root(task) + "/work/data"] = name.encode() * 100
        tasks.append(task)
    cache.sweep(context.project.pk)
    older, newer = tasks
    older.refresh_from_db()
    cache.restore(older)
    vendor.capacity_bytes = 1100
    cache.sweep(context.project.pk)
    assert vendor.files[workspace.task_root(older) + "/work/data"] == b"older" * 100
    assert not any(name.startswith(workspace.task_root(newer) + "/") for name in vendor.files)
