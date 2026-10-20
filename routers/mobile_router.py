from typing import List, Optional
import string
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Announcement, Notification, SupportMessage, SupportThread, User,
    UserRole, Book, BorrowRecord, BorrowStatus,
)
from schemas import (
    BookResponse,
    BorrowRequestCreate,
    BorrowRecordResponse,
    AcquisitionCreate,
    AcquisitionResponse,
    DigitalIDPayload,
    SupportThreadCreate,
    SupportMessageCreate,
    AssistantRequest,
)
import crud
from staff_activity import notify_borrow_activity
from ai_service import answer_library_question
from assistant_local import catalog_terms, conversational_reply
from rate_limits import enforce_rate_limit
from security import require_roles, get_current_user
from archive_service import archive_keys, cancel_support_emails, claim_support_thread, retain_support, snapshot_time

router = APIRouter(prefix="/mobile", tags=["Mobile Client App (Student & Faculty)"])

patron_roles = require_roles([UserRole.STUDENT, UserRole.FACULTY, UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.LIBRARIAN])


@router.get("/books", response_model=List[BookResponse])
def search_catalog_mobile(
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(patron_roles),
):
    return crud.get_books(db, search=search, skip=skip, limit=limit)


@router.get("/books/{book_id}", response_model=BookResponse)
def get_book_detail_mobile(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(patron_roles),
):
    book = crud.get_book(db, book_id)
    if not book or book.is_archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )
    return book


@router.post("/borrow", response_model=BorrowRecordResponse, status_code=status.HTTP_201_CREATED)
def request_borrow_mobile(
    req_data: BorrowRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(patron_roles),
):
    record, msg = crud.create_borrow_request(db, current_user, req_data)
    if not record:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    db.add(Notification(user_id=current_user.id, title="Borrow request received", body=f"The library received your request for {record.book.title if record.book else 'a book'}.", kind="borrowing"))
    db.commit()
    record.user_name = current_user.full_name
    record.book_title = record.book.title if record.book else "Unknown"
    record.accession_no = record.copy_accession_no or (record.book.accession_no if record.book else "Unknown")
    return record


@router.get("/my-borrowings", response_model=List[BorrowRecordResponse])
def get_my_borrowings(
    db: Session = Depends(get_db),
    current_user: User = Depends(patron_roles),
):
    return crud.get_borrow_records(db, user_id=current_user.id)


