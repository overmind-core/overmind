from celery import shared_task


@shared_task(name="overbae.tasks.assistant.advance")
def advance(thread_id):
    # Lazy: the loop reaches the MCP catalog, which imports the serializers that import tasks.
    from overbae.services.assistant import loop

    if loop.advance(thread_id):
        advance.delay(thread_id)


@shared_task(name="overbae.tasks.assistant.reconcile")
def reconcile():
    from overbae.services.assistant import loop  # lazy: same cycle as above

    for thread_id in loop.due():
        advance.delay(thread_id)
