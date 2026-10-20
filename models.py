import enum
from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Enum,
    Float,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from database import Base


class UserRole(str, enum.Enum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    LIBRARIAN = "librarian"
    STUDENT = "student"
    FACULTY = "faculty"


class BorrowStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CHECKED_OUT = "CHECKED_OUT"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    first_name = Column(String(80), nullable=True)
    middle_name = Column(String(80), nullable=True)
    last_name = Column(String(80), nullable=True)
    profile_picture_url = Column(String, nullable=True)
    role = Column(Enum(UserRole), default=UserRole.LIBRARIAN, nullable=False)
    department = Column(String, nullable=True)
    course = Column(String, nullable=True)
    contact_no = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    email_verified = Column(Boolean, default=True, nullable=False, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    borrow_records = relationship("BorrowRecord", back_populates="user")
    fines = relationship("Fine", back_populates="user")
    acquisitions = relationship("AcquisitionSuggestion", back_populates="user")


class UserSession(Base):
    __tablename__ = "auth_sessions"

    session_id = Column(String(64), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_activity_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    remember_me = Column(Boolean, default=False, nullable=False)


class EmailActionToken(Base):
    __tablename__ = "email_action_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    purpose = Column(String(24), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)


class PendingRegistration(Base):
    __tablename__ = "pending_registrations"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    first_name = Column(String(80), nullable=True)
    middle_name = Column(String(80), nullable=True)
    last_name = Column(String(80), nullable=True)
    role = Column(Enum(UserRole), nullable=False)
    department = Column(String, nullable=True)
    course = Column(String, nullable=True)
    otp_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SignupOption(Base):
    __tablename__ = "signup_options"
    __table_args__ = (UniqueConstraint("kind", "department_id", "name", name="uq_signup_option_scope"),)

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(16), nullable=False, index=True)  # department or course
    name = Column(String(120), nullable=False)
    department_id = Column(Integer, ForeignKey("signup_options.id"), nullable=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True)
    accession_no = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, index=True, nullable=False)
    author = Column(String, index=True, nullable=False)
    publisher = Column(String, nullable=True)
    year_published = Column(String, nullable=True)
    isbn = Column(String, nullable=True)
    cover_url = Column(String, nullable=True)
    category = Column(String, index=True, nullable=False)  # Subject/Category
    call_number = Column(String, nullable=True)
    location_rack = Column(String, default="Rack A-1", nullable=True)
    total_copies = Column(Integer, default=1, nullable=False)
    available_copies = Column(Integer, default=1, nullable=False)
    condition = Column(String, default="Good", nullable=True)  # Book Condition
    is_archived = Column(Boolean, default=False, nullable=False)  # Soft-delete/archive flag
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    borrow_records = relationship("BorrowRecord", back_populates="book")
    copies = relationship("BookCopy", back_populates="book", cascade="all, delete-orphan")


class BookCopy(Base):
    __tablename__ = "book_copies"
    __table_args__ = (
        UniqueConstraint("book_id", "copy_number", name="uq_book_copy_number"),
    )

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False, index=True)
    copy_number = Column(Integer, nullable=False)
    accession_no = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    qr_printed_at = Column(DateTime, nullable=True)

    book = relationship("Book", back_populates="copies")


class BorrowRecord(Base):
    __tablename__ = "borrow_records"

    id = Column(Integer, primary_key=True, index=True)
    req_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False)
    # Physical copy QR (for example LIB-QR-1001-C06). Nullable for older
    # requests and circulation records created before copy-level tracking.
    copy_accession_no = Column(String, nullable=True, index=True)
    status = Column(Enum(BorrowStatus), default=BorrowStatus.PENDING, nullable=False)
    request_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    pickup_date = Column(String, nullable=True)
    issue_date = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)
    return_date = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="borrow_records")
    book = relationship("Book", back_populates="borrow_records")
    fines = relationship("Fine", back_populates="borrow_record")


