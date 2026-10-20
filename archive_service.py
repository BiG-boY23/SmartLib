"""Transaction-bound retention for removed library records and conversations."""
import enum
import json
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import inspect, or_

from models import DeletedArchive, LibraryEmail, StaffArchive, SupportMessage, SupportThread, User, UserRole


def _json_value(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def record_content(record):
    """Snapshot ordinary columns without retaining credentials or auth tokens."""
    forbidden = ("password", "token", "secret", "otp", "session")
    return {column.key: _json_value(getattr(record, column.key))
            for column in inspect(type(record)).columns
            if not any(word in column.key.lower() for word in forbidden)}


def retain_deleted(db, *, kind, record_id, title, actor, origin, action, content):
    entry = DeletedArchive(id=str(uuid.uuid4()), kind=kind, record_id=str(record_id),
        title=title[:250], action=action, origin=origin, deleted_by=actor.username,
        content_json=json.dumps(content, ensure_ascii=False))
    db.add(entry)
    return entry


def archive_keys(db, kind):
    return {entry.key.split(":", 1)[1] for entry in db.query(StaffArchive).filter(
        StaffArchive.key.startswith(f"{kind}:")).all()}


def require_archive_view(user, archive_view):
    if archive_view and user.role != UserRole.SUPERADMIN:
        raise HTTPException(403, "Only SuperAdmin can view the archive.")


def support_snapshot(db, thread, messages):
    owner = db.get(User, thread.user_id)
    sender_ids = {message.sender_id for message in messages}
    senders = {sender.id: {"id": sender.id, "username": sender.username, "full_name": sender.full_name}
               for sender in db.query(User).filter(User.id.in_(sender_ids)).all()} if sender_ids else {}
    archived = db.get(StaffArchive, f"messages:{thread.id}")
    return {"thread": record_content(thread),
            "patron": {"id": owner.id, "username": owner.username, "full_name": owner.full_name} if owner else None,
            "messages": [dict(record_content(message), sender=senders.get(message.sender_id)) for message in messages],
            "previous_archive": record_content(archived) if archived else None}


def retain_support(db, thread, messages, actor, origin, action, reason=None):
    content = support_snapshot(db, thread, messages)
    if reason:
        content["reason"] = reason
    return retain_deleted(db, kind="messages" if action == "delete_message" else "conversations",
        record_id=messages[0].id if action == "delete_message" else thread.id,
        title=thread.subject, actor=actor, origin=origin, action=action,
        content=content)


def snapshot_time(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def claim_support_thread(db, thread, expected_created_at=None, expected_updated_at=None):
    query = db.query(SupportThread).filter(SupportThread.id == thread.id,
        SupportThread.recipient_role == thread.recipient_role, SupportThread.created_at == thread.created_at)
    if expected_created_at is not None:
        query = query.filter(SupportThread.created_at == snapshot_time(expected_created_at))
    if expected_updated_at is not None:
        query = query.filter(SupportThread.updated_at == snapshot_time(expected_updated_at))
    if not query.update({SupportThread.updated_at: datetime.utcnow()}, synchronize_session=False):
        db.rollback()
        raise HTTPException(409, "This conversation has changed. Refresh it before deleting messages.")
    db.refresh(thread)


def cancel_support_emails(db, message_ids):
    for offset in range(0, len(message_ids), 200):
        batch = message_ids[offset:offset + 200]
        db.query(LibraryEmail).filter(LibraryEmail.status.in_(["pending", "failed"]), or_(
            LibraryEmail.event_key.in_([f"reply:{message_id}" for message_id in batch]),
            *(LibraryEmail.event_key.startswith(f"reply:{message_id}:") for message_id in batch),
        )).update({LibraryEmail.status: "cancelled", LibraryEmail.body: ""}, synchronize_session=False)
