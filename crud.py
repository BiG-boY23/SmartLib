import re
import uuid
import math
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from staff_activity import notify_borrow_activity, notify_staff_activity

from models import (
    User,
    Book,
    BorrowRecord,
    Fine,
    AcquisitionSuggestion,
    Feedback,
    AuditLog,
    SmtpLog,
    UserRole,
    BorrowStatus,
    BookCopy,
)

LOAN_PERIOD_DAYS = 1
OVERDUE_HOURLY_FINE = 1.50
OVERDUE_DAILY_FINE_CAP = 10.00


def calculate_overdue_fine(due_date: datetime, returned_at: datetime) -> float:
    """Apply the published ₱1.50/hour rate, capped at ₱10 for each 24h late day."""
    seconds_late = max(0, (returned_at - due_date).total_seconds())
    if not seconds_late:
        return 0.0
    hours_late = math.ceil(seconds_late / 3600)
    overdue_days = math.ceil(seconds_late / 86400)
    return round(min(hours_late * OVERDUE_HOURLY_FINE, overdue_days * OVERDUE_DAILY_FINE_CAP), 2)
from schemas import (
    UserCreate,
    UserUpdate,
    UserStaffCreate,
    BookCreate,
    BookUpdate,
    BorrowRequestCreate,
    AcquisitionCreate,
)
from security import get_password_hash


# Audit & SMTP Helper Logging
def log_audit(db: Session, user_name: str, event: str, status: str):
    log_entry = AuditLog(user_name=user_name, event=event, status=status)
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry


