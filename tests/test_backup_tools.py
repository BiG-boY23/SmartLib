import sqlite3

import pytest
from sqlalchemy import create_engine, text

from scripts.backup import create_backup
from scripts.restore_backup import stage_backup


def test_backup_is_encrypted_and_can_be_staged_without_overwriting_source(tmp_path, monkeypatch):
    database = tmp_path / "library.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (value TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO sample(value) VALUES ('preserved')"))
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "profile.jpg").write_bytes(b"private-photo")
    backup_dir = tmp_path / "offsite-copy"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    monkeypatch.setenv("SMARTLIB_UPLOAD_DIR", str(uploads))
    monkeypatch.setenv("SMARTLIB_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("SMARTLIB_BACKUP_ENCRYPTION_KEY", "test-key-" + "x" * 40)

    backup = create_backup()
    assert backup.is_file() and backup.read_bytes().startswith(b"SLBK1")
    assert b"private-photo" not in backup.read_bytes()

    restore_dir = tmp_path / "staged-restore"
    path, manifest = stage_backup(backup, restore_dir)
    assert path == restore_dir
    assert manifest["database_type"] == "sqlite"
    assert (restore_dir / "uploads" / "profile.jpg").read_bytes() == b"private-photo"
    with sqlite3.connect(restore_dir / manifest["database_file"]) as restored:
        assert restored.execute("SELECT value FROM sample").fetchone() == ("preserved",)
    with sqlite3.connect(database) as original:
        assert original.execute("SELECT value FROM sample").fetchone() == ("preserved",)
    engine.dispose()


def test_restore_rejects_tampered_backup_and_does_not_leave_staged_files(tmp_path, monkeypatch):
    database = tmp_path / "library.sqlite"
    sqlite3.connect(database).close()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    monkeypatch.setenv("SMARTLIB_UPLOAD_DIR", str(uploads))
    monkeypatch.setenv("SMARTLIB_BACKUP_DIR", str(tmp_path / "backup-out"))
    monkeypatch.setenv("SMARTLIB_BACKUP_ENCRYPTION_KEY", "test-key-" + "x" * 40)
    backup = create_backup()
    changed = bytearray(backup.read_bytes())
    changed[-1] ^= 1
    backup.write_bytes(changed)
    output = tmp_path / "must-not-exist"
    with pytest.raises(RuntimeError, match="authentication failed"):
        stage_backup(backup, output)
    assert not output.exists()
