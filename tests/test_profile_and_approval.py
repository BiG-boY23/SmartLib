"""Registration, own-profile updates, and approval delivery behavior."""
import asyncio
import io
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import inspect

from database import Base, get_db
from email_service import EmailDeliveryError
from models import (Book, BorrowApprovalEmail, BorrowRecord, BorrowStatus,
                    Notification, SignupOption, User, UserRole, UserSession)
from routers import admin_router, auth_router
from security import create_access_token, get_password_hash


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(auth_router.router)
    app.include_router(admin_router.router)

    def db_session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_session
    headers = {}
    with factory() as db:
        for name, role in (("staff", UserRole.LIBRARIAN), ("reader", UserRole.STUDENT), ("other", UserRole.STUDENT)):
            user = User(username=name, email=f"{name}@evsu.edu.ph", hashed_password=get_password_hash("Strong1!"),
                        full_name=f"Existing {name}", role=role, email_verified=True)
            db.add(user)
            db.flush()
            sid = f"profile-test-{name}"
            db.add(UserSession(session_id=sid, user_id=user.id, created_at=datetime.utcnow(),
                               last_activity_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1)))
            headers[name] = {"Authorization": "Bearer " + create_access_token({"sub": name, "sid": sid, "role": role.value})}
        department = SignupOption(kind="department", name="Computer Studies")
        db.add(department)
        db.flush()
        course = SignupOption(kind="course", name="BSIT", department_id=department.id)
        db.add(course)
        book = Book(accession_no="LIB-TEST", title="Library Systems", author="Library", category="Computers", total_copies=2, available_copies=2)
        db.add(book)
        db.flush()
        reader = db.query(User).filter_by(username="reader").one()
        db.add(BorrowRecord(req_id="REQ-EMAIL-1", user_id=reader.id, book_id=book.id,
                            status=BorrowStatus.PENDING, pickup_date="2026-10-20T10:00"))
        db.commit()
        option_ids = (department.id, course.id)
    monkeypatch.setattr(auth_router, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_router, "__file__", str(tmp_path / "routers" / "auth_router.py"))
    yield TestClient(app), factory, headers, option_ids
    engine.dispose()


def test_registration_middle_name_optional_and_saved(api, monkeypatch):
    client, factory, _, (department_id, course_id) = api
    codes = {}
    monkeypatch.setattr(auth_router, "ensure_smtp_configured", lambda: None)
    monkeypatch.setattr(auth_router, "_send_otp", lambda email, purpose, code: codes.__setitem__(email, code))
    for middle, expected in (("", "Ana Santos"), ("Maria", "Ana Maria Santos")):
        email = f"ana{middle.lower()}@evsu.edu.ph"
        payload = {"username": f"id-{middle or 'none'}", "email": email, "password": "Strong1!",
                   "confirm_password": "Strong1!", "first_name": "Ana", "middle_name": middle,
                   "last_name": "Santos", "account_type": "student", "department_id": department_id, "course_id": course_id}
        assert client.post("/auth/register/request-otp", json=payload).status_code == 202
        assert client.post("/auth/register/verify-otp", json={"email": email, "otp": codes[email]}).status_code == 201
        with factory() as db:
            user = db.query(User).filter_by(email=email).one()
            assert user.full_name == expected
            assert user.middle_name == (middle or None)


def test_own_profile_update_and_photo(api):
    client, factory, headers, _ = api
    original = client.get("/auth/me", headers=headers["reader"]).json()
    assert original["full_name"] == "Existing reader" and original["middle_name"] is None
    payload = {"first_name": "Ana", "middle_name": "Maria", "last_name": "Santos", "contact_no": "+63 912 345 6789"}
    response = client.put("/auth/profile", json=payload, headers=headers["reader"])
    assert response.status_code == 200
    assert response.json()["full_name"] == "Ana Maria Santos"
    assert client.get("/auth/me", headers=headers["reader"]).json()["contact_no"] == payload["contact_no"]
    assert client.put("/auth/profile", json={**payload, "email": "changed@evsu.edu.ph"}, headers=headers["reader"]).status_code == 422
    assert client.get("/auth/me", headers=headers["other"]).json()["full_name"] == "Existing other"
    image = Image.new("RGB", (32, 32), "#730d24")
    content = io.BytesIO()
    image.save(content, format="PNG")
    response = client.post("/auth/me/profile-photo", files={"photo": ("avatar.png", content.getvalue(), "image/png")}, headers=headers["reader"])
    assert response.status_code == 200
    assert response.json()["profile_picture_url"].startswith("/profile-photos/")
    assert client.post("/auth/me/profile-photo", files={"photo": ("bad.png", b"not an image", "image/png")}, headers=headers["reader"]).status_code == 400
    with factory() as db:
        assert db.query(User).filter_by(username="reader").one().profile_picture_url == response.json()["profile_picture_url"]


