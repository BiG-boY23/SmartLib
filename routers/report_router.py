from datetime import date, datetime, timedelta
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database import get_db
from models import User, UserRole, SignupOption, LoanExtensionEvent
from security import require_roles
from reporting import (LOCAL_TZ, circulation_report, export_report, weekly_book_trends, export_weekly_book_trends,
                       export_queue_report, loan_extension_report, export_loan_extensions)

router=APIRouter(prefix="/admin/reports",tags=["Circulation reports"])
staff=require_roles([UserRole.SUPERADMIN,UserRole.ADMIN,UserRole.LIBRARIAN])


@router.get("/options")
def report_options(db: Session=Depends(get_db), user: User=Depends(staff)):
    # Include historical member values as well as the current registration catalog.
    pairs={(u.department or "",u.course or "") for u in db.query(User).filter(User.role.in_([UserRole.STUDENT,UserRole.FACULTY])).all()}
    pairs.update(db.query(LoanExtensionEvent.department, LoanExtensionEvent.course).distinct().all())
    options=db.query(SignupOption).filter(SignupOption.is_active.is_(True)).all()
    depts={o.id:o.name for o in options if o.kind=="department"}
    pairs.update((depts[o.department_id],o.name) for o in options if o.kind=="course" and o.department_id in depts)
    return {"departments":sorted(set(depts.values())|{d for d,c in pairs if d}),
        "courses":[{"department":d,"name":c} for d,c in sorted(pairs) if c]}


@router.get("/circulation")
def get_report(start: Optional[date]=None, end: Optional[date]=None,
    department: Optional[str]=Query(None,max_length=120), course: Optional[str]=Query(None,max_length=120),
    role: Literal["student","faculty","all"]="student", sort: Literal["date","department","course"]="date",
    direction: Literal["asc","desc"]="asc", format: Literal["json","csv","pdf","docx","xlsx"]="json", paper: Literal["a4","long"]="long",
    db: Session=Depends(get_db), user: User=Depends(staff)):
    end=end or datetime.now(LOCAL_TZ).date(); start=start or end-timedelta(days=29)
    if start>end or (end-start).days>365:
        raise HTTPException(400,"Choose a date range of 1 to 366 days, with the start before the end.")
    report=circulation_report(db,start,end,department,course,role,sort,direction)
    if format=="json": return report
    mime={"csv":"text/csv; charset=utf-8","pdf":"application/pdf","docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[format]
    return Response(export_report(report,format,paper=paper),media_type=mime,headers={
        "Content-Disposition":f'attachment; filename="SmartLib-circulation-{start}-{end}.{format}"',"Cache-Control":"no-store"})


@router.get("/book-trends")
def book_trends(week: Optional[date]=None,
    department: Optional[str]=Query(None,max_length=120), course: Optional[str]=Query(None,max_length=120),
    role: Literal["student","faculty","all"]="all", format: Literal["json","csv","pdf","docx","xlsx"]="json", paper: Literal["a4","long"]="long",
    db: Session=Depends(get_db), user: User=Depends(staff)):
    if week and (week < date.min + timedelta(days=14) or week > date.max - timedelta(days=7)):
        raise HTTPException(400, "Choose a week with room for the previous week's comparison.")
    report = weekly_book_trends(db,week,department,course,role)
    if format == "json":
        return report
    mime = {"csv":"text/csv; charset=utf-8", "pdf":"application/pdf",
            "docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[format]
    filename = f"SmartLib-weekly-books-{report['week_start']}-{report['week_end']}.{format}"
    return Response(export_weekly_book_trends(report,format,paper=paper), media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control":"no-store"})


@router.get("/queue")
def queue_report(kind: Literal["borrow-requests", "acquisitions"], format: Literal["csv", "pdf", "docx", "xlsx"]="csv", paper: Literal["a4","long"]="long",
    status: str = "", search: str = "", archived: bool = False,
    db: Session=Depends(get_db), user: User=Depends(staff)):
    from models import BorrowRecord, AcquisitionSuggestion, StaffArchive
    if archived and user.role != UserRole.SUPERADMIN:
        raise HTTPException(403, "Only SuperAdmin can view archived records.")
    if kind == "borrow-requests":
        columns = ["Reference", "Patron", "Book", "Status", "Requested", "Returned"]
        records = db.query(BorrowRecord).order_by(BorrowRecord.request_date.desc()).all()
        rows = [[r.req_id, r.user.full_name, r.book.title if r.book else "Removed book", r.status.value,
                 str(r.request_date or ""), str(r.return_date or "")] for r in records]
    else:
        columns = ["Reference", "Patron", "Book", "Status", "Author", "Justification"]
        keys = {a.key for a in db.query(StaffArchive).all()}
        records = db.query(AcquisitionSuggestion).order_by(AcquisitionSuggestion.created_at.desc()).all()
        rows = [[r.acq_id, r.user.full_name, r.title, r.status, r.author, r.justification] for r in records
                if (f"acquisitions:{r.acq_id}" in keys) == archived]
    rows = [r for r in rows if (not status or r[3] == status) and
            (not search or search.casefold() in " ".join(r).casefold())]
    title = "Online Borrow Request Queue" if kind == "borrow-requests" else "Book Acquisition Suggestions"
    metadata = [f"{len(rows)} records | Generated: {datetime.now(LOCAL_TZ).isoformat()}",
        f"Status: {status or 'All'} | Search: {search or 'None'}",
        f"Records: {'Archived' if archived and kind=='acquisitions' else 'Active' if kind=='acquisitions' else 'All'}",
        "Timezone: Asia/Manila (UTC+08:00)"]
    payload = export_queue_report(title, columns, rows, format, metadata, paper=paper)
    mime = {"csv":"text/csv", "pdf":"application/pdf", "docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[format]
    return Response(payload, media_type=mime, headers={"Content-Disposition":f'attachment; filename="SmartLib-{kind}.{format}"', "Cache-Control":"no-store"})


@router.get("/loan-extensions")
def extension_history(start: Optional[date]=None, end: Optional[date]=None,
    department: Optional[str]=Query(None,max_length=120), course: Optional[str]=Query(None,max_length=120),
    role: Literal["student","faculty","all"]="all", sort: Literal["date","department","course"]="date",
    direction: Literal["asc","desc"]="asc", format: Literal["json","csv","pdf","docx","xlsx"]="json", paper: Literal["a4","long"]="long",
    db: Session=Depends(get_db), user: User=Depends(staff)):
    end=end or datetime.now(LOCAL_TZ).date()
    start=start or end-timedelta(days=min(29,(end-date.min).days))
    if start>end or (end-start).days>365 or start==date.min or end==date.max:
        raise HTTPException(400,"Choose a date range of 1 to 366 days, with the start before the end.")
    report=loan_extension_report(db,start,end,department,course,role,sort,direction)
    if format=="json":
        return report
    mime={"csv":"text/csv; charset=utf-8","pdf":"application/pdf","docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[format]
    return Response(export_loan_extensions(report,format,paper=paper),media_type=mime,headers={
        "Content-Disposition":f'attachment; filename="SmartLib-loan-extensions-{start}-{end}.{format}"', "Cache-Control":"no-store"})
