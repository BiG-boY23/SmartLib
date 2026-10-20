"""Retained deletion, archive RBAC, ownership, and active-history boundaries."""
from datetime import datetime, timedelta
import json

import pytest

from archive_service import record_content
from models import (AcquisitionSuggestion, AuditLog, Book, BorrowRecord, BorrowStatus,
    DeletedArchive, SignupOption, StaffArchive, SupportMessage, SupportThread, User, UserRole, UserSession)
from security import create_access_token
from test_message_deletion import inbox_api, start_conversation


def _archive(client, headers, kind=None):
    response = client.get('/admin/deleted-archives', params={'kind': kind} if kind else {}, headers=headers['superadmin'])
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    item = response.json()['items'][0]
    detail = client.get('/admin/deleted-archives/' + item['id'], headers=headers['superadmin'])
    assert detail.status_code == 200
    assert detail.headers['cache-control'] == 'no-store'
    return detail.json()


@pytest.mark.parametrize('role', ['librarian', 'admin', 'superadmin'])
def test_staff_deleted_message_is_retained_only_in_superadmin_archive(inbox_api, role):
    client, headers, factory = inbox_api
    thread_id, message_id = start_conversation(client, headers, role, body='Private historical message')
    assert client.delete(f'/admin/messages/{thread_id}/messages/{message_id}', headers=headers[role]).status_code == 200
    detail = _archive(client, headers, 'messages')
    assert detail['deleted_by'] == role and detail['origin'] == 'staff' and detail['action'] == 'delete_message'
    assert detail['record_id'] == str(message_id)
    assert detail['content']['messages'][0]['body'] == 'Private historical message'
    assert detail['content']['thread']['id'] == thread_id
    assert detail['content']['patron']['username'] == 'student'
    for denied in ['librarian', 'admin', 'student']:
        assert client.get('/admin/deleted-archives', headers=headers[denied]).status_code == 403
        assert client.get('/admin/deleted-archives/' + detail['id'], headers=headers[denied]).status_code == 403
        assert client.get('/admin/archives', headers=headers[denied]).status_code == 403
    assert client.get('/admin/deleted-archives').status_code == 401
    with factory() as db:
        assert db.get(SupportMessage, message_id) is None
        assert db.query(DeletedArchive).count() == 1


@pytest.mark.parametrize('suffix,action', [('/messages', 'clear_messages'), ('', 'delete_conversation')])
def test_mobile_clearing_or_deleting_retains_full_history(inbox_api, suffix, action):
    client, headers, factory = inbox_api
    thread_id, _ = start_conversation(client, headers, 'librarian', body='Staff history')
    assert client.post(f'/mobile/messages/{thread_id}/reply', headers=headers['student'], json={'message': 'Patron history'}).status_code == 201
    before = client.get(f'/mobile/messages/{thread_id}', headers=headers['student']).json()['thread']
    response = client.delete(f'/mobile/messages/{thread_id}{suffix}', headers=headers['student'], params={
        'expected_created_at': before['created_at'], 'expected_updated_at': before['updated_at']})
    assert response.status_code == 200 and response.json()['deleted_messages'] == 2
    detail = _archive(client, headers)
    assert detail['origin'] == 'mobile' and detail['action'] == action
    assert [message['body'] for message in detail['content']['messages']] == ['Staff history', 'Patron history']
    with factory() as db:
        assert db.query(SupportMessage).count() == 0
        assert (db.get(SupportThread, thread_id) is not None) == bool(suffix)
    if suffix:
        assert client.get(f'/mobile/messages/{thread_id}', headers=headers['student']).json()['messages'] == []
    else:
        assert client.get(f'/admin/messages/{thread_id}', headers=headers['librarian']).status_code == 404
        assert client.get('/mobile/messages', headers=headers['student']).json() == []


def test_mobile_individual_deletion_checks_sender_and_pair_and_stale_snapshot(inbox_api):
    client, headers, factory = inbox_api
    thread_id, staff_message = start_conversation(client, headers, 'librarian')
    assert client.delete(f'/mobile/messages/{thread_id}/messages/{staff_message}', headers=headers['student']).status_code == 404
    assert client.post(f'/mobile/messages/{thread_id}/reply', headers=headers['student'], json={'message': 'Own message'}).status_code == 201
    history = client.get(f'/mobile/messages/{thread_id}', headers=headers['student']).json()
    own = history['messages'][-1]
    bad = {'expected_created_at': '2000-01-01T00:00:00', 'expected_thread_created_at': history['thread']['created_at']}
    url = f'/mobile/messages/{thread_id}/messages/{own["id"]}'
    assert client.delete(url, headers=headers['student'], params=bad).status_code == 409
    with factory() as db:
        assert db.query(DeletedArchive).count() == 0
        assert db.query(AuditLog).count() == 0
        assert db.get(SupportMessage, own['id']).body == 'Own message'
    bad['expected_created_at'] = own['created_at']
    assert client.delete(url, headers=headers['student'], params=bad).status_code == 200
    detail = _archive(client, headers)
    assert detail['content']['messages'][0]['body'] == 'Own message'
    assert client.get(f'/mobile/messages/{thread_id}', headers=headers['student']).json()['messages'][0]['id'] == staff_message


