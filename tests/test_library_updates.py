from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import (User, UserRole, Book, BorrowRecord, BorrowStatus, AcquisitionSuggestion,
                    LibraryEmail, Notification, SupportThread, AuditLog)
from routers import admin_router
from schemas import AnnouncementCreate, AcquisitionStatusUpdate, LoanExtensionRequest, SupportMessageCreate
from library_notifications import queue_due_reminders, deliver_emails


@pytest.fixture
def records():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        staff = User(username='staff', email='staff@example.test', full_name='Librarian', role=UserRole.LIBRARIAN, hashed_password='unused')
        reader = User(username='reader', email='reader@example.test', full_name='Reader', role=UserRole.STUDENT, hashed_password='unused')
        inactive = User(username='inactive', email='inactive@example.test', full_name='Inactive Reader', role=UserRole.STUDENT, is_active=False, hashed_password='unused')
        book = Book(accession_no='BOOK-1', title='Programming', author='Author', category='IT')
        db.add_all([staff, reader, inactive, book])
        db.flush()
        loan = BorrowRecord(req_id='REQ-1', user_id=reader.id, book_id=book.id, status=BorrowStatus.CHECKED_OUT,
                            due_date=datetime.utcnow() + timedelta(hours=12), issue_date=datetime.utcnow())
        suggestion = AcquisitionSuggestion(acq_id='ACQ-1', user_id=reader.id, title='New book', author='Author', justification='For classes')
        thread = SupportThread(user_id=reader.id, recipient_role='librarian', subject='Loan question')
        db.add_all([loan, suggestion, thread]); db.commit()
        yield db, staff, reader, loan, suggestion, thread
    engine.dispose()


def test_publish_and_reply_queue_app_and_email_notifications(records):
    db, staff, reader, loan, suggestion, thread = records
    post = admin_router.publish_library_announcement(AnnouncementCreate(title='Library event', body='Join us this Friday.'), db, staff)
    assert post.title == 'Library event'
    assert db.query(Notification).count() == 1
    email = db.query(LibraryEmail).one()
    assert email.recipient == reader.email and 'Friday' in email.body
    admin_router.reply_to_support_thread(thread.id, SupportMessageCreate(message='We can discuss an extension.'), db, staff)
    assert db.query(Notification).count() == 2
    assert db.query(LibraryEmail).count() == 2
    with pytest.raises(HTTPException) as error:
        admin_router.publish_library_announcement(AnnouncementCreate(title='   ', body=' '), db, staff)
    assert error.value.status_code == 422


def test_acquisition_review_is_final_and_notifies_once(records):
    db, staff, reader, loan, suggestion, thread = records
    admin_router.update_acquisition(suggestion.acq_id, AcquisitionStatusUpdate(status='Approved for Purchase'), db, staff)
    assert db.query(LibraryEmail).count() == db.query(Notification).count() == 1
    with pytest.raises(HTTPException) as error:
        admin_router.update_acquisition(suggestion.acq_id, AcquisitionStatusUpdate(status='Declined'), db, staff)
    assert error.value.status_code == 409
    assert db.query(Notification).count() == 1
    assert db.query(AcquisitionSuggestion).one().status == 'Approved for Purchase'


def test_extension_validates_updates_due_and_reschedules_reminder(records):
    db, staff, reader, loan, suggestion, thread = records
    now = datetime.utcnow()
    queue_due_reminders(db, now)
    queue_due_reminders(db, now)
    assert db.query(Notification).count() == 1
    old_due = loan.due_date
    new_due = (old_due + timedelta(days=2)).replace(tzinfo=timezone.utc)
    admin_router.extend_loan(loan.req_id, LoanExtensionRequest(due_date=new_due, reason='Reader needs additional study time.'), db, staff)
    assert loan.due_date == new_due.replace(tzinfo=None)
    assert db.query(AuditLog).one().status == 'Extended'
    queue_due_reminders(db, now)
    assert db.query(Notification).filter(Notification.user_id == reader.id).count() == 2
    assert db.query(Notification).filter(Notification.user_id == staff.id, Notification.title == 'Loan period extended').count() == 1
    queue_due_reminders(db, loan.due_date - timedelta(hours=12))
    assert db.query(Notification).filter(Notification.user_id == reader.id).count() == 3
    with pytest.raises(HTTPException) as error:
        admin_router.extend_loan(loan.req_id, LoanExtensionRequest(due_date=new_due, reason='Another request'), db, staff)
    assert error.value.status_code == 422
    loan.status = BorrowStatus.RETURNED; db.commit()
    with pytest.raises(HTTPException) as error:
        admin_router.extend_loan(loan.req_id, LoanExtensionRequest(due_date=new_due + timedelta(days=1), reason='Another request'), db, staff)
    assert error.value.status_code == 409


def test_delivery_failure_retries_without_repeating_app_notification(records):
    db, staff, reader, loan, suggestion, thread = records
    now = datetime.utcnow()
    queue_due_reminders(db, now)
    with patch('library_notifications.send_email', side_effect=OSError('SMTP offline')) as sender:
        deliver_emails(db, now + timedelta(seconds=1))
        deliver_emails(db, now + timedelta(seconds=2))
        assert sender.call_count == 1
    email = db.query(LibraryEmail).one()
    assert email.status == 'failed'
    with patch('library_notifications.send_email') as sender:
        deliver_emails(db, now + timedelta(minutes=3))
        deliver_emails(db, now + timedelta(minutes=4))
        assert sender.call_count == 1
    assert email.status == 'sent' and email.attempts == 2
    assert db.query(Notification).count() == 1


def test_queue_filters_and_sorts_before_pagination(records):
    db, staff, reader, loan, suggestion, thread = records
    db.add(BorrowRecord(req_id='REQ-PENDING', user_id=reader.id, book_id=loan.book_id,
                       status=BorrowStatus.PENDING, request_date=datetime.utcnow() - timedelta(days=1)))
    db.commit()
    rows = admin_router.get_all_borrow_requests(db, staff, BorrowStatus.PENDING, 'Programming', 'oldest', 0, 1)
    assert [row.req_id for row in rows] == ['REQ-PENDING']
    assert rows[0].book_title == 'Programming'
    rows = admin_router.get_all_borrow_requests(db, staff, None, '', 'oldest', 1, 1)
    assert [row.req_id for row in rows] == ['REQ-1']
