"""Database-backed circulation analytics and portable report exports."""
import csv
import io
import math
from datetime import datetime, date, time, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from sqlalchemy import or_, func
from sqlalchemy.orm import joinedload
from models import Book, BorrowRecord, BorrowStatus, Fine, User, UserRole, LoanExtensionEvent

CAMPUS = "EVSU Ormoc City Campus"
LOCAL_TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent


def local_day(value):
    return value.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).date()


def dashboard_stats(db):
    books = db.query(Book).filter(Book.is_archived.is_(False))
    return {
        "total_books": books.with_entities(func.coalesce(func.sum(Book.total_copies), 0)).scalar(),
        "catalog_titles": books.count(),
        "active_borrows": db.query(BorrowRecord).filter(BorrowRecord.status.in_(
            [BorrowStatus.CHECKED_OUT, BorrowStatus.OVERDUE])).count(),
        "pending_requests": db.query(BorrowRecord).filter(BorrowRecord.status == BorrowStatus.PENDING).count(),
        "registered_patrons": db.query(User).filter(User.role.in_([UserRole.STUDENT, UserRole.FACULTY])).count(),
        "uncollected_fines": float(db.query(func.coalesce(func.sum(Fine.amount), 0)).filter(Fine.is_paid.is_(False)).scalar()),
    }


