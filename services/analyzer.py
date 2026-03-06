"""Post-call analysis using Claude claude-sonnet-4-6."""
import json
import re
import logging
from anthropic import AsyncAnthropic
from lib.config import settings
from models.schemas import CallAnalysis

logger = logging.getLogger(__name__)

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


SYSTEM_PROMPT = """Tu es un assistant spécialisé dans l'analyse d'appels téléphoniques pour des professionnels libéraux (médecins, avocats, agents immobiliers).

À partir d'une transcription d'appel, tu dois extraire :
1. Un résumé concis de l'appel (1-2 phrases en français)
2. Le sentiment général (positive / neutral / negative)
3. Le nom de l'appelant si mentionné
4. Les actions à exécuter (emails, événements calendrier, tâches)

IMPORTANT : Réponds UNIQUEMENT avec un JSON valide, sans markdown, sans explication.

Format de réponse :
{
  "summary": "string",
  "sentiment": "positive" | "neutral" | "negative",
  "caller_name": "string | null",
  "actions": [
    {
      "type": "email",
      "label": "Email envoyé",
      "detail": "description courte",
      "to": "email@example.com",
      "subject": "Sujet de l'email",
      "body": "Corps de l'email en français"
    },
    {
      "type": "calendar_event",
      "label": "RDV créé",
      "detail": "description courte",
      "title": "Titre du RDV",
      "start_datetime": "2024-03-15T11:00:00",
      "end_datetime": "2024-03-15T12:00:00",
      "attendee_email": "email@example.com | null"
    },
    {
      "type": "task",
      "label": "Tâche créée",
      "detail": "description courte",
      "task_title": "Titre de la tâche",
      "priority": "high" | "medium" | "low",
      "due_date": "YYYY-MM-DD | null",
      "notes": "Notes supplémentaires"
    }
  ]
}"""


async def analyze_call(
    transcript: list[dict],
    caller_number: str,
    sector: str | None = None,
) -> CallAnalysis:
    """Analyze a call transcript with Claude and return structured actions."""

    transcript_text = "\n".join(
        f"[{line.get('speaker', 'Unknown')}] {line.get('text', '')}"
        for line in transcript
    )

    user_message = f"""Appel téléphonique — Numéro appelant : {caller_number}
Secteur : {sector or 'non précisé'}

Transcription :
{transcript_text}

Analyse cet appel et retourne le JSON demandé."""

    client = _get_client()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = message.content[0].text.strip()

    # Extract JSON (Claude may wrap in markdown even with instructions)
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        logger.error("Claude did not return valid JSON: %s", raw[:200])
        return _fallback_analysis(caller_number)

    try:
        data = json.loads(match.group())
        return CallAnalysis(**data)
    except Exception as e:
        logger.error("Failed to parse Claude response: %s | raw: %s", e, raw[:200])
        return _fallback_analysis(caller_number)


def _fallback_analysis(caller_number: str) -> CallAnalysis:
    return CallAnalysis(
        summary="Analyse indisponible — traitement manuel requis.",
        sentiment="neutral",
        caller_name=None,
        actions=[],
    )
