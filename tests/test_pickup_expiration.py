from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from library_notifications import expire_uncollected_approved_requests
from models import (AuditLog, Book, BorrowRecord, BorrowStatus, Fine, LibraryEmail,
                    Notification, User, UserRole, UserSession)
from routers import admin_router, mobile_router
from security import create_access_token
from staff_activity import StaffActivityEvent


def setup_database():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        reader = User(username="reader", email="reader@evsu.edu.ph", hashed_password="unused",
                      full_name="Test Reader", role=UserRole.STUDENT, email_verified=True)
        librarian = User(username="librarian", email="librarian@evsu.edu.ph", hashed_password="unused",
                         full_name="Test Librarian", role=UserRole.LIBRARIAN, email_verified=True)
        book = Book(accession_no="PICKUP-1", title="Test book", author="Library", category="Reference",
                    total_copies=1, available_copies=1)
        db.add_all([reader, librarian, book])
        db.flush()
        db.add_all([
            BorrowRecord(req_id="EXPIRE-ME", user_id=reader.id, book_id=book.id,
                         status=BorrowStatus.APPROVED, pickup_date="2026-11-12T09:00", notes="Patron note"),
            BorrowRecord(req_id="KEEP-PENDING", user_id=reader.id, book_id=book.id,
                         status=BorrowStatus.PENDING, pickup_date="2026-11-11T09:00"),
            BorrowRecord(req_id="KEEP-CHECKED-OUT", user_id=reader.id, book_id=book.id,
                         status=BorrowStatus.CHECKED_OUT, pickup_date="2026-11-11T09:00"),
            BorrowRecord(req_id="KEEP-UNSCHEDULED", user_id=reader.id, book_id=book.id,
                         status=BorrowStatus.APPROVED),
        ])
        db.commit()
    return engine, factory


def test_pickup_reservation_expires_after_close_once_with_notifications():
    engine, factory = setup_database()
    try:
        with factory() as db:
            before_close = datetime.fromisoformat("2026-11-12T17:59:59+08:00")
            assert expire_uncollected_approved_requests(db, before_close) == 0

            at_close = datetime.fromisoformat("2026-11-12T18:00:00+08:00")
            assert expire_uncollected_approved_requests(db, at_close) == 1
            assert expire_uncollected_approved_requests(db, at_close) == 0

        with factory() as db:
            expired = db.query(BorrowRecord).filter_by(req_id="EXPIRE-ME").one()
            assert expired.status == BorrowStatus.EXPIRED
            assert "Patron note" in expired.notes
            assert "6:00 PM Philippine time" in expired.notes
            assert db.query(BorrowRecord).filter_by(req_id="KEEP-PENDING").one().status == BorrowStatus.PENDING
            assert db.query(BorrowRecord).filter_by(req_id="KEEP-CHECKED-OUT").one().status == BorrowStatus.CHECKED_OUT
            assert db.query(BorrowRecord).filter_by(req_id="KEEP-UNSCHEDULED").one().status == BorrowStatus.APPROVED
            assert db.query(Fine).count() == 0
            assert db.query(Notification).filter_by(user_id=expired.user_id, title="Pickup reservation expired").count() == 1
            assert db.query(LibraryEmail).filter_by(event_key=f"pickup-expired:{expired.id}").count() == 1
            assert db.query(StaffActivityEvent).filter_by(event_key=f"borrow:{expired.id}:EXPIRED").count() == 1
            assert db.query(AuditLog).filter(AuditLog.event.contains("EXPIRE-ME")).count() == 1
    finally:
        engine.dispose()


def test_my_borrowings_applies_expiration_and_returns_expired_status():
    engine, factory = setup_database()
    try:
        app = FastAPI()
        app.include_router(mobile_router.router)
        app.include_router(admin_router.router)

        def db_session():
            with factory() as db:
                yield db

        app.dependency_overrides[get_db] = db_session
        with factory() as db:
            reader = db.query(User).filter_by(username="reader").one()
            sid = "pickup-expiration-session"
            now = datetime.utcnow()
            db.add(UserSession(session_id=sid, user_id=reader.id, created_at=now,
                               last_activity_at=now, expires_at=now + timedelta(hours=1)))
            librarian = db.query(User).filter_by(username="librarian").one()
            staff_sid = "pickup-expiration-staff-session"
            db.add(UserSession(session_id=staff_sid, user_id=librarian.id, created_at=now,
                               last_activity_at=now, expires_at=now + timedelta(hours=1)))
            old_pickup = (datetime.now().date() - timedelta(days=1)).isoformat()
            db.query(BorrowRecord).filter_by(req_id="EXPIRE-ME").update(
                {BorrowRecord.pickup_date: old_pickup}, synchronize_session=False
            )
            db.commit()
            token = create_access_token({"sub": reader.username, "sid": sid, "role": reader.role.value})
            staff_token = create_access_token({"sub": librarian.username, "sid": staff_sid, "role": librarian.role.value})

        with TestClient(app) as client:
            response = client.get("/mobile/my-borrowings", headers={"Authorization": f"Bearer {token}"})
            staff_response = client.get("/admin/borrow-requests?status_filter=EXPIRED",
                                        headers={"Authorization": f"Bearer {staff_token}"})
        assert response.status_code == 200, response.text
        expired = next(row for row in response.json() if row["req_id"] == "EXPIRE-ME")
        assert expired["status"] == "EXPIRED"
        assert staff_response.status_code == 200, staff_response.text
        assert [row["req_id"] for row in staff_response.json()] == ["EXPIRE-ME"]
    finally:
        engine.dispose()
