"""More hand-built OOXML shapes for the robustness corpus (standard library only).

``handbuilt.py`` holds the original set; this module adds the real-world shapes
found later, built with the same helpers so every file is synthetic,
general-purpose and byte-stable across regenerations. ``generate_corpus.py``
writes both sets under the ``handbuilt`` writer.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import handbuilt as hb
from handbuilt import minimal_styles, sheet_xml, simple


def dropdown_formulas():
    """Drop-down cells that already hold formulas, as Excel saves them: each
    result was in the list when it was entered. One result has since drifted
    out of its list on recalculation, which Excel never re-checks."""
    rows = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Choice</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Source</t></is></c></row>'
        '<row r="2"><c r="A2" t="str"><f>B2</f><v>Yes</v></c>'
        '<c r="B2" t="inlineStr"><is><t>Yes</t></is></c></row>'
        '<row r="3"><c r="A3" t="str"><f>B3</f><v>No</v></c>'
        '<c r="B3" t="inlineStr"><is><t>Maybe</t></is></c></row>'
        '<row r="4"><c r="A4"><f>2*B4</f><v>4</v></c><c r="B4"><v>2</v></c></row>'
    )
    validations = (
        '<dataValidations count="2">'
        '<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="A2:A3">'
        '<formula1>"Yes,No"</formula1></dataValidation>'
        '<dataValidation type="whole" operator="between" showErrorMessage="1" sqref="A4">'
        '<formula1>1</formula1><formula2>10</formula2></dataValidation>'
        '</dataValidations>'
    )
    return simple({"Choices": sheet_xml(rows, dimension="A1:B4", after=validations)},
                  styles=minimal_styles())


def styles_with(xfs: list[str], num_fmts: str = "") -> str:
    """A minimal style sheet whose cellXfs are ``xfs`` (xf 0 should be General)."""
    return (
        f'{hb.DECL}<styleSheet xmlns="{hb.MAIN}">{num_fmts}'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
        "</styleSheet>"
    )


def text_columns():
    """Text (@) set on whole columns and rows, the way a template reserves a
    column for codes: cells typed later into column A, or anywhere in row 5,
    must stay text; row 6's own number format overrides column A."""
    xfs = [
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>',
        '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>',
        '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>',
    ]
    cols = '<cols><col min="1" max="1" width="12" style="1" customWidth="1"/><col min="3" max="4" width="10" style="2" customWidth="1"/></cols>'
    rows = (
        '<row r="1"><c r="A1" s="1" t="inlineStr"><is><t>Code</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Name</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>Amount</t></is></c></row>'
        '<row r="2"><c r="A2" s="1" t="inlineStr"><is><t>00017</t></is></c>'
        '<c r="B2" t="inlineStr"><is><t>Widget</t></is></c><c r="C2" s="2"><v>3.5</v></c></row>'
        '<row r="3"><c r="A3"><v>42</v></c><c r="B3" t="inlineStr"><is><t>Number in a text column</t></is></c></row>'
        '<row r="5" s="1" customFormat="1"><c r="B5" s="1" t="inlineStr"><is><t>Text row</t></is></c></row>'
        '<row r="6" s="2" customFormat="1"><c r="B6" s="2"><v>1</v></c></row>'
    )
    return simple({"Codes": sheet_xml(rows, dimension="A1:C6", before=cols)}, styles=styles_with(xfs))
# ---------------------------------------------------------------- scale


def _col(n: int) -> str:
    name = ""
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def wide_sheet():
    """A sheet as wide as Excel allows: cells across the first 300 columns, a
    sparse band out to XFD, and a formula that reads the last column."""
    first = "".join(f'<c r="{_col(c)}1"><v>{c}</v></c>' for c in range(1, 301))
    first += '<c r="XFC1"><v>16383</v></c><c r="XFD1"><v>16384</v></c>'
    second = '<c r="A2"><f>XFD1-XFC1</f><v>1</v></c><c r="B2"><f>SUM(A1:KN1)</f><v>45150</v></c>'
    second += '<c r="XFD2" t="inlineStr"><is><t>last column</t></is></c>'
    rows = f'<row r="1" spans="1:16384">{first}</row><row r="2">{second}</row>'
    return simple({"Wide": sheet_xml(rows, dimension="A1:XFD2")}, styles=minimal_styles())


def many_sheets():
    """210 worksheets, as large exports and per-period templates produce, with
    a summary sheet that reads across them."""
    sheets = {}
    for index in range(1, 210):
        sheets[f"P{index:03d}"] = sheet_xml(
            f'<row r="1"><c r="A1"><v>{index}</v></c><c r="B1" t="inlineStr"><is><t>period {index}</t></is></c></row>',
            dimension="B1",
        )
    total = sum(range(1, 210))
    sheets["Summary"] = sheet_xml(
        f'<row r="1"><c r="A1"><f>P001!A1+P209!A1</f><v>{1 + 209}</v></c>'
        f"<c r=\"B1\"><f>'P105'!A1*2</f><v>210</v></c><c r=\"C1\"><v>{total}</v></c></row>",
        dimension="A1:C1",
    )
    return simple(sheets, styles=minimal_styles())


def huge_shared_strings():
    """30,000 distinct shared strings (some long, some repeated by many cells),
    near the scale of a large product or address list."""
    strings = [f"item-{index:05d}" for index in range(30000)]
    strings[7] = "x" * 32767  # Excel's per-cell text limit
    strings[8] = "Ünïcödé ✓ 漢字 " * 50
    rows = []
    for row in range(1, 15001):
        rows.append(
            f'<row r="{row}"><c r="A{row}" t="s"><v>{row - 1}</v></c>'
            f'<c r="B{row}" t="s"><v>{29999 - (row - 1)}</v></c></row>'
        )
    rows.append('<row r="15001"><c r="A15001" t="s"><v>7</v></c><c r="B15001" t="s"><v>8</v></c></row>')
    return simple({"Strings": sheet_xml("".join(rows), dimension="A1:B15001")}, sst=strings,
                  styles=minimal_styles())