def test_approval_email_failure_retry_and_no_duplicate(api, monkeypatch):
    client, factory, headers, _ = api
    attempts = []

    def fail_once(recipient, subject, body):
        attempts.append((recipient, subject, body))
        if len(attempts) == 1:
            raise EmailDeliveryError("SMTP temporarily unavailable")

    monkeypatch.setattr(admin_router, "send_email", fail_once)
    url = "/admin/borrow-requests/REQ-EMAIL-1/status"
    result = client.put(url, json={"status": "APPROVED"}, headers=headers["staff"])
    assert result.status_code == 200 and result.json()["status"] == "APPROVED"
    with factory() as db:
        record = db.query(BorrowRecord).filter_by(req_id="REQ-EMAIL-1").one()
        delivery = db.query(BorrowApprovalEmail).filter_by(borrow_record_id=record.id).one()
        assert delivery.status == "failed" and delivery.attempts == 1
        assert db.query(Notification).filter_by(user_id=record.user_id, title="Borrow request approved").count() == 1
    assert client.put(url, json={"status": "APPROVED"}, headers=headers["staff"]).status_code == 409
    retry = client.post("/admin/borrow-requests/REQ-EMAIL-1/approval-email/retry", headers=headers["staff"])
    assert retry.status_code == 200 and retry.json() == {"status": "sent", "attempts": 2}
    assert "Library Systems" in attempts[-1][2] and "REQ-EMAIL-1" in attempts[-1][2]
    assert "Existing reader" in attempts[-1][2] and "Pickup schedule" in attempts[-1][2]
    assert client.post("/admin/borrow-requests/REQ-EMAIL-1/approval-email/retry", headers=headers["staff"]).json()["status"] == "sent"
    assert len(attempts) == 2


def test_legacy_accounts_gain_nullable_name_columns(tmp_path, monkeypatch):
    import main

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("""CREATE TABLE users (
            id INTEGER PRIMARY KEY, username VARCHAR NOT NULL UNIQUE, email VARCHAR NOT NULL UNIQUE,
            hashed_password VARCHAR NOT NULL, full_name VARCHAR NOT NULL, role VARCHAR NOT NULL,
            department VARCHAR, course VARCHAR, contact_no VARCHAR, is_active BOOLEAN NOT NULL,
            email_verified BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME NOT NULL)""")
        connection.exec_driver_sql("""CREATE TABLE pending_registrations (
            id INTEGER PRIMARY KEY, username VARCHAR NOT NULL UNIQUE, email VARCHAR NOT NULL UNIQUE,
            hashed_password VARCHAR NOT NULL, full_name VARCHAR NOT NULL, role VARCHAR NOT NULL,
            department VARCHAR, course VARCHAR, otp_hash VARCHAR NOT NULL, expires_at DATETIME NOT NULL,
            attempts INTEGER NOT NULL, created_at DATETIME NOT NULL)""")
        connection.exec_driver_sql("""INSERT INTO users
            (username, email, hashed_password, full_name, role, is_active, email_verified, created_at)
            VALUES ('legacy', 'legacy@evsu.edu.ph', 'old-hash', 'Legacy Student', 'STUDENT', 1, 1, '2026-01-01')""")
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=engine))

    async def migrate():
        async with main.lifespan(main.app):
            pass

    asyncio.run(migrate())
    assert {"first_name", "middle_name", "last_name", "profile_picture_url"} <= {
        column["name"] for column in inspect(engine).get_columns("users")
    }
    with sessionmaker(bind=engine)() as db:
        user = db.query(User).filter_by(username="legacy").one()
        assert user.full_name == "Legacy Student"
        assert user.middle_name is None and user.profile_picture_url is None
    engine.dispose()
