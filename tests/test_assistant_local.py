from assistant_local import catalog_terms, conversational_reply, response_language


def test_screenshot_greeting_and_mixed_language_request():
    assert conversational_reply('maaung gabie', [], 'auto', []).startswith('Maayong gabii!')
    assert 'information technology' in catalog_terms('need nakog book para IT')
    assert 'programming' in catalog_terms('need nakog book para IT')
    assert 'Aniay' in conversational_reply('need nakog book para IT', [], 'auto', [{'title': 'Programming'}])


def test_language_preference_and_greeting_history():
    history = [{'role': 'user', 'content': 'need nakog book para IT'}]
    assert response_language('hi', history) == 'cebuano'
    assert conversational_reply('hi', history, 'Filipino', []).startswith('Kumusta!')
    assert conversational_reply('Maupay nga aga', [], 'auto', []).startswith('Maupay')


def test_unknown_question_does_not_become_failed_catalog_search():
    assert conversational_reply('How do I renew my membership?', [], 'auto', []) is None
    assert 'couldn\'t find' in conversational_reply('Find books on biology', [], 'English', [])


def test_mixed_language_search_uses_live_catalog():
    from types import SimpleNamespace
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from database import Base
    from models import Book
    from routers import mobile_router
    from schemas import AssistantRequest

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Book(accession_no='IT-1', title='Introduction to Programming',
                    author='Test Author', category='Information Technology'))
        db.commit()
        with patch.object(mobile_router, 'enforce_rate_limit'), patch.object(mobile_router, 'answer_library_question', return_value=None):
            result = mobile_router.ask_library_assistant(
                AssistantRequest(message='need nakog book para IT'), db, SimpleNamespace(id=1))
        assert result['source'] == 'local'
        assert result['books'][0]['title'] == 'Introduction to Programming'
        assert result['reply'].startswith('Aniay')
    engine.dispose()
