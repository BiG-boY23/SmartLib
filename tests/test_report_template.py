"""Verify actual report files preserve the university template and readable rows."""
import base64
import copy
from hashlib import sha256
import io
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import zlib

import pytest
from PIL import Image

from report_documents import LETTERHEAD, TEMPLATE, export_document


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
REFERENCE_SHA256 = "d181c0b4bd890131da6abe443efef26e5bfac4b1839c249b9637ecabd50802d7"


def docx_parts(payload):
    with ZipFile(io.BytesIO(payload)) as package:
        assert package.testzip() is None
        return {name: package.read(name) for name in package.namelist()}


def source_parts():
    return docx_parts(TEMPLATE.read_bytes())


def report_sections(text):
    return [{"title": "Book activity", "columns": ["Book", "Borrowed", "Returned"],
             "rows": [[text, 12, 7]], "widths": [5, 1, 1]}]


def pdf_objects(payload):
    """Read the simple, unencrypted object layout produced by ReportLab.

    Inspect file output without introducing a PDF-reader application dependency.
    This helper deliberately supports this generator's ordinary indirect objects,
    not arbitrary uploaded PDFs or compressed object streams.
    """
    return {int(match.group(1)): match.group(2)
            for match in re.finditer(rb"(?m)^(\d+) 0 obj\s*\r?\n(.*?)\r?\nendobj", payload, re.S)}


def decoded_stream(obj):
    stream = re.search(rb"\bstream\r?\n(.*?)\s*endstream\b", obj, re.S)
    assert stream is not None
    data = stream.group(1)
    filters = re.search(rb"/Filter\s*\[(.*?)\]", obj.split(b"stream", 1)[0], re.S)
    if filters:
        for name in re.findall(rb"/([A-Za-z0-9]+)", filters.group(1)):
            if name == b"ASCII85Decode":
                data = base64.a85decode(data.strip(), adobe=True)
            elif name == b"FlateDecode":
                data = zlib.decompress(data)
            elif name == b"DCTDecode":
                # The remaining stream is the original JPEG file.
                pass
            else:
                raise AssertionError(f"Unsupported test PDF filter {name!r}")
    return data


def test_word_keeps_source_parts_and_uses_selected_long_bond_paper():
    before = TEMPLATE.read_bytes()
    assert sha256(before).hexdigest() == REFERENCE_SHA256
    text = "A long book title with <markup> & quotations. " * 100 + "Final title text."
    sections = report_sections(text)
    untouched = copy.deepcopy(sections)
    generated = docx_parts(export_document("Circulation report", ["Philippine time"], sections, "docx"))
    reference = source_parts()

    # In a report without new media, every source part except the filled body
    # must be byte-identical, including all headers, styles, settings and rels.
    for name, payload in reference.items():
        if name != "word/document.xml":
            assert generated[name] == payload, f"Template part changed: {name}"
    assert sections == untouched
    assert TEMPLATE.read_bytes() == before
    document = ET.fromstring(generated["word/document.xml"])
    section = document.find("w:body/w:sectPr", NS)
    size = section.find("w:pgSz", NS)
    assert (size.get(f"{{{W}}}w"), size.get(f"{{{W}}}h")) == ("12240", "18720")
    margins = section.find("w:pgMar", NS)
    assert {key: margins.get(f"{{{W}}}{key}") for key in ["top", "bottom", "left", "right"]} == {
        "top": "2200", "bottom": "1560", "left": "1440", "right": "1440"}
    assert document.find(".//w:trHeight", NS) is None
    assert document.find(".//w:noWrap", NS) is None
    table = document.find(".//w:tbl", NS)
    assert table.find("w:tr/w:trPr/w:tblHeader", NS) is not None
    assert text in "".join(document.itertext())


def test_word_chart_additions_preserve_original_furniture_and_relationships():
    chart = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(chart, format="PNG")
    reference = source_parts()
    generated = docx_parts(export_document("Activity report", [], report_sections("Book"), "docx", chart=chart.getvalue()))
    mutable = {"word/document.xml", "word/_rels/document.xml.rels", "[Content_Types].xml"}
    for name, payload in reference.items():
        if name not in mutable:
            assert generated[name] == payload, f"Chart replaced template part: {name}"
    for name in mutable - {"word/document.xml"}:
        old = ET.fromstring(reference[name])
        new = ET.fromstring(generated[name])
        assert {tuple(sorted(item.attrib.items())) for item in old}.issubset(
            {tuple(sorted(item.attrib.items())) for item in new})
    added_media = [name for name in generated if name.startswith("word/media/") and name not in reference]
    assert added_media and any(generated[name] == chart.getvalue() for name in added_media)


