import os
import logging
import uuid
from typing import List, Optional, Literal
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_

from database import get_db
from models import (
    StaffArchive, User, UserRole, Book, BorrowRecord, BorrowStatus, Fine, AcquisitionSuggestion, SignupOption,
    AuditLog, SmtpLog, Announcement, Notification, SupportMessage, SupportThread, BorrowApprovalEmail, LibraryEmail, LoanExtensionEvent,
)
from schemas import (
    BookCreate,
    BookUpdate,
    BookResponse,
    BookCopyResponse,
    BookCopiesPrintedRequest,
    BorrowRecordResponse,
    BorrowStatusUpdate,
    LoanExtensionRequest,
    CirculationExecuteRequest,
    AcquisitionResponse,
    AcquisitionStatusUpdate,
    UserResponse,
    UserStaffCreate,
    FeedbackResponse,
    AuditLogResponse,
    SmtpLogResponse,
    DashboardStatsResponse,
    AnnouncementCreate,
    SupportMessageCreate,
    StaffThreadCreate,
    SignupOptionCreate,
    SignupOptionUpdate,
    SignupOptionResponse,
)
import crud
from email_service import send_email
from library_notifications import queue_email
from security import require_roles, get_current_user
from archive_service import archive_keys, require_archive_view, record_content, retain_deleted, retain_support

router = APIRouter(prefix="/admin", tags=["Web Dashboard (Admin & Librarian)"])
logger = logging.getLogger(__name__)

# Dependency enforcing ADMIN, LIBRARIAN, or SUPERADMIN
admin_or_staff = require_roles([UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.LIBRARIAN])
superadmin_only = require_roles([UserRole.SUPERADMIN])


@router.get("/dashboard/stats", response_model=DashboardStatsResponse)
def get_dashboard_stats(
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)
):
    from reporting import dashboard_stats
    return dashboard_stats(db)


# Book Management
@router.post("/books", response_model=BookResponse, status_code=status.HTTP_201_CREATED)
def create_new_book(
    book_data: BookCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    if crud.get_book_by_accession(db, book_data.accession_no):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Accession Number {book_data.accession_no} already exists",
        )
    return crud.create_book(db, book_data)


