# Render PostgreSQL setup

## Preserve existing data first
Do not restart or redeploy the current Free service until needed SQLite data is exported. The hosted /tmp database is ephemeral. Your local library.db is NOT automatically a backup of the hosted data. Lost records cannot be recovered simply by changing databases.

## Create PostgreSQL
1. In Render choose New > Postgres, name it smartlib-db.
2. Choose the SAME REGION as the existing web service (check Settings).
3. Choose paid PostgreSQL for lasting storage. Free Postgres expires after 30 days. Your web service may remain Free; its SMTP restrictions and idle sleep still apply.
4. When Available, open Connect and copy the Internal Database URL privately.

## Fresh start
If no existing data is needed, set DATABASE_URL on the web service to the Internal Database URL and deploy the updated code. Startup creates tables. Set the three BOOTSTRAP_ADMIN variables for initial SuperAdmin creation. Check /ready, login, add a record, redeploy, and verify it remains.

## Copy an existing SQLite backup
Stop writes and obtain a consistent SQLite backup first. Render Free has no shell access. Arrange a supported export before restarting if the only copy is on the running host. This utility cannot recover missing ephemeral data.

Migrate BEFORE connecting the web service to PostgreSQL: startup inserts bootstrap records and the migration intentionally refuses a nonempty target. Use a fresh database; do not delete existing target data to force a migration.

The source must have the current app schema. In PowerShell from D:\Master:

```powershell
$secureUrl = Read-Host "External PostgreSQL URL (include sslmode=require)" -AsSecureString
$env:TARGET_DATABASE_URL = [System.Net.NetworkCredential]::new('', $secureUrl).Password
.venv\Scripts\python.exe migrate_sqlite_postgres.py --source "D:\backups\library.db"
Remove-Item Env:TARGET_DATABASE_URL
```

Use Render's External Database URL for this local command, with sslmode=require in its query string. Never commit or share database credentials. The script copies typed records, checks counts, resets PostgreSQL ID sequences and rolls back the target transaction if any step fails. The source is not modified. Upgrade an older source schema on a backed-up copy before migration.

After successful verification, change the web service DATABASE_URL to the Internal Database URL and deploy. Existing accounts and password hashes remain. Test login, new catalog records, checkout, return and reports. Keep the source backup.

PostgreSQL stores records, NOT profile/cover image files. Those need a persistent disk or separate object storage; /tmp/smartlib/uploads remains temporary.

## Workflow changes
- Staff selects a future due date and time at checkout (Philippine time). Actual return time is recorded when an item is returned.
- Library Attendance uses a keyboard-style Library ID QR scanner or manual institutional ID. It records visits, counts unique visitors, and exports CSV/Excel/PDF/Word with A4/long paper options.
- New catalog and copy identifiers are numeric, server-generated, unique and padded to five digits. Existing labels remain usable to preserve old QR codes and loan references. Renumbering legacy holdings requires a separate label migration.

References:
https://render.com/docs/postgresql-creating-connecting
https://render.com/docs/free
