from datetime import datetime, timedelta
import hashlib
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db
from email_service import (
    EmailConfigurationError,
    EmailDeliveryError,
    ensure_smtp_configured,
    ensure_email_configured,
    public_base_url,
    send_email,
)
from models import EmailActionToken, PendingRegistration, User, UserRole, UserSession
from rate_limits import enforce_rate_limit
from schemas import (
    EmailActionRequest,
    EmailAddressRequest,
    PasswordChangeRequest,
    PasswordResetComplete,
    PasswordResetOtpRequest,
    RegistrationOtpRequest,
    RegisterRequest,
    Token,
    UserResponse,
    UserUpdate,
    validate_password_policy,
)
import crud
from security import (
    IDLE_TIMEOUT_MINUTES,
    REMEMBERED_SESSION_ABSOLUTE_DAYS,
    SESSION_ABSOLUTE_HOURS,
    create_access_token,
    get_current_user,
    get_password_hash,
    get_session_id,
    oauth2_scheme,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _email_configuration_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Email delivery is not configured. Set SMTP_HOST and SMTP_FROM in the project-root .env file, then restart the server.",
    )


def _hash_action_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_action_token(db: Session, user: User, purpose: str, lifetime: timedelta) -> str:
    now = datetime.utcnow()
    db.query(EmailActionToken).filter(
        EmailActionToken.user_id == user.id,
        EmailActionToken.purpose == purpose,
        EmailActionToken.consumed_at.is_(None),
    ).update({EmailActionToken.consumed_at: now}, synchronize_session=False)
    raw_token = secrets.token_urlsafe(32)
    db.add(
        EmailActionToken(
            user_id=user.id,
            token_hash=_hash_action_token(raw_token),
            purpose=purpose,
            expires_at=now + lifetime,
        )
    )
    db.flush()
    return raw_token


def _send_email_action(email: str, purpose: str, token: str) -> None:
    base_url = public_base_url()
    if purpose == "verify_email":
        link = f"{base_url}/verify-email#{token}"
        subject = "Verify your EVSU SmartLib email"
        body = (
            "Welcome to EVSU SmartLib. Verify your email address to activate your account:\n\n"
            f"{link}\n\nThis link expires in 24 hours. If you did not create this account, ignore this email."
        )
    else:
        link = f"{base_url}/reset-password#{token}"
        subject = "Reset your EVSU SmartLib password"
        body = (
            "Use this link to choose a new EVSU SmartLib password:\n\n"
            f"{link}\n\nThis link expires in 1 hour. If you did not request a reset, ignore this email."
        )
    send_email(email, subject, body)


def _new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _send_otp(email: str, purpose: str, otp: str) -> None:
    action = "finish creating your EVSU SmartLib account" if purpose == "registration" else "reset your EVSU SmartLib password"
    send_email(
        email,
        f"Your EVSU SmartLib {purpose.replace('_', ' ')} code",
        f"Your verification code to {action} is {otp}. It expires in 10 minutes. If you did not request this, ignore this email.",
    )


