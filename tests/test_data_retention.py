from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from data_retention import purge_expired_transient_data
from models import (
    AuditLog,
    EmailActionToken,
    MfaChallenge,
    PendingRegistration,
    RateLimitBucket,
    User,
    UserRole,
    UserSession,
)


def test_transient_retention_removes_only_expired_or_unusable_records(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'retention.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = datetime(2026, 11, 14, 12, 0, 0)

    with factory() as db:
        user = User(username="reader", email="reader@example.test", full_name="Reader",
                    role=UserRole.STUDENT, hashed_password="hashed")
        db.add(user)
        db.flush()

        db.add_all([
            PendingRegistration(username="expired", email="expired@example.test",
                hashed_password="hashed", full_name="Expired", role=UserRole.STUDENT,
                otp_hash="expired-hash", expires_at=now - timedelta(seconds=1),
                created_at=now - timedelta(minutes=11)),
            PendingRegistration(username="fresh", email="fresh@example.test",
                hashed_password="hashed", full_name="Fresh", role=UserRole.STUDENT,
                otp_hash="fresh-hash", expires_at=now + timedelta(minutes=1), created_at=now),
            EmailActionToken(user_id=user.id, token_hash="a" * 64, purpose="verify",
                expires_at=now - timedelta(seconds=1)),
            EmailActionToken(user_id=user.id, token_hash="b" * 64, purpose="verify",
                expires_at=now + timedelta(days=1), consumed_at=now),
            EmailActionToken(user_id=user.id, token_hash="c" * 64, purpose="verify",
                expires_at=now + timedelta(days=1)),
            MfaChallenge(challenge_id="expired", user_id=user.id, purpose="login",
                expires_at=now - timedelta(seconds=1)),
            MfaChallenge(challenge_id="fresh", user_id=user.id, purpose="login",
                expires_at=now + timedelta(minutes=1)),
            UserSession(session_id="expired-session", user_id=user.id,
                last_activity_at=now, expires_at=now - timedelta(seconds=1)),
            UserSession(session_id="revoked-session", user_id=user.id,
                last_activity_at=now, expires_at=now + timedelta(days=1), revoked_at=now),
            UserSession(session_id="active-session", user_id=user.id,
                last_activity_at=now, expires_at=now + timedelta(days=1)),
            RateLimitBucket(bucket_key="a" * 64, hits=3, expires_at=now - timedelta(seconds=1)),
            RateLimitBucket(bucket_key="b" * 64, hits=1, expires_at=now + timedelta(minutes=1)),
            AuditLog(timestamp=now - timedelta(days=3650), user_name="reader",
                event="historical event", status="Success"),
        ])
        db.commit()

        counts = purge_expired_transient_data(db, now=now)

        assert counts == {
            "pending_registrations": 1,
            "email_action_tokens": 2,
            "mfa_challenges": 1,
            "auth_sessions": 2,
            "rate_limit_buckets": 1,
        }
        assert db.query(PendingRegistration).count() == 1
        assert db.query(EmailActionToken).count() == 1
        assert db.query(MfaChallenge).count() == 1
        assert db.query(UserSession).count() == 1
        assert db.query(RateLimitBucket).count() == 1
        # Long-lived audit history is deliberately outside this automatic cleanup.
        assert db.query(AuditLog).count() == 1

    engine.dispose()
