"""
Shared SMTP email sender.

Uses Python stdlib smtplib + email.mime — no extra dependencies.

Usage:
    from app.emailer import send_email
    success = send_email("student@example.com", "Offer Letter", "Body text", pdf_path="...")
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("emailer")


def send_email(
    to_address: str,
    subject: str,
    body: str,
    pdf_path: str | None = None,
) -> bool:
    """
    Send an email via SMTP, optionally with a PDF attachment.

    SMTP settings are read from app.config.settings (SMTP_HOST, SMTP_PORT,
    SMTP_USER, SMTP_PASS).

    Returns True on success, False on failure.
    """
    import smtplib
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from app.config import settings

    if not settings.SMTP_USER or not settings.SMTP_PASS:
        logger.warning("SMTP_USER or SMTP_PASS not set — cannot send email")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = settings.SMTP_USER
        msg["To"] = to_address
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if pdf_path:
            pdf_file = Path(pdf_path)
            if pdf_file.is_file():
                with open(pdf_file, "rb") as f:
                    part = MIMEApplication(f.read(), _subtype="pdf")
                    part.add_header(
                        "Content-Disposition",
                        "attachment",
                        filename=pdf_file.name,
                    )
                    msg.attach(part)
            else:
                logger.warning(f"PDF not found for attachment: {pdf_path}")

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.send_message(msg)

        logger.info(f"Email sent to {to_address}: {subject}")
        return True
    except Exception as e:
        logger.exception(f"Email send failed to {to_address}: {e}")
        return False
