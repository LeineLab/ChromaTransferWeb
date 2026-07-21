"""
Optional OIDC authentication support.
When OIDC_ENABLED=false (default), all auth endpoints return stub responses
and get_current_user returns a dummy local user.
"""
import os
import secrets
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware  # re-exported for main.py

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def is_oidc_enabled() -> bool:
    return os.environ.get("OIDC_ENABLED", "false").lower() in ("1", "true", "yes")


def _oidc_config() -> dict:
    return {
        "issuer": os.environ.get("OIDC_ISSUER", ""),
        "client_id": os.environ.get("OIDC_CLIENT_ID", ""),
        "client_secret": os.environ.get("OIDC_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("OIDC_REDIRECT_URI", "http://localhost:8000/auth/callback"),
    }


# Cached OIDC discovery document
_oidc_discovery: Optional[dict] = None


async def _get_discovery() -> dict:
    global _oidc_discovery
    if _oidc_discovery is not None:
        return _oidc_discovery
    cfg = _oidc_config()
    issuer = cfg["issuer"]
    if not issuer:
        raise RuntimeError("OIDC_ISSUER is not configured")
    discovery_url = f"{issuer}/.well-known/openid-configuration"
    async with httpx.AsyncClient() as client:
        resp = await client.get(discovery_url, timeout=10)
        resp.raise_for_status()
        _oidc_discovery = resp.json()
    return _oidc_discovery


# ---------------------------------------------------------------------------
# Dependency: get_current_user
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> dict[str, Any]:
    """
    Returns the authenticated user from session, or a dummy user when OIDC
    is disabled.  Raises HTTP 401 when OIDC is enabled but no user is found.
    """
    if not is_oidc_enabled():
        return {"sub": "local", "name": "Local User"}

    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ---------------------------------------------------------------------------
# Auth router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request):
    """Redirect to OIDC provider authorization endpoint."""
    if not is_oidc_enabled():
        return JSONResponse({"detail": "OIDC not enabled"}, status_code=400)

    discovery = await _get_discovery()
    cfg = _oidc_config()

    state = secrets.token_urlsafe(32)
    request.session["oidc_state"] = state

    auth_endpoint = discovery["authorization_endpoint"]
    params = (
        f"?response_type=code"
        f"&client_id={cfg['client_id']}"
        f"&redirect_uri={cfg['redirect_uri']}"
        f"&scope=openid+profile+email"
        f"&state={state}"
    )
    return RedirectResponse(auth_endpoint + params)


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle OIDC authorization code callback."""
    if not is_oidc_enabled():
        return JSONResponse({"detail": "OIDC not enabled"}, status_code=400)

    if error:
        raise HTTPException(status_code=400, detail=f"OIDC error: {error}")

    # Validate state
    stored_state = request.session.get("oidc_state")
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    discovery = await _get_discovery()
    cfg = _oidc_config()

    # Exchange code for tokens
    token_endpoint = discovery["token_endpoint"]
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": cfg["redirect_uri"],
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
            },
            timeout=15,
        )
        if token_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Token exchange failed: {token_resp.text}",
            )
        tokens = token_resp.json()

        # Fetch user info
        userinfo_endpoint = discovery["userinfo_endpoint"]
        userinfo_resp = await client.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=15,
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Userinfo fetch failed: {userinfo_resp.text}",
            )
        user_info = userinfo_resp.json()

    # Store user in session
    request.session["user"] = user_info
    # Clean up state
    request.session.pop("oidc_state", None)

    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to home."""
    request.session.clear()
    return RedirectResponse("/")


@router.get("/me")
async def me(request: Request):
    """Return current user info as JSON."""
    if not is_oidc_enabled():
        # No OIDC - return 401 so the frontend knows to skip user display
        raise HTTPException(status_code=401, detail="OIDC not enabled")

    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
