import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import security
from database import Base, get_db
from main import app
from models import MfaChallenge, RateLimitBucket, User, UserRole, UserSession
from routers import auth_router
from security import create_access_token, get_password_hash, verify_password
from mfa_security import matching_totp_counter, totp_code
from rate_limits import enforce_rate_limit


@pytest.fixture
def staff_client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'mfa.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        staff = User(username="mfa-staff", email="mfa-staff@evsu.edu.ph", full_name="MFA Staff",
                     role=UserRole.SUPERADMIN, hashed_password=get_password_hash("Secure1!pass"),
                     email_verified=True)
        student = User(username="mfa-student", email="mfa-student@evsu.edu.ph", full_name="MFA Student",
                       role=UserRole.STUDENT, hashed_password=get_password_hash("Secure1!pass"),
                       email_verified=True)
        db.add_all([staff, student])
        db.flush()
        db.add(UserSession(session_id="legacy-staff-session", user_id=staff.id,
                           created_at=datetime.utcnow(), last_activity_at=datetime.utcnow(),
                           expires_at=datetime.utcnow() + timedelta(hours=1)))
        db.commit()

    def override_db():
        with factory() as db:
            yield db

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setenv("SMARTLIB_ENV", "development")
    monkeypatch.setattr(auth_router, "staff_mfa_required", lambda: True)
    monkeypatch.setattr(security, "staff_mfa_required", lambda: True)
    client = TestClient(app, base_url="https://testserver")
    yield client, factory
    client.close()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def test_totp_matches_rfc_vector_and_accepts_only_six_digits():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret, at=59) == "287082"
    assert matching_totp_counter(secret, "287082", at=59) == 1
    assert matching_totp_counter(secret, "28708x", at=59) is None


def test_staff_login_requires_setup_then_mfa_and_recovery_code_is_one_time(staff_client):
    client, factory = staff_client
    login = client.post("/auth/login", json={"username": "mfa-staff", "password": "Secure1!pass"},
                        headers={"X-SmartLib-Web-Session": "1"})
    assert login.status_code == 200
    assert login.json()["mfa_setup_required"] is True
    setup_token = login.json()["setup_token"]
    assert login.json()["access_token"] is None

    setup = client.post("/auth/mfa/setup", json={"setup_token": setup_token})
    assert setup.status_code == 200
    assert setup.json()["qr_code"].startswith("data:image/png;base64,")
    manual_key = setup.json()["manual_key"]
    assert manual_key

    from mfa_security import totp_code
    enabled = client.post("/auth/mfa/enable", json={"setup_token": setup_token, "code": totp_code(manual_key)},
                          headers={"X-SmartLib-Web-Session": "1"})
    assert enabled.status_code == 200
    recovery_codes = enabled.json()["recovery_codes"]
    assert len(recovery_codes) == 10
    assert enabled.json()["access_token"] is None
    assert "smartlib_web_session" in client.cookies
    assert client.get("/auth/me").status_code == 200

    with factory() as db:
        user = db.query(User).filter_by(username="mfa-staff").one()
        assert user.mfa_enabled is True
        assert user.mfa_secret_enc != manual_key
        assert len(json.loads(user.mfa_recovery_hashes)) == 10
        assert db.query(UserSession).filter_by(session_id="legacy-staff-session").one().revoked_at is not None

    # A password alone now yields only a short-lived MFA challenge.
    next_login = client.post("/auth/login", json={"username": "mfa-staff", "password": "Secure1!pass"},
                             headers={"X-SmartLib-Web-Session": "1"})
    assert next_login.status_code == 200
    assert next_login.json()["mfa_required"] is True
    challenge = next_login.json()["challenge_token"]
    verified = client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": recovery_codes[0]},
                           headers={"X-SmartLib-Web-Session": "1"})
    assert verified.status_code == 200
    assert client.get("/auth/me").status_code == 200
    replay = client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": recovery_codes[0]})
    assert replay.status_code == 401

    again = client.post("/auth/login", json={"username": "mfa-staff", "password": "Secure1!pass"})
    assert again.json()["mfa_required"] is True
    reused = client.post("/auth/mfa/verify", json={"challenge_token": again.json()["challenge_token"],
                                                   "code": recovery_codes[0]})
    assert reused.status_code == 401


def test_staff_cookie_session_is_http_only_and_not_returned_as_a_bearer(staff_client, monkeypatch):
    client, _ = staff_client
    # Disable mandatory MFA just for this cookie transport test; this is a dev-mode login.
    monkeypatch.setattr(auth_router, "staff_mfa_required", lambda: False)
    monkeypatch.setattr(security, "staff_mfa_required", lambda: False)
    response = client.post("/auth/login", json={"username": "mfa-student", "password": "Secure1!pass", "remember_me": True},
                           headers={"X-SmartLib-Web-Session": "1"})
    assert response.status_code == 200
    assert response.json()["access_token"] is None
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    profile = client.get("/auth/me")
    assert profile.status_code == 200 and profile.json()["role"] == "student"
    blocked = client.post("/auth/logout")
    assert blocked.status_code == 403
    cross_origin = client.post("/auth/logout", headers={"Origin": "https://attacker.example"})
    assert cross_origin.status_code == 403
    logout = client.post("/auth/logout", headers={"Origin": "https://testserver"})
    assert logout.status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_password_hashing_handles_long_passwords_without_bcrypt_truncation():
    password = "Ab3!" + "long phrase " * 4 + "z"
    hashed = get_password_hash(password)
    assert verify_password(password, hashed)
    assert not verify_password(password[:-1] + "x", hashed)


def test_database_rate_limit_is_shared_and_stores_only_a_key_hash(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'limits.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        enforce_rate_limit("login-account:private@example.test", 1, 60, db=db)
        with pytest.raises(HTTPException) as error:
            enforce_rate_limit("login-account:private@example.test", 1, 60, db=db)
        assert getattr(error.value, "status_code", None) == 429
        bucket = db.query(RateLimitBucket).one()
        assert "private@example.test" not in bucket.bucket_key
        assert bucket.hits == 2
    engine.dispose()
