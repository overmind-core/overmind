import hashlib
import json
import os
import posixpath
import tarfile
from pathlib import Path, PurePosixPath

from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig
from daytona.common.errors import DaytonaBadRequestError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from overbae.models import Project, WorkshopWorkspace
from workshop_runtime.environment import identity


def validate_checkpoint(path, *, max_expanded_bytes=8 * 1024**3):
    if path.stat().st_size > 2 * 1024**3:
        raise CommandError("Checkpoint exceeds the 2 GiB operator transfer limit.")
    with tarfile.open(path) as archive:
        members = archive.getmembers()
    expanded_bytes = sum(member.size for member in members)
    if expanded_bytes > max_expanded_bytes:
        raise CommandError("Expanded checkpoint exceeds the restore size limit.")
    names, links = set(), set()
    base = PurePosixPath("/home/daytona/overmind")
    for member in members:
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != "overmind"
            or not (member.isdir() or member.isfile() or member.issym())
            or member.uid not in {0, 10001, 10002}
            or member.gid not in {0, 10001, 10002}
            or member.mode & 0o6000
            or path in names
        ):
            raise CommandError("Checkpoint contains an unsafe path or special file.")
        names.add(path)
        if member.issym():
            target = PurePosixPath(member.linkname)
            if not target.is_absolute():
                target = PurePosixPath("/home/daytona") / path.parent / target
            target = PurePosixPath(posixpath.normpath(str(target)))
            if not target.is_relative_to(base) or path == PurePosixPath("overmind"):
                raise CommandError("Checkpoint link escapes the retained workspace.")
            links.add(path)
    if any(parent in links for path in names for parent in path.parents):
        raise CommandError("Checkpoint writes through a symbolic link.")
    return expanded_bytes


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--project", required=True)
        parser.add_argument("--state", required=True, type=Path)
        parser.add_argument("--snapshot", required=True)

    def handle(self, *args, **options):
        project = Project.objects.get(pk=options["project"], is_active=True)
        root = Path(__file__).resolve().parents[3]
        expected = identity(root)
        state_path = options["state"]
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if state and (state.get("project") != str(project.pk) or state.get("runtime") != expected):
            raise CommandError("Receipt belongs to a different project or runtime.")

        def save():
            state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2))
            temporary.replace(state_path)

        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            old = WorkshopWorkspace.objects.select_for_update().filter(project=project).first()
            if old and not old.sandbox_id:
                raise CommandError(
                    "Initial workspace setup is managed by the native worker; reconcile its saved provisioning receipt."
                )
            if old and old.task_owner:
                raise CommandError(
                    "An active task owns the workspace. Finish or cancel it before provisioning."
                )
            if old and state.get("bound") == old.sandbox_id:
                self.stdout.write("This runtime is already bound; receipt retained.")
                return
        state.update(
            project=str(project.pk), runtime=expected, previous=old.sandbox_id if old else None
        )
        save()
        client = Daytona(
            DaytonaConfig(
                api_key=os.environ["DAYTONA_API_KEY"],
                api_url="https://app.daytona.io/api",
                otel_enabled=False,
            )
        )
        if old:
            with transaction.atomic():
                binding = WorkshopWorkspace.objects.select_for_update().get(project=project)
                if binding.task_owner or binding.sandbox_id != old.sandbox_id:
                    raise CommandError("Workspace binding changed before checkpointing.")
                binding.enabled = False
                binding.save(update_fields=["enabled"])
            previous = client.get(old.sandbox_id, request_timeout=30)
            checkpoint = state_path.with_suffix(".tar.gz")
            if not checkpoint.exists():
                result = previous.process.exec(
                    "tar -I 'gzip -1' -cf /tmp/overmind-workshop-checkpoint.tar.gz -C /home/daytona overmind",
                    timeout=60,
                )
                if result.exit_code:
                    raise CommandError("Checkpoint failed; previous workspace remains retained.")
                partial = checkpoint.with_suffix(".partial")
                with partial.open("wb") as destination:
                    for chunk in previous.fs.download_file_stream(
                        "/tmp/overmind-workshop-checkpoint.tar.gz"
                    ):
                        destination.write(chunk)
                validate_checkpoint(partial)
                partial.replace(checkpoint)
            # Release the old allocation before creating another full-size workspace.
            if previous.state not in {"stopped", "archived", "archiving"}:
                previous.stop(timeout=60)
            if previous.state not in {"archived", "archiving"}:
                previous.archive(request_timeout=30)
        name = f"overmind-workshop-{project.pk.hex[:12]}-{expected[7:19]}"
        matches = [s for s in client.list(request_timeout=30) if s.name == name]
        if len(matches) > 1:
            raise CommandError("Ambiguous provider workspace identity.")
        if matches:
            sandbox = matches[0]
            if sandbox.labels.get("project") != str(project.pk):
                raise CommandError("Provider workspace ownership does not match.")
        else:
            if state.get("create_intent") and not state.get("create_rejected"):
                raise CommandError(
                    "Creation outcome is uncertain; reconcile the saved provider name before retrying."
                )
            state.pop("create_rejected", None)
            state["create_intent"] = name
            save()
            try:
                sandbox = client.create(
                    CreateSandboxFromSnapshotParams(
                        name=name,
                        snapshot=options["snapshot"],
                        labels={"project": str(project.pk), "runtime": expected[7:]},
                        public=False,
                        auto_stop_interval=0,
                        auto_pause_interval=0,
                        auto_delete_interval=-1,
                        ephemeral=False,
                        network_block_all=True,
                    ),
                    timeout=600,
                )
            except DaytonaBadRequestError as exc:
                state["create_rejected"] = str(exc)
                save()
                raise
        state["sandbox"] = sandbox.id
        save()
        probe = sandbox.process.exec(
            "/opt/overmind-runtime/venv/bin/python -m workshop_runtime.environment", timeout=60
        )
        if probe.exit_code != 0:
            raise CommandError(
                "Runtime readiness check failed; original workspace remains retained and disabled."
            )
        report = json.loads(probe.result)
        if not report["ready"] or report["identity"] != expected:
            raise CommandError("Built runtime identity does not match this release.")
        state["readiness"] = report
        if old and old.sandbox_id != sandbox.id:
            with transaction.atomic():
                binding = WorkshopWorkspace.objects.select_for_update().get(project=project)
                if binding.task_owner or binding.sandbox_id != state["previous"]:
                    raise CommandError("Workspace binding changed during qualification.")
                binding.enabled = False
                binding.save(update_fields=["enabled"])
            checkpoint = state_path.with_suffix(".tar.gz")
            expanded_bytes = validate_checkpoint(checkpoint)
            capacity = sandbox.process.exec(
                "/opt/overmind-runtime/venv/bin/python -c 'import shutil; from pathlib import Path; "
                'p=Path("/home/daytona"); p.mkdir(parents=True, exist_ok=True); '
                "print(shutil.disk_usage(p).free)'",
                timeout=30,
            )
            if capacity.exit_code:
                raise CommandError("Could not measure workspace disk capacity before restore.")
            if int(capacity.result.strip()) < (
                expanded_bytes + checkpoint.stat().st_size + 256 * 1024**2
            ):
                raise CommandError("Insufficient workspace disk capacity to restore checkpoint.")
            with checkpoint.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            verified = sandbox.process.exec(
                "sha256sum -- /tmp/overmind-workshop-checkpoint.tar.gz",
                timeout=60,
            )
            if verified.exit_code != 0 or verified.result.split(maxsplit=1)[:1] != [checksum]:
                sandbox.fs.upload_file(str(checkpoint), "/tmp/overmind-workshop-checkpoint.tar.gz")
                verified = sandbox.process.exec(
                    "sha256sum -- /tmp/overmind-workshop-checkpoint.tar.gz",
                    timeout=60,
                )
            if verified.exit_code != 0 or verified.result.split(maxsplit=1)[:1] != [checksum]:
                raise CommandError("Checkpoint transfer checksum mismatch.")
            restored = sandbox.process.exec(
                "mkdir -p /home/daytona && tar --same-owner --same-permissions -xzf /tmp/overmind-workshop-checkpoint.tar.gz -C /home/daytona",
                timeout=60,
            )
            verification = sandbox.process.exec(
                "tar -df /tmp/overmind-workshop-checkpoint.tar.gz -C /home/daytona --ignore-command-error",
                timeout=60,
            )
            if restored.exit_code != 0 or verification.exit_code != 0:
                raise CommandError(
                    "Checkpoint restore failed; original workspace remains retained."
                )
            state["checkpoint_sha256"] = checksum
            save()
            sandbox.fs.delete_file("/tmp/overmind-workshop-checkpoint.tar.gz")
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            binding = WorkshopWorkspace.objects.select_for_update().filter(project=project).first()
            if binding and (binding.task_owner or binding.sandbox_id != state["previous"]):
                raise CommandError("Workspace binding changed during provisioning.")
            if binding:
                binding.sandbox_id = sandbox.id
                binding.enabled = True
                binding.generation += 1
                binding.save(update_fields=["sandbox_id", "enabled", "generation"])
            else:
                WorkshopWorkspace.objects.create(project=project, sandbox_id=sandbox.id)
        state["bound"] = sandbox.id
        save()
        if old and old.sandbox_id != sandbox.id:
            previous = client.get(old.sandbox_id, request_timeout=30)
            if previous.state not in {"stopped", "archived", "archiving"}:
                previous.stop(timeout=60)
        self.stdout.write(
            json.dumps(
                {
                    "sandbox": sandbox.id,
                    "runtime": expected,
                    "ready": True,
                    "previous_retained": state["previous"],
                }
            )
        )
