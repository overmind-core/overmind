import os

from django.core.asgi import get_asgi_application
from starlette.routing import Mount

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "overbae.settings")

django_application = get_asgi_application()

from overbae.api.mcp import mcp_application  # noqa: E402

mcp_application.router.routes.append(Mount("/", app=django_application))
application = mcp_application
