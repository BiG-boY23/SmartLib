"""Exercise the actual staff XLSX download routes and their filter scopes."""
import io
import zipfile
from datetime import date, datetime
from xml.etree import ElementTree as ET

import pytest

from database import get_db
from models import AcquisitionSuggestion, StaffArchive, User
from test_book_trends import book_api, PERIOD, WEEK


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
JUSTIFICATION = "Academic material supports this module and its required readings. " * 10 + "\nSecond paragraph with the complete recommendation."


@pytest.fixture
def xlsx_api(book_api):
    client, tokens = book_api
    session = client.app.dependency_overrides[get_db]()
    with next(session) as db:
        student = db.query(User).filter(User.username == "student").one()
        for reference, title, status in [
            ("ACQ-00001", "XLSX current recommendation", "Declined"),
            ("ACQ-00002", "XLSX archived recommendation", "Declined"),
            ("ACQ-00003", "XLSX different review status", "Under Review"),
            ("ACQ-00004", "Unselected recommendation", "Declined"),
        ]:
            db.add(AcquisitionSuggestion(acq_id=reference, user_id=student.id, title=title,
                author="=Formula author", justification=JUSTIFICATION, status=status,
                created_at=datetime(2026, 10, 19, 8)))
        db.add(StaffArchive(key="acquisitions:ACQ-00002", reason="Review complete", archived_by="librarian"))
        db.commit()
    session.close()
    return client, tokens


def _value(cell):
    if cell.get("t") == "inlineStr":
        return "".join(cell.find("s:is", NS).itertext())
    value = cell.find("s:v", NS)
    if value is None:
        return None
    if cell.get("t") == "b":
        return value.text == "1"
    number = float(value.text)
    return int(number) if number.is_integer() else number


def _rows(sheet):
    heading_row = int(sheet.find("s:sheetViews/s:sheetView/s:pane", NS).get("ySplit"))
    return [[_value(cell) for cell in row.findall("s:c", NS)]
            for row in sheet.findall("s:sheetData/s:row", NS) if int(row.get("r")) > heading_row]


def _workbook(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(XLSX_MIME)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith('attachment; filename="SmartLib-')
    assert response.headers["content-disposition"].endswith('.xlsx"')
    with zipfile.ZipFile(io.BytesIO(response.content)) as package:
        assert package.testzip() is None
        assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/styles.xml"} <= set(package.namelist())
        parts = {name: ET.fromstring(package.read(name)) for name in package.namelist() if name.endswith(".xml")}
    styles = parts["xl/styles.xml"].findall("s:cellXfs/s:xf", NS)
    sheets = {}
    for index, entry in enumerate(parts["xl/workbook.xml"].findall("s:sheets/s:sheet", NS), start=1):
        sheet = parts[f"xl/worksheets/sheet{index}.xml"]
        assert sheet.find(".//s:f", NS) is None
        assert sheet.find("s:hyperlinks", NS) is None
        assert sheet.find("s:sheetViews/s:sheetView/s:pane", NS).get("state") == "frozen"
        assert sheet.findall("s:cols/s:col", NS)
        for row in sheet.findall("s:sheetData/s:row", NS):
            assert 20 <= float(row.get("ht")) <= 409.5
            for cell in row.findall("s:c", NS):
                alignment = styles[int(cell.get("s"))].find("s:alignment", NS)
                assert alignment.get("wrapText") == "1" and alignment.get("vertical") == "top"
        sheets[entry.get("name")] = sheet
    return sheets, parts


@pytest.mark.parametrize("role", ["librarian", "admin"])
@pytest.mark.parametrize("kind", ["circulation", "weekly", "borrow-requests", "acquisitions"])
def test_xlsx_downloads_require_staff_and_match_the_visible_filter_scope(xlsx_api, role, kind):
    client, tokens = xlsx_api
    if kind in ("circulation", "weekly"):
        base = PERIOD if kind == "circulation" else WEEK
        endpoint = base + "&format=xlsx&role=faculty&department=Education&course=BPEd"
    else:
        endpoint = "/admin/reports/queue?format=xlsx&kind=" + kind
        endpoint += "&status=RETURNED&search=Faculty" if kind == "borrow-requests" else "&status=Declined&search=XLSX"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=tokens["student"]).status_code == 403
    response = client.get(endpoint, headers=tokens[role])
    sheets, _ = _workbook(response)
    text = " ".join(" ".join(sheet.itertext()) for sheet in sheets.values())
    if kind in ("circulation", "weekly"):
        assert "Audience: faculty | Department: Education | Course: BPEd" in text
        assert "Archived reference" not in text and "Prior week only" not in text
        report = client.get(endpoint.replace("format=xlsx", "format=json"), headers=tokens[role]).json()
        if kind == "circulation":
            assert list(sheets) == ["Period totals", "Daily totals", "Books", "Activity details"]
            assert dict(_rows(sheets["Period totals"])) == {key.replace("_", " ").title(): value for key, value in report["summary"].items()}
            assert _rows(sheets["Books"]) == [["B", "Shared title", "Library", 1, 3]]
            daily = _rows(sheets["Daily totals"])
            assert len(daily) == 7
            assert daily[0][0] == (date(2026, 10, 19) - date(1899, 12, 30)).days
            assert sum(row[3] for row in daily) == 1 and sum(row[4] for row in daily) == 3
            activity = _rows(sheets["Activity details"])
            assert len(activity) == len(report["rows"]) == 4
            assert {row[2] for row in activity} == {"Faculty"}
            assert {row[7] for row in activity} == {"Borrowed", "Returned"}
            assert {row[8] for row in activity} == {row["reference"] for row in report["rows"]}
            assert all(isinstance(row[0], int) for row in activity)
        else:
            assert _rows(sheets["Weekly book trends"]) == [[1, "B", "Shared title", "Library", 1, 3, 2, 0, -1, 3]]
            assert "Week: 2026-10-19 to 2026-10-25" in text
            assert "Previous week: 2026-10-12 to 2026-10-18" in text
    elif kind == "borrow-requests":
        rows = _rows(sheets["Records"])
        assert len(rows) == 3
        assert {row[0] for row in rows} == {"B-current", "B-prior-1", "B-prior-2"}
        assert all(row[1:4] == ["Faculty", "Shared title", "RETURNED"] for row in rows)
        assert "Status: RETURNED | Search: Faculty" in text
    else:
        rows = _rows(sheets["Records"])
        assert rows == [["ACQ-00001", "Student", "XLSX current recommendation", "Declined", "=Formula author", JUSTIFICATION]]
        assert "Records: Active" in text
        assert "Status: Declined | Search: XLSX" in text
        assert "ACQ-00002" not in text and "ACQ-00003" not in text and "ACQ-00004" not in text
        data_row = sheets["Records"].findall("s:sheetData/s:row", NS)[-1]
        assert float(data_row.get("ht")) > 60
        assert all(cell.get("t") == "inlineStr" for cell in data_row.findall("s:c", NS))