def inline_and_shared_strings():
    """Inline strings, shared strings, formula strings and rich runs mixed in
    the same rows, as writers that stream some cells and pool others do."""
    rows = (
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>inline one</t></is></c>'
        '<c r="C1" t="s"><v>1</v></c><c r="D1" t="inlineStr"><is><r><t>rich </t></r><r><rPr><b/></rPr><t>inline</t></r></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t xml:space="preserve">  padded  </t></is></c>'
        '<c r="B2" t="s"><v>2</v></c><c r="C2" t="str"><f>A1&amp;B1</f><v>sharedinline one</v></c>'
        '<c r="D2" t="inlineStr"><is><t></t></is></c></row>'
    )
    return simple({"Mixed": sheet_xml(rows, dimension="A1:D2")}, sst=["shared", "shared again", "shared 2"],
                  styles=minimal_styles())


# ------------------------------------------------------- styles and layout

THEME_CT = "application/vnd.openxmlformats-officedocument.theme+xml"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _theme() -> str:
    colors = "".join(
        f"<a:{name}><a:srgbClr val=\"{value}\"/></a:{name}>"
        for name, value in [("dk1", "000000"), ("lt1", "FFFFFF"), ("dk2", "1F2937"), ("lt2", "F3F4F6"),
                            ("accent1", "2563EB"), ("accent2", "DC2626"), ("accent3", "16A34A"),
                            ("accent4", "CA8A04"), ("accent5", "7C3AED"), ("accent6", "0891B2"),
                            ("hlink", "1D4ED8"), ("folHlink", "6D28D9")]
    )
    return (
        f'{hb.DECL}<a:theme xmlns:a="{A_NS}" name="Synthetic"><a:themeElements>'
        f'<a:clrScheme name="Synthetic">{colors}</a:clrScheme>'
        '<a:fontScheme name="Synthetic"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
        '<a:fmtScheme name="Synthetic"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
        '<a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
        '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>'
        '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
        '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>'
        "</a:themeElements></a:theme>"
    )


def many_styles():
    """4,000 cell formats (the size of a workbook whose styles were copied in
    from many sources), custom and locale number formats, theme and indexed
    colors with tints."""
    formats = [
        (164, "#,##0.00\\ [$€-1]"), (165, "[$-409]mmmm d, yyyy"), (166, "[$-F800]dddd\\,\\ mmmm\\ dd\\,\\ yyyy"),
        (167, "0.00%;[Red]\\-0.00%"), (168, '_-* #,##0.00\\ "kr"_-;\\-* #,##0.00\\ "kr"_-;_-* "-"??\\ "kr"_-;_-@_-'),
        (169, "[$¥-411]#,##0"), (170, "[<=9999999]###\\-####;\\(###\\)\\ ###\\-####"), (171, "yyyy\\-mm\\-dd\\Thh:mm:ss"),
        (172, "#\\ ?/?"), (173, "[h]:mm:ss.000"),
    ]
    num_fmts = f'<numFmts count="{len(formats)}">' + "".join(
        f'<numFmt numFmtId="{i}" formatCode="{hb._esc(code).replace(chr(34), "&quot;")}"/>' for i, code in formats
    ) + "</numFmts>"
    fonts = (
        '<fonts count="4"><font><sz val="11"/><color theme="1"/><name val="Calibri"/><scheme val="minor"/></font>'
        '<font><b/><sz val="11"/><color theme="4" tint="-0.249977111117893"/><name val="Calibri"/></font>'
        '<font><i/><sz val="9"/><color indexed="10"/><name val="Arial"/></font>'
        '<font><u/><sz val="11"/><color rgb="FF7C3AED"/><name val="Calibri"/></font></fonts>'
    )
    fills = (
        '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor theme="5" tint="0.79998168889431442"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><gradientFill degree="90"><stop position="0"><color theme="0"/></stop><stop position="1"><color theme="4"/></stop></gradientFill></fill></fills>'
    )
    xfs = []
    ids = [0, 2, 4, 10, 14, 49] + [i for i, _ in formats]
    for index in range(4000):
        xfs.append(
            f'<xf numFmtId="{ids[index % len(ids)]}" fontId="{index % 4}" fillId="{index % 4}" borderId="0" xfId="0"'
            + (' applyNumberFormat="1"' if index else "") + "/>"
        )
    styles = (
        f'{hb.DECL}<styleSheet xmlns="{hb.MAIN}">{num_fmts}{fonts}{fills}'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '<dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>'
        "</styleSheet>"
    )
    rows = []
    for row in range(1, 41):
        cells = "".join(
            f'<c r="{_col(col)}{row}" s="{(row * 97 + col * 13) % 4000}"><v>{row * 1000 + col + 0.25}</v></c>'
            for col in range(1, 11)
        )
        rows.append(f'<row r="{row}">{cells}</row>')
    rows.append('<row r="41"><c r="A41" s="3999"><v>45000.5</v></c><c r="B41" s="5" t="inlineStr"><is><t>text format</t></is></c></row>')
    parts = simple({"Styled": sheet_xml("".join(rows), dimension="A1:J41")}, styles=styles,
                   extra_wb_rels=[(f"{hb.REL}/theme", "theme/theme1.xml", False)],
                   extra_overrides={"xl/theme/theme1.xml": THEME_CT},
                   extra_parts={"xl/theme/theme1.xml": _theme()})
    return parts


