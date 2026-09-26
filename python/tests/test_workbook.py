from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from workbook_forge import ErrorValue, analyze_formula
from workbook_forge.workbook import (
    UnsupportedWorkbook,
    Workbook,
    WorkbookError,
    _read_archive_part,
    _safe_xml,
)


def make_xlsx(path, overrides=None):
    parts = {
        "[Content_Types].xml": b'''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="bin" ContentType="application/octet-stream"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''',
        "_rels/.rels": b'''<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''',
        "xl/workbook.xml": b'''<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>''',
        "xl/_rels/workbook.xml.rels": b'''<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''',
        "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>hello</t></is></c><c r="B1"><f>1+2</f><v>3</v></c></row></sheetData></worksheet>''',
        "custom/custom.bin": b"preserve-this-payload",
    }
    parts.update(overrides or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return parts


def write_parts(path, parts):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def make_table_xlsx(path, worksheet_xml, table_attributes, table_column_xml):
    parts = make_xlsx(path, {"xl/worksheets/sheet1.xml": worksheet_xml})
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>",
        b'<Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/></Types>',
    )
    parts["xl/worksheets/sheet1.xml"] = worksheet_xml.replace(
        b"</worksheet>",
        b'<tableParts count="1"><tablePart r:id="rId2"/></tableParts></worksheet>',
    )
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = b'''<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table" Target="../tables/table1.xml"/></Relationships>'''
    parts["xl/tables/table1.xml"] = (
        '<?xml version="1.0"?><table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'id="1" name="Table1" displayName="Table1" {table_attributes}>'
        f'<tableColumns count="2"><tableColumn id="1" name="Input"/>{table_column_xml}</tableColumns></table>'
    ).encode()
    write_parts(path, parts)
    return parts


def test_reads_inline_strings_and_formulas(tmp_path):
    source = tmp_path / "source.xlsx"
    make_xlsx(source)
    with Workbook.open(source) as workbook:
        assert workbook.sheet_names == ("Sheet1",)
        assert workbook.get("Sheet1", "a1").value == "hello"
        formula = workbook.get("Sheet1", "B1")
        assert formula.formula == "1+2"
        assert formula.value == 3


