"""Verify and stage an encrypted SmartLib backup without overwriting live data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from scripts.backup import CHUNK_SIZE, MAGIC, _encryption_key


def _decrypt_file(source: Path, destination: Path, key: bytes) -> None:
    size = source.stat().st_size
    if size < len(MAGIC) + 12 + 16:
        raise RuntimeError("Backup file is incomplete.")
    with source.open("rb") as incoming:
        header = incoming.read(len(MAGIC) + 12)
        if header[:len(MAGIC)] != MAGIC:
            raise RuntimeError("Unsupported or invalid SmartLib backup format.")
        nonce = header[len(MAGIC):]
        incoming.seek(-16, os.SEEK_END)
        tag = incoming.read(16)
        ciphertext_end = size - 16
        incoming.seek(len(header))
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        remaining = ciphertext_end - len(header)
        try:
            with destination.open("wb") as outgoing:
                while remaining:
                    block = incoming.read(min(CHUNK_SIZE, remaining))
                    if not block:
                        raise RuntimeError("Backup file ended unexpectedly.")
                    remaining -= len(block)
                    outgoing.write(decryptor.update(block))
                outgoing.write(decryptor.finalize())
        except InvalidTag as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError("Backup authentication failed. Check the file and encryption key.") from exc
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def _safe_extract(archive_path: Path, output_dir: Path) -> dict:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.name or "\\" in member.name or path.is_absolute() or
                    any(part in {"..", ""} for part in path.parts) or
                    member.issym() or member.islnk() or member.isdev()):
                raise RuntimeError("Backup contains an unsafe archive path.")
        archive.extractall(output_dir, members=members, filter="data")
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Backup manifest is missing.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    database_file = manifest.get("database_file", "")
    if (not database_file or Path(database_file).name != database_file or
            not (output_dir / database_file).is_file() or
            not (output_dir / "uploads").is_dir()):
        raise RuntimeError("Backup is missing its database or uploads directory.")
    return manifest


def stage_backup(backup_path: Path, output_dir: Path) -> tuple[Path, dict]:
    if not backup_path.is_file():
        raise RuntimeError("Backup file does not exist.")
    if output_dir.exists():
        raise RuntimeError("Choose a new, non-existent restore directory so live files cannot be overwritten.")
    key = _encryption_key()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="smartlib-restore-") as temp_name:
            archive_path = Path(temp_name) / "smartlib.tar.gz"
            _decrypt_file(backup_path, archive_path, key)
            output_dir.mkdir(parents=False)
            manifest = _safe_extract(archive_path, output_dir)
    except Exception:
        if output_dir.exists():
            import shutil
            shutil.rmtree(output_dir)
        raise
    return output_dir, manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="Encrypted .slbk file")
    parser.add_argument("output_dir", type=Path, help="New directory to stage the verified backup")
    args = parser.parse_args()
    path, manifest = stage_backup(args.backup, args.output_dir)
    print(f"Verified backup staged at {path.resolve()}")
    print(f"Created: {manifest.get('created_at')} · database: {manifest.get('database_type')}")
