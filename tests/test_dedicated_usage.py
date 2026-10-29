import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.models import BillingTelemetry, DedicatedDeployment, DeployedModel, Project, User
from overbae.services.inference_metrics import dedicated_usage


@pytest.mark.django_db
def test_dedicated_usage_is_model_scoped_and_preserves_missing_measurements():
    project = Project.objects.create(name="Usage", slug="usage")
    user = User.objects.create_user(email="usage@test.com", password="test")
    model = DeployedModel.objects.create(project=project, model_id="usage-model")
    row = DedicatedDeployment.objects.create(
        project=project,
        deployed_model=model,
        gpu_type="H100",
        rate_usd_per_gpu_hour=4,
        credits_per_gpu_hour=400,
    )
    assert dedicated_usage(model, "24h")["credits"] is None

    def charge(key, metadata):
        return BillingTelemetry.objects.create(
            user=user,
            project=project,
            service="dedicated-inference",
            amount=-2,
            idempotency_key=key,
            metadata=metadata,
        )

    charge(f"dedicated:{row.id}:one", {"minutes": 30, "replicas": 2, "gpu_type": "H100"})
    charge(f"dedicated:{uuid.uuid4()}:other", {"minutes": 30, "replicas": 2, "gpu_type": "H100"})
    old = charge(f"dedicated:{row.id}:old", {})
    BillingTelemetry.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(days=2))
    result = dedicated_usage(model, "24h")
    assert result["credits"] == 200
    assert result["gpu_hours"] == 1
    assert sum(p["credits"] for p in result["points"]) == 200
    assert dedicated_usage(model, "all")["gpu_hours"] is None
