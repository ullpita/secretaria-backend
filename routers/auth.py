"""Google OAuth 2.0 flow for Gmail and Google Calendar."""
import json
import secrets
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from lib.config import settings
from lib.supabase import get_supabase
from services.crypto import encrypt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

SCOPES = {
    "gmail": [
        "https://www.googleapis.com/auth/gmail.send",
        "openid",
        "email",
    ],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.events",
        "openid",
        "email",
    ],
}

# In-memory state store (use Redis in production for multi-instance)
_pending_states: dict[str, dict] = {}


def _make_flow(scope: str) -> Flow:
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES.get(scope, SCOPES["gmail"]),
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )
    return flow


@router.get("/google")
async def google_auth_start(
    request: Request,
    scope: str = "gmail",
    redirect: str = "/dashboard/integrations",
    org_id: str | None = None,
):
    """Redirect user to Google consent screen."""
    if scope not in SCOPES:
        raise HTTPException(400, f"Unknown scope '{scope}'. Use 'gmail' or 'calendar'.")

    flow = _make_flow(scope)
    state = secrets.token_urlsafe(32)
    _pending_states[state] = {
        "scope": scope,
        "redirect": redirect,
        "org_id": org_id or request.headers.get("x-org-id"),
    }

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return RedirectResponse(auth_url)


@router.get("/google/callback")
async def google_auth_callback(request: Request, code: str, state: str):
    """Handle Google OAuth callback, store encrypted tokens."""
    state_data = _pending_states.pop(state, None)
    if not state_data:
        raise HTTPException(400, "Invalid or expired OAuth state.")

    scope = state_data["scope"]
    org_id = state_data.get("org_id")
    redirect_path = state_data.get("redirect", "/dashboard/integrations")

    flow = _make_flow(scope)
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.error("Failed to fetch Google token: %s", e)
        raise HTTPException(400, "Failed to exchange authorization code.")

    creds: Credentials = flow.credentials

    if not org_id:
        # Fallback: use sub from id_token if available
        logger.warning("No org_id in OAuth state — token will not be saved.")
        return RedirectResponse(f"{settings.FRONTEND_URL}{redirect_path}?error=no_org")

    # Encrypt tokens before storage
    encrypted_access = encrypt(creds.token)
    encrypted_refresh = encrypt(creds.refresh_token or "")
    expiry = creds.expiry.isoformat() if creds.expiry else None

    sb = get_supabase()
    sb.table("integrations").upsert(
        {
            "org_id": org_id,
            "provider": scope,
            "access_token": encrypted_access,
            "refresh_token": encrypted_refresh,
            "token_expiry": expiry,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="org_id,provider",
    ).execute()

    logger.info("Stored %s tokens for org %s", scope, org_id)
    return RedirectResponse(f"{settings.FRONTEND_URL}{redirect_path}?connected={scope}")