def layout_and_protection():
    """Outline groups on rows and columns (one collapsed), frozen panes, print
    area and print titles, page setup, sheet protection with a password hash
    and allowed actions, and workbook structure protection."""
    sheet_pr = '<sheetPr><outlinePr summaryBelow="0" summaryRight="0"/><pageSetUpPr fitToPage="1"/></sheetPr>'
    views = ('<sheetViews><sheetView tabSelected="1" workbookViewId="0"><pane xSplit="1" ySplit="2" '
             'topLeftCell="B3" activePane="bottomRight" state="frozen"/><selection pane="topRight"/>'
             '<selection pane="bottomLeft"/><selection pane="bottomRight" activeCell="B3" sqref="B3"/></sheetView></sheetViews>')
    fmt = '<sheetFormatPr defaultRowHeight="15" outlineLevelRow="2" outlineLevelCol="1"/>'
    cols = ('<cols><col min="1" max="1" width="18" customWidth="1"/><col min="2" max="4" width="10" outlineLevel="1" collapsed="0"/>'
            '<col min="5" max="5" width="10" hidden="1" outlineLevel="1"/><col min="6" max="6" width="12" collapsed="1"/></cols>')
    rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>Line</t></is></c><c r="B1" t="inlineStr"><is><t>Q1</t></is></c><c r="F1" t="inlineStr"><is><t>Total</t></is></c></row>'
    rows += '<row r="2"><c r="A2" t="inlineStr"><is><t>Group</t></is></c></row>'
    for r in range(3, 9):
        level = 2 if r in (4, 5, 7) else 1
        hidden = ' hidden="1"' if r == 7 else ""
        rows += (f'<row r="{r}" outlineLevel="{level}"{hidden}><c r="A{r}" t="inlineStr"><is><t>item {r}</t></is></c>'
                 f'<c r="B{r}"><v>{r}</v></c><c r="C{r}"><v>{r * 2}</v></c><c r="F{r}"><f>SUM(B{r}:E{r})</f><v>{r * 3}</v></c></row>')
    rows += '<row r="9" collapsed="1"><c r="A9" t="inlineStr"><is><t>subtotal</t></is></c><c r="F9"><f>SUBTOTAL(9,F3:F8)</f><v>99</v></c></row>'
    protection = ('<sheetProtection algorithmName="SHA-512" hashValue="c3ludGhldGljLWhhc2gtdmFsdWU=" '
                  'saltValue="c3ludGhldGljLXNhbHQ=" spinCount="100000" sheet="1" objects="1" scenarios="1" '
                  'formatColumns="0" sort="0" autoFilter="0"/>')
    after = (protection + '<printOptions gridLines="1"/><pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
             '<pageSetup orientation="landscape" fitToHeight="0"/><headerFooter><oddFooter>&amp;P of &amp;N</oddFooter></headerFooter>'
             '<rowBreaks count="1" manualBreakCount="1"><brk id="6" max="16383" man="1"/></rowBreaks>')
    body = (f'{hb.DECL}<worksheet xmlns="{hb.MAIN}" xmlns:r="{hb.REL}">{sheet_pr}<dimension ref="A1:F9"/>{views}{fmt}{cols}'
            f"<sheetData>{rows}</sheetData>{after}</worksheet>")
    names = ("<definedNames><definedName name=\"_xlnm.Print_Area\" localSheetId=\"0\">Plan!$A$1:$F$9</definedName>"
             "<definedName name=\"_xlnm.Print_Titles\" localSheetId=\"0\">Plan!$A:$A,Plan!$1:$2</definedName></definedNames>")
    protect = ('<workbookProtection workbookAlgorithmName="SHA-512" workbookHashValue="c3ludGhldGljLWhhc2g=" '
               'workbookSaltValue="c2FsdA==" workbookSpinCount="100000" lockStructure="1"/>')
    parts = simple({"Plan": body}, styles=minimal_styles(), wb_extra=names)
    # CT_Workbook order: ... workbookPr, workbookProtection, bookViews, sheets.
    wb = parts["xl/workbook.xml"]
    anchor = "<bookViews" if "<bookViews" in wb else "<sheets>"
    parts["xl/workbook.xml"] = wb.replace(anchor, protect + anchor, 1)
    return parts

# ------------------------------------------------- objects and extensions
# Feature parts Excel writes that the libraries do not: form controls,
# slicers, timelines, sparklines, connections and query tables, customXml,
# embedded OLE objects. Every payload is synthetic.

MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
X14_NS = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
X15_NS = "http://schemas.microsoft.com/office/spreadsheetml/2010/11/main"
XM_NS = "http://schemas.microsoft.com/office/excel/2006/main"
VML_CT = "application/vnd.openxmlformats-officedocument.vmlDrawing"
TABLE_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"
_VML_DEFAULTS = {"rels": hb.RELS_CT, "xml": "application/xml", "vml": VML_CT}


def _vml(object_type: str, row: int = 0, column: int = 3) -> str:
    return (
        '<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:x="urn:schemas-microsoft-com:office:excel"><v:shape id="_x0000_s1025" type="#_x0000_t201">'
        f'<x:ClientData ObjectType="{object_type}"><x:Anchor>{column}, 0, {row}, 0, {column + 2}, 0, {row + 1}, 0</x:Anchor>'
        "</x:ClientData></v:shape></xml>"
    )


def _sheet(rows: str, *, dimension: str, after: str = "", ns: str = "") -> str:
    return (
        f'{hb.DECL}<worksheet xmlns="{hb.MAIN}" xmlns:r="{hb.REL}"{ns}><dimension ref="{dimension}"/>'
        f"<sheetData>{rows}</sheetData>{after}</worksheet>"
    )


def _table(name: str, ref: str, columns: list[tuple[str, str]], *, totals: bool = False, tid: int = 1) -> str:
    """columns: (name, extra attributes/children markup)."""
    cols = "".join(
        f'<tableColumn id="{i}" name="{n}"{extra}' for i, (n, extra) in enumerate(columns, 1)
    )
    total_attr = ' totalsRowCount="1"' if totals else ""
    auto_ref = ref if not totals else ref.split(":")[0] + ":" + _shift_row(ref.split(":")[1], -1)
    return (
        f'{hb.DECL}<table xmlns="{hb.MAIN}" id="{tid}" name="{name}" displayName="{name}" ref="{ref}"{total_attr}>'
        f'<autoFilter ref="{auto_ref}"/><tableColumns count="{len(columns)}">{cols}</tableColumns>'
        '<tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/>'
        "</table>"
    )


def _shift_row(cell: str, delta: int) -> str:
    letters = cell.rstrip("0123456789")
    return f"{letters}{int(cell[len(letters):]) + delta}"


