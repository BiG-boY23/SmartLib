"""Exercise report totals, real download formats, staff access and password revocation."""
import csv
import io
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from models import User, UserRole, UserSession, Book, BorrowRecord, BorrowStatus
from routers import admin_router, auth_router, mobile_router, report_router
from main import submit_public_feedback
from security import create_access_token, get_password_hash
import crud


@pytest.fixture
def api(tmp_path):
    engine=create_engine(f'sqlite:///{tmp_path / "report-tests.db"}',connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine)
    app=FastAPI()
    for router in (admin_router,auth_router,mobile_router,report_router): app.include_router(router.router)
    app.post('/public/feedback',status_code=201)(submit_public_feedback)
    def db_session():
        with factory() as db: yield db
    app.dependency_overrides[get_db]=db_session
    tokens={}
    password_hash=get_password_hash('Oldpass1!')
    with factory() as db:
        people=[('staff',UserRole.LIBRARIAN,'Library',''),('admin',UserRole.SUPERADMIN,'Library',''),
                ('A',UserRole.STUDENT,'Computers','BSIT'),('B',UserRole.STUDENT,'Education','BPEd'),('F',UserRole.FACULTY,'Computers','BSIT')]
        users={}
        for key,role,department,course in people:
            user=User(username=key,full_name='=Formula name' if key=='B' else f'Person {key}',email=f'{key.lower()}@evsu.edu.ph',
                role=role,department=department,course=course,hashed_password=password_hash,is_active=True,email_verified=True)
            db.add(user);db.flush();users[key]=user
            now=datetime.utcnow();sid=f'test-session-{key}'
            db.add(UserSession(session_id=sid,user_id=user.id,created_at=now,last_activity_at=now,expires_at=now+timedelta(hours=1)))
            tokens[key]={'Authorization':'Bearer '+create_access_token({'sub':key,'sid':sid,'role':role.value})}
        book=Book(accession_no='LIB-REPORT',title='Reporting reference',author='Library',category='Reference',total_copies=8,available_copies=4)
        db.add(book);db.flush()
        for ref,key,status,issued,returned in [
            ('R1','A',BorrowStatus.RETURNED,datetime(2026,10,9,16),datetime(2026,10,10,16,30)),
            ('R2','A',BorrowStatus.CHECKED_OUT,datetime(2026,10,10,2),None),
            ('R3','B',BorrowStatus.CHECKED_OUT,datetime(2026,10,11,1),None),
            ('R4','F',BorrowStatus.CHECKED_OUT,datetime(2026,10,10,2),None),
            ('R5','A',BorrowStatus.APPROVED,datetime(2026,10,10,2),None),
            ('R6','A',BorrowStatus.PENDING,None,None),
        ]:
            db.add(BorrowRecord(req_id=ref,user_id=users[key].id,book_id=book.id,status=status,issue_date=issued,return_date=returned))
        db.commit()
    yield TestClient(app),tokens
    engine.dispose()


URL='/admin/reports/circulation?start=2026-10-10&end=2026-10-11'


def test_report_counts_unique_people_by_local_day_and_filters(api):
    client,tokens=api
    report=client.get(URL,headers=tokens['staff']).json()
    assert report['summary']=={'borrowed_items':3,'returned_items':1,'unique_borrowers':2,'unique_returners':1}
    assert report['daily'][0]=={'date':'2026-10-10','borrowed_items':2,'returned_items':0,'borrowers':1,'returners':0}
    assert report['daily'][1]['returners']==1
    filtered=client.get(URL+'&department=Computers&course=BSIT&sort=course&direction=desc',headers=tokens['admin']).json()
    assert filtered['summary']['borrowed_items']==2
    assert {r['institutional_id'] for r in filtered['rows']}=={'A'}
    empty=client.get(URL+'&department=Missing',headers=tokens['staff']).json()
    assert empty['rows']==[] and empty['summary']['unique_borrowers']==0
    assert client.get(URL,headers=tokens['A']).status_code==403
    assert client.get(URL).status_code==401
    assert client.get('/admin/reports/circulation?start=2026-10-11&end=2026-10-10',headers=tokens['staff']).status_code==400


