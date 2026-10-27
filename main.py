import os
import asyncio
import logging
from contextlib import asynccontextmanager

from config import load_project_dotenv

# Load settings before importing routers/security modules that read environment values.
load_project_dotenv()

from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from deployment_middleware import PublicHTTPSRedirect, RequestSizeLimit
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, or_, func, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import engine, Base, SessionLocal, get_db
from models import Book
from routers import auth_router, admin_router, mobile_router, report_router, staff_notification_router
from schemas import FeedbackCreate, FeedbackResponse, BookResponse
import crud
from library_notifications import process_notifications
from production import validate_production_settings, prepare_production_accounts
from storage import BOOK_COVERS, PROFILE_PHOTOS, prepare_storage


async def notification_worker():
    while True:
        try:
            await asyncio.to_thread(process_notifications, SessionLocal)
        except Exception:
            logging.getLogger(__name__).exception("Library notification worker failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_settings()
    # Startup: Create tables & seed database
    Base.metadata.create_all(bind=engine)
    thread_columns = {column["name"] for column in inspect(engine).get_columns("support_threads")}
    if "recipient_role" not in thread_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE support_threads ADD COLUMN recipient_role VARCHAR(20) NOT NULL DEFAULT 'librarian'")
    # Preserve existing accounts as verified when upgrading older SQLite files.
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "email_verified" not in user_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT TRUE"
            )
    if "course" not in user_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE users ADD COLUMN course VARCHAR")
    for column in ("first_name", "middle_name", "last_name", "profile_picture_url"):
        if column not in user_columns:
            with engine.begin() as connection:
                connection.exec_driver_sql(f"ALTER TABLE users ADD COLUMN {column} VARCHAR")
    book_columns = {column["name"] for column in inspect(engine).get_columns("books")}
    if "cover_url" not in book_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE books ADD COLUMN cover_url VARCHAR")
    borrow_columns = {column["name"] for column in inspect(engine).get_columns("borrow_records")}
    if "copy_accession_no" not in borrow_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE borrow_records ADD COLUMN copy_accession_no VARCHAR")
    pending_columns = {column["name"] for column in inspect(engine).get_columns("pending_registrations")}
    if "course" not in pending_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE pending_registrations ADD COLUMN course VARCHAR")
    for column in ("first_name", "middle_name", "last_name"):
        if column not in pending_columns:
            with engine.begin() as connection:
                connection.exec_driver_sql(f"ALTER TABLE pending_registrations ADD COLUMN {column} VARCHAR")
    approval_email_columns = {column["name"] for column in inspect(engine).get_columns("borrow_approval_emails")}
    if "last_attempt_at" not in approval_email_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE borrow_approval_emails ADD COLUMN last_attempt_at TIMESTAMP")
    from models import active_copy_index
    active_copy_index.create(bind=engine, checkfirst=True)
    db = SessionLocal()
    try:
        crud.seed_initial_data(db)
        prepare_production_accounts(db)
        crud.ensure_book_copies(db, legacy_as_printed=True)
    finally:
        db.close()
    worker = asyncio.create_task(notification_worker())
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="EVSU SmartLib Integrated Library Resource & Circulation System",
    description="Library Web Management Portal API (Admin & Librarian)",
    version="2.0.0",
    lifespan=lifespan,
)


@app.exception_handler(IntegrityError)
async def database_conflict(request, exc):
    return JSONResponse({"detail": "This record conflicts with a recent change. Refresh and try again."}, status_code=409)

# Native clients do not use browser CORS; allow only explicitly configured web origins.
allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

https_required = os.getenv("REQUIRE_HTTPS", "false").lower() in {"1", "true", "yes"}
if https_required or os.getenv("SMARTLIB_ENV", "development").lower() == "production":
    app.add_middleware(PublicHTTPSRedirect)

if os.getenv("SMARTLIB_ENV", "development").lower() == "production":
    from urllib.parse import urlsplit
    host = urlsplit(os.getenv("PUBLIC_BASE_URL", "")).hostname
    if host:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host, "localhost", "127.0.0.1", "healthcheck.railway.app"])

app.add_middleware(RequestSizeLimit)


@app.middleware("http")
async def security_response_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
        "img-src 'self' data: blob: https:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith(("/auth/", "/admin/", "/mobile/", "/reports/", "/staff/", "/profile-photos/")):
        response.headers["Cache-Control"] = "no-store"
    return response

# Include API Routers
app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(mobile_router.router)
from routers import public_assistant_router
app.include_router(public_assistant_router.router)
app.include_router(report_router.router)
app.include_router(staff_notification_router.router)

# Installable patron app assets; mobile API routes above take precedence.
mobile_assets_path = os.path.join(os.path.dirname(__file__), "mobile")
app.mount("/mobile-assets", StaticFiles(directory=mobile_assets_path), name="mobile-assets")

static_assets_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_assets_path):
    app.mount("/static", StaticFiles(directory=static_assets_path), name="static")

prepare_storage()
book_covers_path = str(BOOK_COVERS)
app.mount("/book-covers", StaticFiles(directory=book_covers_path), name="book-covers")

