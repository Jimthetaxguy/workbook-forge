"""Modeled corpus shapes for Excel's lists, arrays in cells and nested arrays.

Microsoft announced lists, arrays in cells, nested arrays, the FLATTEN, HAS,
HASANY and HASALL functions and Compatibility Version 3 for the Excel Beta
Channel on 24 September 2026 (Windows 2610 build 20520.20000, Mac 16.114
build 26092111):
https://techcommunity.microsoft.com/blog/microsoft365insiderblog/put-multiple-values-in-one-cell-with-lists-and-arrays-in-excel/4559395

None of these files was captured from Excel. They are MODELED: hand-built
from published pieces, and every place where the packaging is a guess is
named below. Replace them with real Excel Beta captures when one is available
(see PROVENANCE, which generate_corpus.py copies into MANIFEST.json).

What is published, and what is guessed:

- Rich values (``xl/metadata.xml`` valueMetadata with an XLRICHVALUE future
  metadata block, ``xl/richData/rdrichvalue.xml``,
  ``rdrichvaluestructure.xml``) and rich arrays (``xl/richData/rdarray.xml``,
  root ``arrayData`` in the 2017/richdata2 namespace, a structure with
  ``t="_array"`` and one key ``array`` of type ``a``, array values typed
  ``d i b e s r a``) are documented in [MS-XLSX] sections 2.1.x, 2.3.6.1.1
  and 2.6.158 onward. A third-party test of the Beta build reports that an
  in-cell array is saved in ``xl/richData/rdarray.xml``
  (https://spreadsheetweb.com/excel-in-cell-arrays/), so the parts are
  plausible; the exact cell markup is not published.
- GUESSED: the cell's fallback value (``t="e"`` ``#VALUE!``, as linked data
  types write), the ``vm`` index wiring, the formula text Excel writes for a
  formula wrapped in extra braces (``{{1;2;3}}``) and for a nested array
  constant, and the ``_xlfn.`` prefix on FLATTEN, HAS, HASANY and HASALL.
- The Compatibility Version element (``xlwcv:version setVersion`` in the
  2024/workbookCompatibilityVersion namespace, workbook extLst entry
  ``{D14903EA-33C4-47F7-8F05-3474C54BE107}``) is documented in [MS-XLSX]
  2.6.260 for version 2; ``setVersion="3"`` is GUESSED from it.
"""
from __future__ import annotations

import handbuilt as hb
from handbuilt import DECL, MAIN, minimal_styles, sheet_xml, simple

RD = "http://schemas.microsoft.com/office/spreadsheetml/2017/richdata"
RD2 = "http://schemas.microsoft.com/office/spreadsheetml/2017/richdata2"
RELS_2017 = "http://schemas.microsoft.com/office/2017/06/relationships"
MD_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheetMetadata+xml"
MD_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sheetMetadata"
XLWCV = "http://schemas.microsoft.com/office/spreadsheetml/2024/workbookCompatibilityVersion"
XLWCV_URI = "{D14903EA-33C4-47F7-8F05-3474C54BE107}"

MODELED = "modeled, not captured from Excel"
REPLACE = (
    "Replace with a real capture from Excel Beta (Windows 2610 build 20520.20000+ or "
    "Mac 16.114 build 26092111+) once one is available; the cell markup is a guess."
)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _array_value(item) -> str:
    """One CT_ArrayValue. ``("a", n)`` points at rich array n (a nested array)."""
    if isinstance(item, tuple):
        kind, body = item
        return f'<v t="{kind}">{body}</v>'
    if isinstance(item, bool):
        return f'<v t="b">{int(item)}</v>'
    if isinstance(item, (int, float)):
        return f"<v>{item}</v>"
    return f'<v t="s">{_esc(item)}</v>'


