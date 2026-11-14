from datetime import datetime, timedelta
import hmac
import hashlib
import json
import logging
import secrets
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from PIL import Image, UnidentifiedImageError
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
from models import EmailActionToken, MfaChallenge, PendingRegistration, SignupOption, User, UserRole, UserSession
from rate_limits import enforce_rate_limit
from schemas import (
    EmailActionRequest,
    EmailAddressRequest,
    MfaCodeRequest,
    MfaLoginRequest,
    MfaSetupRequest,
    PasswordChangeRequest,
    PasswordResetComplete,
    PasswordResetOtpRequest,
    RegistrationOtpRequest,
    RegisterRequest,
    Token,
    UserResponse,
    SelfProfileUpdate,
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
    WEB_SESSION_COOKIE,
    staff_mfa_required,
    get_token_payload,
    verify_password,
)
from mfa_security import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    hash_recovery_code,
    matching_totp_counter,
    new_recovery_codes,
    new_totp_secret,
    provisioning_qr,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _email_configuration_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Email verification is temporarily unavailable. Please contact the library.",
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


_STAFF_ROLES = {UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.LIBRARIAN}
_MFA_CHALLENGE_MINUTES = 10
_MFA_MAX_ATTEMPTS = 5


def _new_mfa_challenge(db: Session, user: User, purpose: str, remember_me: bool = False) -> str:
    now = datetime.utcnow()
    challenge_id = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=_MFA_CHALLENGE_MINUTES)
    db.add(MfaChallenge(
        challenge_id=challenge_id,
        user_id=user.id,
        purpose=purpose,
        created_at=now,
        expires_at=expires_at,
        remember_me=remember_me,
    ))
    db.commit()
    return create_access_token(
        {"sub": user.username, "cid": challenge_id, "purpose": f"mfa_{purpose}"},
        expires_delta=timedelta(minutes=_MFA_CHALLENGE_MINUTES),
    )


def _load_mfa_challenge(token: str, purpose: str, db: Session) -> tuple[MfaChallenge, User]:
    payload = get_token_payload(token)
    challenge_id = payload.get("cid")
    if payload.get("purpose") != f"mfa_{purpose}" or not challenge_id:
        raise HTTPException(status_code=401, detail="MFA challenge expired. Sign in again.")
    challenge = db.query(MfaChallenge).filter(MfaChallenge.challenge_id == challenge_id).with_for_update().first()
    now = datetime.utcnow()
    if (not challenge or challenge.purpose != purpose or challenge.consumed_at or
            challenge.expires_at <= now or challenge.attempts >= _MFA_MAX_ATTEMPTS):
        raise HTTPException(status_code=401, detail="MFA challenge expired. Sign in again.")
    user = db.query(User).filter(User.id == challenge.user_id).first()
    if (not user or not user.is_active or user.role not in _STAFF_ROLES or
            user.username != payload.get("sub")):
        raise HTTPException(status_code=401, detail="MFA challenge expired. Sign in again.")
    return challenge, user


def _failed_mfa_attempt(challenge: MfaChallenge, db: Session, message: str = "Invalid verification code."):
    challenge.attempts += 1
    if challenge.attempts >= _MFA_MAX_ATTEMPTS:
        challenge.consumed_at = datetime.utcnow()
    db.commit()
    user = db.query(User).filter(User.id == challenge.user_id).first()
    if user:
        crud.log_audit(db, user.username, "Staff MFA Verification Failed", "Rejected")
    raise HTTPException(status_code=401, detail=message)


def _issue_session(user: User, request: Request, response: Response, db: Session, remember_me: bool = False) -> dict:
    is_staff = user.role in _STAFF_ROLES
    # Staff always use a bounded work-session, even if a browser sent remember_me.
    remember_me = bool(remember_me and not is_staff)
    lifetime = timedelta(days=REMEMBERED_SESSION_ABSOLUTE_DAYS) if remember_me else timedelta(hours=SESSION_ABSOLUTE_HOURS)
    now = datetime.utcnow()
    session_id = secrets.token_urlsafe(32)
    db.add(UserSession(
        session_id=session_id,
        user_id=user.id,
        created_at=now,
        last_activity_at=now,
        expires_at=now + lifetime,
        remember_me=remember_me,
    ))
    db.commit()
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role.value, "sid": session_id},
        expires_delta=lifetime,
    )
    web_client = request.headers.get("x-smartlib-web-session") == "1"
    if web_client:
        response.set_cookie(
            key=WEB_SESSION_COOKIE,
            value=access_token,
            max_age=int(lifetime.total_seconds()) if (remember_me or is_staff) else None,
            httponly=True,
            secure=request.url.scheme == "https" or staff_mfa_required(),
            samesite="strict",
            path="/",
        )
        response_access_token = None
    else:
        response_access_token = access_token
    crud.log_audit(db, user.username, f"User Authentication Passed ({user.role.value})", "Authorized")
    return {
        "access_token": response_access_token,
        "token_type": "bearer",
        "role": user.role.value,
        "username": user.username,
    }


