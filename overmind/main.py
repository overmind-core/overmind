"""
overmind standalone entry point.

Self-hosted single-user instance. On first startup auto-provisions a
default admin user, project, and API token.

The built frontend (React SPA) is served from /frontend_dist when present,
so the entire application is accessible on a single port.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from overmind.core.invocation_helpers import ClientCacheManager
from fastapi.middleware.cors import CORSMiddleware
from overmind.config import settings
from overmind.api.v1.router import core_api_router, core_auth_router
from overmind.api.v1.helpers.policy_interface import NoopOrgPolicyProvider
from overmind.api.v1.helpers.authentication import RBACAuthenticationProvider
from overmind.api.v1.helpers.auth_interface import NoopAuthorizationProvider
from overmind.db.session import get_session_local
from overmind.celery_app import get_celery_app
from overmind.bootstrap import ensure_default_user
from logging import getLogger, Filter
import logging

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend_dist"

logger = getLogger(__name__)
logger.setLevel(logging.INFO)


class HealthCheckFilter(Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find("/health") == -1


logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())


app = FastAPI(title=settings.app_name, debug=settings.debug, redirect_slashes=False)


@app.on_event("startup")
async def startup_event():
    try:
        logger.info("--- Starting overmind worker startup ---")

        app.state.client_manager = ClientCacheManager(maxsize=10)

        # Core uses basic auth (no RBAC permission checking)
        app.state.authentication_provider = RBACAuthenticationProvider()
        app.state.authorization_provider = NoopAuthorizationProvider()
        app.state.org_policy_provider = NoopOrgPolicyProvider()

        app.state.celery_app = get_celery_app()

        # Auto-provision default user on first startup
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as db:
            try:
                await ensure_default_user(db)
            except Exception as e:
                logger.error(f"Warning: Error during bootstrap: {e}")
            finally:
                await db.close()

        logger.info("--- overmind startup completed ---")
    except Exception as e:
        logger.error(f"Warning: Failed to setup resources: {e}")
        import traceback

        logger.error(f"Full traceback: {traceback.format_exc()}")


@app.on_event("shutdown")
async def shutdown_event():
    try:
        logger.info("--- Server shutting down! ---")

        from overmind.db.session import dispose_engine

        await dispose_engine()
        logger.info("--- Database connections closed. ---")

        if hasattr(app.state, "client_manager"):
            await app.state.client_manager.close_all()

        logger.info("--- All cached clients closed. ---")
    except Exception as e:
        logger.error(f"Warning: Error during shutdown: {e}")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core_api_router, prefix="/api/v1")
app.include_router(core_auth_router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "healthy"}


# ---------------------------------------------------------------------------
# Frontend SPA serving
# ---------------------------------------------------------------------------
if FRONTEND_DIR.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIR / "assets"),
        name="frontend-assets",
    )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA for any route not matched by the API."""
        file_path = (FRONTEND_DIR / full_path).resolve()
        if file_path.is_relative_to(FRONTEND_DIR) and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
else:

    @app.get("/")
    def read_root():
        return {"message": "Welcome to Overmind Core (frontend not built)"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
