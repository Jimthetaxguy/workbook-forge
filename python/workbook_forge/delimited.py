"""CSV and TSV import into a fresh workbook model (docs/csv-import.md).

Delimited text becomes one worksheet of plain values. Rust implements the same
rules in ``rust/src/delimited.rs`` and both engines run the shared case table in
``tools/corpus/csv-cases.json``, so one file gives the same cells and the same
refusal codes in either engine.

Rules, in order:

* Decoding. With no ``encoding``, a UTF-8 or UTF-16 byte-order mark picks the
  encoding and is dropped; otherwise the bytes must be UTF-8. An explicit
  encoding is one of ``utf-8``, ``utf-16`` (mark required), ``utf-16-le``,
  ``utf-16-be`` or ``latin-1``. Bytes that do not decode refuse with
  ``csv_encoding``; nothing falls back to another encoding silently.
* Syntax (RFC 4180, as Excel reads it). A field that starts with ``"`` is
  quoted: ``""`` is a literal quote and the field may hold delimiters and line
  breaks. A quote inside an unquoted field is literal. Records end at CRLF,
  LF or CR; a final line break does not add a row, a blank line in the middle
  is an empty row. An unterminated quoted field, or text after a closing
  quote, refuses with ``csv_malformed``. So does a control character a
  workbook cannot store.
* Values. An empty field is an absent cell. A field that is exactly a plain
  decimal number (``-12``, ``3.5``, ``1E+20``) with at most 15 significant
  digits becomes a number. Everything else stays text exactly as written:
  ``00123``, ``1,234``, `` 12``, ``TRUE``, ``#N/A`` and ``=SUM(A1:A2)``
  (never a formula). ``as_text=True`` keeps every field text.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from .toolkit import WorkbookModel
from .workbook import WorkbookError

MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_ROWS = 1_048_576
MAX_COLUMNS = 16_384
DELIMITERS = (",", "\t", ";", "|")

_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_ENCODINGS = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "utf-16": "utf-16",
    "utf16": "utf-16",
    "utf-16-le": "utf-16-le",
    "utf-16le": "utf-16-le",
    "utf-16-be": "utf-16-be",
    "utf-16be": "utf-16-be",
    "latin-1": "latin-1",
    "latin1": "latin-1",
    "iso-8859-1": "latin-1",
}


def _refuse(code: str, message: str) -> WorkbookError:
    return WorkbookError(f"{code}: {message}", code=code)


def _xml_storable(char: str) -> bool:
    point = ord(char)
    if point < 0x20:
        return char in "\t\n\r"
    return point not in (0xFFFE, 0xFFFF)


def decode(data: bytes, encoding: str | None = None) -> str:
    """Decode delimited text by the module's encoding rules."""
    if len(data) > MAX_INPUT_BYTES:
        raise _refuse("package_limit", "delimited text exceeds the 128 MiB input limit")
    if encoding is None:
        if data.startswith(b"\xef\xbb\xbf"):
            name, data = "utf-8", data[3:]
        elif data.startswith(b"\xff\xfe"):
            name, data = "utf-16-le", data[2:]
        elif data.startswith(b"\xfe\xff"):
            name, data = "utf-16-be", data[2:]
        else:
            name = "utf-8"
    else:
        name = _ENCODINGS.get(encoding.strip().lower().replace("_", "-"))
        if name is None:
            raise _refuse("csv_encoding", f"unsupported encoding {encoding!r}")
        if name == "utf-8" and data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        elif name == "utf-16":
            if data.startswith(b"\xff\xfe"):
                name, data = "utf-16-le", data[2:]
            elif data.startswith(b"\xfe\xff"):
                name, data = "utf-16-be", data[2:]
            else:
                raise _refuse("csv_encoding", "utf-16 text needs a byte-order mark")
        elif name in ("utf-16-le", "utf-16-be"):
            mark = b"\xff\xfe" if name == "utf-16-le" else b"\xfe\xff"
            if data.startswith(mark):
                data = data[2:]
    try:
        return data.decode(name, errors="strict")
    except UnicodeDecodeError as error:
        raise _refuse(
            "csv_encoding", f"bytes do not decode as {name} at byte offset {error.start}"
        ) from None


