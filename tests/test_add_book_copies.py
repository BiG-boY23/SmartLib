from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import crud
from models import Book, BookCopy, AuditLog, IdempotencyRecord
from schemas import BookCreate
from test_message_deletion import inbox_api

@pytest.mark.parametrize('role', ['librarian', 'superadmin'])
def test_add_copies_preserves_existing_stock(inbox_api, role):
    client, headers, factory = inbox_api
    with factory() as db:
        book = crud.create_book(db, BookCreate(title='RAD', author='Author', category='Computing', total_copies=5))
        book.available_copies = 3
        db.commit()
        book_id = book.id
        original = [c.accession_no for c in crud.get_book_copies(db, book_id)]
    response = client.post(f'/admin/books/{book_id}/copies', headers=headers[role], json={'quantity': 5})
    assert response.status_code == 201
    assert response.json()['total_copies'] == 10
    assert response.json()['available_copies'] == 8
    with factory() as db:
        copies = crud.get_book_copies(db, book_id)
        assert [c.accession_no for c in copies[:5]] == original
        assert len(copies) == len({c.accession_no for c in copies}) == 10
        assert all(c.accession_no.isdigit() and c.qr_printed_at is None for c in copies[5:])
        assert db.query(AuditLog).filter(AuditLog.user_name == role, AuditLog.event.like('Added 5 copies%')).count() == 1
    for invalid in [0, -1, 501, 1.5, '5']:
        assert client.post(f'/admin/books/{book_id}/copies', headers=headers[role], json={'quantity': invalid}).status_code == 422
    assert client.post(f'/admin/books/{book_id}/copies', headers=headers['student'], json={'quantity': 1}).status_code == 403


@pytest.mark.parametrize('role', ['librarian', 'superadmin'])
def test_create_book_retry_with_same_key_returns_original_book(inbox_api, role):
    client, headers, factory = inbox_api
    request_headers = {**headers[role], 'Idempotency-Key': 'catalog-retry-key-001'}
    payload = {
        'accession_no': '0001-0001', 'title': 'RAD', 'author': 'Author',
        'category': 'Computing', 'total_copies': 3, 'available_copies': 3,
    }

    first = client.post('/admin/books', headers=request_headers, json=payload)
    retry = client.post('/admin/books', headers=request_headers, json=payload)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    with factory() as db:
        assert db.query(Book).count() == 1
        assert db.query(BookCopy).count() == 3
        assert db.query(IdempotencyRecord).count() == 1
        assert db.query(AuditLog).filter(AuditLog.event.like('Cataloged Book%')).count() == 1


def test_create_book_idempotency_key_cannot_be_reused_for_different_details(inbox_api):
    client, headers, factory = inbox_api
    request_headers = {**headers['librarian'], 'Idempotency-Key': 'catalog-retry-key-002'}
    payload = {'title': 'RAD', 'author': 'Author', 'category': 'Computing', 'total_copies': 2}
    assert client.post('/admin/books', headers=request_headers, json=payload).status_code == 201

    changed = {**payload, 'title': 'Different title'}
    response = client.post('/admin/books', headers=request_headers, json=changed)

    assert response.status_code == 409
    assert 'different book details' in response.json()['detail']
    with factory() as db:
        assert db.query(Book).count() == 1
        assert db.query(BookCopy).count() == 2


def test_simultaneous_create_book_submissions_share_one_result(inbox_api):
    client, headers, factory = inbox_api
    request_headers = {**headers['librarian'], 'Idempotency-Key': 'catalog-race-key-001'}
    payload = {'title': 'Concurrent RAD', 'author': 'Author', 'category': 'Computing', 'total_copies': 2}
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        return client.post('/admin/books', headers=request_headers, json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [future.result(timeout=30) for future in (pool.submit(submit), pool.submit(submit))]

    assert first.status_code == second.status_code == 201
    assert first.json()['id'] == second.json()['id']
    with factory() as db:
        assert db.query(Book).count() == 1
        assert db.query(BookCopy).count() == 2
