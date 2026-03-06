from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel


# ── Vapi ─────────────────────────────────────────────────────────────────────

class VapiMessage(BaseModel):
    type: str
    call: dict[str, Any] | None = None
    artifact: dict[str, Any] | None = None
    timestamp: str | None = None


class TranscriptLine(BaseModel):
    speaker: Literal["assistant", "user"]
    text: str
    timestamp: float | None = None


# ── Claude analysis ───────────────────────────────────────────────────────────

class ExtractedAction(BaseModel):
    type: Literal["email", "calendar_event", "task"]
    label: str
    detail: str
    # For email
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    # For calendar_event
    title: str | None = None
    start_datetime: str | None = None  # ISO 8601
    end_datetime: str | None = None
    attendee_email: str | None = None
    # For task
    task_title: str | None = None
    priority: Literal["high", "medium", "low"] = "medium"
    due_date: str | None = None  # YYYY-MM-DD
    notes: str | None = None


class CallAnalysis(BaseModel):
    summary: str
    sentiment: Literal["positive", "neutral", "negative"]
    caller_name: str | None = None
    actions: list[ExtractedAction]


# ── Action results ────────────────────────────────────────────────────────────

class ActionResult(BaseModel):
    type: str
    label: str
    detail: str
    status: Literal["success", "failed", "pending"]
    error: str | None = None


# ── Supabase row shapes (insert) ──────────────────────────────────────────────

class CallRow(BaseModel):
    id: str
    org_id: str | None = None
    caller_number: str
    caller_name: str | None = None
    duration_seconds: int
    started_at: str
    status: Literal["completed", "failed", "ongoing"]
    sentiment: Literal["positive", "neutral", "negative"]
    summary: str
    transcript: list[dict[str, Any]]
    sector: str | None = None


class TaskRow(BaseModel):
    id: str
    org_id: str | None = None
    title: str
    status: Literal["todo", "in_progress", "done"] = "todo"
    priority: Literal["high", "medium", "low"]
    due_date: str | None = None
    caller_name: str | None = None
    caller_phone: str | None = None
    notes: str | None = None
    call_id: str | None = None
