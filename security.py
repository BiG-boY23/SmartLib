from datetime import datetime, timedelta
from typing import Optional, List
import base64
import hashlib
import logging
import os
import secrets
from urllib.parse import urlsplit
import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole, UserSession

logger = logging.getLogger(__name__)
SECRET_KEY = os.getenv("SMARTLIB_SECRET_KEY")
if os.getenv("SMARTLIB_ENV", "development").lower() == "production" and (not SECRET_KEY or len(SECRET_KEY) < 32):
    raise RuntimeError("Configure a random SMARTLIB_SECRET_KEY of at least 32 characters in the hosting secret settings")
if not SECRET_KEY:
    if os.getenv("SMARTLIB_ENV", "development").lower() == "production":
        raise RuntimeError("SMARTLIB_SECRET_KEY must be configured in production")
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning("SMARTLIB_SECRET_KEY is not set; using an ephemeral development key")
ALGORITHM = "HS256"
IDLE_TIMEOUT_MINUTES = 15
SESSION_ABSOLUTE_HOURS = 12
REMEMBERED_SESSION_ABSOLUTE_DAYS = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
WEB_SESSION_COOKIE = "smartlib_web_session"


def _require_same_origin_cookie_request(request: Request) -> None:
    """Block cross-origin state changes authenticated by ambient browser cookies."""
    if request.method.upper() in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        origin = referer
    try:
        source = urlsplit(origin or "")
        expected_scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip().lower()
        source_host = source.netloc.casefold()
        request_host = request.headers.get("host", request.url.netloc).casefold()
    except (TypeError, ValueError):
        source_host = ""
        request_host = ""
        expected_scheme = ""
        source = urlsplit("")
    if not source_host or source_host != request_host or source.scheme.lower() != expected_scheme:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-origin session request rejected")


def staff_mfa_required() -> bool:
    """Production staff access always requires MFA; dev can opt in for testing."""
    return os.getenv("SMARTLIB_ENV", "development").lower() == "production" or \
        os.getenv("SMARTLIB_REQUIRE_STAFF_MFA", "false").lower() in {"1", "true", "yes"}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        if hashed_password.startswith("bcrypt-sha256$"):
            digest = hashlib.sha256(b"EVSU-SmartLib-password-v1:" + plain_password.encode("utf-8")).digest()
            encoded = base64.urlsafe_b64encode(digest)
            stored_hash = hashed_password.split("$", 1)[1]
            return bcrypt.checkpw(encoded, stored_hash.encode("utf-8"))
        # Legacy bcrypt hashes remain valid. Successful legacy logins are upgraded.
        return bcrypt.checkpw(plain_password.encode("utf-8")[:72], hashed_password.encode("utf-8"))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    # Pre-hash so bcrypt's 72-byte input limit never silently truncates passwords.
    digest = hashlib.sha256(b"EVSU-SmartLib-password-v1:" + password.encode("utf-8")).digest()
    pwd_bytes = base64.urlsafe_b64encode(digest)
    salt = bcrypt.gensalt()
    return "bcrypt-sha256$" + bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=SESSION_ABSOLUTE_HOURS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_token_payload(token: str) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise credentials_exception


def get_session_id(token: str) -> str:
    session_id = get_token_payload(token).get("sid")
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return session_id


def get_current_user(
    request: Request, token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    if not token:
        token = request.cookies.get(WEB_SESSION_COOKIE)
        if token:
            _require_same_origin_cookie_request(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required",
                            headers={"WWW-Authenticate": "Bearer"})
    payload = get_token_payload(token)
    username: str = payload.get("sub")
    session_id: str = payload.get("sid")
    if not username or not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

    auth_session = db.query(UserSession).filter(UserSession.session_id == session_id).first()
    now = datetime.utcnow()
    if not auth_session or auth_session.revoked_at or auth_session.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    if auth_session.last_activity_at + timedelta(minutes=IDLE_TIMEOUT_MINUTES) <= now:
        auth_session.revoked_at = now
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired after inactivity")

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        auth_session.revoked_at = now
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    if not user.is_active:
        auth_session.revoked_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user account"
        )
    if user.role in (UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.LIBRARIAN) and staff_mfa_required() and not user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Staff multi-factor authentication setup is required. Sign in again to continue setup.")
    auth_session.last_activity_at = now
    db.commit()
    return user


def require_roles(allowed_roles: List[UserRole]):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted. Required roles: {[r.value for r in allowed_roles]}",
            )
        return current_user

    return role_checker