@app.get("/profile-photos/{filename}", include_in_schema=False)
def serve_profile_photo(filename: str, expires: int = 0, signature: str = ""):
    from photo_security import valid_photo_link
    from pathlib import Path
    if Path(filename).name != filename or not valid_photo_link(filename, expires, signature):
        raise HTTPException(status_code=403, detail="This photo link has expired or is invalid")
    path = PROFILE_PHOTOS / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})


@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon():
    favicon_path = os.path.join(os.path.dirname(__file__), "static", "evsu-logo.jpg")
    if not os.path.exists(favicon_path):
        favicon_path = os.path.join(os.path.dirname(__file__), "mobile", "evsu-smartlib-logo.jpg")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/jpeg")
    return JSONResponse({"message": "Favicon not found"}, status_code=404)


@app.get("/health", tags=["Health Check"])
def health_check():
    return {
        "status": "healthy",
        "system": "EVSU SmartLib API Service",
        "version": "2.0.0",
    }


@app.get("/ready", tags=["Health Check"])
def readiness_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}


@app.get("/public/app-releases", tags=["Public Portal"])
def public_app_releases():
    from app_releases import app_releases
    return JSONResponse(app_releases(), headers={"Cache-Control": "no-store"})


@app.post("/public/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED, tags=["Public Portal"])
def submit_public_feedback(fb_data: FeedbackCreate, request: Request, db: Session = Depends(get_db)):
    if os.getenv("SMARTLIB_ENV", "development").lower() == "production":
        from rate_limits import enforce_rate_limit
        enforce_rate_limit(f"feedback:{request.client.host if request.client else 'unknown'}", 10, 3600)
    return crud.create_feedback(db, fb_data)


@app.get("/public/books", response_model=List[BookResponse], tags=["Public Portal"])
def get_public_books(
    search: Optional[str] = None,
    category: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(Book).filter(Book.is_archived == False)
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Book.title.ilike(s),
                Book.author.ilike(s),
                Book.accession_no.ilike(s),
                Book.call_number.ilike(s),
                Book.category.ilike(s),
                Book.isbn.ilike(s),
            )
        )
    if category and category.lower() != "all":
        query = query.filter(Book.category == category)
    return query.order_by(Book.id.desc()).offset(skip).limit(limit).all()


@app.get("/public/stats", tags=["Public Portal"])
def get_public_stats(db: Session = Depends(get_db)):
    total_titles = db.query(Book).filter(Book.is_archived == False).count()
    total_copies = db.query(func.sum(Book.total_copies)).filter(Book.is_archived == False).scalar() or 0
    available_copies = db.query(func.sum(Book.available_copies)).filter(Book.is_archived == False).scalar() or 0
    cat_rows = (
        db.query(Book.category)
        .filter(Book.is_archived == False)
        .distinct()
        .all()
    )
    categories = sorted([r[0] for r in cat_rows if r[0]])
    return {
        "total_titles": total_titles,
        "total_copies": int(total_copies),
        "available_copies": int(available_copies),
        "categories": categories,
    }


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def serve_index_html():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "EVSU SmartLib Landing Page."})


@app.get("/login", include_in_schema=False)
@app.get("/login.html", include_in_schema=False)
def serve_login_html():
    login_path = os.path.join(os.path.dirname(__file__), "login.html")
    if os.path.exists(login_path):
        return FileResponse(login_path)
    return JSONResponse({"message": "Login page template not found"}, status_code=404)


@app.get("/mobile", include_in_schema=False)
@app.get("/mobile/", include_in_schema=False)
@app.get("/app", include_in_schema=False)
def serve_mobile_app():
    mobile_path = os.path.join(os.path.dirname(__file__), "mobile", "index.html")
    if os.path.exists(mobile_path):
        return FileResponse(mobile_path)
    return JSONResponse({"message": "Mobile app not found"}, status_code=404)


@app.get("/service-worker.js", include_in_schema=False)
def serve_mobile_service_worker():
    worker_path = os.path.join(os.path.dirname(__file__), "mobile", "service-worker.js")
    if os.path.exists(worker_path):
        return FileResponse(worker_path, media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})
    return JSONResponse({"message": "Service worker not found"}, status_code=404)


@app.get("/mobile-manifest.json", include_in_schema=False)
def serve_mobile_manifest():
    manifest_path = os.path.join(os.path.dirname(__file__), "mobile", "manifest.json")
    if os.path.exists(manifest_path):
        return FileResponse(manifest_path, media_type="application/manifest+json")
    return JSONResponse({"message": "Mobile manifest not found"}, status_code=404)


@app.get("/verify-email", include_in_schema=False)
@app.get("/reset-password", include_in_schema=False)
def serve_auth_action_page():
    action_path = os.path.join(os.path.dirname(__file__), "auth-action.html")
    if os.path.exists(action_path):
        return FileResponse(action_path)
    return JSONResponse({"message": "Account security page not found"}, status_code=404)


@app.get("/admin-dashboard.html", include_in_schema=False)
def serve_admin_dashboard():
    file_path = os.path.join(os.path.dirname(__file__), "admin-dashboard.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return JSONResponse({"message": "Admin dashboard template not found"}, status_code=404)


@app.get("/librarian-dashboard.html", include_in_schema=False)
def serve_librarian_dashboard():
    file_path = os.path.join(os.path.dirname(__file__), "librarian-dashboard.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return JSONResponse({"message": "Librarian dashboard template not found"}, status_code=404)