@router.post("/borrow/{borrow_id}/cancel", response_model=BorrowRecordResponse)
def cancel_my_borrow_request(
    borrow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(patron_roles),
):
    record = (
        db.query(BorrowRecord)
        .filter(BorrowRecord.id == borrow_id, BorrowRecord.user_id == current_user.id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Borrow request not found")
    if record.status != BorrowStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only pending requests can be cancelled. Contact the library if the request has already been approved.")
    changed = db.query(BorrowRecord).filter(
        BorrowRecord.id == record.id, BorrowRecord.status == BorrowStatus.PENDING
    ).update({BorrowRecord.status: BorrowStatus.CANCELLED}, synchronize_session=False)
    if not changed:
        db.rollback()
        raise HTTPException(status_code=409, detail="The request has changed. Refresh your borrowing list.")
    db.refresh(record)
    notify_borrow_activity(db, record)
    db.add(Notification(user_id=current_user.id, title="Borrow request cancelled", body=f"Your request for {record.book.title if record.book else 'a book'} was cancelled.", kind="borrowing"))
    db.commit()
    db.refresh(record)
    return record


@router.post("/acquisitions", response_model=AcquisitionResponse, status_code=status.HTTP_201_CREATED)
def suggest_book_acquisition(
    acq_data: AcquisitionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(patron_roles),
):
    acq = crud.create_acquisition(db, current_user, acq_data)
    acq.requester_name = current_user.full_name
    return acq


@router.get("/profile/digital-id", response_model=DigitalIDPayload)
def get_digital_student_id(
    db: Session = Depends(get_db),
    current_user: User = Depends(patron_roles),
):
    active_borrows_count = (
        db.query(BorrowRecord)
        .filter(
            BorrowRecord.user_id == current_user.id,
            BorrowRecord.status.in_([BorrowStatus.APPROVED, BorrowStatus.CHECKED_OUT]),
        )
        .count()
    )

    qr_payload = f"EVSU-CARD-2026::{current_user.username}::{current_user.role.value}"
    issued_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "patron_id": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role.value.upper(),
        "department": current_user.department or "EVSU Ormoc City Campus",
        "course": current_user.course,
        "is_active": current_user.is_active,
        "active_borrows_count": active_borrows_count,
        "qr_payload": qr_payload,
        "issued_at": issued_at,
    }


@router.get("/profile/digital-id/image")
def download_library_id(current_user: User = Depends(patron_roles)):
    from fastapi.responses import Response
    from reporting import library_id_png
    return Response(library_id_png(current_user), media_type="image/png", headers={
        "Content-Disposition": 'attachment; filename="SmartLib-library-id.png"', "Cache-Control": "no-store"})


@router.get("/announcements")
def list_announcements(db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    keys = archive_keys(db, "announcements")
    posts = db.query(Announcement).filter(Announcement.is_active.is_(True)).order_by(Announcement.created_at.desc()).limit(30).all()
    return [{"id": p.id, "title": p.title, "body": p.body, "created_at": p.created_at} for p in posts if str(p.id) not in keys]


@router.get("/notifications")
def list_notifications(db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    rows = db.query(Notification).filter(Notification.user_id == current_user.id).order_by(Notification.created_at.desc()).limit(100).all()
    return {"unread_count": sum(row.read_at is None for row in rows), "items": [
        {"id": row.id, "title": row.title, "body": row.body, "kind": row.kind, "created_at": row.created_at, "read": row.read_at is not None}
        for row in rows
    ]}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    row = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == current_user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    if row.read_at is None:
        row.read_at = datetime.utcnow()
        db.commit()
    return {"message": "Notification marked as read."}


def _thread_summary(db: Session, thread: SupportThread, staff_view: bool = False):
    latest = db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).order_by(SupportMessage.created_at.desc()).first()
    owner = db.query(User).filter(User.id == thread.user_id).first() if staff_view else None
    return {
        "id": thread.id, "recipient_role": thread.recipient_role, "subject": thread.subject, "is_closed": thread.is_closed,
        "created_at": thread.created_at, "updated_at": thread.updated_at,
        "last_message": latest.body if latest else "",
        "unread_count": db.query(SupportMessage).filter(
            SupportMessage.thread_id == thread.id,
            SupportMessage.is_staff.is_(not staff_view),
            (SupportMessage.read_by_staff.is_(False) if staff_view else SupportMessage.read_by_user.is_(False)),
        ).count(),
        "patron_name": owner.full_name if owner else None,
        "patron_email": owner.email if owner else None,
    }


@router.get("/messages")
def list_user_message_threads(db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    keys = archive_keys(db, "messages")
    threads = db.query(SupportThread).filter(SupportThread.user_id == current_user.id).order_by(SupportThread.updated_at.desc()).all()
    return [_thread_summary(db, thread) for thread in threads if str(thread.id) not in keys]


def _patron_support_thread(db, thread_id, current_user):
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id, SupportThread.user_id == current_user.id).first()
    if not thread or str(thread.id) in archive_keys(db, "messages"):
        raise HTTPException(404, "Conversation not found")
    return thread


@router.post("/messages", status_code=status.HTTP_201_CREATED)
def start_message_thread(req: SupportThreadCreate, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    if len(req.subject.strip()) < 3 or not req.message.strip():
        raise HTTPException(status_code=422, detail="Enter a subject and message.")
    enforce_rate_limit(f"support-thread:{current_user.id}", 5, 60 * 60)
    now = datetime.utcnow()
    thread = SupportThread(user_id=current_user.id, recipient_role=req.recipient_role, subject=req.subject.strip(), created_at=now, updated_at=now)
    db.add(thread)
    db.flush()
    db.add(SupportMessage(thread_id=thread.id, sender_id=current_user.id, is_staff=False, body=req.message.strip()))
    staff = db.query(User).filter(User.role.in_([UserRole.LIBRARIAN] if thread.recipient_role == "librarian" else [UserRole.ADMIN, UserRole.SUPERADMIN]), User.is_active.is_(True)).all()
    for person in staff:
        db.add(Notification(user_id=person.id, title="New library message", body=f"{current_user.full_name}: {req.subject.strip()}", kind="message"))
    db.commit()
    return _thread_summary(db, thread)


@router.get("/messages/{thread_id}")
def read_user_message_thread(thread_id: int, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    thread = _patron_support_thread(db, thread_id, current_user)
    db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id, SupportMessage.is_staff.is_(True)).update({SupportMessage.read_by_user: True}, synchronize_session=False)
    db.commit()
    messages = db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).order_by(SupportMessage.created_at.asc()).all()
    senders = {user.id: user.full_name for user in db.query(User).filter(User.id.in_([m.sender_id for m in messages])).all()} if messages else {}
    return {"thread": _thread_summary(db, thread), "messages": [{"id": m.id, "message": m.body, "is_staff": m.is_staff, "sender_id": m.sender_id, "sender_name": senders.get(m.sender_id, "Library staff" if m.is_staff else "Patron"), "created_at": m.created_at} for m in messages]}


@router.post("/messages/{thread_id}/reply", status_code=status.HTTP_201_CREATED)
def reply_to_staff_thread(thread_id: int, req: SupportMessageCreate, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="Enter a message.")
    enforce_rate_limit(f"support-reply:{current_user.id}", 20, 60 * 60)
    thread = _patron_support_thread(db, thread_id, current_user)
    if thread.is_closed:
        raise HTTPException(status_code=400, detail="This conversation is closed")
    thread.updated_at = datetime.utcnow()
    db.add(SupportMessage(thread_id=thread.id, sender_id=current_user.id, is_staff=False, body=req.message.strip()))
    staff = db.query(User).filter(User.role.in_([UserRole.LIBRARIAN] if thread.recipient_role == "librarian" else [UserRole.ADMIN, UserRole.SUPERADMIN]), User.is_active.is_(True)).all()
    for person in staff:
        db.add(Notification(user_id=person.id, title="New library message", body=f"Reply from {current_user.full_name}: {thread.subject}", kind="message"))
    db.commit()
    return {"message": "Your message was sent."}


@router.delete("/messages/{thread_id}/messages/{message_id}")
def delete_user_message(thread_id: int, message_id: int,
    expected_created_at: Optional[datetime] = None, expected_thread_created_at: Optional[datetime] = None,
    db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    from models import AuditLog
    thread = _patron_support_thread(db, thread_id, current_user)
    claim_support_thread(db, thread, expected_thread_created_at)
    message = db.query(SupportMessage).filter(SupportMessage.id == message_id,
        SupportMessage.thread_id == thread.id, SupportMessage.sender_id == current_user.id,
        SupportMessage.is_staff.is_(False)).first()
    if not message:
        db.rollback()
        raise HTTPException(404, "Your message was not found.")
    if expected_created_at is not None and message.created_at != snapshot_time(expected_created_at):
        db.rollback()
        raise HTTPException(409, "This message has changed. Refresh the conversation before deleting it.")
    retain_support(db, thread, [message], current_user, "mobile", "delete_message")
    cancel_support_emails(db, [message.id])
    db.delete(message)
    db.add(AuditLog(user_name=current_user.username,
        event=f"Deleted own message {message_id} from conversation {thread.id}; retained in SuperAdmin archive", status="Archived"))
    db.commit()
    return {"message": "Message moved to the SuperAdmin archive.", "deleted_messages": 1, "archived": True}


def _remove_user_conversation(db, thread, user, keep_thread):
    from models import AuditLog
    messages = db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).order_by(SupportMessage.created_at, SupportMessage.id).all()
    if messages or not keep_thread:
        retain_support(db, thread, messages, user, "mobile", "clear_messages" if keep_thread else "delete_conversation")
    cancel_support_emails(db, [message.id for message in messages])
    db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).delete(synchronize_session=False)
    db.add(AuditLog(user_name=user.username,
        event=f"{'Cleared messages in' if keep_thread else 'Deleted'} own conversation {thread.id}; {len(messages)} messages retained in SuperAdmin archive", status="Archived"))
    if not keep_thread:
        db.delete(thread)
    db.commit()
    return {"message": "Messages moved to the SuperAdmin archive." if keep_thread else "Conversation moved to the SuperAdmin archive.",
        "deleted_messages": len(messages), "archived": True}


@router.delete("/messages/{thread_id}/messages")
def clear_user_conversation(thread_id: int, expected_created_at: Optional[datetime] = None,
    expected_updated_at: Optional[datetime] = None, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    thread = _patron_support_thread(db, thread_id, current_user)
    claim_support_thread(db, thread, expected_created_at, expected_updated_at)
    return _remove_user_conversation(db, thread, current_user, True)


@router.delete("/messages/{thread_id}")
def delete_user_conversation(thread_id: int, expected_created_at: Optional[datetime] = None,
    expected_updated_at: Optional[datetime] = None, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    thread = _patron_support_thread(db, thread_id, current_user)
    claim_support_thread(db, thread, expected_created_at, expected_updated_at)
    return _remove_user_conversation(db, thread, current_user, False)


@router.post("/assistant")
def ask_library_assistant(req: AssistantRequest, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    enforce_rate_limit(f"library-assistant:{current_user.id}", 30, 60 * 60)
    question = req.message.strip()
    lower = question.casefold()
    terms = catalog_terms(question)
    found = {}
    for term in terms:
        for book in crud.get_books(db, search=term, skip=0, limit=5):
            found[book.id] = book
    matches = list(found.values())[:5]
    books = [
        {"id": book.id, "title": book.title, "author": book.author,
         "category": book.category, "available_copies": book.available_copies}
        for book in matches
    ]
    rows = crud.get_borrow_records(db, user_id=current_user.id, limit=5)
    activity = [
        {"request_reference": row.req_id, "title": row.book_title, "status": row.status.value,
         "pickup_schedule": row.pickup_date if row.status in {BorrowStatus.PENDING, BorrowStatus.APPROVED} else None,
         "due_date": row.due_date.isoformat() if row.due_date else None}
        for row in rows
    ]
    model_reply = answer_library_question(
        question,
        [turn.model_dump() for turn in req.history],
        {"matching_books": books, "recent_borrowing": activity},
        language=req.language,
    )
    if model_reply:
        return {"reply": model_reply, "books": books, "source": "model"}

    conversation = conversational_reply(question, [turn.model_dump() for turn in req.history], req.language, books)
    if conversation:
        answer = conversation
    elif any(word in lower for word in ("borrow", "loan", "request", "my book", "return")):
        summary = "; ".join(f"{item['title']}: {item['status']}" for item in activity)
        answer = f"Here is your recent library activity: {summary}." if summary else "You don't have any borrowing requests yet. Browse the catalog and tap 'Request a copy' to start one."
    elif any(word in lower for word in ("fine", "overdue", "late", "penalty")):
        answer = "Loans are due 24 hours after checkout. Late returns accrue PHP 1.50 per started hour, capped at PHP 10 per started 24-hour overdue period. Contact the library for your current balance."
    elif any(word in lower for word in ("open", "hours", "schedule", "when")):
        answer = "Library hours can change during holidays and campus events. Check the latest Library Updates or message the librarian for today's schedule."
    elif any(word in lower for word in ("contact", "librarian", "admin", "help", "inquiry")):
        answer = "Open Messages to start a conversation with the library team. Staff replies will appear in the same conversation."
    elif matches:
        answer = f"I found {len(matches)} matching title(s) in the library catalog. Tap Explore to view availability or request a copy."
    else:
        answer = "I can help with books, borrowing, and library services. What would you like to know?"
    return {"reply": answer, "books": books, "source": "local"}
