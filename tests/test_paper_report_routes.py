"""Check paper selection through every real staff document download route."""
from datetime import datetime
import re
from xml.etree import ElementTree as ET

import pytest

from database import get_db
from models import AcquisitionSuggestion, User
from test_reports_and_exports import api
from test_report_template import docx_parts, pdf_objects


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
CASES = [
    pytest.param("/admin/reports/circulation?start=2026-10-10&end=2026-10-11&role=all", "Reporting reference", id="circulation"),
    pytest.param("/admin/reports/book-trends?week=2026-10-10", "Reporting reference", id="weekly"),
    pytest.param("/admin/reports/queue?kind=borrow-requests", "R1", id="borrow-requests"),
    pytest.param("/admin/reports/queue?kind=acquisitions", "Paper choice recommendation", id="acquisitions"),
]
MIMES = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


@pytest.fixture
def paper_api(api):
    client, tokens = api
    sessions = client.app.dependency_overrides[get_db]()
    db = next(sessions)
    try:
        student = db.query(User).filter_by(username="A").one()
        db.add(AcquisitionSuggestion(acq_id="ACQ-PAPER-1", user_id=student.id,
            title="Paper choice recommendation", author="Library author",
            justification="Course reading material with complete details.",
            created_at=datetime(2026, 10, 19, 8)))
        db.commit()
    finally:
        db.close()
        sessions.close()
    return client, tokens


@pytest.mark.parametrize("endpoint,record", CASES)
@pytest.mark.parametrize("format", ["pdf", "docx"])
@pytest.mark.parametrize("paper", ["a4", "long", None], ids=["a4", "long", "default-long"])
def test_staff_documents_honor_paper_choice_and_default(paper_api, endpoint, record, format, paper):
    client, tokens = paper_api
    url = endpoint + f"&format={format}" + (f"&paper={paper}" if paper else "")
    response = client.get(url, headers=tokens["admin" if paper == "long" else "staff"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(MIMES[format])
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].endswith(f'.{format}"')
    if format == "docx":
        parts = docx_parts(response.content)
        document = ET.fromstring(parts["word/document.xml"])
        size = document.find(".//w:sectPr/w:pgSz", NS)
        expected = ("11906", "16838") if paper == "a4" else ("12240", "18720")
        assert (size.get(f"{{{W}}}w"), size.get(f"{{{W}}}h")) == expected
        assert record in "".join(document.itertext())
        assert "word/media/image1.jpeg" in parts
    else:
        assert response.content.startswith(b"%PDF-") and response.content.rstrip().endswith(b"%%EOF")
        pages = [value for value in pdf_objects(response.content).values() if re.search(rb"/Type\s*/Page(?!s)\b", value)]
        assert pages
        expected = [0, 0, 595.3, 841.9] if paper == "a4" else [0, 0, 612, 936]
        for page in pages:
            media = re.search(rb"/MediaBox\s*\[([^]]+)\]", page)
            assert [float(value) for value in media.group(1).split()] == expected


@pytest.mark.parametrize("endpoint,record", CASES)
@pytest.mark.parametrize("format", ["pdf", "docx"])
def test_paper_downloads_keep_staff_authorization_and_validate_choices(paper_api, endpoint, record, format):
    client, tokens = paper_api
    url = endpoint + f"&format={format}&paper=a4"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=tokens["A"]).status_code == 403
    invalid = client.get(url.replace("paper=a4", "paper=letter"), headers=tokens["staff"])
    assert invalid.status_code == 422
    assert any(error["loc"] == ["query", "paper"] for error in invalid.json()["detail"])
