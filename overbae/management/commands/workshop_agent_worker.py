import asyncio
import os
from contextlib import suppress

from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import ActivityInboundInterceptor, Interceptor, Worker

from overbae.models import WorkshopTask
from overbae.services.workshop_agent.cache import maintain_forever
from overbae.services.workshop_agent.resident import fence_run, supervise_run
from workshop_runtime.workflow import WorkshopWorkflow


class DatabaseActivity(ActivityInboundInterceptor):
    async def execute_activity(self, input):
        async with ThreadSensitiveContext():
            try:
                return await self.next.execute_activity(input)
            finally:
                await sync_to_async(connections.close_all, thread_sensitive=True)()


class DatabaseInterceptor(Interceptor):
    def intercept_activity(self, next):
        return DatabaseActivity(next)


class Command(BaseCommand):
    def handle(self, *args, **options):
        for name in ("DAYTONA_API_KEY",):
            if not os.environ.get(name):
                raise CommandError(f"{name} must be configured.")
        asyncio.run(self.serve())

    async def serve(self):
        client = await Client.connect(
            os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
            namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        )
        async with Worker(
            client,
            task_queue="overmind-workshop",
            workflows=[WorkshopWorkflow],
            activities=[supervise_run, fence_run],
            max_concurrent_activities=4,
            interceptors=[DatabaseInterceptor()],
        ):
            maintenance = asyncio.create_task(maintain_forever())
            try:
                while True:
                    tasks = await sync_to_async(list, thread_sensitive=True)(
                        WorkshopTask.objects.filter(dispatched_at=None)
                        .exclude(state__in=["completed", "cancelled", "failed"])
                        .values_list("pk", flat=True)[:100]
                    )
                    for task_id in tasks:
                        with suppress(WorkflowAlreadyStartedError):
                            await client.start_workflow(
                                WorkshopWorkflow.run,
                                {"task": str(task_id)},
                                id=f"workshop:{task_id}",
                                task_queue="overmind-workshop",
                                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                            )
                        await sync_to_async(
                            WorkshopTask.objects.filter(pk=task_id).update, thread_sensitive=True
                        )(dispatched_at=timezone.now())
                    await asyncio.sleep(2)
            finally:
                maintenance.cancel()
                with suppress(asyncio.CancelledError):
                    await maintenance
