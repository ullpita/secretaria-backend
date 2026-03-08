"""Automated Vapi phone + assistant setup.

Flow:
  1. Check if Twilio number already exists in Vapi (avoid duplicate import)
  2. Create Sofia assistant via Vapi API with org_id in metadata + webhook URL
  3. Import (or update) Twilio number and link to assistant
  4. Save vapi_phone_id + vapi_assistant_id + sofia_phone to Supabase organizations
"""
import logging
import re
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from lib.config import settings
from lib.supabase import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["setup"])

VAPI_API = "https://api.vapi.ai"

SOFIA_SYSTEM_PROMPT = (
    "Tu es Sofia, une assistante vocale professionnelle et bienveillante. "
    "Tu prends les rendez-vous, tu réponds aux questions courantes et tu transmets "
    "les messages urgents. Tu es toujours polie, claire et concise. "
    "Tu ne donnes jamais de conseils médicaux ou juridiques. "
    "Si tu ne peux pas traiter une demande, tu proposes de prendre un message."
)


def _normalize_phone(number: str) -> str:
    """Strip spaces and ensure E.164 format."""
    return re.sub(r"\s+", "", number)


class PhoneSetupRequest(BaseModel):
    org_id: str
    twilio_account_sid: str
    twilio_auth_token: str
    phone_number: str  # E.164: +33159580013


class PhoneSetupResponse(BaseModel):
    success: bool
    phone_number: str
    vapi_phone_id: str
    vapi_assistant_id: str


@router.post("/phone", response_model=PhoneSetupResponse)
async def setup_phone(req: PhoneSetupRequest):
    """Import Twilio number into Vapi, create Sofia assistant, link them."""
    if not settings.VAPI_API_KEY:
        raise HTTPException(500, "VAPI_API_KEY not configured on server")

    normalized = _normalize_phone(req.phone_number)
    webhook_url = f"{settings.BACKEND_URL}/webhooks/vapi"

    headers = {
        "Authorization": f"Bearer {settings.VAPI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:

        # ── Step 1: Create Sofia assistant ────────────────────────────────
        assistant_payload = {
            "name": "Sofia",
            "model": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "systemPrompt": SOFIA_SYSTEM_PROMPT,
            },
            "voice": {
                "provider": "azure",
                "voiceId": "fr-FR-DeniseNeural",
            },
            "firstMessageMode": "assistant-speaks-first",
            "firstMessage": "Bonjour, je suis Sofia, comment puis-je vous aider ?",
            "serverUrl": webhook_url,
            "metadata": {"org_id": req.org_id},
        }

        asst_resp = await client.post(
            f"{VAPI_API}/assistant", headers=headers, json=assistant_payload
        )
        if asst_resp.status_code not in (200, 201):
            logger.error("Failed to create Vapi assistant: %s", asst_resp.text)
            raise HTTPException(
                400, f"Impossible de créer l'assistant Sofia: {asst_resp.text}"
            )

        assistant_id: str = asst_resp.json()["id"]
        logger.info("Created Vapi assistant %s for org %s", assistant_id, req.org_id)

        # ── Step 2: Check if phone number already exists in Vapi ──────────
        existing_phone_id: str | None = None
        list_resp = await client.get(f"{VAPI_API}/phone-number", headers=headers)
        if list_resp.status_code == 200:
            for p in list_resp.json():
                if _normalize_phone(p.get("number", "")) == normalized:
                    existing_phone_id = p["id"]
                    break

        if existing_phone_id:
            # Update existing phone number to point to the new assistant
            patch_resp = await client.patch(
                f"{VAPI_API}/phone-number/{existing_phone_id}",
                headers=headers,
                json={"assistantId": assistant_id},
            )
            if patch_resp.status_code not in (200, 201):
                logger.warning("Failed to update existing phone: %s", patch_resp.text)
            vapi_phone_id = existing_phone_id
            logger.info("Linked existing Vapi phone %s to assistant %s", vapi_phone_id, assistant_id)
        else:
            # Import Twilio number into Vapi
            phone_payload = {
                "provider": "twilio",
                "number": normalized,
                "twilioAccountSid": req.twilio_account_sid,
                "twilioAuthToken": req.twilio_auth_token,
                "assistantId": assistant_id,
            }
            phone_resp = await client.post(
                f"{VAPI_API}/phone-number", headers=headers, json=phone_payload
            )
            if phone_resp.status_code not in (200, 201):
                # Rollback: delete the assistant we just created
                await client.delete(
                    f"{VAPI_API}/assistant/{assistant_id}", headers=headers
                )
                logger.error("Failed to import Twilio number: %s", phone_resp.text)
                raise HTTPException(
                    400, f"Impossible d'importer le numéro Twilio: {phone_resp.text}"
                )
            vapi_phone_id = phone_resp.json()["id"]
            logger.info("Imported Twilio %s as Vapi phone %s", normalized, vapi_phone_id)

    # ── Step 3: Persist to Supabase ───────────────────────────────────────
    sb = get_supabase()
    try:
        sb.table("organizations").update(
            {
                "sofia_phone": normalized,
                "vapi_phone_id": vapi_phone_id,
                "vapi_assistant_id": assistant_id,
            }
        ).eq("id", req.org_id).execute()
    except Exception as e:
        logger.error("Failed to save phone config to Supabase: %s", e)
        # Non-fatal — Vapi is configured correctly, only DB save failed

    return PhoneSetupResponse(
        success=True,
        phone_number=normalized,
        vapi_phone_id=vapi_phone_id,
        vapi_assistant_id=assistant_id,
    )


@router.get("/phone/{org_id}")
async def get_phone_config(org_id: str):
    """Return current phone config for an org."""
    sb = get_supabase()
    result = (
        sb.table("organizations")
        .select("sofia_phone, vapi_phone_id, vapi_assistant_id")
        .eq("id", org_id)
        .single()
        .execute()
    )
    if not result.data:
        return {"configured": False}
    data = result.data
    return {
        "configured": bool(data.get("sofia_phone")),
        "phone_number": data.get("sofia_phone"),
        "vapi_phone_id": data.get("vapi_phone_id"),
        "vapi_assistant_id": data.get("vapi_assistant_id"),
    }
