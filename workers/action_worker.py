"""
Post-call async worker.

Flow:
  1. Analyze transcript with Claude → CallAnalysis
  2. Save call to Supabase (calls table)
  3. Execute each action (email / calendar / task)
  4. Save action results to Supabase (call_actions table)
  5. Create tasks in Supabase (tasks table)
"""
import uuid
import logging
from datetime import datetime, timezone
from services.analyzer import analyze_call
from services.gmail import send_email
from services.calendar import create_event
from lib.supabase import get_supabase
from models.schemas import ExtractedAction, ActionResult

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

    # ── Step 3: Execute actions ────────────────────────────────────────────────
    action_results: list[ActionResult] = []

    for action in analysis.actions:
        result = await _execute_action(action, org_id, call_id, analysis.caller_name, caller_number)
        action_results.append(result)

    # ── Step 4: Save action results ───────────────────────────────────────────
    if action_results:
        action_rows = [
            {
                "id": f"a_{uuid.uuid4().hex[:8]}",
                "call_id": call_id,
                "org_id": org_id,
                "type": r.type,
                "label": r.label,
                "detail": r.detail,
                "status": r.status,
                "error": r.error,
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }
            for r in action_results
        ]
        try:
            sb.table("call_actions").insert(action_rows).execute()
        except Exception as e:
            logger.error("[%s] Failed to save action results: %s", call_id, e)

    logger.info(
        "[%s] Done — %d/%d actions succeeded",
        call_id,
        sum(1 for r in action_results if r.status == "success"),
        len(action_results),
    )


async def _execute_action(
    action: ExtractedAction,
    org_id: str | None,
    call_id: str,
    caller_name: str | None,
    caller_number: str,
) -> ActionResult:
    """Route action to the right executor."""
    try:
        if action.type == "email":
            if not org_id:
                return ActionResult(type="email", label=action.label, detail=action.detail,
                                    status="failed", error="No org_id — cannot load Gmail credentials")
            return await send_email(action, org_id)

        elif action.type == "calendar_event":
            if not org_id:
                return ActionResult(type="calendar_event", label=action.label, detail=action.detail,
                                    status="failed", error="No org_id — cannot load Calendar credentials")
            return await create_event(action, org_id)

        elif action.type == "task":
            return await _create_task(action, org_id, call_id, caller_name, caller_number)

        else:
            return ActionResult(type=action.type, label=action.label, detail=action.detail,
                                status="failed", error=f"Unknown action type: {action.type}")
    except Exception as e:
        logger.error("Action %s failed unexpectedly: %s", action.type, e)
        return ActionResult(type=action.type, label=action.label, detail=action.detail,
                            status="failed", error=str(e))


async def _create_task(
    action: ExtractedAction,
    org_id: str | None,
    call_id: str,
    caller_name: str | None,
    caller_number: str,
) -> ActionResult:
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
        return ActionResult(type="task", label=action.label, detail=action.detail, status="success")
    except Exception as e:
        return ActionResult(type="task", label=action.label, detail=action.detail,
                            status="failed", error=str(e))