def _rich_array_package(arrays: list[tuple[int, int, list]], cell_arrays: list[int]) -> tuple[dict, dict, list]:
    """Parts, content-type overrides and workbook relationships for rich arrays.

    ``arrays`` holds (rows, columns, values) for each rich array; ``cell_arrays``
    names, for each rich value (vm 1, 2, ...), the rich array it wraps.
    """
    count = len(cell_arrays)
    metadata = (
        f'{DECL}<metadata xmlns="{MAIN}" xmlns:xlrd="{RD}">'
        '<metadataTypes count="1"><metadataType name="XLRICHVALUE" minSupportedVersion="120000" copy="1" '
        'pasteAll="1" pasteValues="1" merge="1" splitFirst="1" rowColShift="1" clearFormats="1" '
        'clearComments="1" assign="1" coerce="1"/></metadataTypes>'
        f'<futureMetadata name="XLRICHVALUE" count="{count}">'
        + "".join(
            f'<bk><extLst><ext uri="{{3e2802c4-a4d2-4d8b-9148-e3be6c30e623}}"><xlrd:rvb i="{i}"/></ext></extLst></bk>'
            for i in range(count)
        )
        + f'</futureMetadata><valueMetadata count="{count}">'
        + "".join(f'<bk><rc t="1" v="{i}"/></bk>' for i in range(count))
        + "</valueMetadata></metadata>"
    )
    values = (
        f'{DECL}<rvData xmlns="{RD}" count="{count}">'
        + "".join(f'<rv s="0"><v>{index}</v></rv>' for index in cell_arrays)
        + "</rvData>"
    )
    structures = (
        f'{DECL}<rvStructures xmlns="{RD}" count="1"><s t="_array"><k n="array" t="a"/></s></rvStructures>'
    )
    array_data = (
        f'{DECL}<arrayData xmlns="{RD2}" count="{len(arrays)}">'
        + "".join(
            f'<a r="{rows}" c="{columns}">' + "".join(_array_value(v) for v in items) + "</a>"
            for rows, columns, items in arrays
        )
        + "</arrayData>"
    )
    parts = {
        "xl/metadata.xml": metadata,
        "xl/richData/rdrichvalue.xml": values,
        "xl/richData/rdrichvaluestructure.xml": structures,
        "xl/richData/rdarray.xml": array_data,
    }
    overrides = {
        "xl/metadata.xml": MD_CT,
        "xl/richData/rdrichvalue.xml": "application/vnd.ms-excel.rdrichvalue+xml",
        "xl/richData/rdrichvaluestructure.xml": "application/vnd.ms-excel.rdrichvaluestructure+xml",
        "xl/richData/rdarray.xml": "application/vnd.ms-excel.rdArray+xml",
    }
    workbook_rels = [
        (MD_REL, "metadata.xml", False),
        (f"{RELS_2017}/rdRichValue", "richData/rdrichvalue.xml", False),
        (f"{RELS_2017}/rdRichValueStructure", "richData/rdrichvaluestructure.xml", False),
        (f"{RELS_2017}/rdArray", "richData/rdarray.xml", False),
    ]
    return parts, overrides, workbook_rels


def in_cell_list():
    """Lists (a kind of array in a cell): B2 and B3 each hold a list of names
    as one cell value (vm), the shape Insert > List or Ctrl+J makes."""
    parts, overrides, wb_rels = _rich_array_package(
        [(1, 3, ["Carlos", "Henrietta", "Jacob"]), (1, 1, ["Ana"])], [0, 1]
    )
    rows = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Project</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Owners</t></is></c><c r="C1" t="inlineStr"><is><t>Budget</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>Apollo</t></is></c>'
        '<c r="B2" t="e" vm="1"><v>#VALUE!</v></c><c r="C2"><v>1200</v></c></row>'
        '<row r="3"><c r="A3" t="inlineStr"><is><t>Borealis</t></is></c>'
        '<c r="B3" t="e" vm="2"><v>#VALUE!</v></c><c r="C3"><v>800</v></c></row>'
    )
    return simple({"Projects": sheet_xml(rows, dimension="A1:C3")}, styles=minimal_styles(),
                  extra_parts=parts, extra_overrides=overrides, extra_wb_rels=wb_rels)


def in_cell_array_formula():
    """Formulas wrapped in extra braces keep their array result in one cell:
    ={{1;2;3}} and ={TEXTSPLIT(A3,",")} return an array held in A1 and B3."""
    parts, overrides, wb_rels = _rich_array_package(
        [(3, 1, [1, 2, 3]), (1, 3, ["4:51", "5:02", "4:58"])], [0, 1]
    )
    rows = (
        '<row r="1"><c r="A1" t="e" vm="1"><f>{{1;2;3}}</f><v>#VALUE!</v></c><c r="B1"><v>7</v></c></row>'
        '<row r="3"><c r="A3" t="inlineStr"><is><t>4:51,5:02,4:58</t></is></c>'
        '<c r="B3" t="e" vm="2"><f>{_xlfn.TEXTSPLIT(A3,",")}</f><v>#VALUE!</v></c></row>'
    )
    return simple({"Splits": sheet_xml(rows, dimension="A1:B3")}, styles=minimal_styles(),
                  extra_parts=parts, extra_overrides=overrides, extra_wb_rels=wb_rels)


