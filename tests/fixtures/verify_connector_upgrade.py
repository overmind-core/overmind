"""Replay the connector migration against assigned and native spans in memory."""

import os

import django
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


def main():
    os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"
    django.setup()
    assert connection.settings_dict["NAME"] == ":memory:"
    executor = MigrationExecutor(connection)
    old_target = [("overbae", "0008_model_activation")]
    executor.migrate(old_target)
    old = executor.loader.project_state(old_target).apps
    project = old.get_model("overbae", "Project").objects.create(name="Upgrade", slug="upgrade")
    capability = old.get_model("overbae", "Capability").objects.create(
        project_id=project.id, name="Support", slug="support"
    )
    credential = old.get_model("overbae", "ConnectorCredential").objects.create(
        project_id=project.id, name="Fixture", connector_type="langfuse"
    )
    Span = old.get_model("overbae", "Span")
    for number in range(100):
        Span.objects.create(
            span_id=f"{number:016x}",
            trace_id=f"{number:032x}",
            project_id=project.id,
            capability_id=capability.id,
            name="Support",
            span_type="entry_point",
            resource_attrs={
                "connector.credential_id": str(credential.id),
                "connector.source": "langfuse",
            },
            attributes={"overmind.capability.id": str(capability.id)},
        )
    Span.objects.create(span_id="f" * 16, trace_id="f" * 32, project_id=project.id, name="Native")
    executor = MigrationExecutor(connection)
    new_target = [("overbae", "0010_connectorcredential_sync_lease_expires_at_and_more")]
    executor.migrate(new_target)
    current = executor.loader.project_state(new_target).apps
    Span = current.get_model("overbae", "Span")
    assert Span.objects.count() == 101
    assert (
        Span.objects.filter(
            capability_id=capability.id, connector_group__isnull=False, connector_reviewed=False
        ).count()
        == 100
    )
    assert Span.objects.get(span_id="f" * 16).connector_group_id is None
    assert current.get_model("overbae", "ConnectorTraceGroup").objects.count() == 1
    assert current.get_model("overbae", "Capability").objects.count() == 1
    print(
        "Upgrade verified: 100 assigned imported traces grouped without loss; native trace unchanged."
    )


if __name__ == "__main__":
    main()
