from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from main import app
from models import AuditLog, User, UserRole, UserSession
from security import create_access_token, get_password_hash


@pytest.fixture
def backup_client(tmp_path, monkeypatch):
    database_path = tmp_path / "manual-backup.db"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        superadmin = User(
            username="backup-admin", email="backup-admin@evsu.edu.ph", full_name="Backup Admin",
            hashed_password=get_password_hash("Secure1!backup"), role=UserRole.SUPERADMIN,
            is_active=True, email_verified=True,
        )
        db.add(superadmin)
        db.flush()
        db.add(UserSession(
            session_id="backup-session", user_id=superadmin.id, created_at=datetime.utcnow(),
            last_activity_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1),
        ))
        reader = User(
            username="backup-reader", email="backup-reader@evsu.edu.ph", full_name="Backup Reader",
            hashed_password=get_password_hash("Secure1!reader"), role=UserRole.STUDENT,
            is_active=True, email_verified=True,
        )
        db.add(reader)
        db.flush()
        db.add(UserSession(
            session_id="reader-session", user_id=reader.id, created_at=datetime.utcnow(),
            last_activity_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1),
        ))
        db.commit()

    def override_get_db():
        with factory() as db:
            yield db

    old_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setenv("SMARTLIB_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "avatar.jpg").write_bytes(b"example profile image")
    monkeypatch.setenv("SMARTLIB_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("SMARTLIB_BACKUP_ENCRYPTION_KEY", "a-separate-test-backup-key-with-32-chars")
    client = TestClient(app)
    yield client, factory
    client.close()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old_overrides)
    engine.dispose()


def test_superadmin_can_download_verified_encrypted_backup(backup_client):
    client, factory = backup_client
    token = create_access_token({"sub": "backup-admin", "role": "superadmin", "sid": "backup-session"})
    response = client.post("/admin/backup/download", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.content.startswith(b"SLBK1")
    assert response.headers["content-type"] == "application/octet-stream"
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["content-disposition"].startswith("attachment;")
    with factory() as db:
        audit = db.query(AuditLog).filter_by(user_name="backup-admin").one()
        assert "backup downloaded" in audit.event.lower()


def test_manual_backup_is_restricted_and_reports_missing_key(backup_client, monkeypatch):
    client, _ = backup_client
    reader_token = create_access_token({"sub": "backup-reader", "role": "student", "sid": "reader-session"})
    assert client.post("/admin/backup/download", headers={"Authorization": f"Bearer {reader_token}"}).status_code == 403

    admin_token = create_access_token({"sub": "backup-admin", "role": "superadmin", "sid": "backup-session"})
    monkeypatch.delenv("SMARTLIB_BACKUP_ENCRYPTION_KEY")
    response = client.post("/admin/backup/download", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 503
    assert "SMARTLIB_BACKUP_ENCRYPTION_KEY" in response.json()["detail"]