def _region_rows(count: int = 4) -> str:
    rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>Region</t></is></c><c r="B1" t="inlineStr"><is><t>Units</t></is></c></row>'
    for r, (region, units) in enumerate([("North", 10), ("South", 7), ("East", 5), ("West", 8)][:count], 2):
        rows += f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>{region}</t></is></c><c r="B{r}"><v>{units}</v></c></row>'
    return rows


def form_controls():
    """A check box and a drop-down form control (ctrlProp parts, legacy VML),
    each linked to a cell, as Excel 2010+ saves them."""
    rows = ('<row r="1"><c r="A1" t="inlineStr"><is><t>Include</t></is></c><c r="B1" t="b"><v>1</v></c>'
            '<c r="E1" t="inlineStr"><is><t>low</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>Pick</t></is></c><c r="B2"><v>2</v></c>'
            '<c r="C2" t="str"><f>INDEX(E1:E3,B2)</f><v>mid</v></c><c r="E2" t="inlineStr"><is><t>mid</t></is></c></row>'
            '<row r="3"><c r="E3" t="inlineStr"><is><t>high</t></is></c></row>')
    controls = (
        f'<legacyDrawing r:id="rId1"/><mc:AlternateContent xmlns:mc="{MC_NS}"><mc:Choice Requires="x14"><controls>'
        '<control shapeId="1025" r:id="rId2" name="Check Box 1"/>'
        '<control shapeId="1026" r:id="rId3" name="Drop Down 2"/>'
        "</controls></mc:Choice></mc:AlternateContent>"
    )
    margins = '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
    sheet = _sheet(rows, dimension="A1:E3", after=margins + controls)
    ctrl_ct = "application/vnd.ms-excel.controlproperties+xml"
    ctrl_rel = "http://schemas.microsoft.com/office/2006/relationships/ctrlProp"
    return simple(
        {"Controls": sheet}, styles=minimal_styles(), defaults=_VML_DEFAULTS,
        extra_overrides={"xl/ctrlProps/ctrlProp1.xml": ctrl_ct, "xl/ctrlProps/ctrlProp2.xml": ctrl_ct},
        sheet_rels={1: hb.rels([
            ("rId1", f"{hb.REL}/vmlDrawing", "../drawings/vmlDrawing1.vml"),
            ("rId2", ctrl_rel, "../ctrlProps/ctrlProp1.xml"),
            ("rId3", ctrl_rel, "../ctrlProps/ctrlProp2.xml"),
        ])},
        extra_parts={
            "xl/drawings/vmlDrawing1.vml": _vml("Checkbox"),
            "xl/ctrlProps/ctrlProp1.xml": f'{hb.DECL}<formControlPr xmlns="{X14_NS}" objectType="CheckBox" checked="Checked" fmlaLink="$B$1"/>',
            "xl/ctrlProps/ctrlProp2.xml": f'{hb.DECL}<formControlPr xmlns="{X14_NS}" objectType="Drop" dropStyle="combo" dx="16" fmlaLink="$B$2" fmlaRange="$E$1:$E$3" sel="2"/>',
        },
    )


def slicers():
    """A table slicer: slicer and slicerCache parts, the workbook and sheet
    extLst lists that point at them, and the `Slicer_Region` name Excel adds
    (its value is #N/A)."""
    rows = _region_rows()
    sheet_ext = (
        f'<tableParts count="1"><tablePart r:id="rId1"/></tableParts><extLst>'
        f'<ext xmlns:x14="{X14_NS}" uri="{{3A4CF648-6AED-40f4-86FF-DC5316D8AED3}}">'
        '<x14:slicerList><x14:slicer r:id="rId2"/></x14:slicerList></ext></extLst>'
    )
    names = '<definedNames><definedName name="Slicer_Region">#N/A</definedName></definedNames>'
    wb_ext = (
        f'<extLst><ext xmlns:x15="{X15_NS}" uri="{{46BE6895-7355-4a93-B00E-2C351335B9C9}}">'
        '<x15:slicerCaches xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
        '<x14:slicerCache r:id="rId4"/></x15:slicerCaches></ext></extLst>'
    )
    slicer_rel = "http://schemas.microsoft.com/office/2007/relationships/slicer"
    cache_rel = "http://schemas.microsoft.com/office/2007/relationships/slicerCache"
    return simple(
        {"Sales": _sheet(rows, dimension="A1:B5", after=sheet_ext)}, styles=minimal_styles(),
        wb_extra=names + wb_ext,
        extra_wb_rels=[(cache_rel, "slicerCaches/slicerCache1.xml", False)],
        extra_overrides={
            "xl/tables/table1.xml": TABLE_CT,
            "xl/slicers/slicer1.xml": "application/vnd.ms-excel.slicer+xml",
            "xl/slicerCaches/slicerCache1.xml": "application/vnd.ms-excel.slicerCache+xml",
        },
        sheet_rels={1: hb.rels([
            ("rId1", f"{hb.REL}/table", "../tables/table1.xml"),
            ("rId2", slicer_rel, "../slicers/slicer1.xml"),
        ])},
        extra_parts={
            "xl/tables/table1.xml": _table("Sales", "A1:B5", [("Region", "/>"), ("Units", "/>")]),
            "xl/slicers/slicer1.xml": (
                f'{hb.DECL}<slicers xmlns="{X14_NS}" xmlns:mc="{MC_NS}" mc:Ignorable="x xr10" '
                f'xmlns:x="{hb.MAIN}"><slicer name="Region" cache="Slicer_Region" caption="Region" rowHeight="241300"/></slicers>'
            ),
            "xl/slicerCaches/slicerCache1.xml": (
                f'{hb.DECL}<slicerCacheDefinition xmlns="{X14_NS}" xmlns:mc="{MC_NS}" mc:Ignorable="x x15" '
                f'xmlns:x="{hb.MAIN}" xmlns:x15="{X15_NS}" name="Slicer_Region" sourceName="Region">'
                '<extLst><x:ext uri="{2F2917AC-EB37-4324-AD4E-5DD8C200BD13}">'
                '<x15:tableSlicerCache tableId="1" column="1"/></x:ext></extLst></slicerCacheDefinition>'
            ),
        },
    )