def test_patches_only_target_parts_and_refuses_overwrite(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "updated.xlsx"
    original = make_xlsx(source)
    with Workbook.open(source) as workbook:
        workbook.set_value("Sheet1", "C2", 4.5)
        workbook.set_formula("Sheet1", "D2", "=SUM(A1:C2)")
        workbook.save_as(output)
        with pytest.raises(FileExistsError):
            workbook.save_as(output)
        with pytest.raises(WorkbookError, match="overwrite"):
            workbook.save_as(source)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("custom/custom.bin") == original["custom/custom.bin"]
        sheet_xml = archive.read("xl/worksheets/sheet1.xml")
        assert b'r="C2"' in sheet_xml and b">4.5<" in sheet_xml
        assert b'r="D2"' in sheet_xml and b"SUM(A1:C2)" in sheet_xml
        assert b"fullCalcOnLoad" in archive.read("xl/workbook.xml")
    with zipfile.ZipFile(source) as archive:
        assert archive.read("xl/worksheets/sheet1.xml") == original["xl/worksheets/sheet1.xml"]


def test_value_only_edit_requests_recalculation(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "updated.xlsx"
    make_xlsx(source)
    with Workbook.open(source) as workbook:
        workbook.set_value("Sheet1", "A1", None)
        workbook.save_as(output)
    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
    calc = root.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}calcPr")
    assert calc is not None
    assert calc.attrib["calcMode"] == "auto"
    assert calc.attrib["fullCalcOnLoad"] == "1"
    assert calc.attrib["forceFullCalc"] == "1"


def test_calc_properties_follow_workbook_schema_order(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "updated.xlsx"
    workbook_xml = b'''<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets><oleSize ref="A1"/><customWorkbookViews/><pivotCaches/><extLst/></workbook>'''
    make_xlsx(source, {"xl/workbook.xml": workbook_xml})
    with Workbook.open(source) as workbook:
        workbook.set_value("Sheet1", "A1", 9)
        workbook.save_as(output)
    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
    names = [child.tag.rsplit("}", 1)[-1] for child in root]
    assert names.index("sheets") < names.index("calcPr")
    assert names.index("calcPr") < names.index("oleSize")
    assert names.index("oleSize") < names.index("customWorkbookViews")
    assert names.index("pivotCaches") < names.index("extLst")


def test_new_cells_and_values_precede_extension_elements(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "updated.xlsx"
    parts = make_xlsx(source)
    parts["xl/worksheets/sheet1.xml"] = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>1</v></c><extLst/></row></sheetData></worksheet>'''
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with Workbook.open(source) as workbook:
        workbook.set_formula("Sheet1", "C1", "=1+2")
        workbook.save_as(output)
    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    row = root.find(f"{ns}sheetData/{ns}row")
    assert row is not None
    assert [child.tag for child in row] == [f"{ns}c", f"{ns}c", f"{ns}extLst"]
    formula_cell = row.findall(f"{ns}c")[1]
    assert [child.tag for child in formula_cell] == [f"{ns}f", f"{ns}v"]


def test_edit_removes_calculation_chain_metadata(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "updated.xlsx"
    parts = make_xlsx(source)
    content_types = parts["[Content_Types].xml"].replace(
        b"</Types>",
        b'<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/></Types>',
    )
    rels = parts["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>",
        b'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain" Target="calcChain.xml"/></Relationships>',
    )
    parts["[Content_Types].xml"] = content_types
    parts["xl/_rels/workbook.xml.rels"] = rels
    parts["xl/calcChain.xml"] = b'''<?xml version="1.0"?><calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><c r="B1" i="1"/></calcChain>'''
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with Workbook.open(source) as workbook:
        workbook.set_value("Sheet1", "A1", 11)
        workbook.save_as(output)
    with zipfile.ZipFile(output) as archive:
        assert "xl/calcChain.xml" not in archive.namelist()
        rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        types_root = ET.fromstring(archive.read("[Content_Types].xml"))
    assert not any(node.attrib.get("Type", "").endswith("/calcChain") for node in rels_root)
    assert not any(node.attrib.get("PartName") == "/xl/calcChain.xml" for node in types_root)


def test_rejects_macro_enabled_file_and_group_formula_edit(tmp_path):
    source = tmp_path / "source.xlsx"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><f t="array" ref="A1:B2">1+1</f><v>2</v></c></row></sheetData></worksheet>'''
        },
    )
    with pytest.raises(UnsupportedWorkbook, match=".xlsx only"):
        Workbook.open(tmp_path / "source.xlsm")
    with Workbook.open(source) as workbook:
        with pytest.raises(UnsupportedWorkbook, match="grouped formula range"):
            workbook.set_value("Sheet1", "B2", 5)
        with pytest.raises(ValueError, match="formula must begin"):
            workbook.set_formula("Sheet1", "C3", "1+2")


def test_rejects_macro_content_even_with_xlsx_suffix(tmp_path):
    source = tmp_path / "misnamed.xlsx"
    make_xlsx(
        source,
        {
            "[Content_Types].xml": b'''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="bin" ContentType="application/octet-stream"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'''
        },
    )
    with pytest.raises(UnsupportedWorkbook, match="macro-enabled"):
        Workbook.open(source)


def test_rejects_vba_part_hidden_in_macro_free_package(tmp_path):
    source = tmp_path / "hidden-vba.xlsx"
    parts = make_xlsx(source)
    parts["xl/vbaProject.bin"] = b"opaque-macro-payload"
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with pytest.raises(UnsupportedWorkbook, match="VBA or macro-enabled"):
        Workbook.open(source)


def test_follows_package_office_document_relationship(tmp_path):
    source = tmp_path / "unusual-layout.xlsx"
    output = tmp_path / "updated.xlsx"
    parts = make_xlsx(source)
    parts["_rels/.rels"] = parts["_rels/.rels"].replace(
        b'Target="xl/workbook.xml"', b'Target="books/main.xml"'
    )
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"/xl/workbook.xml", b"/books/main.xml"
    ).replace(b"/xl/worksheets/sheet1.xml", b"/books/worksheets/sheet1.xml")
    parts["books/main.xml"] = parts.pop("xl/workbook.xml")
    parts["books/_rels/main.xml.rels"] = parts.pop("xl/_rels/workbook.xml.rels")
    parts["books/worksheets/sheet1.xml"] = parts.pop("xl/worksheets/sheet1.xml")
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with Workbook.open(source) as workbook:
        assert workbook.get("Sheet1", "A1").value == "hello"
        workbook.set_value("Sheet1", "C1", 12)
        workbook.save_as(output)
    with zipfile.ZipFile(output) as archive:
        assert b"12" in archive.read("books/worksheets/sheet1.xml")
        assert b"fullCalcOnLoad" in archive.read("books/main.xml")


def test_edit_inputs_reject_invalid_xml_and_excel_lengths(tmp_path):
    source = tmp_path / "source.xlsx"
    make_xlsx(source)
    with Workbook.open(source) as workbook:
        with pytest.raises(ValueError, match="XML 1.0"):
            workbook.set_value("Sheet1", "C1", "invalid\x01text")
        with pytest.raises(ValueError, match="32767-character"):
            workbook.set_value("Sheet1", "C1", "🙂" * 16_384)
        with pytest.raises(ValueError, match="XML 1.0"):
            workbook.set_formula("Sheet1", "C1", "=\x01")
        with pytest.raises(ValueError, match="8192-character"):
            workbook.set_formula("Sheet1", "C1", "=" + "1" * 8_193)


