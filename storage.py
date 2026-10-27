"""Runtime data lives outside source/build artifacts in hosted deployments."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UPLOAD_ROOT = Path(os.getenv("SMARTLIB_UPLOAD_DIR", str(ROOT / "uploads"))).expanduser().resolve()
BOOK_COVERS = UPLOAD_ROOT / "book-covers"
PROFILE_PHOTOS = UPLOAD_ROOT / "profile-photos"


def prepare_storage():
    for directory in (BOOK_COVERS, PROFILE_PHOTOS):
        directory.mkdir(parents=True, exist_ok=True)
