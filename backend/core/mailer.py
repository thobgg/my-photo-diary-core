"""
core/mailer.py
Thin facade around smtplib for outbound mail via mailbox.org.

Philosophy: synchronous, no queue, no retries — if SMTP is briefly
unreachable, the caller is allowed to bubble the error up.
For the volume of mail MPD sends (Share-per-Mail: a few a day within
the family), complexity like Celery/Redis would be overkill.

STARTTLS on port 587 is the mailbox.org standard.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import List, Optional, Tuple

import config

logger = logging.getLogger(__name__)


# --- Herkunft der Zugangsdaten -------------------------------------------
#
# Die Einstellung gewinnt, mpd.env ist der Rueckfall. Ein leerer Wert (bzw.
# Port 0) in den Einstellungen heisst "nicht gesetzt".
#
# Warum ueberhaupt zwei Quellen: mpd.env erreicht nur, wer eine Shell im
# Container hat. Auf einer fremden NAS (SPK) waere Mailversand damit gar
# nicht einzurichten gewesen — und ohne ihn gibt es kein Teilen per Mail.
# Bestehende Installationen, die nur mpd.env benutzen, merken nichts:
# solange in den Einstellungen nichts steht, gilt weiterhin die Umgebung.
#
# Absichtlich bei JEDEM Aufruf frisch gelesen statt auf Modulebene: sonst
# muesste man nach jeder Aenderung im Einstellungsdialog neu starten.

_ENV_FALLBACK = {
    "smtp_host"     : lambda: config.SMTP_HOST,
    "smtp_port"     : lambda: config.SMTP_PORT,
    "smtp_user"     : lambda: config.SMTP_USER,
    "smtp_pass"     : lambda: config.SMTP_PASS,
    "smtp_from_addr": lambda: config.SMTP_FROM_ADDR,
    "smtp_from_name": lambda: config.SMTP_FROM_NAME,
}


def _val(key: str):
    """Einstellung, sonst mpd.env. Leer/0 in den Einstellungen = nicht gesetzt."""
    try:
        from core import settings as _settings
        v = _settings.get(key)
        if v not in ("", 0, None):
            return v
    except Exception:
        # Einstellungen nicht lesbar (z. B. sehr frueh beim Start) — dann
        # zaehlt die Umgebung. Mailversand darf daran nicht scheitern.
        logger.debug("settings unreadable for %s, using env", key, exc_info=True)
    return _ENV_FALLBACK[key]()


def config_source() -> str:
    """'settings' | 'env' | 'none' — fuer die Anzeige im Einstellungsdialog."""
    try:
        from core import settings as _settings
        if _settings.get("smtp_host"):
            return "settings"
    except Exception:
        pass
    return "env" if config.SMTP_HOST else "none"


class MailerNotConfigured(Exception):
    """SMTP credentials missing or incomplete."""


# (cid_without_angle_brackets, image_bytes, mime_subtype) — subtype e.g. "jpeg", "png"
InlineImage = Tuple[str, bytes, str]


def is_configured() -> bool:
    """True if host/user/pass/from-addr are all set (settings or env)."""
    return bool(_val("smtp_host") and _val("smtp_user")
                and _val("smtp_pass") and _val("smtp_from_addr"))


def send(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    inline_images: Optional[List[InlineImage]] = None,
    reply_to: Optional[str] = None,
    timeout: int = 15,
) -> None:
    """
    Send a mail. If body_html is set, a multipart/alternative mail is
    produced; body_text stays as fallback for clients that don't
    render HTML. inline_images are bound to the HTML part via
    Content-ID (<img src="cid:NAME">).
    """
    if not is_configured():
        raise MailerNotConfigured(
            "SMTP unvollstaendig — Einstellungen (Mailversand) oder "
            "MPD_SMTP_HOST/USER/PASS/FROM_ADDR in mpd.env"
        )

    host      = _val("smtp_host")
    port      = int(_val("smtp_port") or 587)
    user      = _val("smtp_user")
    password  = _val("smtp_pass")
    from_addr = _val("smtp_from_addr")
    from_name = _val("smtp_from_name")

    msg = EmailMessage()
    msg["From"]    = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg["To"]      = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body_text)

    if body_html:
        msg.add_alternative(body_html, subtype="html")
        if inline_images:
            # Find HTML part and attach images as related so they
            # are referenceable via cid:.
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    for cid, img_bytes, subtype in inline_images:
                        part.add_related(
                            img_bytes,
                            maintype="image",
                            subtype=subtype,
                            cid=f"<{cid}>",
                        )
                    break

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        smtp.starttls(context=context)
        smtp.login(user, password)
        smtp.send_message(msg)
    logger.info("Mail sent to %s (subject: %r)", to, subject)
