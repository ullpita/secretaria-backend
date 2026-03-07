"""Transactional email via Resend HTTP API."""
import logging
import httpx
from lib.config import settings

logger = logging.getLogger(__name__)


async def send_transactional_email(to: str, subject: str, html: str) -> None:
    if not settings.SMTP_PASS:
        logger.warning("RESEND_API_KEY not configured — skipping email to %s | subject: %s", to, subject)
        return

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.SMTP_PASS}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.SMTP_FROM,
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        resp.raise_for_status()

    logger.info("Email sent to %s: %s", to, subject)
