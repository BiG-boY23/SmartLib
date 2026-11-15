"""Coverage for staff account provisioning/archive and copy-level stock status."""
from datetime import datetime, timezone
import re

import crud
import pytest
from routers import auth_router
from models import AuditLog, Book, BookCopy, BorrowRecord, BorrowStatus, User, UserRole, UserSession

from test_message_deletion import inbox_api


def test_staff_ids_are_generated_and_account_fields_are_distinct(inbox_api, monkeypatch):
    client, headers, factory = inbox_api
    monkeypatch.setenv('SMARTLIB_ENV', 'development')
    monkeypatch.setenv('SMARTLIB_REQUIRE_STAFF_MFA', 'false')
    client.app.include_router(auth_router.router)
    assert client.get('/admin/users/staff/next-id', headers=headers['librarian']).status_code == 403
    next_id = client.get('/admin/users/staff/next-id', headers=headers['superadmin'])
    assert next_id.status_code == 200
    assert re.fullmatch(r'\d{4}-0000', next_id.json()['staff_id'])

    response = client.post('/admin/users/staff', headers=headers['superadmin'], json={
        'username': 'librarian.jane', 'first_name': 'Jane', 'middle_name': 'M',
        'last_name': 'Reyes', 'email': 'Jane.Reyes@evsu.edu.ph',
        'department': 'Circulation Desk', 'password': 'Securepass1!', 'confirm_password': 'Securepass1!',
    })
    assert response.status_code == 201, response.text
    staff = response.json()
    assert staff['username'] == 'librarian.jane'
    assert staff['staff_id'] == next_id.json()['staff_id']
    assert staff['full_name'] == 'Jane M Reyes'
    assert staff['first_name'] == 'Jane' and staff['middle_name'] == 'M' and staff['last_name'] == 'Reyes'
    assert client.post('/auth/login', json={'username': staff['staff_id'], 'password': 'Securepass1!'}).status_code == 200
    assert client.get('/admin/users/staff/next-id', headers=headers['superadmin']).json()['staff_id'].endswith('-0001')
    assert client.post('/admin/users/staff', headers=headers['superadmin'], json={
        'staff_id': '2026-0009', 'username': 'another', 'first_name': 'A', 'last_name': 'B',
        'email': 'another@evsu.edu.ph', 'department': 'Desk', 'password': 'Securepass1!',
        'confirm_password': 'Securepass1!',
    }).status_code == 422
    mismatch = client.post('/admin/users/staff', headers=headers['superadmin'], json={
        'username': 'librarian.mismatch', 'first_name': 'Test', 'last_name': 'User',
        'email': 'mismatch@evsu.edu.ph', 'department': 'Desk', 'password': 'Securepass1!',
        'confirm_password': 'Different1!',
    })
    assert mismatch.status_code == 422
    weak_password = client.post('/admin/users/staff', headers=headers['superadmin'], json={
        'username': 'librarian.weak', 'first_name': 'Test', 'last_name': 'User',
        'email': 'weak@evsu.edu.ph', 'department': 'Desk', 'password': 'weakpassword',
        'confirm_password': 'weakpassword',
    })
    assert weak_password.status_code == 422


def test_user_archive_revokes_sessions_and_restore_requires_reactivation(inbox_api):
    client, headers, factory = inbox_api
    patron = next(user for user in client.get('/admin/users', headers=headers['superadmin']).json() if user['username'] == 'student')
    assert client.patch(f"/admin/users/{patron['id']}/archive", headers=headers['admin']).status_code == 403
    archived = client.patch(f"/admin/users/{patron['id']}/archive", headers=headers['superadmin'])
    assert archived.status_code == 200 and archived.json()['is_archived'] is True and archived.json()['is_active'] is False
    assert all(user['username'] != 'student' for user in client.get('/admin/users', headers=headers['superadmin']).json())
    assert client.get('/admin/users?archived=true', headers=headers['admin']).status_code == 403
    assert any(user['username'] == 'student' for user in client.get('/admin/users?archived=true', headers=headers['superadmin']).json())
    with factory() as db:
        session = db.query(UserSession).filter(UserSession.user_id == patron['id']).one()
        assert session.revoked_at is not None

    restored = client.patch(f"/admin/users/{patron['id']}/restore", headers=headers['superadmin'])
    assert restored.status_code == 200 and restored.json()['is_archived'] is False and restored.json()['is_active'] is False
    assert client.put(f"/admin/users/{patron['id']}/status", headers=headers['superadmin']).json()['is_active'] is True
    with factory() as db:
        events = [row.event for row in db.query(AuditLog).order_by(AuditLog.id).all()]
        assert any('Archived user account: student' in event for event in events)
        assert any('Restored user account: student' in event for event in events)


def test_missing_copy_changes_available_count_and_cannot_be_issued(inbox_api):
    client, headers, factory = inbox_api
    with factory() as db:
        book = Book(accession_no='COPY-STATUS', title='Copy status test', author='Library', category='Test', total_copies=2, available_copies=2)
        db.add(book)
        db.flush()
        first = BookCopy(book_id=book.id, copy_number=1, accession_no='COPY-STATUS-C01')
        second = BookCopy(book_id=book.id, copy_number=2, accession_no='COPY-STATUS-C02')
        db.add_all([first, second])
        db.commit()
        book_id, first_id, second_id = book.id, first.id, second.id

    missing = client.patch(f'/admin/books/{book_id}/copies/{first_id}/status', headers=headers['librarian'], json={'status': 'missing'})
    assert missing.status_code == 200 and missing.json()['status'] == 'missing'
    with factory() as db:
        book = db.get(Book, book_id)
        assert book.total_copies == 2 and book.available_copies == 1
        result = crud.execute_circulation(db, 'borrow', 'student', 'COPY-STATUS', actor='librarian', due_date=datetime(2026, 12, 1, tzinfo=timezone.utc))
        assert result['status'] == 'success', result
        assert result['copy_accession_no'] == 'COPY-STATUS-C02'
        patron = db.query(User).filter(User.username == 'student').one()
        checkout = db.query(BorrowRecord).filter(BorrowRecord.user_id == patron.id, BorrowRecord.book_id == book_id).one()
        assert checkout.copy_accession_no == 'COPY-STATUS-C02'

    assert client.patch(f'/admin/books/{book_id}/copies/{second_id}/status', headers=headers['librarian'], json={'status': 'missing'}).status_code == 409
    restored = client.patch(f'/admin/books/{book_id}/copies/{first_id}/status', headers=headers['librarian'], json={'status': 'available'})
    assert restored.status_code == 200 and restored.json()['status'] == 'available'
    with factory() as db:
        assert db.get(Book, book_id).available_copies == 1  # one copy remains checked out