@router.post("/login", response_model=Token)
async def login(request: Request, db: Session = Depends(get_db)):
    username = ""
    password = ""
    remember_me = False
    content_type = request.headers.get("content-type", "")
    try:
        if "application/json" in content_type:
            body = await request.json()
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            remember_me = body.get("remember_me") is True
        else:
            form = await request.form()
            username = str(form.get("username", "")).strip()
            password = str(form.get("password", ""))
            remember_me = str(form.get("remember_me", "")).lower() in {"true", "1", "on", "yes"}
    except Exception:
        pass

    enforce_rate_limit(f"login-ip:{_ip(request)}", 30, 15 * 60)
    enforce_rate_limit(f"login-account:{username.casefold()}", 10, 15 * 60)
    if not username or not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username and password are required")

    user = crud.get_user_by_username(db, username=username)
    if not user:
        user = crud.get_user_by_email(db, email=username.casefold())
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verify your email before signing in.")
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is suspended. Please contact the Library Administrator.",
        )

    now = datetime.utcnow()
    lifetime = timedelta(days=REMEMBERED_SESSION_ABSOLUTE_DAYS) if remember_me else timedelta(hours=SESSION_ABSOLUTE_HOURS)
    session_id = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            session_id=session_id,
            user_id=user.id,
            created_at=now,
            last_activity_at=now,
            expires_at=now + lifetime,
            remember_me=remember_me,
        )
    )
    db.commit()

    access_token = create_access_token(
        data={"sub": user.username, "role": user.role.value, "sid": session_id},
        expires_delta=lifetime,
    )
    crud.log_audit(db, user.username, f"User Authentication Passed ({user.role.value})", "Authorized")
    return {"access_token": access_token, "token_type": "bearer", "role": user.role.value, "username": user.username}


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Direct sign-up is disabled. Request an email code at /auth/register/request-otp and verify it at /auth/register/verify-otp.",
    )


@router.post("/register/request-otp", status_code=status.HTTP_202_ACCEPTED)
def request_registration_otp(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(f"register-otp-ip:{_ip(request)}", 5, 60 * 60)
    email = str(req.email).strip().casefold()
    username = req.username.strip()
    if crud.get_user_by_username(db, username=username):
        raise HTTPException(status_code=400, detail="Username / Institutional ID already registered")
    if crud.get_user_by_email(db, email=email):
        raise HTTPException(status_code=400, detail="Email address already registered")
    try:
        ensure_smtp_configured()
    except EmailConfigurationError:
        raise _email_configuration_error()
    db.query(PendingRegistration).filter(
        (PendingRegistration.email == email) | (PendingRegistration.username == username)
    ).delete(synchronize_session=False)
    otp = _new_otp()
    pending = PendingRegistration(
        username=username,
        email=email,
        hashed_password=get_password_hash(req.password),
        full_name=f"{req.first_name.strip()} {req.last_name.strip()}".strip(),
        role=req.account_type,
        department=req.department,
        otp_hash=_hash_action_token(f"{email}:{otp}"),
        expires_at=datetime.utcnow() + timedelta(minutes=10),
        attempts=0,
    )
    db.add(pending)
    try:
        db.commit()
        _send_otp(email, "registration", otp)
    except EmailConfigurationError:
        db.rollback()
        raise _email_configuration_error()
    except EmailDeliveryError:
        db.rollback()
        logger.exception("Could not deliver registration OTP")
        raise HTTPException(status_code=503, detail="Verification code could not be sent. Please try again later.")
    crud.log_smtp(db, email, "Account Verification OTP", "EVSU SmartLib account verification")
    return {"message": "If the email can be used for registration, a verification code has been sent."}


@router.post("/register/verify-otp", status_code=status.HTTP_201_CREATED)
def verify_registration_otp(req: RegistrationOtpRequest, request: Request, db: Session = Depends(get_db)):
    email = str(req.email).strip().casefold()
    enforce_rate_limit(f"register-verify-ip:{_ip(request)}", 10, 15 * 60)
    pending = db.query(PendingRegistration).filter(PendingRegistration.email == email).first()
    now = datetime.utcnow()
    if not pending or pending.expires_at <= now or pending.attempts >= 5:
        raise HTTPException(status_code=400, detail="This code is invalid or expired. Please request a new one.")
    if not secrets.compare_digest(pending.otp_hash, _hash_action_token(f"{email}:{req.otp}")):
        pending.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="This code is invalid or expired. Please request a new one.")
    if crud.get_user_by_username(db, username=pending.username) or crud.get_user_by_email(db, email=pending.email):
        db.delete(pending)
        db.commit()
        raise HTTPException(status_code=400, detail="This account information is already registered. Please sign in.")
    user = User(
        username=pending.username,
        email=pending.email,
        hashed_password=pending.hashed_password,
        full_name=pending.full_name,
        role=pending.role,
        department=pending.department,
        email_verified=True,
    )
    db.add(user)
    db.delete(pending)
    db.commit()
    crud.log_audit(db, user.username, "Account Created; Email OTP Verified", "Verified")
    return {"message": "Email verified and account created. You can now sign in."}


