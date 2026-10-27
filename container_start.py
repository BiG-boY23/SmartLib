"""Initialize a newly attached volume, then run the API as an unprivileged user."""
import os
from pathlib import Path


def main():
    # Railway mounts new volumes as root. RAILWAY_RUN_UID=0 lets this tiny
    # initializer assign the known volume directories before dropping privileges.
    if os.geteuid() == 0:
        for path in (Path("/var/data"), Path("/var/data/uploads"),
                     Path("/var/data/uploads/book-covers"), Path("/var/data/uploads/profile-photos")):
            path.mkdir(parents=True, exist_ok=True)
            os.chown(path, 10001, 10001)
        for name in ("library.db", "library.db-wal", "library.db-shm", "library.db-journal"):
            path = Path("/var/data") / name
            if path.exists():
                os.chown(path, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    port = str(int(os.getenv("PORT", "8000")))
    os.execvp("uvicorn", ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", port,
                          "--workers", "1", "--proxy-headers"])


if __name__ == "__main__":
    main()