def test_dashboard_has_real_counts_and_zero_fines(api):
    client,tokens=api
    stats=client.get('/admin/dashboard/stats',headers=tokens['staff']).json()
    assert stats=={'total_books':8,'catalog_titles':1,'active_borrows':3,'pending_requests':1,'registered_patrons':3,'uncollected_fines':0.0}


def test_download_formats_and_id_access(api):
    client,tokens=api
    csv_response=client.get(URL+'&format=csv',headers=tokens['staff'])
    rows=list(csv.reader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
    assert ['2026-10-10', '1', '0', '2', '0'] in rows
    assert ['Unique Borrowers', '2'] in rows
    details = rows[rows.index(['Activity details']) + 2:]
    assert len(details)==4
    assert any(row[2]=="'=Formula name" for row in details)
    pdf=client.get(URL+'&format=pdf',headers=tokens['staff'])
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    doc=client.get(URL+'&format=docx',headers=tokens['staff'])
    assert doc.status_code==200
    with zipfile.ZipFile(io.BytesIO(doc.content)) as archive:
        assert 'word/media/image1.png' in archive.namelist()
        assert b'Person A' in archive.read('word/document.xml')
    assert client.get('/mobile/profile/digital-id/image').status_code==401
    png=client.get('/mobile/profile/digital-id/image',headers=tokens['A'])
    assert png.status_code==200 and png.headers['cache-control']=='no-store'
    assert Image.open(io.BytesIO(png.content)).size==(900,1400)


def test_feedback_visible_to_both_staff_roles(api):
    client,tokens=api
    response=client.post('/public/feedback',json={'name':'Visitor','email':'reader@evsu.edu.ph','category':'General Inquiry','message':'Please extend library hours.'})
    assert response.status_code==201
    for role in ('staff','admin'):
        feedback=client.get('/admin/feedbacks',headers=tokens[role])
        assert feedback.status_code==200 and feedback.json()[0]['message']=='Please extend library hours.'


def test_password_change_checks_confirmation_and_revokes_session(api):
    client,tokens=api
    url='/auth/change-password'
    body={'current_password':'Oldpass1!','new_password':'Newpass2!','confirm_new_password':'Mismatch3!'}
    assert client.post(url,json=body,headers=tokens['A']).status_code==400
    body['confirm_new_password']='Newpass2!';body['current_password']='Wrongpass1!'
    assert client.post(url,json=body,headers=tokens['A']).status_code==400
    body['current_password']='Oldpass1!'
    assert client.post(url,json=body,headers=tokens['A']).status_code==200
    assert client.get('/auth/me',headers=tokens['A']).status_code==401
    assert client.post('/auth/login',json={'username':'A','password':'Newpass2!'}).status_code==200


def test_one_day_loan_and_published_overdue_fine_policy(api):
    client,tokens=api
    session_generator = client.app.dependency_overrides[get_db]()
    session = next(session_generator)
    try:
        approved, message = crud.update_borrow_status(session, 'R6', BorrowStatus.APPROVED)
        assert message == 'Success' and approved.issue_date is None and approved.due_date is None
        record, message = crud.update_borrow_status(session, 'R1', BorrowStatus.CHECKED_OUT)
        assert message == 'Success'
        assert round((record.due_date - record.issue_date).total_seconds()) == 86400
        late_record = session.query(BorrowRecord).filter_by(req_id='R1').one()
        # Use a due date well beyond the daily cap so this assertion is not
        # sensitive to test runtime or timing at the hour boundary.
        late_record.due_date = datetime.utcnow() - timedelta(days=2)
        session.commit()
    finally:
        session.close()
        session_generator.close()
    due = datetime(2026, 10, 10, 9)
    assert crud.calculate_overdue_fine(due, due + timedelta(hours=1)) == 1.5
    assert crud.calculate_overdue_fine(due, due + timedelta(hours=6)) == 9.0
    assert crud.calculate_overdue_fine(due, due + timedelta(hours=7)) == 10.0
    assert crud.calculate_overdue_fine(due, due + timedelta(hours=25)) == 20.0
    returned = client.post('/admin/circulation/execute', json={
        'mode':'return', 'patron_id':'A', 'accession_no':'LIB-REPORT'}, headers=tokens['staff'])
    assert returned.status_code == 200 and returned.json()['fine'] == 30.0
    assert client.get('/admin/dashboard/stats', headers=tokens['admin']).json()['uncollected_fines'] == 30.0