def timelines():
    """A pivot table with a date timeline (timeline and timelineCache parts),
    the shape Excel 2013+ writes for a date filter."""
    parts = hb.pivot_cache()
    sheet2 = parts["xl/worksheets/sheet2.xml"]
    ext = (
        f'<extLst><ext xmlns:x15="{X15_NS}" uri="{{7E03D99C-DC04-49d9-9315-930204A7B6E9}}">'
        '<x15:timelineRefs><x15:timelineRef r:id="rId2"/></x15:timelineRefs></ext></extLst>'
    )
    parts["xl/worksheets/sheet2.xml"] = sheet2.replace("</worksheet>", ext + "</worksheet>")
    parts["xl/worksheets/_rels/sheet2.xml.rels"] = hb.rels([
        ("rId1", f"{hb.REL}/pivotTable", "../pivotTables/pivotTable1.xml"),
        ("rId2", "http://schemas.microsoft.com/office/2011/relationships/timeline", "../timelines/timeline1.xml"),
    ])
    wb = parts["xl/workbook.xml"]
    wb_ext = (
        f'<extLst><ext xmlns:x15="{X15_NS}" uri="{{D0CA8CA8-9F24-4464-BF8E-62219DCF47F9}}">'
        '<x15:timelineCacheRefs><x15:timelineCacheRef r:id="rId9"/></x15:timelineCacheRefs></ext></extLst>'
    )
    parts["xl/workbook.xml"] = wb.replace("</workbook>", wb_ext + "</workbook>")
    wb_rels = parts["xl/_rels/workbook.xml.rels"]
    parts["xl/_rels/workbook.xml.rels"] = wb_rels.replace(
        "</Relationships>",
        '<Relationship Id="rId9" Type="http://schemas.microsoft.com/office/2011/relationships/timelineCache" '
        'Target="timelineCaches/timelineCache1.xml"/></Relationships>',
    )
    ct = parts["[Content_Types].xml"]
    parts["[Content_Types].xml"] = ct.replace(
        "</Types>",
        '<Override PartName="/xl/timelines/timeline1.xml" ContentType="application/vnd.ms-excel.timeline+xml"/>'
        '<Override PartName="/xl/timelineCaches/timelineCache1.xml" ContentType="application/vnd.ms-excel.timelineCache+xml"/></Types>',
    )
    parts["xl/timelines/timeline1.xml"] = (
        f'{hb.DECL}<timelines xmlns="{X15_NS}" xmlns:mc="{MC_NS}" mc:Ignorable="x" xmlns:x="{hb.MAIN}">'
        '<timeline name="Month" cache="NativeTimeline_Month" caption="Month" level="2" selectionLevel="2"/></timelines>'
    )
    parts["xl/timelineCaches/timelineCache1.xml"] = (
        f'{hb.DECL}<timelineCacheDefinition xmlns="{X15_NS}" xmlns:mc="{MC_NS}" mc:Ignorable="x" '
        f'xmlns:x="{hb.MAIN}" name="NativeTimeline_Month" sourceName="Month">'
        '<pivotTables><pivotTable tabId="2" name="PivotTable1"/></pivotTables>'
        '<state minimalRefreshVersion="6" lastRefreshVersion="6" pivotCacheId="1" filterType="unknown">'
        '<bounds startDate="2020-01-01T00:00:00" endDate="2021-01-01T00:00:00"/></state></timelineCacheDefinition>'
    )
    return parts


def sparklines():
    """Line, column and win/loss sparkline groups in the worksheet's x14
    extLst, one reading another sheet."""
    rows = ""
    for r in range(1, 5):
        rows += f'<row r="{r}">' + "".join(
            f'<c r="{col}{r}"><v>{(r * 7 + i * 3) % 11 - 3}</v></c>' for i, col in enumerate("ABCDE")
        ) + "</row>"
    groups = (
        f'<extLst><ext xmlns:x14="{X14_NS}" uri="{{05C60535-1F16-4fd2-B633-F4F36F0B64E0}}">'
        f'<x14:sparklineGroups xmlns:xm="{XM_NS}">'
        '<x14:sparklineGroup displayEmptyCellsAs="gap" markers="1"><x14:colorSeries rgb="FF376092"/>'
        '<x14:sparklines><x14:sparkline><xm:f>Trend!A1:E1</xm:f><xm:sqref>F1</xm:sqref></x14:sparkline>'
        '<x14:sparkline><xm:f>Trend!A2:E2</xm:f><xm:sqref>F2</xm:sqref></x14:sparkline></x14:sparklines></x14:sparklineGroup>'
        '<x14:sparklineGroup type="column" displayEmptyCellsAs="gap"><x14:colorSeries rgb="FF376092"/>'
        '<x14:sparklines><x14:sparkline><xm:f>Trend!A3:E3</xm:f><xm:sqref>F3</xm:sqref></x14:sparkline></x14:sparklines></x14:sparklineGroup>'
        '<x14:sparklineGroup type="stacked" negative="1" displayEmptyCellsAs="gap"><x14:colorSeries rgb="FF376092"/>'
        '<x14:sparklines><x14:sparkline><xm:f>\'Other Data\'!A1:E1</xm:f><xm:sqref>F4</xm:sqref></x14:sparkline></x14:sparklines></x14:sparklineGroup>'
        "</x14:sparklineGroups></ext></extLst>"
    )
    other = sheet_xml('<row r="1">' + "".join(f'<c r="{c}1"><v>{i - 2}</v></c>' for i, c in enumerate("ABCDE")) + "</row>",
                      dimension="A1:E1")
    return simple({"Trend": _sheet(rows, dimension="A1:E4", after=groups), "Other Data": other},
                  styles=minimal_styles())


