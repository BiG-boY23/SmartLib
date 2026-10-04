# EVSU SmartLib - Integrated Library Resource & Circulation System

A production-ready, modular FastAPI backend application with an integrated Web Management Portal for Administrators & Librarians and REST APIs for Mobile Clients (Students & Faculty).

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

Upon first launch, the system automatically initializes `library.db` and provisions the following default credentials:

| Role | Username / ID | Password | Access Rights |
|---|---|---|---|
| **Super Admin** | `ADMIN-EVSU-2026` | `admin123` | Full System Access & User Provisioning |
| **Librarian Staff** | `EVSU-LIB-2026` | `password123` | Dashboard, Cataloging, Circulation & Borrow Queues |
| **Student Patron** | `2026-STUDENT-088` | `password123` | OPAC Catalog, Online Requests, Digital ID QR |

Students and faculty can also create an account from the mobile app at `/mobile/`.

## Account security and deployment

- New accounts remain inactive for sign-in until the email verification link is used. Verification links expire after 24 hours; password reset links expire after one hour and can be used once.
- Configure mail delivery before account registration or password recovery. Copy `.env.example` to `.env` beside `main.py`, fill in `SMTP_HOST`, `SMTP_FROM`, and a `PUBLIC_BASE_URL` reachable by the account holder, plus credentials if your provider requires them. The server loads that file on startup; process environment values take priority. Never commit SMTP passwords or a production signing key.
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