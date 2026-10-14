"""Durable email delivery and due-date reminders; all times are stored in UTC."""
import logging
from datetime import datetime, timedelta
from sqlalchemy import or_, and_
from models import LibraryEmail, Notification, BorrowRecord, BorrowStatus, SmtpLog
from email_service import send_email

logger = logging.getLogger(__name__)


def queue_email(db, key, patron, subject, body):
    if not db.query(LibraryEmail.id).filter_by(event_key=key).first():
        db.add(LibraryEmail(event_key=key, recipient=patron.email, subject=" ".join(subject.split()),
                            body=f"Dear {patron.full_name or patron.username},\n\n{body}\n\nEVSU Ormoc City Campus Library"))


def queue_due_reminders(db, now=None):
    now = now or datetime.utcnow()
    loans = db.query(BorrowRecord).filter(BorrowRecord.status.in_([BorrowStatus.CHECKED_OUT, BorrowStatus.OVERDUE]),
        BorrowRecord.due_date <= now + timedelta(hours=24), BorrowRecord.due_date.isnot(None)).all()
    for loan in loans:
        key = f"due:{loan.id}:{loan.due_date.isoformat()}"
        if db.query(LibraryEmail.id).filter_by(event_key=key).first():
            continue
        if not loan.user or not loan.user.is_active:
            continue
        subject = "Library loan overdue" if loan.due_date < now else "Library loan due soon"
        body = (f"{loan.book.title} ({loan.req_id}) is due {loan.due_date:%B %d, %Y at %H:%M UTC}. "
                "Need more time? Open Messages in SmartLib and ask the librarian for a loan extension. "
                "Your current due date remains in effect until the librarian confirms an extension.")
        db.add(Notification(user_id=loan.user_id, title=subject, body=body, kind="borrowing"))
        queue_email(db, key, loan.user, subject, body)
    db.commit()


def deliver_emails(db, now=None):
    now = now or datetime.utcnow()
    rows = db.query(LibraryEmail).filter(or_(
        and_(LibraryEmail.status.in_(["pending", "failed"]), LibraryEmail.next_attempt_at <= now),
        and_(LibraryEmail.status == "sending", LibraryEmail.next_attempt_at <= now)),
        LibraryEmail.attempts < 8).order_by(LibraryEmail.id).limit(20).all()
    for row in rows:
        claimed = db.query(LibraryEmail).filter(LibraryEmail.id == row.id,
            LibraryEmail.status == row.status, LibraryEmail.next_attempt_at <= now).update(
            {LibraryEmail.status: "sending", LibraryEmail.attempts: LibraryEmail.attempts + 1,
             LibraryEmail.next_attempt_at: now + timedelta(minutes=10)}, synchronize_session=False)
        db.commit()
        if not claimed:
            continue
        db.refresh(row)
        try:
            send_email(row.recipient, row.subject, row.body)
            row.status = "sent"
            row.last_error = None
        except Exception as exc:
            row.status = "failed"
            row.last_error = type(exc).__name__
            row.next_attempt_at = now + timedelta(minutes=min(60, 2 ** row.attempts))
            logger.warning("Library email %s failed: %s", row.id, type(exc).__name__)
        delivery_status = "Delivered" if row.status == "sent" else (
            "Failed; retries exhausted" if row.attempts >= 8 else "Failed; queued for retry")
        db.add(SmtpLog(recipient=row.recipient, event_type="Library notification", subject=row.subject,
                       status=delivery_status))
        db.commit()


def process_notifications(session_factory):
    with session_factory() as db:
        queue_due_reminders(db)
        deliver_emails(db)
