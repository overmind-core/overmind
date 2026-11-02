import asyncio
import hashlib
import json
import time

from asgiref.sync import sync_to_async
from django.utils import timezone

from overbae.services.mcp import mutations
from overbae.services.mcp.contracts.monitoring import (
    InspectJobsInput,
    InspectJobsOutput,
    InspectMutationInput,
    InspectMutationOutput,
    JobObservation,
)
from overbae.services.mcp.contracts.observability import GetJobInput
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.tools_observability import get_job_sync


def observations(payload, context):
    rows = []
    for reference in payload.jobs:
        try:
            rows.append(
                JobObservation(
                    kind=reference.kind,
                    id=reference.id,
                    job=get_job_sync(
                        GetJobInput(kind=reference.kind, id=reference.id, view=payload.view),
                        context,
                    ),
                )
            )
        except MCPError as error:
            rows.append(JobObservation(kind=reference.kind, id=reference.id, error=error.data))
    return rows


async def inspect_jobs(payload, context):
    deadline = time.monotonic() + payload.timeout_seconds
    while True:
        rows = await sync_to_async(observations, thread_sensitive=True)(payload, context)
        encoded = json.dumps(
            [row.model_dump(mode="json") for row in rows], sort_keys=True, separators=(",", ":")
        )
        cursor = hashlib.sha256(encoded.encode()).hexdigest()
        changed = cursor != payload.after
        remaining = deadline - time.monotonic()
        if changed or remaining <= 0:
            return InspectJobsOutput(
                jobs=rows, cursor=cursor, changed=changed, observed_at=timezone.now()
            )
        await asyncio.sleep(min(5, remaining))


async def inspect_mutation(payload, context):
    return InspectMutationOutput(
        **await sync_to_async(mutations.inspect, thread_sensitive=True)(
            context, payload.request_key
        )
    )


def register_monitoring_tools(catalog):
    from overbae.services.mcp.catalog import ToolDefinition

    catalog.register(
        ToolDefinition(
            name="inspect_mutation",
            title="Inspect mutation receipt",
            description="Recover this account's generic project mutation receipt by request_key. Native request-key workflows use list_objects/get_job. Pending/unknown never authorizes replay.",
            input_model=InspectMutationInput,
            output_model=InspectMutationOutput,
            read_only=True,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
        ),
        inspect_mutation,
    )
    catalog.register(
        ToolDefinition(
            name="inspect_jobs",
            title="Inspect or wait for jobs",
            description="Inspect up to eight project jobs; optionally wait up to 30 seconds for a cursor change. Per-job errors remain explicit. Passive: never retries, resumes or starts provider work.",
            input_model=InspectJobsInput,
            output_model=InspectJobsOutput,
            read_only=True,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
        ),
        inspect_jobs,
    )
