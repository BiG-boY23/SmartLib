"""List and download encrypted SmartLib archives from configured object storage."""
from __future__ import annotations

import argparse
from datetime import timezone
from pathlib import Path

from backup_service import (
    create_s3_client,
    download_remote_backup,
    list_remote_backups,
    load_backup_settings,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List available encrypted backups")
    download = commands.add_parser("download", help="Download and integrity-check one backup")
    download.add_argument("key", help="Full object key printed by the list command")
    download.add_argument("destination", type=Path, help="New local filename ending in .slbk")
    args = parser.parse_args()

    settings = load_backup_settings()
    client = create_s3_client(settings)
    if args.command == "list":
        backups = list_remote_backups(settings, client)
        if not backups:
            print("No remote backups found.")
            return
        for item in backups:
            created = item["last_modified"].astimezone(timezone.utc).isoformat()
            print(f"{created}\t{item['size']} bytes\t{item['key']}")
        return

    path = download_remote_backup(args.key, args.destination, settings, client)
    print(f"Verified encrypted backup downloaded: {path.resolve()}")
    print("Stage it safely with: python -m scripts.restore_backup <backup-file> <new-empty-directory>")


if __name__ == "__main__":
    main()
