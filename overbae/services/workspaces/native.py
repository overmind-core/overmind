from django.conf import settings

from overbae.services.workspaces.server import ServerProvider


class NativeProvider(ServerProvider):
    """Workspaces in Overmind's own ``sandbox`` service: folders on its volume, kernels as
    confined processes. It holds them in one server's memory and disk, so a deployment
    runs one sandbox server."""

    def __init__(self, url: str | None = None, token: str | None = None):
        super().__init__(url or settings.SANDBOX_URL, token or settings.SANDBOX_TOKEN)
