import os
import smtplib
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
    required = ["SMTP_HOST", "SMTP_FROM"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise EmailConfigurationError(f"Email delivery is not configured: {', '.join(missing)}")


def send_email(recipient: str, subject: str, body: str) -> None:
    ensure_smtp_configured()
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
        raise EmailDeliveryError("The email service could not send the message") from exc
