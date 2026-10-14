from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import User, UserRole, Notification
from routers import mobile_router, admin_router
from schemas import SupportThreadCreate, SupportMessageCreate, AssistantRequest


def test_recipient_isolation_and_greeting():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        users = [User(username=role.value, email=role.value+'@example.com', full_name=role.value,
                      hashed_password='unused', role=role, is_active=True) for role in
                 (UserRole.STUDENT, UserRole.LIBRARIAN, UserRole.ADMIN, UserRole.SUPERADMIN)]
        db.add_all(users)
        db.commit()
        patron, librarian, admin, superadmin = users
        with patch.object(mobile_router, 'enforce_rate_limit'):
            for recipient, allowed, denied in [('librarian', librarian, admin), ('admin', admin, librarian)]:
                result = mobile_router.start_message_thread(SupportThreadCreate(subject='Question', message='Help', recipient_role=recipient), db, patron)
                tid = result['id']
                assert result['recipient_role'] == recipient
                assert tid in [t['id'] for t in admin_router.list_support_inbox(db, allowed)]
                assert tid not in [t['id'] for t in admin_router.list_support_inbox(db, denied)]
                for call in [lambda: admin_router.read_support_thread(tid, db, denied),
                             lambda: admin_router.reply_to_support_thread(tid, SupportMessageCreate(message='Reply'), db, denied)]:
                    with pytest.raises(HTTPException) as error:
                        call()
                    assert error.value.status_code == 404
                admin_router.read_support_thread(tid, db, allowed)
                mobile_router.reply_to_staff_thread(tid, SupportMessageCreate(message='Follow up'), db, patron)
            assert len(admin_router.list_support_inbox(db, superadmin)) == 1
            assert db.query(Notification).filter_by(user_id=librarian.id).count() == 2
            assert db.query(Notification).filter_by(user_id=admin.id).count() == 2
            with patch.object(mobile_router, 'answer_library_question', return_value=None):
                for greeting in ['hello', 'hi', 'Hello!']:
                    answer = mobile_router.ask_library_assistant(AssistantRequest(message=greeting), db, patron)
                    assert answer['reply'].startswith('Hello!')
                    assert answer['source'] == 'local'
    engine.dispose()
