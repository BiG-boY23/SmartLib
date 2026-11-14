from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import logging
from threading import Lock
from time import monotonic
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from models import RateLimitBucket

logger = logging.getLogger(__name__)

_events = defaultdict(deque)
_lock = Lock()


def _enforce_database_limit(key: str, limit: int, window_seconds: int, db: Session) -> None:
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    window_start = epoch - epoch % window_seconds
    bucket_key = hashlib.sha256(f"{key}:{window_seconds}:{window_start}".encode("utf-8")).hexdigest()
    # Persist timezone-naive UTC consistently with the existing SQLAlchemy models.
    expires_at = datetime.fromtimestamp(window_start + window_seconds, timezone.utc).replace(tzinfo=None)
    now_db = now.replace(tzinfo=None)
    try:
        if db.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif db.bind.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise NotImplementedError("Shared database rate limits require SQLite or PostgreSQL")
        statement = insert(RateLimitBucket).values(
            bucket_key=bucket_key, hits=1, expires_at=expires_at
        ).on_conflict_do_update(
            index_elements=[RateLimitBucket.bucket_key],
            set_={"hits": RateLimitBucket.hits + 1},
        ).returning(RateLimitBucket.hits)
        hits = db.execute(statement).scalar_one()
        db.query(RateLimitBucket).filter(RateLimitBucket.expires_at <= now_db).delete(synchronize_session=False)
        db.commit()
    except NotImplementedError:
        db.rollback()
        _enforce_process_limit(key, limit, window_seconds)
        return
    except Exception as exc:
        db.rollback()
        logger.exception("Could not update shared request rate limit")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Request protection is temporarily unavailable. Try again shortly.",
        ) from exc
    if hits > limit:
        retry_after = max(1, window_start + window_seconds - epoch)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def _enforce_process_limit(key: str, limit: int, window_seconds: int) -> None:
    now = monotonic()
    cutoff = now - window_seconds
    with _lock:
        events = _events[key]
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= limit:
            retry_after = max(1, int(window_seconds - (now - events[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait and try again.",
                headers={"Retry-After": str(retry_after)},
            )
        events.append(now)

        if len(_events) > 4096:
            expired_keys = [
                stored_key
                for stored_key, stored_events in _events.items()
                if not stored_events or stored_events[-1] <= cutoff
            ]
            for stored_key in expired_keys:
                _events.pop(stored_key, None)


def enforce_rate_limit(key: str, limit: int, window_seconds: int, db: Optional[Session] = None) -> None:
    """Use shared persistent counters when the route has a database session."""
    if db is not None:
        _enforce_database_limit(key, limit, window_seconds, db)
    else:
        _enforce_process_limit(key, limit, window_seconds)
