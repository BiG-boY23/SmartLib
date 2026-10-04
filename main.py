import os
from contextlib import asynccontextmanager

from config import load_project_dotenv

# Load settings before importing routers/security modules that read environment values.
load_project_dotenv()

from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, or_, func
from sqlalchemy.orm import Session

from database import engine, Base, SessionLocal, get_db
from models import Book
from routers import auth_router, admin_router, mobile_router
from schemas import FeedbackCreate, FeedbackResponse, BookResponse
import crud


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables & seed database
    Base.metadata.create_all(bind=engine)
    # Preserve existing accounts as verified when upgrading older SQLite files.
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "email_verified" not in user_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 1"
            )
    db = SessionLocal()
    try:
        crud.seed_initial_data(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="EVSU SmartLib Integrated Library Resource & Circulation System",
    description="Production-ready FastAPI backend for Library Web Management Portal (Admin & Librarian)",
    version="2.0.0",
    lifespan=lifespan,
)

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
    app.add_middleware(HTTPSRedirectMiddleware)


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
    if request.url.path.startswith(("/auth/", "/admin/")):
        response.headers["Cache-Control"] = "no-store"
    return response

# Include API Routers
app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(mobile_router.router)

# Installable patron app assets; mobile API routes above take precedence.
mobile_assets_path = os.path.join(os.path.dirname(__file__), "mobile")
app.mount("/mobile-assets", StaticFiles(directory=mobile_assets_path), name="mobile-assets")

static_assets_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_assets_path):
    app.mount("/static", StaticFiles(directory=static_assets_path), name="static")


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


@app.post("/public/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED, tags=["Public Portal"])
def submit_public_feedback(fb_data: FeedbackCreate, db: Session = Depends(get_db)):
    return crud.create_feedback(db, fb_data)


@app.get("/public/books", response_model=List[BookResponse], tags=["Public Portal"])
def get_public_books(
    search: Optional[str] = None,
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
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