def test_reads_formula_attributes_and_ignores_inline_phonetics(tmp_path):
    source = tmp_path / "source.xlsx"
    parts = make_xlsx(source)
    parts["xl/worksheets/sheet1.xml"] = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><r><t>read</t></r><rPh sb="0" eb="4"><t>phonetic</t></rPh></is></c><c r="B1"><f t="shared" ref="B1:B2" si="3">A1+1</f><v>2</v></c></row></sheetData></worksheet>'''
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with Workbook.open(source) as workbook:
        assert workbook.get("Sheet1", "A1").value == "read"
        formula = workbook.get("Sheet1", "B1")
        assert formula.formula_kind == "shared"
        assert dict(formula.formula_attributes) == {"ref": "B1:B2", "si": "3", "t": "shared"}


def test_rejects_xml_entity_declarations(tmp_path):
    source = tmp_path / "unsafe.xlsx"
    parts = make_xlsx(source)
    parts["xl/workbook.xml"] = b'<!DOCTYPE x [<!ENTITY e "expanded">]><workbook/>'
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with pytest.raises(UnsupportedWorkbook, match="DTD/entity"):
        Workbook.open(source)


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_rejects_xml_entity_declarations_in_wide_encodings(encoding):
    xml = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "expanded">]><workbook>&e;</workbook>'
    byte_order_marks = {
        "utf-16-le": b"\xff\xfe",
        "utf-16-be": b"\xfe\xff",
        "utf-32-le": b"\xff\xfe\x00\x00",
        "utf-32-be": b"\x00\x00\xfe\xff",
    }
    with pytest.raises(UnsupportedWorkbook, match="DTD/entity"):
        _safe_xml(byte_order_marks[encoding] + xml.encode(encoding), "xl/workbook.xml")


def test_open_caps_bytes_emitted_by_archive_member(monkeypatch, tmp_path):
    source = tmp_path / "expanded-member.xlsx"
    make_xlsx(source)
    with zipfile.ZipFile(source) as archive:
        declared_total = sum(info.file_size for info in archive.infolist())
        target_name = archive.infolist()[0].filename

    package_limit = declared_total + 31
    expanded_member = b"x" * (package_limit + 1)
    requested_sizes = []
    original_open = zipfile.ZipFile.open

    class ReaderProxy:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def read(self, size=-1):
            requested_sizes.append(size)
            return self.stream.read(size)

    def audited_open(archive, name, mode="r", pwd=None, *, force_zip64=False):
        part_name = name.filename if isinstance(name, zipfile.ZipInfo) else name
        if part_name == target_name and mode == "r":
            stream = io.BytesIO(expanded_member)
        else:
            stream = original_open(archive, name, mode, pwd, force_zip64=force_zip64)
        return ReaderProxy(stream)

    monkeypatch.setattr(zipfile.ZipFile, "open", audited_open)
    monkeypatch.setattr("workbook_forge.workbook.MAX_PACKAGE_BYTES", package_limit)
    with pytest.raises(UnsupportedWorkbook, match="actual uncompressed size limit"):
        Workbook.open(source)

    assert requested_sizes
    assert all(0 < size <= 64 * 1024 for size in requested_sizes)


def test_archive_part_reads_real_zip_members_in_bounded_chunks(monkeypatch, tmp_path):
    source = tmp_path / "compressed-members.zip"
    payload = b"x" * (256 * 1024)
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/test.xml", payload)

    requested_sizes = []
    original_open = zipfile.ZipFile.open

    class ReaderProxy:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def read(self, size=-1):
            requested_sizes.append(size)
            return self.stream.read(size)

    def audited_open(archive, name, mode="r", pwd=None, *, force_zip64=False):
        stream = original_open(archive, name, mode, pwd, force_zip64=force_zip64)
        return ReaderProxy(stream)

    monkeypatch.setattr(zipfile.ZipFile, "open", audited_open)
    with zipfile.ZipFile(source, "r") as archive:
        result = _read_archive_part(archive, archive.getinfo("xl/test.xml"), 0)

    assert result == payload
    assert len(requested_sizes) > 1
    assert all(0 < size <= 64 * 1024 for size in requested_sizes)


@pytest.mark.parametrize("archive_kind", ["ordinary", "zip64", "low-declared-count"])
def test_open_preflights_actual_package_entry_count(monkeypatch, tmp_path, archive_kind):
    source = tmp_path / f"many-members-{archive_kind}.xlsx"
    if archive_kind == "zip64":
        monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 4)
    make_xlsx(source)

    with zipfile.ZipFile(source) as archive:
        entry_count = len(archive.infolist())

    if archive_kind == "low-declared-count":
        with source.open("r+b") as stream:
            stream.seek(-22, 2)
            end_record = bytearray(stream.read(22))
            assert end_record[:4] == b"PK\x05\x06"
            end_record[8:12] = (1).to_bytes(2, "little") * 2
            stream.seek(-22, 2)
            stream.write(end_record)
    else:
        monkeypatch.setattr("workbook_forge.workbook.MAX_PACKAGE_ENTRIES", entry_count - 1)
    monkeypatch.setattr(
        "workbook_forge.workbook.zipfile.ZipFile",
        lambda *_args, **_kwargs: pytest.fail("ZipFile was constructed before entry preflight"),
    )

    expected_error_type = (
        WorkbookError if archive_kind == "low-declared-count" else UnsupportedWorkbook
    )
    expected_error = (
        "entry count is inconsistent"
        if archive_kind == "low-declared-count"
        else "package entries"
    )
    with pytest.raises(expected_error_type, match=expected_error):
        Workbook.open(source)


def test_open_rejects_nonzero_disk_start_in_central_directory(monkeypatch, tmp_path):
    source = tmp_path / "multi-disk-entry.xlsx"
    parts = make_xlsx(source)
    payload = bytearray(source.read_bytes())
    end_record_offset = payload.rfind(b"PK\x05\x06")
    assert end_record_offset >= 0
    entry_count = int.from_bytes(
        payload[end_record_offset + 10 : end_record_offset + 12], "little"
    )
    central_offset = int.from_bytes(
        payload[end_record_offset + 16 : end_record_offset + 20], "little"
    )

    offset = central_offset
    for _ in range(entry_count):
        assert payload[offset : offset + 4] == b"PK\x01\x02"
        payload[offset + 34 : offset + 36] = (1).to_bytes(2, "little")
        name_length = int.from_bytes(payload[offset + 28 : offset + 30], "little")
        extra_length = int.from_bytes(payload[offset + 30 : offset + 32], "little")
        comment_length = int.from_bytes(payload[offset + 32 : offset + 34], "little")
        offset += 46 + name_length + extra_length + comment_length
    assert offset == end_record_offset
    assert entry_count == len(parts)
    source.write_bytes(payload)

    monkeypatch.setattr(
        "workbook_forge.workbook.zipfile.ZipFile",
        lambda *_args, **_kwargs: pytest.fail("ZipFile was constructed before disk-start preflight"),
    )
    with pytest.raises(UnsupportedWorkbook, match="multi-disk package entries"):
        Workbook.open(source)


def test_open_rejects_real_archive_over_entry_limit_before_zipfile(monkeypatch, tmp_path):
    source = tmp_path / "many-empty-members.xlsx"
    with zipfile.ZipFile(source, "w") as archive:
        for index in range(10_001):
            archive.writestr(f"part-{index:05}.bin", b"")

    monkeypatch.setattr(
        "workbook_forge.workbook.zipfile.ZipFile",
        lambda *_args, **_kwargs: pytest.fail("ZipFile parsed an over-limit directory"),
    )
    with pytest.raises(UnsupportedWorkbook, match="package entries"):
        Workbook.open(source)


def test_open_supports_small_zip64_packages(monkeypatch, tmp_path):
    source = tmp_path / "zip64.xlsx"
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 4)
    make_xlsx(source)

    with Workbook.open(source) as workbook:
        assert workbook.sheet_names == ("Sheet1",)
        assert workbook.get("Sheet1", "A1").value == "hello"


def test_open_closes_archive_and_stream_when_directory_read_fails(monkeypatch, tmp_path):
    source = tmp_path / "source.xlsx"
    make_xlsx(source)
    zip_file_class = zipfile.ZipFile
    original_init = zip_file_class.__init__
    streams = []
    archives = []

    def tracked_init(self, file, *args, **kwargs):
        streams.append(file)
        original_init(self, file, *args, **kwargs)
        archives.append(self)

    def fail_infolist(_self):
        raise RuntimeError("injected directory failure")

    monkeypatch.setattr(zip_file_class, "__init__", tracked_init)
    monkeypatch.setattr(zip_file_class, "infolist", fail_infolist)

    with pytest.raises(RuntimeError, match="injected directory failure"):
        Workbook.open(source)

    assert streams and streams[0].closed
    assert archives and archives[0].fp is None


def test_open_closes_source_stream_when_file_stat_fails(monkeypatch, tmp_path):
    source = tmp_path / "source.xlsx"
    make_xlsx(source)
    original_open = type(source).open
    streams = []

    def tracked_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        streams.append(stream)
        return stream

    def fail_fstat(_file_descriptor):
        raise OSError("injected stat failure")

    monkeypatch.setattr(type(source), "open", tracked_open)
    monkeypatch.setattr("workbook_forge.workbook.os.fstat", fail_fstat)

    with pytest.raises(WorkbookError, match="injected stat failure"):
        Workbook.open(source)

    assert streams and streams[0].closed


def test_preserves_markup_compatibility_namespace_prefixes(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "updated.xlsx"
    parts = make_xlsx(source)
    parts["xl/worksheets/sheet1.xml"] = b'''<?xml version="1.0"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
      xmlns:x14ac="urn:example:x14ac" mc:Ignorable="x14ac">
      <sheetData><row r="1" x14ac:dyDescent="0.25">
        <c r="A1"><v>1</v></c>
      </row></sheetData>
    </worksheet>'''
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)

    with Workbook.open(source) as workbook:
        workbook.set_value("Sheet1", "A2", 2)
        workbook.save_as(output)

    with zipfile.ZipFile(output) as archive:
        rendered = archive.read("xl/worksheets/sheet1.xml")
    root = ET.fromstring(rendered)
    assert root.attrib["{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable"] == "x14ac"
    namespaces = {
        prefix or "": uri
        for _, (prefix, uri) in ET.iterparse(io.BytesIO(rendered), events=("start-ns",))
    }
    assert namespaces["x14ac"] == "urn:example:x14ac"
    assert b"xmlns:x14ac=\"urn:example:x14ac\"" in rendered


def test_analyze_formula_reports_ast_references_without_reading_string_text():
    analysis = analyze_formula("=SUM('Sales Data'!$A$1:$B$2)+C1:C3+\"D5\"")
    assert [(ref.start, ref.end, ref.sheet) for ref in analysis.references] == [
        ("$A$1", "$B$2", "Sales Data"),
        ("C1", "C3", None),
    ]
    assert analysis.functions == ("SUM",)


def test_analyze_formula_handles_long_flat_operator_chains_iteratively():
    formula = "=" + "A1" + "+A1" * 1_000
    analysis = analyze_formula(formula)
    assert len(analysis.references) == 1_001
    assert analysis.functions == ()


def test_calculates_formula_dependencies_and_ignores_cached_values(tmp_path):
    source = tmp_path / "source.xlsx"
    output = tmp_path / "calculated.xlsx"
    parts = make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>5</v></c><c r="B1" s="7"><f ca="1">A1+1</f></c><c r="C1"><f>B1*2</f><v>-900</v></c></row></sheetData></worksheet>'''
        },
    )
    with Workbook.open(source) as workbook:
        result = workbook.calculate_cells_to(output, ["Sheet1!C1"])
        assert result == output
    with Workbook.open(output) as workbook:
        first = workbook.get("Sheet1", "B1")
        second = workbook.get("Sheet1", "C1")
        assert first.value == 6
        assert first.formula == "A1+1"
        assert dict(first.formula_attributes) == {"ca": "1"}
        assert first.style_id == 7
        assert second.value == 12
        assert second.formula == "B1*2"
    with zipfile.ZipFile(output) as archive:
        assert archive.read("custom/custom.bin") == parts["custom/custom.bin"]


