from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException, status

_events = defaultdict(deque)
_lock = Lock()


def enforce_rate_limit(key: str, limit: int, window_seconds: int) -> None:
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
