from typing import List, Optional
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
from rate_limits import enforce_rate_limit
from security import require_roles, get_current_user

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
    if not book:
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
    record.accession_no = record.book.accession_no if record.book else "Unknown"
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
    record.status = BorrowStatus.CANCELLED
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
        "department": current_user.department or "EVSU Main Campus",
        "is_active": current_user.is_active,
        "active_borrows_count": active_borrows_count,
        "qr_payload": qr_payload,
        "issued_at": issued_at,
    }


@router.get("/announcements")
def list_announcements(db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    posts = db.query(Announcement).filter(Announcement.is_active.is_(True)).order_by(Announcement.created_at.desc()).limit(30).all()
    return [{"id": p.id, "title": p.title, "body": p.body, "created_at": p.created_at} for p in posts]


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
        "id": thread.id, "subject": thread.subject, "is_closed": thread.is_closed,
        "updated_at": thread.updated_at,
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
    threads = db.query(SupportThread).filter(SupportThread.user_id == current_user.id).order_by(SupportThread.updated_at.desc()).all()
    return [_thread_summary(db, thread) for thread in threads]


@router.post("/messages", status_code=status.HTTP_201_CREATED)
def start_message_thread(req: SupportThreadCreate, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    enforce_rate_limit(f"support-thread:{current_user.id}", 5, 60 * 60)
    now = datetime.utcnow()
    thread = SupportThread(user_id=current_user.id, subject=req.subject.strip(), created_at=now, updated_at=now)
    db.add(thread)
    db.flush()
    db.add(SupportMessage(thread_id=thread.id, sender_id=current_user.id, is_staff=False, body=req.message.strip()))
    staff = db.query(User).filter(User.role.in_([UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.LIBRARIAN]), User.is_active.is_(True)).all()
    for person in staff:
        db.add(Notification(user_id=person.id, title="New library message", body=f"{current_user.full_name}: {req.subject.strip()}", kind="message"))
    db.commit()
    return _thread_summary(db, thread)


@router.get("/messages/{thread_id}")
def read_user_message_thread(thread_id: int, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id, SupportThread.user_id == current_user.id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id, SupportMessage.is_staff.is_(True)).update({SupportMessage.read_by_user: True}, synchronize_session=False)
    db.commit()
    messages = db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).order_by(SupportMessage.created_at.asc()).all()
    return {"thread": _thread_summary(db, thread), "messages": [{"id": m.id, "message": m.body, "is_staff": m.is_staff, "created_at": m.created_at} for m in messages]}


@router.post("/messages/{thread_id}/reply", status_code=status.HTTP_201_CREATED)
def reply_to_staff_thread(thread_id: int, req: SupportMessageCreate, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    enforce_rate_limit(f"support-reply:{current_user.id}", 20, 60 * 60)
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id, SupportThread.user_id == current_user.id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if thread.is_closed:
        raise HTTPException(status_code=400, detail="This conversation is closed")
    thread.updated_at = datetime.utcnow()
    db.add(SupportMessage(thread_id=thread.id, sender_id=current_user.id, is_staff=False, body=req.message.strip()))
    staff = db.query(User).filter(User.role.in_([UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.LIBRARIAN]), User.is_active.is_(True)).all()
    for person in staff:
        db.add(Notification(user_id=person.id, title="New library message", body=f"Reply from {current_user.full_name}: {thread.subject}", kind="message"))
    db.commit()
    return {"message": "Your message was sent."}


@router.post("/assistant")
def ask_library_assistant(req: AssistantRequest, db: Session = Depends(get_db), current_user: User = Depends(patron_roles)):
    enforce_rate_limit(f"library-assistant:{current_user.id}", 30, 60 * 60)
    question = req.message.strip()
    lower = question.casefold()
    if any(word in lower for word in ("borrow", "loan", "request", "my book", "return")):
        rows = crud.get_borrow_records(db, user_id=current_user.id)
        summary = "; ".join(f"{row.book_title or 'Book'}: {row.status.value if hasattr(row.status, 'value') else row.status}" for row in rows[:5])
        answer = f"Here is your recent library activity: {summary}." if summary else "You don’t have any borrowing requests yet. Browse the catalog and tap ‘Request a copy’ to start one."
        return {"reply": answer, "books": []}
    if any(word in lower for word in ("open", "hours", "schedule", "when")):
        return {"reply": "Library hours can change during holidays and campus events. Check the latest Library Updates or message the librarian for today’s schedule.", "books": []}
    if any(word in lower for word in ("contact", "librarian", "admin", "help", "inquiry")):
        return {"reply": "I can help with common library questions. For a librarian’s help, open Messages and start a conversation; replies will appear in your notifications.", "books": []}
    ignore = {"find", "search", "book", "books", "about", "recommend", "recommendation", "please", "show", "want", "need", "have", "what", "where", "which", "the", "for", "with"}
    words = [word.strip(".,?!:;()[]{}\"'") for word in lower.split()]
    terms = [word for word in words if len(word) >= 3 and word not in ignore][:5]
    found = {}
    for term in terms:
        for book in crud.get_books(db, search=term, skip=0, limit=5):
            found[book.id] = book
    matches = list(found.values())[:5]
    if matches:
        return {"reply": f"I found {len(matches)} matching title(s) in the library catalog. Tap Explore to view availability or request a copy.", "books": [{"id": b.id, "title": b.title, "author": b.author, "category": b.category, "available_copies": b.available_copies} for b in matches]}
    return {"reply": "I couldn’t find a matching title in the catalog. Try a shorter title, author name, or subject. You can also message the librarian for help.", "books": []}
