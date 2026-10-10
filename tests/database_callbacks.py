from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections


def database_callback(callback):
    """Model a concurrent actor from a fake async HTTP response callback.

    Callers require transactional tests with committed fixtures. The separate
    connection must not borrow the async client's event loop or test transaction.
    """

    def invoke():
        close_old_connections()
        try:
            return callback()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(invoke).result(timeout=5)
