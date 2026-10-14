import json
from unittest.mock import patch
from io import BytesIO

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import ai_service
from database import Base, get_db
from routers import public_assistant_router as public


def test_multilingual_model_payload(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    response = BytesIO(json.dumps({'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Kumusta!'}]}]}).encode())
    with patch.object(ai_service, 'urlopen', return_value=response) as provider:
        assert ai_service.answer_library_question('Kumusta', [], {}, language='Filipino') == 'Kumusta!'
        payload = json.loads(provider.call_args.args[0].data)
        assert 'Waray' in payload['instructions']
        assert 'Requested response language: Filipino' in payload['instructions']
        assert payload['store'] is False


def test_public_assistant_model_fallback_validation_and_rate_limit():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(public.router)
    def session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    client = TestClient(app)
    with patch.object(public, 'enforce_rate_limit') as limit, patch.object(public, 'answer_library_question', return_value='Maupay!') as model:
        response = client.post('/public/assistant', json={'message': 'Maupay nga aga', 'history': [{'role':'user', 'content':'Waray please'}]})
        assert response.status_code == 200
        assert response.json()['source'] == 'model'
        context = model.call_args.args[2]
        assert 'recent_borrowing' not in context
        assert len(context['library_guide']) == 10
        assert limit.call_args.args[1:] == (20, 3600)
        model.return_value = None
        result = client.post('/public/assistant', json={'message': 'Library hours'}).json()
        assert result['source'] == 'local'
        assert '7:00 AM' in result['reply']
        assert client.post('/public/assistant', json={'message': '  '}).status_code == 422
        assert client.post('/public/assistant', json={'message': 'x'*501}).status_code == 422
    engine.dispose()
