from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from database import Base
from models import User, UserRole, BookCopy, BorrowRecord, BorrowStatus, LibraryAttendance
from schemas import BookCreate, BorrowStatusUpdate
from routers.admin_router import record_attendance
from routers.report_router import attendance_report
import crud


@pytest.fixture
def data():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        staff = User(username="staff", email="staff@example.test", full_name="Staff", role=UserRole.LIBRARIAN, hashed_password="unused", is_active=True, email_verified=True)
        patron = User(username="student", email="student@example.test", full_name="Student", role=UserRole.STUDENT, hashed_password="unused", is_active=True, email_verified=True)
        db.add_all([staff, patron]); db.commit()
        yield db, staff, patron
    engine.dispose()


def book(db, **kw):
    return crud.create_book(db, BookCreate(title="Title", author="Author", category="General", **kw))


def test_numeric_accessions_ignore_client_and_resolve_copies(data):
    db, _, _ = data
    one = book(db, accession_no="CLIENT-TEXT", total_copies=2, available_copies=2)
    two = book(db, accession_no=one.accession_no)
    copies = db.query(BookCopy).all()
    values = [one.accession_no, two.accession_no] + [c.accession_no for c in copies]
    assert len(values) == len(set(values))
    assert all(v.isascii() and v.isdigit() and len(v) >= 5 for v in values)
    for copy in copies:
        resolved, tag, is_copy = crud.resolve_book_copy(db, copy.accession_no)
        assert resolved.id == copy.book_id and tag == copy.accession_no and is_copy


def test_checkout_requires_explicit_future_due_and_preserves_stock(data):
    db, staff, patron = data
    b = book(db)
    for due in [None, datetime.now(timezone.utc)-timedelta(hours=1), datetime.now()+timedelta(days=1)]:
        result = crud.execute_circulation(db, "borrow", patron.username, b.accession_no, actor=staff.username, due_date=due)
        assert result['status'] == 'error'
        assert b.available_copies == 1
        assert db.query(BorrowRecord).count() == 0
    due = datetime.now(timezone(timedelta(hours=8)))+timedelta(days=5)
    result = crud.execute_circulation(db, "borrow", patron.username, b.accession_no, actor=staff.username, due_date=due)
    assert result['status'] == 'success'
    record = db.query(BorrowRecord).one()
    assert record.due_date == due.astimezone(timezone.utc).replace(tzinfo=None)
    assert record.return_date is None
    result = crud.execute_circulation(db, "return", None, record.copy_accession_no, actor=staff.username)
    assert result['status'] == 'success'
    assert record.return_date is not None


def test_approved_checkout_requires_due_without_mutation(data):
    db, _, patron = data
    b = book(db)
    rec = BorrowRecord(req_id='REQUEST', user_id=patron.id, book_id=b.id, status=BorrowStatus.APPROVED)
    db.add(rec);db.commit()
    assert crud.update_borrow_status(db, rec.req_id, BorrowStatus.CHECKED_OUT)[0] is None
    assert rec.issue_date is None and rec.copy_accession_no is None
    due = datetime.now(timezone.utc)+timedelta(days=2)
    assert crud.update_borrow_status(db, rec.req_id, BorrowStatus.CHECKED_OUT, due_date=due)[0]
    assert rec.due_date == due.replace(tzinfo=None)


def test_attendance_repeated_scans_and_philippine_boundaries(data):
    db, staff, patron = data
    first = record_attendance({'identifier':f'EVSU-CARD-2026::{patron.username}::student'},db,staff)
    second = record_attendance({'identifier':patron.username},db,staff)
    assert not first['duplicate'] and second['duplicate']
    assert db.query(LibraryAttendance).count() == 1
    entry = db.query(LibraryAttendance).one()
    entry.entered_at = datetime(2026,10,29,16,0) # midnight Oct 30 in Manila
    db.add(LibraryAttendance(user_id=patron.id, recorded_by=staff.id, entered_at=datetime(2026,10,30,3,0)))
    db.commit()
    day = datetime(2026,10,30).date()
    report = attendance_report(day, day, 'json', 'long', db, staff)
    assert report['visits'] == 2 and report['unique_visitors'] == 1
    export = attendance_report(day, day, 'csv', 'long', db, staff)
    assert b'Student' in export.body


