"""Generate the robustness corpus under fixtures/corpus/.

Every file is produced here from synthetic, general-purpose content (budgets,
inventories, gradebooks, research data, engineering calculations). No
third-party spreadsheet is downloaded.

Writers:
- openpyxl and XlsxWriter, when installed (generator-only dependencies; the
  package itself does not depend on them). Files from a missing writer are
  skipped with a note and the committed copies stay in place.
- hand-built OOXML (tools/corpus/handbuilt.py, standard library only).
- LibreOffice headless (``soffice --convert-to xlsx``) only with ``--libreoffice``.

Usage: python tools/corpus/generate_corpus.py [--out fixtures/corpus] [--libreoffice]
The manifest (fixtures/corpus/MANIFEST.json) lists each file's writer and
categories.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handbuilt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXED = dt.datetime(2020, 1, 1, 0, 0, 0)


def _normalize_zip(path: Path) -> None:
    """Rewrite entry timestamps so regenerated files are byte-stable."""
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(path, "w") as archive:
        for info, data in entries:
            fresh = zipfile.ZipInfo(info.filename, handbuilt.STAMP)
            fresh.compress_type = zipfile.ZIP_DEFLATED
            fresh.external_attr = 0o644 << 16
            archive.writestr(fresh, data)


# ------------------------------------------------------------------ openpyxl


def openpyxl_files(out: Path) -> dict[str, list[str]]:
    try:
        import openpyxl
        from openpyxl.cell.rich_text import CellRichText, TextBlock
        from openpyxl.cell.text import InlineFont
        from openpyxl.comments import Comment
        from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, FormulaRule
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.workbook.defined_name import DefinedName
        from openpyxl.worksheet.datavalidation import DataValidation
        from openpyxl.worksheet.formula import ArrayFormula
        from openpyxl.worksheet.table import Table, TableStyleInfo
        from openpyxl.chart import BarChart, LineChart, PieChart, Reference
    except ImportError:
        print("openpyxl not installed; skipping openpyxl files", file=sys.stderr)
        return {}
    made: dict[str, list[str]] = {}

    def save(wb, name, categories):
        wb.properties.created = FIXED
        wb.properties.modified = FIXED
        wb.properties.creator = "corpus"
        path = out / f"{name}.xlsx"
        wb.save(path)
        _normalize_zip(path)
        made[path.name] = categories

    # Budget with shared strings and plain formulas.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Category", "Planned", "Actual", "Variance"])
    for i, (cat, plan, act) in enumerate([("Rent", 1200, 1200), ("Groceries", 450, 512.37),
                                           ("Transport", 120, 98.5), ("Utilities", 180, 201.1)], 2):
        ws.append([cat, plan, act, f"=C{i}-B{i}"])
    ws.append(["Total", "=SUM(B2:B5)", "=SUM(C2:C5)", "=C6-B6"])
    save(wb, "op_budget_shared_strings", ["shared-strings", "formulas", "budget"])

    # Styles and number formats.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Formats"
    rows = [
        ("date", dt.date(2024, 2, 29), "yyyy-mm-dd"),
        ("datetime", dt.datetime(2024, 3, 1, 13, 45, 0), "yyyy-mm-dd hh:mm:ss"),
        ("time", dt.time(8, 30), "h:mm AM/PM"),
        ("percent", 0.1275, "0.00%"),
        ("currency", 1234.5, '"$"#,##0.00'),
        ("negative red", -42.75, "#,##0.00;[Red]-#,##0.00"),
        ("scientific", 6.022e23, "0.00E+00"),
        ("fraction", 0.375, "# ?/?"),
        ("custom text", 7, '0" units"'),
        ("thousands", 9876543.21, "#,##0,"),
        ("elapsed", 1.75, "[h]:mm"),
    ]
    for r, (label, value, fmt) in enumerate(rows, 1):
        ws.cell(r, 1, label).font = Font(bold=True, color="FF1F4E79")
        cell = ws.cell(r, 2, value)
        cell.number_format = fmt
        cell.fill = PatternFill("solid", fgColor="FFFFF2CC")
        cell.alignment = Alignment(horizontal="right")
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 22
    save(wb, "op_styles_number_formats", ["styles", "number-formats", "dates", "percent", "currency", "custom-formats"])

    # 1904 date system.
    wb = openpyxl.Workbook()
    wb.epoch = openpyxl.utils.datetime.CALENDAR_MAC_1904
    ws = wb.active
    ws.title = "Log"
    ws["A1"] = dt.date(2021, 6, 1)
    ws["A1"].number_format = "yyyy-mm-dd"
    ws["B1"] = 3
    ws["C1"] = "=A1+B1"
    save(wb, "op_1904_dates", ["date-1904", "dates", "formulas"])

    # Merged cells.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Report"
    ws["A1"] = "Quarterly summary"
    ws.merge_cells("A1:D1")
    ws.append(["Region", "Q1", "Q2", "Q3"])
    ws.append(["North", 10, 12, 15])
    ws["A4"] = "Notes"
    ws.merge_cells("A4:A6")
    ws["B4"] = 1
    save(wb, "op_merged_cells", ["merged-cells"])

    # Hidden and very hidden sheets.
    wb = openpyxl.Workbook()
    wb.active.title = "Visible"
    wb.active["A1"] = "=Hidden!A1*2"
    h = wb.create_sheet("Hidden")
    h["A1"] = 21
    h.sheet_state = "hidden"
    v = wb.create_sheet("VeryHidden")
    v["A1"] = "lookup"
    v.sheet_state = "veryHidden"
    save(wb, "op_hidden_sheets", ["hidden-sheets", "very-hidden", "cross-sheet"])

    # Sheet names that need quoting.
    wb = openpyxl.Workbook()
    names = ["Q1 Budget", "Bob's Data", "Données été", "Σ Totals", "2024", "It's \"quoted\""]
    wb.active.title = names[0]
    for name in names[1:]:
        wb.create_sheet(name)
    for index, name in enumerate(names):
        wb[name]["A1"] = index + 1
    summary = wb.create_sheet("Summary")
    summary["A1"] = "='Q1 Budget'!A1+'Bob''s Data'!A1+'Données été'!A1"
    summary["A2"] = "='Σ Totals'!A1*'2024'!A1"
    summary["A3"] = "='It''s \"quoted\"'!A1"
    summary["A4"] = "=SUM('Q1 Budget:Σ Totals'!A1)"
    save(wb, "op_sheet_name_quoting", ["sheet-names", "quoting", "unicode", "apostrophes", "3d-refs"])

    # Defined names.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inputs"
    ws["A1"] = 0.08
    ws["A2"] = 1500
    calc = wb.create_sheet("Calc")
    calc["A1"] = "=Rate*Principal"
    calc["A2"] = "=LocalFactor*2"
    calc["B1"] = 3
    wb.defined_names["Rate"] = DefinedName("Rate", attr_text="Inputs!$A$1")
    wb.defined_names["Principal"] = DefinedName("Principal", attr_text="Inputs!$A$2")
    calc.defined_names["LocalFactor"] = DefinedName("LocalFactor", attr_text="Calc!$B$1")
    wb.defined_names["Growth"] = DefinedName("Growth", attr_text="0.03")
    save(wb, "op_defined_names", ["defined-names", "sheet-scoped-names", "constant-names"])

    # Table with structured references.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stock"
    ws.append(["Item", "Qty", "Cost", "Value"])
    for i, (item, qty, cost) in enumerate([("Bolts", 120, 0.15), ("Nuts", 300, 0.05), ("Washers", 80, 0.02)], 2):
        ws.append([item, qty, cost, "=Inventory[[#This Row],[Qty]]*Inventory[[#This Row],[Cost]]"])
    table = Table(displayName="Inventory", ref="A1:D4")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(table)
    ws["F1"] = "=SUM(Inventory[Value])"
    ws["F2"] = "=COUNTA(Inventory[Item])"
    save(wb, "op_table_structured_refs", ["tables", "structured-refs"])

    # Data validation and drop-downs (inline list, range source, name source).
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tasks"
    lists = wb.create_sheet("Lists")
    for i, status in enumerate(["Open", "In progress", "Done"], 1):
        lists.cell(i, 1, status)
    for i, owner in enumerate(["Avery", "Blake", "Casey"], 1):
        lists.cell(i, 2, owner)
    wb.defined_names["Owners"] = DefinedName("Owners", attr_text="Lists!$B$1:$B$3")
    ws.append(["Task", "Priority", "Status", "Owner", "Hours"])
    ws.append(["Draft plan", "High", "Open", "Avery", 4])
    ws.append(["Review", "Low", "Done", "Blake", 1.5])
    inline = DataValidation(type="list", formula1='"High,Medium,Low"', allow_blank=True)
    inline.add("B2:B20")
    by_range = DataValidation(type="list", formula1="=Lists!$A$1:$A$3", allow_blank=True,
                              showErrorMessage=True, errorTitle="Status", error="Pick a status")
    by_range.add("C2:C20")
    by_name = DataValidation(type="list", formula1="=Owners")
    by_name.add("D2:D20")
    hours = DataValidation(type="decimal", operator="between", formula1="0", formula2="24")
    hours.add("E2:E20")
    for item in (inline, by_range, by_name, hours):
        ws.add_data_validation(item)
    save(wb, "op_dropdowns", ["data-validation", "dropdowns", "inline-list", "range-list", "name-list", "decimal-validation"])

    # Conditional formatting.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Grades"
    ws.append(["Student", "Score"])
    for name, score in [("Avery", 91), ("Blake", 67), ("Casey", 78), ("Drew", 55)]:
        ws.append([name, score])
    ws.conditional_formatting.add("B2:B5", CellIsRule(operator="lessThan", formula=["60"], fill=PatternFill("solid", fgColor="FFFFC7CE")))
    ws.conditional_formatting.add("B2:B5", ColorScaleRule(start_type="min", start_color="FFF8696B", end_type="max", end_color="FF63BE7B"))
    ws.conditional_formatting.add("A2:A5", FormulaRule(formula=["$B2>=90"], font=Font(bold=True)))
    ws["B6"] = "=AVERAGE(B2:B5)"
    save(wb, "op_conditional_formatting", ["conditional-formatting", "gradebook"])

    # Legacy notes.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Samples"
    ws["A1"] = "Sample"
    ws["B1"] = 12.5
    ws["B1"].comment = Comment("Measured twice", "Lab")
    ws["A2"] = "Control"
    ws["A2"].comment = Comment("Baseline batch", "Lab")
    save(wb, "op_comments_notes", ["comments", "vml"])

    # Hyperlinks (external, internal, mailto).
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Links"
    ws["A1"] = "Project site"
    ws["A1"].hyperlink = "https://example.com/project"
    ws["A2"] = "Jump"
    ws["A2"].hyperlink = "#Links!C5"
    ws["A3"] = "Mail"
    ws["A3"].hyperlink = "mailto:team@example.com"
    ws["C5"] = 1
    save(wb, "op_hyperlinks", ["hyperlinks"])

    # Array (CSE) formula.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Calc"
    for r, (a, b) in enumerate([(2, 3), (4, 5), (6, 7)], 1):
        ws.cell(r, 1, a)
        ws.cell(r, 2, b)
    ws["D1"] = ArrayFormula("D1", "=SUM(A1:A3*B1:B3)")
    ws["E1"] = ArrayFormula("E1:E3", "=A1:A3*2")
    ws["F1"] = "=D1+1"
    save(wb, "op_array_formulas", ["array-formulas", "cse"])

    # Text handling.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Text"
    ws["A1"] = "00123"
    ws["A1"].number_format = "@"
    ws["A2"] = "007"
    ws["A2"].number_format = "@"
    ws["A2"].quotePrefix = True
    ws["A3"] = "1e5"
    ws["A3"].quotePrefix = True
    ws["A4"] = "=SUM(1,2)"  # starts with = but is text
    ws["A4"].data_type = "s"
    ws["A4"].number_format = "@"
    ws["A5"] = "Ünïcødé — 日本語 — emoji 🧪"
    ws["A6"] = "x" * 32767
    ws["A7"] = "  leading and trailing  "
    ws["A8"] = "line1\nline2"
    ws["A9"] = CellRichText(["Plain ", TextBlock(InlineFont(b=True), "bold"), " and ", TextBlock(InlineFont(i=True, color="FF0000FF"), "italic blue")])
    ws["B1"] = None
    ws["B1"].number_format = "@"  # empty text-formatted cell for edits
    ws["B2"] = "0042"
    ws["B2"].number_format = "@"
    ws["C1"] = "=LEN(A1)"
    ws["C2"] = '=A1&"-"&A2'
    ws["C3"] = "=VALUE(A1)+1"
    save(wb, "op_text_cells", ["text", "text-format", "quote-prefix", "leading-zeros", "unicode", "long-strings", "rich-text", "whitespace"])

    # Rich text shared strings.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Notes"
    for r in range(1, 4):
        ws.cell(r, 1, CellRichText([TextBlock(InlineFont(b=True), f"Step {r}: "), "mix reagents"]))
    save(wb, "op_rich_text", ["rich-text", "shared-strings"])

    # Errors and booleans.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Checks"
    ws["A1"] = True
    ws["A2"] = False
    ws["B1"] = "=1/0"
    ws["B2"] = "=NA()"
    ws["B3"] = "=A1=A2"
    ws["C1"] = "#N/A"
    ws["C1"].data_type = "e"
    save(wb, "op_errors_booleans", ["errors", "booleans"])

    # Empty workbook / empty sheets.
    wb = openpyxl.Workbook()
    wb.active.title = "Empty"
    wb.create_sheet("AlsoEmpty")
    save(wb, "op_empty_sheets", ["empty-sheet"])

    # Sparse sheet with a far-off cell.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sparse"
    ws["A1"] = 1
    ws["Z500"] = 2
    ws["XFD1048576"] = 3
    save(wb, "op_sparse_far_cell", ["sparse", "xfd1048576"])

    # Large research-data sheet (60k cells).
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Readings"
    ws.append(["Run", "T1", "T2", "T3", "T4", "Mean"])
    for r in range(2, 10002):
        ws.append([r - 1, (r * 7) % 101 / 3, (r * 11) % 97 / 7, (r * 13) % 89, (r * 17) % 83 / 11, f"=AVERAGE(B{r}:E{r})"])
    save(wb, "op_large_60k", ["large", "perf", "research-data", "formulas"])

    # Freeze panes, autofilter, print settings, hidden rows/cols.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Roster"
    ws.append(["Name", "Team", "Points"])
    for name, team, pts in [("Avery", "Red", 12), ("Blake", "Blue", 9), ("Casey", "Red", 15)]:
        ws.append([name, team, pts])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:C4"
    ws.row_dimensions[3].hidden = True
    ws.column_dimensions["D"].hidden = True
    ws.print_title_rows = "1:1"
    save(wb, "op_freeze_filter_print", ["freeze-panes", "autofilter", "hidden-rows", "print-titles"])

    # Charts (bar, line, pie) on the data sheet.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Month", "Units", "Returns"])
    for m, u, ret in [("Jan", 30, 2), ("Feb", 42, 3), ("Mar", 38, 1), ("Apr", 51, 4)]:
        ws.append([m, u, ret])
    ws["E1"] = "=SUM(B2:B5)"
    bar = BarChart()
    bar.title = "Units"
    bar.add_data(Reference(ws, min_col=2, min_row=1, max_row=5, max_col=3), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=1, min_row=2, max_row=5))
    ws.add_chart(bar, "G2")
    line = LineChart()
    line.add_data(Reference(ws, min_col=2, min_row=1, max_row=5), titles_from_data=True)
    ws.add_chart(line, "G20")
    pie = PieChart()
    pie.add_data(Reference(ws, min_col=3, min_row=1, max_row=5), titles_from_data=True)
    pie.set_categories(Reference(ws, min_col=1, min_row=2, max_row=5))
    ws.add_chart(pie, "P2")
    save(wb, "op_charts", ["charts", "drawings", "bar-chart", "line-chart", "pie-chart"])

    return made


# ---------------------------------------------------------------- XlsxWriter


def xlsxwriter_files(out: Path) -> dict[str, list[str]]:
    try:
        import xlsxwriter
    except ImportError:
        print("XlsxWriter not installed; skipping XlsxWriter files", file=sys.stderr)
        return {}
    made: dict[str, list[str]] = {}

    def book(name, **options):
        wb = xlsxwriter.Workbook(str(out / name), options)
        wb.set_properties({"created": FIXED, "author": "corpus"})
        return wb

    def done(wb, categories):
        wb.close()
        path = Path(wb.filename)
        _normalize_zip(path)
        made[path.name] = categories

    # Cached formula values.
    wb = book("xw_cached_values.xlsx")
    ws = wb.add_worksheet("Physics")
    ws.write_row(0, 0, ["mass", "accel", "force"])
    ws.write_row(1, 0, [2.0, 9.81])
    ws.write_formula(1, 2, "=A2*B2", None, 19.62)
    ws.write_formula(2, 2, '=IF(C2>10,"heavy","light")', None, "heavy")
    ws.write_formula(3, 2, "=C2>5", None, True)
    ws.write_formula(4, 2, "=1/0", None, "#DIV/0!")
    done(wb, ["cached-values", "formulas", "engineering"])

    # Inline strings (constant_memory mode writes inlineStr).
    wb = book("xw_inline_strings.xlsx", constant_memory=True)
    ws = wb.add_worksheet("Catalog")
    for r in range(20):
        ws.write_row(r, 0, [f"SKU-{r:04d}", r * 1.25, f"Shelf {r % 4}"])
    done(wb, ["inline-strings", "inventory"])

    # Charts and an image.
    wb = book("xw_chart_image.xlsx")
    ws = wb.add_worksheet("Trial Data")
    ws.write_row(0, 0, ["Week", "Control", "Treatment"])
    for r in range(1, 9):
        ws.write_row(r, 0, [r, 10 + r, 10 + r * 1.6])
    chart = wb.add_chart({"type": "line"})
    chart.add_series({"name": "='Trial Data'!$B$1", "categories": "='Trial Data'!$A$2:$A$9", "values": "='Trial Data'!$B$2:$B$9"})
    chart.add_series({"name": "='Trial Data'!$C$1", "categories": "='Trial Data'!$A$2:$A$9", "values": "='Trial Data'!$C$2:$C$9"})
    ws.insert_chart("E2", chart)
    scatter = wb.add_chart({"type": "scatter"})
    scatter.add_series({"categories": "='Trial Data'!$B$2:$B$9", "values": "='Trial Data'!$C$2:$C$9"})
    ws.insert_chart("E20", scatter)
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "logo.png"
        png.write_bytes(handbuilt.tiny_png())
        ws.insert_image("M2", str(png))
        done(wb, ["charts", "drawings", "images", "line-chart", "scatter-chart", "sheet-names"])

    # Chartsheet.
    wb = book("xw_chartsheet.xlsx")
    ws = wb.add_worksheet("Data")
    ws.write_column(0, 0, [3, 1, 4, 1, 5])
    cs = wb.add_chartsheet("Overview")
    chart = wb.add_chart({"type": "column"})
    chart.add_series({"values": "=Data!$A$1:$A$5"})
    cs.set_chart(chart)
    done(wb, ["chartsheet", "charts"])

    # Dynamic arrays.
    wb = book("xw_dynamic_arrays.xlsx")
    ws = wb.add_worksheet("Scores")
    ws.write_column(0, 0, [88, 72, 95, 60, 72])
    ws.write_dynamic_array_formula("C1:C5", "=SORT(A1:A5)")
    ws.write_dynamic_array_formula("D1:D4", "=UNIQUE(A1:A5)")
    ws.write_formula("E1", "=SUM(A1:A5)", None, 387)
    done(wb, ["dynamic-array", "spill", "metadata"])

    # Array formula.
    wb = book("xw_array_formula.xlsx")
    ws = wb.add_worksheet("Matrix")
    ws.write_column(0, 0, [1, 2, 3])
    ws.write_column(0, 1, [4, 5, 6])
    ws.write_array_formula("D1:D1", "{=SUM(A1:A3*B1:B3)}", None, 32)
    done(wb, ["array-formulas", "cse", "cached-values"])

    # Table with structured references and totals row.
    wb = book("xw_table_totals.xlsx")
    ws = wb.add_worksheet("Expenses")
    ws.add_table("A1:C5", {
        "name": "Expenses",
        "data": [["Travel", 120, 1], ["Meals", 45.5, 2], ["Supplies", 80, 1]],
        "total_row": True,
        "columns": [
            {"header": "Item", "total_string": "Total"},
            {"header": "Amount", "total_function": "sum"},
            {"header": "Count", "total_function": "count"},
        ],
    })
    ws.write_formula("E1", "=SUM(Expenses[Amount])", None, 245.5)
    done(wb, ["tables", "structured-refs", "totals-row", "budget"])

    # Drop-downs and other validation.
    wb = book("xw_dropdowns.xlsx")
    ws = wb.add_worksheet("Orders")
    src = wb.add_worksheet("Sizes List")
    src.write_column(0, 0, ["S", "M", "L", "XL"])
    wb.define_name("Colors", "={\"Red\",\"Green\",\"Blue\"}")
    wb.define_name("Sizes", "='Sizes List'!$A$1:$A$4")
    ws.write_row(0, 0, ["Order", "Size", "Color", "Qty", "Ship date"])
    ws.write_row(1, 0, [1001, "M", "Red", 2])
    ws.data_validation("B2:B50", {"validate": "list", "source": "=Sizes"})
    ws.data_validation("C2:C50", {"validate": "list", "source": ["Red", "Green", "Blue"]})
    ws.data_validation("D2:D50", {"validate": "integer", "criteria": ">", "value": 0, "error_message": "Positive whole numbers only"})
    ws.data_validation("E2:E50", {"validate": "date", "criteria": ">=", "value": dt.date(2024, 1, 1)})
    ws.data_validation("F2:F50", {"validate": "list", "source": "='Sizes List'!$A$1:$A$4", "dropdown": False})
    done(wb, ["data-validation", "dropdowns", "inline-list", "name-list", "range-list", "integer-validation", "date-validation"])

    # Comments.
    wb = book("xw_comments.xlsx")
    ws = wb.add_worksheet("Inventory")
    ws.write("A1", "Pallets")
    ws.write("B1", 14)
    ws.write_comment("B1", "Counted Monday", {"author": "Stock"})
    done(wb, ["comments", "vml"])

    # xlsm with vbaProject.bin.
    wb = book("xw_macro.xlsm")
    ws = wb.add_worksheet("Tools")
    ws.write("A1", 5)
    ws.write_formula("A2", "=A1*2", None, 10)
    with tempfile.TemporaryDirectory() as tmp:
        vba = Path(tmp) / "vbaProject.bin"
        vba.write_bytes(b"synthetic placeholder, not a VBA project")
        wb.add_vba_project(str(vba))
        done(wb, ["vba", "xlsm"])

    # 1904 dates with cached formula values.
    wb = book("xw_1904_dates.xlsx", date_1904=True)
    ws = wb.add_worksheet("Calendar")
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws.write_datetime("A1", dt.datetime(2022, 5, 17), fmt)
    ws.write("B1", 10)
    ws.write_formula("C1", "=A1+B1", fmt, 42882)
    done(wb, ["date-1904", "dates", "cached-values"])

    # Text and number formats as XlsxWriter writes them.
    wb = book("xw_text_formats.xlsx", strings_to_numbers=False)
    ws = wb.add_worksheet("Codes")
    text = wb.add_format({"num_format": "@"})
    ws.write_string("A1", "00501", text)
    ws.write_string("A2", "000", text)
    ws.write_blank("A3", None, text)
    ws.write_number("B1", 501)
    ws.write_string("C1", "12345678901234567890")
    ws.write_rich_string("D1", wb.add_format({"bold": True}), "Zip", " code ", wb.add_format({"italic": True}), "list")
    done(wb, ["text", "text-format", "leading-zeros", "rich-text"])

    # Hyperlinks and defined names (XlsxWriter style).
    wb = book("xw_hyperlinks_names.xlsx")
    ws = wb.add_worksheet("Index")
    other = wb.add_worksheet("Detail Sheet")
    ws.write_url("A1", "https://example.org/docs", string="Docs")
    ws.write_url("A2", "internal:'Detail Sheet'!A1", string="Detail")
    other.write("A1", 99)
    wb.define_name("'Detail Sheet'!Local", "='Detail Sheet'!$A$1")
    wb.define_name("Answer", "='Detail Sheet'!$A$1")
    ws.write_formula("B1", "=Answer+1", None, 100)
    done(wb, ["hyperlinks", "defined-names", "sheet-scoped-names", "sheet-names"])

    # Conditional formatting with data bars and icon sets (x14 extLst).
    wb = book("xw_conditional_ext.xlsx")
    ws = wb.add_worksheet("Metrics")
    ws.write_column(0, 0, [5, 15, 25, 35, 45])
    ws.conditional_format("A1:A5", {"type": "data_bar", "data_bar_2010": True})
    ws.conditional_format("A1:A5", {"type": "icon_set", "icon_style": "3_arrows"})
    done(wb, ["conditional-formatting", "extlst"])

    return made


# --------------------------------------------------------------- LibreOffice


def libreoffice_files(out: Path) -> dict[str, list[str]]:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        print("LibreOffice not on PATH; skipping soffice conversions", file=sys.stderr)
        return {}
    made = {}
    with tempfile.TemporaryDirectory() as folder:
        csv = Path(folder) / "lo_from_csv.csv"
        csv.write_text("item,qty,price\nbolt,10,0.25\nnut,25,0.1\n", encoding="utf-8")
        subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", folder, str(csv)],
                       check=True, capture_output=True, timeout=120)
        shutil.copy(Path(folder) / "lo_from_csv.xlsx", out / "lo_from_csv.xlsx")
        made["lo_from_csv.xlsx"] = ["libreoffice", "csv-origin"]
    return made


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "fixtures/corpus"))
    parser.add_argument("--writers", default="openpyxl,xlsxwriter,handbuilt",
                        help="comma-separated writers to run (others keep their files)")
    parser.add_argument("--libreoffice", action="store_true",
                        help="also run the LibreOffice writer when soffice is on PATH")
    args = parser.parse_args()
    writers = set(args.writers.split(","))
    if args.libreoffice:
        writers.add("libreoffice")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": {}}
    produced = {}
    for writer, function in (("openpyxl", openpyxl_files), ("xlsxwriter", xlsxwriter_files),
                             ("handbuilt", handbuilt.build_all), ("libreoffice", libreoffice_files)):
        if writer not in writers:
            continue
        for name, categories in function(out).items():
            produced[name] = {"writer": writer, "categories": categories}
    manifest["files"].update(produced)
    for name in list(manifest["files"]):
        if not (out / name).exists():
            del manifest["files"][name]
    manifest["files"] = dict(sorted(manifest["files"].items()))
    manifest["note"] = (
        "Generated by tools/corpus/generate_corpus.py from synthetic content. "
        "Expected outcomes live in tools/corpus/expectations.json."
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(produced)} files written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
