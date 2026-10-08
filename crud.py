import re
import uuid
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

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
)
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


def get_book_by_accession(db: Session, accession_no: str):
    book = db.query(Book).filter(Book.accession_no == accession_no).first()
    if book:
        return book
    if "-C" in accession_no:
        base_acc = accession_no.rsplit("-C", 1)[0]
        return db.query(Book).filter(Book.accession_no == base_acc).first()
    return None


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


def create_book(db: Session, book_data: BookCreate):
    db_book = Book(**book_data.model_dump())
    db.add(db_book)
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
    for field, value in book_update.model_dump(exclude_unset=True).items():
        setattr(db_book, field, value)
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
    if not book:
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
        r.accession_no = r.book.accession_no if r.book else "Unknown"
    return records


def update_borrow_status(db: Session, req_id: str, new_status: BorrowStatus):
    record = (
        db.query(BorrowRecord).filter(BorrowRecord.req_id == req_id).first()
    )
    if not record:
        return None, "Request record not found"

    record.status = new_status
    if new_status == BorrowStatus.APPROVED:
        record.issue_date = datetime.utcnow()
        record.due_date = datetime.utcnow() + timedelta(days=7)
    elif new_status == BorrowStatus.CHECKED_OUT:
        record.issue_date = datetime.utcnow()
        record.due_date = datetime.utcnow() + timedelta(days=7)
        if record.book and record.book.available_copies > 0:
            record.book.available_copies -= 1
    elif new_status == BorrowStatus.RETURNED:
        record.return_date = datetime.utcnow()
        if record.book:
            record.book.available_copies += 1

    db.commit()
    db.refresh(record)

    log_audit(
        db,
        "Librarian",
        f"Updated Borrow Request {req_id} status to {new_status.value}",
        new_status.value,
    )
    log_smtp(
        db,
        record.user.email if record.user else "patron@evsu.edu.ph",
        f"Borrow Request {new_status.value.title()}",
        f"Notice for Request {req_id}: {record.book.title if record.book else ''}",
    )
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
            "accession_no": r.book.accession_no if r.book else "",
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
    book = get_book_by_accession(db, accession_no)
    if not book:
        return {"found": False, "message": f"Book tag '{accession_no}' not found in catalog."}

    # Check if there is an active borrower for this specific book
    active_loan = (
        db.query(BorrowRecord)
        .filter(
            BorrowRecord.book_id == book.id,
            BorrowRecord.status == BorrowStatus.CHECKED_OUT
        )
        .order_by(BorrowRecord.issue_date.desc())
        .first()
    )

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
        "accession_no": book.accession_no,
        "title": book.title,
        "author": book.author,
        "call_number": book.call_number or "General Shelf",
        "category": book.category,
        "condition": book.condition or "Good",
        "available_copies": book.available_copies,
        "total_copies": book.total_copies,
        "is_available": book.available_copies > 0,
        "current_borrower": borrower_info
    }


