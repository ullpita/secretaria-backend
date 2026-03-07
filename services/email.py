"""Transactional email via SMTP. Falls back to logging if SMTP is not configured."""
import asyncio
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from lib.config import settings

logger = logging.getLogger(__name__)


def _send_smtp(to: str, subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(settings.SMTP_USER, settings.SMTP_PASS)
        server.sendmail(settings.SMTP_FROM, to, msg.as_string())


async def send_transactional_email(to: str, subject: str, html: str) -> None:
    if not settings.SMTP_HOST:
        logger.warning("SMTP not configured — skipping email to %s | subject: %s", to, subject)
        return
    await asyncio.to_thread(_send_smtp, to, subject, html)
    logger.info("Email sent to %s: %s", to, subject)
