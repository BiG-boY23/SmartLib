from datetime import date, datetime, timedelta
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database import get_db
from models import User, UserRole, SignupOption
from security import require_roles
from reporting import LOCAL_TZ, circulation_report, export_report

router=APIRouter(prefix="/admin/reports",tags=["Circulation reports"])
staff=require_roles([UserRole.SUPERADMIN,UserRole.ADMIN,UserRole.LIBRARIAN])


@router.get("/options")
def report_options(db: Session=Depends(get_db), user: User=Depends(staff)):
    # Include historical member values as well as the current registration catalog.
    pairs={(u.department or "",u.course or "") for u in db.query(User).filter(User.role.in_([UserRole.STUDENT,UserRole.FACULTY])).all()}
    options=db.query(SignupOption).filter(SignupOption.is_active.is_(True)).all()
    depts={o.id:o.name for o in options if o.kind=="department"}
    pairs.update((depts[o.department_id],o.name) for o in options if o.kind=="course" and o.department_id in depts)
    return {"departments":sorted(set(depts.values())|{d for d,c in pairs if d}),
        "courses":[{"department":d,"name":c} for d,c in sorted(pairs) if c]}


@router.get("/circulation")
def get_report(start: Optional[date]=None, end: Optional[date]=None,
    department: Optional[str]=Query(None,max_length=120), course: Optional[str]=Query(None,max_length=120),
    role: Literal["student","faculty","all"]="student", sort: Literal["date","department","course"]="date",
    direction: Literal["asc","desc"]="asc", format: Literal["json","csv","pdf","docx"]="json",
    db: Session=Depends(get_db), user: User=Depends(staff)):
    end=end or datetime.now(LOCAL_TZ).date(); start=start or end-timedelta(days=29)
    if start>end or (end-start).days>365:
        raise HTTPException(400,"Choose a date range of 1 to 366 days, with the start before the end.")
    report=circulation_report(db,start,end,department,course,role,sort,direction)
    if format=="json": return report
    mime={"csv":"text/csv; charset=utf-8","pdf":"application/pdf","docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[format]
    return Response(export_report(report,format),media_type=mime,headers={
        "Content-Disposition":f'attachment; filename="SmartLib-circulation-{start}-{end}.{format}"',"Cache-Control":"no-store"})
