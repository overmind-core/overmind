import tempfile
from unittest.mock import patch

from django.db import transaction
from django.test import override_settings

from overbae.models import BillingTelemetry, Dataset, Project
from overbae.models.chatgpt import ChatGPTAccount, WorkshopPreference
from overbae.services.datasets import land
from overbae.services.datasets.notebook import agent

account = ChatGPTAccount.objects.exclude(credentials="").select_related("user").get()
with (
    tempfile.TemporaryDirectory(prefix="workshop-readiness-") as media,
    override_settings(MEDIA_ROOT=media),
    transaction.atomic(),
    patch("overbae.services.datasets.notebook.agent.close_old_connections"),
):
    WorkshopPreference.objects.update_or_create(
        user=account.user,
        defaults={"funding_source": "chatgpt", "account": account, "model": "gpt-5.6-luna"},
    )
    project = Project.objects.create(name="Workshop readiness", slug="workshop-readiness-smoke")
    dataset = Dataset.objects.create(project=project, name="Smoke fixture", intent="explore")
    land.land_rows(dataset, [{"evidence": "Paris is in France", "answer": "France"}])
    list(
        agent.iter_turn(
            dataset.id,
            "Rename this dataset to Workshop readiness verified. Do not change any rows.",
            display="Local synthetic smoke test",
            user=account.user,
        )
    )
    dataset.refresh_from_db()
    turn = dataset.chat[-1]
    assert not turn["error"], turn
    assert dataset.name == "Workshop readiness verified", dataset.name
    assert turn["text"], turn
    assert turn["funding_source"] == "chatgpt", turn
    entries = BillingTelemetry.objects.filter(user=account.user, project=project)
    assert entries.exists()
    assert all(entry.amount == 0 for entry in entries)
    print("PASS: live ChatGPT -> workshop tool execution -> saved answer -> zero-charge ledger")
    print("Answer:", turn["text"])
    transaction.set_rollback(True)
print("PASS: synthetic dataset/project/ledger rolled back; temporary files removed")
