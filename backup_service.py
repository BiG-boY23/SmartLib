"""Scheduled, encrypted off-site backups using an S3-compatible object store."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
from urllib.parse import urlsplit

from scripts.backup import create_backup
from scripts.restore_backup import stage_backup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackupSettings:
    enabled: bool
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    endpoint_url: str | None
    prefix: str
    interval_hours: int
    retention_days: int
    backup_dir: Path


def load_backup_settings(*, require_enabled: bool = False) -> BackupSettings:
    """Read and validate backup settings without exposing any secret values."""
    enabled = os.getenv("SMARTLIB_BACKUP_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    if require_enabled and not enabled:
        raise RuntimeError("Scheduled backups are disabled. Set SMARTLIB_BACKUP_ENABLED=true to enable them.")

    bucket = os.getenv("SMARTLIB_BACKUP_S3_BUCKET", "").strip()
    region = os.getenv("SMARTLIB_BACKUP_S3_REGION", "").strip()
    access_key_id = os.getenv("SMARTLIB_BACKUP_S3_ACCESS_KEY_ID", "").strip()
    secret_access_key = os.getenv("SMARTLIB_BACKUP_S3_SECRET_ACCESS_KEY", "")
    endpoint_url = os.getenv("SMARTLIB_BACKUP_S3_ENDPOINT_URL", "").strip() or None
    raw_prefix = os.getenv("SMARTLIB_BACKUP_S3_PREFIX", "smartlib").strip().strip("/")
    raw_interval = os.getenv("SMARTLIB_BACKUP_INTERVAL_HOURS", "24").strip()
    raw_retention = os.getenv("SMARTLIB_BACKUP_RETENTION_DAYS", "").strip()
    backup_dir = Path(os.getenv("SMARTLIB_BACKUP_DIR", "./backups")).expanduser()

    storage_configured = any((bucket, region, access_key_id, secret_access_key, endpoint_url))
    if not enabled and not require_enabled and not storage_configured:
        return BackupSettings(False, bucket, region, access_key_id, secret_access_key,
            endpoint_url, raw_prefix or "smartlib", 24, 0, backup_dir)

    errors = []
    if not bucket:
        errors.append("SMARTLIB_BACKUP_S3_BUCKET is required")
    if not region:
        errors.append("SMARTLIB_BACKUP_S3_REGION is required")
    if not access_key_id or not secret_access_key:
        errors.append("S3 backup access credentials are required")
    if enabled and len(os.getenv("SMARTLIB_BACKUP_ENCRYPTION_KEY", "")) < 32:
        errors.append("SMARTLIB_BACKUP_ENCRYPTION_KEY must be a separate secret of at least 32 characters")
    if enabled and not backup_dir.is_absolute():
        errors.append("SMARTLIB_BACKUP_DIR must be an absolute path for scheduled backups")
    if not raw_prefix or "\\" in raw_prefix or any(part in {".", ".."} for part in raw_prefix.split("/")):
        errors.append("SMARTLIB_BACKUP_S3_PREFIX must be a safe object-key prefix")

    if endpoint_url:
        try:
            endpoint = urlsplit(endpoint_url)
            if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password:
                errors.append("SMARTLIB_BACKUP_S3_ENDPOINT_URL must be an HTTPS endpoint without embedded credentials")
        except ValueError:
            errors.append("SMARTLIB_BACKUP_S3_ENDPOINT_URL is invalid")

    try:
        interval_hours = int(raw_interval)
        if enabled and not 1 <= interval_hours <= 24 * 30:
            errors.append("SMARTLIB_BACKUP_INTERVAL_HOURS must be between 1 and 720")
    except ValueError:
        interval_hours = 24
        if enabled:
            errors.append("SMARTLIB_BACKUP_INTERVAL_HOURS must be a whole number")
    try:
        retention_days = int(raw_retention) if raw_retention else 0
        if enabled and not 1 <= retention_days <= 36500:
            errors.append("SMARTLIB_BACKUP_RETENTION_DAYS must be set to the approved retention period in days")
    except ValueError:
        retention_days = 0
        if enabled:
            errors.append("SMARTLIB_BACKUP_RETENTION_DAYS must be set to the approved retention period in days")

    if errors:
        raise RuntimeError("Scheduled backup configuration is incomplete: " + "; ".join(errors))
    return BackupSettings(enabled, bucket, region, access_key_id, secret_access_key,
        endpoint_url, raw_prefix, interval_hours, retention_days, backup_dir)


def validate_backup_settings() -> None:
    """Fail startup when scheduled backups were explicitly enabled but misconfigured."""
    if os.getenv("SMARTLIB_BACKUP_ENABLED", "false").strip().lower() in {"1", "true", "yes"}:
        load_backup_settings(require_enabled=True)


def create_s3_client(settings: BackupSettings):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError("Install the pinned boto3 dependency to use S3-compatible backup storage.") from exc
    return boto3.client(
        "s3",
        region_name=settings.region,
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def _object_prefix(settings: BackupSettings) -> str:
    return settings.prefix.rstrip("/") + "/"


def _valid_backup_key(settings: BackupSettings, key: str) -> bool:
    prefix = _object_prefix(settings)
    remainder = key[len(prefix):] if key.startswith(prefix) else ""
    return bool(remainder and "/" not in remainder and remainder.endswith(".slbk") and ".." not in remainder)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def upload_backup(path: Path, settings: BackupSettings, client=None) -> str:
    """Upload a locally encrypted archive and verify its stored size and hash."""
    if not path.is_file() or path.suffix != ".slbk":
        raise RuntimeError("Only an existing encrypted .slbk backup can be uploaded.")
    client = client or create_s3_client(settings)
    key = _object_prefix(settings) + path.name
    checksum = _sha256(path)
    client.upload_file(str(path), settings.bucket, key, ExtraArgs={
        "ContentType": "application/octet-stream",
        "Metadata": {"sha256": checksum},
    })
    stored = client.head_object(Bucket=settings.bucket, Key=key)
    metadata = {str(name).lower(): value for name, value in stored.get("Metadata", {}).items()}
    if stored.get("ContentLength") != path.stat().st_size or metadata.get("sha256") != checksum:
        raise RuntimeError("The off-site backup failed its post-upload integrity check.")
    return key


def list_remote_backups(settings: BackupSettings, client=None) -> list[dict]:
    client = client or create_s3_client(settings)
    paginator = client.get_paginator("list_objects_v2")
    results = []
    for page in paginator.paginate(Bucket=settings.bucket, Prefix=_object_prefix(settings)):
        for item in page.get("Contents", []):
            key = item.get("Key", "")
            if _valid_backup_key(settings, key):
                results.append({"key": key, "last_modified": item["LastModified"],
                    "size": int(item.get("Size", 0))})
    return sorted(results, key=lambda item: item["last_modified"], reverse=True)


def prune_expired_backups(settings: BackupSettings, client=None, *, now: datetime | None = None) -> int:
    """Delete expired archives under this app's prefix, always retaining the newest."""
    client = client or create_s3_client(settings)
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.retention_days)
    items = list_remote_backups(settings, client)
    expired = [item for item in items[1:] if item["last_modified"] < cutoff]
    for offset in range(0, len(expired), 1000):
        batch = expired[offset:offset + 1000]
        response = client.delete_objects(Bucket=settings.bucket,
            Delete={"Objects": [{"Key": item["key"]} for item in batch], "Quiet": True})
        if response.get("Errors"):
            raise RuntimeError("The object store did not delete all expired backups.")
    return len(expired)