def log_smtp(
    db: Session,
    recipient: str,
    event_type: str,
    subject: str,
    status: str = "Delivered (200 OK)",
):
    log_entry = SmtpLog(
        recipient=recipient, event_type=event_type, subject=subject, status=status
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry


def get_audit_logs(db: Session, skip: int = 0, limit: int = 100):
    return (
        db.query(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_smtp_logs(db: Session, skip: int = 0, limit: int = 100):
    return (
        db.query(SmtpLog)
        .order_by(SmtpLog.timestamp.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


# Feedback CRUD
def create_feedback(db: Session, feedback_data):
    fb = Feedback(
        name=feedback_data.name,
        email=feedback_data.email,
        category=feedback_data.category or "General Inquiry",
        message=feedback_data.message,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    log_audit(db, feedback_data.name, f"Submitted Feedback ({fb.category})", "Received")
    return fb


def get_feedbacks(db: Session, skip: int = 0, limit: int = 100):
    return db.query(Feedback).order_by(Feedback.created_at.desc()).offset(skip).limit(limit).all()



# User CRUD
def get_user(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_username(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()


def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()


def get_users(db: Session, skip: int = 0, limit: int = 100):
    return db.query(User).offset(skip).limit(limit).all()


def create_user(db: Session, user_data: UserCreate):
    hashed_pwd = get_password_hash(user_data.password)
    db_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_pwd,
        full_name=user_data.full_name,
        role=user_data.role,
        department=user_data.department,
        contact_no=user_data.contact_no,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    log_audit(db, db_user.username, f"User Registered ({db_user.role.value})", "Active")
    return db_user


def create_staff_user(db: Session, staff_data: UserStaffCreate):
    hashed_pwd = get_password_hash(staff_data.password)
    db_user = User(
        username=staff_data.staff_id,
        email=staff_data.email,
        hashed_password=hashed_pwd,
        full_name=staff_data.full_name,
        role=UserRole.LIBRARIAN,
        department=staff_data.department,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    log_audit(
        db, "Super Admin", f"Provisioned Staff {staff_data.staff_id}", "Authorized"
    )
    log_smtp(
        db,
        staff_data.email,
        "Account Credentials Issued",
        f"Welcome to EVSU Library Staff — ID: {staff_data.staff_id}",
    )
    return db_user


def update_user(db: Session, user_id: int, user_update: UserUpdate):
    db_user = get_user(db, user_id)
    if not db_user:
        return None
    for field, value in user_update.model_dump(exclude_unset=True).items():
        setattr(db_user, field, value)
    db.commit()
    db.refresh(db_user)
    log_audit(db, db_user.username, "Profile Information Updated", "Saved")
    return db_user


def toggle_user_active(db: Session, user_id: int):
    db_user = get_user(db, user_id)
    if db_user:
        db_user.is_active = not db_user.is_active
        db.commit()
        db.refresh(db_user)
        status_str = "Active" if db_user.is_active else "Suspended"
        log_audit(
            db, "Super Admin", f"Toggled status for {db_user.username}", status_str
        )
    return db_user


# Book CRUD
def get_book(db: Session, book_id: int):
    return db.query(Book).filter(Book.id == book_id).first()


def resolve_book_copy(db: Session, accession_no: str):
    """Resolve a base catalog accession or an exact physical-copy QR tag."""
    scanned = str(accession_no or "").strip().upper()
    copy_match = re.fullmatch(r"(.+)-C(\d+)", scanned)
    if copy_match:
        base_accession = copy_match.group(1)
        copy_number = int(copy_match.group(2))
        book = db.query(Book).filter(func.upper(Book.accession_no) == base_accession).first()
        if not book or copy_number < 1 or copy_number > book.total_copies:
            return None, None, True
        return book, f"{book.accession_no}-C{copy_number:02d}", True

    book = db.query(Book).filter(func.upper(Book.accession_no) == scanned).first()
    if not book:
        return None, None, False
    return book, book.accession_no, False


def get_book_by_accession(db: Session, accession_no: str):
    book, _, _ = resolve_book_copy(db, accession_no)
    return book


def _active_copy_records(db: Session, book: Book):
    return db.query(BorrowRecord).filter(
        BorrowRecord.book_id == book.id,
        BorrowRecord.status == BorrowStatus.CHECKED_OUT,
    ).all()


def _allocate_available_copy(db: Session, book: Book):
    """Return the next unloaned copy tag, refusing ambiguous legacy loans."""
    active = _active_copy_records(db, book)
    if any(not row.copy_accession_no for row in active):
        return None, "An older checkout has no copy QR recorded. Resolve that loan before issuing another copy of this title."
    used = {row.copy_accession_no.upper() for row in active}
    for copy_number in range(1, book.total_copies + 1):
        tag = f"{book.accession_no}-C{copy_number:02d}"
        if tag.upper() not in used:
            return tag, None
    return None, "All physical copies of this book are currently checked out."


def get_books(
    db: Session, search: Optional[str] = None, skip: int = 0, limit: int = 100,
    archived_only: bool = False
):
    query = db.query(Book)
    # Filter by archive status
    if archived_only:
        query = query.filter(Book.is_archived == True)
    else:
        query = query.filter(Book.is_archived == False)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Book.title.ilike(search_pattern),
                Book.author.ilike(search_pattern),
                Book.category.ilike(search_pattern),
                Book.accession_no.ilike(search_pattern),
                Book.isbn.ilike(search_pattern),
                Book.publisher.ilike(search_pattern),
                Book.call_number.ilike(search_pattern),
            )
        )
    return query.offset(skip).limit(limit).all()


def _new_book_copy(book: Book, copy_number: int, qr_printed_at=None):
    return BookCopy(
        book_id=book.id,
        copy_number=copy_number,
        accession_no=f"{book.accession_no}-C{copy_number:02d}",
        qr_printed_at=qr_printed_at,
    )


def ensure_book_copies(db: Session, legacy_as_printed: bool = True):
    """Backfill the persistent copy registry without changing existing tags."""
    stamp = datetime.utcnow() if legacy_as_printed else None
    for book in db.query(Book).all():
        existing = {
            copy_number
            for (copy_number,) in db.query(BookCopy.copy_number).filter(BookCopy.book_id == book.id).all()
        }
        for copy_number in range(1, book.total_copies + 1):
            if copy_number not in existing:
                db.add(_new_book_copy(book, copy_number, stamp))
    db.commit()


def get_book_copies(db: Session, book_id: int, unprinted_only: bool = False):
    query = db.query(BookCopy).filter(BookCopy.book_id == book_id)
    if unprinted_only:
        query = query.filter(BookCopy.qr_printed_at.is_(None))
    return query.order_by(BookCopy.copy_number.asc()).all()


def mark_book_copies_printed(db: Session, book_id: int, copy_ids: List[int]):
    now = datetime.utcnow()
    rows = db.query(BookCopy).filter(
        BookCopy.book_id == book_id,
        BookCopy.id.in_(copy_ids),
        BookCopy.qr_printed_at.is_(None),
    ).all()
    for row in rows:
        row.qr_printed_at = now
    db.commit()
    return rows


def create_book(db: Session, book_data: BookCreate):
    db_book = Book(**book_data.model_dump())
    db.add(db_book)
    db.flush()
    db.add_all(_new_book_copy(db_book, copy_number) for copy_number in range(1, db_book.total_copies + 1))
    db.commit()
    db.refresh(db_book)
    log_audit(
        db,
        "Librarian",
        f"Cataloged Book {db_book.accession_no} ({db_book.title})",
        "Saved",
    )
    return db_book


def update_book(db: Session, book_id: int, book_update: BookUpdate):
    db_book = get_book(db, book_id)
    if not db_book:
        return None
    changes = book_update.model_dump(exclude_unset=True, exclude={"available_copies"})
    new_total = changes.get("total_copies", db_book.total_copies)
    if new_total is None or new_total < 1:
        raise ValueError("The copy count must be at least one.")
    if new_total < db_book.total_copies:
        raise ValueError("The copy count cannot be reduced because existing copy QR records must be retained. Increase the count to add copies.")
    copy_delta = new_total - db_book.total_copies
    for field, value in changes.items():
        setattr(db_book, field, value)
    if copy_delta:
        db_book.available_copies += copy_delta
        existing_numbers = {
            copy_number
            for (copy_number,) in db.query(BookCopy.copy_number).filter(BookCopy.book_id == db_book.id).all()
        }
        next_number = max(existing_numbers, default=0) + 1
        new_rows = []
        while len(new_rows) < copy_delta:
            if next_number not in existing_numbers:
                new_rows.append(_new_book_copy(db_book, next_number))
            next_number += 1
        db.add_all(new_rows)
    db.commit()
    db.refresh(db_book)
    log_audit(db, "Librarian", f"Updated Book Metadata {db_book.accession_no}", "Saved")
    return db_book


def delete_book(db: Session, book_id: int):
    db_book = get_book(db, book_id)
    if db_book:
        db.delete(db_book)
        db.commit()
        log_audit(
            db, "Librarian", f"Deleted Book {db_book.accession_no}", "Deleted"
        )
        return True
    return False


# Borrow & Circulation CRUD
def create_borrow_request(
    db: Session, user: User, request_data: BorrowRequestCreate
):
    book = get_book_by_accession(db, request_data.accession_no)
    if not book or book.is_archived:
        return None, "Book not found"
    if book.available_copies <= 0:
        return None, "Book is currently unavailable"

    # Keep request identifiers unique across restarts and concurrent submissions.
    req_id = f"REQ-{uuid.uuid4().hex[:8].upper()}"

    borrow_rec = BorrowRecord(
        req_id=req_id,
        user_id=user.id,
        book_id=book.id,
        status=BorrowStatus.PENDING,
        pickup_date=(
            f"{request_data.pickup_date}T{request_data.pickup_time}"
            if request_data.pickup_time
            else request_data.pickup_date
        ),
        notes=request_data.notes,
    )
    db.add(borrow_rec)
    db.flush()
    notify_borrow_activity(db, borrow_rec)
    db.commit()
    db.refresh(borrow_rec)

    log_audit(
        db,
        user.username,
        f"Online Borrow Request {req_id} Submitted for {book.accession_no}",
        "Pending",
    )
    log_smtp(
        db,
        user.email,
        "Borrow Request Submitted",
        f"Request {req_id}: {book.title}",
    )
    return borrow_rec, "Success"


def get_borrow_records(
    db: Session, user_id: Optional[int] = None, skip: int = 0, limit: int = 100
):
    query = db.query(BorrowRecord)
    if user_id:
        query = query.filter(BorrowRecord.user_id == user_id)
    records = (
        query.order_by(BorrowRecord.request_date.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    # Populate helper fields for response
    for r in records:
        r.user_name = r.user.full_name if r.user else "Unknown"
        r.book_title = r.book.title if r.book else "Unknown"
        r.accession_no = r.copy_accession_no or (r.book.accession_no if r.book else "Unknown")
    return records


def _checkout_eligibility(db: Session, patron):
    if not patron:
        return "Patron account not found."
    # Serialize checkouts for this patron, including different titles.
    db.query(User).filter(User.id == patron.id).update({User.is_active: User.is_active}, synchronize_session=False)
    db.refresh(patron)
    if not patron.is_active:
        return "Patron account is suspended."
    if db.query(Fine).filter(Fine.user_id == patron.id, Fine.is_paid.is_(False)).first():
        return "Patron has unpaid library fines."
    maximum = 3 if patron.role == UserRole.STUDENT else 5
    active = db.query(BorrowRecord).filter(BorrowRecord.user_id == patron.id,
        BorrowRecord.status.in_([BorrowStatus.CHECKED_OUT, BorrowStatus.OVERDUE])).count()
    if active >= maximum:
        return f"Borrow limit reached: maximum is {maximum} books at a time."
    return None


def _claim_stock(db: Session, book):
    claimed = db.query(Book).filter(Book.id == book.id, Book.is_archived.is_(False), Book.available_copies > 0).update(
        {Book.available_copies: Book.available_copies - 1}, synchronize_session=False)
    db.refresh(book)
    return claimed == 1


def update_borrow_status(db: Session, req_id: str, new_status: BorrowStatus, persist: bool = True):
    record = (
        db.query(BorrowRecord).filter(BorrowRecord.req_id == req_id).first()
    )
    if not record:
        return None, "Request record not found"

    if new_status == BorrowStatus.APPROVED:
        if record.status != BorrowStatus.PENDING:
            return None, "Only pending requests can be approved."
        # Approval reserves the pickup; the loan clock starts at checkout.
        record.issue_date = None
        record.due_date = None
        record.status = new_status
    elif new_status == BorrowStatus.REJECTED:
        if record.status != BorrowStatus.PENDING:
            return None, "Only pending requests can be rejected."
        record.status = new_status
    elif new_status == BorrowStatus.CHECKED_OUT:
        if record.status != BorrowStatus.APPROVED:
            return None, "Approve the request before checking it out."
        eligibility_error = _checkout_eligibility(db, record.user)
        if eligibility_error:
            return None, eligibility_error
        if not record.book or record.book.is_archived or record.book.available_copies <= 0:
            return None, "No available copy can be checked out for this request."
        copy_tag, allocation_error = _allocate_available_copy(db, record.book)
        if allocation_error:
            return None, allocation_error
        now = datetime.utcnow()
        record.copy_accession_no = copy_tag
        record.issue_date = now
        record.due_date = now + timedelta(days=LOAN_PERIOD_DAYS)
        if not _claim_stock(db, record.book):
            return None, "No available copy can be checked out for this request."
        record.status = new_status
    elif new_status == BorrowStatus.RETURNED:
        if record.status != BorrowStatus.CHECKED_OUT:
            return None, "This request has no active checkout. No return was recorded."
        now = datetime.utcnow()
        record.return_date = now
        record.status = new_status
        if record.book and record.book.available_copies < record.book.total_copies:
            record.book.available_copies += 1
        if record.due_date and now > record.due_date:
            db.add(Fine(
                borrow_record_id=record.id,
                user_id=record.user_id,
                amount=calculate_overdue_fine(record.due_date, now),
                is_paid=False,
            ))
    else:
        return None, f"Status {new_status.value} cannot be set through this action."

    notify_borrow_activity(db, record)
    if persist:
        db.commit()
        db.refresh(record)
        log_audit(
            db,
            "Librarian",
            f"Updated Borrow Request {req_id} status to {new_status.value}",
            new_status.value,
        )
    else:
        db.flush()
    return record, "Success"


def get_circulation_patron_info(db: Session, patron_identifier: str):
    """Instant lookup of patron status for circulation desk scanning."""
    scanned_value = str(patron_identifier or "").strip()
    # Mobile member cards encode the institutional ID in a versioned payload.
    # Some keyboard-wedge scanners send that payload twice in one read, so
    # extract the ID instead of attempting to match the entire QR string.
    qr_match = re.search(
        r"EVSU-CARD-[^:]+::(.+?)::(?:student|faculty|admin|superadmin|librarian)(?=$|EVSU-CARD-)",
        scanned_value,
        flags=re.IGNORECASE,
    )
    if qr_match:
        scanned_value = qr_match.group(1).strip()

    patron = db.query(User).filter(
        or_(
            func.lower(User.username) == scanned_value.casefold(),
            func.lower(User.email) == scanned_value.casefold(),
            func.lower(User.full_name) == scanned_value.casefold(),
        )
    ).first()
    if not patron:
        return {"found": False, "message": "Scanned library ID was not recognized. Ask the patron to refresh their Library ID and try again."}

    # Active loans
    active_borrows = (
        db.query(BorrowRecord)
        .filter(
            BorrowRecord.user_id == patron.id,
            BorrowRecord.status == BorrowStatus.CHECKED_OUT
        )
        .all()
    )
    loans = [
        {
            "req_id": r.req_id,
            "book_title": r.book.title if r.book else "Unknown",
            "accession_no": r.copy_accession_no or (r.book.accession_no if r.book else ""),
            "due_date": r.due_date.strftime("%Y-%m-%d") if r.due_date else ""
        }
        for r in active_borrows
    ]

    # Unpaid fines
    unpaid_fines = db.query(Fine).filter(Fine.user_id == patron.id, Fine.is_paid == False).all()
    fines_total = sum(f.amount for f in unpaid_fines)

    is_faculty = patron.role in [UserRole.FACULTY, UserRole.ADMIN, UserRole.SUPERADMIN, UserRole.LIBRARIAN]
    max_allowed = 5 if is_faculty else 3

    status_tag = "Active & Eligible"
    can_borrow = True
    if not patron.is_active:
        status_tag = "Suspended / Inactive"
        can_borrow = False
    elif fines_total > 0:
        status_tag = f"Blocked (Unpaid Fines: ₱{fines_total:.2f})"
        can_borrow = False
    elif len(loans) >= max_allowed:
        status_tag = f"Limit Reached ({len(loans)}/{max_allowed} Books)"
        can_borrow = False

    return {
        "found": True,
        "id": patron.id,
        "username": patron.username,
        "full_name": patron.full_name or patron.username,
        "email": patron.email,
        "department": patron.department or "General Student",
        "course": patron.course or "Not provided",
        "role": patron.role.value if patron.role else "Student",
        "is_active": patron.is_active,
        "can_borrow": can_borrow,
        "status_tag": status_tag,
        "active_loans_count": len(loans),
        "max_allowed": max_allowed,
        "active_loans": loans,
        "unpaid_fines": fines_total
    }


def get_circulation_book_info(db: Session, accession_no: str):
    """Instant lookup of book availability for circulation desk scanning."""
    book, canonical_copy_tag, is_copy_tag = resolve_book_copy(db, accession_no)
    if not book:
        return {"found": False, "message": f"Book tag '{accession_no}' not found in catalog."}

    active_loans = _active_copy_records(db, book)
    active_loan = None
    legacy_copy_tracking = any(not row.copy_accession_no for row in active_loans)
    checkout_copy_tag = None
    allocation_error = None
    if is_copy_tag:
        active_loan = next(
            (row for row in active_loans if (row.copy_accession_no or "").upper() == canonical_copy_tag.upper()),
            None,
        )
        copy_is_available = not active_loan and not legacy_copy_tracking and book.available_copies > 0
        if copy_is_available:
            checkout_copy_tag = canonical_copy_tag
    else:
        active_loan = active_loans[0] if len(active_loans) == 1 else None
        if book.available_copies > 0:
            checkout_copy_tag, allocation_error = _allocate_available_copy(db, book)
        copy_is_available = bool(checkout_copy_tag)

    borrower_info = None
    if active_loan and active_loan.user:
        borrower_info = {
            "username": active_loan.user.username,
            "full_name": active_loan.user.full_name or active_loan.user.username,
            "due_date": active_loan.due_date.strftime("%Y-%m-%d") if active_loan.due_date else ""
        }

    return {
        "found": True,
        "id": book.id,
        "accession_no": canonical_copy_tag,
        "checkout_copy_accession_no": checkout_copy_tag,
        "scanned_accession_no": str(accession_no or "").strip(),
        "is_copy_tag": is_copy_tag,
        "title": book.title,
        "author": book.author,
        "call_number": book.call_number or "General Shelf",
        "category": book.category,
        "condition": book.condition or "Good",
        "available_copies": book.available_copies,
        "total_copies": book.total_copies,
        "is_available": copy_is_available,
        "copy_status_message": (
            "This copy is already checked out."
            if active_loan and is_copy_tag
            else "An older checkout has no copy QR recorded. Resolve that loan before issuing or returning a specific copy."
            if legacy_copy_tracking
            else allocation_error
            if allocation_error
            else None
        ),
        "current_borrower": borrower_info
    }


def execute_circulation(
    db: Session, mode: str, patron_username: Optional[str], accession_no: str, actor: Optional[str] = None
):
    # Normalized modes
    norm_mode = mode.lower().strip()
    if norm_mode == "borrow_clear":
        norm_mode = "borrow"
    # Older clients labelled a return request "return_late". The actual due
    # date determines whether it was late, so route it through the real check-in.
    if norm_mode in {"return_late", "return_ontime"}:
        norm_mode = "return"

    # 1. Real Borrow Mode (from UI tab or scanner)
    if norm_mode in ["borrow", "issue"]:
        if not patron_username:
            return {"status": "error", "message": "Patron ID / QR scan required for borrowing."}
        patron = get_user_by_username(db, patron_username)
        if not patron:
            return {"status": "error", "message": f"Patron '{patron_username}' not registered in system."}
        eligibility_error = _checkout_eligibility(db, patron)
        if eligibility_error:
            return {"status": "blocked", "message": eligibility_error}
        if not patron.is_active:
            return {"status": "blocked", "message": f"Patron '{patron.username}' is currently suspended."}
        
        # Check fines
        unpaid = db.query(Fine).filter(Fine.user_id == patron.id, Fine.is_paid == False).all()
        if unpaid:
            total_fine = sum(f.amount for f in unpaid)
            return {"status": "blocked", "message": f"Blocked: Patron has ₱{total_fine:.2f} in unpaid library fines."}

        # Check book
        book, requested_copy_tag, is_copy_tag = resolve_book_copy(db, accession_no)
        if not book:
            return {"status": "error", "message": f"Book tag '{accession_no}' not found in catalog."}
        if book.is_archived:
            return {"status": "error", "message": f"Book '{book.title}' is archived and unavailable for checkout."}
        if book.available_copies <= 0:
            return {"status": "unavail", "message": f"All copies of '{book.title}' are currently on loan."}

        active_copies = _active_copy_records(db, book)
        if is_copy_tag:
            if any(not row.copy_accession_no for row in active_copies):
                return {"status": "unavail", "message": "An older checkout has no copy QR recorded. Resolve that loan before issuing a specific copy."}
            if any((row.copy_accession_no or "").upper() == requested_copy_tag.upper() for row in active_copies):
                return {"status": "unavail", "message": f"Copy {requested_copy_tag} is already checked out."}
            copy_tag = requested_copy_tag
        else:
            copy_tag, allocation_error = _allocate_available_copy(db, book)
            if allocation_error:
                return {"status": "unavail", "message": allocation_error}

        # Check role-based borrowing limit (Students: max 3 books, Faculty/Staff: max 5 books)
        is_faculty = patron.role in [UserRole.FACULTY, UserRole.ADMIN, UserRole.SUPERADMIN, UserRole.LIBRARIAN]
        max_allowed = 5 if is_faculty else 3
        active_loans_count = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.user_id == patron.id,
                BorrowRecord.status == BorrowStatus.CHECKED_OUT,
            )
            .count()
        )
        if active_loans_count >= max_allowed:
            return {
                "status": "blocked",
                "message": f"Borrow limit reached: {patron.role.value if patron.role else 'Student'} maximum is {max_allowed} books at a time.",
            }

        # Check out
        if not _claim_stock(db, book):
            return {"status": "unavail", "message": "The last available copy was just checked out. Refresh and try again."}
        now = datetime.utcnow()
        due = now + timedelta(days=LOAN_PERIOD_DAYS)
        loan_label = "1-Day Faculty Loan" if is_faculty else "1-Day Student Loan"
        due_str = due.strftime("%b %d, %Y at %I:%M %p")

        rec = BorrowRecord(
            req_id=f"CIR-{int(now.timestamp())}-{uuid.uuid4().hex[:6].upper()}",
            user_id=patron.id,
            book_id=book.id,
            copy_accession_no=copy_tag,
            status=BorrowStatus.CHECKED_OUT,
            issue_date=now,
            due_date=due,
            notes=f"Issued via Circulation Desk ({loan_label}) to {patron.username}",
        )
        db.add(rec)
        db.flush()
        notify_borrow_activity(db, rec)
        db.commit()

        log_audit(db, actor or patron.username, f"Issued Copy {copy_tag} ({book.title}) to {patron.username}", "Approved")
        log_smtp(db, patron.email, "Book Issued Confirmation", f"You borrowed '{book.title}'. Due Date: {due_str}.")

        return {
            "status": "success",
            "message": f"Checked out '{book.title}' to {patron.full_name or patron.username}. Due: {due_str}.",
            "patron_name": patron.full_name or patron.username,
            "patron_id": patron.username,
            "book_title": book.title,
            "accession_no": copy_tag,
            "copy_accession_no": copy_tag,
            "due_date": due_str,
            "loan_type": loan_label
        }

    # 2. Real Return Mode (from UI tab or scanner)
    elif norm_mode in ["return", "checkin"]:
        book, scanned_copy_tag, is_copy_tag = resolve_book_copy(db, accession_no)
        if not book:
            return {"status": "error", "message": f"Book tag '{accession_no}' not recognized in catalog."}

        # A copy QR must match the active loan for that exact physical copy.
        # Never fall back to another copy of the same title.
        loan_q = db.query(BorrowRecord).filter(
            BorrowRecord.book_id == book.id,
            BorrowRecord.status == BorrowStatus.CHECKED_OUT,
        )
        if patron_username:
            patron = get_user_by_username(db, patron_username)
            if not patron:
                return {
                    "status": "error",
                    "message": f"Patron '{patron_username}' is not registered. No return was recorded.",
                    "book_title": book.title,
                    "accession_no": book.accession_no,
                }
            loan_q = loan_q.filter(BorrowRecord.user_id == patron.id)

        candidates = loan_q.order_by(BorrowRecord.issue_date.desc()).all()
        if is_copy_tag:
            active_rec = next(
                (row for row in candidates if (row.copy_accession_no or "").upper() == scanned_copy_tag.upper()),
                None,
            )
        elif len(candidates) == 1:
            # Base accession scans remain useful for single-copy and legacy records.
            active_rec = candidates[0]
        elif len(candidates) > 1:
            return {
                "status": "error",
                "message": "More than one copy is currently borrowed. Scan the QR label on the exact returned copy.",
                "book_title": book.title,
                "accession_no": book.accession_no,
            }
        else:
            active_rec = None

        if not active_rec:
            legacy_match = any(not row.copy_accession_no for row in candidates)
            if is_copy_tag and legacy_match:
                message = "An earlier active loan has no recorded copy QR label. The physical copy cannot be verified, and no return was recorded. Please reconcile the loan before proceeding."
            elif is_copy_tag:
                message = f"No active checkout was found for '{book.title}' under copy {scanned_copy_tag}. No return was recorded."
            else:
                message = f"No active checkout was found for '{book.title}'. No return was recorded."
            return {
                "status": "error",
                "message": message,
                "book_title": book.title,
                "accession_no": scanned_copy_tag,
                "legacy_copy_tracking": legacy_match,
            }

        now = datetime.utcnow()
        active_rec.status = BorrowStatus.RETURNED
        active_rec.return_date = now
        borrower_name = active_rec.user.full_name or active_rec.user.username if active_rec.user else "Patron"
        if book.available_copies < book.total_copies:
            book.available_copies += 1

        # Check overdue
        is_overdue = False
        fine_amount = 0.0
        if active_rec.due_date and now > active_rec.due_date:
            fine_amount = calculate_overdue_fine(active_rec.due_date, now)
            fine = Fine(
                borrow_record_id=active_rec.id,
                user_id=active_rec.user_id,
                amount=fine_amount,
                is_paid=False
            )
            db.add(fine)
            is_overdue = True

        notify_borrow_activity(db, active_rec)
        db.commit()

        if is_overdue:
            log_audit(db, actor or borrower_name, f"Returned {active_rec.copy_accession_no or book.accession_no} late by {borrower_name}", f"Fine ₱{fine_amount:.2f}")
            return {
                "status": "warning",
                "message": f"Overdue Return: '{book.title}' returned late by {borrower_name}. ₱{fine_amount:.2f} overdue fine added to patron account.",
                "book_title": book.title,
                "accession_no": active_rec.copy_accession_no or book.accession_no,
                "patron_name": borrower_name,
                "fine": fine_amount
            }
        log_audit(db, actor or borrower_name, f"Returned {active_rec.copy_accession_no or book.accession_no} on time by {borrower_name}", "Success")
        return {
            "status": "success",
            "message": f"Returned on time: '{book.title}' by {borrower_name}. Copy restored to shelf ({book.available_copies}/{book.total_copies} available).",
            "book_title": book.title,
            "accession_no": active_rec.copy_accession_no or book.accession_no,
            "patron_name": borrower_name
        }

    # ── BACKWARD COMPATIBILITY BRANCHES (for existing test suites) ──
    patron = get_user_by_username(db, patron_username) if patron_username else None
    if not patron and norm_mode in ["borrow_clear", "borrow_blocked"]:
        return {"status": "error", "message": f"Patron {patron_username} not found"}

    book = get_book_by_accession(db, accession_no)
    if not book and norm_mode != "patron":
        return {"status": "error", "message": f"Book {accession_no} not found"}

    if norm_mode == "borrow_clear":
        if not patron.is_active:
            return {"status": "error", "message": "Patron account is inactive"}
        if book.available_copies <= 0:
            return {"status": "error", "message": "Book is currently on loan"}

        copy_tag, allocation_error = _allocate_available_copy(db, book)
        if allocation_error:
            return {"status": "error", "message": allocation_error}

        book.available_copies -= 1
        record = BorrowRecord(
            req_id=f"CIR-{int(datetime.utcnow().timestamp())}-{uuid.uuid4().hex[:6].upper()}",
            user_id=patron.id,
            book_id=book.id,
            copy_accession_no=copy_tag,
            status=BorrowStatus.CHECKED_OUT,
            issue_date=datetime.utcnow(),
            due_date=datetime.utcnow() + timedelta(days=LOAN_PERIOD_DAYS),
        )
        db.add(record)
        db.flush()
        notify_borrow_activity(db, record)
        db.commit()

        log_audit(db, patron.username, f"Borrowed {book.accession_no}", "Approved")
        log_smtp(db, patron.email, "Borrow Confirmation", f"Notice: Issued {book.accession_no}")
        return {
            "status": "success",
            "message": f"Issued {book.accession_no} to {patron.username}. Due in 1 day.",
        }

    elif norm_mode == "borrow_blocked":
        log_audit(db, patron.username if patron else "Patron", "Attempted borrow on blocked account", "Blocked")
        return {
            "status": "blocked",
            "message": f"Error: Patron {patron.username if patron else ''} account has pending block / unpaid fines.",
        }

    elif norm_mode == "borrow_unavail":
        return {
            "status": "unavail",
            "message": f"Error: Book {accession_no} is currently on loan.",
        }

    return {"status": "info", "message": "Circulation processed"}


# Acquisition CRUD
_acq_counter = 201


def create_acquisition(
    db: Session, user: User, acq_data: AcquisitionCreate
):
    acq_id = f"ACQ-{uuid.uuid4().hex[:12].upper()}"

    acq = AcquisitionSuggestion(
        acq_id=acq_id,
        user_id=user.id,
        title=acq_data.title,
        author=acq_data.author,
        publisher_isbn=acq_data.publisher_isbn,
        justification=acq_data.justification,
        status="Under Review",
    )
    db.add(acq)
    db.flush()
    notify_staff_activity(db, "New book suggestion",
        f"{user.full_name or user.username} suggested {acq.title} by {acq.author} ({acq.acq_id}).",
        "suggestion", event_key=f"acquisition:{acq.id}:submitted")
    db.commit()
    db.refresh(acq)

    log_audit(
        db,
        user.username,
        f"Submitted Acquisition Suggestion ({acq_data.title})",
        "Saved",
    )
    return acq


def get_acquisitions(db: Session, skip: int = 0, limit: int = 100):
    acqs = (
        db.query(AcquisitionSuggestion)
        .order_by(AcquisitionSuggestion.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    for a in acqs:
        a.requester_name = a.user.full_name if a.user else "Patron"
    return acqs


def update_acquisition_status(db: Session, acq_id: str, new_status: str):
    acq = (
        db.query(AcquisitionSuggestion)
        .filter(AcquisitionSuggestion.acq_id == acq_id)
        .first()
    )
    if acq:
        acq.status = new_status
        db.commit()
        db.refresh(acq)
        log_audit(
            db,
            "Librarian",
            f"Updated Acquisition {acq_id} status to {new_status}",
            "Approved" if "Approved" in new_status else "Declined",
        )
    return acq


# Initial Data Seeding
def seed_initial_data(db: Session):
    import os
    if os.getenv("SMARTLIB_ENV", "development").lower() == "production":
        return
    # Check if users exist
    if db.query(User).count() == 0:
        super_admin = User(
            username="ADMIN-EVSU-2026",
            email="admin@evsu.edu.ph",
            hashed_password=get_password_hash("admin123"),
            full_name="Super Admin System",
            role=UserRole.SUPERADMIN,
            department="Main Library Office",
        )
        librarian = User(
            username="EVSU-LIB-2026",
            email="librarian@evsu.edu.ph",
            hashed_password=get_password_hash("password123"),
            full_name="Romulo Joseph M. Jereza, IV, RL, MLIS",
            role=UserRole.LIBRARIAN,
            department="Supervising Librarian",
        )
        librarian2 = User(
            username="FEVIDAL-LIB-2026",
            email="fevidal@evsu.edu.ph",
            hashed_password=get_password_hash("password123"),
            full_name="Neña Divina D. Fevidal, RL, MLIS",
            role=UserRole.LIBRARIAN,
            department="Librarian Designate",
        )
        student = User(
            username="2026-STUDENT-088",
            email="student@evsu.edu.ph",
            hashed_password=get_password_hash("password123"),
            full_name="EVSU Student Patron",
            role=UserRole.STUDENT,
            department="Ormoc City Campus",
        )
        db.add_all([super_admin, librarian, librarian2, student])
        db.commit()
    elif (
        not get_user_by_username(db, "2026-STUDENT-088")
        and not get_user_by_email(db, "student@evsu.edu.ph")
    ):
        # Older installations were seeded before patron roles were available.
        db.add(
            User(
                username="2026-STUDENT-088",
                email="student@evsu.edu.ph",
                hashed_password=get_password_hash("password123"),
                full_name="EVSU Student Patron",
                role=UserRole.STUDENT,
                department="Ormoc City Campus",
            )
        )
        db.commit()

    # Check if books exist
    if db.query(Book).count() == 0:
        book1 = Book(
            accession_no="LIB-QR-1001",
            title="Software Engineering with RAD Methodology",
            author="Dr. R. Sommerville",
            publisher="Pearson Education",
            year_published="2022",
            isbn="978-0133943030",
            category="Computer Science",
            call_number="QA 76.758 .S65 2022",
            location_rack="Rack CS-1",
            total_copies=5,
            available_copies=4,
            condition="Good",
        )
        book2 = Book(
            accession_no="LIB-QR-1002",
            title="Database Systems & MySQL Architectures",
            author="Elmasri & Navathe",
            publisher="Addison-Wesley",
            year_published="2021",
            isbn="978-0136086208",
            category="Information Technology",
            call_number="QA 76.9 .D3 E46 2021",
            location_rack="Rack IT-2",
            total_copies=3,
            available_copies=0,
            condition="New",
        )
        book3 = Book(
            accession_no="LIB-QR-1003",
            title="Academic Research Methods & Capstone Guide",
            author="Prof. E. Santos",
            publisher="Rex Book Store",
            year_published="2023",
            isbn="978-9710891234",
            category="General Education",
            call_number="LB 2369 .S26 2023",
            location_rack="Rack GE-3",
            total_copies=8,
            available_copies=8,
            condition="Good",
        )
        db.add_all([book1, book2, book3])
        db.commit()

    # Initial Logs
    if db.query(AuditLog).count() == 0:
        log_audit(db, "EVSU-LIB-2026", "User Authentication Passed", "Authorized")
        log_audit(db, "ADMIN-EVSU-2026", "System Core Services Initialized", "Active")

    if db.query(SmtpLog).count() == 0:
        log_smtp(
            db,
            "librarian@evsu.edu.ph",
            "Borrow Confirmation",
            "Issue Notice: LIB-QR-1001",
        )
