"""Bounded OOXML workbook reader and patch writer.

Only edited worksheet and workbook metadata parts are regenerated. All other
package members are copied through byte-for-byte at the uncompressed-part
level. It can calculate explicitly targeted formulas with the bounded Workbook
Forge evaluator. It never executes macros or external data sources.
"""

from __future__ import annotations

import io
import math
import os
import re
import struct
import tempfile
import threading
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

from . import ArrayValue, ErrorValue, analyze_formula, evaluate_result
from .catalog import function_status
from .xml_patterns import XMLLimitError, XMLPatternError, parse_xml, rich_text, select_path

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
OFFICE_DOCUMENT_REL = f"{REL_DOC}/officeDocument"
WORKSHEET_REL = f"{REL_DOC}/worksheet"
TABLE_REL = f"{REL_DOC}/table"
CALC_CHAIN_REL = f"{REL_DOC}/calcChain"
WORKBOOK_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
WORKSHEET_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
TABLE_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"
MACRO_WORKBOOK_CONTENT_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"m": MAIN, "r": REL_DOC}
CELL_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})$")
RANGE_RE = re.compile(
    r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})(?::\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6}))?$"
)
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_COMPRESSED_PACKAGE_BYTES = 130 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 10_000
MAX_CENTRAL_DIRECTORY_BYTES = 32 * 1024 * 1024
MAX_XML_PART_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_READ_CHUNK_BYTES = 64 * 1024
MAX_CALCULATION_FORMULA_CHARS = 100_000
MAX_CALCULATION_REFERENCE_CELLS = 100_000
MAX_CALCULATION_REFERENCE_EXPANSION_CELLS = 250_000
ZIP_CENTRAL_DIRECTORY_RECORD = struct.Struct("<4s6H3I5H2I")
ZIP_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
MAX_CALCULATION_RANGE_CHECKS = 250_000
EXCEL_ERROR_CODES = frozenset(
    {"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#N/A!", "#SPILL!", "#CALC!"}
)
_XML_SERIALIZE_LOCK = threading.RLock()
_WORKBOOK_ORDER = (
    "fileVersion",
    "fileSharing",
    "workbookPr",
    "workbookProtection",
    "bookViews",
    "sheets",
    "functionGroups",
    "externalReferences",
    "definedNames",
    "calcPr",
    "oleSize",
    "customWorkbookViews",
    "pivotCaches",
    "smartTagPr",
    "smartTagTypes",
    "webPublishing",
    "fileRecoveryPr",
    "webPublishObjects",
    "extLst",
)


class WorkbookError(Exception):
    """Base error for unsupported or malformed workbook packages."""


class UnsupportedWorkbook(WorkbookError):
    """The package is valid but outside this adapter's explicit support."""


@dataclass(frozen=True)
class Cell:
    """One cell's stored value, formula, and style reference."""

    value: Any = None
    formula: str | None = None
    formula_kind: str | None = None
    style_id: int | None = None
    cell_type: str | None = None
    formula_attributes: tuple[tuple[str, str], ...] = ()


def _q(name: str) -> str:
    return f"{{{MAIN}}}{name}"


def _safe_xml(data: bytes, part: str) -> ET.Element:
    try:
        return parse_xml(data, max_bytes=MAX_XML_PART_BYTES)
    except XMLLimitError as exc:
        raise UnsupportedWorkbook(f"{exc}: {part}") from exc
    except XMLPatternError as exc:
        raise WorkbookError(f"invalid XML in {part}: {exc}") from exc


def _read_archive_part(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    total_bytes: int,
) -> bytes:
    """Read a ZIP member with a bounded decompression request size."""
    name = info.filename
    part_limit = MAX_XML_PART_BYTES if name.lower().endswith((".xml", ".rels")) else MAX_PACKAGE_BYTES
    if info.file_size > part_limit:
        raise UnsupportedWorkbook(f"package part exceeds size limit: {name}")
    if total_bytes + info.file_size > MAX_PACKAGE_BYTES:
        raise UnsupportedWorkbook("workbook exceeds uncompressed size limit")

    buffer = bytearray()
    with archive.open(info, "r") as member:
        while True:
            remaining = min(
                part_limit - len(buffer),
                MAX_PACKAGE_BYTES - total_bytes - len(buffer),
            )
            read_size = min(MAX_ARCHIVE_READ_CHUNK_BYTES, remaining + 1)
            chunk = member.read(read_size)
            if not chunk:
                break
            if len(buffer) + len(chunk) > part_limit:
                raise UnsupportedWorkbook(f"package part exceeds size limit: {name}")
            if total_bytes + len(buffer) + len(chunk) > MAX_PACKAGE_BYTES:
                raise UnsupportedWorkbook("workbook exceeds actual uncompressed size limit")
            buffer.extend(chunk)

    if len(buffer) != info.file_size:
        raise WorkbookError(f"package member size does not match its ZIP record: {name}")
    return bytes(buffer)


def _preflight_archive_directory(source_stream: BinaryIO, source_size: int) -> int:
    """Bound and count ZIP directory records before ZipFile allocates ZipInfo objects."""
    end_record_reader = getattr(zipfile, "_EndRecData", None)
    end_record_indexes = (
        "_ECD_DISK_NUMBER",
        "_ECD_DISK_START",
        "_ECD_ENTRIES_THIS_DISK",
        "_ECD_ENTRIES_TOTAL",
        "_ECD_SIZE",
        "_ECD_OFFSET",
        "_ECD_LOCATION",
    )
    if end_record_reader is None or any(
        not hasattr(zipfile, name) for name in end_record_indexes
    ):
        raise UnsupportedWorkbook("this Python runtime cannot safely inspect ZIP metadata")

    try:
        end_record = end_record_reader(source_stream)
        if not end_record:
            raise WorkbookError("cannot open workbook: ZIP end record is missing")
        disk_number = end_record[zipfile._ECD_DISK_NUMBER]
        disk_start = end_record[zipfile._ECD_DISK_START]
        entries_on_disk = end_record[zipfile._ECD_ENTRIES_THIS_DISK]
        declared_entries = end_record[zipfile._ECD_ENTRIES_TOTAL]
        directory_size = end_record[zipfile._ECD_SIZE]
        directory_offset = end_record[zipfile._ECD_OFFSET]
        record_location = end_record[zipfile._ECD_LOCATION]
    except (OSError, zipfile.BadZipFile, struct.error, IndexError, TypeError) as exc:
        raise WorkbookError(f"cannot inspect workbook ZIP directory: {exc}") from exc
    finally:
        source_stream.seek(0)

    if not all(
        isinstance(value, int) and value >= 0
        for value in (
            disk_number,
            disk_start,
            entries_on_disk,
            declared_entries,
            directory_size,
            directory_offset,
            record_location,
        )
    ):
        raise WorkbookError("workbook ZIP directory metadata is invalid")
    if disk_number != 0 or disk_start != 0 or entries_on_disk != declared_entries:
        raise UnsupportedWorkbook("multi-disk or inconsistent ZIP directories are not supported")
    if declared_entries > MAX_PACKAGE_ENTRIES:
        raise UnsupportedWorkbook(
            f"workbook has more than {MAX_PACKAGE_ENTRIES} package entries"
        )
    if directory_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise UnsupportedWorkbook("workbook ZIP directory exceeds size limit")

    # Match ZipFile's concatenated-archive offset calculation without reading
    # the whole directory into memory.
    concat_offset = record_location - directory_size - directory_offset
    directory_start = directory_offset + concat_offset
    directory_end = directory_start + directory_size
    if (
        directory_start < 0
        or directory_end != record_location
        or record_location > source_size
    ):
        raise WorkbookError("workbook ZIP directory offsets are invalid")

    source_stream.seek(directory_start)
    observed_entries = 0
    remaining = directory_size
    while remaining:
        if remaining < ZIP_CENTRAL_DIRECTORY_RECORD.size:
            raise WorkbookError("workbook ZIP directory has a truncated record")
        raw_record = source_stream.read(ZIP_CENTRAL_DIRECTORY_RECORD.size)
        if len(raw_record) != ZIP_CENTRAL_DIRECTORY_RECORD.size:
            raise WorkbookError("workbook ZIP directory is truncated")
        record = ZIP_CENTRAL_DIRECTORY_RECORD.unpack(raw_record)
        if record[0] != ZIP_CENTRAL_DIRECTORY_SIGNATURE:
            raise WorkbookError("workbook ZIP directory has an invalid record signature")
        if record[13] != 0:
            raise UnsupportedWorkbook("multi-disk package entries are not supported")

        variable_size = record[10] + record[11] + record[12]
        record_size = ZIP_CENTRAL_DIRECTORY_RECORD.size + variable_size
        if record_size > remaining:
            raise WorkbookError("workbook ZIP directory record exceeds its declared boundary")
        source_stream.seek(variable_size, os.SEEK_CUR)
        remaining -= record_size
        observed_entries += 1
        if observed_entries > MAX_PACKAGE_ENTRIES:
            raise UnsupportedWorkbook(
                f"workbook has more than {MAX_PACKAGE_ENTRIES} package entries"
            )

    if observed_entries != declared_entries:
        raise WorkbookError("workbook ZIP directory entry count is inconsistent")
    source_stream.seek(0)
    return observed_entries


