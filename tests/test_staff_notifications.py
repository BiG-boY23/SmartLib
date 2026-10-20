"""Staff activity fanout, transactional delivery, access and read isolation."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
from database import Base, get_db
from models import Book, BorrowRecord, BorrowStatus, Notification, User, UserRole, UserSession
from routers import admin_router, mobile_router, staff_notification_router
from security import create_access_token
from staff_activity import StaffActivityEvent, notify_staff_activity


@pytest.fixture
def activity_api(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "activity.db"}', connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    for router in (admin_router.router, mobile_router.router, staff_notification_router.router):
        app.include_router(router)
    def session():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = session
    tokens, ids = {}, {}
    with factory() as db:
        for name, role, active in [(role.value, role, True) for role in UserRole] + [('inactive', UserRole.ADMIN, False)]:
            user = User(username=name, email=f'{name}@evsu.edu.ph', hashed_password='unused',
                        full_name=f'{name.title()} Person', role=role, is_active=active, email_verified=True)
            db.add(user); db.flush(); ids[name] = user.id
            now = datetime.utcnow(); sid = f'activity-{name}'
            db.add(UserSession(session_id=sid, user_id=user.id, created_at=now,
                               last_activity_at=now, expires_at=now + timedelta(hours=1)))
            tokens[name] = {'Authorization': 'Bearer ' + create_access_token({'sub': name, 'sid': sid})}
        db.add(Book(accession_no='BOOK-1', title='Real circulation book', author='Library Author',
                    category='Reference', total_copies=1, available_copies=1))
        db.commit()
    with TestClient(app) as client:
        yield client, tokens, factory, ids
    engine.dispose()


def staff_counts(factory):
    with factory() as db:
        return {user.username: db.query(Notification).filter(Notification.user_id == user.id).count()
                for user in db.query(User).all()}


def test_helper_fans_out_to_all_active_staff_once_and_rolls_back(activity_api):
    _, _, factory, ids = activity_api
    with factory() as db:
        notify_staff_activity(db, 'Event', 'Details', event_key='test:1')
        assert notify_staff_activity(db, 'Event', 'Details', event_key='test:1') == []
        db.commit()
    counts = staff_counts(factory)
    assert {key for key, value in counts.items() if value} == {'superadmin', 'admin', 'librarian'}
    assert all(counts[name] == 1 for name in ('superadmin', 'admin', 'librarian'))
    with factory() as db:
        notify_staff_activity(db, 'Rolled back', 'Details', event_key='test:rollback')
        db.query(Book).one().available_copies = 0
        db.rollback()
    assert staff_counts(factory) == counts
    with factory() as db:
        assert db.get(StaffActivityEvent, 'test:rollback') is None
        assert db.query(Book).one().available_copies == 1


def test_borrow_submission_and_cancellation_notify_both_staff_roles(activity_api):
    client, tokens, factory, _ = activity_api
    result = client.post('/mobile/borrow', headers=tokens['student'], json={'accession_no': 'BOOK-1', 'pickup_date': '2026-10-21'})
    assert result.status_code == 201, result.text
    record_id = result.json()['id']
    for name in ('admin', 'superadmin', 'librarian'):
        data = client.get('/admin/notifications', headers=tokens[name]).json()
        assert data['unread_count'] == 1
        assert data['items'][0]['title'] == 'New borrow request'
        assert 'Student Person' in data['items'][0]['body'] and 'Real circulation book' in data['items'][0]['body']
    assert client.post(f'/mobile/borrow/{record_id}/cancel', headers=tokens['student']).status_code == 200
    counts = staff_counts(factory)
    assert counts['admin'] == counts['superadmin'] == counts['librarian'] == 2
    assert client.post(f'/mobile/borrow/{record_id}/cancel', headers=tokens['student']).status_code == 400
    assert staff_counts(factory) == counts


def test_failed_request_and_suggestion_submission(activity_api):
    client, tokens, factory, _ = activity_api
    assert client.post('/mobile/borrow', headers=tokens['student'], json={'accession_no':'MISSING', 'pickup_date':'2026-10-21'}).status_code == 400
    assert not any(staff_counts(factory).values())
    result = client.post('/mobile/acquisitions', headers=tokens['faculty'], json={
        'title':'Useful new title', 'author':'New Author', 'justification':'Needed for the research collection',
    })
    assert result.status_code == 201, result.text
    counts = staff_counts(factory)
    assert counts['admin'] == counts['superadmin'] == counts['librarian'] == 1
    assert counts['student'] == counts['faculty'] == counts['inactive'] == 0


@pytest.mark.parametrize('mode', ['borrow', 'borrow_clear'])
def test_desk_checkout_return_and_repeated_failed_return(activity_api, mode):
    client, tokens, factory, _ = activity_api
    payload = {'mode': mode, 'patron_id':'student', 'accession_no':'BOOK-1'}
    assert client.post('/admin/circulation/execute', headers=tokens['librarian'], json=payload).json()['status'] == 'success'
    counts = staff_counts(factory)
    assert counts['admin'] == counts['superadmin'] == counts['librarian'] == 1
    assert client.post('/admin/circulation/execute', headers=tokens['librarian'], json=payload).json()['status'] != 'success'
    assert staff_counts(factory) == counts
    payload['mode'] = 'return'
    assert client.post('/admin/circulation/execute', headers=tokens['admin'], json=payload).json()['status'] == 'success'
    counts = staff_counts(factory)
    assert counts['admin'] == counts['superadmin'] == counts['librarian'] == 2
    assert client.post('/admin/circulation/execute', headers=tokens['admin'], json=payload).json()['status'] == 'error'
    assert staff_counts(factory) == counts


def test_online_checkout_return_status_transitions_and_retry(activity_api):
    client, tokens, factory, ids = activity_api
    with factory() as db:
        book = db.query(Book).one()
        db.add(BorrowRecord(req_id='APPROVED-1', user_id=ids['student'], book_id=book.id, status=BorrowStatus.APPROVED))
        db.commit()
    for state in ('CHECKED_OUT', 'RETURNED'):
        result = client.put('/admin/borrow-requests/APPROVED-1/status', headers=tokens['admin'], json={'status':state})
        assert result.status_code == 200, result.text
        counts = staff_counts(factory)
        assert client.put('/admin/borrow-requests/APPROVED-1/status', headers=tokens['admin'], json={'status':state}).status_code == 409
        assert staff_counts(factory) == counts
    assert counts['admin'] == counts['superadmin'] == counts['librarian'] == 2


def test_failed_circulation_commit_leaves_no_phantom_notification_then_retry_succeeds(activity_api):
    from sqlalchemy import event
    _, _, factory, _ = activity_api
    with factory() as db:
        def fail_commit(_):
            raise RuntimeError('Simulated storage failure')
        event.listen(db, 'before_commit', fail_commit, once=True)
        with pytest.raises(RuntimeError, match='storage failure'):
            crud.execute_circulation(db, 'borrow', 'student', 'BOOK-1')
        db.rollback()
    with factory() as db:
        assert db.query(Book).one().available_copies == 1
        assert db.query(BorrowRecord).count() == db.query(Notification).count() == 0
        assert db.query(StaffActivityEvent).count() == 0
        assert crud.execute_circulation(db, 'borrow', 'student', 'BOOK-1')['status'] == 'success'
    counts = staff_counts(factory)
    assert counts['admin'] == counts['superadmin'] == counts['librarian'] == 1


def test_archived_book_cannot_be_requested_or_generate_staff_activity(activity_api):
    client, tokens, factory, _ = activity_api
    with factory() as db:
        db.query(Book).one().is_archived = True
        db.commit()
    result = client.post('/mobile/borrow', headers=tokens['student'], json={'accession_no':'BOOK-1', 'pickup_date':'2026-10-21'})
    assert result.status_code == 400
    assert not any(staff_counts(factory).values())


@pytest.mark.parametrize('role', ['student', 'faculty', 'inactive'])
def test_staff_endpoints_reject_nonstaff_and_inactive(activity_api, role):
    client, tokens, _, _ = activity_api
    for method, path in [('get','/admin/notifications'), ('post','/admin/notifications/read-all'), ('post','/admin/notifications/1/read')]:
        response = getattr(client, method)(path, headers=tokens[role])
        assert response.status_code in ({401, 403} if role == 'inactive' else {403})


def test_notification_ownership_and_independent_read_state(activity_api):
    client, tokens, factory, ids = activity_api
    with factory() as db:
        notify_staff_activity(db, 'Shared event', '<script>untrusted</script>', event_key='personal:1')
        db.add(Notification(user_id=ids['student'], title='Private patron', body='Private', kind='library'))
        db.commit()
    by_role = {role: client.get('/admin/notifications', headers=tokens[role]).json() for role in ('admin','librarian','superadmin')}
    admin_id = by_role['admin']['items'][0]['id']
    assert client.post(f'/admin/notifications/{admin_id}/read', headers=tokens['librarian']).status_code == 404
    assert client.post(f'/admin/notifications/{admin_id}/read', headers=tokens['admin']).status_code == 200
    assert client.post(f'/admin/notifications/{admin_id}/read', headers=tokens['admin']).status_code == 200
    data = client.get('/admin/notifications', headers=tokens['admin'])
    assert data.headers['cache-control'] == 'no-store'
    assert data.json()['unread_count'] == 0 and data.json()['items'][0]['read_at']
    assert data.json()['items'][0]['read'] is True
    for role in ('librarian', 'superadmin'):
        assert client.get('/admin/notifications', headers=tokens[role]).json()['unread_count'] == 1
    assert client.post('/admin/notifications/read-all', headers=tokens['librarian']).json()['updated_count'] == 1
    assert client.get('/admin/notifications', headers=tokens['superadmin']).json()['unread_count'] == 1


def test_unread_count_covers_more_than_visible_page(activity_api):
    client, tokens, factory, ids = activity_api
    with factory() as db:
        db.add_all([Notification(user_id=ids['admin'], title=f'Event {index}', body='Details') for index in range(105)])
        db.commit()
    data = client.get('/admin/notifications?limit=2', headers=tokens['admin']).json()
    assert data['unread_count'] == 105 and len(data['items']) == 2
    assert data['items'][0]['id'] > data['items'][1]['id']
    assert client.post('/admin/notifications/read-all', headers=tokens['admin']).json()['updated_count'] == 105
    assert client.get('/admin/notifications', headers=tokens['admin']).json()['unread_count'] == 0
    assert client.get('/admin/notifications?limit=101', headers=tokens['admin']).status_code == 422
    assert client.get('/admin/notifications').status_code == 401
