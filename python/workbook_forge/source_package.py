"""Conservative fidelity gate for self-contained source compiler bundles."""
import xml.etree.ElementTree as ET
import zipfile
from .source_contract import SourceDiagnostic

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

def check_fidelity(archive: zipfile.ZipFile, *, allow_strings: bool = False, worksheets=None) -> None:
    """Refuse metadata the small self-contained source bundle cannot represent."""
    def refuse(reason="metadata needs a richer source bundle"):
        raise SourceDiagnostic("unsupported_fidelity", reason)

    for part in worksheets or ["xl/worksheets/sheet1.xml"]:
        worksheet = ET.fromstring(archive.read(part))
        if worksheet.attrib:
            refuse()
        allowed = {"worksheet":set(), "dimension":{"ref"}, "cols":set(),
                   "col":{"min","max","width","customWidth"}, "sheetData":set(),
                   "row":{"r"}, "c":{"r","s","t"}, "f":set(), "v":set(), "is":set(),
                   "t":{"{http://www.w3.org/XML/1998/namespace}space"}}
        for node in worksheet.iter():
            tag=node.tag.removeprefix(f"{{{MAIN}}}")
            if tag not in allowed or set(node.attrib)-allowed[tag]:
                refuse()
        for row in worksheet.findall(f"{{{MAIN}}}sheetData/{{{MAIN}}}row"):
            if set(row.attrib) - {"r"}:
                refuse()
            for cell in row:
                if cell.tag != f"{{{MAIN}}}c" or set(cell.attrib) - {"r", "s", "t"}:
                    refuse()
                for content in cell:
                    if content.tag not in {f"{{{MAIN}}}{tag}" for tag in ("f", "v", "is")}:
                        refuse()
                    if content.tag == f"{{{MAIN}}}f" and content.attrib:
                        refuse()
                    if content.tag == f"{{{MAIN}}}is" and not allow_strings:
                        refuse()  # Input is numeric; no rich/string literal cells in v1.
        for inline in worksheet.findall(f".//{{{MAIN}}}is"):
            if any(child.tag != f"{{{MAIN}}}t" or child.attrib.keys() - {"{http://www.w3.org/XML/1998/namespace}space"} for child in inline):
                refuse()
        for column in worksheet.findall(f"{{{MAIN}}}cols/{{{MAIN}}}col"):
            if set(column.attrib) - {"min", "max", "width", "customWidth"}:
                refuse()
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    if workbook.attrib:
        refuse()
    for node in workbook:
        if node.tag == f"{{{MAIN}}}workbookPr" and (set(node.attrib) - {"date1904"} or node.get("date1904", "0") not in {"0", "false"}):
            refuse()
        if node.tag == f"{{{MAIN}}}calcPr" and set(node.attrib) - {"calcMode", "fullCalcOnLoad", "forceFullCalc"}:
            refuse()
        if node.tag == f"{{{MAIN}}}calcPr" and node.get("calcMode", "auto") != "auto":
            refuse()
    sheets_node=workbook.find(f"{{{MAIN}}}sheets")
    if sheets_node is not None and sheets_node.attrib:
        refuse()
    for sheet in workbook.findall(f"{{{MAIN}}}sheets/{{{MAIN}}}sheet"):
        if set(sheet.attrib) - {"name", "sheetId", "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"}:
            refuse()
    package_rel="http://schemas.openxmlformats.org/package/2006/relationships"
    document_rel="http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    sheet_ids={sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") for sheet in workbook.findall(f"{{{MAIN}}}sheets/{{{MAIN}}}sheet")}
    for part,types in [("_rels/.rels",{document_rel+"officeDocument"}),
                       ("xl/_rels/workbook.xml.rels",{document_rel+"worksheet",document_rel+"styles"})]:
        relationships=ET.fromstring(archive.read(part))
        if relationships.tag!=f"{{{package_rel}}}Relationships" or relationships.attrib:
            refuse()
        ids=set()
        worksheet_ids=set()
        styles_count=0
        for relation in relationships:
            if relation.tag!=f"{{{package_rel}}}Relationship" or set(relation.attrib)!={"Id","Type","Target"} or relation.get("Type") not in types:
                refuse()
            if relation.get("Id") in ids:
                refuse()
            ids.add(relation.get("Id"))
            if relation.get("Type")==document_rel+"worksheet":
                worksheet_ids.add(relation.get("Id"))
            if relation.get("Type")==document_rel+"styles":
                styles_count+=1
        if part=="_rels/.rels" and len(ids)!=1:
            refuse()
        if part=="xl/_rels/workbook.xml.rels" and (worksheet_ids!=sheet_ids or styles_count!=1):
            refuse()
    styles = ET.fromstring(archive.read("xl/styles.xml"))
    known = {"styleSheet", "numFmts", "numFmt", "fonts", "font", "b", "color", "name", "sz",
             "fills", "fill", "patternFill", "fgColor", "bgColor", "borders", "border", "left",
             "right", "top", "bottom", "diagonal", "cellStyleXfs", "cellXfs", "xf", "alignment",
             "cellStyles", "cellStyle"}
    attributes = {"numFmts": {"count"}, "numFmt": {"numFmtId", "formatCode"}, "fonts": {"count"},
                  "b": {"val"}, "color": {"rgb"}, "name": {"val"}, "sz": {"val"}, "fills": {"count"},
                  "patternFill": {"patternType"}, "fgColor": {"rgb"}, "bgColor": {"indexed"},
                  "borders": {"count"}, "cellStyleXfs": {"count"}, "cellXfs": {"count"},
                  "xf": {"numFmtId", "fontId", "fillId", "borderId", "xfId", "applyNumberFormat", "applyFont", "applyFill", "applyAlignment"},
                  "alignment": {"horizontal"}, "cellStyles": {"count"}, "cellStyle": {"name", "xfId", "builtinId"}}
    for node in styles.iter():
        tag = node.tag.removeprefix(f"{{{MAIN}}}")
        if tag not in known or set(node.attrib) - attributes.get(tag, set()):
            refuse()
        if tag == "name" and node.get("val") != "Arial":
            refuse()
        if tag == "sz" and node.get("val") != "11":
            refuse()
        if tag in {"left", "right", "top", "bottom", "diagonal"} and (node.attrib or len(node)):
            refuse()
        if tag == "xf" and (node.get("borderId", "0") != "0" or set(node.attrib) - {"numFmtId", "fontId", "fillId", "borderId", "xfId", "applyNumberFormat", "applyFont", "applyFill", "applyAlignment"}):
            refuse()
        if tag == "alignment" and (set(node.attrib) - {"horizontal"} or node.get("horizontal") not in {"left", "center", "right"}):
            refuse()
        if tag == "color" and set(node.attrib) != {"rgb"}:
            refuse()
        if tag == "cellStyle" and (node.get("name") != "Normal" or node.get("builtinId", "0") != "0"):
            refuse()
        if tag == "patternFill" and node.get("patternType") not in {"none", "gray125", "solid"}:
            refuse()
        if tag == "bgColor" and node.get("indexed") != "64":
            refuse()

    # The importer intentionally exposes only this representation. Validate the
    # raw style graph before accepting information its reduced dict would lose.
    from .xlsx import _BUILTIN_FORMATS
    formats = set(_BUILTIN_FORMATS)
    for item in styles.findall(f"{{{MAIN}}}numFmts/{{{MAIN}}}numFmt"):
        try:
            format_id = int(item.get("numFmtId", ""))
        except ValueError:
            refuse("unrepresentable number-format identifier")
        if format_id < 164 or not item.get("formatCode") or format_id in formats:
            refuse("ambiguous or unrepresentable custom number format")
        formats.add(format_id)
    parents = styles.findall(f"{{{MAIN}}}cellStyleXfs/{{{MAIN}}}xf")
    if len(parents) != 1 or len(parents[0]) or any(
        key not in {"numFmtId", "fontId", "fillId", "borderId"} or value != "0"
        for key, value in parents[0].attrib.items()
    ):
        refuse("style inheritance is outside the fresh-generation profile")
    for style in styles.findall(f"{{{MAIN}}}cellStyles/{{{MAIN}}}cellStyle"):
        if style.get("xfId", "0") != "0":
            refuse("named style inheritance is outside the fresh-generation profile")
    fills = styles.findall(f"{{{MAIN}}}fills/{{{MAIN}}}fill")
    for xf in styles.findall(f"{{{MAIN}}}cellXfs/{{{MAIN}}}xf"):
        if xf.get("xfId", "0") != "0" or any(
            xf.get(key, "1") not in {"1", "true"}
            for key in ("applyNumberFormat", "applyFont", "applyFill", "applyAlignment")
        ):
            refuse("disabled style application or inheritance cannot be retained")
        try:
            format_id = int(xf.get("numFmtId", "0"))
            fill_id = int(xf.get("fillId", "0"))
        except ValueError:
            refuse("unrepresentable style identifier")
        if format_id not in formats:
            refuse("unresolved or locale-dependent number format cannot be retained")
        if not 0 <= fill_id < len(fills):
            refuse("cell style references an absent fill")
        patterns = list(fills[fill_id])
        if len(patterns) != 1:
            refuse("fill is outside the fresh-generation profile")
        pattern = patterns[0]
        kind = pattern.get("patternType", "none")
        colors = list(pattern)
        if kind == "none" and not colors:
            continue
        foreground = pattern.find(f"{{{MAIN}}}fgColor")
        if kind != "solid" or foreground is None or len([node for node in colors if node.tag == f"{{{MAIN}}}fgColor"]) != 1:
            refuse("assigned patterned or incomplete fill cannot be retained")
        if len(colors) != len({node.tag for node in colors}):
            refuse("ambiguous fill colors cannot be retained")