def connections_query():
    """A Power Query-style load: connections.xml, a query table bound to a
    worksheet table, the ExternalData name, and the DataMashup customXml
    item (a synthetic base64 payload)."""
    rows = _region_rows(3)
    after = '<tableParts count="1"><tablePart r:id="rId1"/></tableParts>'
    names = '<definedNames><definedName name="ExternalData_1" localSheetId="0" hidden="1">Query!$A$1:$B$4</definedName></definedNames>'
    connections = (
        f'{hb.DECL}<connections xmlns="{hb.MAIN}"><connection id="1" keepAlive="1" name="Query - Regions" '
        'description="Connection to the Regions query in the workbook." type="5" refreshedVersion="8" background="1" saveData="1">'
        '<dbPr connection="Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=Regions;Extended Properties=&quot;&quot;" '
        'command="SELECT * FROM [Regions]"/></connection></connections>'
    )
    query_table = (
        f'{hb.DECL}<queryTable xmlns="{hb.MAIN}" name="ExternalData_1" connectionId="1" autoFormatId="16" '
        'applyNumberFormats="0" applyBorderFormats="0" applyFontFormats="0" applyPatternFormats="0" applyAlignmentFormats="0" applyWidthHeightFormats="0">'
        '<queryTableRefresh nextId="3"><queryTableFields count="2"><queryTableField id="1" name="Region" tableColumnId="1"/>'
        '<queryTableField id="2" name="Units" tableColumnId="2"/></queryTableFields></queryTableRefresh></queryTable>'
    )
    table = (
        f'{hb.DECL}<table xmlns="{hb.MAIN}" id="1" name="Regions" displayName="Regions" ref="A1:B4" tableType="queryTable" totalsRowShown="0">'
        '<autoFilter ref="A1:B4"/><tableColumns count="2"><tableColumn id="1" uniqueName="1" name="Region" queryTableFieldId="1"/>'
        '<tableColumn id="2" uniqueName="2" name="Units" queryTableFieldId="2"/></tableColumns>'
        '<tableStyleInfo name="TableStyleMedium7" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/></table>'
    )
    # Excel stores the DataMashup item as UTF-16 little-endian with a BOM.
    mashup = (
        '<?xml version="1.0" encoding="utf-16"?><DataMashup xmlns="http://schemas.microsoft.com/DataMashup">'
        "c3ludGhldGljIGRhdGEgbWFzaHVwIHBheWxvYWQgZm9yIHRoZSBjb3JwdXM=</DataMashup>"
    ).encode("utf-16")
    parts = _with_custom_xml(simple(
        {"Query": _sheet(rows, dimension="A1:B4", after=after)}, styles=minimal_styles(), wb_extra=names,
        extra_wb_rels=[(f"{hb.REL}/connections", "connections.xml", False)],
        extra_overrides={
            "xl/connections.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.connections+xml",
            "xl/tables/table1.xml": TABLE_CT,
            "xl/queryTables/queryTable1.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.queryTable+xml",
        },
        sheet_rels={1: hb.rels([("rId1", f"{hb.REL}/table", "../tables/table1.xml")])},
        extra_parts={
            "xl/connections.xml": connections,
            "xl/tables/table1.xml": table,
            "xl/tables/_rels/table1.xml.rels": hb.rels([("rId1", f"{hb.REL}/queryTable", "../queryTables/queryTable1.xml")]),
            "xl/queryTables/queryTable1.xml": query_table,
        },
    ), mashup, "{D7A9C1E2-0000-4000-8000-00000000C0DE}")
    return parts


def _with_custom_xml(parts: dict, item: str | bytes, guid: str, schema: str | None = None) -> dict:
    """Add customXml/item1.xml with its properties part, linked from the root
    package as Office does for document-level custom XML."""
    refs = f'<ds:schemaRefs><ds:schemaRef ds:uri="{schema}"/></ds:schemaRefs>' if schema else "<ds:schemaRefs/>"
    parts["customXml/item1.xml"] = item
    parts["customXml/itemProps1.xml"] = (
        f'{hb.DECL}<ds:datastoreItem ds:itemID="{guid}" '
        f'xmlns:ds="http://schemas.openxmlformats.org/officeDocument/2006/customXml">{refs}</ds:datastoreItem>'
    )
    parts["customXml/_rels/item1.xml.rels"] = hb.rels(
        [("rId1", f"{hb.REL}/customXmlProps", "itemProps1.xml")]
    )
    ct = parts["[Content_Types].xml"]
    parts["[Content_Types].xml"] = ct.replace(
        "</Types>",
        '<Override PartName="/customXml/itemProps1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.customXmlProperties+xml"/></Types>',
    )
    wb_rels = parts["xl/_rels/workbook.xml.rels"]
    parts["xl/_rels/workbook.xml.rels"] = wb_rels.replace(
        "</Relationships>",
        f'<Relationship Id="rId90" Type="{hb.REL}/customXml" Target="../customXml/item1.xml"/></Relationships>',
    )
    return parts


def custom_xml():
    """A document-level customXml data store (as document-management and
    labelling tools add), linked from the workbook."""
    item = (
        f'{hb.DECL}<inventory xmlns="urn:example:inventory"><owner>Facilities</owner>'
        "<reviewed>2020-01-01</reviewed><tags><tag>synthetic</tag><tag>corpus</tag></tags></inventory>"
    )
    rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>Bin</t></is></c><c r="B1"><v>12</v></c><c r="C1"><f>B1*3</f><v>36</v></c></row>'
    parts = simple({"Bins": sheet_xml(rows, dimension="A1:C1")}, styles=minimal_styles())
    return _with_custom_xml(parts, item, "{3F2504E0-4F89-11D3-9A0C-0305E82C3301}", "urn:example:inventory")


def embedded_ole():
    """An embedded OLE package object (synthetic bytes, no preview image)
    shown through legacy VML, with the oleObjects element that names it."""
    rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>Attached notes</t></is></c><c r="B1"><v>3</v></c><c r="C1"><f>B1+1</f><v>4</v></c></row>'
    after = (
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        '<legacyDrawing r:id="rId1"/><oleObjects>'
        '<oleObject progId="Package" shapeId="1025" r:id="rId2"/></oleObjects>'
    )
    payload = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"synthetic ole payload for the robustness corpus\x00" * 8
    return simple(
        {"Notes": _sheet(rows, dimension="A1:C1", after=after)}, styles=minimal_styles(),
        defaults={**_VML_DEFAULTS, "bin": "application/vnd.openxmlformats-officedocument.oleObject"},
        sheet_rels={1: hb.rels([
            ("rId1", f"{hb.REL}/vmlDrawing", "../drawings/vmlDrawing1.vml"),
            ("rId2", f"{hb.REL}/oleObject", "../embeddings/oleObject1.bin"),
        ])},
        extra_parts={"xl/drawings/vmlDrawing1.vml": _vml("Pict"), "xl/embeddings/oleObject1.bin": payload},
    )


