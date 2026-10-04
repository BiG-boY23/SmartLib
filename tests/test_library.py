import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from main import app
from database import Base, get_db
import crud

# In-memory test database with StaticPool for thread-safe shared in-memory SQLite
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=test_engine
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()
    crud.seed_initial_data(db)
    db.close()
    yield


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_public_static_routes():
    # Public index landing page (light theme)
    resp_index = client.get("/")
    assert resp_index.status_code == 200
    assert "EVSU SmartLib" in resp_index.text

    # Dedicated login page
    resp_login = client.get("/login.html")
    assert resp_login.status_code == 200
    assert "Staff Portal Login" in resp_login.text

    # Standalone Admin Dashboard
    resp_admin = client.get("/admin-dashboard.html")
    assert resp_admin.status_code == 200
    assert "Administrator Portal" in resp_admin.text

    # Standalone Librarian Dashboard
    resp_lib = client.get("/librarian-dashboard.html")
    assert resp_lib.status_code == 200
    assert "Librarian Workspace" in resp_lib.text


def test_public_feedback_submission():
    fb_data = {
        "name": "Maria Santos",
        "email": "maria@evsu.edu.ph",
        "category": "Collection Suggestion",
        "message": "Please add more books on Cloud Infrastructure.",
    }
    response = client.post("/public/feedback", json=fb_data)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Maria Santos"
    assert data["category"] == "Collection Suggestion"


def test_login_and_admin_feedback_access():
    # Login as Super Admin
    login_resp = client.post(
        "/auth/login",
        json={"username": "ADMIN-EVSU-2026", "password": "admin123"},
    )
    assert login_resp.status_code == 200
    data = login_resp.json()
    assert data["role"] in ("superadmin", "admin")
    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Submit feedback publicly
    client.post(
        "/public/feedback",
        json={
            "name": "Test Visitor",
            "email": "test@evsu.edu.ph",
            "category": "General Inquiry",
            "message": "Library hours test",
        },
    )

    # Retrieve feedbacks as Admin
    fb_resp = client.get("/admin/feedbacks", headers=headers)
    assert fb_resp.status_code == 200
    assert len(fb_resp.json()) >= 1


def test_rbac_enforcement():
    # Login as Librarian and attempt to access a superadmin-only endpoint
    login_resp = client.post(
        "/auth/login",
        json={"username": "EVSU-LIB-2026", "password": "password123"},
    )
    assert login_resp.status_code == 200
    data = login_resp.json()
    assert data["role"] == "librarian"
    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Librarian cannot access superadmin-only user provisioning endpoint
    response = client.post(
        "/admin/users/staff",
        json={
            "staff_id": "TEST-STAFF-001",
            "full_name": "Test Staff",
            "email": "teststaff@evsu.edu.ph",
            "department": "IT Dept",
            "password": "TestPass123!",
        },
        headers=headers,
    )
    # Librarian should be forbidden from creating staff accounts (superadmin only)
    assert response.status_code == 403


