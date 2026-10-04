"""Load project-local environment settings before modules read configuration."""

import os
from pathlib import Path
import re


def load_project_dotenv() -> None:
    """Read simple KEY=VALUE entries from the project-root .env file.

    Existing process environment variables take precedence over values in .env.
    This supports the plain assignments, comments, and quotes used by this app's
    environment example without adding a runtime dependency.
    """
    dotenv_path = Path(__file__).resolve().parent / ".env"
    if not dotenv_path.is_file():
        return

    for source_line in dotenv_path.read_text(encoding="utf-8-sig").splitlines():
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        os.environ[key] = value