def nested_array():
    """A nested array ={{1,2,3};{4,5,6}}: a 2x1 spill whose cells each hold a
    1x3 array, plus a value cell holding an array that contains an array."""
    parts, overrides, wb_rels = _rich_array_package(
        [
            (1, 3, [1, 2, 3]),
            (1, 3, [4, 5, 6]),
            (1, 2, [("a", 3), "tail"]),  # nested: element 1 is rich array 3
            (2, 1, [10, 20]),
        ],
        [0, 1, 2],
    )
    rows = (
        '<row r="1"><c r="A1" t="e" vm="1"><f t="array" ref="A1:A2">{{1,2,3};{4,5,6}}</f><v>#VALUE!</v></c>'
        '<c r="C1" t="e" vm="3"><v>#VALUE!</v></c><c r="D1"><v>5</v></c></row>'
        '<row r="2"><c r="A2" t="e" vm="2"><v>#VALUE!</v></c></row>'
    )
    return simple({"Nested": sheet_xml(rows, dimension="A1:D2")}, styles=minimal_styles(),
                  extra_parts=parts, extra_overrides=overrides, extra_wb_rels=wb_rels)


def flatten_has_functions():
    """FLATTEN, HAS, HASANY and HASALL over ordinary ranges with scalar
    results, so no rich value is involved: only the functions are new."""
    rows = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>sci-fi</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>drama</t></is></c><c r="C1" t="inlineStr"><is><t>space</t></is></c></row>'
        '<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>2</v></c><c r="C2"><v>3</v></c></row>'
        '<row r="3"><c r="A3" t="b"><f>_xlfn.HAS(A1:C1,"space")</f><v>1</v></c>'
        '<c r="B3" t="b"><f>_xlfn.HASANY(A1:C1,{"comedy","drama"})</f><v>1</v></c>'
        '<c r="C3" t="b"><f>_xlfn.HASALL(A1:C1,{"drama","comedy"})</f><v>0</v></c>'
        '<c r="D3"><f>COUNTA(_xlfn.FLATTEN(A1:C2,""))</f><v>6</v></c>'
        '<c r="E3"><f>SUM(A2:C2)</f><v>6</v></c></row>'
    )
    return simple({"Tags": sheet_xml(rows, dimension="A1:E3")}, styles=minimal_styles())


def compat_version_3():
    """A workbook set to Compatibility Version 3 (Formulas > Calculation
    Options), holding only ordinary formulas whose results may differ there."""
    rows = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>caf\u00e9 \U0001F600</t></is></c><c r="B1"><f>LEN(A1)</f><v>6</v></c></row>'
        '<row r="2"><c r="A2"><v>2</v></c><c r="B2"><f>A2*3</f><v>6</v></c></row>'
    )
    ext = (
        f'<extLst><ext uri="{XLWCV_URI}" xmlns:xlwcv="{XLWCV}">'
        '<xlwcv:version setVersion="3" warnBelowVersion="3"/></ext></extLst>'
    )
    return simple({"Calc": sheet_xml(rows, dimension="A1:B2")}, styles=minimal_styles(),
                  wb_extra='<calcPr calcId="191029"/>' + ext)


BUILDERS = {
    "hb_in_cell_list": (in_cell_list, ["in-cell-array", "list", "rich-value", "metadata", "modeled"]),
    "hb_in_cell_array_formula": (in_cell_array_formula, ["in-cell-array", "array-in-cell-formula", "rich-value", "metadata", "modeled"]),
    "hb_nested_array": (nested_array, ["in-cell-array", "nested-array", "spill", "rich-value", "metadata", "modeled"]),
    "hb_flatten_has_functions": (flatten_has_functions, ["in-cell-array-functions", "new-functions", "modeled"]),
    "hb_compat_version_3": (compat_version_3, ["compatibility-version-3", "modeled"]),
}

PROVENANCE = {f"{name}.xlsx": {"provenance": MODELED, "note": REPLACE} for name in BUILDERS}


def build_all(folder) -> dict[str, list[str]]:
    made = {}
    for name, (builder, categories) in BUILDERS.items():
        hb.write_package(folder / f"{name}.xlsx", builder())
        made[f"{name}.xlsx"] = categories
    return made