def test_staff_login_librarian():
    """Test that Librarian staff can authenticate and get correct role in JWT."""
    login_resp = client.post(
        "/auth/login",
        json={"username": "EVSU-LIB-2026", "password": "password123"},
    )
    assert login_resp.status_code == 200
    data = login_resp.json()
    assert data["role"] == "librarian"
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_admin_book_management():
    # Login as Librarian
    login_resp = client.post(
        "/auth/login",
        json={"username": "EVSU-LIB-2026", "password": "password123"},
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create Book with all 10 fields
    new_book = {
        "accession_no": "LIB-QR-9999",
        "title": "Clean Code Architecture",
        "author": "Robert C. Martin",
        "publisher": "Prentice Hall",
        "year_published": "2024",
        "isbn": "978-0132350884",
        "category": "Computer Science",
        "call_number": "QA 76.76 .D47 M37 2024",
        "location_rack": "Rack CS-9",
        "total_copies": 3,
        "available_copies": 3,
        "condition": "New",
    }
    response = client.post("/admin/books", json=new_book, headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert data["accession_no"] == "LIB-QR-9999"
    assert data["title"] == "Clean Code Architecture"
    assert data["author"] == "Robert C. Martin"
    assert data["publisher"] == "Prentice Hall"
    assert data["year_published"] == "2024"
    assert data["isbn"] == "978-0132350884"
    assert data["category"] == "Computer Science"
    assert data["call_number"] == "QA 76.76 .D47 M37 2024"
    assert data["total_copies"] == 3
    assert data["condition"] == "New"

    # List Books
    list_resp = client.get("/admin/books", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 1

    book_id = data["id"]

    # Update Book
    update_payload = {
        "title": "Clean Code Architecture (Updated)",
        "condition": "Good",
        "total_copies": 5
    }
    update_resp = client.put(f"/admin/books/{book_id}", json=update_payload, headers=headers)
    assert update_resp.status_code == 200
    assert update_resp.json()["title"] == "Clean Code Architecture (Updated)"
    assert update_resp.json()["total_copies"] == 5
    assert update_resp.json()["condition"] == "Good"

    # Archive Book (Soft delete)
    archive_resp = client.patch(f"/admin/books/{book_id}/archive", headers=headers)
    assert archive_resp.status_code == 200
    assert archive_resp.json()["is_archived"] is True

    # Verify not in regular active list
    list_active = client.get("/admin/books", headers=headers)
    assert all(b["id"] != book_id for b in list_active.json())

    # Verify in archived list
    list_archived = client.get("/admin/books/archived", headers=headers)
    assert any(b["id"] == book_id for b in list_archived.json())

    # Restore Book
    restore_resp = client.patch(f"/admin/books/{book_id}/restore", headers=headers)
    assert restore_resp.status_code == 200
    assert restore_resp.json()["is_archived"] is False

    # Delete Book (Permanent delete)
    del_resp = client.delete(f"/admin/books/{book_id}", headers=headers)
    assert del_resp.status_code == 200
    assert "deleted successfully" in del_resp.json()["message"]


def test_circulation_execute():
    login_resp = client.post(
        "/auth/login",
        json={"username": "EVSU-LIB-2026", "password": "password123"},
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    circ_req = {
        "mode": "borrow_clear",
        "patron_id": "EVSU-LIB-2026",
        "accession_no": "LIB-QR-1001",
    }
    response = client.post(
        "/admin/circulation/execute", json=circ_req, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # 1. Test Patron Lookup
    patron_lookup = client.get("/admin/circulation/patron/EVSU-LIB-2026", headers=headers)
    assert patron_lookup.status_code == 200
    p_data = patron_lookup.json()
    assert p_data["found"] is True
    assert p_data["username"] == "EVSU-LIB-2026"

    # 2. Test Book Lookup
    book_lookup = client.get("/admin/circulation/book/LIB-QR-1003", headers=headers)
    assert book_lookup.status_code == 200
    b_data = book_lookup.json()
    assert b_data["found"] is True
    assert b_data["accession_no"] == "LIB-QR-1003"

    # 3. Test Real Scanner Borrow (mode: borrow)
    real_borrow_req = {
        "mode": "borrow",
        "patron_id": "EVSU-LIB-2026",
        "accession_no": "LIB-QR-1003-C01",
    }
    borrow_resp = client.post("/admin/circulation/execute", json=real_borrow_req, headers=headers)
    assert borrow_resp.status_code == 200
    assert borrow_resp.json()["status"] == "success"
    assert "Checked out" in borrow_resp.json()["message"]

    # 4. Test Real Scanner Return (mode: return)
    real_return_req = {
        "mode": "return",
        "accession_no": "LIB-QR-1003-C01",
    }
    return_resp = client.post("/admin/circulation/execute", json=real_return_req, headers=headers)
    assert return_resp.status_code == 200
    assert return_resp.json()["status"] == "success"
    assert "Returned on time" in return_resp.json()["message"]
