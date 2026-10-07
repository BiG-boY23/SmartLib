from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
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
    first_name: str
    last_name: str
    account_type: UserRole = UserRole.STUDENT
    department: Optional[str] = None

    @model_validator(mode="after")
    def passwords_match(self):
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


# User Schemas
class UserBase(BaseModel):
    username: str
    email: str
    full_name: str
    role: UserRole
    department: Optional[str] = None
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


class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DigitalIDPayload(BaseModel):
    patron_id: str
    full_name: str
    role: str
    department: str
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
    category: str  # Subject/Category
    call_number: Optional[str] = None
    location_rack: Optional[str] = "Rack A-1"
    total_copies: int = 1
    available_copies: int = 1
    condition: Optional[str] = "Good"


class BookCreate(BookBase):
    pass


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


# Borrow Record Schemas
class BorrowRequestCreate(BaseModel):
    accession_no: str
    pickup_date: str
    pickup_time: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    notes: Optional[str] = None


class BorrowStatusUpdate(BaseModel):
    status: BorrowStatus


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
    name: str
    email: EmailStr
    category: Optional[str] = "General Inquiry"
    message: str


class FeedbackResponse(BaseModel):
    id: int
    name: str
    email: str
    category: str
    message: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SupportThreadCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=160)
    message: str = Field(min_length=1, max_length=2000)


class SupportMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=1, max_length=4000)


class AssistantRequest(BaseModel):
    message: str = Field(min_length=2, max_length=500)


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
    active_borrows: int
    uncollected_fines: float
    digital_reads: int
