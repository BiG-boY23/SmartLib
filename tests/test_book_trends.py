"""Book-level reports use actual event dates, stable identities and staff access."""
import csv
import io
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from models import Book, BorrowRecord, BorrowStatus, User, UserRole, UserSession
from routers import report_router
from security import create_access_token


@pytest.fixture
def book_api(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "books.db"}', connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(report_router.router)

    def db_session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_session
    tokens = {}
    with factory() as db:
        users = {}
        for name, role, department, course in [
            ("librarian", UserRole.LIBRARIAN, "Library", ""),
            ("admin", UserRole.ADMIN, "Library", ""),
            ("superadmin", UserRole.SUPERADMIN, "Library", ""),
            ("student", UserRole.STUDENT, "Computers", "BSIT"),
            ("faculty", UserRole.FACULTY, "Education", "BPEd"),
        ]:
            user = User(username=name, full_name=name.title(), email=f"{name}@evsu.edu.ph", role=role,
                        department=department, course=course, hashed_password="unused", is_active=True, email_verified=True)
            db.add(user)
            db.flush()
            users[name] = user
            now = datetime.utcnow()
            sid = f"books-{name}"
            db.add(UserSession(session_id=sid, user_id=user.id, created_at=now,
                               last_activity_at=now, expires_at=now + timedelta(hours=1)))
            tokens[name] = {"Authorization": "Bearer " + create_access_token({"sub": name, "sid": sid, "role": role.value})}
        books = {}
        for key, title in [("A", "Shared title"), ("B", "Shared title"), ("C", "Archived reference"),
                           ("D", "Prior week only"), ("E", "Outside report"), ("P", "Pending only")]:
            book = Book(accession_no=key, title=title, author="=Formula author" if key == "A" else "Library",
                        category="Reference", total_copies=5, available_copies=5, is_archived=key == "C")
            db.add(book)
            db.flush()
            books[key] = book
        # Current Philippine week: Oct 19 00:00 to Oct 25 23:59:59, i.e.
        # UTC Oct 18 16:00 (inclusive) to Oct 25 16:00 (exclusive).
        activities = [
            ("A-current-1", "A", "student", BorrowStatus.RETURNED, datetime(2026, 10, 18, 16), datetime(2026, 10, 19, 2)),
            ("A-current-2", "A", "student", BorrowStatus.CHECKED_OUT, datetime(2026, 10, 20, 3), None),
            ("A-current-end", "A", "student", BorrowStatus.CHECKED_OUT, datetime(2026, 10, 25, 15, 59, 59), None),
            ("A-previous", "A", "student", BorrowStatus.RETURNED, datetime(2026, 10, 11, 16), datetime(2026, 10, 18, 15, 59, 59)),
            ("B-current", "B", "faculty", BorrowStatus.RETURNED, datetime(2026, 10, 21, 2), datetime(2026, 10, 22, 4)),
            ("B-prior-1", "B", "faculty", BorrowStatus.RETURNED, datetime(2026, 10, 12, 2), datetime(2026, 10, 18, 16)),
            ("B-prior-2", "B", "faculty", BorrowStatus.RETURNED, datetime(2026, 10, 13, 2), datetime(2026, 10, 18, 16)),
            ("C-return", "C", "student", BorrowStatus.RETURNED, datetime(2026, 10, 12, 3), datetime(2026, 10, 19, 4)),
            ("D-previous", "D", "student", BorrowStatus.OVERDUE, datetime(2026, 10, 15, 2), None),
            ("E-outside", "E", "student", BorrowStatus.RETURNED, datetime(2026, 10, 11, 15, 59, 59), datetime(2026, 10, 25, 16)),
            ("P-pending", "P", "student", BorrowStatus.PENDING, datetime(2026, 10, 19, 3), None),
            ("P-approved", "P", "student", BorrowStatus.APPROVED, datetime(2026, 10, 19, 3), None),
        ]
        for reference, key, person, status, issued, returned in activities:
            db.add(BorrowRecord(req_id=reference, book_id=books[key].id, user_id=users[person].id,
                                status=status, issue_date=issued, return_date=returned))
        db.commit()
    yield TestClient(app), tokens
    engine.dispose()


PERIOD = "/admin/reports/circulation?start=2026-10-19&end=2026-10-25&role=all"
WEEK = "/admin/reports/book-trends?week=2026-10-21"