def test_attendance_routes_reject_patron_access(data):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from security import get_current_user
    from database import get_db
    from routers.admin_router import router as admin_router
    from routers.report_router import router as report_router
    db, staff, patron = data
    app = FastAPI()
    app.include_router(admin_router);app.include_router(report_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: patron
    client = TestClient(app)
    assert client.post('/admin/attendance', json={'identifier':'student'}).status_code == 403
    assert client.get('/admin/reports/attendance?start=2026-10-30&end=2026-10-30').status_code == 403


def test_numeric_counter_cannot_reuse_imported_numbers(data):
    from models import Book
    db, _, _ = data
    book(db)
    db.add(Book(accession_no='09000', title='Imported', author='Author', category='General'))
    db.commit()
    assert int(book(db).accession_no) > 9000


def test_postgresql_schema_and_migration_preconditions(tmp_path):
    from sqlalchemy import create_mock_engine
    from migrate_sqlite_postgres import migrate
    statements = []
    engine = create_mock_engine('postgresql+psycopg://', lambda sql, *a, **kw: statements.append(str(sql.compile(dialect=engine.dialect))))
    Base.metadata.create_all(engine)
    assert any('library_attendance' in sql for sql in statements)
    assert any('WHERE' in sql and 'uq_active_borrow_copy' in sql for sql in statements)
    with pytest.raises(ValueError, match='does not exist'):
        migrate(tmp_path/'missing.db', 'postgresql+psycopg://')
    source = tmp_path/'source.db';source.touch()
    with pytest.raises(ValueError, match='PostgreSQL'):
        migrate(source, 'sqlite://')


def test_legacy_copy_alias_resolves_same_numeric_copy(data):
    db, staff, patron = data
    b = book(db)
    copy = db.query(BookCopy).filter_by(book_id=b.id).one()
    alias = f"{b.accession_no}-C01"
    assert crud.resolve_book_copy(db, alias)[1] == copy.accession_no
    due = datetime.now(timezone.utc)+timedelta(days=2)
    assert crud.execute_circulation(db, 'borrow', patron.username, alias, due_date=due)['status'] == 'success'
    assert crud.execute_circulation(db, 'borrow', patron.username, copy.accession_no, due_date=due)['status'] != 'success'
def test_overdue_checkout_can_be_returned_and_fine_is_calculated(data):
    db, staff, patron = data
    b = book(db, total_copies=1, available_copies=0)
    copy = db.query(BookCopy).filter_by(book_id=b.id).one()
    due = datetime.utcnow() - timedelta(days=1, hours=2)
    record = BorrowRecord(req_id='OVERDUE-RETURN', user_id=patron.id, book_id=b.id,
        copy_accession_no=copy.accession_no, status=BorrowStatus.OVERDUE,
        issue_date=due-timedelta(days=2), due_date=due)
    db.add(record); db.commit()
    result = crud.execute_circulation(db, 'return', None, copy.accession_no, actor=staff.username)
    assert result['status'] == 'warning'
    assert result['fine'] == crud.calculate_overdue_fine(due, datetime.utcnow())
    assert record.status == BorrowStatus.RETURNED and record.return_date is not None
    assert b.available_copies == 1
    from models import Fine
    assert db.query(Fine).filter_by(borrow_record_id=record.id, is_paid=False).count() == 1
    again = crud.execute_circulation(db, 'return', None, copy.accession_no, actor=staff.username)
    assert again['status'] == 'error'
    assert db.query(Fine).filter_by(borrow_record_id=record.id).count() == 1