def download_remote_backup(key: str, destination: Path, settings: BackupSettings | None = None,
                           client=None) -> Path:
    """Download an archive from the configured prefix and verify its checksum."""
    settings = settings or load_backup_settings()
    if not settings.bucket or not settings.region or not settings.access_key_id or not settings.secret_access_key:
        raise RuntimeError("Configure the S3 bucket, region, and credentials before downloading a backup.")
    if not _valid_backup_key(settings, key):
        raise RuntimeError("The requested object is outside this application's backup prefix.")
    destination = Path(destination).expanduser()
    if destination.exists() or destination.suffix != ".slbk":
        raise RuntimeError("Choose a new destination filename ending in .slbk.")
    client = client or create_s3_client(settings)
    expected = client.head_object(Bucket=settings.bucket, Key=key)
    expected_metadata = {str(name).lower(): value for name, value in expected.get("Metadata", {}).items()}
    if not re.fullmatch(r"[0-9a-f]{64}", expected_metadata.get("sha256", "")):
        raise RuntimeError("The remote backup has no valid integrity metadata.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.download_file(settings.bucket, key, str(destination))
        if (destination.stat().st_size != expected.get("ContentLength") or
                _sha256(destination) != expected_metadata["sha256"]):
            raise RuntimeError("The downloaded backup failed its integrity check.")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def verify_backup_archive(path: Path) -> dict:
    """Perform an archive-level recovery rehearsal before an archive is uploaded."""
    with tempfile.TemporaryDirectory(prefix="smartlib-backup-rehearsal-") as temp_name:
        stage_dir = Path(temp_name) / "staged"
        _, manifest = stage_backup(path, stage_dir)
        database_path = stage_dir / manifest["database_file"]
        if manifest["database_type"] == "sqlite":
            database = sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True)
            try:
                result = database.execute("PRAGMA integrity_check").fetchone()
            finally:
                database.close()
            if not result or result[0] != "ok":
                raise RuntimeError("The backup's SQLite snapshot failed its integrity check.")
        elif manifest["database_type"] == "postgresql":
            pg_restore = shutil.which("pg_restore")
            if not pg_restore:
                raise RuntimeError("pg_restore is required to verify PostgreSQL backup archives.")
            subprocess.run([pg_restore, "--list", str(database_path)], check=True,
                timeout=60 * 5, capture_output=True, text=True)
        else:
            raise RuntimeError("The backup contains an unsupported database type.")
        return manifest


