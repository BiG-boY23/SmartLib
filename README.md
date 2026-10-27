# EVSU SmartLib - Integrated Library Resource & Circulation System

A modular FastAPI backend with a Web Management Portal for Administrators & Librarians and REST APIs for Mobile Clients (Students & Faculty).

For the hosted pilot and standalone Android/iPhone distribution, follow
[DEPLOYMENT.md](DEPLOYMENT.md). The landing page includes **Get the App** links
that activate when real signed APK and TestFlight release URLs are configured.

## Core Features

- **FastAPI Framework**: Modern Python backend with async support, Pydantic v2 validation models, and declarative SQLAlchemy 2.0 ORM.
- **Database**: SQLite database (`library.db`) with automatic table creation and initial data seeding on startup.
- **Authentication & Security**: JWT (JSON Web Tokens) with OAuth2 Password flow and password hashing (`passlib`/`bcrypt`).
- **Role-Based Access Control (RBAC)**: Enforced permission checks for `SUPERADMIN`, `ADMIN`, `LIBRARIAN`, `STUDENT`, and `FACULTY`.
- **Web Management Dashboard (`index.html`)**: Integrated frontend serving Administrators & Librarians for real-time cataloging, QR code generation, circulation counter sweeps, borrow request queues, acquisition management, user provisioning, and system audit logs.
- **Mobile Client APIs (`/mobile`)**: Dedicated endpoints for Student & Faculty mobile applications to search holdings, request borrows, submit acquisition suggestions, and retrieve Digital Student ID QR payloads.
- **React Native Mobile App (`mobile-native/`)**: Native Android and iOS patron client connected to the same authentication and library database as the web portal. An installable web client is also available at `/mobile/`.

---

## Default Seed Accounts

In local development, the system initializes `library.db` with these demo accounts.
Production disables demo seeding, blocks known staff demo passwords, and provisions
the first SuperAdmin from hosting secrets as described in the deployment guide.

| Role | Username / ID | Password | Access Rights |
|---|---|---|---|
| **Super Admin** | `ADMIN-EVSU-2026` | `admin123` | Full System Access & User Provisioning |
| **Librarian Staff** | `EVSU-LIB-2026` | `password123` | Dashboard, Cataloging, Circulation & Borrow Queues |
| **Student Patron** | `2026-STUDENT-088` | `password123` | OPAC Catalog, Online Requests, Digital ID QR |

Students and faculty can also create an account from the mobile app at `/mobile/`.

## Account security and deployment

- Students/faculty verify registration using an email OTP. Password recovery also uses email OTP; the old direct sign-up and reset-link routes are disabled.
- Configure mail delivery before account registration or password recovery. Copy `.env.example` to `.env` beside `main.py`, fill in `SMTP_HOST`, `SMTP_FROM`, and a `PUBLIC_BASE_URL` reachable by the account holder, plus credentials if your provider requires them. The server loads that file on startup; process environment values take priority. Never commit SMTP passwords or a production signing key.
- To enable model-backed answers in the mobile library assistant, set `OPENAI_API_KEY` on the FastAPI server and optionally `SMARTLIB_AI_MODEL` (default: `gpt-5-mini`), then restart the server. The key stays on the server. The assistant sends the user's question and recent chat, plus relevant catalog titles, availability, and recent borrowing status to the model. When the key is absent or the provider fails, it uses local library guidance. Staff decisions and transactions remain in the existing authenticated APIs.
- Sessions expire after 15 minutes without an authenticated request. Normal sessions have a 12-hour maximum lifetime; “Remember me” sessions have a 30-day maximum. The web and native clients also sign out after 15 minutes of local inactivity.
- Dashboard pages validate the current account with the API, and every protected API endpoint enforces roles on the server. Keep the database and signing key private.
- SQLAlchemy ORM filters bind user values as query parameters. Web dashboard output escapes database text before rendering it as HTML.
- HTTPS is required for production. Terminate TLS at a trusted reverse proxy or configure TLS directly, set `SMARTLIB_ENV=production`, provide a persistent random `SMARTLIB_SECRET_KEY`, use an `https://` `PUBLIC_BASE_URL`, and set `REQUIRE_HTTPS=true`. Plain HTTP and `127.0.0.1` addresses are for local development only; they do not encrypt traffic. Trust forwarded HTTPS headers only from your known proxy.
- The built-in login rate limiter is in-memory and intended for a single application process. A multi-worker or multi-server deployment should enforce shared rate limits at the gateway or with a shared store.

