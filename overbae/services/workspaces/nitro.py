from django.conf import settings

from overbae.services.workspaces.server import ServerProvider


class NitroProvider(ServerProvider):
    """Workspaces on an EC2 parent that runs each project in an AWS Nitro Sandbox. The
    parent serves the workspace API; the parent's instance role must stay unreachable
    from the sandboxes."""

    def __init__(self, url: str | None = None, token: str | None = None):
        super().__init__(url or settings.NITRO_SANDBOX_URL, token or settings.NITRO_SANDBOX_TOKEN)