def test_calculates_cross_sheet_quoted_range_dependencies(tmp_path):
    source = tmp_path / "cross-sheet.xlsx"
    output = tmp_path / "cross-sheet-calculated.xlsx"
    parts = make_xlsx(source)
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>",
        b'<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
    )
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"</sheets>",
        b'<sheet name="Sales Data" sheetId="2" r:id="rId2"/></sheets>',
    )
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>",
        b'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>',
    )
    parts["xl/worksheets/sheet1.xml"] = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="B1"><f>SUM('Sales Data'!$A$1:$A$2)</f><v>0</v></c></row></sheetData></worksheet>'''
    parts["xl/worksheets/sheet2.xml"] = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>4</v></c></row><row r="2"><c r="A2"><f>A1+2</f><v>-99</v></c></row></sheetData></worksheet>'''
    write_parts(source, parts)

    with Workbook.open(source) as workbook:
        assert workbook.calculate_cells_to(output, [("sheet1", "b1")]) == output
    with Workbook.open(output) as workbook:
        assert workbook.get("Sheet1", "B1").value == 10
        assert workbook.get("Sales Data", "A2").value == 6


def test_writes_typed_formula_caches_and_reads_error_cells(tmp_path):
    source = tmp_path / "typed-values.xlsx"
    output = tmp_path / "typed-values-calculated.xlsx"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="e"><v>#N/A</v></c><c r="B1"><f>1=1</f></c><c r="C1"><f>A1</f><v>not-the-error</v></c><c r="D1"><f>1/0</f></c><c r="E1"><f>\"hello\"</f></c><c r="F1"><f>A99</f></c><c r="G1"><f>\"\"</f></c></row></sheetData></worksheet>'''
        },
    )
    with Workbook.open(source) as workbook:
        assert workbook.get("Sheet1", "A1").value == ErrorValue("#N/A")
        workbook.calculate_cells_to(output, [f"Sheet1!{cell}1" for cell in "BCDEFG"])
    with Workbook.open(output) as workbook:
        assert workbook.get("Sheet1", "B1").value is True
        assert workbook.get("Sheet1", "C1").value == ErrorValue("#N/A")
        assert workbook.get("Sheet1", "D1").value == ErrorValue("#DIV/0!")
        assert workbook.get("Sheet1", "E1").value == "hello"
        assert workbook.get("Sheet1", "F1").value == 0
        assert workbook.get("Sheet1", "G1").value == ""
        assert [workbook.get("Sheet1", f"{cell}1").cell_type for cell in "BCDEFG"] == [
            "b", "e", "e", "str", "n", "str"
        ]