Existing user records are treated as email-verified during the database upgrade so current staff and seed accounts remain usable. New sign-ups must verify their email.

---

## Installation & Setup Instructions

### 1. Create and Activate Virtual Environment

**Windows (PowerShell / Command Prompt):**
```cmd
python -m venv .venv
.venv\Scripts\activate
```

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Required Dependencies

```bash
pip install -r requirements.txt
```

---

## Running the Application

Start the FastAPI application using Uvicorn:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Accessing the Application & API Documentation

- **Web Management Portal**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Installable web client**: [http://127.0.0.1:8000/mobile/](http://127.0.0.1:8000/mobile/)
- **Native app setup**: [mobile-native/README.md](mobile-native/README.md)
- **Interactive Swagger UI (OpenAPI)**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc API Documentation**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
- **Health Check Endpoint**: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## Circulation reports, feedback, and Library ID

- Admin and librarian dashboards use recorded database counts. Their charts show actual checkouts and returns over the last 30 days (admin) or 7 days (librarian).
- Open **Circulation Reports** in either staff portal. Choose a date range (up to 366 days), department, course, and audience. Set both dates to the same day for a daily report, or select **Today**. Details can be sorted by date, department, or course.
- Reports count unique borrowers/returners separately from items. A student borrowing several books counts once per day; period totals count each person once across the entire selected range. Pending requests and approvals are not checkouts. Dates use Philippine time (UTC+08:00); department/course come from the member's current recorded profile.
- Reports include totals, daily counts, books borrowed and returned, and matching activity details. Weekly book trends compare borrowing and returns with the previous week. The browser preview limits detail rows to 200; exports include all matching rows.
- Circulation, weekly book trends, borrow queues, and acquisition suggestions support PDF, Word, Excel (`.xlsx`), and CSV exports in both staff dashboards. PDF and Word use the retained university template in `templates/reports/template.docx`.
- Choose **PDF / Word paper** before exporting: **Long bond (8.5 × 13 in)** is the default and preserves the letterhead's original size; **A4** fits the complete letterhead proportionally, including the footer. Circulation and weekly reports share a paper setting; queue and acquisition export controls have their own settings.
- Excel exports wrap text, fit row heights to the content, freeze column headings, and provide filters. CSV stores values only and cannot preserve row heights, wrapping, or paper settings; choose Excel when those settings are needed.
- **Loan extension history** in Circulation Reports records the approving staff account, patron, book/request, previous and new due dates, reason, and approval time. It uses the report's dates, audience, department, course, and paper settings and exports to CSV, Excel, PDF, or Word. Earlier extensions remain available in Audit Logs; structured history starts with this update. Extension counts are separate from checkouts and returns.
- Both staff dashboards have a notification bell. Every active Admin, SuperAdmin, and Librarian receives an individual notification for borrow requests, approvals, checkouts, returns, cancellations, acquisition suggestions, and approved extensions. Reading one account's notifications does not mark another account's inbox as read.
- Deleting a message, clearing messages, or deleting a conversation removes it from the normal inbox and retains a snapshot with its actor and time. Only SuperAdmin can open **Retained archive** or view existing archived records. Archived books retain their borrowing history and remain returnable at the desk. SuperAdmin can restore operational archived records; retained deletion snapshots are for review.
- Native Messages has visible **Delete message**, **Clear messages**, and **Delete conversation** controls. Patrons can delete their own messages and manage their own conversations; staff can manage messages within their authorized inbox. Delete confirmations explain that a SuperAdmin-only retained copy is kept.
- **Suggestions & inquiries** is available to both staff roles. Public feedback is saved through the landing-page form. Mobile book recommendations can be reviewed in the same inbox.
- The mobile **Library ID** screen displays the Ormoc City Campus card and offers PNG download and sharing. On Android, choose a destination folder; on iOS, use the share sheet. The backend must be reachable to generate the image.
- Mobile **Change password** checks the current password and matching new passwords, keeps the 8–12 character policy, and signs out existing sessions after success.
- Circulation checkouts are due 24 hours after issue for students and faculty. A late return accrues ₱1.50 per started overdue hour, capped at ₱10 for each started 24-hour overdue period, matching the rates published on the library policy page. The fine is added when the item is checked back in.

After pulling these changes, install backend dependencies with `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, then run `npm.cmd install` from `D:\Master\mobile-native`. Restart Uvicorn and Expo, and hard-refresh both staff dashboards.

The server creates the new retained-archive, extension-history, and staff-activity tables on startup. Existing data is preserved.

## Running Automated Tests

Run the pytest suite to verify all API routes, authentication, RBAC rules, cataloging, and circulation logic:

```bash
pytest -v
```

---

## Project Directory Structure

```text
Master/
├── main.py              # FastAPI app initialization, CORS, lifespan seeding, and static route serving
├── database.py          # SQLite engine setup and session dependency (get_db)
├── models.py            # SQLAlchemy models (User, Book, BorrowRecord, Fine, AcquisitionSuggestion, AuditLog, SmtpLog)
├── schemas.py           # Pydantic v2 schemas for request validation & serialization
├── security.py          # Password hashing, JWT token generation/validation, & RBAC dependencies
├── crud.py              # Database operations and initial seed logic
├── index.html           # Web Management Portal frontend integrated with REST APIs
├── mobile/              # Installable student/faculty app shell, styling, and offline static assets
├── mobile-native/       # Expo/React Native Android and iOS client
├── requirements.txt     # Complete Python package dependencies
├── routers/
│   ├── __init__.py
│   ├── auth_router.py   # Universal login (/auth/login), registration, profile & password change
│   ├── admin_router.py  # Web Dashboard, Cataloging, Circulation sweep, User & Log management
│   └── mobile_router.py # Mobile App APIs for catalog search, borrow requests, & Digital ID payload
└── tests/
    └── test_library.py  # Automated unit test suite
```
cd D:\Master
>> .\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Messaging recipients: student/faculty conversations are routed to the selected Librarian or Admin team. Admin includes superadmins. Staff inboxes, direct reads, replies, and notifications are restricted to that team. Existing conversations migrate to Librarian on server startup. Rebuild the native app to use the recipient selector.

Admin and Librarian dashboards now include News & Announcements. Publishing queues individual emails and creates app notifications for active students and faculty. Staff replies, acquisition decisions, and loan extensions also queue email confirmations. FastAPI creates the durable `library_emails` table on startup and runs a delivery/reminder worker every 60 seconds. Configure SMTP on the server; failed email deliveries retry with increasing delays, up to eight attempts, and appear in SMTP logs. Email delivery is at least once: a server interruption after SMTP accepts an email may cause a retry. The server must remain running for scheduled delivery. Existing borrow-approval email delivery and its manual retry remain available.

Active checked-out loans receive a reminder within 24 hours of the due date, with instructions to message the librarian for an extension. My books provides a prefilled extension request; it does not change the due date automatically. Librarian queue controls filter status and search patron/title/reference, and sort by request date, due date, or patron. Use Extend loan on an active loan to choose a later local date/time and record a reason. The server validates the extension, audits the previous/new UTC due dates, and notifies the reader. Reminder keys include the due date so an extension gets a fresh reminder. Native screens use pull-to-refresh. In-app notifications are persisted; native clients check for updates while open. OS push delivery while closed requires a separate push-token/provider integration; local due reminders depend on device notification permission and a development/standalone build on Android.

Both the mobile assistant and landing-page chatbot support multilingual model replies: automatic language matching, mixed-language questions, and explicit language-switch requests. The landing page posts to `/public/assistant` with up to six recent turns; the server supplies the public guide and catalog matches, never personal borrowing records. The shared server `OPENAI_API_KEY` enables both. Local fallback guidance is English only and the public chatbot labels it when AI is unavailable. Restart FastAPI after configuration; do not put the API key in browser/mobile code. Multilingual catalog searches use literal keywords, so use original book titles or authors for best matching.