@router.get("/books", response_model=List[BookResponse])
def list_books(
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    archive_view: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    require_archive_view(current_user, archive_view)
    return crud.get_books(db, search=search, skip=skip, limit=limit, archived_only=archive_view)


@router.put("/books/{book_id}", response_model=BookResponse)
def update_book_inventory(
    book_id: int,
    book_update: BookUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    book = crud.get_book(db, book_id)
    if book and book.is_archived and current_user.role != UserRole.SUPERADMIN:
        raise HTTPException(404, "Book not found")
    if book and book_update.is_archived is True and not book.is_archived:
        retain_deleted(db, kind="books", record_id=book.id, title=book.title, actor=current_user,
            origin="staff", action="archive", content={"book": record_content(book),
                "copies": [record_content(copy) for copy in book.copies]})
    try:
        updated = crud.update_book(db, book_id, book_update)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )
    return updated


@router.put("/books/{book_id}/cover", response_model=BookResponse)
async def upload_book_cover(
    book_id: int,
    cover: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    book = crud.get_book(db, book_id)
    if not book or (book.is_archived and current_user.role != UserRole.SUPERADMIN):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    filename = os.path.basename(cover.filename or "")
    extension = os.path.splitext(filename)[1].lower()
    content = await cover.read(5 * 1024 * 1024 + 1)
    await cover.close()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Cover image must be 5 MB or smaller.")
    if extension in {".jpg", ".jpeg"}:
        valid_image = content.startswith(b"\xff\xd8\xff")
        extension = ".jpg"
    elif extension == ".png":
        valid_image = content.startswith(b"\x89PNG\r\n\x1a\n")
    else:
        valid_image = False
    if not valid_image:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload a valid JPG or PNG front-cover image.")

    cover_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "book-covers")
    os.makedirs(cover_dir, exist_ok=True)
    stored_name = f"book-{book.id}-{uuid.uuid4().hex}{extension}"
    stored_path = os.path.join(cover_dir, stored_name)
    with open(stored_path, "wb") as image_file:
        image_file.write(content)
    book.cover_url = f"/book-covers/{stored_name}"
    db.commit()
    db.refresh(book)
    return book


@router.get("/books/{book_id}/copies", response_model=List[BookCopyResponse])
def list_book_copy_qr_records(
    book_id: int,
    unprinted_only: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    book = crud.get_book(db, book_id)
    if not book or (book.is_archived and current_user.role != UserRole.SUPERADMIN):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return crud.get_book_copies(db, book_id, unprinted_only=unprinted_only)


@router.post("/books/{book_id}/copies/mark-printed", response_model=List[BookCopyResponse])
def mark_book_copy_qr_labels_printed(
    book_id: int,
    request_data: BookCopiesPrintedRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    book = crud.get_book(db, book_id)
    if not book or (book.is_archived and current_user.role != UserRole.SUPERADMIN):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return crud.mark_book_copies_printed(db, book_id, request_data.copy_ids)


@router.delete("/books/{book_id}")
def delete_book_inventory(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    book = crud.get_book(db, book_id)
    if not book or book.is_archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )
    retain_deleted(db, kind="books", record_id=book.id, title=book.title, actor=current_user,
        origin="staff", action="delete", content={"book": record_content(book),
            "copies": [record_content(copy) for copy in book.copies]})
    # Retain the original row as well, so historical circulation and copy foreign
    # keys remain valid. Archived books are excluded from active catalog queries.
    book.is_archived = True
    db.add(AuditLog(user_name=current_user.username, event=f"Deleted book {book.accession_no}; retained in SuperAdmin archive", status="Archived"))
    db.commit()
    return {"message": f"Book ID {book_id} moved to the SuperAdmin archive.", "archived": True}


@router.patch("/books/{book_id}/archive", response_model=BookResponse)
def archive_book(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    """Soft-delete: marks book as archived (hidden from active inventory)."""
    updated = crud.get_book(db, book_id)
    if not updated or updated.is_archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    retain_deleted(db, kind="books", record_id=updated.id, title=updated.title, actor=current_user,
        origin="staff", action="archive", content={"book": record_content(updated),
            "copies": [record_content(copy) for copy in updated.copies]})
    updated.is_archived = True
    db.add(AuditLog(user_name=current_user.username, event=f"Archived book {updated.accession_no}", status="Archived"))
    db.commit()
    db.refresh(updated)
    return updated


@router.patch("/books/{book_id}/restore", response_model=BookResponse)
def restore_book(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(superadmin_only),
):
    """Restore an archived book back to active inventory."""
    updated = crud.update_book(db, book_id, __import__("schemas").BookUpdate(is_archived=False))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return updated


@router.get("/books/archived", response_model=List[BookResponse])
def list_archived_books(
    db: Session = Depends(get_db),
    current_user: User = Depends(superadmin_only),
):
    """Returns only archived (soft-deleted) books."""
    return crud.get_books(db, archived_only=True)


# Circulation Desk (Borrow & Return)
@router.get("/circulation/patron/{identifier}")
def lookup_circulation_patron(
    identifier: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    """Fast lookup of student/patron status when scanner reads ID barcode/QR."""
    return crud.get_circulation_patron_info(db, identifier)


@router.get("/circulation/book/{accession_no}")
def lookup_circulation_book(
    accession_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    """Fast lookup of book details when scanner reads book 2D QR label."""
    return crud.get_circulation_book_info(db, accession_no)


@router.post("/circulation/execute")
def execute_circulation_terminal(
    req: CirculationExecuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    return crud.execute_circulation(db, req.mode, req.patron_id, req.accession_no)


# Borrow Requests Management
@router.get("/borrow-requests", response_model=List[BorrowRecordResponse])
def get_all_borrow_requests(
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff),
    status_filter: Optional[BorrowStatus] = None, search: str = "",
    sort: Literal["newest", "oldest", "due", "patron"] = "newest",
    skip: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
):
    query = db.query(BorrowRecord).join(User, BorrowRecord.user_id == User.id).join(Book, BorrowRecord.book_id == Book.id)
    if status_filter:
        query = query.filter(BorrowRecord.status == status_filter)
    if search.strip():
        term = '%' + search.strip() + '%'
        query = query.filter(or_(User.full_name.ilike(term), Book.title.ilike(term),
                                BorrowRecord.req_id.ilike(term), Book.accession_no.ilike(term)))
    order = {"newest": BorrowRecord.request_date.desc(), "oldest": BorrowRecord.request_date.asc(),
             "due": BorrowRecord.due_date.asc().nullslast(), "patron": User.full_name.asc()}[sort]
    records = query.order_by(order, BorrowRecord.id).offset(skip).limit(limit).all()
    for record in records:
        record.user_name = record.user.full_name
        record.book_title = record.book.title
        record.accession_no = record.book.accession_no
    deliveries = {
        item.borrow_record_id: item.status
        for item in db.query(BorrowApprovalEmail).filter(
            BorrowApprovalEmail.borrow_record_id.in_([record.id for record in records])
        ).all()
    } if records else {}
    for record in records:
        record.approval_email_status = deliveries.get(record.id)
    return records


@router.get("/borrow-requests/{req_id}", response_model=BorrowRecordResponse)
def get_staff_borrow_request(req_id: str, db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    record = db.query(BorrowRecord).filter(BorrowRecord.req_id == req_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Loan not found.")
    record.user_name = record.user.full_name
    record.book_title = record.book.title
    record.accession_no = record.book.accession_no
    return record


@router.put("/borrow-requests/{req_id}/status", response_model=BorrowRecordResponse)
def update_request_status(
    req_id: str,
    status_update: BorrowStatusUpdate,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    updated, msg = crud.update_borrow_status(db, req_id, status_update.status, persist=False)
    if not updated:
        code = status.HTTP_404_NOT_FOUND if msg == "Request record not found" else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=msg)
    if updated.status == BorrowStatus.APPROVED:
        db.add(Notification(user_id=updated.user_id, title="Borrow request approved", body=f"Your request {updated.req_id} for {updated.book.title if updated.book else 'a book'} has been approved. Check your pickup schedule in My books.", kind="borrowing"))
        db.add(BorrowApprovalEmail(borrow_record_id=updated.id))
    else:
        db.add(Notification(user_id=updated.user_id, title="Borrow request updated", body=f"Your request for {updated.book.title if updated.book else 'a book'} is now {updated.status.value.replace('_', ' ').lower()}.", kind="borrowing"))
    db.add(AuditLog(user_name=current_user.username, event=f"Updated Borrow Request {req_id} status to {updated.status.value}", status=updated.status.value))
    db.commit()
    if updated.status == BorrowStatus.APPROVED:
        response.headers["X-Approval-Email-Status"] = _deliver_approval_email(db, updated)
    return updated


def _deliver_approval_email(db: Session, record: BorrowRecord) -> str:
    delivery = db.query(BorrowApprovalEmail).filter(BorrowApprovalEmail.borrow_record_id == record.id).first()
    if not delivery or delivery.status == "sent":
        return delivery.status if delivery else "missing"
    now = datetime.utcnow()
    claimed = db.query(BorrowApprovalEmail).filter(
        BorrowApprovalEmail.id == delivery.id,
        or_(BorrowApprovalEmail.status.in_(["pending", "failed"]),
            and_(BorrowApprovalEmail.status == "sending", BorrowApprovalEmail.last_attempt_at < now - timedelta(minutes=5))),
    ).update({BorrowApprovalEmail.status: "sending", BorrowApprovalEmail.attempts: BorrowApprovalEmail.attempts + 1, BorrowApprovalEmail.last_attempt_at: now}, synchronize_session=False)
    db.commit()
    if not claimed:
        return "sending"
    db.refresh(delivery)
    body = (
        f"Dear {record.user.full_name},\n\n"
        "Your borrowing request has been approved.\n\n"
        f"Book title: {record.book.title}\n"
        f"Request reference: {record.req_id}\n"
        "Approval status: Approved\n"
        f"Approval date: {delivery.created_at.strftime('%B %d, %Y at %I:%M %p UTC')}\n"
        f"Pickup schedule: {record.pickup_date or 'Please contact the library to arrange pickup.'}\n\n"
        "Please present your library ID at the circulation desk. The loan period begins when the book is checked out.\n\n"
        "EVSU Ormoc City Campus Library"
    )
    subject = f"Borrow request approved — {record.req_id}"
    try:
        send_email(record.user.email, subject, body)
    except Exception as exc:
        delivery.status = "failed"
        delivery.last_error = str(exc)
        db.commit()
        crud.log_smtp(db, record.user.email, "Borrow Approval", subject, status="Failed: " + str(exc))
        logger.exception("Borrow approval email failed for request %s", record.req_id)
        return "failed"
    delivery.status = "sent"
    delivery.sent_at = datetime.utcnow()
    delivery.last_error = None
    db.commit()
    crud.log_smtp(db, record.user.email, "Borrow Approval", subject, status="Delivered")
    return "sent"


@router.post("/borrow-requests/{req_id}/approval-email/retry")
def retry_approval_email(req_id: str, db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    record = db.query(BorrowRecord).filter(BorrowRecord.req_id == req_id).first()
    if not record or record.status not in {BorrowStatus.APPROVED, BorrowStatus.CHECKED_OUT, BorrowStatus.RETURNED}:
        raise HTTPException(status_code=404, detail="Approved borrow request not found.")
    delivery = db.query(BorrowApprovalEmail).filter(BorrowApprovalEmail.borrow_record_id == record.id).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="No approval email is queued for this request.")
    return {"status": _deliver_approval_email(db, record), "attempts": delivery.attempts}


# Acquisition Suggestions Management
@router.get("/acquisitions", response_model=List[AcquisitionResponse])
def get_all_acquisitions(
    archive_view: bool = False,
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)
):
    require_archive_view(current_user, archive_view)
    keys = archive_keys(db, "acquisitions")
    records = db.query(AcquisitionSuggestion).order_by(AcquisitionSuggestion.created_at.desc()).all()
    records = [record for record in records if (record.acq_id in keys) == archive_view]
    for record in records:
        record.requester_name = record.user.full_name if record.user else "Patron"
    return records


@router.put("/acquisitions/{acq_id}/status", response_model=AcquisitionResponse)
def update_acquisition(
    acq_id: str,
    status_update: AcquisitionStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    if status_update.status not in {"Approved for Purchase", "Declined"}:
        raise HTTPException(status_code=422, detail="Choose Approved for Purchase or Declined.")
    updated = db.query(AcquisitionSuggestion).filter(AcquisitionSuggestion.acq_id == acq_id).first()
    if updated and acq_id in archive_keys(db, "acquisitions") and current_user.role != UserRole.SUPERADMIN:
        raise HTTPException(404, "Acquisition not found")
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Acquisition not found"
        )
    changed = db.query(AcquisitionSuggestion).filter(AcquisitionSuggestion.id == updated.id,
        AcquisitionSuggestion.status == "Under Review").update({AcquisitionSuggestion.status: status_update.status}, synchronize_session=False)
    if not changed:
        db.rollback()
        raise HTTPException(status_code=409, detail="This suggestion has already been reviewed.")
    db.refresh(updated)
    body = f"Your suggestion for '{updated.title}' is now {updated.status}. Approval for purchase does not mean the book is available yet."
    if status_update.reason:
        body += f" Reason: {status_update.reason.strip()[:500]}"
    db.add(Notification(user_id=updated.user_id, title="Book suggestion updated", body=body, kind="suggestion"))
    patron = db.query(User).filter(User.id == updated.user_id).first()
    queue_email(db, f"acquisition:{updated.id}:{updated.status}", patron, "Book suggestion reviewed", body)
    db.add(AuditLog(user_name=current_user.username, event=f"Reviewed acquisition {acq_id}; reason: {(status_update.reason or '').strip()[:500]}", status=updated.status))
    db.commit()
    return updated


@router.post("/borrow-requests/{req_id}/extend", response_model=BorrowRecordResponse)
def extend_loan(req_id: str, req: LoanExtensionRequest, db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    record = db.query(BorrowRecord).filter(BorrowRecord.req_id == req_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Loan not found.")
    if record.status not in {BorrowStatus.CHECKED_OUT, BorrowStatus.OVERDUE} or not record.due_date:
        raise HTTPException(status_code=409, detail="Only an active checked-out loan can be extended.")
    if not req.due_date.tzinfo or len(req.reason.strip()) < 3:
        raise HTTPException(status_code=422, detail="Provide a date with a timezone and an extension reason.")
    due = req.due_date.astimezone(timezone.utc).replace(tzinfo=None)
    old_due = record.due_date
    if due <= max(old_due, datetime.utcnow()):
        raise HTTPException(status_code=422, detail="The new due date must be later than the current due date and now.")
    changed = db.query(BorrowRecord).filter(BorrowRecord.id == record.id, BorrowRecord.due_date == old_due,
        BorrowRecord.status.in_([BorrowStatus.CHECKED_OUT, BorrowStatus.OVERDUE])).update(
        {BorrowRecord.due_date: due, BorrowRecord.status: BorrowStatus.CHECKED_OUT}, synchronize_session=False)
    if not changed:
        db.rollback()
        raise HTTPException(status_code=409, detail="This loan changed. Reload it before extending.")
    db.refresh(record)
    db.query(LibraryEmail).filter(LibraryEmail.event_key == f"due:{record.id}:{old_due.isoformat()}",
        LibraryEmail.status.in_(["pending", "failed"])).update({LibraryEmail.status: "cancelled"}, synchronize_session=False)
    body = f"The loan for {record.book.title} ({req_id}) has been extended to {due:%B %d, %Y at %H:%M UTC}."
    db.add(Notification(user_id=record.user_id, title="Loan extension approved", body=body, kind="borrowing"))
    queue_email(db, f"extension:{record.id}:{due.isoformat()}", record.user, "Loan extension approved", body)
    approved_at = datetime.utcnow()
    reason = req.reason.strip()
    db.add(LoanExtensionEvent(
        borrow_record_id=record.id, patron_id=record.user_id, book_id=record.book_id, actor_id=current_user.id,
        reference=req_id, patron_name=record.user.full_name, institutional_id=record.user.username,
        patron_role=record.user.role.value, department=record.user.department or "Not recorded",
        course=record.user.course or "Not recorded", book_title=record.book.title,
        accession_no=record.book.accession_no, copy_accession_no=record.copy_accession_no,
        actor_name=current_user.full_name, actor_username=current_user.username, actor_role=current_user.role.value,
        old_due_date=old_due, new_due_date=due, reason=reason, approved_at=approved_at))
    db.add(AuditLog(timestamp=approved_at, user_name=current_user.username,
        event=f"Extended {req_id}; patron: {record.user.full_name} ({record.user.username}); "
              f"book: {record.book.title} ({record.copy_accession_no or record.book.accession_no}); "
              f"due UTC: {old_due.isoformat()} to {due.isoformat()}; reason: {reason}", status="Extended"))
    from staff_activity import notify_staff_activity
    notify_staff_activity(db, "Loan period extended",
        f"{record.user.full_name}'s loan for {record.book.title} ({req_id}) was extended by {current_user.full_name} "
        f"from {old_due.isoformat()} UTC to {due.isoformat()} UTC. Reason: {reason}", kind="borrowing",
        event_key=f"loan-extension:{record.id}:{due.isoformat()}")
    db.commit()
    return record


@router.get("/announcements")
def list_library_announcements(archive_view: bool = False, db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    require_archive_view(current_user, archive_view)
    keys = archive_keys(db, "announcements")
    return [post for post in db.query(Announcement).order_by(Announcement.created_at.desc()).all()
            if ((str(post.id) in keys) or not post.is_active) == archive_view]


@router.post("/announcements", status_code=status.HTTP_201_CREATED)
def publish_library_announcement(req: AnnouncementCreate, db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    if len(req.title.strip()) < 3 or not req.body.strip():
        raise HTTPException(status_code=422, detail="Enter an announcement title and details.")
    post = Announcement(title=" ".join(req.title.split()), body=req.body.strip(), created_by=current_user.id)
    db.add(post)
    db.flush()
    patrons = db.query(User).filter(User.role.in_([UserRole.STUDENT, UserRole.FACULTY]), User.is_active.is_(True)).all()
    for patron in patrons:
        db.add(Notification(user_id=patron.id, title="Library announcement", body=post.title, kind="announcement"))
        queue_email(db, f"announcement:{post.id}:{patron.id}", patron, f"Library update: {post.title}", post.body)
    db.commit()
    db.refresh(post)
    return post


def _support_recipient_role(user: User) -> str:
    return "librarian" if user.role == UserRole.LIBRARIAN else "admin"


def _staff_support_thread(db: Session, thread_id: int, current_user: User, archive_view: bool = False) -> SupportThread:
    require_archive_view(current_user, archive_view)
    query = db.query(SupportThread).filter(SupportThread.id == thread_id)
    if not archive_view:
        query = query.filter(SupportThread.recipient_role == _support_recipient_role(current_user))
    thread = query.first()
    if thread and ((str(thread.id) in archive_keys(db, "messages")) != archive_view):
        thread = None
    if not thread:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return thread


def _support_snapshot_time(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _claim_support_thread(
    db: Session, thread: SupportThread,
    expected_created_at: Optional[datetime] = None, expected_updated_at: Optional[datetime] = None,
) -> None:
    query = db.query(SupportThread).filter(
        SupportThread.id == thread.id,
        SupportThread.recipient_role == thread.recipient_role,
        SupportThread.created_at == thread.created_at,
    )
    if expected_created_at is not None:
        query = query.filter(SupportThread.created_at == _support_snapshot_time(expected_created_at))
    if expected_updated_at is not None:
        query = query.filter(SupportThread.updated_at == _support_snapshot_time(expected_updated_at))
    # Claim a write before clearing history, so a simultaneous reply cannot be
    # committed between checking the displayed snapshot and removing messages.
    if not query.update({SupportThread.updated_at: datetime.utcnow()}, synchronize_session=False):
        db.rollback()
        raise HTTPException(status_code=409, detail="This conversation has changed. Refresh it before deleting messages.")
    db.refresh(thread)


def _cancel_unsent_support_emails(db: Session, message_ids: List[int]) -> None:
    # Keep delivery metadata, but stop queued copies of deleted message content.
    # Batch conditions to stay below SQLite's parameter/expression limits.
    for offset in range(0, len(message_ids), 200):
        batch = message_ids[offset:offset + 200]
        db.query(LibraryEmail).filter(
            LibraryEmail.status.in_(["pending", "failed"]),
            or_(
                LibraryEmail.event_key.in_([f"reply:{message_id}" for message_id in batch]),
                *(LibraryEmail.event_key.startswith(f"reply:{message_id}:") for message_id in batch),
            ),
        ).update({LibraryEmail.status: "cancelled", LibraryEmail.body: ""}, synchronize_session=False)


@router.get("/messages")
def list_support_inbox(archive_view: bool = False, db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    require_archive_view(current_user, archive_view)
    keys = archive_keys(db, "messages")
    query = db.query(SupportThread)
    if not archive_view:
        query = query.filter(SupportThread.recipient_role == _support_recipient_role(current_user))
    threads = [thread for thread in query.order_by(SupportThread.updated_at.desc()).all()
               if (str(thread.id) in keys) == archive_view]
    result = []
    for thread in threads:
        patron = db.query(User).filter(User.id == thread.user_id).first()
        latest = db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).order_by(SupportMessage.created_at.desc()).first()
        unread = db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id, SupportMessage.is_staff.is_(False), SupportMessage.read_by_staff.is_(False)).count()
        result.append({"id": thread.id, "subject": thread.subject, "is_closed": thread.is_closed, "created_at": thread.created_at, "updated_at": thread.updated_at, "last_message": latest.body if latest else "", "unread_count": unread, "patron_name": patron.full_name if patron else "Unknown", "patron_email": patron.email if patron else ""})
    return result


@router.post("/messages", status_code=status.HTTP_201_CREATED)
def start_staff_conversation(req: StaffThreadCreate, db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    if len(req.subject.strip()) < 3 or not req.message.strip():
        raise HTTPException(status_code=422, detail="Enter a subject and message.")
    patron = db.query(User).filter(
        User.username == req.recipient_username.strip(),
        User.role.in_([UserRole.STUDENT, UserRole.FACULTY]),
        User.is_active.is_(True),
    ).first()
    if not patron:
        raise HTTPException(status_code=404, detail="Active student or faculty account not found")
    now = datetime.utcnow()
    thread = SupportThread(user_id=patron.id, recipient_role=_support_recipient_role(current_user), subject=req.subject.strip(), created_at=now, updated_at=now)
    db.add(thread)
    db.flush()
    db.add(SupportMessage(thread_id=thread.id, sender_id=current_user.id, is_staff=True, body=req.message.strip()))
    db.add(Notification(user_id=patron.id, title="Message from the library", body=f"A staff member started a conversation: {thread.subject}", kind="message"))
    db.commit()
    return {"id": thread.id, "subject": thread.subject, "patron_name": patron.full_name}


@router.get("/messages/{thread_id}")
def read_support_thread(thread_id: int, archive_view: bool = False, db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    thread = _staff_support_thread(db, thread_id, current_user, archive_view)
    db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id, SupportMessage.is_staff.is_(False)).update({SupportMessage.read_by_staff: True}, synchronize_session=False)
    db.commit()
    patron = db.query(User).filter(User.id == thread.user_id).first()
    messages = db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).order_by(SupportMessage.created_at.asc()).all()
    senders = {user.id: user.full_name for user in db.query(User).filter(User.id.in_([m.sender_id for m in messages])).all()} if messages else {}
    return {"thread": {"id": thread.id, "subject": thread.subject, "is_closed": thread.is_closed, "created_at": thread.created_at, "updated_at": thread.updated_at, "patron_name": patron.full_name if patron else "Unknown", "patron_email": patron.email if patron else ""}, "messages": [{"id": m.id, "message": m.body, "is_staff": m.is_staff, "sender_id": m.sender_id, "sender_name": senders.get(m.sender_id, "Library staff" if m.is_staff else "Patron"), "created_at": m.created_at} for m in messages]}


@router.delete("/messages/{thread_id}/messages/{message_id}")
def delete_support_message(
    thread_id: int, message_id: int,
    expected_created_at: Optional[datetime] = None, expected_thread_created_at: Optional[datetime] = None,
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff),
):
    """Remove a message from active history while retaining its protected snapshot."""
    thread = _staff_support_thread(db, thread_id, current_user)
    _claim_support_thread(db, thread, expected_thread_created_at)
    message = db.query(SupportMessage).filter(
        SupportMessage.id == message_id, SupportMessage.thread_id == thread.id,
    ).first()
    if not message:
        db.rollback()
        raise HTTPException(status_code=404, detail="Message not found")
    if expected_created_at is not None and message.created_at != _support_snapshot_time(expected_created_at):
        db.rollback()
        raise HTTPException(status_code=409, detail="This message has changed. Refresh the conversation before deleting it.")
    _cancel_unsent_support_emails(db, [message.id])
    retain_support(db, thread, [message], current_user, "staff", "delete_message")
    db.delete(message)
    db.add(AuditLog(user_name=current_user.username,
        event=f"Deleted message {message_id} from {thread.recipient_role} conversation {thread.id}", status="Deleted"))
    db.commit()
    return {"message": "Message moved to the SuperAdmin archive.", "deleted_messages": 1, "archived": True}


@router.delete("/messages/{thread_id}/messages")
def clear_support_conversation(
    thread_id: int, expected_created_at: Optional[datetime] = None, expected_updated_at: Optional[datetime] = None,
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff),
):
    """Clear the shared message history while retaining the conversation."""
    thread = _staff_support_thread(db, thread_id, current_user)
    _claim_support_thread(db, thread, expected_created_at, expected_updated_at)
    messages = db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).order_by(SupportMessage.created_at, SupportMessage.id).all()
    message_ids = [message.id for message in messages]
    if messages:
        retain_support(db, thread, messages, current_user, "staff", "clear_messages")
    _cancel_unsent_support_emails(db, message_ids)
    db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).delete(synchronize_session=False)
    db.add(AuditLog(user_name=current_user.username,
        event=f"Cleared {len(message_ids)} messages from {thread.recipient_role} conversation {thread.id}", status="Cleared"))
    db.commit()
    return {"message": "Messages moved to the SuperAdmin archive.", "deleted_messages": len(message_ids), "archived": True}


@router.delete("/messages/{thread_id}")
def delete_support_conversation(
    thread_id: int, expected_created_at: Optional[datetime] = None, expected_updated_at: Optional[datetime] = None,
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff),
):
    """Remove the active conversation and retain its complete protected snapshot."""
    thread = _staff_support_thread(db, thread_id, current_user)
    _claim_support_thread(db, thread, expected_created_at, expected_updated_at)
    messages = db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).order_by(SupportMessage.created_at, SupportMessage.id).all()
    message_ids = [message.id for message in messages]
    retain_support(db, thread, messages, current_user, "staff", "delete_conversation")
    _cancel_unsent_support_emails(db, message_ids)
    db.query(SupportMessage).filter(SupportMessage.thread_id == thread.id).delete(synchronize_session=False)
    archive = db.get(StaffArchive, f"messages:{thread.id}")
    if archive:
        db.delete(archive)
    db.add(AuditLog(user_name=current_user.username,
        event=f"Deleted {thread.recipient_role} conversation {thread.id} and {len(message_ids)} messages", status="Deleted"))
    db.delete(thread)
    db.commit()
    return {"message": "Conversation moved to the SuperAdmin archive.", "deleted_messages": len(message_ids), "archived": True}


@router.post("/messages/{thread_id}/reply", status_code=status.HTTP_201_CREATED)
def reply_to_support_thread(thread_id: int, req: SupportMessageCreate, db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="Enter a message.")
    thread = _staff_support_thread(db, thread_id, current_user)
    if thread.is_closed:
        raise HTTPException(status_code=400, detail="This conversation is closed")
    thread.updated_at = datetime.utcnow()
    reply = SupportMessage(thread_id=thread.id, sender_id=current_user.id, is_staff=True, body=req.message.strip())
    db.add(reply)
    db.flush()
    patron = db.query(User).filter(User.id == thread.user_id).first()
    queue_email(db, f"reply:{reply.id}:{uuid.uuid4().hex}", patron, f"Library reply: {thread.subject}",
                f"{current_user.full_name or 'Library staff'} replied:\n\n{reply.body}\n\nOpen Messages in SmartLib to continue this conversation.")
    db.add(Notification(user_id=thread.user_id, title="Reply from the library", body=f"A library staff member replied to ‘{thread.subject}’.", kind="message"))
    db.commit()
    return {"message": "Reply sent."}


# User Management & Staff Provisioning
@router.get("/signup-options", response_model=List[SignupOptionResponse])
def list_signup_options(
    db: Session = Depends(get_db), current_user: User = Depends(superadmin_only)
):
    return db.query(SignupOption).order_by(SignupOption.kind, SignupOption.name).all()


@router.post("/signup-options", response_model=SignupOptionResponse, status_code=status.HTTP_201_CREATED)
def create_signup_option(
    req: SignupOptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(superadmin_only),
):
    name = req.name.strip()
    department = None
    if req.kind == "department":
        if req.department_id is not None:
            raise HTTPException(status_code=400, detail="A department option cannot have a parent department.")
    else:
        department = db.query(SignupOption).filter(
            SignupOption.id == req.department_id,
            SignupOption.kind == "department",
            SignupOption.is_active.is_(True),
        ).first()
        if not department:
            raise HTTPException(status_code=400, detail="Choose an active department for this course.")
    duplicate = db.query(SignupOption).filter(
        SignupOption.kind == req.kind,
        SignupOption.department_id == (department.id if department else None),
        SignupOption.name.ilike(name),
    ).first()
    if duplicate:
        if duplicate.is_active:
            raise HTTPException(status_code=400, detail="That option already exists.")
        duplicate.is_active = True
        db.commit()
        db.refresh(duplicate)
        return duplicate
    option = SignupOption(
        kind=req.kind,
        name=name,
        department_id=department.id if department else None,
        created_by=current_user.id,
    )
    db.add(option)
    db.commit()
    db.refresh(option)
    crud.log_audit(db, current_user.username, f"Signup {req.kind} option added: {name}", "Authorized")
    return option


@router.put("/signup-options/{option_id}", response_model=SignupOptionResponse)
def update_signup_option(
    option_id: int,
    req: SignupOptionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(superadmin_only),
):
    option = db.query(SignupOption).filter(SignupOption.id == option_id).first()
    if not option:
        raise HTTPException(status_code=404, detail="Signup option not found.")
    name = req.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Option name must contain at least two characters.")
    if req.kind != option.kind:
        raise HTTPException(status_code=400, detail="An option's type cannot be changed after creation.")

    department = None
    if option.kind == "department":
        if req.department_id is not None:
            raise HTTPException(status_code=400, detail="A department option cannot have a parent department.")
    else:
        department = db.query(SignupOption).filter(
            SignupOption.id == req.department_id,
            SignupOption.kind == "department",
            SignupOption.is_active.is_(True),
        ).first()
        if not department:
            raise HTTPException(status_code=400, detail="Choose an active department for this course.")

    parent_id = department.id if department else None
    duplicate = db.query(SignupOption).filter(
        SignupOption.id != option.id,
        SignupOption.kind == option.kind,
        SignupOption.department_id == parent_id,
        func.lower(SignupOption.name) == name.casefold(),
        SignupOption.is_active.is_(True),
    ).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="Another active option already uses that name in this department.")

    option.name = name
    option.department_id = parent_id
    db.commit()
    db.refresh(option)
    crud.log_audit(db, current_user.username, f"Signup {option.kind} option updated: {name}", "Authorized")
    return option


@router.delete("/signup-options/{option_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_signup_option(
    option_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(superadmin_only),
):
    option = db.query(SignupOption).filter(SignupOption.id == option_id).first()
    if not option:
        raise HTTPException(status_code=404, detail="Signup option not found.")
    if option.kind == "department":
        courses = db.query(SignupOption).filter(SignupOption.kind == "course", SignupOption.department_id == option.id, SignupOption.is_active.is_(True)).all()
    else:
        courses = []
    if option.is_active or courses:
        retain_deleted(db, kind="signup-options", record_id=option.id, title=option.name,
            actor=current_user, origin="staff", action="delete", content={"option": record_content(option),
                "courses": [record_content(course) for course in courses]})
    if option.kind == "department":
        db.query(SignupOption).filter(
            SignupOption.kind == "course", SignupOption.department_id == option.id
        ).update({SignupOption.is_active: False}, synchronize_session=False)
    option.is_active = False
    db.commit()
    crud.log_audit(db, current_user.username, f"Signup option deactivated: {option.name}", "Authorized")


@router.patch("/signup-options/{option_id}/restore", response_model=SignupOptionResponse)
def restore_signup_option(
    option_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(superadmin_only),
):
    option = db.query(SignupOption).filter(SignupOption.id == option_id).first()
    if not option:
        raise HTTPException(status_code=404, detail="Signup option not found.")
    if option.kind == "course":
        parent = db.query(SignupOption).filter(
            SignupOption.id == option.department_id,
            SignupOption.kind == "department",
            SignupOption.is_active.is_(True),
        ).first()
        if not parent:
            raise HTTPException(status_code=400, detail="Reactivate the department before restoring this course.")
    option.is_active = True
    db.commit()
    db.refresh(option)
    crud.log_audit(db, current_user.username, f"Signup option restored: {option.name}", "Authorized")
    return option


@router.get("/users", response_model=List[UserResponse])
def list_all_users(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_or_staff),
):
    return crud.get_users(db, skip=skip, limit=limit)


@router.post("/users/staff", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def provision_staff_account(
    staff_data: UserStaffCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(superadmin_only),
):
    if crud.get_user_by_username(db, staff_data.staff_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Staff ID {staff_data.staff_id} already provisioned",
        )
    return crud.create_staff_user(db, staff_data)


@router.put("/users/{user_id}/status", response_model=UserResponse)
def toggle_user_account_status(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(superadmin_only),
):
    user = crud.toggle_user_active(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return user


# Audit & Email Logs
@router.get("/logs/audit", response_model=List[AuditLogResponse])
def get_audit_trail(
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)
):
    return crud.get_audit_logs(db)


@router.get("/logs/smtp", response_model=List[SmtpLogResponse])
def get_smtp_email_logs(
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)
):
    return crud.get_smtp_logs(db)


@router.get("/feedbacks", response_model=List[FeedbackResponse])
def get_public_feedbacks(
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)
):
    return crud.get_feedbacks(db)



@router.get("/archives")
def staff_archives(db: Session = Depends(get_db), current_user: User = Depends(superadmin_only)):
    return db.query(StaffArchive).order_by(StaffArchive.created_at.desc()).all()


@router.put("/archives/{kind}/{record_id}")
def archive_staff_record(kind: Literal["acquisitions", "announcements", "messages"], record_id: str,
    archived: bool = True, reason: str = Query("", max_length=500),
    db: Session = Depends(get_db), current_user: User = Depends(admin_or_staff)):
    if not archived and current_user.role != UserRole.SUPERADMIN:
        raise HTTPException(403, "Only SuperAdmin can restore archived records.")
    model = {"acquisitions": AcquisitionSuggestion, "announcements": Announcement, "messages": SupportThread}[kind]
    column = AcquisitionSuggestion.acq_id if kind == "acquisitions" else model.id
    record = db.query(model).filter(column == record_id).first()
    if not record or (kind == "messages" and record.recipient_role != _support_recipient_role(current_user) and current_user.role != UserRole.SUPERADMIN):
        raise HTTPException(404, "Record not found.")
    if archived and len(reason.strip()) < 3:
        raise HTTPException(422, "Enter an archive reason of at least 3 characters.")
    key = f"{kind}:{record_id}"
    entry = db.get(StaffArchive, key)
    if archived and not entry:
        if kind == "messages":
            messages = db.query(SupportMessage).filter(SupportMessage.thread_id == record.id).order_by(SupportMessage.created_at, SupportMessage.id).all()
            retain_support(db, record, messages, current_user, "staff", "archive_conversation", reason=reason.strip())
        else:
            retain_deleted(db, kind=kind, record_id=record_id, title=record.title,
                actor=current_user, origin="staff", action="archive", content={"record": record_content(record), "reason": reason.strip()})
        db.add(StaffArchive(key=key, reason=reason.strip(), archived_by=current_user.username))
    elif not archived and entry:
        db.delete(entry)
    if kind == "announcements":
        record.is_active = not archived
    db.add(AuditLog(user_name=current_user.username, event=f"{'Archived' if archived else 'Restored'} {key}; {reason.strip()}", status="Success"))
    db.commit()
    return {"archived": archived}


def _deleted_archive_summary(entry):
    return {field: getattr(entry, field) for field in (
        "id", "kind", "record_id", "title", "action", "origin", "deleted_by", "deleted_at")}


@router.get("/deleted-archives")
def list_deleted_archives(response: Response, q: str = Query("", max_length=200), kind: str = Query("", max_length=32),
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db), current_user: User = Depends(superadmin_only)):
    from models import DeletedArchive
    response.headers["Cache-Control"] = "no-store"
    query = db.query(DeletedArchive)
    if kind:
        query = query.filter(DeletedArchive.kind == kind)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(DeletedArchive.title.ilike(term), DeletedArchive.record_id.ilike(term), DeletedArchive.deleted_by.ilike(term)))
    total = query.count()
    entries = query.order_by(DeletedArchive.deleted_at.desc(), DeletedArchive.id).offset(skip).limit(limit).all()
    return {"items": [_deleted_archive_summary(entry) for entry in entries], "total": total, "skip": skip, "limit": limit}


@router.get("/deleted-archives/{archive_id}")
def read_deleted_archive(archive_id: str, response: Response, db: Session = Depends(get_db), current_user: User = Depends(superadmin_only)):
    import json
    from models import DeletedArchive
    response.headers["Cache-Control"] = "no-store"
    entry = db.get(DeletedArchive, archive_id)
    if not entry:
        raise HTTPException(404, "Archived record not found.")
    return dict(_deleted_archive_summary(entry), content=json.loads(entry.content_json))
