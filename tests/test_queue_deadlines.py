from datetime import datetime, timedelta
from models import BorrowRecord, BorrowStatus, User
from schemas import BookCreate
import crud
from test_message_deletion import inbox_api

def test_queue_refreshes_overdue_before_filtering(inbox_api):
    client, headers, factory = inbox_api
    with factory() as db:
        book = crud.create_book(db, BookCreate(title='Deadline test', author='Author', category='Test', total_copies=4))
        user = db.query(User).filter(User.username == 'student').one()
        for name, status, due in [('late', BorrowStatus.CHECKED_OUT, datetime.utcnow()-timedelta(days=1)), ('future', BorrowStatus.CHECKED_OUT, datetime.utcnow()+timedelta(days=1)), ('missing', BorrowStatus.CHECKED_OUT, None), ('returned', BorrowStatus.RETURNED, datetime.utcnow()-timedelta(days=1))]:
            db.add(BorrowRecord(req_id='deadline-'+name, user_id=user.id, book_id=book.id, status=status, due_date=due))
        db.commit()
    response = client.get('/admin/borrow-requests?status_filter=OVERDUE', headers=headers['librarian'])
    assert response.status_code == 200
    assert [r['req_id'] for r in response.json()] == ['deadline-late']
    rows = client.get('/admin/borrow-requests', headers=headers['superadmin']).json()
    records = {r['req_id']: r for r in rows}
    assert records['deadline-future']['status'] == 'CHECKED_OUT'
    assert records['deadline-missing']['due_date'] is None
    assert records['deadline-returned']['status'] == 'RETURNED'