def test_period_books_include_return_only_archived_titles_and_keep_ids_separate(book_api):
    client, tokens = book_api
    report = client.get(PERIOD, headers=tokens["librarian"]).json()
    by_accession = {book["accession_no"]: book for book in report["books"]}
    assert set(by_accession) == {"A", "B", "C"}
    assert by_accession["A"]["title"] == by_accession["B"]["title"]
    assert by_accession["A"]["book_id"] != by_accession["B"]["book_id"]
    assert (by_accession["A"]["borrowed_items"], by_accession["A"]["returned_items"]) == (3, 1)
    assert (by_accession["B"]["borrowed_items"], by_accession["B"]["returned_items"]) == (1, 3)
    assert (by_accession["C"]["borrowed_items"], by_accession["C"]["returned_items"]) == (0, 1)
    assert sum(book["borrowed_items"] for book in report["books"]) == report["summary"]["borrowed_items"] == 4
    assert sum(book["returned_items"] for book in report["books"]) == report["summary"]["returned_items"] == 5
    assert report["daily"][0]["borrowed_items"] == 1
    assert report["daily"][-1]["borrowed_items"] == 1
    assert {row["book_id"] for row in report["rows"]} == {book["book_id"] for book in report["books"]}


def test_weekly_ranking_uses_philippine_week_and_shows_both_activity_deltas(book_api):
    client, tokens = book_api
    response = client.get(WEEK, headers=tokens["admin"])
    assert response.status_code == 200
    report = response.json()
    assert (report["week_start"], report["week_end"]) == ("2026-10-19", "2026-10-25")
    assert (report["previous_week_start"], report["previous_week_end"]) == ("2026-10-12", "2026-10-18")
    assert len(report["daily"]) == len(report["previous_daily"]) == 7
    assert [book["accession_no"] for book in report["books"]] == ["A", "B", "C", "D"]
    books = {book["accession_no"]: book for book in report["books"]}
    assert (books["A"]["rank"], books["A"]["previous_borrowed_items"], books["A"]["borrowed_change"], books["A"]["returned_change"]) == (1, 1, 2, 0)
    assert (books["B"]["rank"], books["B"]["previous_borrowed_items"], books["B"]["borrowed_change"], books["B"]["returned_change"]) == (2, 2, -1, 3)
    assert books["C"]["rank"] is None and books["C"]["returned_change"] == 1
    assert books["D"]["rank"] is None and books["D"]["borrowed_change"] == -1
    assert report["previous_summary"]["borrowed_items"] == 5
    assert report["previous_summary"]["returned_items"] == 1


def test_book_views_share_audience_department_and_course_filters(book_api):
    client, tokens = book_api
    for endpoint in (PERIOD, WEEK):
        filtered = client.get(endpoint + "&department=Education&course=BPEd", headers=tokens["admin"]).json()
        assert [book["accession_no"] for book in filtered["books"]] == ["B"]
        assert filtered["summary"]["borrowed_items"] == 1
        assert filtered["summary"]["returned_items"] == 3
        empty = client.get(endpoint + "&department=Missing", headers=tokens["librarian"]).json()
        assert empty["books"] == [] and empty["summary"]["returned_items"] == 0
    students = client.get(WEEK + "&role=student", headers=tokens["superadmin"]).json()
    assert {book["accession_no"] for book in students["books"]} == {"A", "C", "D"}
    assert students["summary"]["borrowed_items"] == 3 and students["summary"]["returned_items"] == 2


def test_weekly_endpoint_staff_access_default_week_and_validation(book_api):
    client, tokens = book_api
    assert client.get(WEEK).status_code == 401
    assert client.get(WEEK, headers=tokens["student"]).status_code == 403
    for role in ("admin", "superadmin", "librarian"):
        assert client.get(WEEK, headers=tokens[role]).status_code == 200
    current = client.get("/admin/reports/book-trends", headers=tokens["librarian"]).json()
    from reporting import LOCAL_TZ
    today = datetime.now(LOCAL_TZ).date()
    assert current["week_start"] == str(today - timedelta(days=today.weekday()))
    assert client.get(WEEK + "&role=unknown", headers=tokens["librarian"]).status_code == 422
    assert client.get("/admin/reports/book-trends?week=not-a-date", headers=tokens["librarian"]).status_code == 422
    assert client.get("/admin/reports/book-trends?week=0001-01-01", headers=tokens["librarian"]).status_code == 400


