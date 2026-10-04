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


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.LIBRARIAN, nullable=False)
    department = Column(String, nullable=True)
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
    role = Column(Enum(UserRole), nullable=False)
    department = Column(String, nullable=True)
    otp_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
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
    category = Column(String, index=True, nullable=False)  # Subject/Category
    call_number = Column(String, nullable=True)
    location_rack = Column(String, default="Rack A-1", nullable=True)
    total_copies = Column(Integer, default=1, nullable=False)
    available_copies = Column(Integer, default=1, nullable=False)
    condition = Column(String, default="Good", nullable=True)  # Book Condition
    is_archived = Column(Boolean, default=False, nullable=False)  # Soft-delete/archive flag
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    borrow_records = relationship("BorrowRecord", back_populates="book")


class BorrowRecord(Base):
    __tablename__ = "borrow_records"

    id = Column(Integer, primary_key=True, index=True)
    req_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False)
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


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    category = Column(String, default="General Inquiry", nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


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
