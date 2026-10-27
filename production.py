"""Fail closed for known deployment mistakes; never print credential values."""
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit


def public_https_url(value):
    try:
        if any(ord(char) <= 32 for char in value):
            return False
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            return False
        reserved_hosts = {"localhost", "testserver", "example.com", "example.org", "example.net"}
        reserved_suffixes = (".local", ".internal", ".invalid", ".example", ".test",
                             ".example.com", ".example.org", ".example.net")
        if url.hostname in reserved_hosts or url.hostname.endswith(reserved_suffixes):
            return False
        if url.port is not None and not 1 <= url.port <= 65535:
            return False
        try:
            return ipaddress.ip_address(url.hostname).is_global
        except ValueError:
            return "." in url.hostname
    except ValueError:
        return False


def validate_production_settings():
    if os.getenv("SMARTLIB_ENV", "development").lower() != "production":
        return
    errors = []
    if len(os.getenv("SMARTLIB_SECRET_KEY", "")) < 32:
        errors.append("SMARTLIB_SECRET_KEY must be a random secret of at least 32 characters")
    base = os.getenv("PUBLIC_BASE_URL", "")
    if not public_https_url(base):
        errors.append("PUBLIC_BASE_URL must be the real public HTTPS URL")
    else:
        url = urlsplit(base)
        if url.path not in ("", "/") or url.query or url.fragment:
            errors.append("PUBLIC_BASE_URL must be an HTTPS origin without a path, query or fragment")
    database = os.getenv("DATABASE_URL", "")
    if not database:
        errors.append("DATABASE_URL must point to persistent SQLite or PostgreSQL storage")
    elif database.startswith("sqlite:"):
        from sqlalchemy.engine import make_url
        try:
            data = make_url(database).database
            if not data or not Path(data).is_absolute():
                errors.append("SQLite must use an absolute path on the attached persistent volume")
        except Exception:
            errors.append("DATABASE_URL is invalid")
    uploads = os.getenv("SMARTLIB_UPLOAD_DIR", "")
    if not uploads or not Path(uploads).is_absolute():
        errors.append("SMARTLIB_UPLOAD_DIR must be an absolute path on persistent upload storage")
    if os.getenv("SMARTLIB_SEED_DEMO", "false").lower() in {"true", "1", "yes"}:
        errors.append("Demo seeding must be disabled in production")
    if errors:
        raise RuntimeError("Production configuration is incomplete: " + "; ".join(errors))


def prepare_production_accounts(db):
    if os.getenv("SMARTLIB_ENV", "development").lower() != "production":
        return
    from models import User, UserRole
    from security import get_password_hash, verify_password
    from schemas import validate_password_policy

    staff = db.query(User).filter(User.is_active.is_(True), User.role.in_(
        [UserRole.ADMIN, UserRole.SUPERADMIN, UserRole.LIBRARIAN])).all()
    if any(any(verify_password(password, user.hashed_password)
               for password in ("admin123", "password123")) for user in staff):
        raise RuntimeError("An active staff account still uses a known demo password. "
                           "Change it or disable that account before deployment.")
    if any(user.role == UserRole.SUPERADMIN for user in staff):
        return
    username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "").strip()
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().casefold()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not all((username, email, password)):
        raise RuntimeError("For the first deployment, configure BOOTSTRAP_ADMIN_USERNAME, "
                           "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD as hosting secrets.")
    try:
        from pydantic import TypeAdapter, EmailStr
        TypeAdapter(EmailStr).validate_python(email)
        validate_password_policy(password)
    except ValueError:
        raise RuntimeError("Bootstrap email or password does not meet the account policy") from None
    if db.query(User).filter((User.username == username) | (User.email == email)).first():
        raise RuntimeError("Bootstrap account conflicts with an existing user. Choose a different "
                           "account identifier or restore the existing SuperAdmin.")
    db.add(User(username=username, email=email, full_name="Library SuperAdmin",
                hashed_password=get_password_hash(password), role=UserRole.SUPERADMIN,
                is_active=True, email_verified=True))
    db.commit()
