"""Release blockers: privilege escalation, secret exposure and lost persistent data."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import crud
from app_releases import app_releases
from database import Base, get_db
from main import app
from models import (User, UserRole, UserSession, PendingRegistration, EmailActionToken,
                    Book, BorrowRecord, BorrowStatus, Fine, AuditLog)
from photo_security import signed_photo_url
from production import prepare_production_accounts, validate_production_settings
from routers.auth_router import _hash_action_token
from security import create_access_token, get_password_hash


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'release.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    password = get_password_hash("Secure1!pass")
    with factory() as db:
        reader = User(username="pilot-reader", email="pilot-reader@evsu.edu.ph", full_name="Pilot Reader",
                      hashed_password=password, role=UserRole.STUDENT, email_verified=True)
        db.add(reader)
        db.flush()
        db.add(UserSession(session_id="pilot-session", user_id=reader.id, created_at=datetime.utcnow(),
                           last_activity_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1)))
        db.commit()
    def override():
        with factory() as db:
            yield db
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "pilot-reader", "sid": "pilot-session"})}
    monkeypatch.setenv("SMARTLIB_ENV", "development")
    yield TestClient(app), factory, headers
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


@pytest.mark.parametrize("role", ["superadmin", "admin", "librarian"])
def test_public_signup_cannot_request_staff_role(isolated, role):
    client, factory, _ = isolated
    payload = dict(username="intruder", email="intruder@evsu.edu.ph", password="Secure1!pass",
                   confirm_password="Secure1!pass", first_name="Pilot", last_name="Reader", account_type=role)
    assert client.post("/auth/register/request-otp", json=payload).status_code == 422
    with factory() as db:
        assert db.query(PendingRegistration).count() == 0


def test_old_privileged_pending_signup_is_invalidated(isolated):
    client, factory, _ = isolated
    with factory() as db:
        db.add(PendingRegistration(username="old-privileged", email="old@evsu.edu.ph", full_name="Old Pending",
            hashed_password="unused", role=UserRole.SUPERADMIN, otp_hash=_hash_action_token("123456"),
            expires_at=datetime.utcnow() + timedelta(minutes=5)))
        db.commit()
    assert client.post("/auth/register/verify-otp", json={"email": "old@evsu.edu.ph", "otp": "123456"}).status_code == 400
    with factory() as db:
        assert db.query(PendingRegistration).count() == 0
        assert db.query(User).filter_by(username="old-privileged").first() is None


def test_password_change_revokes_existing_reset_grants(isolated):
    client, factory, headers = isolated
    with factory() as db:
        reader = db.query(User).one()
        for purpose in ("password_reset", "mobile_reset_otp", "mobile_reset_verified"):
            db.add(EmailActionToken(user_id=reader.id, purpose=purpose, token_hash=_hash_action_token(purpose * 2),
                                   expires_at=datetime.utcnow() + timedelta(minutes=5)))
        db.commit()
    result = client.post("/auth/change-password", headers=headers,
                         json={"current_password": "Secure1!pass", "new_password": "Changed1!ok", "confirm_new_password": "Changed1!ok"})
    assert result.status_code == 200
    assert client.post("/auth/forgot-password/complete", json={"token": "mobile_reset_verified" * 2,
        "new_password": "Secure1!pass", "confirm_new_password": "Secure1!pass"}).status_code == 400
    with factory() as db:
        assert db.query(EmailActionToken).filter(EmailActionToken.consumed_at.is_(None)).count() == 0


def test_private_files_and_unsigned_profile_photos_not_public(isolated, tmp_path, monkeypatch):
    import main
    client, _, _ = isolated
    monkeypatch.setattr(main, "PROFILE_PHOTOS", tmp_path)
    (tmp_path / "avatar.jpg").write_bytes(b"test image bytes")
    for path in ("/.env", "/.env.example", "/library.db", "/.git/config", "/profile-photos/avatar.jpg"):
        assert client.get(path).status_code in (403, 404)
    url = signed_photo_url("/profile-photos/avatar.jpg")
    result = client.get(url)
    assert result.status_code == 200 and result.content == b"test image bytes"
    assert "no-store" in result.headers["cache-control"]
    assert client.get(url + "tampered").status_code == 403


def test_release_links_only_enable_real_https_distribution_urls(monkeypatch):
    monkeypatch.setenv("ANDROID_APK_URL", "http://192.168.1.10/app.apk")
    monkeypatch.setenv("IOS_TESTFLIGHT_URL", "https://attacker.test/join/abc")
    assert not app_releases()["android"]["available"]
    assert not app_releases()["ios"]["available"]
    monkeypatch.setenv("ANDROID_APK_URL", "https://downloads.evsu.edu.ph/smartlib.apk")
    monkeypatch.setenv("IOS_TESTFLIGHT_URL", "https://testflight.apple.com/join/AbC123xy")
    assert app_releases()["android"]["available"] and app_releases()["ios"]["available"]
    monkeypatch.setenv("IOS_TESTFLIGHT_URL", "https://[invalid")
    assert not app_releases()["ios"]["available"]


def test_production_rejects_missing_settings_and_does_not_seed_demo(isolated, monkeypatch, tmp_path):
    _, factory, _ = isolated
    monkeypatch.setenv("SMARTLIB_ENV", "production")
    for key in ("SMARTLIB_SECRET_KEY", "DATABASE_URL", "PUBLIC_BASE_URL", "SMARTLIB_UPLOAD_DIR"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="Production configuration is incomplete"):
        validate_production_settings()
    monkeypatch.setenv("SMARTLIB_SECRET_KEY", "a" * 48)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'persistent.db'}")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://library.evsu.edu.ph")
    monkeypatch.setenv("SMARTLIB_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SMARTLIB_SEED_DEMO", "false")
    validate_production_settings()
    with factory() as db:
        crud.seed_initial_data(db)
        assert db.query(User).count() == 1
        with pytest.raises(RuntimeError, match="first deployment"):
            prepare_production_accounts(db)
        monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "new-library-superadmin")
        monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "new-admin@evsu.edu.ph")
        monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Admin9!pilot")
        prepare_production_accounts(db)
        prepare_production_accounts(db)
        assert db.query(User).filter_by(role=UserRole.SUPERADMIN).count() == 1
        assert db.query(User).filter(User.username.in_(["ADMIN-EVSU-2026", "EVSU-LIB-2026"])).count() == 0


def test_all_checkout_paths_honor_fines_and_audit_staff_actor(isolated):
    _, factory, _ = isolated
    with factory() as db:
        reader = db.query(User).one()
        book = Book(accession_no="PILOT-BOOK", title="Pilot Book", author="Library", category="Reference", total_copies=2, available_copies=2)
        db.add(book)
        db.flush()
        approved = BorrowRecord(req_id="PILOT-APPROVED", user_id=reader.id, book_id=book.id, status=BorrowStatus.APPROVED)
        db.add(approved)
        db.flush()
        fine = Fine(user_id=reader.id, borrow_record_id=approved.id, amount=5, is_paid=False)
        db.add(fine)
        db.commit()
        assert crud.execute_circulation(db, "borrow_clear", reader.username, book.accession_no)["status"] == "blocked"
        assert crud.update_borrow_status(db, approved.req_id, BorrowStatus.CHECKED_OUT)[0] is None
        fine.is_paid = True
        db.commit()
        assert crud.execute_circulation(db, "borrow", reader.username, book.accession_no, actor="pilot-librarian")["status"] == "success"
        assert db.query(AuditLog).filter_by(user_name="pilot-librarian").count() == 1
        active = db.query(BorrowRecord).filter_by(status=BorrowStatus.CHECKED_OUT).one()
        db.add(BorrowRecord(req_id="DUPLICATE", user_id=reader.id, book_id=book.id,
                           status=BorrowStatus.CHECKED_OUT, copy_accession_no=active.copy_accession_no))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_chunked_body_limit_rejects_before_application_parsing():
    import asyncio
    from deployment_middleware import RequestSizeLimit
    called, sent = [], []
    chunks = iter([{"type": "http.request", "body": b"12345", "more_body": True},
                   {"type": "http.request", "body": b"67890", "more_body": False}])
    async def receive():
        return next(chunks)
    async def send(message):
        sent.append(message)
    async def application(scope, receive, send):
        called.append(True)
    asyncio.run(RequestSizeLimit(application, maximum=8)(
        {"type": "http", "method": "POST", "path": "/auth/login"}, receive, send))
    assert not called
    assert sent[0]["status"] == 413


def test_private_health_probe_works_without_disabling_user_https():
    from fastapi import FastAPI
    from deployment_middleware import PublicHTTPSRedirect
    probe = FastAPI()
    probe.add_middleware(PublicHTTPSRedirect)
    @probe.get("/ready")
    def ready():
        return {"status": "ready"}
    client = TestClient(probe)
    assert client.get("/ready", follow_redirects=False).status_code == 200
    response = client.get("/auth/me", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://testserver/auth/me"