@pytest.mark.parametrize("role", ["librarian", "admin"])
def test_archived_acquisition_xlsx_uses_archive_filter_without_changing_records(xlsx_api, role):
    client, tokens = xlsx_api
    endpoint = "/admin/reports/queue?kind=acquisitions&format=xlsx&archived=true&status=Declined&search=XLSX"
    assert client.get(endpoint, headers=tokens[role]).status_code == 403
    sheets, _ = _workbook(client.get(endpoint, headers=tokens['superadmin']))
    assert _rows(sheets["Records"]) == [["ACQ-00002", "Student", "XLSX archived recommendation", "Declined", "=Formula author", JUSTIFICATION]]
    assert "Records: Archived" in " ".join(sheets["Records"].itertext())
    active, _ = _workbook(client.get(endpoint.replace("archived=true", "archived=false"), headers=tokens[role]))
    assert _rows(active["Records"])[0][0] == "ACQ-00001"


def test_weekly_xlsx_formula_text_is_literal_while_signed_changes_remain_numeric(xlsx_api):
    client, tokens = xlsx_api
    sheets, _ = _workbook(client.get(WEEK + "&format=xlsx", headers=tokens["librarian"]))
    sheet = sheets["Weekly book trends"]
    rows = _rows(sheet)
    assert len(rows) == 4
    assert rows[0] == [1, "A", "Shared title", "=Formula author", 3, 1, 1, 1, 2, 0]
    assert rows[1][-2:] == [-1, 3]
    author = next(cell for cell in sheet.findall(".//s:c", NS) if _value(cell) == "=Formula author")
    assert author.get("t") == "inlineStr"
    assert all(isinstance(value, int) for row in rows for value in row[4:])


def test_empty_filtered_xlsx_downloads_keep_headers_and_explain_missing_records(xlsx_api):
    client, tokens = xlsx_api
    for endpoint in [PERIOD + "&department=Missing", WEEK + "&department=Missing",
                     "/admin/reports/queue?kind=borrow-requests&search=NoMatches",
                     "/admin/reports/queue?kind=acquisitions&search=NoMatches"]:
        sheets, _ = _workbook(client.get(endpoint + "&format=xlsx", headers=tokens["admin"]))
        assert any("No records match the selected filters." in " ".join(sheet.itertext()) for sheet in sheets.values())
