from uuid import UUID

from celery import shared_task


@shared_task(name="overbae.tasks.assistant.run_turn")
def run_turn(thread_id):
    # Lazy: the turn reaches the MCP catalog, which imports the serializers that import tasks.
    from overbae.services.assistant import turn

    turn.run(thread_id)


@shared_task(name="overbae.tasks.assistant.stop_kernel")
def stop_kernel(project_id, thread_id, turn):
    """End the kernel of the turn that was stopped, also during a cell: that turn gets its
    answer and sees the stop. A later turn's kernel is left alone."""
    from overbae.models import AssistantThread
    from overbae.services import workspaces

    if AssistantThread.objects.filter(pk=thread_id, turn=turn, state="running").exists():
        workspaces.provider().stop(project_id, UUID(thread_id).hex)


@shared_task(
    name="overbae.tasks.assistant.forget_workspace_data",
    autoretry_for=(Exception,),
    retry_backoff=60,
    max_retries=10,
)
def forget_workspace_data(project_id, data, conversations):
    """A deleted dataset's copies leave its project's workspace, also at a hosted provider."""
    from overbae.services import workspaces

    workspaces.provider().forget(project_id, data, conversations)


@shared_task(
    name="overbae.tasks.assistant.remove_workspace",
    autoretry_for=(Exception,),
    retry_backoff=60,
    max_retries=10,
)
def remove_workspace(project_id):
    """A deleted project's workspace goes with it, also at a hosted provider."""
    from overbae.services import workspaces

    workspaces.provider().remove(project_id)