@router.post("/verify-email")
def verify_email(req: EmailActionRequest, db: Session = Depends(get_db)):
    action = db.query(EmailActionToken).filter(
        EmailActionToken.token_hash == _hash_action_token(req.token),
        EmailActionToken.purpose == "verify_email",
        EmailActionToken.consumed_at.is_(None),
    ).first()
    now = datetime.utcnow()
    if not action or action.expires_at <= now:
        raise HTTPException(status_code=400, detail="This verification link is invalid or expired. Request a new one.")
    user = db.query(User).filter(User.id == action.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="This verification link is invalid or expired.")
    action.consumed_at = now
    user.email_verified = True
    db.commit()
    crud.log_audit(db, user.username, "Email Address Verified", "Verified")
    return {"message": "Email verified. You can now sign in."}


@router.post("/resend-verification")
def resend_verification(req: EmailAddressRequest, request: Request, db: Session = Depends(get_db)):
    email = str(req.email).strip().casefold()
    email_key = hashlib.sha256(email.encode("utf-8")).hexdigest()
    enforce_rate_limit(f"resend-verification-ip:{_ip(request)}", 5, 60 * 60)
    enforce_rate_limit(f"resend-verification-email:{email_key}", 3, 60 * 60)
    try:
        ensure_email_configured()
    except EmailConfigurationError:
        raise _email_configuration_error()
    user = crud.get_user_by_email(db, email=email)
    if not user or user.email_verified:
        return {"message": "If the account needs verification, a new link will be emailed."}
    token = _new_action_token(db, user, "verify_email", timedelta(hours=24))
    db.commit()
    try:
        _send_email_action(user.email, "verify_email", token)
    except EmailDeliveryError:
        logger.exception("Could not resend verification email")
        raise HTTPException(status_code=503, detail="Verification email could not be sent. Please try again later.")
    crud.log_smtp(db, user.email, "Account Verification", "EVSU SmartLib email verification")
    return {"message": "If the account needs verification, a new link will be emailed."}


