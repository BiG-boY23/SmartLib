"""Check Excel readability, source preservation, and untrusted report text."""
import copy
import io
import zipfile
from datetime import date, datetime, time
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from report_spreadsheets import export_workbook


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def workbook_parts(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        return {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith(".xml")}


def cell_text(cell):
    return "".join(cell.itertext())


def cells_by_text(sheet):
    return {cell_text(cell): cell for cell in sheet.findall(".//s:sheetData/s:row/s:c", NS)}


def test_report_preserves_literal_patron_text_and_typed_values():
    malicious = '=HYPERLINK("https://example.invalid","Click")'
    rows = [["001234567890123456789", malicious, 12, -3, Decimal("2.50"), True, date(2026, 10, 19),
             datetime(2026, 10, 19, 14, 30), time(14, 30), "A & B <script>\nCafé βιβλίο 中文"]]
    source = [{"name": "Activity", "columns": ["ID", "Message", "Borrowed", "Change", "Amount", "Active", "Date", "Timestamp", "Time", "Book"], "rows": rows}]
    original = copy.deepcopy(source)
    parts = workbook_parts(export_workbook("Library report", ["Philippine time"], source))
    sheet = parts["xl/worksheets/sheet1.xml"]
    cells = cells_by_text(sheet)
    assert source == original
    assert cells["001234567890123456789"].get("t") == "inlineStr"
    assert cells[malicious].get("t") == "inlineStr"
    assert cells['A & B <script>\nCafé βιβλίο 中文'].get("t") == "inlineStr"
    assert cells["12"].get("t") is None
    assert cells["-3"].get("t") is None
    assert cells["2.50"].get("t") is None
    assert cells["1"].get("t") == "b"
    assert sheet.find(".//s:f", NS) is None
    assert sheet.find("s:hyperlinks", NS) is None
    formats = {item.get("numFmtId"): item.get("formatCode") for item in parts["xl/styles.xml"].findall("s:numFmts/s:numFmt", NS)}
    styles = parts["xl/styles.xml"].findall("s:cellXfs/s:xf", NS)
    dated_cells = [cell for cell in sheet.findall(".//s:c", NS) if styles[int(cell.get("s"))].get("numFmtId") in formats]
    assert len(dated_cells) == 3
    date_cell = dated_cells[0]
    assert float(date_cell.find("s:v", NS).text) == (date(2026, 10, 19) - date(1899, 12, 30)).days


def test_package_manifests_use_default_namespaces_for_native_opc_readers():
    content = export_workbook("Reader compatibility", [], [{"name": "Records", "columns": ["Book"], "rows": [["Reference"]]}])
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name, root, namespace in [
            ("[Content_Types].xml", "Types", "http://schemas.openxmlformats.org/package/2006/content-types"),
            ("_rels/.rels", "Relationships", "http://schemas.openxmlformats.org/package/2006/relationships"),
            ("xl/_rels/workbook.xml.rels", "Relationships", "http://schemas.openxmlformats.org/package/2006/relationships"),
        ]:
            xml = archive.read(name)
            assert f'<{root} xmlns="{namespace}">'.encode() in xml
            assert b"ns0:" not in xml
            assert ET.fromstring(xml).tag == "{" + namespace + "}" + root


def test_rows_fit_wrapped_multiline_text_and_have_frozen_filterable_headings():
    message = "A complete book description with many words. " * 9 + "\nSecond paragraph.\nThird paragraph."
    parts = workbook_parts(export_workbook("Borrow queue", ["2026-10-01 to 2026-10-19"], [
        {"name": "Requests", "columns": ["Request ID", "Justification"], "rows": [["REQ-1", "Short"], ["REQ-2", message]], "widths": [18, 34]},
    ]))
    sheet = parts["xl/worksheets/sheet1.xml"]
    cells = cells_by_text(sheet)
    long_row_number = cells[message].get("r")[1:]
    short_row_number = cells["Short"].get("r")[1:]
    long_row = sheet.find(f"s:sheetData/s:row[@r='{long_row_number}']", NS)
    short_row = sheet.find(f"s:sheetData/s:row[@r='{short_row_number}']", NS)
    assert float(long_row.get("ht")) > float(short_row.get("ht"))
    assert float(long_row.get("ht")) >= 180
    assert float(long_row.get("ht")) <= 409.5
    pane = sheet.find("s:sheetViews/s:sheetView/s:pane", NS)
    assert pane.get("state") == "frozen"
    assert pane.get("topLeftCell") == "A5"
    assert sheet.find("s:autoFilter", NS).get("ref") == "A4:B6"
    for style in parts["xl/styles.xml"].findall("s:cellXfs/s:xf", NS):
        assert style.find("s:alignment", NS).get("wrapText") == "1"
        assert style.find("s:alignment", NS).get("vertical") == "top"


def test_oversized_text_continues_without_clipping_losing_characters_or_duplicating_counts():
    message = ("Long patron explanation with symbols < & >.\n" * 1000) + "The end."
    content = export_workbook("Acquisitions", [], [{"name": "Suggestions", "columns": ["Quantity", "Justification"], "rows": [[7, message]], "widths": [14, 34]}])
    sheet = workbook_parts(content)["xl/worksheets/sheet1.xml"]
    rows = sheet.findall("s:sheetData/s:row", NS)
    data_rows = [row for row in rows if any(cell.get("r").startswith("B") and cell.get("t") == "inlineStr" and cell_text(cell) != "Justification" for cell in row.findall("s:c", NS))]
    chunks = [cell_text(cell) for row in data_rows for cell in row.findall("s:c", NS) if cell.get("r").startswith("B")]
    assert len(chunks) > 1
    assert "".join(chunks) == message
    assert all(float(row.get("ht")) <= 409.5 for row in data_rows)
    assert all(len(chunk) <= 32767 for chunk in chunks)
    quantities = [float(cell.find("s:v", NS).text) for row in data_rows for cell in row.findall("s:c", NS) if cell.get("r").startswith("A") and cell.find("s:v", NS) is not None]
    assert quantities == [7]


def test_empty_workbook_and_sheets_are_readable_and_names_are_excel_safe():
    parts = workbook_parts(export_workbook("Empty circulation report", [], []))
    assert "No records match the selected filters." in cells_by_text(parts["xl/worksheets/sheet1.xml"])
    sheets = [{"name": name, "columns": ["Book", "Count"], "rows": []} for name in ["Activity/invalid:*?[]", "activity/invalid:*?[]", "'" * 5, "x" * 50]]
    parts = workbook_parts(export_workbook("Empty circulation report", [], sheets))
    names = [sheet.get("name") for sheet in parts["xl/workbook.xml"].findall("s:sheets/s:sheet", NS)]
    assert len({name.casefold() for name in names}) == 4
    assert all(0 < len(name) <= 31 for name in names)
    assert all(not any(character in name for character in ":\\/?*[]") for name in names)
    for number in range(1, 5):
        sheet = parts[f"xl/worksheets/sheet{number}.xml"]
        assert sheet.find("s:autoFilter", NS) is None
        assert "No records match the selected filters." in cells_by_text(sheet)


def test_formula_prefixes_and_control_characters_cannot_corrupt_xlsx():
    values = ["+1+1", "-1+1", "@SUM(A1)", "\t=1+1", "_x0001_", "Hello\x01world\r\nNext"]
    parts = workbook_parts(export_workbook("Safety", [], [{"name": "Report", "columns": ["Text"], "rows": [[value] for value in values]}]))
    sheet = parts["xl/worksheets/sheet1.xml"]
    text = cells_by_text(sheet)
    for value in values[:4]:
        assert text[value].get("t") == "inlineStr"
    assert "_x005F_x0001_" in text
    assert "Hello_x0001_world_x000D_\nNext" in text
    assert sheet.find(".//s:f", NS) is None


@pytest.mark.parametrize("spec", [
    {"columns": [], "rows": []},
    {"columns": ["ID"], "rows": [[1, 2]]},
    {"columns": ["ID"], "rows": [], "widths": [10, 20]},
    {"columns": ["ID"], "rows": [], "widths": [float("nan")]},
])
def test_invalid_export_specs_are_rejected_clearly(spec):
    with pytest.raises(ValueError):
        export_workbook("Report", [], [spec])
