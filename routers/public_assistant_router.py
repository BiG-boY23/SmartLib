"""Public library assistant: no private patron/account data."""
import json
from pathlib import Path
import string

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ai_service import answer_library_question
from assistant_local import catalog_terms, conversational_reply
from database import get_db
from models import Book
from rate_limits import enforce_rate_limit
from schemas import AssistantRequest

router = APIRouter(prefix='/public', tags=['Public Portal'])
GUIDE = json.loads((Path(__file__).resolve().parent.parent / 'library_guide.json').read_text(encoding='utf-8'))


@router.post('/assistant')
def ask_public_assistant(req: AssistantRequest, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit('public-assistant:' + (request.client.host if request.client else 'unknown'), 20, 3600)
    question = req.message.strip()
    if len(question) < 2:
        raise HTTPException(status_code=422, detail='Enter a library question.')
    terms = catalog_terms(question)
    found = {}
    for term in terms:
        for book in db.query(Book).filter(Book.is_archived.is_(False),
                (Book.title.ilike('%'+term+'%') | Book.author.ilike('%'+term+'%') | Book.category.ilike('%'+term+'%'))).limit(5):
            found[book.id] = book
    books = [{'id': b.id, 'title': b.title, 'author': b.author, 'category': b.category,
              'available_copies': b.available_copies} for b in list(found.values())[:5]]
    answer = answer_library_question(question, [turn.model_dump() for turn in req.history],
        {'audience': 'public visitor; personal records unavailable, sign in to check borrowing status',
         'library_guide': GUIDE, 'matching_books': books,
         'catalog_note': 'Keyword matches only; no matches does not establish that the library lacks the book. Try its original title or author.'}, language=req.language)
    if answer:
        return {'reply': answer, 'source': 'model', 'books': books}
    lower = question.casefold().strip(string.punctuation + ' ')
    conversation = conversational_reply(question, [turn.model_dump() for turn in req.history], req.language, books)
    if conversation:
        return {'reply': conversation, 'source': 'local', 'books': books}
    else:
        best = max(GUIDE, key=lambda entry: sum(len(k) for k in entry['keywords'] if k in lower))
        answer = best['answer'] if any(k in lower for k in best['keywords']) else 'Please contact library staff for help with this question.'
    return {'reply': 'AI is temporarily unavailable; this local library guide currently answers in English.\n\n'+answer,
            'source': 'local', 'books': books}