def _root_namespaces(data: bytes) -> tuple[tuple[str, str], ...]:
    """Collect namespace declarations on the package part's root element."""
    declarations: list[tuple[str, str]] = []
    depth = 0
    for event, value in ET.iterparse(
        io.BytesIO(data), events=("start-ns", "start", "end")
    ):
        if event == "start-ns" and depth == 0:
            prefix, uri = value
            declarations.append((prefix or "", uri))
        elif event == "start":
            depth += 1
        elif event == "end":
            depth -= 1
    return tuple(declarations)


def _xml_tag_end(data: bytes, start: int) -> int:
    """Find a start tag's closing `>` while respecting quoted attributes."""
    quote: int | None = None
    for index in range(start, len(data)):
        byte = data[index]
        if quote is not None:
            if byte == quote:
                quote = None
        elif byte in (ord('"'), ord("'")):
            quote = byte
        elif byte == ord(">"):
            return index
    raise WorkbookError("serialized XML has an unterminated root start tag")


def _serialize_xml(root: ET.Element, original: bytes, part: str) -> bytes:
    """Serialize an edited XML part while retaining its original root prefixes.

    Prefixes listed in markup-compatibility attributes such as
    `mc:Ignorable` are values, not QName nodes. ElementTree cannot infer that
    those bindings still need declarations, so we restore every root binding
    from the original part after serialization.
    """
    namespaces = _root_namespaces(original)
    with _XML_SERIALIZE_LOCK:
        for prefix, uri in namespaces:
            if re.fullmatch(r"ns[0-9]+", prefix):
                raise UnsupportedWorkbook(
                    f"cannot safely preserve reserved XML namespace prefix {prefix!r} in {part}"
                )
            ET.register_namespace(prefix, uri)
        rendered = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    first_tag = rendered.find(b"<", rendered.find(b"?>") + 2)
    if first_tag < 0:
        raise WorkbookError(f"serialized XML has no root element: {part}")
    tag_end = _xml_tag_end(rendered, first_tag)
    root_tag = rendered[first_tag : tag_end + 1]
    declared = {
        (match.group(1) or b"").decode("utf-8"): match.group(3).decode("utf-8")
        for match in re.finditer(rb"\sxmlns(?::([A-Za-z_][\w.-]*))?=(['\"])(.*?)\2", root_tag)
    }
    missing: list[bytes] = []
    for prefix, uri in namespaces:
        if prefix not in declared:
            attribute = "xmlns" if not prefix else f"xmlns:{prefix}"
            missing.append(f" {attribute}={quoteattr(uri)}".encode("utf-8"))
    if not missing:
        return rendered
    return rendered[:tag_end] + b"".join(missing) + rendered[tag_end:]


def _column_number(address: str) -> int:
    match = CELL_RE.fullmatch(address)
    if not match:
        raise ValueError(f"invalid A1 cell reference: {address}")
    column = 0
    for char in match.group(1).upper():
        column = column * 26 + ord(char) - 64
    row = int(match.group(2))
    if column > 16_384 or row > 1_048_576:
        raise ValueError(f"cell reference is outside Excel worksheet bounds: {address}")
    return column


def _normal_address(address: str) -> str:
    _column_number(address)
    return address.replace("$", "").upper()


def _resolve_part(base: str, target: str) -> str:
    if "?" in target or "#" in target:
        raise UnsupportedWorkbook("relationship target contains a URI query or fragment")
    target = unquote(target)
    target = target.replace("\\", "/")
    if target.startswith("/"):
        resolved = target.lstrip("/")
    else:
        resolved = os.path.normpath(os.path.join(os.path.dirname(base), target))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise UnsupportedWorkbook("relationship target escapes package root")
    return resolved.replace("\\", "/")


def _relationship_part(part: str) -> str:
    directory, filename = os.path.split(part)
    return f"{directory + '/' if directory else ''}_rels/{filename}.rels"


def _validate_xml_text(value: str, label: str, max_utf16_units: int | None = None) -> None:
    units = 0
    for char in value:
        codepoint = ord(char)
        if not (
            codepoint in (0x9, 0xA, 0xD)
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        ):
            raise ValueError(f"{label} contains a character not allowed by XML 1.0")
        units += 2 if codepoint > 0xFFFF else 1
    if max_utf16_units is not None and units > max_utf16_units:
        raise ValueError(f"{label} exceeds Excel's {max_utf16_units}-character limit")


def _cell_position(address: str) -> tuple[int, int]:
    match = CELL_RE.fullmatch(address)
    if not match:
        raise ValueError(f"invalid A1 cell reference: {address}")
    return _column_number(address), int(match.group(2))


def _column_name(column: int) -> str:
    result = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _cell_address(column: int, row: int) -> str:
    return f"{_column_name(column)}{row}"


def _in_range(address: str, reference: str) -> bool:
    match = RANGE_RE.fullmatch(reference)
    if not match:
        raise UnsupportedWorkbook(f"cannot safely edit malformed formula range: {reference!r}")
    first = _cell_position(match.group(1) + match.group(2))
    last = _cell_position((match.group(3) or match.group(1)) + (match.group(4) or match.group(2)))
    column, row = _cell_position(address)
    min_column, max_column = sorted((first[0], last[0]))
    min_row, max_row = sorted((first[1], last[1]))
    return min_column <= column <= max_column and min_row <= row <= max_row


