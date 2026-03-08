"""Automated Vapi phone + assistant setup.

Two flows:
  A. provision  — Secretaria buys a French number from its own Twilio account.
                  User just clicks a button, no credentials needed.
  B. bring-your-own — User provides their own Twilio Account SID + Auth Token + number.
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
TWILIO_API = "https://api.twilio.com/2010-04-01"

SOFIA_SYSTEM_PROMPT = (
    "Tu es Sofia, une assistante vocale professionnelle et bienveillante. "
    "Tu prends les rendez-vous, tu réponds aux questions courantes et tu transmets "
    "les messages urgents. Tu es toujours polie, claire et concise. "
    "Tu ne donnes jamais de conseils médicaux ou juridiques. "
    "Si tu ne peux pas traiter une demande, tu proposes de prendre un message."
)


def _normalize_phone(number: str) -> str:
    return re.sub(r"\s+", "", number)


# ── Shared Vapi helper ────────────────────────────────────────────────────────

async def _configure_vapi(
    org_id: str,
    phone_number: str,
    twilio_account_sid: str,
    twilio_auth_token: str,
) -> tuple[str, str]:
    """Create Sofia assistant in Vapi, import (or update) Twilio number, link them.

    Returns (vapi_phone_id, vapi_assistant_id).
    """
    normalized = _normalize_phone(phone_number)
    webhook_url = f"{settings.BACKEND_URL}/webhooks/vapi"
    headers = {
        "Authorization": f"Bearer {settings.VAPI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: Create Sofia assistant
        asst_resp = await client.post(f"{VAPI_API}/assistant", headers=headers, json={
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
            "metadata": {"org_id": org_id},
        })
        if asst_resp.status_code not in (200, 201):
            raise HTTPException(400, f"Impossible de créer l'assistant Sofia: {asst_resp.text}")

        assistant_id: str = asst_resp.json()["id"]
        logger.info("Created Vapi assistant %s for org %s", assistant_id, org_id)

        # Step 2: Check if phone already exists in Vapi
        existing_phone_id: str | None = None
        list_resp = await client.get(f"{VAPI_API}/phone-number", headers=headers)
        if list_resp.status_code == 200:
            for p in list_resp.json():
                if _normalize_phone(p.get("number", "")) == normalized:
                    existing_phone_id = p["id"]
                    break

        if existing_phone_id:
            patch_resp = await client.patch(
                f"{VAPI_API}/phone-number/{existing_phone_id}",
                headers=headers,
                json={"assistantId": assistant_id},
            )
            if patch_resp.status_code not in (200, 201):
                logger.warning("Failed to update existing phone: %s", patch_resp.text)
            vapi_phone_id = existing_phone_id
            logger.info("Linked existing phone %s to assistant %s", vapi_phone_id, assistant_id)
        else:
            phone_resp = await client.post(f"{VAPI_API}/phone-number", headers=headers, json={
                "provider": "twilio",
                "number": normalized,
                "twilioAccountSid": twilio_account_sid,
                "twilioAuthToken": twilio_auth_token,
                "assistantId": assistant_id,
            })
            if phone_resp.status_code not in (200, 201):
                await client.delete(f"{VAPI_API}/assistant/{assistant_id}", headers=headers)
                raise HTTPException(400, f"Impossible d'importer le numéro: {phone_resp.text}")
            vapi_phone_id = phone_resp.json()["id"]
            logger.info("Imported %s as Vapi phone %s", normalized, vapi_phone_id)

    return vapi_phone_id, assistant_id


def _save_to_supabase(org_id: str, phone_number: str, vapi_phone_id: str, assistant_id: str) -> None:
    sb = get_supabase()
    try:
        sb.table("organizations").update({
            "sofia_phone": phone_number,
            "vapi_phone_id": vapi_phone_id,
            "vapi_assistant_id": assistant_id,
        }).eq("id", org_id).execute()
    except Exception as e:
        logger.error("Failed to save phone config: %s", e)


# ── Route A: Secretaria provisions the number ─────────────────────────────────

class ProvisionRequest(BaseModel):
    org_id: str


@router.post("/phone/provision")
async def provision_phone(req: ProvisionRequest):
    """Buy a French number from Secretaria's Twilio account and configure Sofia."""
    if not settings.VAPI_API_KEY:
        raise HTTPException(500, "VAPI_API_KEY non configuré")
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        raise HTTPException(500, "Identifiants Twilio Secretaria non configurés")

    twilio_auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

    async with httpx.AsyncClient(timeout=30) as client:
        # Search available French local numbers
        search_resp = await client.get(
            f"{TWILIO_API}/Accounts/{settings.TWILIO_ACCOUNT_SID}/AvailablePhoneNumbers/FR/Local.json",
            auth=twilio_auth,
            params={"VoiceEnabled": "true", "PageSize": "5"},
        )
        if search_resp.status_code != 200:
            raise HTTPException(400, f"Impossible de chercher des numéros français: {search_resp.text}")

        numbers = search_resp.json().get("available_phone_numbers", [])
        if not numbers:
            raise HTTPException(404, "Aucun numéro français disponible pour le moment")

        chosen = numbers[0]["phone_number"]
        logger.info("Provisioning Twilio number %s for org %s", chosen, req.org_id)

        # Purchase the number
        buy_resp = await client.post(
            f"{TWILIO_API}/Accounts/{settings.TWILIO_ACCOUNT_SID}/IncomingPhoneNumbers.json",
            auth=twilio_auth,
            data={"PhoneNumber": chosen},
        )
        if buy_resp.status_code not in (200, 201):
            raise HTTPException(400, f"Impossible d'acheter le numéro: {buy_resp.text}")

        logger.info("Purchased %s", chosen)

    # Configure Vapi with Secretaria's Twilio credentials
    vapi_phone_id, assistant_id = await _configure_vapi(
        org_id=req.org_id,
        phone_number=chosen,
        twilio_account_sid=settings.TWILIO_ACCOUNT_SID,
        twilio_auth_token=settings.TWILIO_AUTH_TOKEN,
    )

    _save_to_supabase(req.org_id, chosen, vapi_phone_id, assistant_id)

    return {
        "success": True,
        "phone_number": chosen,
        "vapi_phone_id": vapi_phone_id,
        "vapi_assistant_id": assistant_id,
    }


# ── Route B: User brings their own Twilio number ──────────────────────────────

class PhoneSetupRequest(BaseModel):
    org_id: str
    twilio_account_sid: str
    twilio_auth_token: str
    phone_number: str


@router.post("/phone")
async def setup_phone(req: PhoneSetupRequest):
    """Import user's own Twilio number into Vapi and configure Sofia."""
    if not settings.VAPI_API_KEY:
        raise HTTPException(500, "VAPI_API_KEY non configuré")

    normalized = _normalize_phone(req.phone_number)
    vapi_phone_id, assistant_id = await _configure_vapi(
        org_id=req.org_id,
        phone_number=normalized,
        twilio_account_sid=req.twilio_account_sid,
        twilio_auth_token=req.twilio_auth_token,
    )

    _save_to_supabase(req.org_id, normalized, vapi_phone_id, assistant_id)

    return {
        "success": True,
        "phone_number": normalized,
        "vapi_phone_id": vapi_phone_id,
        "vapi_assistant_id": assistant_id,
    }


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/phone/{org_id}")
async def get_phone_config(org_id: str):
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
