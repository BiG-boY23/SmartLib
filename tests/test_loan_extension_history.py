"""Approved extension events are durable, auditable, and independently reportable."""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from database import Base, get_db
from models import (AuditLog, Book, BorrowRecord, BorrowStatus, LoanExtensionEvent,
                    Notification, User, UserRole)
from reporting import circulation_report, loan_extension_report
from routers import admin_router, report_router
from schemas import LoanExtensionRequest
from security import get_current_user


@pytest.fixture
def history(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "extensions.db"}', connect_args={"check_same_thread":False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        users = {}
        for username, role in [("librarian",UserRole.LIBRARIAN),("admin",UserRole.ADMIN),
                               ("superadmin",UserRole.SUPERADMIN),("reader",UserRole.STUDENT),
                               ("faculty",UserRole.FACULTY)]:
            user = User(username=username, email=f"{username}@example.test", full_name=f"Name {username}",
                        role=role, department="Computing", course="BSIT", hashed_password="unused")
            db.add(user); db.flush(); users[username]=user
        book = Book(accession_no="EXT-BOOK", title="Extension reference", author="Library", category="Reference")
        db.add(book); db.flush()
        loan = BorrowRecord(req_id="REQ-EXT", user_id=users["reader"].id, book_id=book.id,
                            copy_accession_no="EXT-BOOK-C01", status=BorrowStatus.CHECKED_OUT,
                            issue_date=datetime.utcnow(), due_date=datetime.utcnow()+timedelta(days=1))
        db.add(loan); db.commit()
        yield db, users, loan, factory
    engine.dispose()


def make_event(loan, patron, actor, stamp, reference="REQ-HIST", department="Historic department", course="Historic course"):
    return LoanExtensionEvent(borrow_record_id=loan.id, patron_id=patron.id, book_id=loan.book_id, actor_id=actor.id,
        reference=reference, patron_name="Original patron", institutional_id=patron.username, patron_role=patron.role.value,
        department=department, course=course, book_title="Original book", accession_no="ORIGINAL-BOOK", copy_accession_no="ORIGINAL-BOOK-C01",
        actor_name="Original approver", actor_username=actor.username, actor_role=actor.role.value,
        old_due_date=datetime(2026,7,7,10), new_due_date=datetime(2026,7,9,10),
        reason="=Additional time for research", approved_at=stamp)


def test_extension_logs_complete_event_and_audit_in_successful_transaction(history):
    db, users, loan, _ = history
    old_due=loan.due_date
    new_due=(old_due+timedelta(days=2)).replace(tzinfo=timezone.utc)
    admin_router.extend_loan(loan.req_id, LoanExtensionRequest(due_date=new_due,reason="  Study time  "),db,users["librarian"])
    event=db.query(LoanExtensionEvent).one()
    assert (event.reference,event.old_due_date,event.new_due_date,event.reason)==(loan.req_id,old_due,new_due.replace(tzinfo=None),"Study time")
    assert (event.actor_id,event.actor_username,event.actor_role)==(users["librarian"].id,"librarian","librarian")
    assert (event.patron_name,event.institutional_id,event.book_title,event.copy_accession_no)==("Name reader","reader","Extension reference","EXT-BOOK-C01")
    notifications=db.query(Notification).filter(Notification.title=="Loan period extended").all()
    assert {notification.user_id for notification in notifications}=={users[role].id for role in ("librarian","admin","superadmin")}
    with pytest.raises(HTTPException):
        admin_router.extend_loan(loan.req_id,LoanExtensionRequest(due_date=new_due,reason="Retry approval"),db,users["librarian"])
    assert db.query(Notification).filter(Notification.title=="Loan period extended").count()==3
    assert db.query(LoanExtensionEvent).count()==1
    audit=db.query(AuditLog).filter(AuditLog.status=="Extended").one()
    assert audit.timestamp==event.approved_at and all(value in audit.event for value in ("REQ-EXT","Name reader","EXT-BOOK-C01","Study time",old_due.isoformat(),new_due.replace(tzinfo=None).isoformat()))
    # Identity/profile mutations must not rewrite the approval history.
    users["reader"].full_name="Changed name"; users["reader"].department="Changed department"; loan.book.title="Changed title"; db.commit()
    report=loan_extension_report(db,date.today()-timedelta(days=1),date.today()+timedelta(days=1))
    assert report["rows"][0]["name"]=="Name reader" and report["rows"][0]["book"]=="Extension reference"
    assert report["summary"]=={"approved_extensions":1,"unique_patrons":1}