class Workbook:
    """Read, inspect, and patch a conservative subset of `.xlsx` packages.

    Existing formulas are exposed with their stored caches. Use
    `calculate_cells_to` to calculate targeted scalar formulas and their
    dependencies with the bounded Python evaluator; unsupported closures and
    array results are refused. Edits and calculations ask Excel to recalculate
    on next open. `save_as` always requires a new, non-existing output path.
    """

    def __init__(
        self,
        source: Path,
        archive: zipfile.ZipFile,
        source_stream: BinaryIO,
        parts: dict[str, bytes],
        workbook_part: str,
        content_types: ET.Element,
    ):
        self.source = source
        self._archive = archive
        self._source_stream = source_stream
        self._parts = parts
        self._workbook_part = workbook_part
        self._workbook_rels_part = _relationship_part(workbook_part)
        self._content_types = content_types
        self._content_type_overrides: dict[str, str] = {}
        self._content_type_defaults: dict[str, str] = {}
        for override in content_types.findall(f"{{{CONTENT_TYPES}}}Override"):
            part_name = override.attrib.get("PartName", "").lstrip("/")
            content_type = override.attrib.get("ContentType")
            if not part_name or not content_type or part_name in self._content_type_overrides:
                raise UnsupportedWorkbook("content type declarations are missing or duplicated")
            self._content_type_overrides[part_name] = content_type
        for default in content_types.findall(f"{{{CONTENT_TYPES}}}Default"):
            extension = default.attrib.get("Extension", "").lower()
            content_type = default.attrib.get("ContentType")
            if not extension or not content_type or extension in self._content_type_defaults:
                raise UnsupportedWorkbook("content type defaults are missing or duplicated")
            self._content_type_defaults[extension] = content_type
        self._changed_sheets: dict[str, ET.Element] = {}
        self._changed_workbook: ET.Element | None = None
        self._changed_misc: dict[str, bytes] = {}
        self._removed_parts: set[str] = set()
        self._calc_chain_checked = False
        self._sheet_parts: dict[str, str] = {}
        self._cells: dict[str, dict[str, ET.Element]] = {}
        self._table_formula_ranges: dict[
            str, list[tuple[int, int, int, int, str]]
        ] = {}
        self._shared_strings: list[str] = []
        self._uses_1904_date_system = False
        self._load()

    @classmethod
    def open(cls, path: str | Path) -> Workbook:
        source = Path(path).resolve()
        if source.suffix.lower() != ".xlsx":
            raise UnsupportedWorkbook("v0.1 accepts .xlsx only; register an audited adapter for other formats")
        source_stream: BinaryIO | None = None
        archive: zipfile.ZipFile | None = None
        transferred = False
        try:
            try:
                source_stream = source.open("rb")
                source_size = os.fstat(source_stream.fileno()).st_size
            except OSError as exc:
                raise WorkbookError(f"cannot open workbook: {exc}") from exc
            if source_size > MAX_COMPRESSED_PACKAGE_BYTES:
                raise UnsupportedWorkbook("compressed workbook exceeds size limit")
            entry_count = _preflight_archive_directory(source_stream, source_size)
            source_stream.seek(0)
            try:
                archive = zipfile.ZipFile(source_stream, "r")
            except (OSError, zipfile.BadZipFile) as exc:
                raise WorkbookError(f"cannot open workbook: {exc}") from exc
            infos = archive.infolist()
            if len(infos) != entry_count:
                raise WorkbookError("workbook ZIP directory changed after preflight")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise UnsupportedWorkbook("duplicate package part names are not supported")
            if sum(info.file_size for info in infos) > MAX_PACKAGE_BYTES:
                raise UnsupportedWorkbook("workbook exceeds uncompressed size limit")
            if (
                "_rels/.rels" not in names
                or "[Content_Types].xml" not in names
            ):
                raise WorkbookError("not a complete Open Packaging Convention package")

            parts: dict[str, bytes] = {}
            total_bytes = 0
            for info in infos:
                content = _read_archive_part(archive, info, total_bytes)
                parts[info.filename] = content
                total_bytes += len(content)
            root_rels = _safe_xml(parts["_rels/.rels"], "_rels/.rels")
            if root_rels.tag != f"{{{PKG_REL}}}Relationships":
                raise UnsupportedWorkbook("package root relationships use an unsupported namespace")
            root_relationships = root_rels.findall(f"{{{PKG_REL}}}Relationship")
            root_ids = [rel.attrib.get("Id") for rel in root_relationships]
            if any(not rel_id for rel_id in root_ids) or len(root_ids) != len(set(root_ids)):
                raise UnsupportedWorkbook("package root relationship IDs are missing or duplicated")
            office_documents = [
                rel for rel in root_relationships
                if rel.attrib.get("Type") == OFFICE_DOCUMENT_REL
            ]
            if len(office_documents) != 1 or office_documents[0].attrib.get("TargetMode") == "External":
                raise WorkbookError("package must have one internal officeDocument relationship")
            workbook_part = _resolve_part("", office_documents[0].attrib.get("Target", ""))
            if workbook_part not in parts:
                raise WorkbookError(f"workbook part is absent: {workbook_part}")
            content_types = _safe_xml(parts["[Content_Types].xml"], "[Content_Types].xml")
            if content_types.tag != f"{{{CONTENT_TYPES}}}Types":
                raise UnsupportedWorkbook("content type declarations use an unsupported namespace")
            declared_macro_content = any(
                "macroenabled" in node.attrib.get("ContentType", "").casefold()
                or "vbaproject" in node.attrib.get("ContentType", "").casefold()
                for node in content_types
            )
            has_vba_part = any(Path(name).name.casefold() == "vbaproject.bin" for name in names)
            if declared_macro_content or has_vba_part:
                raise UnsupportedWorkbook("VBA or macro-enabled package content is not accepted")
            workbook_type = next(
                (
                    node.attrib.get("ContentType")
                    for node in content_types.findall(f"{{{CONTENT_TYPES}}}Override")
                    if node.attrib.get("PartName", "").lstrip("/") == workbook_part
                ),
                None,
            )
            if workbook_type == MACRO_WORKBOOK_CONTENT_TYPE:
                raise UnsupportedWorkbook("macro-enabled workbook content is not accepted")
            if workbook_type != WORKBOOK_CONTENT_TYPE:
                raise UnsupportedWorkbook("workbook part must use the macro-free XLSX content type")
            workbook_rels_part = _relationship_part(workbook_part)
            if workbook_rels_part not in parts:
                raise WorkbookError(f"workbook relationships are absent: {workbook_rels_part}")
            workbook = cls(
                source,
                archive,
                source_stream,
                parts,
                workbook_part,
                content_types,
            )
            transferred = True
            return workbook
        finally:
            if not transferred:
                try:
                    if archive is not None:
                        archive.close()
                finally:
                    if source_stream is not None:
                        source_stream.close()

    def close(self) -> None:
        try:
            self._archive.close()
        finally:
            self._source_stream.close()

    def __enter__(self) -> Workbook:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def sheet_names(self) -> tuple[str, ...]:
        return tuple(self._sheet_parts)

    def calculate_cells_to(
        self,
        output: str | Path,
        targets: Iterable[str | tuple[str, str]],
    ) -> Path:
        """Calculate requested formula cells and their formula dependencies.

        A target may be ``"Sheet1!C1"``, a bare A1 address on the first sheet,
        or a ``(sheet, address)`` pair. Formula caches are never used as inputs.
        The dependency closure is evaluated and validated before any worksheet
        XML is changed; array results and unsupported dependency features fail
        without creating the output package.
        """

        if isinstance(targets, str):
            target_items: tuple[str | tuple[str, str], ...] = (targets,)
        else:
            target_items = tuple(targets)
        if not target_items:
            raise ValueError("at least one formula target is required")
        destination = Path(output).resolve()
        if destination == self.source:
            raise WorkbookError("refusing to overwrite the source workbook")
        if destination.exists():
            raise FileExistsError(destination)
        if self._uses_1904_date_system:
            raise UnsupportedWorkbook(
                "formula calculation does not support the workbook's 1904 date system"
            )

        sheet_by_fold = {name.casefold(): name for name in self._sheet_parts}

        def normalize_target(item: str | tuple[str, str]) -> tuple[str, str]:
            if isinstance(item, tuple) and len(item) == 2:
                sheet, address = item
                if not isinstance(sheet, str) or not isinstance(address, str):
                    raise TypeError("target pairs must contain a sheet name and A1 address")
            elif isinstance(item, str):
                if "!" in item:
                    sheet, address = item.rsplit("!", 1)
                    if sheet.startswith("'") and sheet.endswith("'"):
                        sheet = sheet[1:-1].replace("''", "'")
                else:
                    sheet, address = self.sheet_names[0], item
            else:
                raise TypeError("targets must be A1 strings or (sheet, address) pairs")
            canonical = sheet_by_fold.get(sheet.casefold())
            if canonical is None:
                raise KeyError(f"unknown worksheet: {sheet}")
            return canonical, _normal_address(address)

        formula_cells: dict[
            tuple[str, int, int], tuple[str, str, ET.Element, ET.Element, str]
        ] = {}
        grouped_formula_ranges_by_sheet: dict[
            str, list[tuple[int, int, int, int, str]]
        ] = {}
        for sheet, cells in self._cells.items():
            for address, cell in cells.items():
                formula_node = cell.find(_q("f"))
                if formula_node is None:
                    continue
                formula_type = formula_node.attrib.get("t")
                reference = formula_node.attrib.get("ref")
                if formula_type in {"array", "dataTable", "shared"} and reference:
                    match = RANGE_RE.fullmatch(reference)
                    if match is None:
                        raise UnsupportedWorkbook(
                            f"grouped formula has an invalid result range at {sheet}!{address}"
                        )
                    first_column, first_row = _cell_position(
                        match.group(1) + match.group(2)
                    )
                    last_column, last_row = _cell_position(
                        (match.group(3) or match.group(1))
                        + (match.group(4) or match.group(2))
                    )
                    if first_column > last_column or first_row > last_row:
                        raise UnsupportedWorkbook(
                            f"grouped formula has a reversed result range at {sheet}!{address}"
                        )
                    grouped_formula_ranges_by_sheet.setdefault(
                        sheet.casefold(), []
                    ).append(
                        (
                            first_row,
                            last_row,
                            first_column,
                            last_column,
                            f"{sheet}!{address}",
                        )
                    )
                elif formula_type in {"array", "dataTable"}:
                    raise UnsupportedWorkbook(
                        f"grouped formula is missing its result range at {sheet}!{address}"
                    )
                column, row = _cell_position(address)
                key = (sheet.casefold(), row, column)
                formula_cells[key] = (
                    sheet,
                    address,
                    cell,
                    formula_node,
                    formula_node.text or "",
                )

        targets_normalized = tuple(dict.fromkeys(normalize_target(item) for item in target_items))
        root_targets: list[tuple[str, int, int]] = []
        for sheet, address in targets_normalized:
            column, row = _cell_position(address)
            key = (sheet.casefold(), row, column)
            if key not in formula_cells:
                raise ValueError(f"calculation target is not a formula cell: {sheet}!{address}")
            root_targets.append(key)

        references_by_formula: dict[tuple[str, int, int], set[tuple[str, int, int]]] = {}
        colors: dict[tuple[str, int, int], int] = {}
        active_path: list[tuple[str, int, int]] = []
        active_positions: dict[tuple[str, int, int], int] = {}
        order: list[tuple[str, int, int]] = []
        expanded_reference_cells = 0
        reference_expansion_cells = 0
        range_checks = 0

        def label(key: tuple[str, int, int]) -> str:
            sheet, address, *_ = formula_cells[key]
            return f"{sheet}!{address}"

        def reject_special_range(
            sheet_key: str,
            first_row: int,
            last_row: int,
            first_column: int,
            last_column: int,
            context: str,
        ) -> None:
            nonlocal range_checks
            candidates = (
                (
                    grouped_formula_ranges_by_sheet.get(sheet_key, ()),
                    "grouped formula result range",
                ),
                (self._table_formula_ranges.get(sheet_key, ()), "table formula column"),
            )
            for ranges, kind in candidates:
                for (
                    range_first_row,
                    range_last_row,
                    range_first_column,
                    range_last_column,
                    owner,
                ) in ranges:
                    range_checks += 1
                    if range_checks > MAX_CALCULATION_RANGE_CHECKS:
                        raise UnsupportedWorkbook(
                            "grouped/table range analysis exceeds the "
                            f"{MAX_CALCULATION_RANGE_CHECKS}-check calculation limit"
                        )
                    if (
                        first_row <= range_last_row
                        and last_row >= range_first_row
                        and first_column <= range_last_column
                        and last_column >= range_first_column
                    ):
                        raise UnsupportedWorkbook(
                            f"{context} intersects {kind} {owner}"
                        )

        def prepare_formula(key: tuple[str, int, int]) -> set[tuple[str, int, int]]:
            nonlocal expanded_reference_cells, reference_expansion_cells
            sheet, address, _cell, formula_node, formula = formula_cells[key]
            sheet_key, row, column = key
            reject_special_range(
                sheet_key, row, row, column, column, f"formula cell {sheet}!{address}"
            )
            formula_type = formula_node.attrib.get("t")
            if formula_type not in (None, "normal"):
                raise UnsupportedWorkbook(
                    f"grouped formula type {formula_type!r} is unsupported at {sheet}!{address}"
                )
            if not formula:
                raise UnsupportedWorkbook(f"formula text is missing at {sheet}!{address}")
            if len(formula) > MAX_CALCULATION_FORMULA_CHARS:
                raise UnsupportedWorkbook(
                    f"formula exceeds the {MAX_CALCULATION_FORMULA_CHARS}-character calculation limit at {sheet}!{address}"
                )
            try:
                analysis = analyze_formula(formula)
            except (RecursionError, TypeError, ValueError) as error:
                raise UnsupportedWorkbook(
                    f"unsupported formula syntax at {sheet}!{address}: {error}"
                ) from error
            for function in analysis.functions:
                try:
                    status = function_status(function, "python")
                except KeyError as error:
                    raise UnsupportedWorkbook(
                        f"function {function} is not in the Workbook Forge catalog at {sheet}!{address}"
                    ) from error
                if status != "conformance-tested":
                    raise UnsupportedWorkbook(
                        f"function {function} is not conformance-tested in Python at {sheet}!{address}"
                    )

            formula_refs: set[tuple[str, int, int]] = set()
            formula_dependencies: set[tuple[str, int, int]] = set()
            for reference in analysis.references:
                referenced_sheet = reference.sheet or sheet
                canonical = sheet_by_fold.get(referenced_sheet.casefold())
                if canonical is None:
                    raise UnsupportedWorkbook(
                        f"formula references unknown worksheet {referenced_sheet!r} at {sheet}!{address}"
                    )
                first_column, first_row = _cell_position(reference.start)
                last_column, last_row = (
                    _cell_position(reference.end)
                    if reference.end is not None
                    else (first_column, first_row)
                )
                if first_column > last_column or first_row > last_row:
                    raise UnsupportedWorkbook(
                        f"reversed ranges are outside workbook calculation at {sheet}!{address}"
                    )
                reject_special_range(
                    canonical.casefold(),
                    first_row,
                    last_row,
                    first_column,
                    last_column,
                    f"dependency from {sheet}!{address}",
                )
                area = (last_column - first_column + 1) * (last_row - first_row + 1)
                if area > MAX_CALCULATION_REFERENCE_CELLS:
                    raise UnsupportedWorkbook(
                        f"reference exceeds the {MAX_CALCULATION_REFERENCE_CELLS}-cell calculation limit at {sheet}!{address}"
                    )
                if (
                    reference_expansion_cells + area
                    > MAX_CALCULATION_REFERENCE_EXPANSION_CELLS
                ):
                    raise UnsupportedWorkbook(
                        "reference expansion exceeds the "
                        f"{MAX_CALCULATION_REFERENCE_EXPANSION_CELLS}-cell calculation work limit at {sheet}!{address}"
                    )
                reference_expansion_cells += area
                old_ref_count = len(formula_refs)
                for row in range(first_row, last_row + 1):
                    for column in range(first_column, last_column + 1):
                        formula_refs.add((canonical.casefold(), row, column))
                expanded_reference_cells += len(formula_refs) - old_ref_count
                if expanded_reference_cells > MAX_CALCULATION_REFERENCE_CELLS:
                    raise UnsupportedWorkbook(
                        f"dependency closure exceeds the {MAX_CALCULATION_REFERENCE_CELLS}-cell calculation limit"
                    )

            for ref_key in formula_refs:
                if ref_key in formula_cells:
                    formula_dependencies.add(ref_key)
            references_by_formula[key] = formula_refs
            return formula_dependencies

        for target in root_targets:
            # An explicit enter/exit stack keeps dependency depth independent
            # of Python's recursion limit while preserving postorder evaluation.
            stack: list[tuple[tuple[str, int, int], bool]] = [(target, False)]
            while stack:
                key, exiting = stack.pop()
                state = colors.get(key, 0)
                if exiting:
                    if state != 1 or not active_path or active_path[-1] != key:
                        raise WorkbookError("formula dependency traversal state is inconsistent")
                    colors[key] = 2
                    active_path.pop()
                    active_positions.pop(key, None)
                    order.append(key)
                    continue
                if state == 2:
                    continue
                if state == 1:
                    cycle = active_path[active_positions[key] :] + [key]
                    raise UnsupportedWorkbook(
                        "formula dependency cycle: " + " -> ".join(label(item) for item in cycle)
                    )
                colors[key] = 1
                active_positions[key] = len(active_path)
                active_path.append(key)
                formula_dependencies = prepare_formula(key)
                stack.append((key, True))
                for dependency in sorted(formula_dependencies, reverse=True):
                    stack.append((dependency, False))

        # Read only populated, non-formula cells in the requested closure.
        input_values: dict[str, Any] = {}
        all_referenced = set().union(*(references_by_formula[key] for key in order))
        for sheet, cells in self._cells.items():
            for address in cells:
                column, row = _cell_position(address)
                key = (sheet.casefold(), row, column)
                if key not in all_referenced or key in formula_cells:
                    continue
                cell = self.get(sheet, address)
                if cell.cell_type == "d":
                    raise UnsupportedWorkbook(
                        f"ISO date-typed cells are unsupported formula inputs: {sheet}!{address}"
                    )
                if cell.cell_type not in (None, "n", "b", "e", "s", "str", "inlineStr"):
                    raise UnsupportedWorkbook(
                        f"cell type {cell.cell_type!r} is unsupported at {sheet}!{address}"
                    )
                if cell.value is not None:
                    input_values[f"{sheet}!{address}"] = cell.value

        calculated: dict[tuple[str, int, int], Any] = {}
        staged: dict[tuple[str, int, int], Any] = {}
        for key in order:
            sheet, address, _cell, _formula_node, formula = formula_cells[key]
            context: dict[str, Any] = {}
            for ref_key in references_by_formula[key]:
                ref_sheet_key, row, column = ref_key
                ref_address = _cell_address(column, row)
                ref_formula = formula_cells.get(ref_key)
                if ref_formula is not None:
                    if ref_key not in calculated:
                        raise WorkbookError(
                            f"formula dependency was not evaluated before {sheet}!{address}"
                        )
                    context[f"{ref_formula[0]}!{ref_address}"] = calculated[ref_key]
                else:
                    canonical_sheet = sheet_by_fold[ref_sheet_key]
                    value_key = f"{canonical_sheet}!{ref_address}"
                    if value_key in input_values:
                        context[value_key] = input_values[value_key]
            try:
                result = evaluate_result(formula, context, sheet)
            except RecursionError as error:
                raise UnsupportedWorkbook(
                    f"formula evaluation is nested beyond the evaluator limit at {sheet}!{address}"
                ) from error
            if isinstance(result, ArrayValue):
                raise UnsupportedWorkbook(
                    f"array formula results are unsupported at {sheet}!{address}"
                )
            cache_value = self._normalize_formula_cache(result, sheet, address)
            calculated[key] = cache_value
            staged[key] = cache_value

        # Only now is it safe to mutate worksheet XML or workbook calculation metadata.
        for key in staged:
            sheet, address, *_ = formula_cells[key]
            self._formula_cache_cell(sheet, address)
        self._request_recalculation()
        for key, value in staged.items():
            sheet, address, *_ = formula_cells[key]
            self._write_formula_cache(sheet, address, value)
        return self.save_as(destination)

    @staticmethod
    def _normalize_formula_cache(value: Any, sheet: str, address: str) -> Any:
        if isinstance(value, ErrorValue):
            if value.code not in EXCEL_ERROR_CODES:
                raise UnsupportedWorkbook(
                    f"unrecognized formula error {value.code!r} at {sheet}!{address}"
                )
            return value
        if value is None:
            # A scalar formula reference to an empty cell evaluates to numeric
            # zero; an explicit empty-string formula remains a string above.
            return 0
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            try:
                number = float(value)
            except (OverflowError, ValueError) as error:
                raise UnsupportedWorkbook(
                    f"non-finite numeric result at {sheet}!{address}"
                ) from error
            if not math.isfinite(number):
                raise UnsupportedWorkbook(f"non-finite numeric result at {sheet}!{address}")
            return number
        if isinstance(value, str):
            try:
                _validate_xml_text(value, "formula result", 32_767)
            except ValueError as error:
                raise UnsupportedWorkbook(
                    f"formula string result is outside the XLSX cell limit at {sheet}!{address}"
                ) from error
            return value
        raise UnsupportedWorkbook(
            f"unsupported formula result type {type(value).__name__} at {sheet}!{address}"
        )

    def _write_formula_cache(self, sheet: str, address: str, value: Any) -> None:
        cell = self._formula_cache_cell(sheet, address)
        old_value = cell.find(_q("v"))
        if old_value is not None:
            cell.remove(old_value)
        if isinstance(value, ErrorValue):
            cell.set("t", "e")
            cached = ET.Element(_q("v"))
            cached.text = value.code
        elif isinstance(value, bool):
            cell.set("t", "b")
            cached = ET.Element(_q("v"))
            cached.text = "1" if value else "0"
        elif isinstance(value, str):
            cell.set("t", "str")
            cached = ET.Element(_q("v"))
            cached.text = value
        elif value is None:
            cell.attrib.pop("t", None)
            cached = ET.Element(_q("v"))
        else:
            cell.set("t", "n")
            cached = ET.Element(_q("v"))
            cached.text = repr(value)
        self._insert_cell_child(cell, cached, "v")

    def _formula_cache_cell(self, sheet: str, address: str) -> ET.Element:
        """Load and validate an existing formula cell for staged cache writes."""
        if sheet not in self._sheet_parts:
            raise KeyError(f"unknown worksheet: {sheet}")
        address = _normal_address(address)
        part = self._sheet_parts[sheet]
        root = self._changed_sheets.get(part)
        if root is None:
            root = _safe_xml(self._parts[part], part)
            self._changed_sheets[part] = root
            self._cells[sheet] = self._index_cells(root)
        cell = self._cells[sheet].get(address)
        if cell is None or cell.find(_q("f")) is None:
            raise WorkbookError(
                f"formula cache target is not an existing formula cell: {sheet}!{address}"
            )
        return cell

    def _load(self) -> None:
        wb_root = _safe_xml(self._parts[self._workbook_part], self._workbook_part)
        if wb_root.tag != _q("workbook"):
            raise UnsupportedWorkbook("strict OOXML or non-workbook XML is outside the XLSX adapter profile")
        workbook_properties = wb_root.find(_q("workbookPr"))
        date1904 = (
            workbook_properties.attrib.get("date1904")
            if workbook_properties is not None
            else None
        )
        if date1904 is not None and date1904.casefold() not in {"0", "1", "false", "true"}:
            raise UnsupportedWorkbook("workbook date1904 setting is not a recognized Boolean")
        self._uses_1904_date_system = date1904 is not None and date1904.casefold() in {"1", "true"}
        rel_root = _safe_xml(
            self._parts[self._workbook_rels_part], self._workbook_rels_part
        )
        if rel_root.tag != f"{{{PKG_REL}}}Relationships":
            raise UnsupportedWorkbook("workbook relationships use an unsupported namespace")
        relationships = rel_root.findall(f"{{{PKG_REL}}}Relationship")
        rel_ids = [rel.attrib.get("Id") for rel in relationships]
        if any(not rel_id for rel_id in rel_ids) or len(rel_ids) != len(set(rel_ids)):
            raise UnsupportedWorkbook("workbook relationship IDs are missing or duplicated")
        rels = {rel.attrib["Id"]: rel for rel in relationships}
        sheets = wb_root.find(_q("sheets"))
        if sheets is None:
            raise WorkbookError("workbook has no sheet collection")
        folded_names: set[str] = set()
        for sheet in sheets.findall(_q("sheet")):
            name = sheet.attrib.get("name")
            rid = sheet.attrib.get(f"{{{REL_DOC}}}id")
            if not name or rid not in rels:
                raise WorkbookError("worksheet relationship is missing or invalid")
            if len(name) > 31 or name.casefold() in folded_names:
                raise UnsupportedWorkbook("worksheet names are invalid or duplicated")
            folded_names.add(name.casefold())
            relationship = rels[rid]
            if relationship.attrib.get("Type") != WORKSHEET_REL:
                raise UnsupportedWorkbook("chartsheets and non-worksheet sheet types are outside this adapter")
            if relationship.attrib.get("TargetMode") == "External":
                raise UnsupportedWorkbook("worksheet relationships must be internal")
            part = _resolve_part(self._workbook_part, relationship.attrib.get("Target", ""))
            if part not in self._parts:
                raise WorkbookError(f"worksheet part is absent: {part}")
            if self._content_type_for(part) != WORKSHEET_CONTENT_TYPE:
                raise UnsupportedWorkbook(f"worksheet part has an unexpected content type: {part}")
            worksheet_root = _safe_xml(self._parts[part], part)
            if worksheet_root.tag != _q("worksheet"):
                raise UnsupportedWorkbook("strict OOXML or a non-worksheet part is outside the adapter profile")
            self._sheet_parts[name] = part
            self._cells[name] = self._index_cells(worksheet_root)
            self._table_formula_ranges[name.casefold()] = self._load_table_formula_ranges(
                name, worksheet_root, part
            )
        if not self._sheet_parts:
            raise WorkbookError("workbook has no worksheets")
        shared_string_parts = [
            part for part in self._parts if self._content_type_for(part)
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
        ]
        if len(shared_string_parts) > 1:
            raise UnsupportedWorkbook("multiple shared-string tables are not supported")
        if shared_string_parts:
            shared_string_part = shared_string_parts[0]
            root = _safe_xml(self._parts[shared_string_part], shared_string_part)
            if root.tag != _q("sst"):
                raise UnsupportedWorkbook("shared-string part has an unsupported XML root")
            self._shared_strings = [
                self._inline_text(item)
                for item in root.findall(_q("si"))
            ]

    def _load_table_formula_ranges(
        self, sheet: str, worksheet_root: ET.Element, worksheet_part: str
    ) -> list[tuple[int, int, int, int, str]]:
        """Index table columns whose formulas are stored outside worksheet cells."""
        table_parts = worksheet_root.findall(f"{_q('tableParts')}/{_q('tablePart')}")
        if not table_parts:
            return []

        relationships_part = _relationship_part(worksheet_part)
        if relationships_part not in self._parts:
            raise WorkbookError(
                f"worksheet table relationships are absent: {relationships_part}"
            )
        relationships_root = _safe_xml(
            self._parts[relationships_part], relationships_part
        )
        if relationships_root.tag != f"{{{PKG_REL}}}Relationships":
            raise UnsupportedWorkbook(
                f"worksheet relationships use an unsupported namespace: {relationships_part}"
            )
        relationships = relationships_root.findall(f"{{{PKG_REL}}}Relationship")
        ids = [relationship.attrib.get("Id") for relationship in relationships]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise UnsupportedWorkbook(
                f"worksheet relationship IDs are missing or duplicated: {relationships_part}"
            )
        rels = {relationship.attrib["Id"]: relationship for relationship in relationships}

        result: list[tuple[int, int, int, int, str]] = []
        seen_relationships: set[str] = set()
        for table_part in table_parts:
            relationship_id = table_part.attrib.get(f"{{{REL_DOC}}}id")
            if not relationship_id or relationship_id in seen_relationships:
                raise UnsupportedWorkbook(
                    f"worksheet table relationships are missing or duplicated: {sheet}"
                )
            seen_relationships.add(relationship_id)
            relationship = rels.get(relationship_id)
            if relationship is None or relationship.attrib.get("Type") != TABLE_REL:
                raise UnsupportedWorkbook(
                    f"worksheet table relationship is missing or invalid: {sheet}"
                )
            if relationship.attrib.get("TargetMode") == "External":
                raise UnsupportedWorkbook("table relationships must be internal")
            table_part_name = _resolve_part(
                worksheet_part, relationship.attrib.get("Target", "")
            )
            if table_part_name not in self._parts:
                raise WorkbookError(f"table part is absent: {table_part_name}")
            if self._content_type_for(table_part_name) != TABLE_CONTENT_TYPE:
                raise UnsupportedWorkbook(
                    f"table part has an unexpected content type: {table_part_name}"
                )
            table_root = _safe_xml(self._parts[table_part_name], table_part_name)
            if table_root.tag != _q("table"):
                raise UnsupportedWorkbook(
                    f"table part has an unsupported XML root: {table_part_name}"
                )
            table_columns_node = table_root.find(_q("tableColumns"))
            table_columns = (
                table_columns_node.findall(_q("tableColumn"))
                if table_columns_node is not None
                else []
            )
            formula_columns = []
            for index, column in enumerate(table_columns):
                calculated_formula = column.find(_q("calculatedColumnFormula")) is not None
                totals_formula = (
                    column.find(_q("totalsRowFormula")) is not None
                    or column.attrib.get("totalsRowFunction", "none").casefold() != "none"
                )
                if calculated_formula or totals_formula:
                    formula_columns.append((index, column, calculated_formula, totals_formula))
            if not formula_columns:
                continue

            reference = table_root.attrib.get("ref", "")
            match = RANGE_RE.fullmatch(reference)
            if match is None:
                raise UnsupportedWorkbook(
                    f"table formula column has an invalid table range: {table_part_name}"
                )
            first_column, first_row = _cell_position(match.group(1) + match.group(2))
            last_column, last_row = _cell_position(
                (match.group(3) or match.group(1))
                + (match.group(4) or match.group(2))
            )
            if first_column > last_column or first_row > last_row:
                raise UnsupportedWorkbook(
                    f"table formula column has a reversed table range: {table_part_name}"
                )
            if len(table_columns) != last_column - first_column + 1:
                raise UnsupportedWorkbook(
                    f"table formula column metadata does not match its table range: {table_part_name}"
                )
            declared_count = table_columns_node.attrib.get("count") if table_columns_node is not None else None
            if declared_count is not None and declared_count != str(len(table_columns)):
                raise UnsupportedWorkbook(
                    f"table formula column count is inconsistent: {table_part_name}"
                )

            header_count = self._table_row_count(
                table_root, "headerRowCount", default=1, part=table_part_name
            )
            totals_count = self._table_row_count(
                table_root, "totalsRowCount", default=0, part=table_part_name
            )
            insert_count = int(
                self._table_boolean(
                    table_root, "insertRow", default=False, part=table_part_name
                )
            )
            totals_shown = self._table_boolean(
                table_root, "totalsRowShown", default=False, part=table_part_name
            )
            table_height = last_row - first_row + 1
            if header_count + totals_count + insert_count > table_height:
                raise UnsupportedWorkbook(
                    f"table formula row metadata exceeds its table range: {table_part_name}"
                )

            table_name = table_root.attrib.get("displayName") or table_root.attrib.get("name") or "unnamed"
            for index, column, calculated_formula, totals_formula in formula_columns:
                column_number = first_column + index
                if column_number > last_column:
                    raise UnsupportedWorkbook(
                        f"table formula column is outside its table range: {table_part_name}"
                    )
                column_name = column.attrib.get("name", str(index + 1))
                owner = f"{sheet}[{table_name}].{column_name}"
                if calculated_formula:
                    if totals_shown and totals_count == 0:
                        # Office does not define a relationship between the
                        # legacy shown flag and totalsRowCount. Guard the whole
                        # column when those signals disagree.
                        data_first_row = first_row
                        data_last_row = last_row
                    else:
                        data_first_row = first_row + header_count
                        data_last_row = last_row - totals_count - insert_count
                    if data_first_row <= data_last_row:
                        result.append(
                            (
                                data_first_row,
                                data_last_row,
                                column_number,
                                column_number,
                                owner,
                            )
                        )
                if totals_formula:
                    if totals_count > 0:
                        totals_first_row = last_row - totals_count + 1
                        totals_last_row = last_row
                    else:
                        # A formula marker without an explicit totals-row count
                        # is ambiguous; guard the full column rather than risk a
                        # cached totals value being accepted as an ordinary cell.
                        totals_first_row = first_row
                        totals_last_row = last_row
                    result.append(
                        (
                            totals_first_row,
                            totals_last_row,
                            column_number,
                            column_number,
                            owner,
                        )
                    )
        return result

    @staticmethod
    def _table_row_count(
        table_root: ET.Element, attribute: str, *, default: int, part: str
    ) -> int:
        raw = table_root.attrib.get(attribute)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError as error:
            raise UnsupportedWorkbook(
                f"table has an invalid {attribute}: {part}"
            ) from error
        if value < 0:
            raise UnsupportedWorkbook(f"table has a negative {attribute}: {part}")
        return value

    @staticmethod
    def _table_boolean(
        table_root: ET.Element, attribute: str, *, default: bool, part: str
    ) -> bool:
        raw = table_root.attrib.get(attribute)
        if raw is None:
            return default
        folded = raw.casefold()
        if folded in {"1", "true"}:
            return True
        if folded in {"0", "false"}:
            return False
        raise UnsupportedWorkbook(f"table has an invalid {attribute}: {part}")

    def _content_type_for(self, part: str) -> str | None:
        override = self._content_type_overrides.get(part)
        if override:
            return override
        extension = Path(part).suffix.lstrip(".").lower()
        return self._content_type_defaults.get(extension)

    @staticmethod
    def _index_cells(root: ET.Element) -> dict[str, ET.Element]:
        result: dict[str, ET.Element] = {}
        for cell in select_path(root, MAIN, ("worksheet", "sheetData", "row", "c")):
            for name in ("f", "v", "is"):
                children = cell.findall(_q(name))
                if len(children) > 1:
                    raise UnsupportedWorkbook(f"duplicate cell {name} element")
                if name in {"f", "v"} and children and len(children[0]):
                    raise UnsupportedWorkbook(f"cell {name} element must not contain child elements")
            if cell.find(_q("is")) is not None and (
                cell.find(_q("v")) is not None or cell.find(_q("f")) is not None
            ):
                raise UnsupportedWorkbook("inline-string payload cannot coexist with a value or formula")
            address = cell.attrib.get("r")
            if not address:
                # Dropping the cell would lose data without a word.
                raise UnsupportedWorkbook("a cell has no address; cells placed by position are not read")
            normalized = _normal_address(address)
            if normalized in result:
                raise UnsupportedWorkbook(f"duplicate cell address: {normalized}")
            result[normalized] = cell
        return result

    def get(self, sheet: str, address: str) -> Cell:
        if sheet not in self._cells:
            raise KeyError(f"unknown worksheet: {sheet}")
        element = self._cells[sheet].get(_normal_address(address))
        if element is None:
            return Cell()
        formula_node = element.find(_q("f"))
        value_node = element.find(_q("v"))
        cell_type = element.attrib.get("t")
        value: Any = value_node.text if value_node is not None else None
        if cell_type == "s" and value is not None:
            try:
                if re.fullmatch(r"[0-9]+", value) is None:
                    raise ValueError("shared-string index must be nonnegative")
                value = self._shared_strings[int(value)]
            except (ValueError, IndexError) as exc:
                raise WorkbookError("invalid shared-string index") from exc
        elif cell_type == "inlineStr":
            inline = element.find(_q("is"))
            value = "" if inline is None else self._inline_text(inline)
        elif cell_type == "str" and value_node is not None and value is None:
            value = ""
        elif cell_type == "b" and value is not None:
            if value not in {"0", "1"}:
                raise WorkbookError(f"invalid Boolean value at {address}")
            value = value == "1"
        elif cell_type == "e":
            if value not in EXCEL_ERROR_CODES:
                raise WorkbookError(f"invalid Excel error value at {address}")
            value = ErrorValue(value)
        elif cell_type in (None, "n") and value is not None:
            try:
                value = float(value)
            except ValueError as exc:
                raise WorkbookError(f"invalid numeric value at {address}") from exc
            if value.is_integer():
                value = int(value)
        formula = formula_node.text if formula_node is not None else None
        formula_kind = formula_node.attrib.get("t") if formula_node is not None else None
        try:
            style_id = int(element.attrib["s"]) if "s" in element.attrib else None
            if style_id is not None and style_id < 0:
                raise ValueError("style index must be nonnegative")
        except ValueError as exc:
            raise WorkbookError(f"invalid style index at {address}") from exc
        formula_attributes = (
            tuple(sorted(formula_node.attrib.items())) if formula_node is not None else ()
        )
        return Cell(value, formula, formula_kind, style_id, cell_type, formula_attributes)

    @staticmethod
    def _inline_text(inline: ET.Element) -> str:
        return rich_text(inline, MAIN)

    def set_value(self, sheet: str, address: str, value: str | float | int | bool | None) -> None:
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise TypeError(f"unsupported cell value type: {type(value).__name__}")
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise ValueError("Excel cell numbers must be finite")
        if isinstance(value, str):
            _validate_xml_text(value, "cell text", 32_767)
        cell = self._writable_cell(sheet, address)
        self._request_recalculation()
        for child in list(cell):
            if child.tag in (_q("f"), _q("v"), _q("is")):
                cell.remove(child)
        cell.attrib.pop("t", None)
        if value is None:
            return
        if isinstance(value, bool):
            cell.attrib["t"] = "b"
            self._insert_cell_child(cell, ET.Element(_q("v"), {}), "v").text = "1" if value else "0"
        elif isinstance(value, (int, float)):
            value_node = ET.Element(_q("v"))
            value_node.text = repr(value)
            self._insert_cell_child(cell, value_node, "v")
        elif isinstance(value, str):
            cell.attrib["t"] = "inlineStr"
            inline = ET.SubElement(cell, _q("is"))
            text = ET.SubElement(inline, _q("t"))
            text.text = value
            if value[:1].isspace() or value[-1:].isspace():
                text.set(f"{{{XML_NS}}}space", "preserve")
            cell.remove(inline)
            self._insert_cell_child(cell, inline, "is")

    def set_formula(self, sheet: str, address: str, formula: str) -> None:
        if not isinstance(formula, str) or not formula.startswith("=") or len(formula) == 1:
            raise ValueError("formula must begin with '=' and contain an expression")
        _validate_xml_text(formula[1:], "formula", 8_192)
        cell = self._writable_cell(sheet, address)
        self._request_recalculation()
        for child in list(cell):
            if child.tag in (_q("f"), _q("v"), _q("is")):
                cell.remove(child)
        cell.attrib.pop("t", None)
        formula_node = ET.Element(_q("f"))
        formula_node.text = formula[1:]
        self._insert_cell_child(cell, formula_node, "f")
        self._insert_cell_child(cell, ET.Element(_q("v")), "v")

    @staticmethod
    def _insert_cell_child(cell: ET.Element, child: ET.Element, child_name: str) -> ET.Element:
        child_order = {"f": 0, "v": 1, "is": 2, "extLst": 3}
        rank = child_order[child_name]
        following = next(
            (
                index
                for index, current in enumerate(cell)
                if child_order.get(current.tag.rsplit("}", 1)[-1], 4) > rank
            ),
            len(cell),
        )
        cell.insert(following, child)
        return child

    def _writable_cell(self, sheet: str, address: str) -> ET.Element:
        if sheet not in self._sheet_parts:
            raise KeyError(f"unknown worksheet: {sheet}")
        address = _normal_address(address)
        part = self._sheet_parts[sheet]
        root = self._changed_sheets.get(part)
        if root is None:
            root = _safe_xml(self._parts[part], part)
            self._changed_sheets[part] = root
            self._cells[sheet] = self._index_cells(root)
        current = self._cells[sheet].get(address)
        shared_formula_ranges: dict[str, list[str]] = {}
        for grouped_cell in self._cells[sheet].values():
            formula = grouped_cell.find(_q("f"))
            if formula is None or formula.attrib.get("t") != "shared":
                continue
            reference = formula.attrib.get("ref")
            shared_index = formula.attrib.get("si")
            if reference and shared_index is not None:
                shared_formula_ranges.setdefault(shared_index, []).append(reference)
        for grouped_cell in self._cells[sheet].values():
            formula = grouped_cell.find(_q("f"))
            if formula is None or formula.attrib.get("t") not in {"array", "shared", "dataTable"}:
                continue
            reference = formula.attrib.get("ref")
            if not reference:
                if formula.attrib.get("t") == "shared":
                    shared_index = formula.attrib.get("si")
                    master_ranges = shared_formula_ranges.get(shared_index, [])
                    if grouped_cell is current or not master_ranges:
                        raise UnsupportedWorkbook(
                            f"cannot safely edit sheet with an unbounded shared formula at {sheet}!{address}"
                        )
                    if any(_in_range(address, item) for item in master_ranges):
                        raise UnsupportedWorkbook(
                            f"cannot replace grouped formula range at {sheet}!{address}"
                        )
                    continue
                raise UnsupportedWorkbook(
                    f"cannot safely edit sheet with a grouped formula missing its range: {sheet}"
                )
            if _in_range(address, reference):
                raise UnsupportedWorkbook(f"cannot replace grouped formula range at {sheet}!{address}")
        if current is not None:
            return current
        sheet_data = root.find(_q("sheetData"))
        if sheet_data is None:
            raise UnsupportedWorkbook(f"worksheet has no sheetData: {sheet}")
        column = _column_number(address)
        row_number = int(CELL_RE.fullmatch(address).group(2))  # validated above
        row = next((item for item in sheet_data.findall(_q("row")) if int(item.attrib["r"]) == row_number), None)
        if row is None:
            row = ET.Element(_q("row"), {"r": str(row_number)})
            following = next(
                (idx for idx, item in enumerate(sheet_data) if int(item.attrib.get("r", "0")) > row_number),
                None,
            )
            sheet_data.insert(following if following is not None else len(sheet_data), row)
        cell = ET.Element(_q("c"), {"r": address})
        following = next(
            (
                idx
                for idx, item in enumerate(row)
                if item.tag == _q("c") and _column_number(item.attrib["r"]) > column
            ),
            None,
        )
        if following is None:
            following = next((idx for idx, item in enumerate(row) if item.tag == _q("extLst")), len(row))
        row.insert(following, cell)
        self._cells[sheet][address] = cell
        return cell

    def _invalidate_calc_chain(self) -> None:
        if self._calc_chain_checked:
            return
        rels_part = self._workbook_rels_part
        root = _safe_xml(self._parts[rels_part], rels_part)
        chain_rels = [
            relationship
            for relationship in root.findall(f"{{{PKG_REL}}}Relationship")
            if relationship.attrib.get("Type") == CALC_CHAIN_REL
        ]
        if not chain_rels:
            self._calc_chain_checked = True
            return
        if len(chain_rels) != 1 or chain_rels[0].attrib.get("TargetMode") == "External":
            raise UnsupportedWorkbook("calculation-chain relationships are ambiguous or external")
        relationship = chain_rels[0]
        part = _resolve_part(self._workbook_part, relationship.attrib.get("Target", ""))
        if part not in self._parts:
            raise WorkbookError(f"calculation-chain part is absent: {part}")
        root.remove(relationship)
        updated_rels = _serialize_xml(root, self._parts[rels_part], rels_part)
        updated_types = _safe_xml(self._parts["[Content_Types].xml"], "[Content_Types].xml")
        for override in list(updated_types.findall(f"{{{CONTENT_TYPES}}}Override")):
            if override.attrib.get("PartName", "").lstrip("/") == part:
                updated_types.remove(override)
        updated_content_types = _serialize_xml(
            updated_types, self._parts["[Content_Types].xml"], "[Content_Types].xml"
        )
        self._changed_misc[rels_part] = updated_rels
        self._changed_misc["[Content_Types].xml"] = updated_content_types
        self._content_types = updated_types
        self._content_type_overrides.pop(part, None)
        self._removed_parts.add(part)
        self._calc_chain_checked = True

    def _request_recalculation(self) -> None:
        self._invalidate_calc_chain()
        root = self._changed_workbook
        if root is None:
            root = _safe_xml(self._parts[self._workbook_part], self._workbook_part)
            self._changed_workbook = root
        calc = root.find(_q("calcPr"))
        if calc is None:
            order = {name: index for index, name in enumerate(_WORKBOOK_ORDER)}
            calc_rank = order["calcPr"]
            following = next(
                (
                    index
                    for index, child in enumerate(root)
                    if order.get(child.tag.rsplit("}", 1)[-1], -1) > calc_rank
                ),
                len(root),
            )
            calc = ET.Element(_q("calcPr"))
            root.insert(following, calc)
        calc.set("fullCalcOnLoad", "1")
        calc.set("forceFullCalc", "1")
        calc.set("calcMode", "auto")

    def save_as(self, output: str | Path) -> Path:
        target = Path(output).resolve()
        if target == self.source:
            raise WorkbookError("refusing to overwrite the source workbook")
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        changed: dict[str, bytes] = dict(self._changed_misc)
        changed.update({
            part: _serialize_xml(root, self._parts[part], part)
            for part, root in self._changed_sheets.items()
        })
        if self._changed_workbook is not None:
            changed[self._workbook_part] = _serialize_xml(
                self._changed_workbook,
                self._parts[self._workbook_part],
                self._workbook_part,
            )
        fd, temp_name = tempfile.mkstemp(
            prefix=".workbook_forge-", suffix=".xlsx", dir=target.parent
        )
        os.close(fd)
        temp = Path(temp_name)
        try:
            with zipfile.ZipFile(temp, "w") as destination:
                destination.comment = self._archive.comment
                for info in self._archive.infolist():
                    if info.filename in self._removed_parts:
                        continue
                    destination.writestr(
                        info, changed.get(info.filename, self._parts[info.filename])
                    )
            os.link(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return target