# ------------------------------------------------------ formulas and tables


def pivot_calculated_field():
    """A pivot whose cache defines a calculated field (`formula` on a
    cacheField with databaseField="0") used as a data field."""
    parts = hb.pivot_cache()
    pcd = parts["xl/pivotCache/pivotCacheDefinition1.xml"]
    pcd = pcd.replace('<cacheFields count="2">', '<cacheFields count="3">').replace(
        "</cacheFields>",
        '<cacheField name="Double" numFmtId="0" formula="Units*2" databaseField="0"/></cacheFields>',
    )
    parts["xl/pivotCache/pivotCacheDefinition1.xml"] = pcd
    pt = parts["xl/pivotTables/pivotTable1.xml"]
    pt = pt.replace('<location ref="A3:B5"', '<location ref="A3:C5"').replace(
        '<pivotFields count="2">', '<pivotFields count="3">'
    ).replace("</pivotFields>", '<pivotField dataField="1" dragToRow="0" dragToCol="0" dragToPage="0" showAll="0" defaultSubtotal="0"/></pivotFields>')
    pt = pt.replace(
        '<dataFields count="1"><dataField name="Sum of Units" fld="1" baseField="0" baseItem="0"/></dataFields>',
        '<colFields count="1"><field x="-2"/></colFields>'
        '<dataFields count="2"><dataField name="Sum of Units" fld="1" baseField="0" baseItem="0"/>'
        '<dataField name="Sum of Double" fld="2" baseField="0" baseItem="0"/></dataFields>',
    )
    parts["xl/pivotTables/pivotTable1.xml"] = pt
    sheet = parts["xl/worksheets/sheet2.xml"]
    sheet = sheet.replace('<dimension ref="A3:B5"/>', '<dimension ref="A3:C5"/>')
    sheet = sheet.replace('<c r="B3" t="inlineStr"><is><t>Sum of Units</t></is></c>',
                          '<c r="B3" t="inlineStr"><is><t>Sum of Units</t></is></c><c r="C3" t="inlineStr"><is><t>Sum of Double</t></is></c>')
    sheet = sheet.replace('<c r="B4"><v>15</v></c>', '<c r="B4"><v>15</v></c><c r="C4"><v>30</v></c>')
    sheet = sheet.replace('<c r="B5"><v>7</v></c>', '<c r="B5"><v>7</v></c><c r="C5"><v>14</v></c>')
    parts["xl/worksheets/sheet2.xml"] = sheet
    return parts


def table_totals_calculated():
    """A table with calculated columns (calculatedColumnFormula, [@Col]
    references) and a totals row mixing a built-in function, a custom total
    formula and a label."""
    header = ('<row r="1"><c r="A1" t="inlineStr"><is><t>Item</t></is></c><c r="B1" t="inlineStr"><is><t>Qty</t></is></c>'
              '<c r="C1" t="inlineStr"><is><t>Price</t></is></c><c r="D1" t="inlineStr"><is><t>Amount</t></is></c></row>')
    body = ""
    data = [("bolts", 4, 2.5), ("nuts", 10, 0.75), ("washers", 7, 0.2)]
    for r, (item, qty, price) in enumerate(data, 2):
        body += (f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>{item}</t></is></c><c r="B{r}"><v>{qty}</v></c>'
                 f'<c r="C{r}"><v>{price}</v></c><c r="D{r}"><f>Parts[[#This Row],[Qty]]*Parts[[#This Row],[Price]]</f>'
                 f'<v>{qty * price}</v></c></row>')
    total = sum(q * p for _, q, p in data)
    body += ('<row r="5"><c r="A5" t="inlineStr"><is><t>Total</t></is></c><c r="B5"><f>SUBTOTAL(109,Parts[Qty])</f><v>21</v></c>'
             '<c r="C5"><f>MAX(Parts[Price])</f><v>2.5</v></c>'
             f'<c r="D5"><f>SUBTOTAL(109,Parts[Amount])</f><v>{total}</v></c></row>')
    body += '<row r="7"><c r="A7" t="inlineStr"><is><t>Check</t></is></c><c r="B7"><f>Parts[[#Totals],[Amount]]-SUM(D2:D4)</f><v>0</v></c></row>'
    columns = [
        ("Item", ' totalsRowLabel="Total"/>'),
        ("Qty", ' totalsRowFunction="sum"/>'),
        ("Price", ' totalsRowFunction="custom"><totalsRowFormula>MAX(Parts[Price])</totalsRowFormula></tableColumn>'),
        ("Amount", ' totalsRowFunction="sum"><calculatedColumnFormula>Parts[[#This Row],[Qty]]*Parts[[#This Row],[Price]]</calculatedColumnFormula></tableColumn>'),
    ]
    after = '<tableParts count="1"><tablePart r:id="rId1"/></tableParts>'
    return simple(
        {"Orders": _sheet(header + body, dimension="A1:D7", after=after)}, styles=minimal_styles(),
        extra_overrides={"xl/tables/table1.xml": TABLE_CT},
        sheet_rels={1: hb.rels([("rId1", f"{hb.REL}/table", "../tables/table1.xml")])},
        extra_parts={"xl/tables/table1.xml": _table("Parts", "A1:D5", columns, totals=True)},
    )


