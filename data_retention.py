"""Remove expired authentication and registration artifacts.

Long-lived library records are intentionally not purged here: their retention
periods must come from an approved university policy before deletion is enabled.
"""
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models import (
    EmailActionToken,
    MfaChallenge,
    PendingRegistration,
    RateLimitBucket,
    UserSession,
)


def purge_expired_transient_data(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Delete expired one-time auth data and unusable session/rate-limit rows."""
    now = now or datetime.utcnow()
    counts = {
        "pending_registrations": db.query(PendingRegistration).filter(
            PendingRegistration.expires_at <= now
        ).delete(synchronize_session=False),
        "email_action_tokens": db.query(EmailActionToken).filter(or_(
            EmailActionToken.expires_at <= now,
            EmailActionToken.consumed_at.is_not(None),
        )).delete(synchronize_session=False),
        "mfa_challenges": db.query(MfaChallenge).filter(or_(
            MfaChallenge.expires_at <= now,
            MfaChallenge.consumed_at.is_not(None),
        )).delete(synchronize_session=False),
        "auth_sessions": db.query(UserSession).filter(or_(
            UserSession.expires_at <= now,
            UserSession.revoked_at.is_not(None),
        )).delete(synchronize_session=False),
        "rate_limit_buckets": db.query(RateLimitBucket).filter(
            RateLimitBucket.expires_at <= now
        ).delete(synchronize_session=False),
    }
    db.commit()
    return counts


def purge_expired_transient_data_with_factory(session_factory) -> dict[str, int]:
    """Run cleanup with a short-lived database session, rolling back on failure."""
    with session_factory() as db:
        try:
            return purge_expired_transient_data(db)
        except Exception:
            db.rollback()
            raise
