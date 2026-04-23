"""Optional HTTP Basic auth for the dashboard.

If DASHBOARD_USER and DASHBOARD_PASSWORD are both set in the environment,
every /dashboard/* and /api/* route requires a matching Basic credential.
If either is empty the dashboard is open (intended for local development).
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.config import settings

_security = HTTPBasic(auto_error=False)


def require_dashboard_auth(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> str:
    """FastAPI dependency that enforces auth iff configured.

    Returns the authenticated username, or "anonymous" when auth isn't set.
    """
    user_env = settings.DASHBOARD_USER
    pwd_env = settings.DASHBOARD_PASSWORD
    if not (user_env and pwd_env):
        return "anonymous"

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dashboard authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Elaxtra Dashboard"'},
        )

    user_ok = secrets.compare_digest(credentials.username, user_env)
    pwd_ok = secrets.compare_digest(credentials.password, pwd_env)
    if not (user_ok and pwd_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard credentials",
            headers={"WWW-Authenticate": 'Basic realm="Elaxtra Dashboard"'},
        )
    return credentials.username