@pytest.mark.parametrize("failure",["same_due","naive","closed","missing"])
def test_rejected_extension_creates_no_history_or_approval_notification(history,failure):
    db, users, loan, _ = history
    due=(loan.due_date+timedelta(days=1)).replace(tzinfo=timezone.utc)
    reference=loan.req_id
    if failure=="same_due": due=loan.due_date.replace(tzinfo=timezone.utc)
    if failure=="naive": due=due.replace(tzinfo=None)
    if failure=="closed": loan.status=BorrowStatus.RETURNED; db.commit()
    if failure=="missing": reference="MISSING"
    with pytest.raises(HTTPException):
        admin_router.extend_loan(reference,LoanExtensionRequest(due_date=due,reason="Research extension"),db,users["admin"])
    assert db.query(LoanExtensionEvent).count()==db.query(AuditLog).count()==db.query(Notification).count()==0


def test_repeated_approvals_keep_each_previous_due_and_one_patron(history):
    db, users, loan, _=history
    initial_due=loan.due_date
    for days in (2,4):
        due=(initial_due+timedelta(days=days)).replace(tzinfo=timezone.utc)
        admin_router.extend_loan(loan.req_id,LoanExtensionRequest(due_date=due,reason=f"Research stage {days}"),db,users["admin"])
    events=db.query(LoanExtensionEvent).order_by(LoanExtensionEvent.id).all()
    assert len(events)==2 and events[0].old_due_date==initial_due
    assert events[1].old_due_date==events[0].new_due_date and events[1].new_due_date==initial_due+timedelta(days=4)
    report=loan_extension_report(db,date.today()-timedelta(days=1),date.today()+timedelta(days=1))
    assert report["summary"]=={"approved_extensions":2,"unique_patrons":1}


def test_failed_transaction_rolls_back_due_event_and_audit_together(history,monkeypatch):
    db,users,loan,_=history
    old_due=loan.due_date
    def unavailable(*args,**kwargs): raise RuntimeError("Delivery queue unavailable")
    monkeypatch.setattr("staff_activity.notify_staff_activity",unavailable)
    with pytest.raises(RuntimeError):
        admin_router.extend_loan(loan.req_id,LoanExtensionRequest(due_date=(old_due+timedelta(days=2)).replace(tzinfo=timezone.utc),reason="Research"),db,users["admin"])
    db.rollback();db.refresh(loan)
    assert loan.due_date==old_due
    assert db.query(LoanExtensionEvent).count()==db.query(AuditLog).count()==db.query(Notification).count()==0


def test_legacy_audits_are_not_fabricated_into_new_history(history):
    db,_,_,_=history
    db.add(AuditLog(timestamp=datetime(2026,7,6,16),user_name="former-staff",event="Extended legacy loan",status="Extended"));db.commit()
    report=loan_extension_report(db,date(2026,7,7),date(2026,7,7))
    assert report["summary"]["approved_extensions"]==0 and report["rows"]==[]
    assert "Earlier approvals remain in Audit Logs" in report["history_note"]