def test_mobile_cannot_delete_another_patrons_conversation(inbox_api):
    client, headers, factory = inbox_api
    thread_id, message_id = start_conversation(client, headers, 'librarian')
    with factory() as db:
        user = User(username='other', email='other@evsu.edu.ph', full_name='Other reader', hashed_password='unused', role=UserRole.STUDENT)
        db.add(user); db.flush()
        now = datetime.utcnow()
        db.add(UserSession(session_id='other-sid', user_id=user.id, created_at=now, last_activity_at=now, expires_at=now + timedelta(hours=1)))
        db.commit()
    other = {'Authorization': 'Bearer ' + create_access_token({'sub': 'other', 'sid': 'other-sid'})}
    for suffix in ['', '/messages', f'/messages/{message_id}']:
        assert client.delete(f'/mobile/messages/{thread_id}{suffix}', headers=other).status_code == 404
    with factory() as db:
        assert db.query(DeletedArchive).count() == 0
        assert db.get(SupportMessage, message_id) is not None


def test_archived_threads_hidden_from_all_active_apis_and_visible_to_superadmin(inbox_api):
    client, headers, factory = inbox_api
    thread_id, _ = start_conversation(client, headers, 'librarian')
    assert client.put(f'/admin/archives/messages/{thread_id}', params={'reason': 'Resolved issue'}, headers=headers['librarian']).status_code == 200
    for role, base in [('librarian', 'admin'), ('student', 'mobile')]:
        assert client.get(f'/{base}/messages', headers=headers[role]).json() == []
        assert client.get(f'/{base}/messages/{thread_id}', headers=headers[role]).status_code == 404
        assert client.post(f'/{base}/messages/{thread_id}/reply', headers=headers[role], json={'message': 'Leak attempt'}).status_code == 404
    for role in ['librarian', 'admin', 'student']:
        assert client.get('/admin/messages?archive_view=true', headers=headers[role]).status_code == 403
        assert client.get(f'/admin/messages/{thread_id}?archive_view=true', headers=headers[role]).status_code == 403
    assert client.get('/admin/messages?archive_view=true', headers=headers['superadmin']).json()[0]['id'] == thread_id
    assert client.get(f'/admin/messages/{thread_id}?archive_view=true', headers=headers['superadmin']).status_code == 200
    archive = _archive(client, headers)
    assert archive['content']['messages'][0]['body'] == 'Original message'
    assert archive['content']['reason'] == 'Resolved issue'
    assert client.put(f'/admin/archives/messages/{thread_id}?archived=false', headers=headers['librarian']).status_code == 403
    assert client.put(f'/admin/archives/messages/{thread_id}?archived=false', headers=headers['superadmin']).status_code == 200
    assert client.get(f'/mobile/messages/{thread_id}', headers=headers['student']).status_code == 200


def test_book_deletion_retains_copies_and_circulation_foreign_keys(inbox_api):
    client, headers, factory = inbox_api
    created = client.post('/admin/books', headers=headers['librarian'], json={'accession_no': 'ARCHIVE-BOOK', 'title': 'Historical title', 'author': 'Author', 'category': 'History', 'total_copies': 2}).json()
    with factory() as db:
        patron = db.query(User).filter_by(username='student').one()
        db.add(BorrowRecord(req_id='ARCHIVE-LOAN', user_id=patron.id, book_id=created['id'], status=BorrowStatus.RETURNED))
        db.commit()
    assert client.delete(f'/admin/books/{created["id"]}', headers=headers['librarian']).status_code == 200
    detail = _archive(client, headers, 'books')
    assert detail['content']['book']['title'] == 'Historical title' and len(detail['content']['copies']) == 2
    with factory() as db:
        assert db.get(Book, created['id']).is_archived
        assert db.query(BorrowRecord).one().book.title == 'Historical title'
    assert client.get('/admin/books', headers=headers['librarian']).json() == []
    assert client.get(f'/mobile/books/{created["id"]}', headers=headers['student']).status_code == 404
    for path in ['/admin/books/archived', '/admin/books?archive_view=true']:
        assert client.get(path, headers=headers['librarian']).status_code == 403
        assert client.get(path, headers=headers['superadmin']).json()[0]['id'] == created['id']
    assert client.patch(f'/admin/books/{created["id"]}/restore', headers=headers['librarian']).status_code == 403
    assert client.put(f'/admin/books/{created["id"]}', headers=headers['librarian'], json={'is_archived': False}).status_code == 404


