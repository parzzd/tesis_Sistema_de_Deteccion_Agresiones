from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Iterable


RESEND_API_URL = "https://api.resend.com/emails"

DEFAULT_RESEND_FROM = "Sicher <onboarding@resend.dev>"


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "si"}


def email_alerts_enabled() -> bool:
    return _truthy(os.getenv("EMAIL_ALERTS_ENABLED"), default=True)


def resend_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER")))


def _send_via_resend(recipients: list[str], subject: str, body: str) -> dict[str, object]:
    api_key = os.environ["RESEND_API_KEY"]
    sender = os.getenv("RESEND_FROM") or DEFAULT_RESEND_FROM
    payload = json.dumps(
        {"from": sender, "to": recipients, "subject": subject, "text": body}
    ).encode("utf-8")
    request = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "sicher-backend/1.0 (+https://sircher.online)",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
        return {"sent": True, "provider": "resend", "id": data.get("id"), "recipients": recipients}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"sent": False, "provider": "resend", "reason": f"http_{exc.code}", "detail": detail}
    except urllib.error.URLError as exc:
        return {"sent": False, "provider": "resend", "reason": "network_error", "detail": str(exc.reason)}


def unique_recipients(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    recipients: list[str] = []
    for value in values:
        email = (value or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        recipients.append(email)
    return recipients


def send_email(recipients: list[str], subject: str, body: str) -> dict[str, object]:
    if not recipients:
        return {"sent": False, "reason": "no_recipients"}
    if not email_alerts_enabled():
        return {"sent": False, "reason": "disabled"}

    # Resend tiene prioridad si esta configurado; SMTP queda como respaldo.
    if resend_configured():
        return _send_via_resend(recipients, subject, body)

    if not smtp_configured():
        return {"sent": False, "reason": "smtp_not_configured", "recipients": recipients}

    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or user or "alerts@sicher.local"
    use_tls = _truthy(os.getenv("SMTP_TLS"), default=True)
    use_ssl = _truthy(os.getenv("SMTP_SSL"), default=False)

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as smtp:
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)

    return {"sent": True, "recipients": recipients}


def send_alert_email(recipients: list[str], subject: str, body: str) -> dict[str, object]:
    return send_email(recipients, subject, body)