@router.post("/forgot-password/request-otp")
def request_password_reset_otp(req: EmailAddressRequest, request: Request, db: Session = Depends(get_db)):
    email = str(req.email).strip().casefold()
    email_key = hashlib.sha256(email.encode("utf-8")).hexdigest()
    enforce_rate_limit(f"reset-otp-ip:{_ip(request)}", 5, 15 * 60)
    enforce_rate_limit(f"reset-otp-email:{email_key}", 3, 60 * 60)
    try:
        ensure_smtp_configured()
    except EmailConfigurationError:
        raise _email_configuration_error()
    user = crud.get_user_by_email(db, email=email)
    if user and user.email_verified and user.is_active:
        otp = _new_otp()
        db.query(EmailActionToken).filter(
            EmailActionToken.user_id == user.id,
            EmailActionToken.purpose == "mobile_reset_otp",
            EmailActionToken.consumed_at.is_(None),
        ).update({EmailActionToken.consumed_at: datetime.utcnow()}, synchronize_session=False)
        token_row = EmailActionToken(
            user_id=user.id,
            token_hash=_hash_action_token(f"{user.id}:{otp}"),
            purpose="mobile_reset_otp",
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        db.add(token_row)
        db.commit()
        try:
            _send_otp(user.email, "password_reset", otp)
        except EmailDeliveryError:
            logger.exception("Could not deliver password reset OTP")
            token_row.consumed_at = datetime.utcnow()
            db.commit()
        else:
            crud.log_smtp(db, user.email, "Password Reset OTP", "EVSU SmartLib password reset")
    return {"message": "If an active account uses that email, a verification code will be sent."}


@router.post("/forgot-password/verify-otp")
def verify_password_reset_otp(req: PasswordResetOtpRequest, request: Request, db: Session = Depends(get_db)):
    email = str(req.email).strip().casefold()
    email_key = hashlib.sha256(email.encode("utf-8")).hexdigest()
    enforce_rate_limit(f"reset-verify-ip:{_ip(request)}", 10, 15 * 60)
    enforce_rate_limit(f"reset-verify-email:{email_key}", 8, 15 * 60)
    user = crud.get_user_by_email(db, email=email)
    action = db.query(EmailActionToken).filter(
        EmailActionToken.token_hash == (_hash_action_token(f"{user.id}:{req.otp}") if user else ""),
        EmailActionToken.purpose == "mobile_reset_otp",
        EmailActionToken.consumed_at.is_(None),
        EmailActionToken.expires_at > datetime.utcnow(),
    ).first()
    if not user or not action or action.user_id != user.id:
        raise HTTPException(status_code=400, detail="This code is invalid or expired. Request a new code.")
    action.consumed_at = datetime.utcnow()
    reset_token = secrets.token_urlsafe(32)
    db.add(EmailActionToken(
        user_id=user.id,
        token_hash=_hash_action_token(reset_token),
        purpose="mobile_reset_verified",
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    ))
    db.commit()
    return {"reset_token": reset_token, "message": "Email verified. Choose a new password."}


@router.post("/forgot-password/complete")
def complete_mobile_password_reset(req: PasswordResetComplete, db: Session = Depends(get_db)):
    action = db.query(EmailActionToken).filter(
        EmailActionToken.token_hash == _hash_action_token(req.token),
        EmailActionToken.purpose == "mobile_reset_verified",
        EmailActionToken.consumed_at.is_(None),
        EmailActionToken.expires_at > datetime.utcnow(),
    ).first()
    if not action:
        raise HTTPException(status_code=400, detail="Your verification has expired. Request a new code.")
    user = db.query(User).filter(User.id == action.user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=400, detail="Your verification has expired. Request a new code.")
    now = datetime.utcnow()
    action.consumed_at = now
    user.hashed_password = get_password_hash(req.new_password)
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update(
        {UserSession.revoked_at: now}, synchronize_session=False
    )
    db.commit()
    crud.log_audit(db, user.username, "Password Reset", "Completed via OTP")
    return {"message": "Password changed. Sign in using your new password."}


@router.post("/forgot-password")
def forgot_password(req: EmailAddressRequest, request: Request, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Password reset links are disabled. Request an email code at /auth/forgot-password/request-otp.",
    )


@router.post("/reset-password")
def reset_password(req: PasswordResetComplete, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Password reset links are disabled. Verify an email code, then complete the reset in the app.",
    )


@router.post("/logout")
def logout(
    current_user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    session_id = get_session_id(token)
    auth_session = db.query(UserSession).filter(UserSession.session_id == session_id).first()
    if auth_session and auth_session.user_id == current_user.id and not auth_session.revoked_at:
        auth_session.revoked_at = datetime.utcnow()
        db.commit()
    return {"message": "Signed out."}


@router.get("/me", response_model=UserResponse)
def read_current_user_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/profile", response_model=UserResponse)
def update_profile(
    update_data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updated_user = crud.update_user(db, current_user.id, update_data)
    return updated_user


@router.post("/change-password")
def change_password(
    req: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(req.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect current password")
    try:
        validate_password_policy(req.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    now = datetime.utcnow()
    current_user.hashed_password = get_password_hash(req.new_password)
    db.query(UserSession).filter(UserSession.user_id == current_user.id, UserSession.revoked_at.is_(None)).update(
        {UserSession.revoked_at: now}, synchronize_session=False
    )
    db.commit()
    crud.log_audit(db, current_user.username, "Password Changed; Sessions Revoked", "Authorized")
    return {"message": "Password updated. Sign in again on this and other devices."}