@router.post("/login", response_model=Token)
async def login(request: Request, response: Response, db: Session = Depends(get_db)):
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

    enforce_rate_limit(f"login-ip:{_ip(request)}", 30, 15 * 60, db=db)
    enforce_rate_limit(f"login-account:{username.casefold()}", 10, 15 * 60, db=db)
    if not username or not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username and password are required")
    if len(username) > 254 or len(password) > 128:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credentials are too long")

    user = crud.get_user_by_username(db, username=username)
    if not user:
        user = crud.get_user_by_email(db, email=username.casefold())
    if not user or not verify_password(password, user.hashed_password):
        audited_name = user.username if user else f"unknown:{hashlib.sha256(username.casefold().encode('utf-8')).hexdigest()[:16]}"
        crud.log_audit(db, audited_name, "User Authentication Failed", "Rejected")
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
    if not user.hashed_password.startswith("bcrypt-sha256$"):
        user.hashed_password = get_password_hash(password)
        db.commit()

    if user.role in _STAFF_ROLES and staff_mfa_required():
        if user.mfa_enabled:
            if not user.mfa_secret_enc:
                logger.error("Staff MFA secret missing for user id %s", user.id)
                raise HTTPException(status_code=503, detail="Staff MFA configuration needs administrator attention.")
            response.delete_cookie(WEB_SESSION_COOKIE, path="/", httponly=True, samesite="strict")
            challenge_token = _new_mfa_challenge(db, user, "login")
            return {"role": user.role.value, "username": user.username, "mfa_required": True,
                    "challenge_token": challenge_token}
        response.delete_cookie(WEB_SESSION_COOKIE, path="/", httponly=True, samesite="strict")
        setup_token = _new_mfa_challenge(db, user, "setup")
        return {"role": user.role.value, "username": user.username, "mfa_setup_required": True,
                "setup_token": setup_token}

    return _issue_session(user, request, response, db, remember_me)


@router.post("/mfa/setup")
def begin_mfa_setup(req: MfaSetupRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_rate_limit(f"mfa-setup-ip:{_ip(request)}", 20, 15 * 60, db=db)
    challenge, user = _load_mfa_challenge(req.setup_token, "setup", db)
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="MFA is already enabled. Sign in again.")
    secret = new_totp_secret()
    challenge.pending_secret_enc = encrypt_totp_secret(secret)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"qr_code": provisioning_qr(secret, user.email), "manual_key": secret,
            "expires_in_seconds": _MFA_CHALLENGE_MINUTES * 60}


