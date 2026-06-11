"""Transactional email via Resend."""
import os

from core.config import logger


def send_email(to: str, subject: str, body_text: str, body_html: str = None):
    """Returns (True, None) on success, (False, reason_str) on failure."""
    import resend as _resend
    api_key = os.getenv("RESEND_API_KEY", "")
    frm     = os.getenv("MAIL_FROM", "info@kolaylistele.com")

    if not api_key:
        reason = "RESEND_API_KEY not set"
        logger.error("send_email: %s", reason)
        return False, reason

    _resend.api_key = api_key
    payload = {"from": frm, "to": [to], "subject": subject, "text": body_text}
    if body_html:
        payload["html"] = body_html

    try:
        r = _resend.Emails.send(payload)
        logger.info("send_email: sent id=%s to=%s subject=%r", r.get("id"), to, subject)
        return True, None
    except Exception as e:
        logger.error("send_email failed to=%s: %s", to, e, exc_info=True)
        return False, str(e)
