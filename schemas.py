from datetime import datetime
import re
from typing import Optional, List, Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator, field_validator
from models import UserRole, BorrowStatus


class Token(BaseModel):
    access_token: str
    token_type: str
    role: str
    username: str


class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str = Field(min_length=8, max_length=12)
    confirm_password: str = Field(min_length=8, max_length=12)
    first_name: str = Field(min_length=1, max_length=80)
    middle_name: Optional[str] = Field(default=None, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    account_type: UserRole = UserRole.STUDENT
    department: Optional[str] = None
    course: Optional[str] = None
    department_id: Optional[int] = None
    course_id: Optional[int] = None

    @field_validator("first_name", "middle_name", "last_name")
    @classmethod
    def clean_name(cls, value):
        clean = " ".join(value.split()) if value is not None else None
        if clean is not None and any(ord(char) < 32 for char in clean):
            raise ValueError("Names cannot contain control characters")
        return clean or None

    @model_validator(mode="after")
    def passwords_match(self):
        if not self.first_name or not self.last_name:
            raise ValueError("First and last name are required")
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        validate_password_policy(self.password)
        return self


class EmailAddressRequest(BaseModel):
    email: EmailStr


class EmailActionRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


def validate_password_policy(password: str) -> None:
    if len(password) < 8 or len(password) > 12:
        raise ValueError("Password must be 8 to 12 characters long")
    if not any(char.islower() for char in password):
        raise ValueError("Password must include a lowercase letter")
    if not any(char.isupper() for char in password):
        raise ValueError("Password must include an uppercase letter")
    if not any(char.isdigit() for char in password):
        raise ValueError("Password must include a number")
    if not any(not char.isalnum() for char in password):
        raise ValueError("Password must include a symbol")
class RegistrationOtpRequest(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")


class PasswordResetOtpRequest(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")


class PasswordResetComplete(EmailActionRequest):
    new_password: str = Field(min_length=8, max_length=12)
    confirm_new_password: str = Field(min_length=8, max_length=12)

    @model_validator(mode="after")
    def password_within_bcrypt_limit(self):
        if self.new_password != self.confirm_new_password:
            raise ValueError("Passwords do not match")
        validate_password_policy(self.new_password)
        return self


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_new_password: Optional[str] = None


# User Schemas
class UserBase(BaseModel):
    username: str
    email: str
    full_name: str
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    profile_picture_url: Optional[str] = None
    role: UserRole
    department: Optional[str] = None
    course: Optional[str] = None
    contact_no: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserStaffCreate(BaseModel):
    staff_id: str
    full_name: str
    email: str
    department: str
    password: str


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None
    contact_no: Optional[str] = None


class SelfProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = Field(min_length=1, max_length=80)
    middle_name: Optional[str] = Field(default=None, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    contact_no: Optional[str] = Field(default=None, max_length=30)

    @field_validator("first_name", "middle_name", "last_name")
    @classmethod
    def clean_name(cls, value):
        clean = " ".join(value.split()) if value is not None else None
        return clean or None

    @field_validator("contact_no")
    @classmethod
    def clean_contact(cls, value):
        clean = value.strip() if value else None
        if clean and (not re.fullmatch(r"\+?[0-9 ()-]{7,30}", clean) or sum(char.isdigit() for char in clean) < 7):
            raise ValueError("Enter a valid contact number")
        return clean

    @model_validator(mode="after")
    def required_names(self):
        if not self.first_name or not self.last_name:
            raise ValueError("First and last name are required")
        return self


class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SignupOptionCreate(BaseModel):
    kind: str = Field(pattern=r"^(department|course)$")
    name: str = Field(min_length=2, max_length=120)
    department_id: Optional[int] = None


class SignupOptionUpdate(SignupOptionCreate):
    pass


class SignupOptionResponse(BaseModel):
    id: int
    kind: str
    name: str
    department_id: Optional[int] = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class DigitalIDPayload(BaseModel):
    patron_id: str
    full_name: str
    role: str
    department: str
    course: Optional[str] = None
    is_active: bool
    active_borrows_count: int
    qr_payload: str
    issued_at: str


# Book Schemas
class BookBase(BaseModel):
    accession_no: str
    title: str
    author: str
    publisher: Optional[str] = None
    year_published: Optional[str] = None
    isbn: Optional[str] = None
    cover_url: Optional[str] = None
    category: str  # Subject/Category
    call_number: Optional[str] = None
    location_rack: Optional[str] = "Rack A-1"
    total_copies: int = 1
    available_copies: int = 1
    condition: Optional[str] = "Good"


class BookCreate(BookBase):
    # Covers are stored through the validated multipart upload endpoint, not an arbitrary client URL.
    cover_url: Optional[str] = Field(default=None, exclude=True)


class BookUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    publisher: Optional[str] = None
    year_published: Optional[str] = None
    isbn: Optional[str] = None
    category: Optional[str] = None
    call_number: Optional[str] = None
    location_rack: Optional[str] = None
    total_copies: Optional[int] = None
    available_copies: Optional[int] = None
    condition: Optional[str] = None
    is_archived: Optional[bool] = None


class BookResponse(BookBase):
    id: int
    is_archived: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BookCopyResponse(BaseModel):
    id: int
    book_id: int
    copy_number: int
    accession_no: str
    created_at: datetime
    qr_printed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BookCopiesPrintedRequest(BaseModel):
    copy_ids: List[int] = Field(min_length=1, max_length=500)


# Borrow Record Schemas
class BorrowRequestCreate(BaseModel):
    accession_no: str
    pickup_date: str
    pickup_time: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    notes: Optional[str] = None


class BorrowStatusUpdate(BaseModel):
    status: BorrowStatus


class LoanExtensionRequest(BaseModel):
    due_date: datetime
    reason: str = Field(min_length=3, max_length=500)


class CirculationExecuteRequest(BaseModel):
    mode: str
    patron_id: Optional[str] = None
    accession_no: str


class BorrowRecordResponse(BaseModel):
    id: int
    req_id: str
    user_id: int
    book_id: int
    status: BorrowStatus
    request_date: datetime
    pickup_date: Optional[str] = None
    issue_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    return_date: Optional[datetime] = None
    notes: Optional[str] = None
    user_name: Optional[str] = None
    book_title: Optional[str] = None
    accession_no: Optional[str] = None
    copy_accession_no: Optional[str] = None
    approval_email_status: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# Fine Schemas
class FineResponse(BaseModel):
    id: int
    borrow_record_id: int
    user_id: int
    amount: float
    is_paid: bool
    created_at: datetime
    paid_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# Acquisition Schemas
class AcquisitionCreate(BaseModel):
    title: str
    author: str
    publisher_isbn: Optional[str] = None
    justification: str


class AcquisitionStatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None


class AcquisitionResponse(BaseModel):
    id: int
    acq_id: str
    user_id: int
    title: str
    author: str
    publisher_isbn: Optional[str] = None
    justification: str
    status: str
    created_at: datetime
    requester_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# Feedback Schemas
class FeedbackCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    category: Optional[str] = "General Inquiry"
    message: str = Field(min_length=1, max_length=4000)


class FeedbackResponse(BaseModel):
    id: int
    name: str
    email: str
    category: str
    message: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SupportThreadCreate(BaseModel):
    recipient_role: Literal["librarian", "admin"] = "librarian"
    subject: str = Field(min_length=3, max_length=160)
    message: str = Field(min_length=1, max_length=2000)


class StaffThreadCreate(SupportThreadCreate):
    recipient_username: str = Field(min_length=1, max_length=100)


class SupportMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=1, max_length=4000)


class AssistantTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=500)


class AssistantRequest(BaseModel):
    language: str = Field(default="auto", min_length=2, max_length=40)
    message: str = Field(min_length=2, max_length=500)
    history: List[AssistantTurn] = Field(default_factory=list, max_length=6)


# Audit & Logs
class AuditLogResponse(BaseModel):
    id: int
    timestamp: datetime
    user_name: str
    event: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class SmtpLogResponse(BaseModel):
    id: int
    timestamp: datetime
    recipient: str
    event_type: str
    subject: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class DashboardStatsResponse(BaseModel):
    total_books: int
    catalog_titles: int
    active_borrows: int
    uncollected_fines: float
    registered_patrons: int
    pending_requests: int
