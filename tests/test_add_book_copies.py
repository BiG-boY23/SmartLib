import pytest
import crud
from models import BookCopy, AuditLog
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