@router.post("/mfa/enable", response_model=Token)
def enable_mfa(req: MfaCodeRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_rate_limit(f"mfa-setup-ip:{_ip(request)}", 20, 15 * 60, db=db)
    challenge, user = _load_mfa_challenge(req.setup_token, "setup", db)
    if not challenge.pending_secret_enc:
        raise HTTPException(status_code=400, detail="Start MFA setup again before verifying the code.")
    try:
        secret = decrypt_totp_secret(challenge.pending_secret_enc)
    except ValueError:
        logger.exception("Unable to decrypt pending staff MFA secret for user id %s", user.id)
        raise HTTPException(status_code=503, detail="MFA setup is unavailable. Contact the system administrator.")
    counter = matching_totp_counter(secret, req.code)
    if counter is None:
        _failed_mfa_attempt(challenge, db)
    codes = new_recovery_codes()
    user.mfa_enabled = True
    user.mfa_secret_enc = challenge.pending_secret_enc
    user.mfa_recovery_hashes = json.dumps([hash_recovery_code(code) for code in codes])
    user.mfa_last_counter = counter
    # Enrollment invalidates every old bearer token and browser session.
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update(
        {UserSession.revoked_at: datetime.utcnow()}, synchronize_session=False
    )
    challenge.pending_secret_enc = None
    challenge.consumed_at = datetime.utcnow()
    db.commit()
    crud.log_audit(db, user.username, "Staff MFA Enabled", "Authorized")
    result = _issue_session(user, request, response, db)
    result["recovery_codes"] = codes
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/mfa/verify", response_model=Token)
def verify_mfa_login(req: MfaLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_rate_limit(f"mfa-login-ip:{_ip(request)}", 20, 15 * 60, db=db)
    challenge, user = _load_mfa_challenge(req.challenge_token, "login", db)
    if not user.mfa_enabled or not user.mfa_secret_enc:
        raise HTTPException(status_code=403, detail="MFA setup is required. Sign in again to configure it.")
    try:
        secret = decrypt_totp_secret(user.mfa_secret_enc)
    except ValueError:
        logger.exception("Unable to decrypt staff MFA secret for user id %s", user.id)
        raise HTTPException(status_code=503, detail="Staff MFA is unavailable. Contact the system administrator.")

    counter = matching_totp_counter(secret, req.code)
    recovery_hashes = json.loads(user.mfa_recovery_hashes or "[]")
    recovery_index = None
    if counter is not None and user.mfa_last_counter is not None and counter <= user.mfa_last_counter:
        counter = None
    if counter is None and len(req.code.strip()) > 6:
        submitted_hash = hash_recovery_code(req.code)
        for index, stored_hash in enumerate(recovery_hashes):
            if hmac.compare_digest(stored_hash, submitted_hash):
                recovery_index = index
                break
    if counter is None and recovery_index is None:
        _failed_mfa_attempt(challenge, db)

    if recovery_index is not None:
        recovery_hashes.pop(recovery_index)
        user.mfa_recovery_hashes = json.dumps(recovery_hashes)
        crud.log_audit(db, user.username, "Staff MFA Recovery Code Used", "Authorized")
    else:
        user.mfa_last_counter = counter
    challenge.consumed_at = datetime.utcnow()
    db.commit()
    return _issue_session(user, request, response, db, challenge.remember_me)


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Direct sign-up is disabled. Request an email code at /auth/register/request-otp and verify it at /auth/register/verify-otp.",
    )


@router.get("/signup-options")
def get_signup_options(db: Session = Depends(get_db)):
    departments = db.query(SignupOption).filter(
        SignupOption.kind == "department", SignupOption.is_active.is_(True)
    ).order_by(SignupOption.name.asc()).all()
    department_ids = [item.id for item in departments]
    courses = db.query(SignupOption).filter(
        SignupOption.kind == "course", SignupOption.is_active.is_(True),
        SignupOption.department_id.in_(department_ids) if department_ids else SignupOption.id == -1,
    ).order_by(SignupOption.name.asc()).all()
    return {
        "departments": [{"id": item.id, "name": item.name} for item in departments],
        "courses": [{"id": item.id, "name": item.name, "department_id": item.department_id} for item in courses],
    }


@router.post("/register/request-otp", status_code=status.HTTP_202_ACCEPTED)
def request_registration_otp(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    if req.account_type not in (UserRole.STUDENT, UserRole.FACULTY):
        raise HTTPException(status_code=403, detail="Staff accounts must be provisioned by a SuperAdmin")
    enforce_rate_limit(f"register-otp-ip:{_ip(request)}", 5, 60 * 60, db=db)
    email = str(req.email).strip().casefold()
    username = req.username.strip()
    if crud.get_user_by_username(db, username=username):
        raise HTTPException(status_code=400, detail="Username / Institutional ID already registered")
    if crud.get_user_by_email(db, email=email):
        raise HTTPException(status_code=400, detail="Email address already registered")
    department = db.query(SignupOption).filter(
        SignupOption.id == req.department_id, SignupOption.kind == "department", SignupOption.is_active.is_(True)
    ).first() if req.department_id else None
    course = db.query(SignupOption).filter(
        SignupOption.id == req.course_id, SignupOption.kind == "course", SignupOption.is_active.is_(True)
    ).first() if req.course_id else None
    if not department:
        raise HTTPException(status_code=400, detail="Choose a valid department.")
    if req.account_type == UserRole.STUDENT and (not course or course.department_id != department.id):
        raise HTTPException(status_code=400, detail="Choose a course offered by your department.")
    if req.account_type == UserRole.FACULTY:
        course = None
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
        full_name=" ".join(part for part in (req.first_name, req.middle_name, req.last_name) if part),
        first_name=req.first_name,
        middle_name=req.middle_name,
        last_name=req.last_name,
        role=req.account_type,
        department=department.name,
        course=course.name if course else None,
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
    enforce_rate_limit(f"register-verify-ip:{_ip(request)}", 10, 15 * 60, db=db)
    pending = db.query(PendingRegistration).filter(PendingRegistration.email == email).first()
    now = datetime.utcnow()
    if not pending or pending.expires_at <= now or pending.attempts >= 5:
        raise HTTPException(status_code=400, detail="This code is invalid or expired. Please request a new one.")
    if pending.role not in (UserRole.STUDENT, UserRole.FACULTY):
        db.delete(pending)
        db.commit()
        raise HTTPException(status_code=400, detail="Staff accounts must be provisioned by SuperAdmin.")
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
        first_name=pending.first_name,
        middle_name=pending.middle_name,
        last_name=pending.last_name,
        role=pending.role,
        department=pending.department,
        course=pending.course,
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
    enforce_rate_limit(f"resend-verification-ip:{_ip(request)}", 5, 60 * 60, db=db)
    enforce_rate_limit(f"resend-verification-email:{email_key}", 3, 60 * 60, db=db)
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
    enforce_rate_limit(f"reset-otp-ip:{_ip(request)}", 5, 15 * 60, db=db)
    enforce_rate_limit(f"reset-otp-email:{email_key}", 3, 60 * 60, db=db)
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
def verify_password_reset_otp(req: PasswordResetOtpRequest, request: Request, response: Response,
                              db: Session = Depends(get_db)):
    email = str(req.email).strip().casefold()
    email_key = hashlib.sha256(email.encode("utf-8")).hexdigest()
    enforce_rate_limit(f"reset-verify-ip:{_ip(request)}", 10, 15 * 60, db=db)
    enforce_rate_limit(f"reset-verify-email:{email_key}", 8, 15 * 60, db=db)
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
    response.headers["Cache-Control"] = "no-store"
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
    claimed = db.query(EmailActionToken).filter(EmailActionToken.id == action.id,
        EmailActionToken.consumed_at.is_(None), EmailActionToken.expires_at > now).update(
            {EmailActionToken.consumed_at: now}, synchronize_session=False)
    if claimed != 1:
        db.rollback()
        raise HTTPException(400, "Your verification has expired. Request a new code.")
    user.hashed_password = get_password_hash(req.new_password)
    db.query(EmailActionToken).filter(EmailActionToken.user_id == user.id,
        EmailActionToken.purpose.in_(["password_reset", "mobile_reset_otp", "mobile_reset_verified"]),
        EmailActionToken.consumed_at.is_(None)).update({EmailActionToken.consumed_at: now}, synchronize_session=False)
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
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    token = token or request.cookies.get(WEB_SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in required")
    session_id = get_session_id(token)
    auth_session = db.query(UserSession).filter(UserSession.session_id == session_id).first()
    if auth_session and auth_session.user_id == current_user.id and not auth_session.revoked_at:
        auth_session.revoked_at = datetime.utcnow()
        db.commit()
    response.delete_cookie(WEB_SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return {"message": "Signed out."}


@router.get("/me", response_model=UserResponse)
def read_current_user_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/profile", response_model=UserResponse)
def update_profile(
    update_data: SelfProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.first_name = update_data.first_name
    current_user.middle_name = update_data.middle_name
    current_user.last_name = update_data.last_name
    current_user.full_name = " ".join(part for part in (update_data.first_name, update_data.middle_name, update_data.last_name) if part)
    current_user.contact_no = update_data.contact_no
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/profile-photo", response_model=UserResponse)
async def upload_profile_photo(
    photo: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content = await photo.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Profile picture must be 5 MB or smaller.")
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
        with Image.open(BytesIO(content)) as image:
            image_format = image.format
            width, height = image.size
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="Upload a valid JPG or PNG image.")
    if image_format not in {"JPEG", "PNG"} or width > 4096 or height > 4096:
        raise HTTPException(status_code=400, detail="Upload a JPG or PNG image up to 4096 pixels per side.")
    extension = "jpg" if image_format == "JPEG" else "png"
    filename = f"{secrets.token_hex(20)}.{extension}"
    from storage import PROFILE_PHOTOS
    destination = PROFILE_PHOTOS
    photo_path = destination / filename
    temporary_path = destination / f".{filename}.tmp"
    try:
        destination.mkdir(parents=True, exist_ok=True)
        temporary_path.write_bytes(content)
        temporary_path.replace(photo_path)
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Profile photo storage is unavailable. Check that SMARTLIB_UPLOAD_DIR is mounted and writable, then try again.",
        ) from exc
    current_user.profile_picture_url = f"/profile-photos/{filename}"
    try:
        db.commit()
        db.refresh(current_user)
    except Exception:
        db.rollback()
        photo_path.unlink(missing_ok=True)
        raise
    return current_user


@router.post("/change-password")
def change_password(
    req: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.confirm_new_password is not None and req.new_password != req.confirm_new_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")
    if not verify_password(req.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect current password")
    try:
        validate_password_policy(req.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if verify_password(req.new_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Choose a password different from your current password.")
    now = datetime.utcnow()
    current_user.hashed_password = get_password_hash(req.new_password)
    db.query(EmailActionToken).filter(EmailActionToken.user_id == current_user.id,
        EmailActionToken.purpose.in_(["password_reset", "mobile_reset_otp", "mobile_reset_verified"]),
        EmailActionToken.consumed_at.is_(None)).update({EmailActionToken.consumed_at: now}, synchronize_session=False)
    db.query(UserSession).filter(UserSession.user_id == current_user.id, UserSession.revoked_at.is_(None)).update(
        {UserSession.revoked_at: now}, synchronize_session=False
    )
    db.commit()
    crud.log_audit(db, current_user.username, "Password Changed; Sessions Revoked", "Authorized")
    return {"message": "Password updated. Sign in again on this and other devices."}
