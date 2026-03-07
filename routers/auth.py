"""Google OAuth 2.0 flow for Gmail and Google Calendar."""
import json
import secrets
import logging
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from lib.config import settings
from lib.supabase import get_supabase
from services.crypto import encrypt
from services.email import send_transactional_email
from gotrue.errors import AuthApiError

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
_deletion_tokens: dict[str, dict] = {}  # token -> {user_id, email, expires_at}


class ConfirmDeleteRequest(BaseModel):
    token: str


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


@router.post("/account/request-delete")
async def request_account_delete(request: Request):
    """Send a deletion confirmation email to the authenticated user."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    jwt = auth_header.split(" ", 1)[1]
    sb = get_supabase()

    try:
        user_resp = sb.auth.get_user(jwt)
    except AuthApiError as e:
        raise HTTPException(401, f"Invalid token: {e}")
    except Exception as e:
        logger.error("get_user failed: %s", e)
        raise HTTPException(500, f"Auth error: {e}")

    user = user_resp.user
    deletion_token = secrets.token_urlsafe(32)
    _deletion_tokens[deletion_token] = {
        "user_id": user.id,
        "email": user.email,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
    }

    confirm_url = f"{settings.FRONTEND_URL}/confirm-delete?token={deletion_token}"
    try:
        await send_transactional_email(
            to=user.email,
            subject="Confirmez la suppression de votre compte Secretaria",
            html=f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
              <h2 style="color:#111;margin-bottom:8px">Suppression de compte</h2>
              <p style="color:#555;line-height:1.6">
                Vous avez demandé la suppression définitive de votre compte Secretaria
                et de toutes vos données associées.
              </p>
              <p style="margin:24px 0">
                <a href="{confirm_url}"
                   style="background:#ef4444;color:#fff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;display:inline-block">
                  Confirmer la suppression
                </a>
              </p>
              <p style="color:#999;font-size:13px">
                Ce lien expire dans 15 minutes. Si vous n'avez pas demandé cette action, ignorez cet email.
              </p>
            </div>
            """,
        )
    except Exception as e:
        logger.error("Failed to send deletion email to %s: %s", user.email, e)
        del _deletion_tokens[deletion_token]
        raise HTTPException(500, f"Échec d'envoi de l'email: {e}")

    logger.info("Deletion email sent for user %s (%s)", user.id, user.email)
    return {"status": "email_sent"}


@router.post("/account/confirm-delete")
async def confirm_account_delete(req: ConfirmDeleteRequest):
    """Complete account deletion after email confirmation."""
    data = _deletion_tokens.get(req.token)
    if not data:
        raise HTTPException(400, "Lien invalide ou déjà utilisé.")

    if datetime.now(timezone.utc) > data["expires_at"]:
        del _deletion_tokens[req.token]
        raise HTTPException(400, "Lien expiré. Recommencez la procédure depuis le dashboard.")

    user_id = data["user_id"]
    del _deletion_tokens[req.token]

    sb = get_supabase()
    try:
        sb.auth.admin.delete_user(user_id)
    except AuthApiError as e:
        logger.error("Failed to delete user %s: %s", user_id, e)
        raise HTTPException(500, f"Erreur lors de la suppression: {e}")

    logger.info("Account confirmed deleted: %s", user_id)
    return {"status": "deleted"}
