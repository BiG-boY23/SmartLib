from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

import backup_service
from scripts.backup import create_backup
from backup_service import (
    BackupSettings,
    download_remote_backup,
    list_remote_backups,
    load_backup_settings,
    prune_expired_backups,
    run_scheduled_backup,
    upload_backup,
)


class FakePaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, *, Bucket, Prefix):
        return [{"Contents": [
            {"Key": key, "LastModified": item["last_modified"], "Size": len(item["body"])}
            for key, item in self.client.objects.items()
            if item["bucket"] == Bucket and key.startswith(Prefix)
        ]}]


class FakeS3:
    def __init__(self):
        self.objects = {}

    def upload_file(self, filename, bucket, key, *, ExtraArgs):
        self.objects[key] = {
            "bucket": bucket,
            "body": Path(filename).read_bytes(),
            "metadata": ExtraArgs["Metadata"],
            "last_modified": datetime.now(timezone.utc),
        }

    def head_object(self, *, Bucket, Key):
        item = self.objects[Key]
        return {"ContentLength": len(item["body"]), "Metadata": item["metadata"]}

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return FakePaginator(self)

    def delete_objects(self, *, Bucket, Delete):
        for item in Delete["Objects"]:
            self.objects.pop(item["Key"], None)
        return {}

    def download_file(self, bucket, key, destination):
        Path(destination).write_bytes(self.objects[key]["body"])


def settings(*, retention_days=30, backup_dir=None):
    return BackupSettings(
        enabled=True,
        bucket="smartlib-private-backups",
        region="ap-southeast-1",
        access_key_id="test-access",
        secret_access_key="test-secret",
        endpoint_url=None,
        prefix="smartlib",
        interval_hours=24,
        retention_days=retention_days,
        backup_dir=backup_dir or Path("/tmp/smartlib-backups"),
    )


def configure_enabled_backup(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTLIB_BACKUP_ENABLED", "true")
    monkeypatch.setenv("SMARTLIB_BACKUP_S3_BUCKET", "smartlib-private-backups")
    monkeypatch.setenv("SMARTLIB_BACKUP_S3_REGION", "ap-southeast-1")
    monkeypatch.setenv("SMARTLIB_BACKUP_S3_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("SMARTLIB_BACKUP_S3_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("SMARTLIB_BACKUP_S3_PREFIX", "smartlib")
    monkeypatch.setenv("SMARTLIB_BACKUP_INTERVAL_HOURS", "24")
    monkeypatch.setenv("SMARTLIB_BACKUP_RETENTION_DAYS", "30")
    monkeypatch.setenv("SMARTLIB_BACKUP_ENCRYPTION_KEY", "separate-test-backup-key-12345678")
    monkeypatch.setenv("SMARTLIB_BACKUP_DIR", str(tmp_path / "backups"))


def test_scheduled_backups_require_an_explicit_retention_period(monkeypatch):
    configure_enabled_backup(monkeypatch, Path("/tmp"))
    monkeypatch.delenv("SMARTLIB_BACKUP_RETENTION_DAYS")
    with pytest.raises(RuntimeError, match="approved retention period"):
        load_backup_settings(require_enabled=True)


def test_upload_and_download_verify_encrypted_archive_hash(tmp_path):
    source = tmp_path / "smartlib-20261114T000000Z.slbk"
    source.write_bytes(b"SLBK1 encrypted payload")
    client = FakeS3()
    config = settings()
    key = upload_backup(source, config, client)
    assert key == "smartlib/" + source.name
    assert client.objects[key]["metadata"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()

    downloaded = download_remote_backup(key, tmp_path / "download.slbk", config, client)
    assert downloaded.read_bytes() == source.read_bytes()
    with pytest.raises(RuntimeError, match="outside this application's backup prefix"):
        download_remote_backup("other/backup.slbk", tmp_path / "bad.slbk", config, client)


def test_upload_detects_post_upload_integrity_mismatch(tmp_path):
    source = tmp_path / "smartlib-20261114T000000Z.slbk"
    source.write_bytes(b"encrypted payload")

    class CorruptingS3(FakeS3):
        def head_object(self, *, Bucket, Key):
            result = super().head_object(Bucket=Bucket, Key=Key)
            result["ContentLength"] += 1
            return result

    with pytest.raises(RuntimeError, match="post-upload integrity"):
        upload_backup(source, settings(), CorruptingS3())


def test_archive_level_recovery_rehearsal_validates_database_and_uploads(tmp_path, monkeypatch):
    database_path = tmp_path / "library.sqlite"
    import sqlite3
    with sqlite3.connect(database_path) as database:
        database.execute("CREATE TABLE sample (value TEXT)")
        database.execute("INSERT INTO sample VALUES ('ready')")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "profile.jpg").write_bytes(b"profile")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("SMARTLIB_UPLOAD_DIR", str(uploads))
    monkeypatch.setenv("SMARTLIB_BACKUP_DIR", str(tmp_path / "backup-output"))
    monkeypatch.setenv("SMARTLIB_BACKUP_ENCRYPTION_KEY", "separate-test-backup-key-12345678")

    manifest = backup_service.verify_backup_archive(create_backup())
    assert manifest["database_type"] == "sqlite"


def test_listing_and_retention_delete_old_files_but_keep_newest():
    client = FakeS3()
    config = settings(retention_days=30)
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    for name, age in (("new.slbk", 2), ("old.slbk", 45), ("oldest.slbk", 90)):
        client.objects[f"smartlib/{name}"] = {
            "bucket": config.bucket,
            "body": b"encrypted",
            "metadata": {"sha256": hashlib.sha256(b"encrypted").hexdigest()},
            "last_modified": now - timedelta(days=age),
        }
    client.objects["other/unrelated.slbk"] = {
        "bucket": config.bucket, "body": b"x", "metadata": {}, "last_modified": now,
    }
    assert [item["key"] for item in list_remote_backups(config, client)] == [
        "smartlib/new.slbk", "smartlib/old.slbk", "smartlib/oldest.slbk"
    ]
    assert prune_expired_backups(config, client, now=now) == 2
    assert set(client.objects) == {"smartlib/new.slbk", "other/unrelated.slbk"}


def test_scheduled_backup_uploads_once_then_waits_for_interval(monkeypatch, tmp_path):
    configure_enabled_backup(monkeypatch, tmp_path)
    client = FakeS3()

    def create(destination_dir):
        destination_dir.mkdir(parents=True, exist_ok=True)
        path = destination_dir / "smartlib-20261114T000000Z.slbk"
        path.write_bytes(b"encrypted scheduled snapshot")
        return path

    monkeypatch.setattr(backup_service, "create_backup", create)
    monkeypatch.setattr(backup_service, "verify_backup_archive", lambda path: {"database_type": "sqlite"})
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    result = run_scheduled_backup(now=now, client=client)
    assert result["status"] == "uploaded"
    assert result["key"].startswith("smartlib/smartlib-")
    assert len(client.objects) == 1
    assert not list((tmp_path / "backups" / "scheduled").glob("*.slbk"))

    later = run_scheduled_backup(now=now + timedelta(hours=2), client=client)
    assert later == {"status": "not_due"}
    assert len(client.objects) == 1