def parse(text: str, delimiter: str = ",") -> list[list[str]]:
    """Split decoded text into records of fields by the module's syntax rules."""
    if delimiter not in DELIMITERS:
        raise ValueError(f"delimiter must be one of {DELIMITERS!r}")
    records: list[list[str]] = []
    record: list[str] = []
    field: list[str] = []
    line = 1
    index = 0
    length = len(text)
    at_field_start = True
    while index < length:
        char = text[index]
        if at_field_start and char == '"':
            start_line = line
            index += 1
            while True:
                if index >= length:
                    raise _refuse(
                        "csv_malformed", f"unterminated quoted field starting on line {start_line}"
                    )
                char = text[index]
                if char == '"':
                    if index + 1 < length and text[index + 1] == '"':
                        field.append('"')
                        index += 2
                        continue
                    index += 1
                    break
                if char == "\n" or (char == "\r" and text[index + 1 : index + 2] != "\n"):
                    line += 1
                if not _xml_storable(char):
                    raise _refuse(
                        "csv_malformed",
                        f"character U+{ord(char):04X} on line {line} cannot be stored in a workbook",
                    )
                field.append(char)
                index += 1
            if index < length and text[index] not in (delimiter, "\r", "\n"):
                raise _refuse(
                    "csv_malformed", f"unexpected text after a closing quote on line {line}"
                )
            at_field_start = False
            continue
        if char == delimiter:
            record.append("".join(field))
            field = []
            at_field_start = True
            index += 1
            continue
        if char in "\r\n":
            record.append("".join(field))
            records.append(record)
            record, field = [], []
            at_field_start = True
            index += 2 if char == "\r" and text[index + 1 : index + 2] == "\n" else 1
            line += 1
            continue
        if not _xml_storable(char):
            raise _refuse(
                "csv_malformed",
                f"character U+{ord(char):04X} on line {line} cannot be stored in a workbook",
            )
        field.append(char)
        at_field_start = False
        index += 1
    if field or record or not at_field_start:
        record.append("".join(field))
        records.append(record)
    return records


def field_value(text: str, *, as_text: bool = False) -> Any:
    """The cell value of one field: None (absent), a number or the text itself."""
    if text == "":
        return None
    if as_text or not _NUMBER.fullmatch(text):
        return text
    mantissa = re.split("[eE]", text, maxsplit=1)[0]
    digits = mantissa.lstrip("-").replace(".", "").lstrip("0")
    if len(digits) > 15:
        return text
    number = float(text)
    if not math.isfinite(number):
        return text
    return 0.0 if number == 0 else number


def _column_name(column: int) -> str:
    name = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        name = chr(65 + remainder) + name
    return name


def to_document(
    records: list[list[str]], *, sheet_name: str = "Sheet1", as_text: bool = False
) -> dict[str, Any]:
    """The version 1 workbook document for parsed records."""
    if len(records) > MAX_ROWS:
        raise _refuse("out_of_bounds", f"{len(records)} rows exceed the {MAX_ROWS}-row grid")
    cells: dict[str, Any] = {}
    for row, record in enumerate(records, 1):
        if len(record) > MAX_COLUMNS:
            raise _refuse(
                "out_of_bounds",
                f"line {row} has {len(record)} fields, more than the {MAX_COLUMNS}-column grid",
            )
        for column, text in enumerate(record, 1):
            value = field_value(text, as_text=as_text)
            if value is not None:
                cells[f"{_column_name(column)}{row}"] = {"value": value}
    return {
        "schema_version": 1,
        "revision": 0,
        "sheets": [{"id": "sheet-1", "name": sheet_name, "cells": cells, "column_widths": {}}],
        "inputs": {},
        "outputs": {},
    }


def import_csv(
    source: str | Path | bytes,
    *,
    delimiter: str | None = None,
    encoding: str | None = None,
    sheet_name: str = "Sheet1",
    as_text: bool = False,
    backend: str = "python",
) -> WorkbookModel:
    """Read CSV or TSV text into a new one-sheet workbook model.

    ``source`` is a path or the file's bytes. With no ``delimiter``, a path
    ending in ``.tsv`` or ``.tab`` uses a tab and anything else a comma.
    Export the model with :func:`workbook_forge.xlsx.export_xlsx`.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        suffix = ""
    else:
        path = Path(source)
        size = path.stat().st_size
        if size > MAX_INPUT_BYTES:
            raise _refuse("package_limit", "delimited text exceeds the 128 MiB input limit")
        data = path.read_bytes()
        suffix = path.suffix.lower()
    if delimiter is None:
        delimiter = "\t" if suffix in (".tsv", ".tab") else ","
    records = parse(decode(data, encoding), delimiter)
    return WorkbookModel(
        document=to_document(records, sheet_name=sheet_name, as_text=as_text), backend=backend
    )
