"""Shared staff inbox deletion, access boundaries, and patron-visible results."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from models import AuditLog, LibraryEmail, StaffArchive, SupportMessage, SupportThread, User, UserRole, UserSession
from routers import admin_router, mobile_router
from security import create_access_token


@pytest.fixture
def inbox_api(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "message-tests.db"}', connect_args={'check_same_thread': False})

    @event.listens_for(engine, 'connect')
    def enforce_foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(admin_router.router)
    app.include_router(mobile_router.router)

    def get_session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = get_session
    headers = {}
    with factory() as db:
        for role in (UserRole.LIBRARIAN, UserRole.ADMIN, UserRole.SUPERADMIN, UserRole.STUDENT):
            username = role.value.lower()
            user = User(username=username, full_name=f'{username} reader', email=f'{username}@evsu.edu.ph',
                        hashed_password='unused', role=role, is_active=True, email_verified=True)
            db.add(user)
            db.flush()
            now = datetime.utcnow()
            sid = f'deletion-test-{username}'
            db.add(UserSession(session_id=sid, user_id=user.id, created_at=now,
                               last_activity_at=now, expires_at=now + timedelta(hours=1)))
            headers[username] = {'Authorization': 'Bearer ' + create_access_token({'sub': username, 'sid': sid})}
        db.commit()
    with TestClient(app) as client:
        yield client, headers, factory
    engine.dispose()


def start_conversation(client, headers, role, subject='Library question', body='Original message'):
    response = client.post('/admin/messages', headers=headers[role], json={
        'recipient_username': 'student', 'subject': subject, 'message': body,
    })
    assert response.status_code == 201
    thread_id = response.json()['id']
    data = client.get(f'/admin/messages/{thread_id}', headers=headers[role]).json()
    return thread_id, data['messages'][0]['id']


@pytest.mark.parametrize('role', ['librarian', 'admin', 'superadmin'])
def test_delete_individual_message_updates_both_sides_and_preview(inbox_api, role):
    client, headers, factory = inbox_api
    thread_id, first_id = start_conversation(client, headers, role, body='Keep this message')
    assert client.post(f'/admin/messages/{thread_id}/reply', headers=headers[role], json={
        'message': 'Delete this secret content',
    }).status_code == 201
    before = client.get(f'/admin/messages/{thread_id}', headers=headers[role]).json()
    deleted_id = before['messages'][-1]['id']
    response = client.delete(f'/admin/messages/{thread_id}/messages/{deleted_id}', headers=headers[role])
    assert response.status_code == 200 and response.json()['deleted_messages'] == 1
    for path, account in [('admin', role), ('mobile', 'student')]:
        history = client.get(f'/{path}/messages/{thread_id}', headers=headers[account])
        assert [message['id'] for message in history.json()['messages']] == [first_id]
        assert 'Delete this secret content' not in history.text
        inbox = client.get(f'/{path}/messages', headers=headers[account]).json()
        assert inbox[0]['last_message'] == 'Keep this message'
    assert client.delete(f'/admin/messages/{thread_id}/messages/{deleted_id}', headers=headers[role]).status_code == 404
    with factory() as db:
        audit = db.query(AuditLog).one()
        assert audit.user_name == role and f'Deleted message {deleted_id}' in audit.event
        assert 'secret' not in audit.event
        email = db.query(LibraryEmail).one()
        assert email.status == 'cancelled' and email.body == ''


@pytest.mark.parametrize('role', ['librarian', 'admin'])
def test_clear_history_keeps_conversation_and_allows_fresh_reply(inbox_api, role):
    client, headers, factory = inbox_api
    thread_id, message_id = start_conversation(client, headers, role)
    url = f'/admin/messages/{thread_id}/messages'
    cleared = client.delete(url, headers=headers[role])
    assert cleared.status_code == 200 and cleared.json()['deleted_messages'] == 1
    data = client.get(f'/mobile/messages/{thread_id}', headers=headers['student']).json()
    assert data['messages'] == [] and data['thread']['id'] == thread_id
    assert data['thread']['last_message'] == '' and data['thread']['unread_count'] == 0
    with factory() as db:
        assert db.get(SupportMessage, message_id) is None
        assert db.get(StaffArchive, f'messages:{thread_id}') is None
    # Clearing an empty active thread is safe and never removes its subject.
    assert client.delete(url, headers=headers[role]).json()['deleted_messages'] == 0
    assert client.post(f'/admin/messages/{thread_id}/reply', headers=headers[role], json={'message': 'Fresh reply'}).status_code == 201
    history = client.get(f'/mobile/messages/{thread_id}', headers=headers['student']).json()['messages']
    assert [message['message'] for message in history] == ['Fresh reply']


@pytest.mark.parametrize('role', ['librarian', 'admin'])
def test_delete_entire_conversation_removes_history_and_archive(inbox_api, role):
    client, headers, factory = inbox_api
    thread_id, message_id = start_conversation(client, headers, role)
    survivor_id, survivor_message = start_conversation(client, headers, role, subject='Keep conversation')
    response = client.delete(f'/admin/messages/{thread_id}', headers=headers[role])
    assert response.status_code == 200 and response.json()['deleted_messages'] == 1
    for path, account in [('admin', role), ('mobile', 'student')]:
        assert client.get(f'/{path}/messages/{thread_id}', headers=headers[account]).status_code == 404
        assert [thread['id'] for thread in client.get(f'/{path}/messages', headers=headers[account]).json()] == [survivor_id]
    assert client.delete(f'/admin/messages/{thread_id}', headers=headers[role]).status_code == 404
    with factory() as db:
        assert db.get(SupportThread, thread_id) is None
        assert db.get(SupportMessage, message_id) is None
        assert db.get(StaffArchive, f'messages:{thread_id}') is None
        assert db.get(SupportMessage, survivor_message) is not None
        assert f'conversation {thread_id}' in db.query(AuditLog).one().event


@pytest.mark.parametrize('owner,denied', [('librarian', 'admin'), ('admin', 'librarian'), ('librarian', 'superadmin')])
def test_deletion_preserves_recipient_isolation(inbox_api, owner, denied):
    client, headers, factory = inbox_api
    thread_id, message_id = start_conversation(client, headers, owner)
    for suffix in ['', '/messages', f'/messages/{message_id}']:
        assert client.delete(f'/admin/messages/{thread_id}{suffix}', headers=headers[denied]).status_code == 404
    with factory() as db:
        assert db.get(SupportThread, thread_id) is not None
        assert db.get(SupportMessage, message_id) is not None
        assert db.query(AuditLog).count() == 0


def test_deletion_checks_thread_message_pair_and_staff_permissions(inbox_api):
    client, headers, factory = inbox_api
    first_id, first_message = start_conversation(client, headers, 'librarian')
    second_id, second_message = start_conversation(client, headers, 'librarian', subject='Other question')
    assert client.delete(f'/admin/messages/{first_id}/messages/{second_message}', headers=headers['librarian']).status_code == 404
    for suffix in ['', '/messages', f'/messages/{first_message}']:
        url = f'/admin/messages/{first_id}{suffix}'
        assert client.delete(url, headers=headers['student']).status_code == 403
        assert client.delete(url).status_code == 401
    with factory() as db:
        assert db.query(SupportThread).count() == 2
        assert db.query(SupportMessage).count() == 2
        assert db.query(AuditLog).count() == 0
    assert client.get(f'/admin/messages/{second_id}', headers=headers['librarian']).status_code == 200


def test_future_reply_email_is_queued_after_sqlite_reuses_deleted_message_id(inbox_api):
    client, headers, factory = inbox_api
    thread_id, _ = start_conversation(client, headers, 'librarian')
    url = f'/admin/messages/{thread_id}'
    assert client.post(url + '/reply', headers=headers['librarian'], json={'message': 'First reply'}).status_code == 201
    with factory() as db:
        original_email = db.query(LibraryEmail).one()
        original_email.status = 'sent'
        original_key = original_email.event_key
        db.commit()
    assert client.delete(url + '/messages', headers=headers['librarian']).status_code == 200
    assert client.post(url + '/reply', headers=headers['librarian'], json={'message': 'New reply'}).status_code == 201
    # Existing SQLite databases reuse numeric IDs after clearing the table.
    assert client.post(url + '/reply', headers=headers['librarian'], json={'message': 'Reused ID reply'}).status_code == 201
    with factory() as db:
        assert db.query(SupportMessage).filter_by(body='Reused ID reply').one().id == int(original_key.split(':')[1])
        emails = db.query(LibraryEmail).all()
        assert len(emails) == 3 and len({email.event_key for email in emails}) == 3
        assert sum(email.status == 'sent' for email in emails) == 1
        assert any('Reused ID reply' in email.body and email.status == 'pending' for email in emails)


def test_delete_cancels_legacy_and_new_unsent_email_but_preserves_sent_email(inbox_api):
    client, headers, factory = inbox_api
    thread_id, message_id = start_conversation(client, headers, 'librarian')
    with factory() as db:
        for key, state in [(f'reply:{message_id}', 'failed'), (f'reply:{message_id}:new', 'pending'),
                           (f'reply:{message_id}:sent', 'sent'), (f'reply:{message_id + 10}', 'pending')]:
            db.add(LibraryEmail(event_key=key, status=state, recipient='student@evsu.edu.ph',
                                subject='Library reply', body='Message content'))
        db.commit()
    assert client.delete(f'/admin/messages/{thread_id}/messages/{message_id}', headers=headers['librarian']).status_code == 200
    with factory() as db:
        emails = {email.event_key: email for email in db.query(LibraryEmail).all()}
        assert emails[f'reply:{message_id}'].status == 'cancelled'
        assert emails[f'reply:{message_id}:new'].body == ''
        assert emails[f'reply:{message_id}:sent'].status == 'sent'
        assert emails[f'reply:{message_id}:sent'].body == 'Message content'
        assert emails[f'reply:{message_id + 10}'].status == 'pending'


def test_stale_message_snapshot_cannot_delete_reused_id(inbox_api):
    client, headers, factory = inbox_api
    thread_id, message_id = start_conversation(client, headers, 'librarian')
    url = f'/admin/messages/{thread_id}'
    old = client.get(url, headers=headers['librarian']).json()
    assert client.delete(url + '/messages', headers=headers['librarian']).status_code == 200
    assert client.post(url + '/reply', headers=headers['librarian'], json={'message': 'Replacement message'}).status_code == 201
    current = client.get(url, headers=headers['librarian']).json()
    assert current['messages'][0]['id'] == message_id
    stale = {'expected_created_at': old['messages'][0]['created_at'],
             'expected_thread_created_at': old['thread']['created_at']}
    with factory() as db:
        original_updated = db.get(SupportThread, thread_id).updated_at
        audit_count = db.query(AuditLog).count()
    assert client.delete(url + f'/messages/{message_id}', params=stale, headers=headers['librarian']).status_code == 409
    with factory() as db:
        assert db.get(SupportMessage, message_id).body == 'Replacement message'
        assert db.get(SupportThread, thread_id).updated_at == original_updated
        assert db.query(AuditLog).count() == audit_count
    # Offset-bearing timestamps compare as UTC, so a valid refreshed snapshot works.
    stamp = datetime.fromisoformat(current['messages'][0]['created_at']).replace(tzinfo=timezone.utc)
    stale['expected_created_at'] = stamp.astimezone(timezone(timedelta(hours=8))).isoformat()
    assert client.delete(url + f'/messages/{message_id}', params=stale, headers=headers['librarian']).status_code == 200


@pytest.mark.parametrize('suffix', ['', '/messages'])
def test_stale_thread_snapshot_cannot_clear_or_delete_replacement_conversation(inbox_api, suffix):
    client, headers, factory = inbox_api
    thread_id, _ = start_conversation(client, headers, 'librarian')
    url = f'/admin/messages/{thread_id}'
    old = client.get(url, headers=headers['librarian']).json()['thread']
    assert client.delete(url, headers=headers['librarian']).status_code == 200
    replacement_id, replacement_message = start_conversation(client, headers, 'librarian', body='New conversation')
    assert replacement_id == thread_id
    stale = {'expected_created_at': old['created_at'], 'expected_updated_at': old['updated_at']}
    response = client.delete(url + suffix, params=stale, headers=headers['librarian'])
    assert response.status_code == 409
    with factory() as db:
        assert db.get(SupportThread, replacement_id) is not None
        assert db.get(SupportMessage, replacement_message).body == 'New conversation'
        assert db.query(AuditLog).count() == 1


@pytest.mark.parametrize('suffix', ['', '/messages'])
def test_clear_and_delete_require_refreshed_snapshot_after_unseen_reply(inbox_api, suffix):
    client, headers, factory = inbox_api
    thread_id, _ = start_conversation(client, headers, 'librarian')
    url = f'/admin/messages/{thread_id}'
    old = client.get(url, headers=headers['librarian']).json()['thread']
    assert client.post(url + '/reply', headers=headers['librarian'], json={'message': 'Unseen new reply'}).status_code == 201
    stale = {'expected_created_at': old['created_at'], 'expected_updated_at': old['updated_at']}
    assert client.delete(url + suffix, params=stale, headers=headers['librarian']).status_code == 409
    with factory() as db:
        assert db.query(SupportMessage).filter_by(thread_id=thread_id).count() == 2
        assert db.query(AuditLog).count() == 0
    current = client.get(url, headers=headers['librarian']).json()['thread']
    fresh = {'expected_created_at': current['created_at'], 'expected_updated_at': current['updated_at']}
    response = client.delete(url + suffix, params=fresh, headers=headers['librarian'])
    assert response.status_code == 200 and response.json()['deleted_messages'] == 2
