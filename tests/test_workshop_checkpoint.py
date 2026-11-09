import io
import tarfile

import pytest
from django.core.management.base import CommandError

from overbae.management.commands.provision_workshop_workspace import validate_checkpoint

pytestmark = pytest.mark.covers("work-once")


def test_checkpoint_enforces_expanded_size_before_restore(tmp_path):
    path = tmp_path / "checkpoint.tar.gz"
    with tarfile.open(path, mode="w:gz") as target:
        for index in range(2):
            member = tarfile.TarInfo(f"overmind/task/{index}.txt")
            member.size = 512
            target.addfile(member, io.BytesIO(b"x" * member.size))
    assert validate_checkpoint(path, max_expanded_bytes=1024) == 1024
    with pytest.raises(CommandError):
        validate_checkpoint(path, max_expanded_bytes=1023)


def archive(path, entries):
    with tarfile.open(path, mode="w:gz") as target:
        for name, link in entries:
            member = tarfile.TarInfo(name)
            if link:
                member.type = tarfile.SYMTYPE
                member.linkname = link
            target.addfile(member)
    return path


def test_checkpoint_preserves_internal_data_links_without_permitting_escape_or_link_writes(
    tmp_path,
):
    validate_checkpoint(
        archive(
            tmp_path / "checkpoint.tar.gz",
            [
                ("overmind/task/inputs/input.parquet", None),
                ("overmind/task/work/data", "/home/daytona/overmind/task/inputs"),
            ],
        )
    )
    for entries in [
        [("overmind/task/work/data", "/etc")],
        [("overmind/task/work/data", "../../../../etc")],
        [
            ("overmind/task/work/data", "/home/daytona/overmind/task/inputs"),
            ("overmind/task/work/data/overwrite", None),
        ],
        [("../outside", None)],
    ]:
        with pytest.raises(CommandError):
            validate_checkpoint(archive(tmp_path / "checkpoint.tar.gz", entries))
