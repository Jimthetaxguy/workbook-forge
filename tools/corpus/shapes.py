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



BUILDERS = {
    "hb_wide_sheet": (wide_sheet, ["scale", "wide-sheet", "xfd-column"]),
    "hb_many_sheets": (many_sheets, ["scale", "many-sheets", "cross-sheet"]),
    "hb_huge_shared_strings": (huge_shared_strings, ["scale", "shared-strings", "long-strings", "unicode"]),
    "hb_inline_and_shared_strings": (inline_and_shared_strings, ["inline-strings", "shared-strings", "rich-text", "str-formula"]),
    "hb_many_styles": (many_styles, ["styles", "number-formats", "theme", "scale"]),
    "hb_layout_protection": (layout_and_protection, ["outline", "freeze-panes", "print-settings", "protection"]),
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