def run_scheduled_backup(*, now: datetime | None = None, client=None) -> dict:
    """Create/upload a daily encrypted backup, then enforce the configured retention."""
    settings = load_backup_settings(require_enabled=True)
    now = now or datetime.now(timezone.utc)
    scheduled_dir = settings.backup_dir / "scheduled"
    scheduled_dir.mkdir(parents=True, exist_ok=True)
    marker = scheduled_dir / ".last-successful-backup"
    if marker.is_file():
        try:
            last_success = datetime.fromisoformat(marker.read_text(encoding="utf-8").strip())
            if last_success.tzinfo is None:
                last_success = last_success.replace(tzinfo=timezone.utc)
            if now - last_success < timedelta(hours=settings.interval_hours):
                return {"status": "not_due"}
        except (OSError, ValueError):
            logger.warning("Backup schedule marker is unreadable; running a fresh backup.")

    local_backups = sorted(scheduled_dir.glob("smartlib-*.slbk"), key=lambda path: path.name)
    backup_path = local_backups[-1] if local_backups else create_backup(destination_dir=scheduled_dir)
    manifest = verify_backup_archive(backup_path)
    client = client or create_s3_client(settings)
    key = upload_backup(backup_path, settings, client)
    marker_tmp = marker.with_suffix(".tmp")
    marker_tmp.write_text(now.isoformat(), encoding="utf-8")
    marker_tmp.replace(marker)
    backup_path.unlink(missing_ok=True)
    for older_backup in local_backups[:-1]:
        older_backup.unlink(missing_ok=True)

    expired_count = 0
    try:
        expired_count = prune_expired_backups(settings, client, now=now)
    except Exception:
        logger.exception("Off-site backup was saved, but old-backup cleanup failed.")
    return {"status": "uploaded", "key": key, "expired_backups_removed": expired_count,
        "database_type": manifest["database_type"]}