class LoanExtensionEvent(Base):
    """Append-only approval history with snapshots that survive profile/catalog changes."""
    __tablename__ = "loan_extension_events"

    id = Column(Integer, primary_key=True, index=True)
    borrow_record_id = Column(Integer, ForeignKey("borrow_records.id", ondelete="SET NULL"), nullable=True, index=True)
    patron_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="SET NULL"), nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reference = Column(String, nullable=False, index=True)
    patron_name = Column(String, nullable=False)
    institutional_id = Column(String, nullable=False)
    patron_role = Column(String(20), nullable=False)
    department = Column(String, nullable=False)
    course = Column(String, nullable=False)
    book_title = Column(String, nullable=False)
    accession_no = Column(String, nullable=False)
    copy_accession_no = Column(String, nullable=True)
    actor_name = Column(String, nullable=False)
    actor_username = Column(String, nullable=False)
    actor_role = Column(String(20), nullable=False)
    old_due_date = Column(DateTime, nullable=False)
    new_due_date = Column(DateTime, nullable=False)
    reason = Column(Text, nullable=False)
    approved_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class BorrowApprovalEmail(Base):
    __tablename__ = "borrow_approval_emails"

    id = Column(Integer, primary_key=True)
    borrow_record_id = Column(Integer, ForeignKey("borrow_records.id"), unique=True, nullable=False, index=True)
    status = Column(String(16), default="pending", nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    last_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_at = Column(DateTime, nullable=True)


class Fine(Base):
    __tablename__ = "fines"

    id = Column(Integer, primary_key=True, index=True)
    borrow_record_id = Column(Integer, ForeignKey("borrow_records.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    is_paid = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    paid_at = Column(DateTime, nullable=True)

    borrow_record = relationship("BorrowRecord", back_populates="fines")
    user = relationship("User", back_populates="fines")


class AcquisitionSuggestion(Base):
    __tablename__ = "acquisitions"

    id = Column(Integer, primary_key=True, index=True)
    acq_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    author = Column(String, nullable=False)
    publisher_isbn = Column(String, nullable=True)
    justification = Column(Text, nullable=False)
    status = Column(String, default="Under Review", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="acquisitions")


class StaffArchive(Base):
    __tablename__ = "staff_archives"
    # Operational archive markers hide the retained records from active lists.
    # Archive browsing and restoration are reserved for SuperAdmin.
    key = Column(String, primary_key=True)
    reason = Column(Text, nullable=False)
    archived_by = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DeletedArchive(Base):
    __tablename__ = "deleted_archives"
    # Retained snapshots have no foreign keys: deleting an active record must
    # never destroy the evidence kept for SuperAdmin review.
    id = Column(String(36), primary_key=True)
    kind = Column(String(32), nullable=False, index=True)
    record_id = Column(String(80), nullable=False)
    title = Column(String(250), nullable=False)
    action = Column(String(32), nullable=False)
    origin = Column(String(32), nullable=False)
    deleted_by = Column(String(120), nullable=False)
    deleted_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    content_json = Column(Text, nullable=False)


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    category = Column(String, default="General Inquiry", nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Announcement(Base):
    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(160), nullable=False)
    body = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(160), nullable=False)
    body = Column(Text, nullable=False)
    kind = Column(String(32), default="library", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    read_at = Column(DateTime, nullable=True)


class SupportThread(Base):
    __tablename__ = "support_threads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    recipient_role = Column(String(20), default="librarian", server_default="librarian", nullable=False, index=True)
    subject = Column(String(160), nullable=False)
    is_closed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("support_threads.id"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_staff = Column(Boolean, default=False, nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    read_by_user = Column(Boolean, default=False, nullable=False)
    read_by_staff = Column(Boolean, default=False, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    user_name = Column(String, nullable=False)
    event = Column(String, nullable=False)
    status = Column(String, nullable=False)


class SmtpLog(Base):
    __tablename__ = "smtp_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    recipient = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    status = Column(String, default="Delivered (200 OK)", nullable=False)


class LibraryEmail(Base):
    __tablename__ = "library_emails"
    id = Column(Integer, primary_key=True)
    event_key = Column(String, unique=True, nullable=False)
    recipient = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String, default="pending", nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    next_attempt_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_error = Column(String, nullable=True)
