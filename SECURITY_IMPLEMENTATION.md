# SmartLib security implementation status

This document separates controls enforced by the application from controls that
still require a deployment or university policy decision. It is not a claim of
security certification.

## Implemented in this change

- **Staff authentication:** Production Librarian, Admin, and SuperAdmin accounts
  must enroll a time-based authenticator before opening a dashboard. Login then
  requires a current authenticator code. Ten single-use recovery codes are
  issued once and can replace an authenticator code. Each MFA challenge expires
  after ten minutes and locks after five invalid attempts. TOTP codes cannot be
  replayed in the same time step.
- **Secret protection:** Authenticator seeds are encrypted in the database with
  `SMARTLIB_MFA_ENCRYPTION_KEY`; recovery codes are stored only as hashes. Keep
  this key separate from and independent of `SMARTLIB_SECRET_KEY`.
- **Browser sessions:** Staff dashboards and the student/faculty web app use
  `HttpOnly`, `SameSite=Strict` cookies; JavaScript no longer stores or sends
  browser bearer tokens. Unsafe cookie-authenticated requests require a matching
  same-origin `Origin` (or `Referer`) check. Production cookies are Secure.
  Existing staff sessions are revoked when staff enroll MFA. Staff always use a 12-hour maximum session;
  patron browser sessions can use the 30-day remember option. The 15-minute
  idle timeout remains enforced server-side.
- **Native sessions:** The Expo app continues to store bearer tokens using
  `expo-secure-store` and sends them only to the configured API.
- **Passwords:** New and changed passwords accept 8–64 characters and retain
  the current letter/number/symbol checks. New hashes pre-hash with SHA-256
  before bcrypt, preventing silent truncation at bcrypt's 72-byte boundary.
  Existing bcrypt hashes remain valid and are upgraded on successful login.
- **Shared rate limits:** Login, MFA, email OTP, support, and assistant request
  limits use atomic, fixed-window counters in SQLite/PostgreSQL so limits apply
  across workers. The database stores a hash of each counter key, not the raw
  email address or IP.
- **Audit:** Successful authentication, failed password login, MFA failures,
  MFA enrollment, and recovery-code use are recorded without recording passwords
  or MFA codes.
- **Backup and recovery:** The service can create scheduled AES-GCM-encrypted
  database-and-uploads archives, run an archive-level restore rehearsal, upload
  them to a private S3-compatible bucket, verify remote size and SHA-256 metadata,
  and prune expired copies while retaining the newest. The scheduler is disabled
  by default until its destination, separate encryption key, and approved
  retention period are configured. `scripts.remote_backup` lists and downloads
  verified copies; `scripts.restore_backup` authenticates and stages them in a
  new folder without overwriting a live database. The encryption key is separate
  from app keys.
- **Transient data retention:** The service hourly deletes expired pending
  registrations, expired/consumed email action tokens and MFA challenges,
  expired or revoked sessions, and expired rate-limit buckets. These records are
  no longer usable for their original purpose.
- Existing server-side role checks, request validation, upload checks, HTTPS
  redirect/HSTS, response headers, session revocation on password change, and
  audit coverage remain in force.

## Required rollout steps

1. Deploy the latest schema and code. `render.yaml` generates
   `SMARTLIB_MFA_ENCRYPTION_KEY`; if managing Render variables manually, create a
   separate random value of at least 32 characters and keep it backed up. Do not
   rotate it without re-enrolling staff.
2. Each staff member signs in with their existing password, scans the displayed
   QR code using an authenticator app, confirms a code, then saves the recovery
   codes in a private location. Authenticator apps can be free.
3. Provision a private S3-compatible bucket, approve a backup retention period,
   and add `SMARTLIB_BACKUP_S3_BUCKET`, `SMARTLIB_BACKUP_S3_REGION`,
   `SMARTLIB_BACKUP_S3_ACCESS_KEY_ID`, `SMARTLIB_BACKUP_S3_SECRET_ACCESS_KEY`,
   `SMARTLIB_BACKUP_ENCRYPTION_KEY`, and `SMARTLIB_BACKUP_RETENTION_DAYS` as
   Render environment values/secrets. Use a separate random encryption key of
   at least 32 characters; keep a recovery copy outside Render. Then set
   `SMARTLIB_BACKUP_ENABLED=true`, `SMARTLIB_BACKUP_DIR=/var/data/backups`, and
   choose `SMARTLIB_BACKUP_INTERVAL_HOURS` (default 24). The S3 identity needs
   list, read, write, head, and delete access only to this app's backup prefix.
   Confirm backups appear in the service logs and the object store.
4. Perform a full recovery rehearsal into a new database and upload directory
   before relying on the backups. From a trusted machine, run
   `python -m scripts.remote_backup list`, download a selected archive with
   `python -m scripts.remote_backup download <object-key> <new-file.slbk>`, then
   stage it with `python -m scripts.restore_backup <new-file.slbk> <new-empty-directory>`.
   Review the staged manifest and database, restore to a fresh target, and only
   switch production over after validation. The tools never overwrite a live
   database or uploads directory.
5. Test staff MFA and browser sign-in on the deployed HTTPS URL, then verify
   logout, expiry, OTP delivery, and a recovery-code login.

The code does not provision a bucket or enable paid storage. The scheduler stays
off until the required values are set. Render's mounted disk holds a retry copy
only; it is not an off-site backup. Provider database snapshots remain a separate
complementary recovery layer and should be checked in the Render dashboard.

## Decisions still needed before claiming the whole security plan is complete

- **SuperAdmin passkeys:** TOTP MFA is active for SuperAdmins as well as other
  staff. WebAuthn/passkey login is not included yet; it needs the final public
  HTTPS origin and a recovery policy.
- **Long-lived data retention:** Existing deletion snapshots and archives are
  retained. No automatic purge period is set for circulation, messages, audit
  logs, photos, or backups because the university/library must choose and approve
  retention periods and any legal-hold exceptions before those records are deleted.
- **Privacy notice and consent:** The AI assistant currently discloses that
  questions and recent chat may be sent to the configured provider. A complete
  university-approved privacy notice, consent versioning, and user data request
  process still need the library's exact policy text and contact.
- **Offline app data:** The remembered offline snapshot contains account and
  circulation details. Native files stay inside the app's private storage and
  web snapshots stay in browser storage, but those snapshots are not separately
  encrypted by SmartLib. Avoid using shared/unlocked devices for remembered
  sessions until app-level cache encryption is implemented.
- **Hosting encryption and recovery:** Database/disk encryption and provider
  snapshots depend on Render account settings and the chosen database plan.
  Backup scheduling also remains inactive until a private destination and
  university-approved retention period are configured. A real recovery rehearsal
  into a fresh database and upload directory still needs to be completed after
  deployment.