@pytest.mark.parametrize(
    ("sheet_xml", "message"),
    [
        (
            b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><f>B1+1</f></c><c r="B1"><f>A1+1</f></c></row></sheetData></worksheet>''',
            "dependency cycle",
        ),
        (
                b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>1</v></c><c r="B1"><f>UNIQUE(A1:A1)</f></c></row></sheetData></worksheet>''',
            "array formula results",
        ),
        (
            b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="B1"><f t="array" ref="B1:B2">1+1</f></c></row></sheetData></worksheet>''',
            "grouped formula",
        ),
        (
            b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="B1"><f>SEQUENCE(2)</f></c></row></sheetData></worksheet>''',
            "array formula results",
        ),
    ],
)
def test_refuses_unsafe_target_closures_without_output(tmp_path, sheet_xml, message):
    source = tmp_path / "unsafe-closure.xlsx"
    output = tmp_path / "must-not-exist.xlsx"
    make_xlsx(source, {"xl/worksheets/sheet1.xml": sheet_xml})
    with Workbook.open(source) as workbook:
        with pytest.raises(UnsupportedWorkbook, match=message):
            workbook.calculate_cells_to(output, ["B1"])
    assert not output.exists()


def test_unrelated_unsupported_formula_does_not_block_target(tmp_path):
    source = tmp_path / "targeted.xlsx"
    output = tmp_path / "targeted-calculated.xlsx"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>1</v></c><c r="B1"><f>UNIQUE(A1:A1)</f></c><c r="C1"><f>A1+2</f></c></row></sheetData></worksheet>'''
        },
    )
    with Workbook.open(source) as workbook:
        workbook.calculate_cells_to(output, ["C1"])
    with Workbook.open(output) as workbook:
        assert workbook.get("Sheet1", "C1").value == 3
        assert workbook.get("Sheet1", "B1").formula == "UNIQUE(A1:A1)"


def test_rejects_1904_date_system_without_output(tmp_path):
    source = tmp_path / "date1904.xlsx"
    output = tmp_path / "date1904-calculated.xlsx"
    parts = make_xlsx(source)
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"<sheets>", b'<workbookPr date1904="1"/><sheets>'
    )
    parts["xl/worksheets/sheet1.xml"] = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="B1"><f>1+2</f></c></row></sheetData></worksheet>'''
    write_parts(source, parts)
    with Workbook.open(source) as workbook:
        with pytest.raises(UnsupportedWorkbook, match="1904 date system"):
            workbook.calculate_cells_to(output, ["B1"])
    assert not output.exists()


