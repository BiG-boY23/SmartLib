from datetime import datetime, timedelta
from typing import Optional, List
import logging
import os
import secrets
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole, UserSession

logger = logging.getLogger(__name__)
SECRET_KEY = os.getenv("SMARTLIB_SECRET_KEY")
if not SECRET_KEY:
    if os.getenv("SMARTLIB_ENV", "development").lower() == "production":
        raise RuntimeError("SMARTLIB_SECRET_KEY must be configured in production")
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning("SMARTLIB_SECRET_KEY is not set; using an ephemeral development key")
ALGORITHM = "HS256"
IDLE_TIMEOUT_MINUTES = 15
SESSION_ABSOLUTE_HOURS = 12
REMEMBERED_SESSION_ABSOLUTE_DAYS = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    # Truncate to 72 bytes max for bcrypt standard compliance
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


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
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
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
