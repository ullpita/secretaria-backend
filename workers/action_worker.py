"""
Post-call async worker.

Flow:
  1. Analyze transcript with Claude → CallAnalysis
  2. Save call to Supabase (calls table)
  3. Save actions as pending (email/calendar) or execute immediately (task)
  4. User approves/rejects via /actions/:id/approve endpoint
"""
import uuid
import logging
from datetime import datetime, timezone
from services.analyzer import analyze_call
from lib.supabase import get_supabase
from models.schemas import ExtractedAction

logger = logging.getLogger(__name__)


async def process_call(payload: dict) -> None:
    """Full post-call processing pipeline."""
    call_id: str = payload["call_id"]
    org_id: str | None = payload.get("org_id")
    caller_number: str = payload["caller_number"]
    transcript: list[dict] = payload["transcript"]
    duration_seconds: int = payload.get("duration_seconds", 0)
    started_at: str = payload.get("started_at", datetime.now(timezone.utc).isoformat())
    sector: str | None = payload.get("sector")

    logger.info("[%s] Starting post-call analysis", call_id)

    # ── Step 1: Claude analysis ────────────────────────────────────────────────
    try:
        analysis = await analyze_call(
            transcript=transcript,
            caller_number=caller_number,
            sector=sector,
        )
        logger.info("[%s] Analysis complete — %d actions", call_id, len(analysis.actions))
    except Exception as e:
        logger.error("[%s] Analysis failed: %s", call_id, e)
        return

    # ── Step 2: Save call to Supabase ─────────────────────────────────────────
    sb = get_supabase()
    call_row = {
        "id": call_id,
        "org_id": org_id,
        "caller_number": caller_number,
        "caller_name": analysis.caller_name,
        "duration_seconds": duration_seconds,
        "started_at": started_at,
        "status": "completed",
        "sentiment": analysis.sentiment,
        "summary": analysis.summary,
        "transcript": transcript,
        "sector": sector,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb.table("calls").insert(call_row).execute()
    except Exception as e:
        logger.error("[%s] Failed to save call: %s", call_id, e)

    # ── Step 3: Save actions (pending for email/calendar, auto for task) ───────
    action_rows = []
    for action in analysis.actions:
        action_id = f"a_{uuid.uuid4().hex[:8]}"
        if action.type == "task":
            await _create_task(action, org_id, call_id, analysis.caller_name, caller_number)
            action_rows.append({
                "id": action_id,
                "call_id": call_id,
                "org_id": org_id,
                "type": action.type,
                "label": action.label,
                "detail": action.detail,
                "status": "success",
                "error": None,
                "metadata": None,
                "executed_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            # email / calendar_event → pending, stored with full content for approval
            action_rows.append({
                "id": action_id,
                "call_id": call_id,
                "org_id": org_id,
                "type": action.type,
                "label": action.label,
                "detail": action.detail,
                "status": "pending",
                "error": None,
                "metadata": _build_metadata(action),
                "executed_at": None,
            })

    if action_rows:
        try:
            sb.table("call_actions").insert(action_rows).execute()
        except Exception as e:
            logger.error("[%s] Failed to save actions: %s", call_id, e)

    pending = sum(1 for r in action_rows if r["status"] == "pending")
    logger.info("[%s] Done — %d pending approval, %d auto-executed", call_id, pending, len(action_rows) - pending)


def _build_metadata(action: ExtractedAction) -> dict:
    """Extract rich content from action for pending approval display."""
    if action.type == "email":
        return {
            "to": action.to or "",
            "subject": action.subject or "",
            "body": action.body or "",
        }
    elif action.type == "calendar_event":
        return {
            "title": action.title or action.label,
            "start_datetime": action.start_datetime or "",
            "end_datetime": action.end_datetime or "",
            "attendee_email": action.attendee_email or "",
        }
    return {}


async def _create_task(
    action: ExtractedAction,
    org_id: str | None,
    call_id: str,
    caller_name: str | None,
    caller_number: str,
) -> None:
    sb = get_supabase()
    task_row = {
        "id": f"t_{uuid.uuid4().hex[:8]}",
        "org_id": org_id,
        "title": action.task_title or action.label,
        "status": "todo",
        "priority": action.priority,
        "due_date": action.due_date,
        "caller_name": caller_name,
        "caller_phone": caller_number,
        "notes": action.notes or action.detail,
        "call_id": call_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb.table("tasks").insert(task_row).execute()
    except Exception as e:
        logger.error("Failed to create task: %s", e)
