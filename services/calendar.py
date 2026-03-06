"""Google Calendar executor — creates events on behalf of the connected user."""
import logging
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from ..models.schemas import ExtractedAction, ActionResult
from .crypto import decrypt
from ..lib.supabase import get_supabase

logger = logging.getLogger(__name__)


async def get_calendar_credentials(org_id: str) -> Credentials | None:
    """Load and decrypt Calendar OAuth tokens from Supabase."""
    sb = get_supabase()
    result = (
        sb.table("integrations")
        .select("access_token, refresh_token, token_expiry")
        .eq("org_id", org_id)
        .eq("provider", "calendar")
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
        logger.error("Failed to decrypt Calendar tokens for org %s: %s", org_id, e)
        return None

    from ..lib.config import settings
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )


async def create_event(action: ExtractedAction, org_id: str) -> ActionResult:
    """Create a Google Calendar event."""
    if not action.title or not action.start_datetime or not action.end_datetime:
        return ActionResult(
            type="calendar_event",
            label=action.label,
            detail=action.detail,
            status="failed",
            error="Missing title/start_datetime/end_datetime fields",
        )

    creds = await get_calendar_credentials(org_id)
    if not creds:
        return ActionResult(
            type="calendar_event",
            label=action.label,
            detail=action.detail,
            status="failed",
            error="Google Calendar not connected for this organization",
        )

    try:
        service = build("calendar", "v3", credentials=creds)

        event_body: dict = {
            "summary": action.title,
            "start": {"dateTime": action.start_datetime, "timeZone": "Europe/Paris"},
            "end":   {"dateTime": action.end_datetime,   "timeZone": "Europe/Paris"},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email",  "minutes": 1440},  # 24h
                    {"method": "popup",  "minutes": 30},
                ],
            },
        }

        if action.attendee_email:
            event_body["attendees"] = [{"email": action.attendee_email}]

        created = service.events().insert(
            calendarId="primary",
            body=event_body,
            sendUpdates="all" if action.attendee_email else "none",
        ).execute()

        return ActionResult(
            type="calendar_event",
            label=action.label,
            detail=f"{action.detail} — {created.get('htmlLink', '')}",
            status="success",
        )
    except Exception as e:
        logger.error("Calendar event creation failed: %s", e)
        return ActionResult(
            type="calendar_event",
            label=action.label,
            detail=action.detail,
            status="failed",
            error=str(e),
        )
