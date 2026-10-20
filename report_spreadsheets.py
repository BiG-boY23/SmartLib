"""Portable XLSX report downloads with fitted, wrapped cells.

CSV has no formatting model. This companion exporter writes the small OOXML
subset needed by report downloads, without adding an Excel dependency to the
server. Values are snapshots of report data; untrusted strings are never
interpreted as formulas or external hyperlinks.
"""
from __future__ import annotations

import io
import math
import re
import unicodedata
import zipfile
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal
from xml.etree import ElementTree as ET


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
MAX_ROW_HEIGHT = 409.5  # Excel's native limit, in points.
MAX_CELL_CHARACTERS = 32767
MAX_WRAPPED_LINES = 24
ET.register_namespace("", MAIN)
ET.register_namespace("r", REL)


def _tag(name):
    return f"{{{MAIN}}}{name}"


def _element(parent, name, attributes=None, **kwargs):
    return ET.SubElement(parent, _tag(name), attributes or {}, **kwargs)


def _xml(element):
    namespace = element.tag.partition("}")[0].lstrip("{")
    if namespace in (PACKAGE_REL, CONTENT_TYPES):
        # Some native OPC readers (including .NET OpenXml) reject prefixed
        # package manifests even though generic XML parsers accept them. Use
        # the conventional default namespace without modifying ET's global
        # namespace registry, which is shared by concurrent report requests.
        element = deepcopy(element)
        for item in element.iter():
            item.tag = item.tag.removeprefix("{" + namespace + "}")
        element.set("xmlns", namespace)
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def _column_name(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _sheet_name(value, used):
    # Excel also forbids an apostrophe at either edge of a tab name.
    name = re.sub(r"[\x00-\x1f\[\]:*?/\\]", " ", str(value)).strip(" '")[:31] or "Report"
    candidate, suffix = name, 1
    while candidate.casefold() in used:
        suffix += 1
        ending = f" ({suffix})"
        candidate = name[:31 - len(ending)] + ending
    used.add(candidate.casefold())
    return candidate


def _excel_text(value):
    """Use OOXML escapes for invalid XML characters and preserve literal escapes."""
    value = re.sub(r"_x[\dA-Fa-f]{4}_", lambda match: "_x005F_" + match.group()[1:], str(value))
    return "".join(
        f"_x{ord(character):04X}_" if (
            ord(character) < 32 and character not in "\t\n"
            or 0xD800 <= ord(character) <= 0xDFFF
            or ord(character) in (0xFFFE, 0xFFFF)
        ) else character
        for character in value
    )


def _display(value):
    if value is None:
        return ""
    if isinstance(value, (date, time)):
        return value.isoformat(sep=" ", timespec="seconds") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def _glyph_width(character):
    if unicodedata.combining(character):
        return 0
    if unicodedata.east_asian_width(character) in ("W", "F"):
        return 2
    if character in "MW@%":
        return 1.5
    if character in "il.,:;'!| ":
        return 0.65
    return 1


def _text_width(value):
    return max((sum(_glyph_width(character) for character in line) for line in _display(value).splitlines()), default=0)


def _wrapped_segments(text, width):
    """Return visual lines without discarding any characters in the source text."""
    # Conservative padding leaves room for font differences among Excel clients.
    available = max(3, (width - 2) * 0.87)
    segments, current, current_width = [], "", 0
    for character in text:
        if character == "\n":
            segments.append(current + character)
            current, current_width = "", 0
            continue
        size = _glyph_width(character)
        if current and current_width + size > available:
            # Excel wraps at spaces when one is present in the current line.
            split = current.rfind(" ") + 1
            if split:
                segments.append(current[:split])
                current = current[split:]
                current_width = sum(_glyph_width(item) for item in current)
            else:
                segments.append(current)
                current, current_width = "", 0
        current += character
        current_width += size
    if current or not segments or text.endswith("\n"):
        segments.append(current)
    return segments


def _cell_chunks(value, width):
    """Continue unusually long text on another row instead of clipping a cell."""
    if not isinstance(value, str):
        return [value]
    lines = _wrapped_segments(value, width)
    chunks, current, line_count = [], "", 0
    for line in lines:
        if line_count >= MAX_WRAPPED_LINES or len(current) + len(line) > MAX_CELL_CHARACTERS:
            chunks.append(current)
            current, line_count = "", 0
        current += line
        line_count += 1
    chunks.append(current)
    return chunks


def _row_height(values, widths, minimum=23):
    line_count = max((len(_wrapped_segments(_display(value), width)) for value, width in zip(values, widths)), default=1)
    return min(MAX_ROW_HEIGHT, max(minimum, line_count * 15 + 8))


def _widths(columns, rows, requested):
    if requested is not None:
        if len(requested) != len(columns):
            raise ValueError("Column widths must match the number of report columns.")
        sizes = [float(value) for value in requested]
        if any(not math.isfinite(value) or not 8 <= value <= 80 for value in sizes):
            raise ValueError("Report column widths must be between 8 and 80 characters.")
        return sizes
    result = []
    for index, heading in enumerate(columns):
        values = [row[index] for row in rows if row[index] is not None]
        numeric = values and all(isinstance(value, (int, float, Decimal)) for value in values)
        largest = max([_text_width(heading)] + [_text_width(value) for value in values])
        result.append(max(14, min(22 if numeric else 48, largest + 3)))
    return result


def _write_cell(row, reference, value, style):
    cell = _element(row, "c", {"r": reference, "s": str(style)})
    if value is None:
        return
    if isinstance(value, bool):
        cell.set("t", "b")
        _element(cell, "v").text = "1" if value else "0"
        return
    alternate = style == 5
    if isinstance(value, datetime) and value.tzinfo is None and value.year >= 1900:
        cell.set("s", "13" if alternate else "9")
        serial = (value - datetime(1899, 12, 30)).total_seconds() / 86400
        if value.date() < date(1900, 3, 1):
            serial -= 1
        _element(cell, "v").text = str(serial)
        return
    if isinstance(value, date) and not isinstance(value, datetime) and value.year >= 1900:
        cell.set("s", "12" if alternate else "8")
        serial = (value - date(1899, 12, 30)).days - int(value < date(1900, 3, 1))
        _element(cell, "v").text = str(serial)
        return
    if isinstance(value, time) and value.tzinfo is None:
        cell.set("s", "14" if alternate else "10")
        _element(cell, "v").text = str((value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6) / 86400)
        return
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        # Excel only retains 15 significant digits. Keep larger exact values as
        # text instead of silently rounding a long identifier or precise value.
        numeric = str(value)
        significant = numeric.lstrip("-+").replace(".", "").lstrip("0")
        precise = isinstance(value, float) or len(significant) <= 15
        if precise and math.isfinite(value):
            cell.set("s", ("11" if alternate else "7") if isinstance(value, int) else str(style))
            _element(cell, "v").text = numeric
            return
    cell.set("t", "inlineStr")
    inline = _element(cell, "is")
    _element(inline, "t", {XML_SPACE: "preserve"}).text = _excel_text(_display(value))


def _styles():
    root = ET.Element(_tag("styleSheet"))
    formats = _element(root, "numFmts", {"count": "3"})
    for code, identifier in [("yyyy-mm-dd", 164), ("yyyy-mm-dd hh:mm", 165), ("hh:mm", 166)]:
        _element(formats, "numFmt", {"numFmtId": str(identifier), "formatCode": code})
    fonts = _element(root, "fonts", {"count": "4"})
    for size, color, bold in [(11, "FF20242B", False), (17, "FF730D24", True), (10, "FF555E6B", False), (11, "FFFFFFFF", True)]:
        font = _element(fonts, "font")
        _element(font, "sz", {"val": str(size)})
        _element(font, "name", {"val": "Calibri"})
        _element(font, "color", {"rgb": color})
        if bold:
            _element(font, "b")
    fills = _element(root, "fills", {"count": "4"})
    for pattern, color in [("none", None), ("gray125", None), ("solid", "FF730D24"), ("solid", "FFF2F4F7")]:
        fill = _element(fills, "fill")
        pattern_fill = _element(fill, "patternFill", {"patternType": pattern})
        if color:
            _element(pattern_fill, "fgColor", {"rgb": color})
            _element(pattern_fill, "bgColor", {"indexed": "64"})
    borders = _element(root, "borders", {"count": "2"})
    for has_border in (False, True):
        border = _element(borders, "border")
        for side in ("left", "right", "top", "bottom", "diagonal"):
            edge = _element(border, side, {"style": "thin"} if has_border and side == "bottom" else {})
            if has_border and side == "bottom":
                _element(edge, "color", {"rgb": "FFE3E6EB"})
    defaults = _element(root, "cellStyleXfs", {"count": "1"})
    _element(defaults, "xf", {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"})
    styles = _element(root, "cellXfs", {"count": "15"})
    definitions = [
        (0, 0, 0, 0), (1, 0, 0, 0), (2, 0, 0, 0), (3, 2, 1, 0),
        (0, 0, 1, 0), (0, 3, 1, 0), (2, 0, 0, 0),
        (0, 0, 1, 3), (0, 0, 1, 164), (0, 0, 1, 165), (0, 0, 1, 166),
        (0, 3, 1, 3), (0, 3, 1, 164), (0, 3, 1, 165), (0, 3, 1, 166),
    ]
    for font, fill, border, number_format in definitions:
        attributes = {"numFmtId": str(number_format), "fontId": str(font), "fillId": str(fill),
                      "borderId": str(border), "xfId": "0", "applyAlignment": "1"}
        if number_format:
            attributes["applyNumberFormat"] = "1"
        xf = _element(styles, "xf", attributes)
        _element(xf, "alignment", {"vertical": "top", "wrapText": "1"})
    cell_styles = _element(root, "cellStyles", {"count": "1"})
    _element(cell_styles, "cellStyle", {"name": "Normal", "xfId": "0", "builtinId": "0"})
    return root


def _worksheet(title, metadata, spec):
    columns = list(spec.get("columns", []))
    if not columns or len(columns) > 16384:
        raise ValueError("An Excel report sheet requires between 1 and 16,384 columns.")
    rows = [list(row) for row in spec.get("rows", [])]
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("Every report row must match its column headings.")
    widths = _widths(columns, rows, spec.get("widths"))
    fitted_rows, continued = [], False
    for row in rows:
        chunks = [_cell_chunks(value, width) for value, width in zip(row, widths)]
        count = max(map(len, chunks))
        continued = continued or count > 1
        for index in range(count):
            fitted_rows.append([parts[index] if index < len(parts) else None for parts in chunks])
    notes = list(metadata)
    if continued:
        notes.append("Long text continues on the following row. Counts appear once per record.")
    header_row = len(notes) + 3
    last_row = header_row + max(1, len(fitted_rows))
    if last_row > 1048576:
        raise ValueError("This report exceeds Excel's 1,048,576-row limit.")
    last_column = _column_name(len(columns))
    root = ET.Element(_tag("worksheet"))
    properties = _element(root, "sheetPr")
    _element(properties, "pageSetUpPr", {"fitToPage": "1"})
    _element(root, "dimension", {"ref": f"A1:{last_column}{last_row}"})
    views = _element(root, "sheetViews")
    view = _element(views, "sheetView", {"workbookViewId": "0", "showGridLines": "0"})
    _element(view, "pane", {"ySplit": str(header_row), "topLeftCell": f"A{header_row + 1}", "activePane": "bottomLeft", "state": "frozen"})
    _element(view, "selection", {"pane": "bottomLeft", "activeCell": f"A{header_row + 1}", "sqref": f"A{header_row + 1}"})
    _element(root, "sheetFormatPr", {"defaultRowHeight": "23"})
    column_settings = _element(root, "cols")
    for index, width in enumerate(widths, start=1):
        _element(column_settings, "col", {"min": str(index), "max": str(index), "width": str(width), "customWidth": "1"})
    data = _element(root, "sheetData")
    merges = []
    for index, (value, style) in enumerate([(title, 1)] + [(note, 2) for note in notes], start=1):
        height = _row_height([value], [sum(widths)], minimum=32 if index == 1 else 23)
        row = _element(data, "row", {"r": str(index), "ht": str(height), "customHeight": "1"})
        _write_cell(row, f"A{index}", value, style)
        if len(columns) > 1:
            merges.append(f"A{index}:{last_column}{index}")
    heading = _element(data, "row", {"r": str(header_row), "ht": str(_row_height(columns, widths, minimum=30)), "customHeight": "1"})
    for column, value in enumerate(columns, start=1):
        _write_cell(heading, f"{_column_name(column)}{header_row}", value, 3)
    for index, values in enumerate(fitted_rows, start=header_row + 1):
        row = _element(data, "row", {"r": str(index), "ht": str(_row_height(values, widths)), "customHeight": "1"})
        for column, value in enumerate(values, start=1):
            _write_cell(row, f"{_column_name(column)}{index}", value, 4 + ((index - header_row - 1) % 2))
    if not fitted_rows:
        message = "No records match the selected filters."
        row = _element(data, "row", {"r": str(header_row + 1), "ht": str(_row_height([message], [sum(widths)])), "customHeight": "1"})
        _write_cell(row, f"A{header_row + 1}", message, 6)
        if len(columns) > 1:
            merges.append(f"A{header_row + 1}:{last_column}{header_row + 1}")
    else:
        _element(root, "autoFilter", {"ref": f"A{header_row}:{last_column}{last_row}"})
    if merges:
        merged = _element(root, "mergeCells", {"count": str(len(merges))})
        for reference in merges:
            _element(merged, "mergeCell", {"ref": reference})
    _element(root, "printOptions", {"gridLines": "0"})
    _element(root, "pageMargins", {"left": "0.3", "right": "0.3", "top": "0.5", "bottom": "0.5", "header": "0.2", "footer": "0.2"})
    _element(root, "pageSetup", {"paperSize": "9", "orientation": "landscape", "fitToWidth": "1", "fitToHeight": "0"})
    return root, header_row


def export_workbook(title: str, metadata: list[str], sheets: list[dict]) -> bytes:
    """Export report snapshots with wrapped, fitted cells and frozen headings.

    Each sheet is ``{name, columns: [str], rows: [[value]], widths?: [float]}``.
    Explicit widths use Excel character units (8–80). Pass Python date/datetime
    objects for typed dates; numeric-looking identifiers should remain strings.
    Long cell text continues on adjacent rows to respect Excel's height limit.
    No source rows, sheet dictionaries, or metadata are changed.
    """
    if not sheets:
        sheets = [{"name": "Report", "columns": ["Result"], "rows": []}]
    used, names, worksheets, header_rows = set(), [], [], []
    for spec in sheets:
        names.append(_sheet_name(spec.get("name", "Report"), used))
        sheet, header_row = _worksheet(title, metadata, spec)
        worksheets.append(sheet)
        header_rows.append(header_row)
    workbook = ET.Element(_tag("workbook"))
    book_views = _element(workbook, "bookViews")
    _element(book_views, "workbookView", {"activeTab": "0"})
    sheet_list = _element(workbook, "sheets")
    relationships = ET.Element(f"{{{PACKAGE_REL}}}Relationships")
    types = ET.Element(f"{{{CONTENT_TYPES}}}Types")
    ET.SubElement(types, f"{{{CONTENT_TYPES}}}Default", {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"})
    ET.SubElement(types, f"{{{CONTENT_TYPES}}}Default", {"Extension": "xml", "ContentType": "application/xml"})
    for index, (name, _) in enumerate(zip(names, worksheets), start=1):
        _element(sheet_list, "sheet", {"name": name, "sheetId": str(index), f"{{{REL}}}id": f"rId{index}"})
        ET.SubElement(relationships, f"{{{PACKAGE_REL}}}Relationship", {"Id": f"rId{index}", "Type": REL + "/worksheet", "Target": f"worksheets/sheet{index}.xml"})
        ET.SubElement(types, f"{{{CONTENT_TYPES}}}Override", {"PartName": f"/xl/worksheets/sheet{index}.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"})
    defined_names = _element(workbook, "definedNames")
    for index, (name, header_row) in enumerate(zip(names, header_rows)):
        _element(defined_names, "definedName", {"name": "_xlnm.Print_Titles", "localSheetId": str(index)}).text = f"'{name.replace(chr(39), chr(39) * 2)}'!${header_row}:${header_row}"
    ET.SubElement(relationships, f"{{{PACKAGE_REL}}}Relationship", {"Id": f"rId{len(sheets) + 1}", "Type": REL + "/styles", "Target": "styles.xml"})
    ET.SubElement(types, f"{{{CONTENT_TYPES}}}Override", {"PartName": "/xl/workbook.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"})
    ET.SubElement(types, f"{{{CONTENT_TYPES}}}Override", {"PartName": "/xl/styles.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"})
    package = ET.Element(f"{{{PACKAGE_REL}}}Relationships")
    ET.SubElement(package, f"{{{PACKAGE_REL}}}Relationship", {"Id": "rId1", "Type": REL + "/officeDocument", "Target": "xl/workbook.xml"})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xml(types))
        archive.writestr("_rels/.rels", _xml(package))
        archive.writestr("xl/workbook.xml", _xml(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", _xml(relationships))
        archive.writestr("xl/styles.xml", _xml(_styles()))
        for index, sheet in enumerate(worksheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _xml(sheet))
    return output.getvalue()
