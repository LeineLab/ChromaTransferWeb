"""
Chroma Transfer - Main FastAPI application entry point.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env before anything else so env vars are available at import time
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import get_current_user, is_oidc_enabled, router as auth_router
from .database import Base, engine
from .models import Machine  # noqa: F401 – ensures table is registered
from .routers.machines import router as machines_router
from .routers.transfer import router as transfer_router

# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------

app = FastAPI(title="Chroma Transfer", version="1.0.0")

# Session middleware (required for OIDC, harmless without it)
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# ---------------------------------------------------------------------------
# OIDC protection middleware
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"


class OIDCProtectionMiddleware:
    """
    When OIDC is enabled, enforce authentication for all routes except:
      - /auth/*          (login/callback/logout/me)
      - /health
      - /static/*
      - /               (root redirect)
    API routes (/api/*) get a 401 JSON response; others get a login redirect.
    """

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        if not is_oidc_enabled():
            await self._app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        # Paths that are always public
        public_prefixes = ("/auth/", "/health", "/static/", "/")
        is_public = path == "/" or any(
            path.startswith(p) for p in ("/auth/", "/health", "/static/")
        )
        if is_public:
            await self._app(scope, receive, send)
            return

        # Check session for authenticated user
        # We need to build a Request to access the session
        from starlette.requests import Request as StarletteRequest
        request = StarletteRequest(scope, receive)
        user = request.session.get("user")
        if user:
            await self._app(scope, receive, send)
            return

        # Not authenticated – return appropriate response
        if path.startswith("/api/"):
            response = JSONResponse(
                {"detail": "Not authenticated"}, status_code=401
            )
        else:
            response = RedirectResponse("/auth/login")

        await response(scope, receive, send)


app.add_middleware(OIDCProtectionMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(machines_router)
app.include_router(transfer_router)
app.include_router(auth_router)

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return RedirectResponse("/static/index.html")


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    # Create all database tables
    Base.metadata.create_all(bind=engine)