def test_rejects_unknown_error_input_without_output(tmp_path):
    source = tmp_path / "unknown-error.xlsx"
    output = tmp_path / "unknown-error-calculated.xlsx"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="e"><v>#FUTURE!</v></c><c r="B1"><f>A1</f></c></row></sheetData></worksheet>'''
        },
    )
    with Workbook.open(source) as workbook:
        with pytest.raises(WorkbookError, match="invalid Excel error"):
            workbook.calculate_cells_to(output, ["B1"])
    assert not output.exists()


@pytest.mark.parametrize("group_type", ["array", "dataTable"])
def test_refuses_formula_reference_to_grouped_result_follower(tmp_path, group_type):
    source = tmp_path / f"grouped-{group_type}.xlsx"
    output = tmp_path / f"grouped-{group_type}-calculated.xlsx"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": (
                '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row r="1">'
                f'<c r="A1"><f t="{group_type}" ref="A1:B1">1+1</f><v>2</v></c>'
                '<c r="B1"><v>100</v></c>'
                '<c r="C1"><f>B1+1</f><v>101</v></c>'
                '</row></sheetData></worksheet>'
            ).encode()
        },
    )
    with Workbook.open(source) as workbook:
        with pytest.raises(UnsupportedWorkbook, match="grouped formula result range"):
            workbook.calculate_cells_to(output, ["C1"])
        assert workbook.get("Sheet1", "C1").value == 101
    assert not output.exists()