def test_pdf_keeps_letterhead_on_every_page_and_splits_oversized_records():
    long_text = "A detailed title with <br/> literal markup & symbols. " * 300 + " LONG_RECORD_END"
    sections = report_sections(long_text)
    sections[0]["rows"].extend([[f"Book number {index}", index, index // 2] for index in range(90)])
    sections[0]["rows"].append(["LAST_ACTIVITY_RECORD", 1, 1])
    original = copy.deepcopy(sections)
    payload = export_document("Weekly book activity", ["2026-10-12 to 2026-10-18"], sections, "pdf")
    assert payload.startswith(b"%PDF-") and payload.rstrip().endswith(b"%%EOF")
    assert sections == original
    objects = pdf_objects(payload)
    pages = [obj for obj in objects.values() if re.search(rb"/Type\s*/Page(?!s)\b", obj)]
    assert len(pages) >= 3
    reference_image = LETTERHEAD.read_bytes()
    image_ids = {number for number, obj in objects.items()
                 if re.search(rb"/Subtype\s*/Image\b", obj) and decoded_stream(obj) == reference_image}
    assert image_ids, "The PDF must retain the original university JPEG"
    page_streams = []
    for page in pages:
        media = re.search(rb"/MediaBox\s*\[([^]]+)\]", page)
        assert media is not None
        assert [float(value) for value in media.group(1).split()] == [0, 0, 612, 936]
        resources = re.search(rb"/XObject\s*<<(.*?)>>", page, re.S)
        assert resources is not None
        named_images = [(name, int(number)) for name, number in re.findall(rb"/(\S+)\s+(\d+) 0 R", resources.group(1))
                        if int(number) in image_ids]
        assert named_images, "Every page must reference the university letterhead"
        content_id = re.search(rb"/Contents\s+(\d+) 0 R", page)
        assert content_id is not None
        content = decoded_stream(objects[int(content_id.group(1))])
        assert any(b"/" + name + b" Do" in content for name, number in named_images)
        assert re.search(rb"\b610\.75\s+0\s+0\s+934\.1\s+0\s+1\.9\s+cm\b", content), \
            "The original artwork must retain its native size on long bond paper"
        assert b"Borrowed" in content and b"Returned" in content, "Column headings must repeat"
        page_streams.append(content)
    assert b"LONG_RECORD_END" in b"".join(page_streams)
    assert b"LAST_ACTIVITY_RECORD" in page_streams[-1]


@pytest.mark.parametrize("format", ["docx", "pdf"])
def test_untrusted_book_text_cannot_break_document_generation(format):
    supplied = "Visible \x00\x01\x0b\x1f\ud800\ufffe <script>alert('x')</script> & title\nSecond line"
    payload = export_document("Report\x00 title", ["Filters\x01"], report_sections(supplied), format)
    assert len(payload) > 1000
    if format == "docx":
        document = ET.fromstring(docx_parts(payload)["word/document.xml"])
        text = "".join(document.itertext())
        assert "<script>alert('x')</script> & title" in text
        assert all(character not in text for character in "\x00\x01\x0b\x1f\ud800\ufffe")
    else:
        objects = pdf_objects(payload)
        assert any(re.search(rb"/Type\s*/Page(?!s)\b", obj) for obj in objects.values())


def test_empty_sections_produce_readable_word_document():
    parts = docx_parts(export_document("No activity report", [], [{"title": "Activity", "columns": ["Book"], "rows": []}], "docx"))
    document = ET.fromstring(parts["word/document.xml"])
    assert "No recorded activity matches these filters." in "".join(document.itertext())
    assert document.find(".//w:tbl", NS) is None


def test_a4_word_fits_original_artwork_without_distortion_or_source_changes():
    ns = {**NS, "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}
    reference = source_parts()
    parts = docx_parts(export_document("A4 circulation report", [], report_sections("Complete book title"), "docx", paper="a4"))
    for name, value in reference.items():
        if name not in {"word/document.xml", "word/header2.xml"}:
            assert parts[name] == value, f"A4 layout unexpectedly changed {name}"
    assert sha256(TEMPLATE.read_bytes()).hexdigest() == REFERENCE_SHA256
    document = ET.fromstring(parts["word/document.xml"])
    section = document.find("w:body/w:sectPr", NS)
    size = section.find("w:pgSz", NS)
    assert (size.get(f"{{{W}}}w"), size.get(f"{{{W}}}h")) == ("11906", "16838")
    margins = section.find("w:pgMar", NS)
    assert (margins.get(f"{{{W}}}top"), margins.get(f"{{{W}}}bottom")) == ("2200", "1560")
    anchor = ET.fromstring(parts["word/header2.xml"]).find(".//wp:anchor", ns)
    extent = anchor.find("wp:extent", ns)
    cx, cy = int(extent.get("cx")), int(extent.get("cy"))
    page_width, page_height = 595.3 * 12700, 841.9 * 12700
    assert abs(cx / 7756525 - cy / 11863070) < 0.0000001
    assert 0 < cx <= page_width and 0 < cy <= page_height
    horizontal = anchor.find("wp:positionH", ns)
    vertical = anchor.find("wp:positionV", ns)
    assert horizontal.get("relativeFrom") == vertical.get("relativeFrom") == "page"
    x = int(horizontal.find("wp:posOffset", ns).text)
    y = int(vertical.find("wp:posOffset", ns).text)
    assert abs(x - (page_width - cx) / 2) <= 1 and y == 0
    assert 0 <= x <= page_width - cx
    assert x / 12700 == pytest.approx(22.4, abs=0.2)
    assert document.find(".//w:trHeight", NS) is None
    assert parts["word/media/image1.jpeg"] == LETTERHEAD.read_bytes()


def test_a4_pdf_uses_source_page_size_and_fully_visible_proportional_letterhead():
    payload = export_document("A4 weekly report", [], report_sections("Book title"), "pdf", paper="a4")
    objects = pdf_objects(payload)
    pages = [obj for obj in objects.values() if re.search(rb"/Type\s*/Page(?!s)\b", obj)]
    assert pages
    image_ids = {number for number, obj in objects.items()
                 if re.search(rb"/Subtype\s*/Image\b", obj) and decoded_stream(obj) == LETTERHEAD.read_bytes()}
    assert image_ids
    for page in pages:
        media = re.search(rb"/MediaBox\s*\[([^]]+)\]", page)
        assert [float(value) for value in media.group(1).split()] == [0, 0, 595.3, 841.9]
        key = int(re.search(rb"/Contents\s+(\d+) 0 R", page).group(1))
        content = decoded_stream(objects[key])
        names = [name for name, number in re.findall(rb"/(\S+)\s+(\d+) 0 R", page) if int(number) in image_ids]
        assert names
        for name in names:
            # The image's transformation matrix immediately precedes its paint
            # operation; inspect that output rather than mirroring helper math.
            transform = re.search(rb"([-\d.]+)\s+0\s+0\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+cm\s*/"
                                  + re.escape(name) + rb"\s+Do", content)
            assert transform is not None
            width, height, x, y = map(float, transform.groups())
            assert width / height == pytest.approx(610.75 / 934.1, rel=0.000001)
            assert x >= 0 and y >= 0
            assert x + width <= 595.3001 and y + height <= 841.9001
            assert x == pytest.approx((595.3 - width) / 2, abs=0.0001)
            assert y + height == pytest.approx(841.9, abs=0.0001)


@pytest.mark.parametrize("format", ["docx", "pdf"])
def test_invalid_paper_choice_fails_clearly(format):
    with pytest.raises(ValueError, match="(?i)paper"):
        export_document("Invalid paper", [], report_sections("Book"), format, paper="letter")


def test_pdf_keeps_normal_records_together_and_prints_one_table_header_per_page():
    sections = [{"title": "Activity details", "columns": ["Date", "Patron profile", "Book", "Activity"],
                 "widths": [1, 3, 3, 2], "rows": [
                     ["2026-10-19", f"ROW_{index:03}_START\nSample Reader\nDepartment of Computer Studies\n"
                      f"Bachelor of Science in Information Technology\nROW_{index:03}_END",
                      "Database Systems and Library Information Management", "Borrowed\nREQ-EXAMPLE"]
                     for index in range(20)]}]
    payload = export_document("Library circulation report", ["Philippine time"], sections, "pdf", paper="a4")
    objects = pdf_objects(payload)
    pages = [obj for obj in objects.values() if re.search(rb"/Type\s*/Page(?!s)\b", obj)]
    assert len(pages) >= 2
    starts, ends = {}, {}
    for page_number, page in enumerate(pages):
        key = int(re.search(rb"/Contents\s+(\d+) 0 R", page).group(1))
        content = decoded_stream(objects[key])
        assert content.count(b"Patron profile") == 1, "A table header must not repeat at the bottom of a page"
        starts.update({match: page_number for match in re.findall(rb"ROW_(\d{3})_START", content)})
        ends.update({match: page_number for match in re.findall(rb"ROW_(\d{3})_END", content)})
    assert len(starts) == len(ends) == 20
    assert starts == ends, "A record that fits on a fresh page must not split across pages"
