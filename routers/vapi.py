"""Vapi webhook endpoint with HMAC-SHA256 signature validation."""
import hmac
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from lib.config import settings
from lib.supabase import get_supabase
from models.schemas import VapiMessage
from workers.action_worker import process_call

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

VAPI_EVENTS = {
    "call-ended",
    "call-started",
    "transcript",
    "function-call",
}


def _verify_signature(body: bytes, signature: str | None) -> bool:
    """Validate Vapi HMAC-SHA256 webhook signature."""
    if not signature:
        return not settings.is_production  # Allow in dev without sig
    expected = hmac.new(
        settings.VAPI_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/vapi")
async def vapi_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive Vapi call events and queue post-call processing."""
    body = await request.body()
    signature = request.headers.get("x-vapi-signature")

    if not _verify_signature(body, signature):
        logger.warning("Invalid Vapi signature")
        raise HTTPException(401, "Invalid webhook signature")

    try:
        payload = VapiMessage.model_validate_json(body)
    except Exception as e:
        logger.error("Failed to parse Vapi payload: %s", e)
        raise HTTPException(422, "Invalid payload")

    event_type = payload.type
    logger.info("Vapi event received: %s", event_type)

    if event_type == "call-ended" and payload.call and payload.artifact:
        call_data = payload.call
        artifact = payload.artifact

        # Extract org_id from Vapi metadata (set when creating the assistant)
        org_id = call_data.get("metadata", {}).get("org_id")

        call_id = f"call_{uuid.uuid4().hex[:12]}"
        transcript_raw = artifact.get("messages", [])

        # Normalize Vapi transcript format → our format
        transcript = [
            {
                "speaker": "Sofia" if m.get("role") == "assistant" else "Client",
                "text": m.get("message", m.get("content", "")),
                "timestamp": _format_ts(m.get("time", 0)),
            }
            for m in transcript_raw
            if m.get("role") in ("assistant", "user")
        ]

        call_payload = {
            "call_id": call_id,
            "org_id": org_id,
            "caller_number": call_data.get("customer", {}).get("number", "unknown"),
            "duration_seconds": int(call_data.get("endedAt", 0) or 0) - int(call_data.get("startedAt", 0) or 0),
            "started_at": call_data.get("createdAt") or datetime.now(timezone.utc).isoformat(),
            "transcript": transcript,
            "sector": call_data.get("metadata", {}).get("sector"),
        }

        # Queue async processing (non-blocking)
        background_tasks.add_task(process_call, call_payload)
        logger.info("Queued post-call processing for %s", call_id)

    # Always return 200 quickly to avoid Vapi retries
    return {"received": True, "event": event_type}


def _format_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"