def circulation_report(db, start: date, end: date, department=None, course=None, role="student", sort="date", direction="asc"):
    start_utc = datetime.combine(start, time.min, LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = datetime.combine(end + timedelta(days=1), time.min, LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    query = db.query(BorrowRecord).join(User).options(joinedload(BorrowRecord.user), joinedload(BorrowRecord.book)).filter(or_(
        (BorrowRecord.issue_date >= start_utc) & (BorrowRecord.issue_date < end_utc),
        (BorrowRecord.return_date >= start_utc) & (BorrowRecord.return_date < end_utc),
    ))
    if role != "all":
        query = query.filter(User.role == UserRole(role))
    if department:
        query = query.filter(User.department == department)
    if course:
        query = query.filter(User.course == course)
    daily = {str(start + timedelta(days=n)): {"date": str(start + timedelta(days=n)), "borrowed_items": 0,
        "returned_items": 0, "borrowers": set(), "returners": set()} for n in range((end - start).days + 1)}
    rows, borrowers, returners, books = [], set(), set(), {}
    for record in query.all():
        for action, stamp in [("Borrowed", record.issue_date), ("Returned", record.return_date)]:
            if action == "Borrowed" and record.status not in (BorrowStatus.CHECKED_OUT, BorrowStatus.OVERDUE, BorrowStatus.RETURNED):
                continue
            if not stamp or not start_utc <= stamp < end_utc:
                continue
            day = str(local_day(stamp))
            count_key, people_key = ("borrowed_items", "borrowers") if action == "Borrowed" else ("returned_items", "returners")
            daily[day][count_key] += 1
            daily[day][people_key].add(record.user_id)
            (borrowers if action == "Borrowed" else returners).add(record.user_id)
            if record.book_id not in books:
                books[record.book_id] = {"book_id": record.book_id,
                    "title": record.book.title if record.book else "Removed book",
                    "author": record.book.author if record.book else "",
                    "accession_no": record.book.accession_no if record.book else "",
                    "borrowed_items": 0, "returned_items": 0}
            books[record.book_id][count_key] += 1
            rows.append({"date": day, "time": stamp.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).strftime("%H:%M"),
                "name": record.user.full_name, "institutional_id": record.user.username,
                "department": record.user.department or "Not recorded", "course": record.user.course or "Not recorded",
                "book": record.book.title if record.book else "Removed book", "book_id": record.book_id,
                "accession_no": record.book.accession_no if record.book else "", "copy_accession_no": record.copy_accession_no,
                "action": action, "reference": record.req_id})
    rows.sort(key=lambda r: (r[sort].casefold(), r["date"], r["time"], r["institutional_id"], r["reference"]), reverse=direction == "desc")
    series = [{**item, "borrowers": len(item["borrowers"]), "returners": len(item["returners"])} for item in daily.values()]
    return {"campus": CAMPUS, "start": str(start), "end": str(end), "timezone": "Asia/Manila (UTC+08:00)",
        "filters": {"department": department or "All", "course": course or "All", "role": role, "sort": sort, "direction": direction},
        "generated_at": datetime.now(LOCAL_TZ).isoformat(),
        "summary": {"borrowed_items": sum(d["borrowed_items"] for d in series), "returned_items": sum(d["returned_items"] for d in series),
                    "unique_borrowers": len(borrowers), "unique_returners": len(returners)},
        "daily": series, "rows": rows, "books": sorted(books.values(), key=book_activity_order)}


def book_activity_order(book):
    """Use real lending activity first, then a stable, readable title order."""
    return (-book["borrowed_items"], -book["returned_items"], book["title"].casefold(), book["book_id"])


EXTENSION_HISTORY_NOTE = ("Structured extension history starts with this feature. Earlier approvals remain in "
                          "Audit Logs and are not included in these totals. Extensions do not count as new checkouts or returns.")


def loan_extension_report(db, start: date, end: date, department=None, course=None, role="all", sort="date", direction="asc"):
    """Report actual approvals using immutable identity/profile snapshots and local approval dates."""
    start_utc = datetime.combine(start, time.min, LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = datetime.combine(end + timedelta(days=1), time.min, LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    query = db.query(LoanExtensionEvent).filter(LoanExtensionEvent.approved_at >= start_utc,
                                               LoanExtensionEvent.approved_at < end_utc)
    if role != "all":
        query = query.filter(LoanExtensionEvent.patron_role == role)
    if department:
        query = query.filter(LoanExtensionEvent.department == department)
    if course:
        query = query.filter(LoanExtensionEvent.course == course)
    events = query.all()
    def local_stamp(value):
        return value.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).isoformat(timespec="seconds")
    rows = []
    for event in events:
        stamp = event.approved_at.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
        rows.append({"id": event.id, "approved_at": local_stamp(event.approved_at),
            "date": stamp.date().isoformat(), "time": stamp.strftime("%H:%M:%S"),
            "reference": event.reference, "name": event.patron_name, "institutional_id": event.institutional_id,
            "role": event.patron_role, "department": event.department, "course": event.course,
            "book": event.book_title, "accession_no": event.accession_no, "copy_accession_no": event.copy_accession_no,
            "old_due_date": local_stamp(event.old_due_date), "new_due_date": local_stamp(event.new_due_date),
            "actor_name": event.actor_name, "actor_username": event.actor_username, "actor_role": event.actor_role,
            "reason": event.reason})
    rows.sort(key=lambda row: (row[sort].casefold(), row["approved_at"], row["id"]), reverse=direction == "desc")
    return {"campus": CAMPUS, "start": str(start), "end": str(end), "timezone": "Asia/Manila (UTC+08:00)",
        "filters": {"department": department or "All", "course": course or "All", "role": role, "sort": sort, "direction": direction},
        "generated_at": datetime.now(LOCAL_TZ).isoformat(), "history_note": EXTENSION_HISTORY_NOTE,
        "summary": {"approved_extensions": len(rows), "unique_patrons": len({event.institutional_id for event in events})},
        "rows": rows}


def weekly_book_trends(db, week=None, department=None, course=None, role="all"):
    """Compare actual issue/return events across Philippine Monday-Sunday weeks.

    Books active only in the prior week are retained to expose a fall to zero.
    Return-only titles are included but do not receive a borrowing rank.
    """
    selected_day = week or datetime.now(LOCAL_TZ).date()
    start = selected_day - timedelta(days=selected_day.weekday())
    end = start + timedelta(days=6)
    previous_start, previous_end = start - timedelta(days=7), start - timedelta(days=1)
    current = circulation_report(db, start, end, department, course, role)
    previous = circulation_report(db, previous_start, previous_end, department, course, role)
    current_books = {book["book_id"]: book for book in current["books"]}
    previous_books = {book["book_id"]: book for book in previous["books"]}
    books = []
    for book_id in current_books.keys() | previous_books.keys():
        metadata = current_books.get(book_id) or previous_books[book_id]
        this_week = current_books.get(book_id, {"borrowed_items": 0, "returned_items": 0})
        last_week = previous_books.get(book_id, {"borrowed_items": 0, "returned_items": 0})
        books.append({**metadata,
            "borrowed_items": this_week["borrowed_items"], "returned_items": this_week["returned_items"],
            "previous_borrowed_items": last_week["borrowed_items"], "previous_returned_items": last_week["returned_items"],
            "borrowed_change": this_week["borrowed_items"] - last_week["borrowed_items"],
            "returned_change": this_week["returned_items"] - last_week["returned_items"]})
    books.sort(key=book_activity_order)
    for index, book in enumerate(books, start=1):
        book["rank"] = index if book["borrowed_items"] else None
    return {"campus": CAMPUS, "week_start": str(start), "week_end": str(end),
        "previous_week_start": str(previous_start), "previous_week_end": str(previous_end),
        "timezone": current["timezone"], "generated_at": current["generated_at"],
        "filters": {"department": department or "All", "course": course or "All", "role": role},
        "summary": current["summary"], "previous_summary": previous["summary"],
        "daily": current["daily"], "previous_daily": previous["daily"], "books": books}


def font(size, bold=False):
    from PIL import ImageFont
    candidates = [ROOT / "static" / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        Path("C:/Windows/Fonts") / ("arialbd.ttf" if bold else "arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def chart_png(report):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (1400, 520), "white")
    draw = ImageDraw.Draw(image)
    draw.text((65, 20), "Daily people borrowing and returning", fill="#252129", font=font(28, True))
    series = report["daily"]
    high = max(1, max((max(d["borrowers"], d["returners"]) for d in series), default=0))
    step = max(1, math.ceil(high / 4))
    high = step * 4
    left, top, right, bottom = 80, 110, 1320, 410
    for n in range(5):
        y = bottom - n * (bottom-top) / 4
        draw.line((left, y, right, y), fill="#e6e3e3", width=2)
        draw.text((25, y-12), str(n*step), fill="#666666", font=font(20))
    for key, color, title, lx in [("borrowers", "#730d24", "Borrowers", 80), ("returners", "#237966", "Returners", 290)]:
        draw.line((lx, 78, lx+38, 78), fill=color, width=5)
        draw.text((lx+50, 62), title, fill=color, font=font(21))
        points = [(left + i * (right-left)/max(1, len(series)-1), bottom - d[key] / high * (bottom-top)) for i, d in enumerate(series)]
        width = max(1, min(12, (right-left) / max(1, len(series)) / 3))
        offset = -width if key == "borrowers" else width
        for x,y in points:
            if y < bottom:
                center = min(right-width/2, max(left+width/2, x+offset))
                draw.rectangle((center-width/2, y, center+width/2, bottom), fill=color)
    for i in sorted(set([0, len(series)-1] + list(range(0, len(series), max(1, len(series)//5))))):
        x = left + i * (right-left)/max(1, len(series)-1)
        draw.text((x, 430), series[i]["date"][5:], fill="#666666", font=font(18), anchor="mt")
    draw.text((80, 478), "Unique people per day; a person borrowing multiple items counts once. Dates: Philippine time.", fill="#666666", font=font(18))
    data=io.BytesIO(); image.save(data, format="PNG"); return data.getvalue()


COLUMNS = [("date", "Date"), ("time", "Time"), ("name", "Name"), ("institutional_id", "ID"), ("department", "Department"),
           ("course", "Course"), ("book", "Book"), ("action", "Activity"), ("reference", "Reference")]
BOOK_COLUMNS = [("accession_no", "Accession no."), ("title", "Book"), ("author", "Author"),
                ("borrowed_items", "Items borrowed"), ("returned_items", "Items returned")]
WEEKLY_BOOK_COLUMNS = [("rank", "Rank"), ("accession_no", "Accession no."), ("title", "Book"), ("author", "Author"),
    ("borrowed_items", "Borrowed"), ("returned_items", "Returned"),
    ("previous_borrowed_items", "Previous borrowed"), ("previous_returned_items", "Previous returned"),
    ("borrowed_change", "Borrowing change"), ("returned_change", "Return change")]


def spreadsheet_cell(value):
    # Keep signed numeric deltas numeric while neutralizing patron-provided text.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_report(report, format, paper="long"):
    if format == "csv":
        output=io.StringIO(newline=""); writer=csv.writer(output)
        safe = spreadsheet_cell
        writer.writerow(["Library circulation report", CAMPUS])
        writer.writerow(["From", report["start"], "To", report["end"], "Timezone", report["timezone"]])
        writer.writerow(["Audience", report["filters"]["role"], "Department", safe(report["filters"]["department"]), "Course", safe(report["filters"]["course"])])
        writer.writerow([])
        writer.writerow(["Period totals", "Count"])
        for key, value in report["summary"].items():
            writer.writerow([key.replace("_", " ").title(), value])
        writer.writerow([])
        writer.writerow(["Daily totals (unique people per day)"])
        writer.writerow(["Date", "Borrowers", "Returners", "Items borrowed", "Items returned"])
        for day in report["daily"]:
            writer.writerow([day[k] for k in ("date", "borrowers", "returners", "borrowed_items", "returned_items")])
        writer.writerow([])
        writer.writerow(["Books borrowed and returned"])
        writer.writerow([label for _, label in BOOK_COLUMNS])
        for book in report.get("books", []):
            writer.writerow([safe(book[key]) for key, _ in BOOK_COLUMNS])
        writer.writerow([])
        writer.writerow(["Activity details"])
        writer.writerow([label for _,label in COLUMNS])
        for row in report["rows"]:
            # Prevent values supplied by patrons from becoming spreadsheet formulas.
            writer.writerow([safe(row[k]) for k,_ in COLUMNS])
        return output.getvalue().encode("utf-8-sig")
    from report_documents import export_document
    from report_spreadsheets import export_workbook
    filters = report["filters"]
    title = "Library circulation report"
    metadata = [f"{CAMPUS} | {report['start']} to {report['end']} | Philippine time",
        f"Audience: {filters['role']} | Department: {filters['department']} | Course: {filters['course']}",
        " | ".join(f"{key.replace('_', ' ').title()}: {value}" for key, value in report["summary"].items()),
        f"Generated: {report['generated_at']}"]
    book_rows = [[book[key] for key, _ in BOOK_COLUMNS] for book in report.get("books", [])]
    if format == "xlsx":
        return export_workbook(title, metadata, [
            {"name":"Period totals", "columns":["Metric","Count"],
             "rows":[[key.replace("_"," ").title(), value] for key,value in report["summary"].items()], "widths":[28,14]},
            {"name":"Daily totals", "columns":["Date","Borrowers","Returners","Items borrowed","Items returned"],
             "rows":[[date.fromisoformat(day["date"])] + [day[key] for key in ("borrowers","returners","borrowed_items","returned_items")] for day in report["daily"]]},
            {"name":"Books", "columns":[label for _, label in BOOK_COLUMNS], "rows":book_rows, "widths":[22,45,32,18,18]},
            {"name":"Activity details", "columns":[label for _, label in COLUMNS],
             "rows":[[date.fromisoformat(row[key]) if key=="date" else row[key] for key,_ in COLUMNS] for row in report["rows"]],
             "widths":[15,12,30,24,28,24,42,16,25]}])
    activity = [[f"{row['date']}\n{row['time']}",
                 f"{row['name']}\nID: {row['institutional_id']}\n{row['department']}\n{row['course']}",
                 row['book'], f"{row['action']}\n{row['reference']}"] for row in report["rows"]]
    sections = [{"title":"Books borrowed and returned", "columns":[label for _,label in BOOK_COLUMNS],
                 "rows":book_rows,"widths":[16,28,24,16,16],"empty":"No books borrowed or returned in this period."},
                {"title":"Activity details", "columns":["Date and time","Patron and profile","Book","Activity and reference"],
                 "rows":activity,"widths":[16,30,30,24]}]
    return export_document(title, metadata, sections, format, chart=chart_png(report), paper=paper)


def export_weekly_book_trends(report, format, paper="long"):
    """Export the same complete weekly comparison shown in the staff report."""
    title = "Weekly most borrowed books"
    filters = report["filters"]
    metadata = [
        CAMPUS,
        f"Week: {report['week_start']} to {report['week_end']}",
        f"Previous week: {report['previous_week_start']} to {report['previous_week_end']}",
        f"Timezone: {report['timezone']}",
        f"Audience: {filters['role']} | Department: {filters['department']} | Course: {filters['course']}",
        "Ranked by items borrowed, then items returned. Blank ranks indicate no borrowing this week.",
        "Includes activity in either week; changes are this week minus the previous week.",
    ]
    values = [[book.get(key) if book.get(key) is not None else "" for key, _ in WEEKLY_BOOK_COLUMNS]
              for book in report["books"]]
    if format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow([title, CAMPUS])
        writer.writerow(["Week", report["week_start"], "To", report["week_end"]])
        writer.writerow(["Previous week", report["previous_week_start"], "To", report["previous_week_end"]])
        writer.writerow(["Timezone", report["timezone"]])
        writer.writerow(["Audience", filters["role"], "Department", spreadsheet_cell(filters["department"]),
                         "Course", spreadsheet_cell(filters["course"])])
        writer.writerow(["Generated", report["generated_at"]])
        writer.writerow([metadata[-2]])
        writer.writerow([metadata[-1]])
        writer.writerow([])
        writer.writerow([label for _, label in WEEKLY_BOOK_COLUMNS])
        writer.writerows([[spreadsheet_cell(value) for value in row] for row in values])
        return stream.getvalue().encode("utf-8-sig")
    from report_documents import export_document
    from report_spreadsheets import export_workbook
    metadata.append(f"Generated: {report['generated_at']}")
    columns = [label for _,label in WEEKLY_BOOK_COLUMNS]
    if format == "xlsx":
        return export_workbook(title, metadata, [{"name":"Weekly book trends", "columns":columns,
            "rows":values,"widths":[10,22,42,30,16,16,20,20,19,19]}])
    document_columns = ["Rank", "Book and author", "Borrowed", "Prior\nborrowed", "Borrow\nchange",
                        "Returned", "Prior\nreturned", "Return\nchange"]
    document_rows = [[book.get("rank") or "", f"{book['title']}\n{book['author']}\n{book['accession_no']}",
                      book['borrowed_items'], book['previous_borrowed_items'], book['borrowed_change'],
                      book['returned_items'], book['previous_returned_items'], book['returned_change']]
                     for book in report["books"]]
    return export_document(title, metadata, [{"title":"Weekly comparison", "columns":document_columns,
        "rows":document_rows,"widths":[7,31,11,11,10,11,11,10],
        "empty":"No recorded book activity matches these weeks and filters."}],format,paper=paper)


def export_queue_report(title, columns, rows, format, metadata, paper="long"):
    if format == "csv":
        stream = io.StringIO(newline=""); writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows([[spreadsheet_cell(value) for value in row] for row in rows])
        return stream.getvalue().encode("utf-8-sig")
    if format == "xlsx":
        from report_spreadsheets import export_workbook
        return export_workbook(title, metadata, [{"name":"Records","columns":columns,"rows":rows,
            "widths":[25,30,42,25,30,60]}])
    from report_documents import export_document
    return export_document(title, metadata, [{"title":"Records", "columns":columns,"rows":rows,
        "widths":[17,20,25,18,18,28]}],format,paper=paper)


EXTENSION_COLUMNS = [("approved_at", "Approved at (Philippine time)"), ("reference", "Loan reference"),
    ("name", "Patron"), ("institutional_id", "Institutional ID"), ("role", "Patron role"),
    ("department", "Department"), ("course", "Course"), ("book", "Book"), ("accession_no", "Catalog accession"),
    ("copy_accession_no", "Copy accession"), ("old_due_date", "Previous due (Philippine time)"),
    ("new_due_date", "New due (Philippine time)"), ("actor_name", "Approved by"),
    ("actor_username", "Approver username"), ("actor_role", "Approver role"), ("reason", "Reason")]


def export_loan_extensions(report, format, paper="long"):
    title = "Loan extension history"
    filters = report["filters"]
    metadata = [f"{CAMPUS} | {report['start']} to {report['end']}",
        f"Timezone: {report['timezone']} | Dates filter the approval time.",
        f"Audience: {filters['role']} | Department: {filters['department']} | Course: {filters['course']}",
        f"Approved extensions: {report['summary']['approved_extensions']} | Unique patrons: {report['summary']['unique_patrons']}",
        report["history_note"], f"Generated: {report['generated_at']}"]
    values = [[row[key] or "" for key, _ in EXTENSION_COLUMNS] for row in report["rows"]]
    if format == "csv":
        stream = io.StringIO(newline=""); writer = csv.writer(stream)
        writer.writerow([title])
        writer.writerows([[spreadsheet_cell(item)] for item in metadata])
        writer.writerow([])
        writer.writerow([label for _, label in EXTENSION_COLUMNS])
        writer.writerows([[spreadsheet_cell(value) for value in row] for row in values])
        return stream.getvalue().encode("utf-8-sig")
    if format == "xlsx":
        from report_spreadsheets import export_workbook
        date_keys = {"approved_at", "old_due_date", "new_due_date"}
        rows = [[datetime.fromisoformat(row[key]).replace(tzinfo=None) if key in date_keys else row[key] or ""
                 for key, _ in EXTENSION_COLUMNS] for row in report["rows"]]
        return export_workbook(title, metadata, [{"name":"Loan extensions", "columns":[label for _,label in EXTENSION_COLUMNS],
            "rows":rows, "widths":[26,25,28,22,16,25,25,40,24,24,26,26,26,23,18,60]}])
    from report_documents import export_document
    rows = [[f"{row['date']}\n{row['time']}\n{row['reference']}",
             f"{row['name']}\nID: {row['institutional_id']} ({row['role']})\n{row['department']} / {row['course']}\n"
             f"{row['book']}\nCatalog: {row['accession_no']}\nCopy: {row['copy_accession_no'] or 'Not recorded'}",
             f"Previous:\n{row['old_due_date'].replace('T', ' ')}\nNew:\n{row['new_due_date'].replace('T', ' ')}",
             f"{row['actor_name']}\n{row['actor_username']}\n{row['actor_role']}", row['reason']] for row in report["rows"]]
    return export_document(title, metadata, [{"title":"Approved extensions", "columns":["Approved / loan", "Patron / book", "Due dates", "Approved by", "Reason"],
        "rows":rows, "widths":[17,29,24,18,24], "empty":"No approved extensions match these dates and filters."}],format,paper=paper)


def library_id_png(user):
    from PIL import Image, ImageDraw
    import qrcode
    image=Image.new("RGB",(900,1400),"#ffffff"); draw=ImageDraw.Draw(image)
    wine, gold, ink="#730d24", "#d8b366", "#252129"
    draw.polygon([(0,0),(335,0),(0,335)],fill=wine)
    draw.polygon([(900,0),(665,0),(900,235)],fill=wine)
    draw.line([(0,350),(350,0)], fill=gold,width=14)
    draw.line([(900,250),(650,0)],fill=gold,width=14)
    logo_path=ROOT/"mobile-native"/"assets"/"evsu-smartlib-logo.jpg"
    if logo_path.exists():
        logo=Image.open(logo_path).convert("RGB").resize((150,150)); image.paste(logo,(375,48))
    def centered(text,y,size=30,bold=False,color=ink,max_width=780):
        words=str(text or "Not recorded").split(); lines=[]; line=""
        for word in words:
            test=(line+" "+word).strip()
            if draw.textlength(test,font=font(size,bold))>max_width and line: lines.append(line); line=word
            else: line=test
        lines.append(line)
        for line in lines:
            while draw.textlength(line,font=font(size,bold))>max_width and size>12: size-=1
            draw.text((450,y),line,font=font(size,bold),fill=color,anchor="mt"); y+=size+10
        return y
    centered("EASTERN VISAYAS STATE UNIVERSITY",226,26,True)
    centered("ORMOC CITY CAMPUS",269,24,True,wine)
    centered("SMARTLIB  /  LIBRARY MEMBERSHIP",320,20,False)
    payload=f"EVSU-CARD-2026::{user.username}::{user.role.value}"
    qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=12,border=4)
    qr.add_data(payload); qr.make(fit=True)
    qr.box_size = max(1, 660 // (qr.modules_count + 8))
    qr_image=qr.make_image(fill_color="black",back_color="white").convert("RGB")
    # Integer module scaling preserves a four-module white quiet zone.
    side=qr_image.width; image.paste(qr_image,((900-side)//2,390))
    draw.rounded_rectangle((100,375,800,1070),radius=24,outline=gold,width=4)
    name_y=1110
    name_y=centered(user.full_name,name_y,32,True)
    name_y=centered(user.username,name_y+2,26,True,wine)
    centered(f"{user.role.value.title()} | {'Active member' if user.is_active else 'Inactive member'}",name_y+4,19)
    draw.rectangle((0,1250,900,1400),fill=wine)
    y=centered(user.department or "Ormoc City Campus",1270,22,True,"#ffffff")
    centered(user.course or "Library member",y+2,20,False,"#ffffff")
    output=io.BytesIO(); image.save(output,format="PNG"); return output.getvalue()