def test_per_book_export_tables_have_real_counts_and_safe_csv(book_api):
    client, tokens = book_api
    response = client.get(PERIOD + "&format=csv", headers=tokens["admin"])
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    start, end = rows.index(["Books borrowed and returned"]), rows.index(["Activity details"])
    exported = rows[start + 2:end - 1]
    assert exported == [["A", "Shared title", "'=Formula author", "3", "1"],
                        ["B", "Shared title", "Library", "1", "3"],
                        ["C", "Archived reference", "Library", "0", "1"]]
    docx = client.get(PERIOD + "&format=docx", headers=tokens["librarian"])
    assert docx.status_code == 200
    with zipfile.ZipFile(io.BytesIO(docx.content)) as package:
        document = ElementTree.fromstring(package.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        tables = document.findall(".//w:tbl", ns)
        assert len(tables) == 2
        cells = [["".join(cell.itertext()) for cell in row.findall("w:tc", ns)] for row in tables[0].findall("w:tr", ns)]
        assert ["C", "Archived reference", "Library", "0", "1"] in cells
    pdf = client.get(PERIOD + "&format=pdf", headers=tokens["admin"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_weekly_csv_keeps_rank_counts_deltas_and_formula_safe_text(book_api):
    client, tokens = book_api
    response = client.get(WEEK + "&format=csv", headers=tokens["admin"])
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert ["Week", "2026-10-19", "To", "2026-10-25"] in rows
    assert ["Previous week", "2026-10-12", "To", "2026-10-18"] in rows
    assert ["Timezone", "Asia/Manila (UTC+08:00)"] in rows
    header = ["Rank", "Accession no.", "Book", "Author", "Borrowed", "Returned", "Previous borrowed",
              "Previous returned", "Borrowing change", "Return change"]
    assert rows[rows.index(header) + 1:] == [
        ["1", "A", "Shared title", "'=Formula author", "3", "1", "1", "1", "2", "0"],
        ["2", "B", "Shared title", "Library", "1", "3", "2", "0", "-1", "3"],
        ["", "C", "Archived reference", "Library", "0", "1", "1", "0", "-1", "1"],
        ["", "D", "Prior week only", "Library", "0", "0", "1", "0", "-1", "0"],
    ]


@pytest.mark.parametrize("format,mime", [
    ("csv", "text/csv"), ("pdf", "application/pdf"),
    ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
])
def test_weekly_exports_require_staff_and_preserve_filter_scope(book_api, format, mime):
    client, tokens = book_api
    endpoint = WEEK + f"&format={format}&role=faculty&department=Education&course=BPEd"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=tokens["student"]).status_code == 403
    response = client.get(endpoint, headers=tokens["librarian"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(mime)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="SmartLib-weekly-books-2026-10-19-2026-10-25.{format}"')
    if format == "csv":
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert ["Audience", "faculty", "Department", "Education", "Course", "BPEd"] in rows
        assert rows[-1] == ["1", "B", "Shared title", "Library", "1", "3", "2", "0", "-1", "3"]
        assert sum(len(row) == 10 for row in rows) == 2  # Header and the matching book.
    elif format == "docx":
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            document = ElementTree.fromstring(package.read("word/document.xml"))
            text = " ".join(document.itertext())
            assert "Week: 2026-10-19 to 2026-10-25" in text
            assert "Previous week: 2026-10-12 to 2026-10-18" in text
            assert "Timezone: Asia/Manila (UTC+08:00)" in text
            assert "Audience: faculty | Department: Education | Course: BPEd" in text
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            tables = document.findall(".//w:tbl", ns)
            assert len(tables) == 1
            rows = [["".join(cell.itertext()) for cell in row.findall("w:tc", ns)]
                    for row in tables[0].findall("w:tr", ns)]
            assert len(rows) == 2
            assert rows[1] == ["1", "Shared titleLibraryB", "1", "2", "-1", "3", "0", "3"]
    else:
        assert response.content.startswith(b"%PDF") and response.content.rstrip().endswith(b"%%EOF")


def test_weekly_empty_exports_remain_valid_and_invalid_format_is_rejected(book_api):
    client, tokens = book_api
    for format in ("csv", "pdf", "docx"):
        response = client.get(WEEK + f"&format={format}&department=Missing", headers=tokens["admin"])
        assert response.status_code == 200
        if format == "csv":
            rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
            assert rows[-1][0] == "Rank"
        elif format == "pdf":
            assert response.content.startswith(b"%PDF")
        else:
            with zipfile.ZipFile(io.BytesIO(response.content)) as package:
                assert b"No recorded book activity matches these weeks and filters." in package.read("word/document.xml")
    assert client.get(WEEK + "&format=xml", headers=tokens["admin"]).status_code == 422
