from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole, Book, BorrowRecord, BorrowStatus
from schemas import (
    BookResponse,
    BorrowRequestCreate,
    BorrowRecordResponse,
    AcquisitionCreate,
    AcquisitionResponse,
    DigitalIDPayload,
)
import crud
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