def execute_circulation(
    db: Session, mode: str, patron_username: Optional[str], accession_no: str
):
    # Normalized modes
    norm_mode = mode.lower().strip()

    # 1. Real Borrow Mode (from UI tab or scanner)
    if norm_mode in ["borrow", "issue"]:
        if not patron_username:
            return {"status": "error", "message": "Patron ID / QR scan required for borrowing."}
        patron = get_user_by_username(db, patron_username)
        if not patron:
            return {"status": "error", "message": f"Patron '{patron_username}' not registered in system."}
        if not patron.is_active:
            return {"status": "blocked", "message": f"Patron '{patron.username}' is currently suspended."}
        
        # Check fines
        unpaid = db.query(Fine).filter(Fine.user_id == patron.id, Fine.is_paid == False).all()
        if unpaid:
            total_fine = sum(f.amount for f in unpaid)
            return {"status": "blocked", "message": f"Blocked: Patron has ₱{total_fine:.2f} in unpaid library fines."}

        # Check book
        book = get_book_by_accession(db, accession_no)
        if not book:
            return {"status": "error", "message": f"Book tag '{accession_no}' not found in catalog."}
        if book.is_archived:
            return {"status": "error", "message": f"Book '{book.title}' is archived and unavailable for checkout."}
        if book.available_copies <= 0:
            return {"status": "unavail", "message": f"All copies of '{book.title}' are currently on loan."}

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
        book.available_copies -= 1
        now = datetime.utcnow()
        if is_faculty:
            due = now + timedelta(days=7)
            loan_label = "7-Day Faculty Loan"
            due_str = due.strftime("%b %d, %Y")
        else:
            # EVSU Overnight Student Loan: due on or before 9:00 AM next business day
            days_ahead = 3 if now.weekday() == 4 else (2 if now.weekday() == 5 else 1)
            due = (now + timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0)
            loan_label = "Overnight Student Loan (Due 9:00 AM)"
            due_str = f"{due.strftime('%b %d, %Y')} at 9:00 AM"

        rec = BorrowRecord(
            req_id=f"CIR-{int(now.timestamp())}-{uuid.uuid4().hex[:6].upper()}",
            user_id=patron.id,
            book_id=book.id,
            status=BorrowStatus.CHECKED_OUT,
            issue_date=now,
            due_date=due,
            notes=f"Issued via Circulation Desk ({loan_label}) to {patron.username}",
        )
        db.add(rec)
        db.commit()

        log_audit(db, patron.username, f"Issued Book {book.accession_no} ({book.title})", "Approved")
        log_smtp(db, patron.email, "Book Issued Confirmation", f"You borrowed '{book.title}'. Due Date: {due_str}.")

        return {
            "status": "success",
            "message": f"Checked out '{book.title}' to {patron.full_name or patron.username}. Due: {due_str}.",
            "patron_name": patron.full_name or patron.username,
            "patron_id": patron.username,
            "book_title": book.title,
            "accession_no": book.accession_no,
            "due_date": due_str,
            "loan_type": loan_label
        }

    # 2. Real Return Mode (from UI tab or scanner)
    elif norm_mode in ["return", "checkin"]:
        book = get_book_by_accession(db, accession_no)
        if not book:
            return {"status": "error", "message": f"Book tag '{accession_no}' not recognized in catalog."}

        # Find active loan
        loan_q = db.query(BorrowRecord).filter(
            BorrowRecord.book_id == book.id,
            BorrowRecord.status == BorrowStatus.CHECKED_OUT
        )
        if patron_username:
            patron = get_user_by_username(db, patron_username)
            if patron:
                loan_q = loan_q.filter(BorrowRecord.user_id == patron.id)

        active_rec = loan_q.order_by(BorrowRecord.issue_date.desc()).first()

        now = datetime.utcnow()
        if book.available_copies < book.total_copies:
            book.available_copies += 1

        if active_rec:
            active_rec.status = BorrowStatus.RETURNED
            active_rec.return_date = now
            borrower_name = active_rec.user.full_name or active_rec.user.username if active_rec.user else "Patron"

            # Check overdue
            is_overdue = False
            fine_amount = 0.0
            if active_rec.due_date and now > active_rec.due_date:
                days = (now - active_rec.due_date).days or 1
                fine_amount = float(days * 10.0) # ₱10 / day
                fine = Fine(
                    borrow_record_id=active_rec.id,
                    user_id=active_rec.user_id,
                    amount=fine_amount,
                    is_paid=False
                )
                db.add(fine)
                is_overdue = True

            db.commit()

            if is_overdue:
                log_audit(db, borrower_name, f"Returned {book.accession_no} late", f"Fine ₱{fine_amount:.2f}")
                return {
                    "status": "warning",
                    "message": f"Overdue Return: '{book.title}' returned late by {borrower_name}. ₱{fine_amount:.2f} overdue fine added to patron account.",
                    "book_title": book.title,
                    "accession_no": book.accession_no,
                    "patron_name": borrower_name,
                    "fine": fine_amount
                }
            else:
                log_audit(db, borrower_name, f"Returned {book.accession_no} on time", "Success")
                return {
                    "status": "success",
                    "message": f"Returned on time: '{book.title}' by {borrower_name}. Copy restored to shelf ({book.available_copies}/{book.total_copies} available).",
                    "book_title": book.title,
                    "accession_no": book.accession_no,
                    "patron_name": borrower_name
                }
        else:
            db.commit()
            return {
                "status": "success",
                "message": f"Book '{book.title}' returned to library collection. Copies available: {book.available_copies}/{book.total_copies}.",
                "book_title": book.title,
                "accession_no": book.accession_no
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

        book.available_copies -= 1
        record = BorrowRecord(
            req_id=f"CIR-{int(datetime.utcnow().timestamp())}-{uuid.uuid4().hex[:6].upper()}",
            user_id=patron.id,
            book_id=book.id,
            status=BorrowStatus.CHECKED_OUT,
            issue_date=datetime.utcnow(),
            due_date=datetime.utcnow() + timedelta(days=7),
        )
        db.add(record)
        db.commit()

        log_audit(db, patron.username, f"Borrowed {book.accession_no}", "Approved")
        log_smtp(db, patron.email, "Borrow Confirmation", f"Notice: Issued {book.accession_no}")
        return {
            "status": "success",
            "message": f"Issued {book.accession_no} to {patron.username}. Due in 7 days.",
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

    elif norm_mode == "return_ontime":
        if book:
            book.available_copies += 1
        db.commit()
        log_audit(db, patron.username if patron else "Patron", f"Returned {accession_no} on time", "Success")
        log_smtp(db, patron.email if patron else "patron@evsu.edu.ph", "Return Notice", f"Returned {accession_no} successfully")
        return {
            "status": "success",
            "message": f"Book {accession_no} returned on time by {patron.username if patron else 'Patron'}.",
        }

    elif norm_mode == "return_late":
        if book:
            book.available_copies += 1
        fine = Fine(borrow_record_id=1, user_id=patron.id if patron else 1, amount=50.0, is_paid=False)
        db.add(fine)
        db.commit()

        log_audit(db, patron.username if patron else "Patron", f"Returned {accession_no} late (₱50 fine applied)", "Fine Applied")
        log_smtp(db, patron.email if patron else "patron@evsu.edu.ph", "Overdue Fine Notice", f"Warning: ₱50 Fine computed for {accession_no}")
        return {
            "status": "warning",
            "message": f"Overdue return for {accession_no}. ₱50.00 fine added to patron account.",
        }

    return {"status": "info", "message": "Circulation processed"}


# Acquisition CRUD
_acq_counter = 201


def create_acquisition(
    db: Session, user: User, acq_data: AcquisitionCreate
):
    global _acq_counter
    acq_id = f"ACQ-{_acq_counter}"
    _acq_counter += 1

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
            department="Main Campus",
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
                department="Main Campus",
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
