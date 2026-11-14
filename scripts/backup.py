"""Create a point-in-time encrypted SmartLib backup for off-site storage.

Run this from a trusted workstation with DATABASE_URL and SMARTLIB_UPLOAD_DIR
configured. Never store the backup key beside the encrypted backup.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from contextlib import closing

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy.engine import make_url

from config import load_project_dotenv

load_project_dotenv()


MAGIC = b"SLBK1"
CHUNK_SIZE = 1024 * 1024


def _encryption_key() -> bytes:
    value = os.getenv("SMARTLIB_BACKUP_ENCRYPTION_KEY", "")
    if len(value) < 32:
        raise RuntimeError("Set SMARTLIB_BACKUP_ENCRYPTION_KEY to a separate random secret of at least 32 characters.")
    return hashlib.sha256(b"EVSU-SmartLib-backup-v1:" + value.encode("utf-8")).digest()


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise RuntimeError(f"SQLite database does not exist: {source}")
    with closing(sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)) as source_db:
        with closing(sqlite3.connect(destination)) as target_db:
            source_db.backup(target_db)


def _postgres_snapshot(database_url: str, destination: Path) -> None:
    executable = shutil.which("pg_dump")
    if not executable:
        raise RuntimeError("pg_dump is required for PostgreSQL backups. Install PostgreSQL client tools first.")
    url = make_url(database_url)
    if not url.host or not url.database:
        raise RuntimeError("DATABASE_URL must include the PostgreSQL host and database name.")
    args = [executable, "--no-password", "--format=custom", "--no-owner", "--no-acl",
            "--file", str(destination), "--host", url.host, "--dbname", url.database]
    if url.port:
        args.extend(["--port", str(url.port)])
    if url.username:
        args.extend(["--username", url.username])
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    sslmode = url.query.get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = str(sslmode)
    subprocess.run(args, env=env, check=True, timeout=60 * 30, capture_output=True, text=True)


def _encrypt_file(source: Path, destination: Path, key: bytes) -> None:
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            outgoing.write(MAGIC + nonce)
            while block := incoming.read(CHUNK_SIZE):
                outgoing.write(encryptor.update(block))
            outgoing.write(encryptor.finalize())
            outgoing.write(encryptor.tag)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def create_backup(*, destination_dir: Path | None = None) -> Path:
    database_url = os.getenv("DATABASE_URL", "")
    upload_dir = Path(os.getenv("SMARTLIB_UPLOAD_DIR", "")).expanduser()
    destination_dir = Path(destination_dir).expanduser() if destination_dir is not None else Path(
        os.getenv("SMARTLIB_BACKUP_DIR", "backups")
    ).expanduser()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required.")
    if not upload_dir.is_dir():
        raise RuntimeError("SMARTLIB_UPLOAD_DIR must point to the uploads directory to include profile photos and book covers.")
    key = _encryption_key()
    destination_dir.mkdir(parents=True, exist_ok=True)
    resolved_uploads = upload_dir.resolve()
    resolved_destination = destination_dir.resolve()
    if resolved_destination == resolved_uploads or resolved_destination.is_relative_to(resolved_uploads) or resolved_uploads.is_relative_to(resolved_destination):
        raise RuntimeError("Choose a backup directory separate from the uploads directory and its parent.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = ".sqlite" if make_url(database_url).get_backend_name() == "sqlite" else ".dump"

    # The temporary plaintext archive is private to this process and is removed on exit.
    with tempfile.TemporaryDirectory(prefix="smartlib-backup-") as temp_name:
        temp_dir = Path(temp_name)
        os.chmod(temp_dir, 0o700)
        db_snapshot = temp_dir / f"database{suffix}"
        if suffix == ".sqlite":
            sqlite_path = make_url(database_url).database
            if not sqlite_path or sqlite_path == ":memory:":
                raise RuntimeError("A file-backed SQLite database is required for backups.")
            _sqlite_snapshot(Path(sqlite_path).expanduser(), db_snapshot)
            database_type = "sqlite"
        else:
            _postgres_snapshot(database_url, db_snapshot)
            database_type = "postgresql"

        manifest = {
            "format": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database_type": database_type,
            "database_file": db_snapshot.name,
            "uploads_directory": "uploads",
        }
        manifest_path = temp_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        archive_path = temp_dir / "smartlib.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(db_snapshot, arcname=db_snapshot.name)
            archive.add(manifest_path, arcname="manifest.json")
            archive.add(upload_dir.resolve(), arcname="uploads")

        output_path = destination_dir / f"smartlib-{stamp}.slbk"
        _encrypt_file(archive_path, output_path, key)
        try:
            os.chmod(output_path, 0o600)
        except OSError:
            pass
    return output_path


if __name__ == "__main__":
    path = create_backup()
    print(f"Encrypted SmartLib backup created: {path.resolve()}")
