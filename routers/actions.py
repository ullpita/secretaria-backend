"""Human-in-the-loop action approval/rejection endpoints."""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from lib.supabase import get_supabase
from models.schemas import ExtractedAction
from services.gmail import send_email
from services.calendar import create_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/actions", tags=["actions"])


class ApproveRequest(BaseModel):
    org_id: str
    # Optional overrides from user edits in the UI
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    title: str | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    attendee_email: str | None = None


class RejectRequest(BaseModel):
    org_id: str
    reason: str | None = None


@router.post("/{action_id}/approve")
async def approve_action(action_id: str, req: ApproveRequest):
    """Execute a pending action after user approval."""
    sb = get_supabase()

    # Fetch the action
    result = sb.table("call_actions").select("*").eq("id", action_id).single().execute()
    if not result.data:
        raise HTTPException(404, "Action not found")

    action_row = result.data
    if action_row["status"] != "pending":
        raise HTTPException(400, f"Action is already {action_row['status']}")

    action_type = action_row["type"]
    metadata = action_row.get("metadata") or {}

    # Build ExtractedAction from stored metadata + any user overrides
    action = ExtractedAction(
        type=action_type,
        label=action_row.get("label", ""),
        detail=action_row.get("detail", ""),
        # Email fields
        to=req.to or metadata.get("to"),
        subject=req.subject or metadata.get("subject"),
        body=req.body or metadata.get("body"),
        # Calendar fields
        title=req.title or metadata.get("title"),
        start_datetime=req.start_datetime or metadata.get("start_datetime"),
        end_datetime=req.end_datetime or metadata.get("end_datetime"),
        attendee_email=req.attendee_email or metadata.get("attendee_email"),
    )

    # Execute the action
    try:
        if action_type == "email":
            result_action = await send_email(action, req.org_id)
        elif action_type == "calendar_event":
            result_action = await create_event(action, req.org_id)
        else:
            raise HTTPException(400, f"Unsupported action type: {action_type}")
    except Exception as e:
        logger.error("Action %s execution failed: %s", action_id, e)
        sb.table("call_actions").update({
            "status": "failed",
            "error": str(e),
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", action_id).execute()
        raise HTTPException(500, f"Execution failed: {e}")

    # Update status in Supabase
    new_status = result_action.status
    sb.table("call_actions").update({
        "status": new_status,
        "error": result_action.error,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", action_id).execute()

    logger.info("Action %s approved → %s", action_id, new_status)
    return {"action_id": action_id, "status": new_status, "error": result_action.error}


@router.post("/{action_id}/reject")
async def reject_action(action_id: str, req: RejectRequest):
    """Reject a pending action without executing it."""
    sb = get_supabase()

    result = sb.table("call_actions").select("id, status").eq("id", action_id).single().execute()
    if not result.data:
        raise HTTPException(404, "Action not found")

    if result.data["status"] != "pending":
        raise HTTPException(400, f"Action is already {result.data['status']}")

    sb.table("call_actions").update({
        "status": "rejected",
        "error": req.reason,
        "rejected_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", action_id).execute()

    logger.info("Action %s rejected", action_id)
    return {"action_id": action_id, "status": "rejected"}