@pytest.mark.parametrize("formula", ["B2+1", "SUM(B1:B2)"])
def test_refuses_reference_to_table_calculated_column_cache(tmp_path, formula):
    source = tmp_path / "table-calculated-column.xlsx"
    output = tmp_path / "table-calculated-column-output.xlsx"
    sheet = (
        '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Input</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Computed</t></is></c>'
        f'<c r="C1"><f>{formula}</f><v>999</v></c></row>'
        '<row r="2"><c r="A2"><v>4</v></c><c r="B2"><v>5</v></c></row>'
        '<row r="3"><c r="A3"><v>8</v></c><c r="B3"><v>9</v></c></row>'
        '</sheetData></worksheet>'
    ).encode()
    make_table_xlsx(
        source,
        sheet,
        'ref="A1:B3" headerRowCount="1" totalsRowCount="0"',
        '<tableColumn id="99" name="Computed"><calculatedColumnFormula>[@Input]+1</calculatedColumnFormula></tableColumn>',
    )
    with Workbook.open(source) as workbook:
        with pytest.raises(UnsupportedWorkbook, match="table formula column"):
            workbook.calculate_cells_to(output, ["C1"])
    assert not output.exists()


@pytest.mark.parametrize(
    "column_xml",
    [
        '<tableColumn id="99" name="Total"><totalsRowFormula>SUBTOTAL(109,[Input])</totalsRowFormula></tableColumn>',
        '<tableColumn id="99" name="Total" totalsRowFunction="sum"/>',
    ],
)
def test_refuses_reference_to_table_totals_row(tmp_path, column_xml):
    source = tmp_path / "table-totals-row.xlsx"
    output = tmp_path / "table-totals-output.xlsx"
    sheet = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Input</t></is></c><c r="B1" t="inlineStr"><is><t>Total</t></is></c><c r="D1"><f>B3+1</f><v>1000</v></c></row><row r="2"><c r="A2"><v>4</v></c><c r="B2"><v>4</v></c></row><row r="3"><c r="A3"><v>8</v></c><c r="B3"><v>12</v></c></row></sheetData></worksheet>'''
    make_table_xlsx(
        source,
        sheet,
        'ref="A1:B3" headerRowCount="1" totalsRowCount="1"',
        column_xml,
    )
    with Workbook.open(source) as workbook:
        with pytest.raises(UnsupportedWorkbook, match="table formula column"):
            workbook.calculate_cells_to(output, ["D1"])
    assert not output.exists()


def test_table_formula_columns_do_not_block_unrelated_targets(tmp_path):
    source = tmp_path / "table-unrelated-target.xlsx"
    output = tmp_path / "table-unrelated-target-output.xlsx"
    sheet = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Input</t></is></c><c r="B1" t="inlineStr"><is><t>Computed</t></is></c><c r="C1"><f>A2+1</f><v>999</v></c></row><row r="2"><c r="A2"><v>4</v></c><c r="B2"><v>5</v></c></row><row r="3"><c r="A3"><v>8</v></c><c r="B3"><v>9</v></c></row></sheetData></worksheet>'''
    make_table_xlsx(
        source,
        sheet,
        'ref="A1:B3" headerRowCount="1" totalsRowCount="0"',
        '<tableColumn id="2" name="Computed"><calculatedColumnFormula>[@Input]+1</calculatedColumnFormula></tableColumn>',
    )
    with Workbook.open(source) as workbook:
        workbook.calculate_cells_to(output, ["C1"])
    with Workbook.open(output) as workbook:
        assert workbook.get("Sheet1", "C1").value == 5


def test_formula_dependency_chain_exceeds_python_recursion_limit(tmp_path):
    source = tmp_path / "deep-formula-chain.xlsx"
    output = tmp_path / "deep-formula-chain-output.xlsx"
    last_row = 1_200
    cells = ['<row r="1"><c r="A1"><v>1</v></c></row>']
    cells.extend(
        f'<row r="{row}"><c r="A{row}"><f>A{row - 1}+1</f><v>0</v></c></row>'
        for row in range(2, last_row + 1)
    )
    sheet = (
        '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(cells)
        + "</sheetData></worksheet>"
    ).encode()
    make_xlsx(source, {"xl/worksheets/sheet1.xml": sheet})
    with Workbook.open(source) as workbook:
        workbook.calculate_cells_to(output, [f"A{last_row}"])
    with Workbook.open(output) as workbook:
        assert workbook.get("Sheet1", f"A{last_row}").value == last_row


