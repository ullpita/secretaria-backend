"""Gmail executor — sends emails on behalf of the connected user."""
import logging
import base64
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from models.schemas import ExtractedAction, ActionResult
from services.crypto import decrypt
from lib.supabase import get_supabase

logger = logging.getLogger(__name__)


async def get_gmail_credentials(org_id: str) -> Credentials | None:
    """Load and decrypt Gmail OAuth tokens from Supabase."""
    sb = get_supabase()
    result = (
        sb.table("integrations")
        .select("access_token, refresh_token, token_expiry")
        .eq("org_id", org_id)
        .eq("provider", "gmail")
        .single()
        .execute()
    )
    if not result.data:
        return None

    row = result.data
    try:
        access_token = decrypt(row["access_token"])
        refresh_token = decrypt(row["refresh_token"])
    except Exception as e:
        logger.error("Failed to decrypt Gmail tokens for org %s: %s", org_id, e)
        return None

    from lib.config import settings
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )


async def send_email(action: ExtractedAction, org_id: str) -> ActionResult:
    """Send an email via Gmail API."""
    if not action.to or not action.subject or not action.body:
        return ActionResult(
            type="email",
            label=action.label,
            detail=action.detail,
            status="failed",
            error="Missing to/subject/body fields",
        )

    creds = await get_gmail_credentials(org_id)
    if not creds:
        return ActionResult(
            type="email",
            label=action.label,
            detail=action.detail,
            status="failed",
            error="Gmail not connected for this organization",
        )

    try:
        service = build("gmail", "v1", credentials=creds)

        msg = MIMEText(action.body)
        msg["to"] = action.to
        msg["subject"] = action.subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        service.users().messages().send(userId="me", body={"raw": raw}).execute()

        return ActionResult(
            type="email",
            label=action.label,
            detail=f"{action.detail} → {action.to}",
            status="success",
        )
    except Exception as e:
        logger.error("Gmail send failed: %s", e)
        return ActionResult(
            type="email",
            label=action.label,
            detail=action.detail,
            status="failed",
            error=str(e),
        )