def test_archived_book_history_and_scanner_return_remain_available_but_checkout_blocked(inbox_api):
    client, headers, factory = inbox_api
    created = client.post('/admin/books', headers=headers['librarian'], json={
        'accession_no': 'RETURN-ARCHIVED', 'title': 'Retained loan history', 'author': 'Author', 'category': 'History'}).json()
    with factory() as db:
        patron = db.query(User).filter_by(username='student').one()
        book = db.get(Book, created['id'])
        book.available_copies = 0
        db.add(BorrowRecord(req_id='ARCHIVED-ACTIVE', user_id=patron.id, book_id=book.id,
            status=BorrowStatus.CHECKED_OUT, copy_accession_no='RETURN-ARCHIVED-C01',
            issue_date=datetime.utcnow() - timedelta(hours=1), due_date=datetime.utcnow() + timedelta(hours=23)))
        db.commit()
    assert client.delete(f'/admin/books/{created["id"]}', headers=headers['librarian']).status_code == 200
    history = client.get('/mobile/my-borrowings', headers=headers['student']).json()
    assert history[0]['book_title'] == 'Retained loan history'
    lookup = client.get('/admin/circulation/book/RETURN-ARCHIVED-C01', headers=headers['librarian']).json()
    assert lookup['found'] and lookup['current_borrower']['username'] == 'student'
    payload = {'mode': 'borrow', 'patron_id': 'student', 'accession_no': 'RETURN-ARCHIVED-C01'}
    blocked = client.post('/admin/circulation/execute', headers=headers['librarian'], json=payload).json()
    assert blocked['status'] == 'error' and 'archived' in blocked['message']
    payload['mode'] = 'return'
    returned = client.post('/admin/circulation/execute', headers=headers['librarian'], json=payload).json()
    assert returned['status'] == 'success'
    history = client.get('/mobile/my-borrowings', headers=headers['student']).json()
    assert history[0]['book_title'] == 'Retained loan history' and history[0]['status'] == 'RETURNED'
    assert client.get('/mobile/books', headers=headers['student']).json() == []
    with factory() as db:
        assert db.get(Book, created['id']).is_archived
        assert db.query(BorrowRecord).one().return_date is not None


def test_archived_acquisitions_announcements_and_search_scopes(inbox_api):
    client, headers, factory = inbox_api
    with factory() as db:
        patron = db.query(User).filter_by(username='student').one()
        db.add(AcquisitionSuggestion(acq_id='ACQ-ARCHIVE', user_id=patron.id, title='Rare title', author='Rare author', justification='For teaching'))
        db.commit()
    post = client.post('/admin/announcements', headers=headers['admin'], json={'title': 'Old announcement', 'body': 'Archived details'}).json()
    for kind, record in [('acquisitions', 'ACQ-ARCHIVE'), ('announcements', post['id'])]:
        assert client.put(f'/admin/archives/{kind}/{record}?reason=Completed', headers=headers['admin']).status_code == 200
        assert client.get(f'/admin/{kind}', headers=headers['admin']).json() == []
        assert client.get(f'/admin/{kind}?archive_view=true', headers=headers['admin']).status_code == 403
        assert len(client.get(f'/admin/{kind}?archive_view=true', headers=headers['superadmin']).json()) == 1
    found = client.get('/admin/deleted-archives?q=Rare&kind=acquisitions&limit=1', headers=headers['superadmin']).json()
    assert found['total'] == 1 and len(found['items']) == 1 and 'content' not in found['items'][0]
    assert client.get('/admin/deleted-archives?skip=1&limit=1', headers=headers['superadmin']).json()['total'] == 2
    assert client.get('/admin/deleted-archives?limit=101', headers=headers['superadmin']).status_code == 422


def test_signup_option_deactivation_retains_cascading_courses_without_credentials(inbox_api):
    client, headers, factory = inbox_api
    with factory() as db:
        department = SignupOption(kind='department', name='Archives department')
        db.add(department); db.flush()
        course = SignupOption(kind='course', name='Archive studies', department_id=department.id)
        db.add(course); db.commit(); department_id = department.id
        user = db.query(User).filter_by(username='superadmin').one()
        assert 'hashed_password' not in record_content(user)
    assert client.delete(f'/admin/signup-options/{department_id}', headers=headers['superadmin']).status_code == 204
    detail = _archive(client, headers, 'signup-options')
    assert detail['content']['option']['name'] == 'Archives department'
    assert detail['content']['courses'][0]['name'] == 'Archive studies'
    with factory() as db:
        assert not any(option.is_active for option in db.query(SignupOption).all())


@pytest.mark.parametrize('suffix', ['', '/messages'])
def test_mobile_stale_thread_snapshot_retains_unseen_reply(inbox_api, suffix):
    client, headers, factory = inbox_api
    thread_id, _ = start_conversation(client, headers, 'librarian')
    old = client.get(f'/mobile/messages/{thread_id}', headers=headers['student']).json()['thread']
    assert client.post(f'/admin/messages/{thread_id}/reply', headers=headers['librarian'], json={'message': 'Unseen staff reply'}).status_code == 201
    response = client.delete(f'/mobile/messages/{thread_id}{suffix}', headers=headers['student'], params={
        'expected_created_at': old['created_at'], 'expected_updated_at': old['updated_at']})
    assert response.status_code == 409
    with factory() as db:
        assert db.query(SupportMessage).count() == 2
        assert db.query(DeletedArchive).count() == 0
