from celery import shared_task


@shared_task(name="overbae.tasks.assistant.run_turn")
def run_turn(thread_id):
    # Lazy: the turn reaches the MCP catalog, which imports the serializers that import tasks.
    from overbae.services.assistant import turn

    turn.run(thread_id)


@shared_task(name="overbae.tasks.assistant.stop_kernel")
def stop_kernel(project_id, conversation):
    """End a conversation's kernel, also during a cell: the turn that waits for the cell
    gets its answer and sees the stop."""
    from overbae.services import workspaces

    workspaces.provider().stop(project_id, conversation)


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