def three_d_references():
    """3D references across a run of sheets (SUM(Jan:Mar!B2)), quoted sheet
    names in a 3D span, and a defined name holding a 3D range."""
    months = {"Jan": 10, "Feb": 20, "Mar": 30}
    sheets = {}
    for name, value in months.items():
        sheets[name] = sheet_xml(f'<row r="2"><c r="B2"><v>{value}</v></c><c r="C2"><v>{value / 10}</v></c></row>',
                                 dimension="B2:C2")
    sheets["Q2 Apr"] = sheet_xml('<row r="2"><c r="B2"><v>40</v></c></row>', dimension="B2")
    sheets["Q2 May"] = sheet_xml('<row r="2"><c r="B2"><v>50</v></c></row>', dimension="B2")
    summary = ('<row r="1"><c r="A1"><f>SUM(Jan:Mar!B2)</f><v>60</v></c><c r="B1"><f>SUM(\'Q2 Apr:Q2 May\'!B2)</f><v>90</v></c>'
               '<c r="C1"><f>AVERAGE(Jan:Mar!C2)</f><v>2</v></c><c r="D1"><f>SUM(FirstQuarter)</f><v>60</v></c>'
               '<c r="E1"><f>COUNT(Jan:Feb!B2:C2)</f><v>4</v></c></row>')
    sheets["Summary"] = sheet_xml(summary, dimension="A1:E1")
    names = '<definedNames><definedName name="FirstQuarter">Jan:Mar!$B$2</definedName></definedNames>'
    return simple(sheets, styles=minimal_styles(), wb_extra=names)


def r1c1_looking_names():
    """Defined names that read like R1C1 or A1 references but are legal
    (R1C1Total, RC_Rate, rate_R2, C3PO, XFE1) used in formulas."""
    rows = ('<row r="1"><c r="A1"><v>100</v></c><c r="B1"><v>0.05</v></c><c r="C1"><v>3</v></c></row>'
            '<row r="2"><c r="A2"><f>R1C1Total*RC_Rate</f><v>5</v></c><c r="B2"><f>rate_R2+C3PO</f><v>3.05</v></c>'
            '<c r="C2"><f>XFE1*2</f><v>6</v></c><c r="D2"><f>SUM(R1C1Total,A1)</f><v>200</v></c></row>')
    names = ('<definedNames>'
             '<definedName name="C3PO">Calc!$C$1</definedName>'
             '<definedName name="R1C1Total">Calc!$A$1</definedName>'
             '<definedName name="RC_Rate">Calc!$B$1</definedName>'
             '<definedName name="rate_R2">Calc!$B$1</definedName>'
             '<definedName name="XFE1">Calc!$C$1</definedName>'
             '</definedNames>')
    return simple({"Calc": sheet_xml(rows, dimension="A1:D2")}, styles=minimal_styles(), wb_extra=names)


def long_formula():
    """Formulas at Excel's 8,192-character limit, as a flat chain of additions
    (no function argument limit applies): one exactly 8,192 characters as
    stored (without the leading '='), one just under it."""
    def build(target: int) -> tuple[str, int]:
        formula, total, count = "0", 0, 0
        while True:
            term = f"+A{(count % 9) + 1}"
            if len(formula) + len(term) > target - 3:
                break
            formula += term
            total += (count % 9) + 1
            count += 1
        pad = target - len(formula)
        if pad % 2:
            formula += "+00"
            pad -= 3
        formula += "+0" * (pad // 2)
        assert len(formula) == target, (len(formula), target)
        return formula, total
    cells = '<c r="A1"><v>1</v></c>'
    for col, target in (("B", 8192), ("C", 8100)):
        formula, total = build(target)
        cells += f'<c r="{col}1"><f>{formula}</f><v>{total}</v></c>'
    rows = f'<row r="1">{cells}</row>' + "".join(
        f'<row r="{r}"><c r="A{r}"><v>{r}</v></c></row>' for r in range(2, 10)
    )
    return simple({"Long": sheet_xml(rows, dimension="A1:C9")}, styles=minimal_styles())


BUILDERS = {
    "hb_wide_sheet": (wide_sheet, ["scale", "wide-sheet", "xfd-column"]),
    "hb_many_sheets": (many_sheets, ["scale", "many-sheets", "cross-sheet"]),
    "hb_huge_shared_strings": (huge_shared_strings, ["scale", "shared-strings", "long-strings", "unicode"]),
    "hb_inline_and_shared_strings": (inline_and_shared_strings, ["inline-strings", "shared-strings", "rich-text", "str-formula"]),
    "hb_many_styles": (many_styles, ["styles", "number-formats", "theme", "scale"]),
    "hb_layout_protection": (layout_and_protection, ["outline", "freeze-panes", "print-settings", "protection"]),
    "hb_form_controls": (form_controls, ["form-controls", "vml", "mc-ignorable"]),
    "hb_slicers": (slicers, ["slicers", "tables", "extlst"]),
    "hb_timelines": (timelines, ["timelines", "pivot", "extlst"]),
    "hb_sparklines": (sparklines, ["sparklines", "extlst", "sheet-name-spaces"]),
    "hb_connections_query": (connections_query, ["connections", "power-query", "query-table", "custom-xml", "tables"]),
    "hb_custom_xml": (custom_xml, ["custom-xml"]),
    "hb_embedded_ole": (embedded_ole, ["ole-objects", "vml"]),
    "hb_pivot_calculated_field": (pivot_calculated_field, ["pivot", "pivot-calculated-field"]),
    "hb_table_totals_calculated": (table_totals_calculated, ["tables", "totals-row", "calculated-columns", "structured-refs"]),
    "hb_3d_references": (three_d_references, ["3d-references", "cross-sheet", "defined-names", "sheet-name-spaces"]),
    "hb_r1c1_looking_names": (r1c1_looking_names, ["defined-names", "r1c1-looking-names"]),
    "hb_long_formula": (long_formula, ["long-formula", "formula-length-limit"]),
    "hb_text_columns": (text_columns, ["text", "text-format", "column-styles", "row-styles", "insert-inherits-style"]),
    "hb_dropdown_formulas": (dropdown_formulas, ["data-validation", "dropdowns", "formula-in-validated-cell"]),
}
STORED: set[str] = set()


def build_all(folder: Path) -> dict[str, list[str]]:
    made = {}
    for name, (builder, categories) in BUILDERS.items():
        compression = zipfile.ZIP_STORED if name in STORED else zipfile.ZIP_DEFLATED
        hb.write_package(folder / f"{name}.xlsx", builder(), compression=compression)
        made[f"{name}.xlsx"] = categories
    return made
