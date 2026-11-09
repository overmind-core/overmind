from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class WorkshopWorkflow:
    @workflow.run
    async def run(self, request: dict) -> dict:
        while True:
            try:
                result = await workflow.execute_activity(
                    "supervise_run",
                    request["task"],
                    start_to_close_timeout=timedelta(days=1),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=5), maximum_attempts=3
                    ),
                )
            except ActivityError:
                result = await workflow.execute_activity(
                    "fence_run", request["task"], start_to_close_timeout=timedelta(seconds=30)
                )
            if result["state"] in {"completed", "cancelled", "failed"}:
                return result
            await workflow.sleep(5)
            if workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(request)
