"""Transactional activity delivery to every active library staff account."""
from datetime import datetime

from sqlalchemy import Column, DateTime, String

from database import Base
from models import Notification, User, UserRole


STAFF_ROLES = (UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.LIBRARIAN)


class StaffActivityEvent(Base):
    """A stable transition key prevents repeated delivery of the same event."""
    __tablename__ = "staff_activity_events"

    event_key = Column(String(200), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


def notify_staff_activity(db, title, body, kind="activity", *, event_key=None):
    """Queue independent inbox entries without committing or sending email.

    Call only after a successful state transition, before its commit. A rollback
    then rolls back both the transition and delivery. Use a stable event key for
    transitions that can be retried; its unique primary key also prevents two
    competing transactions from committing duplicate event delivery.
    """
    if event_key:
        if db.get(StaffActivityEvent, event_key):
            return []
        db.add(StaffActivityEvent(event_key=event_key))
        db.flush()
    recipients = db.query(User.id).filter(
        User.is_active.is_(True), User.role.in_(STAFF_ROLES)
    ).all()
    notifications = [Notification(user_id=recipient.id, title=title,
                                  body=body, kind=kind) for recipient in recipients]
    db.add_all(notifications)
    return notifications


def notify_borrow_activity(db, record, title=None):
    """Describe an actual circulation transition using its permanent reference."""
    labels = {"PENDING": "New borrow request", "APPROVED": "Borrow request approved",
              "REJECTED": "Borrow request declined", "CHECKED_OUT": "Book borrowed",
              "RETURNED": "Book returned", "CANCELLED": "Borrow request cancelled"}
    state = record.status.value
    patron = record.user.full_name or record.user.username if record.user else "Patron"
    book = record.book.title if record.book else "Library book"
    title = title or labels.get(state, "Borrowing activity")
    return notify_staff_activity(
        db, title, f"{patron}: {book}. {title} ({record.req_id}).", "borrowing",
        event_key=f"borrow:{record.id}:{state}",
    )
