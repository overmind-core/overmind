import logging
from fastapi import Depends, HTTPException

from overmind.config import settings

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


logger = logging.getLogger(__name__)


async def is_valid_backend_user(
    bearer_token: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
):
    # TODO: now this is called directly from client, update auth
    if bearer_token and bearer_token.credentials == settings.proxy_token:
        return True
    else:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
