import os
import smtplib
import logging
import httpx
from email.utils import parseaddr

logger = logging.getLogger(__name__)
from email.message import EmailMessage


class EmailConfigurationError(RuntimeError):
    pass


class EmailDeliveryError(RuntimeError):
    pass


def public_base_url() -> str:
    value = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not value or not value.startswith(("https://", "http://")):
        raise EmailConfigurationError("Set PUBLIC_BASE_URL to the app's public HTTPS URL")
    if os.getenv("SMARTLIB_ENV", "development").lower() == "production" and not value.startswith("https://"):
        raise EmailConfigurationError("PUBLIC_BASE_URL must use HTTPS in production")
    return value


def ensure_email_configured() -> None:
    ensure_smtp_configured()
    public_base_url()


def ensure_smtp_configured() -> None:
    """Validate the selected transport (legacy name retained for callers)."""
    provider = os.getenv("EMAIL_PROVIDER", "smtp").strip().lower()
    if provider not in {"smtp", "brevo"}:
        raise EmailConfigurationError("EMAIL_PROVIDER must be smtp or brevo")
    required = ["BREVO_API_KEY", "SMTP_FROM"] if provider == "brevo" else ["SMTP_HOST", "SMTP_FROM"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise EmailConfigurationError(f"Email delivery is not configured: {', '.join(missing)}")
    if provider == "brevo":
        _, address = parseaddr(os.environ["SMTP_FROM"])
        if not address or "@" not in address:
            raise EmailConfigurationError("SMTP_FROM must contain a verified sender email address")


def send_email(recipient: str, subject: str, body: str) -> None:
    ensure_smtp_configured()
    if os.getenv("EMAIL_PROVIDER", "smtp").strip().lower() == "brevo":
        _send_brevo(recipient, subject, body)
        return
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.environ["SMTP_FROM"]
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"}
    use_starttls = os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"}

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)

    try:
        smtp_type = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_type(host, port, timeout=15) as server:
            server.ehlo()
            if use_starttls and not use_ssl:
                server.starttls()
                server.ehlo()
            if username:
                server.login(username, password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("Email delivery failed: provider=smtp category=%s", type(exc).__name__)
        raise EmailDeliveryError("The email service could not send the message") from None


def _send_brevo(recipient: str, subject: str, body: str) -> None:
    name, address = parseaddr(os.environ["SMTP_FROM"])
    if not address or "@" not in address:
        raise EmailConfigurationError("SMTP_FROM must contain a verified sender email address")
    try:
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": os.environ["BREVO_API_KEY"], "accept": "application/json"},
            json={"sender": {"email": address, "name": name or "EVSU SmartLib"},
                  "to": [{"email": recipient}], "subject": subject, "textContent": body},
            timeout=15, follow_redirects=False,
        )
    except httpx.RequestError as exc:
        logger.warning("Email delivery failed: provider=brevo category=%s", type(exc).__name__)
        raise EmailDeliveryError("The email service could not be reached") from None
    if response.status_code != 201:
        # Never log response bodies: they can include addresses or message content.
        logger.warning("Email delivery failed: provider=brevo HTTP=%s", response.status_code)
        raise EmailDeliveryError("The email provider did not accept the message")
