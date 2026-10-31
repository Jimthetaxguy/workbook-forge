"""Hand-built OOXML corpus variants (standard library only).

Each builder returns a mapping of package part name to bytes. The parts are
written by ``write_package``. These files exercise shapes the generator
libraries never produce: missing optional parts, absolute targets, prefixed
SpreadsheetML, strict OOXML, pivot caches, external links, threaded comments,
chartsheets, and the quirks of other spreadsheet writers.

All content is synthetic and general-purpose.
"""
from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
WB_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
WS_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
SST_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
STY_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"
RELS_CT = "application/vnd.openxmlformats-package.relationships+xml"
DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
# Zip entries carry a fixed timestamp so regenerating the corpus is byte-stable.
STAMP = (2020, 1, 1, 0, 0, 0)


def write_package(path: Path, parts: dict[str, bytes | str], *, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = compression
            info.external_attr = 0o644 << 16
            archive.writestr(info, data.encode("utf-8") if isinstance(data, str) else data)


def content_types(overrides: dict[str, str], defaults: dict[str, str] | None = None) -> str:
    defaults = defaults or {"rels": RELS_CT, "xml": "application/xml"}
    body = "".join(f'<Default Extension="{k}" ContentType="{v}"/>' for k, v in defaults.items())
    body += "".join(
        f'<Override PartName="/{k}" ContentType="{v}"/>' for k, v in overrides.items()
    )
    return f'{DECL}<Types xmlns="{CT}">{body}</Types>'


def rels(items: list[tuple[str, str, str]], external: set[str] = frozenset()) -> str:
    body = ""
    for rid, kind, target in items:
        mode = ' TargetMode="External"' if rid in external else ""
        body += f'<Relationship Id="{rid}" Type="{kind}" Target="{target}"{mode}/>'
    return f'{DECL}<Relationships xmlns="{PKG}">{body}</Relationships>'


ROOT_RELS = rels([("rId1", f"{REL}/officeDocument", "xl/workbook.xml")])


def workbook_xml(sheets: list[str], extra: str = "", pr: str = "", states=None) -> str:
    states = states or {}
    items = ""
    for index, name in enumerate(sheets, 1):
        state = f' state="{states[name]}"' if name in states else ""
        escaped = name.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
        items += f'<sheet name="{escaped}" sheetId="{index}"{state} r:id="rId{index}"/>'
    return (
        f'{DECL}<workbook xmlns="{MAIN}" xmlns:r="{REL}">{pr}<sheets>{items}</sheets>{extra}'
        "</workbook>"
    )


def sheet_xml(rows: str, *, dimension: str | None = None, before: str = "", after: str = "") -> str:
    dim = f'<dimension ref="{dimension}"/>' if dimension else ""
    return (
        f'{DECL}<worksheet xmlns="{MAIN}" xmlns:r="{REL}">{dim}{before}'
        f"<sheetData>{rows}</sheetData>{after}</worksheet>"
    )


def minimal_styles() -> str:
    return (
        f'{DECL}<styleSheet xmlns="{MAIN}">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )


def simple(sheets: dict[str, str], *, sst: list[str] | None = None, styles: str | None = None,
           wb_extra: str = "", wb_pr: str = "", states=None, extra_parts=None,
           extra_overrides=None, extra_wb_rels=None, defaults=None, sheet_rels=None):
    """A package with the given worksheet bodies (complete worksheet XML)."""
    names = list(sheets)
    parts: dict[str, str | bytes] = {}
    overrides = {"xl/workbook.xml": WB_CT}
    wb_rels = []
    for index, name in enumerate(names, 1):
        part = f"xl/worksheets/sheet{index}.xml"
        parts[part] = sheets[name]
        overrides[part] = WS_CT
        wb_rels.append((f"rId{index}", f"{REL}/worksheet", f"worksheets/sheet{index}.xml"))
    next_id = len(names) + 1
    if styles is not None:
        parts["xl/styles.xml"] = styles
        overrides["xl/styles.xml"] = STY_CT
        wb_rels.append((f"rId{next_id}", f"{REL}/styles", "styles.xml"))
        next_id += 1
    if sst is not None:
        items = "".join(f"<si><t xml:space=\"preserve\">{_esc(s)}</t></si>" for s in sst)
        parts["xl/sharedStrings.xml"] = (
            f'{DECL}<sst xmlns="{MAIN}" count="{len(sst)}" uniqueCount="{len(sst)}">{items}</sst>'
        )
        overrides["xl/sharedStrings.xml"] = SST_CT
        wb_rels.append((f"rId{next_id}", f"{REL}/sharedStrings", "sharedStrings.xml"))
        next_id += 1
    external = set()
    for kind, target, is_external in extra_wb_rels or []:
        rid = f"rId{next_id}"
        next_id += 1
        wb_rels.append((rid, kind, target))
        if is_external:
            external.add(rid)
    overrides.update(extra_overrides or {})
    out = {
        "[Content_Types].xml": content_types(overrides, defaults),
        "_rels/.rels": ROOT_RELS,
        "xl/workbook.xml": workbook_xml(names, wb_extra, wb_pr, states),
        "xl/_rels/workbook.xml.rels": rels(wb_rels, external),
    }
    out.update(parts)
    for index, body in (sheet_rels or {}).items():
        out[f"xl/worksheets/_rels/sheet{index}.xml.rels"] = body
    out.update(extra_parts or {})
    return out


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tiny_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\x30\x60\x90" * 4 for _ in range(4))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------- builders


def minimal_no_optional_parts():
    """No styles.xml, no sharedStrings, no dimension, no docProps."""
    rows = (
        '<row r="1"><c r="A1"><v>12</v></c><c r="B1"><v>30</v></c><c r="C1"><f>A1+B1</f></c></row>'
        '<row r="2"><c r="A2" t="b"><v>1</v></c><c r="B2" t="inlineStr"><is><t>notes</t></is></c></row>'
    )
    return simple({"Data": sheet_xml(rows)})


def inline_strings_only():
    rows = "".join(
        f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>Item {r}</t></is></c>'
        f'<c r="B{r}"><v>{r * 3}</v></c></row>'
        for r in range(1, 6)
    )
    rows += '<row r="6"><c r="A6" t="inlineStr"><is><r><rPr><b/></rPr><t>Bold</t></r><r><t xml:space="preserve"> tail</t></r></is></c><c r="B6"><f>SUM(B1:B5)</f><v>45</v></c></row>'
    return simple({"Inventory": sheet_xml(rows, dimension="A1:B6")}, styles=minimal_styles())


def absolute_targets():
    parts = minimal_no_optional_parts()
    parts["_rels/.rels"] = rels([("rId1", f"{REL}/officeDocument", "/xl/workbook.xml")])
    parts["xl/_rels/workbook.xml.rels"] = rels(
        [("rId1", f"{REL}/worksheet", "/xl/worksheets/sheet1.xml")]
    )
    return parts


def shared_formulas():
    rows = "".join(f'<row r="{r}"><c r="A{r}"><v>{r}</v></c><c r="B{r}"><v>{r * 10}</v></c>'
                   + (f'<c r="C{r}"><f t="shared" ref="C1:C6" si="0">A1*B1</f><v>{r * r * 10}</v></c>' if r == 1
                      else f'<c r="C{r}"><f t="shared" si="0"/><v>{r * r * 10}</v></c>')
                   + "</row>" for r in range(1, 7))
    rows += '<row r="7"><c r="C7"><f>SUM(C1:C6)</f><v>910</v></c></row>'
    return simple({"Engineering": sheet_xml(rows, dimension="A1:C7")}, styles=minimal_styles())


def strict_ooxml():
    smain = "http://purl.oclc.org/ooxml/spreadsheetml/main"
    srel = "http://purl.oclc.org/ooxml/officeDocument/relationships"
    parts = {
        "[Content_Types].xml": content_types({"xl/workbook.xml": WB_CT, "xl/worksheets/sheet1.xml": WS_CT}),
        "_rels/.rels": rels([("rId1", f"{srel}/officeDocument", "xl/workbook.xml")]),
        "xl/workbook.xml": f'{DECL}<workbook xmlns="{smain}" xmlns:r="{srel}" conformance="strict"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": rels([("rId1", f"{srel}/worksheet", "worksheets/sheet1.xml")]),
        "xl/worksheets/sheet1.xml": f'{DECL}<worksheet xmlns="{smain}"><sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData></worksheet>',
    }
    return parts


def prefixed_spreadsheetml():
    """SpreadsheetML written with an `x:` prefix, as some SDK-based writers do."""
    parts = {
        "[Content_Types].xml": content_types({"xl/workbook.xml": WB_CT, "xl/worksheets/sheet1.xml": WS_CT, "xl/sharedStrings.xml": SST_CT}),
        "_rels/.rels": ROOT_RELS,
        "xl/workbook.xml": f'{DECL}<x:workbook xmlns:x="{MAIN}" xmlns:r="{REL}"><x:sheets><x:sheet name="Gradebook" sheetId="1" r:id="rId1"/></x:sheets></x:workbook>',
        "xl/_rels/workbook.xml.rels": rels([("rId1", f"{REL}/worksheet", "worksheets/sheet1.xml"), ("rId2", f"{REL}/sharedStrings", "sharedStrings.xml")]),
        "xl/sharedStrings.xml": f'{DECL}<x:sst xmlns:x="{MAIN}" count="2" uniqueCount="2"><x:si><x:t>Student</x:t></x:si><x:si><x:t>Score</x:t></x:si></x:sst>',
        "xl/worksheets/sheet1.xml": f'{DECL}<x:worksheet xmlns:x="{MAIN}"><x:sheetData><x:row r="1"><x:c r="A1" t="s"><x:v>0</x:v></x:c><x:c r="B1" t="s"><x:v>1</x:v></x:c></x:row><x:row r="2"><x:c r="A2" t="inlineStr"><x:is><x:t>Avery</x:t></x:is></x:c><x:c r="B2"><x:v>91</x:v></x:c></x:row><x:row r="3"><x:c r="B3"><x:f>AVERAGE(B2:B2)</x:f><x:v>91</x:v></x:c></x:row></x:sheetData></x:worksheet>',
    }
    return parts


def no_cell_references():
    """Rows and cells without `r` attributes (allowed by ECMA-376; some exporters omit them)."""
    rows = "<row><c><v>1</v></c><c><v>2</v></c><c><f>A1+B1</f><v>3</v></c></row><row><c t=\"inlineStr\"><is><t>x</t></is></c></row>"
    return simple({"Sheet1": sheet_xml(rows)}, styles=minimal_styles())


def rows_without_references():
    """Rows without `r` whose cells do carry `r`. Readers that index cells by
    their own `r` open it; a later insert would have to guess the row."""
    rows = '<row><c r="A1"><v>1</v></c><c r="B1"><v>2</v></c></row><row><c r="A2"><f>A1+B1</f><v>3</v></c></row>'
    return simple({"Sheet1": sheet_xml(rows, dimension="A1:B2")}, styles=minimal_styles())


def errors_as_values():
    errors = ["#N/A", "#DIV/0!", "#REF!", "#NAME?", "#NULL!", "#NUM!", "#VALUE!", "#SPILL!", "#CALC!"]
    rows = "".join(f'<row r="{i}"><c r="A{i}" t="e"><v>{e}</v></c><c r="B{i}" t="b"><v>{i % 2}</v></c></row>' for i, e in enumerate(errors, 1))
    rows += f'<row r="{len(errors) + 1}"><c r="A{len(errors) + 1}" t="e"><f>1/0</f><v>#DIV/0!</v></c><c r="B{len(errors) + 1}" t="b"><f>B1</f><v>1</v></c></row>'
    return simple({"Errors": sheet_xml(rows, dimension=f"A1:B{len(errors) + 1}")}, styles=minimal_styles())


def newer_error_values():
    """Error values newer than the v1 error set (data types, linked data, Python in Excel)."""
    errors = ["#GETTING_DATA", "#FIELD!", "#BLOCKED!", "#CONNECT!", "#BUSY!", "#UNKNOWN!"]
    rows = "".join(f'<row r="{i}"><c r="A{i}" t="e"><v>{e}</v></c><c r="B{i}"><v>{i}</v></c></row>' for i, e in enumerate(errors, 1))
    return simple({"Errors": sheet_xml(rows, dimension=f"A1:B{len(errors)}")}, styles=minimal_styles())


def dropdown_rules():
    """Data-validation shapes the libraries rarely write: every error style, a
    sheet-scoped name, quoted commas, numeric lists, text length, custom and a
    formula-built list."""
    rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>Field</t></is></c><c r="B1" t="inlineStr"><is><t>Entry</t></is></c></row>'
    rows += '<row r="2"><c r="A2" t="inlineStr"><is><t>Stop list</t></is></c><c r="B2" t="inlineStr"><is><t>Yes</t></is></c></row>'
    lists = '<row r="1"><c r="A1"><v>10</v></c><c r="B1" t="inlineStr"><is><t>Alpha</t></is></c></row>'
    lists += '<row r="2"><c r="A2"><v>20</v></c><c r="B2" t="inlineStr"><is><t>Beta</t></is></c></row>'
    lists += '<row r="3"><c r="A3"><v>30</v></c></row>'
    validations = (
        '<dataValidations count="10">'
        '<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="B2"><formula1>"Yes,No"</formula1></dataValidation>'
        '<dataValidation type="list" errorStyle="warning" showErrorMessage="1" sqref="B3"><formula1>"Yes,No"</formula1></dataValidation>'
        '<dataValidation type="list" errorStyle="information" showErrorMessage="1" sqref="B4"><formula1>"Yes,No"</formula1></dataValidation>'
        '<dataValidation type="list" showErrorMessage="1" sqref="B5"><formula1>Codes</formula1></dataValidation>'
        '<dataValidation type="list" showErrorMessage="1" sqref="B6"><formula1>"Small ""S"",Large ""L"""</formula1></dataValidation>'
        '<dataValidation type="list" showErrorMessage="1" sqref="B7"><formula1>Levels!$A$1:$A$3</formula1></dataValidation>'
        '<dataValidation type="textLength" operator="lessThanOrEqual" showErrorMessage="1" sqref="B8"><formula1>5</formula1></dataValidation>'
        '<dataValidation type="custom" showErrorMessage="1" sqref="B9"><formula1>ISNUMBER(B9)</formula1></dataValidation>'
        '<dataValidation type="list" showErrorMessage="1" sqref="B10"><formula1>OFFSET(Levels!$B$1,0,0,2,1)</formula1></dataValidation>'
        '<dataValidation type="decimal" operator="notBetween" showErrorMessage="1" sqref="B11 D11:E12"><formula1>-1</formula1><formula2>1</formula2></dataValidation>'
        '</dataValidations>'
    )
    names = ('<definedNames><definedName name="Codes" localSheetId="0">Levels!$B$1:$B$2</definedName>'
             '<definedName name="Codes">{"wrong","scope"}</definedName></definedNames>')
    return simple(
        {"Form": sheet_xml(rows, dimension="A1:B2", after=validations),
         "Levels": sheet_xml(lists, dimension="A1:B3")},
        styles=minimal_styles(), wb_extra=names,
    )


def pivot_fields():
    """A pivot with row, column and page fields and two data fields."""
    pcd_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheDefinition+xml"
    pt_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotTable+xml"
    header = ["Region", "Quarter", "Channel", "Units", "Revenue"]
    data = [("North", "Q1", "Web", 4, 40), ("North", "Q2", "Store", 6, 66), ("South", "Q1", "Web", 3, 27)]
    rows = '<row r="1">' + "".join(f'<c r="{chr(65 + i)}1" t="inlineStr"><is><t>{h}</t></is></c>' for i, h in enumerate(header)) + "</row>"
    for r, record in enumerate(data, 2):
        cells = ""
        for i, item in enumerate(record):
            ref = f"{chr(65 + i)}{r}"
            cells += f'<c r="{ref}"><v>{item}</v></c>' if isinstance(item, int) else f'<c r="{ref}" t="inlineStr"><is><t>{item}</t></is></c>'
        rows += f'<row r="{r}">{cells}</row>'
    out = '<row r="1"><c r="A1" t="inlineStr"><is><t>Channel</t></is></c><c r="B1" t="inlineStr"><is><t>(All)</t></is></c></row>'
    out += '<row r="3"><c r="A3" t="inlineStr"><is><t>Region</t></is></c><c r="B3" t="inlineStr"><is><t>Q1</t></is></c><c r="C3" t="inlineStr"><is><t>Q2</t></is></c></row>'
    out += '<row r="4"><c r="A4" t="inlineStr"><is><t>North</t></is></c><c r="B4"><v>4</v></c><c r="C4"><v>6</v></c></row>'
    out += '<row r="5"><c r="A5" t="inlineStr"><is><t>South</t></is></c><c r="B5"><v>3</v></c></row>'
    fields = "".join(f'<cacheField name="{h}" numFmtId="0"><sharedItems/></cacheField>' for h in header)
    return simple(
        {"Sales": sheet_xml(rows, dimension="A1:E4"), "Summary": sheet_xml(out, dimension="A1:C5")},
        styles=minimal_styles(),
        wb_extra='<pivotCaches><pivotCache cacheId="3" r:id="rId4"/></pivotCaches>',
        extra_wb_rels=[(f"{REL}/pivotCacheDefinition", "pivotCache/pivotCacheDefinition1.xml", False)],
        extra_overrides={"xl/pivotCache/pivotCacheDefinition1.xml": pcd_ct, "xl/pivotTables/pivotTable1.xml": pt_ct},
        sheet_rels={2: rels([("rId1", f"{REL}/pivotTable", "../pivotTables/pivotTable1.xml")])},
        extra_parts={
            "xl/pivotCache/pivotCacheDefinition1.xml": f'{DECL}<pivotCacheDefinition xmlns="{MAIN}" xmlns:r="{REL}" recordCount="3"><cacheSource type="worksheet"><worksheetSource ref="A1:E4" sheet="Sales"/></cacheSource><cacheFields count="5">{fields}</cacheFields></pivotCacheDefinition>',
            "xl/pivotTables/pivotTable1.xml": f'{DECL}<pivotTableDefinition xmlns="{MAIN}" name="RegionByQuarter" cacheId="3" dataCaption="Values"><location ref="A3:C5" firstHeaderRow="1" firstDataRow="1" firstDataCol="1" rowPageCount="1" colPageCount="1"/><pivotFields count="5"><pivotField axis="axisRow" showAll="0"/><pivotField axis="axisCol" showAll="0"/><pivotField axis="axisPage" showAll="0"/><pivotField dataField="1" showAll="0"/><pivotField dataField="1" showAll="0"/></pivotFields><rowFields count="1"><field x="0"/></rowFields><colFields count="2"><field x="1"/><field x="-2"/></colFields><pageFields count="1"><pageField fld="2" hier="-1"/></pageFields><dataFields count="2"><dataField name="Sum of Units" fld="3" baseField="0" baseItem="0"/><dataField name="Average Revenue" fld="4" subtotal="average" baseField="0" baseItem="0"/></dataFields></pivotTableDefinition>',
            "xl/pivotTables/_rels/pivotTable1.xml.rels": rels([("rId1", f"{REL}/pivotCacheDefinition", "../pivotCache/pivotCacheDefinition1.xml")]),
        },
    )


def text_edge_cases():
    """Text cells that must round-trip exactly: Text (@) format, quote prefix,
    leading zeros, a 32,767-character string, astral and combining characters,
    right-to-left text, rich runs with edge whitespace, and escaped controls."""
    styles = (
        f'{DECL}<styleSheet xmlns="{MAIN}">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" quotePrefix="1"/></cellXfs>'
        "</styleSheet>"
    )
    long_text = ("Synthetic long text " * 1700)[:32767]
    sst = [
        "00123", "=SUM(1,2)", "1E5", long_text, "Rocket \U0001F680 and e\u0301 and \u05E9\u05DC\u05D5\u05DD",
        "  padded both ends  ", "line one\nline two", "TRUE",
    ]
    rich = ('<si><r><rPr><b/></rPr><t>Bold</t></r><r><t xml:space="preserve"> then plain </t></r>'
            '<r><rPr><i/></rPr><t>italic</t></r></si>')
    rows = (
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" s="1" t="s"><v>0</v></c><c r="C1" s="1"><v>42</v></c><c r="D1" s="1" t="s"><v>2</v></c></row>'
        '<row r="2"><c r="A2" s="2" t="s"><v>1</v></c><c r="B2" s="2" t="s"><v>7</v></c><c r="C2" s="1" t="inlineStr"><is><t>000</t></is></c></row>'
        '<row r="3"><c r="A3" t="s"><v>3</v></c></row>'
        '<row r="4"><c r="A4" t="s"><v>4</v></c><c r="B4" t="s"><v>5</v></c><c r="C4" t="s"><v>6</v></c><c r="D4" t="s"><v>8</v></c></row>'
        '<row r="5"><c r="A5" t="inlineStr"><is><t>tab_x0009_and_x000D_return</t></is></c><c r="B5" s="1" t="s"><v>0</v></c></row>'
    )
    parts = simple({"Text": sheet_xml(rows, dimension="A1:D5")}, sst=sst, styles=styles)
    items = "".join(f'<si><t xml:space="preserve">{_esc(item)}</t></si>' for item in sst) + rich
    parts["xl/sharedStrings.xml"] = (
        f'{DECL}<sst xmlns="{MAIN}" count="{len(sst) + 1}" uniqueCount="{len(sst) + 1}">{items}</sst>'
    )
    return parts


def far_off_cell():
    rows = '<row r="1"><c r="A1"><v>1</v></c></row><row r="1048576"><c r="XFD1048576"><v>42</v></c></row>'
    return simple({"Sparse": sheet_xml(rows, dimension="A1:XFD1048576")}, styles=minimal_styles())


def threaded_comments():
    tc_ct = "application/vnd.ms-excel.threadedcomments+xml"
    person_ct = "application/vnd.ms-excel.person+xml"
    cm_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml"
    rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>Sample</t></is></c><c r="B1"><v>3.5</v></c></row>'
    sheet = sheet_xml(rows, dimension="A1:B1", after='<legacyDrawing r:id="rId3"/>')
    tid = "{6A2D0B53-1E3A-4C3B-9A51-0B5E1C6E9A11}"
    pid = "{3B9E2F7C-8D4A-4F6B-A1C2-7E5D9B0A1C22}"
    parts = simple(
        {"Lab": sheet},
        styles=minimal_styles(),
        extra_wb_rels=[("http://schemas.microsoft.com/office/2017/10/relationships/person", "persons/person.xml", False)],
        extra_overrides={
            "xl/threadedComments/threadedComment1.xml": tc_ct,
            "xl/persons/person.xml": person_ct,
            "xl/comments1.xml": cm_ct,
        },
        defaults={"rels": RELS_CT, "xml": "application/xml", "vml": "application/vnd.openxmlformats-officedocument.vmlDrawing"},
        sheet_rels={1: rels([
            ("rId1", "http://schemas.microsoft.com/office/2017/10/relationships/threadedComment", "../threadedComments/threadedComment1.xml"),
            ("rId2", f"{REL}/comments", "../comments1.xml"),
            ("rId3", f"{REL}/vmlDrawing", "../drawings/vmlDrawing1.vml"),
        ])},
        extra_parts={
            "xl/threadedComments/threadedComment1.xml": f'{DECL}<ThreadedComments xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"><threadedComment ref="B1" dT="2020-01-01T00:00:00.00" personId="{pid}" id="{tid}"><text>Check calibration</text></threadedComment></ThreadedComments>',
            "xl/persons/person.xml": f'{DECL}<personList xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"><person displayName="Lab Reviewer" id="{pid}" userId="reviewer" providerId="None"/></personList>',
            "xl/comments1.xml": f'{DECL}<comments xmlns="{MAIN}"><authors><author>tc={tid}</author></authors><commentList><comment ref="B1" authorId="0"><text><t>[Threaded comment] Check calibration</t></text></comment></commentList></comments>',
            "xl/drawings/vmlDrawing1.vml": '<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel"><v:shape type="#_x0000_t202"><x:ClientData ObjectType="Note"><x:Row>0</x:Row><x:Column>1</x:Column></x:ClientData></v:shape></xml>',
        },
    )
    return parts


def pivot_cache():
    pcd_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheDefinition+xml"
    pcr_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheRecords+xml"
    pt_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.pivotTable+xml"
    data_rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>Region</t></is></c><c r="B1" t="inlineStr"><is><t>Units</t></is></c></row>'
    for r, (region, units) in enumerate([("North", 10), ("South", 7), ("North", 5)], 2):
        data_rows += f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>{region}</t></is></c><c r="B{r}"><v>{units}</v></c></row>'
    pivot_rows = '<row r="3"><c r="A3" t="inlineStr"><is><t>Region</t></is></c><c r="B3" t="inlineStr"><is><t>Sum of Units</t></is></c></row><row r="4"><c r="A4" t="inlineStr"><is><t>North</t></is></c><c r="B4"><v>15</v></c></row><row r="5"><c r="A5" t="inlineStr"><is><t>South</t></is></c><c r="B5"><v>7</v></c></row>'
    parts = simple(
        {"Data": sheet_xml(data_rows, dimension="A1:B4"), "Pivot": sheet_xml(pivot_rows, dimension="A3:B5")},
        styles=minimal_styles(),
        wb_extra='<pivotCaches><pivotCache cacheId="1" r:id="rId4"/></pivotCaches>',
        extra_wb_rels=[(f"{REL}/pivotCacheDefinition", "pivotCache/pivotCacheDefinition1.xml", False)],
        extra_overrides={
            "xl/pivotCache/pivotCacheDefinition1.xml": pcd_ct,
            "xl/pivotCache/pivotCacheRecords1.xml": pcr_ct,
            "xl/pivotTables/pivotTable1.xml": pt_ct,
        },
        sheet_rels={2: rels([("rId1", f"{REL}/pivotTable", "../pivotTables/pivotTable1.xml")])},
        extra_parts={
            "xl/pivotCache/pivotCacheDefinition1.xml": f'{DECL}<pivotCacheDefinition xmlns="{MAIN}" xmlns:r="{REL}" r:id="rId1" refreshOnLoad="1" recordCount="3"><cacheSource type="worksheet"><worksheetSource ref="A1:B4" sheet="Data"/></cacheSource><cacheFields count="2"><cacheField name="Region" numFmtId="0"><sharedItems count="2"><s v="North"/><s v="South"/></sharedItems></cacheField><cacheField name="Units" numFmtId="0"><sharedItems containsSemiMixedTypes="0" containsString="0" containsNumber="1" containsInteger="1" minValue="5" maxValue="10"/></cacheField></cacheFields></pivotCacheDefinition>',
            "xl/pivotCache/_rels/pivotCacheDefinition1.xml.rels": rels([("rId1", f"{REL}/pivotCacheRecords", "pivotCacheRecords1.xml")]),
            "xl/pivotCache/pivotCacheRecords1.xml": f'{DECL}<pivotCacheRecords xmlns="{MAIN}" count="3"><r><x v="0"/><n v="10"/></r><r><x v="1"/><n v="7"/></r><r><x v="0"/><n v="5"/></r></pivotCacheRecords>',
            "xl/pivotTables/pivotTable1.xml": f'{DECL}<pivotTableDefinition xmlns="{MAIN}" name="PivotTable1" cacheId="1" dataCaption="Values"><location ref="A3:B5" firstHeaderRow="1" firstDataRow="1" firstDataCol="1"/><pivotFields count="2"><pivotField axis="axisRow" showAll="0"><items count="3"><item x="0"/><item x="1"/><item t="default"/></items></pivotField><pivotField dataField="1" showAll="0"/></pivotFields><rowFields count="1"><field x="0"/></rowFields><dataFields count="1"><dataField name="Sum of Units" fld="1" baseField="0" baseItem="0"/></dataFields></pivotTableDefinition>',
            "xl/pivotTables/_rels/pivotTable1.xml.rels": rels([("rId1", f"{REL}/pivotCacheDefinition", "../pivotCache/pivotCacheDefinition1.xml")]),
        },
    )
    return parts


def external_link():
    el_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml"
    rows = '<row r="1"><c r="A1"><f>[1]Rates!$B$2*2</f><v>0.1</v></c><c r="B1"><v>4</v></c><c r="C1"><f>B1*2</f><v>8</v></c></row>'
    parts = simple(
        {"Forecast": sheet_xml(rows, dimension="A1:C1")},
        styles=minimal_styles(),
        wb_extra='<externalReferences><externalReference r:id="rId3"/></externalReferences>',
        extra_wb_rels=[(f"{REL}/externalLink", "externalLinks/externalLink1.xml", False)],
        extra_overrides={"xl/externalLinks/externalLink1.xml": el_ct},
        extra_parts={
            "xl/externalLinks/externalLink1.xml": f'{DECL}<externalLink xmlns="{MAIN}" xmlns:r="{REL}"><externalBook r:id="rId1"><sheetNames><sheetName val="Rates"/></sheetNames><sheetDataSet><sheetData sheetId="0"><row r="2"><cell r="B2"><v>0.05</v></cell></row></sheetData></sheetDataSet></externalBook></externalLink>',
            "xl/externalLinks/_rels/externalLink1.xml.rels": rels([("rId1", f"{REL}/externalLinkPath", "rates.xlsx")], {"rId1"}),
        },
    )
    return parts


def chartsheet():
    cs_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.chartsheet+xml"
    rows = '<row r="1"><c r="A1"><v>1</v></c><c r="B1"><v>4</v></c></row><row r="2"><c r="A2"><v>2</v></c><c r="B2"><v>9</v></c></row>'
    parts = simple({"Readings": sheet_xml(rows, dimension="A1:B2")}, styles=minimal_styles())
    wb = workbook_xml(["Readings", "Chart1"])
    parts["xl/workbook.xml"] = wb
    wb_rels = [
        ("rId1", f"{REL}/worksheet", "worksheets/sheet1.xml"),
        ("rId2", f"{REL}/chartsheet", "chartsheets/sheet1.xml"),
        ("rId3", f"{REL}/styles", "styles.xml"),
    ]
    parts["xl/_rels/workbook.xml.rels"] = rels(wb_rels)
    parts["[Content_Types].xml"] = content_types({
        "xl/workbook.xml": WB_CT, "xl/worksheets/sheet1.xml": WS_CT, "xl/styles.xml": STY_CT,
        "xl/chartsheets/sheet1.xml": cs_ct,
    })
    parts["xl/chartsheets/sheet1.xml"] = f'{DECL}<chartsheet xmlns="{MAIN}" xmlns:r="{REL}"><sheetPr/><sheetViews><sheetView workbookViewId="0"/></sheetViews></chartsheet>'
    return parts


def macro_content_in_xlsx():
    """A macro-enabled workbook content type and vbaProject.bin inside an `.xlsx` name."""
    parts = minimal_no_optional_parts()
    parts["[Content_Types].xml"] = content_types(
        {"xl/workbook.xml": "application/vnd.ms-excel.sheet.macroEnabled.main+xml", "xl/worksheets/sheet1.xml": WS_CT},
        {"rels": RELS_CT, "xml": "application/xml", "bin": "application/vnd.ms-office.vbaProject"},
    )
    parts["xl/vbaProject.bin"] = b"synthetic placeholder, not a VBA project"
    return parts


def excel_like_markup():
    """Excel-style markup: mc:Ignorable, x14ac row attributes, xr uids, calcChain, extLst."""
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    x14ac = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
    xr = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
    rows = (
        '<row r="1" spans="1:3" x14ac:dyDescent="0.25"><c r="A1"><v>100</v></c><c r="B1"><v>0.07</v></c><c r="C1"><f>A1*(1+B1)</f><v>107</v></c></row>'
        '<row r="2" spans="1:3" x14ac:dyDescent="0.25"><c r="C2"><f>C1*(1+B1)</f><v>114.49</v></c></row>'
    )
    sheet = (
        f'{DECL}<worksheet xmlns="{MAIN}" xmlns:r="{REL}" xmlns:mc="{mc}" mc:Ignorable="x14ac xr" '
        f'xmlns:x14ac="{x14ac}" xmlns:xr="{xr}" xr:uid="{{00000000-0001-0000-0000-000000000000}}">'
        '<dimension ref="A1:C2"/><sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15" x14ac:dyDescent="0.25"/>'
        f"<sheetData>{rows}</sheetData>"
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        '<extLst><ext uri="{00000000-0000-0000-0000-000000000000}"><unknownFutureThing/></ext></extLst>'
        "</worksheet>"
    )
    calc_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"
    parts = simple(
        {"Growth": sheet},
        styles=minimal_styles(),
        wb_pr='<fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="27000"/><workbookPr defaultThemeVersion="166925"/>',
        wb_extra='<calcPr calcId="191029"/>',
        extra_wb_rels=[(f"{REL}/calcChain", "calcChain.xml", False)],
        extra_overrides={"xl/calcChain.xml": calc_ct,
                         "docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
                         "docProps/app.xml": "application/vnd.openxmlformats-officedocument.extended-properties+xml"},
        extra_parts={
            "xl/calcChain.xml": f'{DECL}<calcChain xmlns="{MAIN}"><c r="C1" i="1"/><c r="C2"/></calcChain>',
            "docProps/core.xml": f'{DECL}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>corpus</dc:creator></cp:coreProperties>',
            "docProps/app.xml": f'{DECL}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Microsoft Excel</Application></Properties>',
        },
    )
    parts["_rels/.rels"] = rels([
        ("rId1", f"{REL}/officeDocument", "xl/workbook.xml"),
        ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", "docProps/core.xml"),
        ("rId3", f"{REL}/extended-properties", "docProps/app.xml"),
    ])
    return parts


def google_sheets_like():
    """Google Sheets export quirks: _xlnm._FilterDatabase hidden name, sheetPr, no docProps/calcChain, t="str" formula results."""
    rows = (
        '<row r="1"><c r="A1" s="1" t="s"><v>0</v></c><c r="B1" s="1" t="s"><v>1</v></c><c r="C1" s="1" t="s"><v>2</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>3</v></c><c r="B2"><v>12</v></c><c r="C2" t="str"><f>CONCATENATE(A2," x",B2)</f><v>Widget x12</v></c></row>'
        '<row r="3"><c r="A3" t="s"><v>4</v></c><c r="B3"><v>4</v></c><c r="C3" t="str"><f>CONCATENATE(A3," x",B3)</f><v>Gadget x4</v></c></row>'
    )
    sheet = (
        f'{DECL}<worksheet xmlns="{MAIN}" xmlns:r="{REL}"><sheetPr><outlinePr summaryBelow="0" summaryRight="0"/></sheetPr>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetFormatPr customHeight="1" defaultColWidth="12.63" defaultRowHeight="15.75"/>'
        f"<sheetData>{rows}</sheetData><autoFilter ref=\"A1:C3\"/></worksheet>"
    )
    styles = minimal_styles().replace(
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>',
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment readingOrder="0" shrinkToFit="0" vertical="bottom" wrapText="0"/></xf><xf borderId="0" fillId="0" fontId="0" numFmtId="0" xfId="0" applyAlignment="1" applyFont="1"><alignment readingOrder="0"/></xf></cellXfs>',
    )
    return simple(
        {"Inventory": sheet},
        sst=["Item", "Qty", "Label", "Widget", "Gadget"],
        styles=styles,
        wb_pr="<workbookPr/>",
        wb_extra='<definedNames><definedName hidden="1" localSheetId="0" name="_xlnm._FilterDatabase">Inventory!$A$1:$C$3</definedName></definedNames><calcPr/>',
    )


def numbers_like():
    """Apple Numbers-style quirks: absolute rel targets, a sheet named with spaces, explicit t="n", no calcChain."""
    rows = (
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="n"><v>2.5</v></c></row>'
        '<row r="3"><c r="A3" t="s"><v>3</v></c><c r="B3" t="n"><f>B2*4</f><v>10</v></c></row>'
    )
    parts = simple(
        {"Sheet 1 - Table 1": sheet_xml(rows, dimension="A1:B3")},
        sst=["Measure", "Value", "Length (m)", "Total"],
        styles=minimal_styles(),
    )
    parts["xl/_rels/workbook.xml.rels"] = rels([
        ("rId1", f"{REL}/worksheet", "/xl/worksheets/sheet1.xml"),
        ("rId2", f"{REL}/styles", "/xl/styles.xml"),
        ("rId3", f"{REL}/sharedStrings", "/xl/sharedStrings.xml"),
    ])
    return parts


def libreoffice_like():
    """LibreOffice-style quirks: aca attributes on formulas, [$-409] formats, empty-string cache, booleans."""
    styles = (
        f'{DECL}<styleSheet xmlns="{MAIN}"><numFmts count="1"><numFmt numFmtId="164" formatCode="[$-409]MMMM\\ D&quot;, &quot;YYYY;@"/></numFmts>'
        '<fonts count="1"><font><sz val="10"/><name val="Arial"/><family val="2"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border diagonalUp="false" diagonalDown="false"><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="164" fontId="0" fillId="0" borderId="0" applyFont="true" applyBorder="true" applyAlignment="true" applyProtection="true"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyFont="false" applyBorder="false" applyAlignment="false" applyProtection="false"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyFont="false" applyBorder="false" applyAlignment="false" applyProtection="false"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    )
    rows = (
        '<row r="1" customFormat="false" ht="12.8" hidden="false" customHeight="false" outlineLevel="0" collapsed="false">'
        '<c r="A1" s="1" t="n"><v>44197</v></c><c r="B1" s="0" t="b"><v>1</v></c><c r="C1" s="0" t="str"><f aca="false">IF(B1,"","no")</f><v></v></c></row>'
        '<row r="2" customFormat="false" ht="12.8" hidden="false" customHeight="false" outlineLevel="0" collapsed="false">'
        '<c r="A2" s="1" t="n"><f aca="false">A1+30</f><v>44227</v></c></row>'
    )
    sheet = (
        f'{DECL}<worksheet xmlns="{MAIN}" xmlns:r="{REL}"><sheetPr filterMode="false"><pageSetUpPr fitToPage="false"/></sheetPr>'
        '<dimension ref="A1:C2"/><sheetViews><sheetView showFormulas="false" showGridLines="true" workbookViewId="0"/></sheetViews>'
        f'<sheetFormatPr defaultColWidth="11.53515625" defaultRowHeight="12.8" zeroHeight="false" outlineLevelRow="0" outlineLevelCol="0"/><sheetData>{rows}</sheetData></worksheet>'
    )
    return simple({"Schedule": sheet}, styles=styles,
                  wb_pr='<fileVersion appName="Calc"/><workbookPr backupFile="false" showObjects="all" date1904="false"/>',
                  wb_extra='<calcPr iterateCount="100" refMode="A1" iterate="false" iterateDelta="0.001"/>')


def bom_and_stored():
    parts = minimal_no_optional_parts()
    parts["xl/worksheets/sheet1.xml"] = "\ufeff" + parts["xl/worksheets/sheet1.xml"]
    return parts


def dynamic_array_cached():
    """A dynamic-array (spill) formula with its cached spill range, as Excel writes it."""
    md_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheetMetadata+xml"
    rows = "".join(f'<row r="{r}"><c r="A{r}"><v>{v}</v></c>' + (
        f'<c r="C1" cm="1"><f t="array" ref="C1:C4">_xlfn._xlws.SORT(A1:A4)</f><v>{sorted([7, 3, 9, 1])[0]}</v></c>' if r == 1
        else f'<c r="C{r}"><v>{sorted([7, 3, 9, 1])[r - 1]}</v></c>') + "</row>"
        for r, v in enumerate([7, 3, 9, 1], 1))
    metadata = (
        f'{DECL}<metadata xmlns="{MAIN}" xmlns:xda="http://schemas.microsoft.com/office/spreadsheetml/2017/dynamicarray">'
        '<metadataTypes count="1"><metadataType name="XLDAPR" minSupportedVersion="120000" copy="1" pasteAll="1" pasteValues="1" merge="1" splitFirst="1" rowColShift="1" clearFormats="1" clearComments="1" assign="1" coerce="1" cellMeta="1"/></metadataTypes>'
        '<futureMetadata name="XLDAPR" count="1"><bk><extLst><ext uri="{bdbb8cdc-fa1e-496e-a857-3c3f30c029c3}"><xda:dynamicArrayProperties fDynamic="1" fCollapsed="0"/></ext></extLst></bk></futureMetadata>'
        '<cellMetadata count="1"><bk><rc t="1" v="0"/></bk></cellMetadata></metadata>'
    )
    return simple({"Ranked": sheet_xml(rows, dimension="A1:C4")}, styles=minimal_styles(),
                  extra_wb_rels=[("http://schemas.openxmlformats.org/officeDocument/2006/relationships/sheetMetadata", "metadata.xml", False)],
                  extra_overrides={"xl/metadata.xml": md_ct}, extra_parts={"xl/metadata.xml": metadata})


def rich_text_phonetic():
    sst = (
        f'{DECL}<sst xmlns="{MAIN}" count="3" uniqueCount="3">'
        '<si><r><rPr><b/><sz val="11"/><color rgb="FFFF0000"/><rFont val="Calibri"/></rPr><t>Warning:</t></r><r><rPr><sz val="11"/><rFont val="Calibri"/></rPr><t xml:space="preserve"> recheck totals</t></r></si>'
        '<si><t>東京</t><rPh sb="0" eb="2"><t>トウキョウ</t></rPh><phoneticPr fontId="1"/></si>'
        '<si><t xml:space="preserve">  padded  </t></si></sst>'
    )
    rows = '<row r="1"><c r="A1" t="s"><v>0</v></c></row><row r="2"><c r="A2" t="s"><v>1</v></c></row><row r="3"><c r="A3" t="s"><v>2</v></c></row>'
    parts = simple({"Notes": sheet_xml(rows, dimension="A1:A3")}, styles=minimal_styles())
    parts["xl/sharedStrings.xml"] = sst
    parts["[Content_Types].xml"] = content_types({"xl/workbook.xml": WB_CT, "xl/worksheets/sheet1.xml": WS_CT, "xl/styles.xml": STY_CT, "xl/sharedStrings.xml": SST_CT})
    parts["xl/_rels/workbook.xml.rels"] = rels([
        ("rId1", f"{REL}/worksheet", "worksheets/sheet1.xml"), ("rId2", f"{REL}/styles", "styles.xml"),
        ("rId3", f"{REL}/sharedStrings", "sharedStrings.xml")])
    return parts


def escaped_control_chars():
    """Shared strings with _x000D_ escapes, tabs and newlines."""
    parts = rich_text_phonetic()
    parts["xl/sharedStrings.xml"] = (
        f'{DECL}<sst xmlns="{MAIN}" count="3" uniqueCount="3"><si><t>line one_x000D_\nline two</t></si>'
        '<si><t xml:space="preserve">tab\there</t></si><si><t>literal _x005F_x0041_ text</t></si></sst>'
    )
    return parts


def stale_dimension():
    """A dimension element that does not cover the stored cells."""
    rows = '<row r="1"><c r="A1"><v>1</v></c></row><row r="40"><c r="H40"><v>2</v></c></row>'
    return simple({"Survey": sheet_xml(rows, dimension="A1")}, styles=minimal_styles())


def data_table_formula():
    """A what-if data table ({=TABLE()}) region."""
    rows = (
        '<row r="1"><c r="A1"><v>0.05</v></c><c r="B1"><f>A1*1000</f><v>50</v></c></row>'
        '<row r="2"><c r="A2"><v>0.04</v></c><c r="B2"><f t="dataTable" ref="B2:B3" dt2D="0" dtr="0" r1="A1"/><v>40</v></c></row>'
        '<row r="3"><c r="A3"><v>0.06</v></c><c r="B3"><v>60</v></c></row>'
    )
    return simple({"WhatIf": sheet_xml(rows, dimension="A1:B3")}, styles=minimal_styles())


def uppercase_extension_name():
    """A worksheet part with a non-standard name and directory."""
    parts = minimal_no_optional_parts()
    body = parts.pop("xl/worksheets/sheet1.xml")
    parts["xl/tabs/Data_Tab.XML"] = body
    parts["xl/_rels/workbook.xml.rels"] = rels([("rId1", f"{REL}/worksheet", "./tabs/../tabs/Data_Tab.XML")])
    parts["[Content_Types].xml"] = content_types({"xl/workbook.xml": WB_CT, "xl/tabs/Data_Tab.XML": WS_CT})
    return parts


BUILDERS = {
    "hb_minimal_no_optional_parts": (minimal_no_optional_parts, ["missing-parts", "no-styles", "no-sst", "no-dimension", "inline-strings", "booleans"]),
    "hb_inline_strings_rich": (inline_strings_only, ["inline-strings", "rich-text", "cached-values"]),
    "hb_absolute_targets": (absolute_targets, ["absolute-targets", "missing-parts"]),
    "hb_shared_formulas": (shared_formulas, ["shared-formulas", "cached-values"]),
    "hb_strict_ooxml": (strict_ooxml, ["strict-ooxml"]),
    "hb_prefixed_spreadsheetml": (prefixed_spreadsheetml, ["prefixed-namespace", "shared-strings"]),
    "hb_no_cell_references": (no_cell_references, ["no-cell-refs"]),
    "hb_rows_without_references": (rows_without_references, ["no-row-refs"]),
    "hb_errors_as_values": (errors_as_values, ["errors", "booleans", "cached-values"]),
    "hb_errors_newer_values": (newer_error_values, ["errors", "newer-errors"]),
    "hb_far_off_cell": (far_off_cell, ["sparse", "xfd1048576"]),
    "hb_threaded_comments": (threaded_comments, ["threaded-comments", "comments", "vml"]),
    "hb_pivot_cache": (pivot_cache, ["pivot"]),
    "hb_pivot_fields": (pivot_fields, ["pivot", "pivot-fields", "page-fields"]),
    "hb_text_edge_cases": (text_edge_cases, ["text", "text-format", "quote-prefix", "leading-zeros", "long-strings", "unicode", "rtl", "rich-text", "whitespace", "escaped-chars"]),
    "hb_dropdown_rules": (dropdown_rules, ["data-validation", "dropdowns", "error-styles", "sheet-scoped-names", "text-length", "custom-validation", "formula-list"]),
    "hb_external_link": (external_link, ["external-links"]),
    "hb_macro_content_in_xlsx": (macro_content_in_xlsx, ["vba"]),
    "hb_excel_like_markup": (excel_like_markup, ["excel-markup", "mc-ignorable", "calcchain", "extlst", "docprops"]),
    "hb_google_sheets_like": (google_sheets_like, ["google-sheets", "defined-names", "autofilter", "str-formula"]),
    "hb_numbers_like": (numbers_like, ["numbers", "absolute-targets", "sheet-name-spaces"]),
    "hb_libreoffice_like": (libreoffice_like, ["libreoffice", "number-formats", "dates", "booleans", "empty-string-cache"]),
    "hb_bom_part": (bom_and_stored, ["xml-bom", "stored-zip"]),
    "hb_dynamic_array_cached": (dynamic_array_cached, ["dynamic-array", "spill", "metadata"]),
    "hb_rich_text_phonetic": (rich_text_phonetic, ["rich-text", "phonetic", "unicode", "whitespace"]),
    "hb_escaped_control_chars": (escaped_control_chars, ["escaped-chars", "shared-strings"]),
    "hb_stale_dimension": (stale_dimension, ["stale-dimension", "sparse"]),
    "hb_data_table_formula": (data_table_formula, ["data-table"]),
    "hb_odd_part_name": (uppercase_extension_name, ["odd-part-names", "dot-segments"]),
}
STORED = {"hb_bom_part"}


def build_all(folder: Path) -> dict[str, list[str]]:
    made = {}
    for name, (builder, categories) in BUILDERS.items():
        compression = zipfile.ZIP_STORED if name in STORED else zipfile.ZIP_DEFLATED
        write_package(folder / f"{name}.xlsx", builder(), compression=compression)
        made[f"{name}.xlsx"] = categories
    return made
