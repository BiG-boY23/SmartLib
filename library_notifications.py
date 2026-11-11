"""Durable email delivery and due-date reminders; all times are stored in UTC."""
import logging
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from sqlalchemy import or_, and_
from models import AuditLog, LibraryEmail, Notification, BorrowRecord, BorrowStatus, SmtpLog
from email_service import send_email
from staff_activity import notify_borrow_activity

logger = logging.getLogger(__name__)
PICKUP_TIMEZONE = ZoneInfo("Asia/Manila")
PICKUP_CLOSING_TIME = time(18, 0)


def queue_email(db, key, patron, subject, body):
    if not db.query(LibraryEmail.id).filter_by(event_key=key).first():
        db.add(LibraryEmail(event_key=key, recipient=patron.email, subject=" ".join(subject.split()),
                            body=f"Dear {patron.full_name or patron.username},\n\n{body}\n\nEVSU Ormoc Campus Library"))


def pickup_expiration_cutoff(pickup_date):
    """Return the scheduled pickup day's 6 PM Philippine-time cutoff."""
    try:
        scheduled = datetime.fromisoformat((pickup_date or "").strip())
    except (TypeError, ValueError):
        return None
    return datetime.combine(scheduled.date(), PICKUP_CLOSING_TIME, tzinfo=PICKUP_TIMEZONE)


def pickup_window_is_closed(pickup_date, now=None):
    cutoff = pickup_expiration_cutoff(pickup_date)
    if cutoff is None:
        return False
    if now is None:
        now = datetime.now(PICKUP_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=PICKUP_TIMEZONE)
    else:
        now = now.astimezone(PICKUP_TIMEZONE)
    return now >= cutoff


def expire_uncollected_approved_requests(db, now=None):
    """Expire approved reservations after closing on their scheduled pickup day.

    The status compare-and-set makes this safe to run repeatedly and prevents
    an expiration worker from overwriting a checkout that already won the race.
    """
    if now is None:
        now = datetime.now(PICKUP_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=PICKUP_TIMEZONE)
    else:
        now = now.astimezone(PICKUP_TIMEZONE)

    candidates = db.query(BorrowRecord).filter(
        BorrowRecord.status == BorrowStatus.APPROVED,
        BorrowRecord.pickup_date.isnot(None),
    ).all()
    expired_count = 0
    for record in candidates:
        if not pickup_window_is_closed(record.pickup_date, now):
            continue

        note = "System: Pickup reservation expired because the item was not checked out by 6:00 PM Philippine time on the scheduled pickup day."
        updated_notes = f"{record.notes.rstrip()}\n{note}" if record.notes and record.notes.strip() else note
        changed = db.query(BorrowRecord).filter(
            BorrowRecord.id == record.id,
            BorrowRecord.status == BorrowStatus.APPROVED,
        ).update(
            {BorrowRecord.status: BorrowStatus.EXPIRED, BorrowRecord.notes: updated_notes},
            synchronize_session=False,
        )
        if not changed:
            continue

        record.status = BorrowStatus.EXPIRED
        record.notes = updated_notes
        title = record.book.title if record.book else "your requested item"
        body = (f"Your approved pickup reservation for {title} ({record.req_id}) expired because it was not checked out by "
                f"6:00 PM Philippine time on the scheduled pickup day. The copy is now available to other patrons. "
                "No checkout or fine was recorded.")
        db.add(Notification(user_id=record.user_id, title="Pickup reservation expired", body=body, kind="borrowing"))
        if record.user and record.user.is_active and record.user.email:
            queue_email(db, f"pickup-expired:{record.id}", record.user,
                        f"Pickup reservation expired — {record.req_id}", body)
        notify_borrow_activity(db, record)
        db.add(AuditLog(user_name="System", event=f"Expired uncollected borrow request {record.req_id}", status="EXPIRED"))
        expired_count += 1

    if expired_count:
        db.commit()
    return expired_count


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
        expire_uncollected_approved_requests(db)
        queue_due_reminders(db)
        deliver_emails(db)