def test_grouped_formula_range_analysis_has_a_work_budget(tmp_path, monkeypatch):
    source = tmp_path / "group-analysis-budget.xlsx"
    output = tmp_path / "group-analysis-budget-output.xlsx"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><f t="array" ref="A1:B1">1+1</f><v>2</v></c><c r="B1"><v>2</v></c><c r="C1"><f>B1+1</f></c></row></sheetData></worksheet>'''
        },
    )
    monkeypatch.setattr("workbook_forge.workbook.MAX_CALCULATION_RANGE_CHECKS", 0)
    with Workbook.open(source) as workbook:
        with pytest.raises(UnsupportedWorkbook, match="range analysis exceeds"):
            workbook.calculate_cells_to(output, ["C1"])
    assert not output.exists()


def test_repeated_reference_ranges_count_toward_expansion_work_budget(
    tmp_path, monkeypatch
):
    source = tmp_path / "repeated-reference-budget.xlsx"
    output = tmp_path / "repeated-reference-budget-output.xlsx"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="B1"><f>SUM(A1:A6,A1:A6)</f></c></row></sheetData></worksheet>'''
        },
    )
    monkeypatch.setattr(
        "workbook_forge.workbook.MAX_CALCULATION_REFERENCE_EXPANSION_CELLS", 10
    )
    with Workbook.open(source) as workbook:
        with pytest.raises(UnsupportedWorkbook, match="reference expansion exceeds"):
            workbook.calculate_cells_to(output, ["B1"])
    assert not output.exists()


def test_formula_text_length_has_a_calculation_budget(tmp_path, monkeypatch):
    source = tmp_path / "formula-length-budget.xlsx"
    output = tmp_path / "formula-length-budget-output.xlsx"
    formula = "1+1+1+1+1+1+1"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": (
                '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><f>'
                + formula
                + "</f></c></row></sheetData></worksheet>"
            ).encode()
        },
    )
    monkeypatch.setattr("workbook_forge.workbook.MAX_CALCULATION_FORMULA_CHARS", 8)
    with Workbook.open(source) as workbook:
        with pytest.raises(UnsupportedWorkbook, match="formula exceeds the 8-character"):
            workbook.calculate_cells_to(output, ["A1"])
    assert not output.exists()


def test_shared_string_formula_input_excludes_phonetic_annotation(tmp_path):
    source = tmp_path / "phonetic-shared-string.xlsx"
    output = tmp_path / "phonetic-shared-string-calculated.xlsx"
    parts = make_xlsx(source)
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>",
        b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>',
    )
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>",
        b'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>',
    )
    parts["xl/sharedStrings.xml"] = b'''<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><r><t>base</t></r><rPh sb="0" eb="4"><t>pronunciation</t></rPh></si></sst>'''
    parts["xl/worksheets/sheet1.xml"] = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><f>LEN(A1)</f><v>999</v></c></row></sheetData></worksheet>'''
    write_parts(source, parts)
    with Workbook.open(source) as workbook:
        assert workbook.get("Sheet1", "A1").value == "base"
        workbook.calculate_cells_to(output, ["B1"])
    with Workbook.open(output) as workbook:
        assert workbook.get("Sheet1", "B1").value == 4


def test_calculation_ignores_unrelated_shared_formula_follower(tmp_path):
    source = tmp_path / "shared-follower-unrelated.xlsx"
    output = tmp_path / "shared-follower-unrelated-output.xlsx"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>4</v></c><c r="B1"><f t="shared" ref="B1:B2" si="7">A1+1</f><v>5</v></c><c r="D1"><f>A1+2</f><v>6</v></c></row><row r="2"><c r="A2"><v>10</v></c><c r="B2"><f t="shared" si="7"/><v>11</v></c></row></sheetData></worksheet>'''
        },
    )
    with Workbook.open(source) as workbook:
        workbook.calculate_cells_to(output, ["D1"])
    with Workbook.open(output) as workbook:
        assert workbook.get("Sheet1", "D1").value == 6
        assert workbook.get("Sheet1", "B2").value == 11


def test_value_edit_ignores_unrelated_shared_formula_follower(tmp_path):
    source = tmp_path / "shared-follower-edit.xlsx"
    output = tmp_path / "shared-follower-edit-output.xlsx"
    make_xlsx(
        source,
        {
            "xl/worksheets/sheet1.xml": b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>4</v></c><c r="B1"><f t="shared" ref="B1:B2" si="7">A1+1</f><v>5</v></c></row><row r="2"><c r="A2"><v>10</v></c><c r="B2"><f t="shared" si="7"/><v>11</v></c></row></sheetData></worksheet>'''
        },
    )
    with Workbook.open(source) as workbook:
        workbook.set_value("Sheet1", "C1", 17)
        workbook.save_as(output)
    with Workbook.open(output) as workbook:
        assert workbook.get("Sheet1", "C1").value == 17
        assert workbook.get("Sheet1", "B2").value == 11