def test_report_uses_approval_local_date_snapshot_filters_and_preserves_circulation_counts(history):
    db, users, loan, _ = history
    for stamp,patron,ref in [(datetime(2026,7,6,15,59,59),users["reader"],"BEFORE"),
        (datetime(2026,7,6,16),users["reader"],"START"),(datetime(2026,7,7,15,59,59),users["faculty"],"END"),
        (datetime(2026,7,7,16),users["reader"],"AFTER")]:
        db.add(make_event(loan,patron,users["admin"],stamp,ref))
    db.commit()
    report=loan_extension_report(db,date(2026,7,7),date(2026,7,7),role="all",direction="desc")
    assert [row["reference"] for row in report["rows"]]==["END","START"]
    assert report["rows"][1]["approved_at"]=="2026-07-07T00:00:00+08:00"
    assert report["rows"][0]["new_due_date"]=="2026-07-09T18:00:00+08:00"
    assert report["summary"]=={"approved_extensions":2,"unique_patrons":2}
    filtered=loan_extension_report(db,date(2026,7,7),date(2026,7,7),department="Historic department",course="Historic course",role="student")
    assert [row["reference"] for row in filtered["rows"]]==["START"]
    assert loan_extension_report(db,date(2026,7,7),date(2026,7,7),department="Computing")["rows"]==[]
    assert circulation_report(db,date(2026,7,7),date(2026,7,7))["summary"]=={
        "borrowed_items":0,"returned_items":0,"unique_borrowers":0,"unique_returners":0}


def report_client(history, role):
    db, users, loan, factory=history
    if not db.query(LoanExtensionEvent).count():
        db.add(make_event(loan,users["reader"],users["admin"],datetime(2026,7,6,16))); db.commit()
    app=FastAPI();app.include_router(report_router.router)
    def session():
        with factory() as session: yield session
    app.dependency_overrides[get_db]=session
    # Role enforcement is covered independently below; this override makes export tests fast.
    app.dependency_overrides[report_router.staff]=lambda:users[role]
    return TestClient(app)


@pytest.mark.parametrize("role",["librarian","admin","superadmin"])
@pytest.mark.parametrize("format",["json","csv","xlsx","pdf","docx"])
def test_extension_reports_are_available_to_both_staff_roles_and_export_complete_history(history,role,format):
    client=report_client(history,role)
    response=client.get(f"/admin/reports/loan-extensions?start=2026-07-07&end=2026-07-07&department=Historic%20department&course=Historic%20course&role=student&format={format}&paper=a4")
    assert response.status_code==200,response.text[:200] if format=="json" else response.status_code
    if format=="json":
        row=response.json()["rows"][0]
        assert (row["actor_name"],row["reason"],row["old_due_date"],row["new_due_date"])==(
            "Original approver","=Additional time for research","2026-07-07T18:00:00+08:00","2026-07-09T18:00:00+08:00")
        return
    assert response.headers["cache-control"]=="no-store"
    assert response.headers["content-disposition"].endswith(f'.{format}"')
    if format=="csv":
        rows=list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert rows[-1][-1]=="'=Additional time for research"
        assert rows[-1][10:15]==["2026-07-07T18:00:00+08:00","2026-07-09T18:00:00+08:00","Original approver","admin","admin"]
    elif format in {"docx","xlsx"}:
        with ZipFile(io.BytesIO(response.content)) as archive:
            part="word/document.xml" if format=="docx" else "xl/worksheets/sheet1.xml"
            xml=archive.read(part)
            assert b"Original approver" in xml and b"Additional time for research" in xml and b"Original book" in xml
            if format=="docx":
                assert b"2026-07-07 18:00:00+08:00" in xml and b"2026-07-09 18:00:00+08:00" in xml
                assert 'word/media/image1.jpeg' in archive.namelist()
            else:
                ns={"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                root=ET.fromstring(xml)
                assert root.findall(".//s:f",ns)==[]
                assert len(root.findall(".//s:c[@s='9']",ns))==3
    else:
        assert response.content.startswith(b"%PDF")


def test_extension_report_rejects_bad_ranges_and_unauthorized_users(history):
    client=report_client(history,"librarian")
    for query in ["start=2026-07-08&end=2026-07-07","start=2025-01-01&end=2026-07-07","start=9999-12-31&end=9999-12-31","end=0001-01-01"]:
        assert client.get("/admin/reports/loan-extensions?"+query).status_code==400
    assert client.get("/admin/reports/loan-extensions?paper=letter").status_code==422
    client.app.dependency_overrides.pop(report_router.staff)
    assert client.get("/admin/reports/loan-extensions").status_code==401
    client.app.dependency_overrides[get_current_user]=lambda:history[1]["reader"]
    assert client.get("/admin/reports/loan-extensions").status_code==403
